"""DCFR preflop solver for 8-max tournament spots (12.5% antes, no rake, chip EV).

EXPERIMENTAL: heads-up after the open (the players behind are only approximated) and no
postflop tree. Against a GTO Wizard chart for BTN vs CO at 25 bb it calls about the same
amount but jams roughly three times as often and never small 3-bets, so it isn't used by
the trainer or the bot yet.

Solves one spot at a time: an opener (who opens to 2 bb, or 2.3 bb deeper than 40 bb, with a
fixed opening range from the bot's opening order) against one player still to act, with
everyone else folded:

    defender:  fold / call / small 3-bet (2.65x in position, 3.2x out) / all-in
      after a small 3-bet, opener:  fold / call / all-in
        after the opener's all-in, defender:  fold / call
      after the defender's all-in, opener:  fold / call

All-in pots are worth the exact preflop equity between the two hands (with card removal).
Pots that go to the flop are split by an equity-realisation model instead of a postflop
solve: the player in position keeps more of their equity, suited and connected hands keep
more than offsuit junk, and at low stack-to-pot ratios everyone keeps nearly all of it.

The game is solved with Discounted CFR (Brown & Sandholm 2019; alpha 1.5, beta 0, gamma 2)
over all 169 hand types, vectorised with numpy. Output: each player's strategy for every
hand at every decision, and every option's EV in bb (relative to folding).

    python solver/dcfr_preflop.py BTN CO 25          # one spot, prints the charts and EVs
    python solver/dcfr_preflop.py --all              # every spot at 10-40 bb -> bots/dcfr_table.py
"""

import argparse
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

POSITIONS = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
ANTE_TOTAL = 1.0     # 8 x 0.125 bb
PIPS = "23456789TJQKA"


def load_equity():
    d = np.load(ROOT / "solver" / "preflop_equity.npz")
    names = [str(n) for n in d["names"]]
    return names, d["equity"], d["weight"]


def playability(h):
    """Equity kept after the flop, by hand shape (1 = average)."""
    if len(h) == 2:
        return 1.0 if PIPS.index(h[0]) >= 4 else 0.96
    gap = PIPS.index(h[0]) - PIPS.index(h[1])
    if h[2] == "s":
        return 1.06 if gap == 1 else 1.04 if gap == 2 else 1.02
    if h[0] in "AKQJ" and h[1] in "KQJT":
        return 0.97
    return 0.92 if gap >= 3 else 0.95


def open_range(names, opener, depth):
    """The opener's range: the bot's opening order and width (see bots/contender8.py)."""
    from bots.contender8 import RFI, open_strength
    share = min(0.95, RFI.get(opener, 0.25) * (1 + 0.30 * ANTE_TOTAL))
    w = np.zeros(len(names))
    for i, h in enumerate(names):
        st = open_strength(h, depth)
        # A soft edge (the last couple of percent mixed) keeps the solve smooth.
        w[i] = min(1.0, max(0.0, (share + 0.02 - st) / 0.04))
    return w


class Spot:
    curve_lo, curve_slope, curve_pivot, oop = 0.46, 1.6, 0.305, 0.92
    behind_tax = 0.5       # bb per player still to act behind a flat call

    def __init__(self, opener, defender, depth, names, E, W):
        self.names, self.E, self.W = names, E, W
        self.opener, self.defender, self.depth = opener, defender, depth
        oi, di = POSITIONS.index(opener), POSITIONS.index(defender)
        assert di > oi, "the defender must act after the opener"
        self.open = 2.3 if depth > 40 else 2.0
        # Blinds of players not in the hand are dead money.
        blind_in = {"SB": 0.5, "BB": 1.0}
        self.dead = ANTE_TOTAL + sum(v for p, v in blind_in.items() if p not in (opener, defender))
        self.d_posted = blind_in.get(defender, 0.0)
        self.o_posted = blind_in.get(opener, 0.0)
        # Postflop the defender is in position unless they're a blind (BB vs SB: BB is in position).
        if defender in ("SB", "BB"):
            self.d_ip = defender == "BB" and opener == "SB"
        else:
            self.d_ip = True
        mult = 2.65 if self.d_ip else 3.2
        self.three = round(mult * self.open, 2)
        self.stack = float(depth)
        self.o_range = open_range(names, opener, depth)
        self.d_range = np.ones(len(names))
        self.play = np.array([playability(h) for h in names])
        # Each hand's equity against the other's range (for the realisation model).
        self.eq_d = (E * W) @ self.o_range / np.maximum(W @ self.o_range, 1e-9)
        top = np.array([1.0 if i < 60 else 0.0 for i in range(len(names))])  # ~ a calling range
        order = np.argsort(-self.eq_d)
        flat = np.zeros(len(names)); flat[order[:60]] = 1.0
        self.eq_o = (E.T * W.T) @ flat / np.maximum(W.T @ flat, 1e-9)
        self.behind = sum(1 for p in POSITIONS[POSITIONS.index(defender) + 1:]
                          if p in ("SB", "BB"))
        # Players behind who might wake up: fewer folds to our jams and 3-bets.
        self.wake = 0.04 * self.behind

    # --- terminal payoffs for the defender (bb), as [defender hand, opener hand] matrices ---
    def showdown(self, invest):
        pot = self.dead + 2 * invest
        return self.E * pot - invest

    def realise(self, eq, ip):
        """Share of its equity a hand keeps after the flop: weak hands keep far less (they
        rarely make a hand worth going on with and get bluffed off), strong hands keep it
        all; a bit more in position."""
        base = np.clip(self.curve_lo + self.curve_slope * (eq - self.curve_pivot),
                       self.curve_lo, 1.05)
        return base * (1.0 if ip else self.oop)

    def flop(self, invest):
        """A called pot going to the flop, split by realised equity. Players still to act
        behind the defender (who can call or squeeze) cost the flat a little."""
        pot = self.dead + 2 * invest
        spr = (self.stack - invest) / pot
        k = min(1.0, spr / 6.0)           # at low SPR nearly all equity is realised
        rd = self.realise(self.eq_d, self.d_ip) * self.play
        ro = self.realise(self.eq_o, not self.d_ip) * self.play
        rd = 1 - (1 - rd[:, None]) * k
        ro = 1 - (1 - ro[None, :]) * k
        e = self.E
        share = e * rd / (e * rd + (1 - e) * ro)
        return share * pot - invest - self.behind_tax * self.behind

    def tree(self):
        """Nodes: ('D' | 'O', [(action name, child)]) or ('T', payoff matrix)."""
        S, op, th = self.stack, self.open, self.three
        fold_d = ("T", np.full(self.E.shape, -self.d_posted))
        # When the opener folds to a 3-bet the defender wins the dead money and the open,
        # unless a player behind wakes up (then the 3-bet is usually lost).
        fold_o_vs3 = ("T", np.full(self.E.shape, (1 - self.wake) * (self.dead + op) +
                                    self.wake * (-th)))
        w = self.wake
        win_now = self.dead + op
        jam_d = ("O", [("fold", ("T", np.full(self.E.shape, (1 - w) * win_now + w * (-0.3 * S)))),
                       ("call", ("T", self.showdown(S)))])
        vs_jam_after_3 = ("D", [("fold", ("T", np.full(self.E.shape, -th))),
                                ("call", ("T", self.showdown(S)))])
        vs3 = ("O", [("fold", fold_o_vs3),
                     ("call", ("T", self.flop(th))),
                     ("allin", vs_jam_after_3)])
        nodes = [("fold", fold_d), ("call", ("T", self.flop(op)))]
        if th < 0.45 * S:
            nodes.append((f"raise_{th:g}bb", vs3))
        nodes.append(("allin", jam_d))
        return ("D", nodes)

    # --- DCFR ---
    def solve(self, iters=1500):
        root = self.tree()
        self.regret, self.avg = {}, {}
        Wt = self.W

        def walk(node, path, rd, ro, t):
            kind = node[0]
            if kind == "T":
                U = node[1]
                vd = (U * Wt) @ ro
                vo = (-(U) * Wt).T @ rd
                return vd, vo
            acts = node[1]
            key = path
            n_a = len(acts)
            R = self.regret.setdefault(key, np.zeros((len(self.names), n_a)))
            pos = np.maximum(R, 0)
            tot = pos.sum(axis=1, keepdims=True)
            sigma = np.where(tot > 0, pos / np.where(tot > 0, tot, 1), 1.0 / n_a)
            vds, vos = [], []
            for a, (name, child) in enumerate(acts):
                if kind == "D":
                    vd, vo = walk(child, path + (name,), rd * sigma[:, a], ro, t)
                else:
                    vd, vo = walk(child, path + (name,), rd, ro * sigma[:, a], t)
                vds.append(vd)
                vos.append(vo)
            if kind == "D":
                v_all = np.stack(vds, axis=1)
                v = (sigma * v_all).sum(axis=1)
                R += v_all - v[:, None]
                own = rd
                other_v = sum(vos)
                result = (v, other_v)
            else:
                v_all = np.stack(vos, axis=1)
                v = (sigma * v_all).sum(axis=1)
                R += v_all - v[:, None]
                own = ro
                other_v = sum(vds)
                result = (other_v, v)
            # DCFR discounting.
            a_ = t ** 1.5
            R[R > 0] *= a_ / (a_ + 1)
            R[R < 0] *= 0.5
            A = self.avg.setdefault(key, np.zeros_like(R))
            A += ((t / (t + 1)) ** 2) * own[:, None] * sigma
            return result

        for t in range(1, iters + 1):
            walk(root, (), self.d_range.copy(), self.o_range.copy(), t)
        self.root = root
        return self

    def strategy(self, path):
        A = self.avg[path]
        tot = A.sum(axis=1, keepdims=True)
        n_a = A.shape[1]
        return np.where(tot > 0, A / np.where(tot > 0, tot, 1), 1.0 / n_a)

    def evs(self):
        """EV (bb, relative to folding) of each defender option at the root, per hand, with
        both players playing the average strategies afterwards; and the same for the
        opener facing the 3-bet."""
        Wt = self.W

        def value(node, path, rd, ro):
            if node[0] == "T":
                U = node[1]
                return (U * Wt) @ ro, (-(U) * Wt).T @ rd
            sig = self.strategy(path)
            vd_t, vo_t = 0, 0
            for a, (name, child) in enumerate(node[1]):
                if node[0] == "D":
                    vd, vo = value(child, path + (name,), rd * sig[:, a], ro)
                else:
                    vd, vo = value(child, path + (name,), rd, ro * sig[:, a])
                vd_t = vd_t + vd
                vo_t = vo_t + vo
            return vd_t, vo_t

        out = {}
        rd, ro = self.d_range, self.o_range
        mass_d = Wt @ ro            # how much opener range each defender hand faces
        acts = self.root[1]
        for name, child in acts:
            vd, _ = value(child, (name,), rd, ro)
            out[name] = np.where(mass_d > 0, vd / np.where(mass_d > 0, mass_d, 1), 0)
        base = out["fold"].copy()
        return {k: v - base for k, v in out.items()}


def pct(x):
    return f"{100 * x:.0f}%"


def show(spot):
    names = spot.names
    sig = spot.strategy(())
    acts = [a for a, _ in spot.root[1]]
    tot = {}
    combos = np.array([6 if len(h) == 2 else 4 if h[2] == "s" else 12 for h in names])
    for a_i, a in enumerate(acts):
        tot[a] = (sig[:, a_i] * combos).sum() / 1326
    print(f"{spot.defender} vs {spot.opener} open {spot.open:g}bb, {spot.depth}bb deep: "
          + "  ".join(f"{a} {pct(v)}" for a, v in tot.items()))
    ev = spot.evs()
    R = "AKQJT98765432"
    letter = {"fold": ".", "call": "C", "allin": "J"}
    for i, r1 in enumerate(R):
        row = []
        for j, r2 in enumerate(R):
            h = r1 + r2 if i == j else (r1 + r2 + "s" if i < j else r2 + r1 + "o")
            k = names.index(h)
            best = max(range(len(acts)), key=lambda a: sig[k, a])
            row.append(letter.get(acts[best], "R"))
        print("   " + " ".join(row))
    for h in ("AA", "AKo", "ATo", "KQo", "JTs", "77", "A5s", "Q9s", "72o"):
        k = names.index(h)
        print(f"   {h:4s} " + "  ".join(f"{a}:{pct(sig[k, i])} ev {ev[a][k]:+.2f}"
                                        for i, a in enumerate(acts)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("defender", nargs="?")
    ap.add_argument("opener", nargs="?")
    ap.add_argument("depth", nargs="?", type=float, default=25)
    ap.add_argument("--iters", type=int, default=1500)
    args = ap.parse_args()
    names, E, W = load_equity()
    spot = Spot(args.opener, args.defender, args.depth, names, E, W).solve(args.iters)
    show(spot)


if __name__ == "__main__":
    main()
