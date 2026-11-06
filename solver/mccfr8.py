"""Monte Carlo CFR preflop solver for full tournament tables (8-max or 6-max), every player
in the tree: opens, cold calls, small 3-bets and squeezes, jams, calls of jams.

    python solver/mccfr8.py --players 8 --depth 25 --iters 200000
    python solver/mccfr8.py --players 8 --depth 25 --show CO BTN_COr

Game: blinds 0.5 / 1, 0.125 bb ante from every player, no rake, chip EV, equal stacks.
Actions (the bot's sizes):
  first in:             fold, open (2 bb; 2.3 bb deeper than 40 bb), and at 15 bb or less all-in
  facing an open:       fold, call, small 3-bet (2.65x, 3.2x from the blinds, plus 1x per
                        caller) unless that's 45%+ of the stack, all-in
  facing a 3-bet:       fold, call, all-in
  facing an all-in:     fold, call
Pots that are all in are worth each hand's preflop equity (heads-up exactly, from the
169x169 table; multiway approximated from the pairwise equities). Pots that go to the
flop are split by an equity-realisation model: the player last to act keeps more of their
equity, suited and connected hands more than offsuit junk, weak hands much less, and at
low stack-to-pot ratios everyone keeps nearly all of it.

Algorithm: external-sampling MCCFR (Lanctot et al. 2009) with regret matching+ and
linearly weighted averaging (as in Linear / Discounted CFR), information sets = (spot,
hand type). Like Pluribus-style multiplayer CFR, it isn't guaranteed to reach an exact
equilibrium with more than two players, but it works well in practice.

Output: bots/mccfr_table.py, in the same format as imported sims (TABLE[spot][depth]
[action][hand] = frequency), plus EVs, for the trainer.
"""

import argparse
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine8 import position_names  # noqa: E402

PIPS = "23456789TJQKA"


def load_equity():
    """The 169x169 all-in equity table (plain JSON, so PyPy can run this without numpy:
    `pypy3 solver/mccfr8.py ...` is several times faster)."""
    import json
    d = json.loads((ROOT / "solver" / "preflop_equity.json").read_text())
    return d["names"], d["equity"]


def type_of(c1, c2):
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 < r2:
        r1, r2, c1, c2 = r2, r1, c2, c1
    if r1 == r2:
        return PIPS[r1] * 2
    return PIPS[r1] + PIPS[r2] + ("s" if (c1 & 3) == (c2 & 3) else "o")


def playability(h):
    """Equity kept after the flop by hand shape (1 = average): suited connectors and
    one-gappers make straights and flushes and win big pots; weak offsuit aces and
    disconnected offsuit hands make dominated pairs and little else."""
    if len(h) == 2:
        return 1.0 if PIPS.index(h[0]) >= 4 else 0.98
    hi, lo = PIPS.index(h[0]), PIPS.index(h[1])
    gap = hi - lo
    if h[2] == "s":
        if gap == 1:
            return 1.15
        if gap == 2:
            return 1.10
        if gap == 3:
            return 1.05
        return 1.04 if h[0] == "A" else 1.02
    if h[0] in "AKQJ" and h[1] in "KQJT":
        return 0.97
    if h[0] == "A":
        return 0.90
    if gap == 1:
        return 0.95
    return 0.85 if gap >= 3 else 0.92


class Solver:
    oop = 0.85      # equity kept out of position, relative to the last player to act
    r_lo, r_slope, r_pivot = 0.30, 2.6, 0.40  # realisation by hand strength (see payoff)
    spr_full = 2.5  # at this stack-to-pot ratio and above, the full realisation model applies

    def __init__(self, n, depth, seed=1):
        self.n, self.depth = n, float(depth)
        self.pos = position_names(n)           # preflop order: first to act ... BB
        self.names, E = load_equity()
        self.idx = {h: i for i, h in enumerate(self.names)}
        self.E = E
        self.play = {h: playability(h) for h in self.names}
        # Each hand's equity against a random hand (its general strength), for the
        # equity-realisation model.
        combos = [6 if len(h) == 2 else 4 if h[2] == "s" else 12 for h in self.names]
        self.eqr = {h: sum(e * c for e, c in zip(row, combos)) / 1326
                    for h, row in zip(self.names, E)}
        self.open_to = 2.3 if depth > 40 else 2.0
        self.ante = 0.125 * n
        self.regret, self.strat = {}, {}
        self.rng = random.Random(seed)
        # Seats in preflop order 0..n-1; the last two are SB and BB (heads-up: SB, BB).
        self.sb = n - 2
        self.bbs = n - 1
        # Postflop order: SB, BB, then the rest in preflop order (the BTN acts last).
        self.post_order = [self.sb, self.bbs] + list(range(n - 2))

    # ----- game state -----
    def start(self):
        bets = [0.0] * self.n
        bets[self.sb], bets[self.bbs] = 0.5, 1.0
        return {"bets": bets, "folded": [False] * self.n, "allin": [False] * self.n,
                "level": 1.0, "raises": 0, "to_act": list(range(self.n)), "hist": [],
                "callers": 0, "last_raiser": None, "line": "", "live": self.n}

    CODE = {"c": "c", "r": "r", "j": "a"}

    def spot_name(self, st, seat):
        """The spot's name (the hero's position, then each earlier action that wasn't a
        fold), built up as the hand goes (see step)."""
        return self.pos[seat] + st["line"]

    def actions(self, st, seat):
        S = self.depth
        bet = st["bets"][seat]
        acts = ["f"]
        if st["raises"] == 0:
            if self.depth <= 15:
                acts += ["r", "j"]
            else:
                acts += ["r"]
            return acts
        acts.append("c")
        if st["raises"] == 1:
            if self.small3_to(st, seat) < 0.45 * S:
                acts.append("r")
            acts.append("j")
        elif st["raises"] == 2 and not any(st["allin"]):
            acts.append("j")
        return acts

    def small3_to(self, st, seat):
        blind = seat in (self.sb, self.bbs)
        opener = st["last_raiser"]
        bvb = seat == self.bbs and opener == self.sb
        mult = 2.65 if (not blind or bvb) else 3.2
        return round((mult + st["callers"]) * st["level"], 2)

    def raise_to(self, st, seat, a):
        if a == "j":
            return self.depth
        if st["raises"] == 0:
            return self.open_to
        return self.small3_to(st, seat)

    def step(self, st, seat, a):
        """New state after `seat` takes action `a` (copies)."""
        nb = list(st["bets"])
        folded, allin = list(st["folded"]), list(st["allin"])
        level, raises, callers, last = st["level"], st["raises"], st["callers"], st["last_raiser"]
        to_act = [s for s in st["to_act"] if s != seat]
        if a == "f":
            folded[seat] = True
        elif a == "c":
            nb[seat] = min(level, self.depth)
            if nb[seat] >= self.depth:
                allin[seat] = True
            callers += 1
        else:
            to = self.raise_to(st, seat, a)
            nb[seat] = to
            if to >= self.depth:
                allin[seat] = True
            level, raises, callers, last = to, raises + 1, 0, seat
            # Everyone else still in (and not all in) must act again, in order after seat.
            order = [(seat + k) % self.n for k in range(1, self.n)]
            to_act = [s for s in order if not folded[s] and not allin[s]]
        line = st["line"] if a == "f" else st["line"] + "_" + self.pos[seat] + self.CODE[a]
        return {"bets": nb, "folded": folded, "allin": allin, "level": level,
                "raises": raises, "to_act": to_act, "hist": st["hist"] + [(seat, a)],
                "callers": callers, "last_raiser": last, "line": line,
                "live": st["live"] - (a == "f")}

    def next_seat(self, st):
        if st["live"] <= 1:
            return None   # everyone else folded (e.g. a walk for the big blind)
        if st["raises"] == 0 and all(st["folded"][s] for s in range(self.n) if s != self.bbs):
            return None
        for s in st["to_act"]:
            if not st["folded"][s] and not st["allin"][s]:
                return s
        return None

    # ----- payoffs -----
    def payoff(self, st, hands):
        """Chips won by each seat (relative to the start of the hand)."""
        n = self.n
        pot = sum(st["bets"]) + self.ante
        live = [s for s in range(n) if not st["folded"][s]]
        out = [-st["bets"][s] - 0.125 for s in range(n)]
        if len(live) == 1:
            out[live[0]] += pot
            return out
        types = [hands[s] for s in live]
        ix = [self.idx[t] for t in types]
        if len(live) == 2:
            e0 = self.E[ix[0]][ix[1]]
            eq = [e0, 1 - e0]
        else:
            prods = []
            for i in range(len(live)):
                p = 1.0
                for j in range(len(live)):
                    if i != j:
                        p *= self.E[ix[i]][ix[j]]
                prods.append(p)
            tot = sum(prods) or 1.0
            eq = [p / tot for p in prods]
        if any(st["allin"][s] for s in live):
            share = eq
        else:
            invested = st["level"]
            spr = (self.depth - invested) / pot
            # Only at genuinely low stack-to-pot ratios (3-bet pots) does everyone get to
            # realise nearly all their equity; single-raised pots play out normally.
            k = min(1.0, spr / self.spr_full)
            last_to_act = max(live, key=lambda s: self.post_order.index(s))
            rs = []
            for s, e, t in zip(live, eq, types):
                # Weak hands keep far less of their equity than strong ones (judged by the
                # hand's strength, not this matchup), and out of position keeps less.
                base = min(1.05, max(self.r_lo, self.r_lo + self.r_slope * (self.eqr[t] -
                                                                         self.r_pivot)))
                r = base * (1.0 if s == last_to_act else self.oop) * self.play[t]
                rs.append(1 - (1 - r) * k)
            w = [e * r for e, r in zip(eq, rs)]
            tot = sum(w) or 1.0
            share = [x / tot for x in w]
        for s, sh in zip(live, share):
            out[s] += sh * pot
        return out

    # ----- MCCFR -----
    def strategy(self, key, n_a):
        r = self.regret.get(key)
        if r is None:
            return [1.0 / n_a] * n_a
        pos = [x if x > 0 else 0.0 for x in r]
        tot = sum(pos)
        return [x / tot for x in pos] if tot > 0 else [1.0 / n_a] * n_a

    def traverse(self, st, hands, p, t):
        seat = self.next_seat(st)
        if seat is None:
            return self.payoff(st, hands)[p]
        acts = self.actions(st, seat)
        key = (self.spot_name(st, seat), hands[seat])
        sigma = self.strategy(key, len(acts))
        if seat == p:
            vals = [self.traverse(self.step(st, seat, a), hands, p, t) for a in acts]
            v = sum(s * x for s, x in zip(sigma, vals))
            r = self.regret.setdefault(key, [0.0] * len(acts))
            for i in range(len(acts)):
                r[i] = max(0.0, r[i] + t * (vals[i] - v))   # regret matching+, linear weight
            return v
        # Others: sample one action; record their average strategy.
        sm = self.strat.setdefault(key, [0.0] * len(acts))
        for i in range(len(acts)):
            sm[i] += t * sigma[i]
        x = self.rng.random()
        for i, s in enumerate(sigma):
            x -= s
            if x <= 0:
                break
        return self.traverse(self.step(st, seat, acts[i]), hands, p, t)

    def iterate(self, t):
        deck = list(range(52))
        self.rng.shuffle(deck)
        hands = [type_of(deck[2 * i], deck[2 * i + 1]) for i in range(self.n)]
        root = self.start()
        for p in range(self.n):
            self.traverse(root, hands, p, t)

    def run(self, iters, report=0):
        t0 = time.time()
        for t in range(1, iters + 1):
            self.iterate(t)
            if report and t % report == 0:
                print(f"  {t} iterations, {time.time() - t0:.0f}s, {len(self.regret)} infosets",
                      flush=True)
        return self

    # ----- results -----
    def avg(self, key):
        s = self.strat.get(key)
        if not s or sum(s) == 0:
            return None
        tot = sum(s)
        return [x / tot for x in s]

    def table(self):
        """{spot: {action name: {hand: frequency}}} from the average strategy."""
        out = {}
        for (spot, hand), s in self.strat.items():
            tot = sum(s)
            if tot <= 0:
                continue
            acts = self.actions_for_spot(spot)
            if acts is None or len(acts) != len(s):
                continue
            for a, x in zip(acts, s):
                if a == "f" or x <= 0:
                    continue
                out.setdefault(spot, {}).setdefault(self.action_name(spot, a), {})[hand] = \
                    round(x / tot, 3)
        return out

    def actions_for_spot(self, spot):
        st = self.replay(spot)
        return None if st is None else self.actions(st[0], st[1])

    def replay(self, spot):
        """Rebuild the state of a spot name (everyone not named folded)."""
        parts = spot.split("_")
        hero = self.pos.index(parts[0])
        acts = [(self.pos.index(p[:-1]), {"r": "r", "c": "c", "a": "j"}[p[-1]]) for p in parts[1:]]
        st = self.start()
        for seat_a, a in acts + [(hero, None)]:
            while True:
                s = self.next_seat(st)
                if s is None:
                    return None
                if s == seat_a:
                    break
                st = self.step(st, s, "f")
            if a is None:
                return st, hero
            st = self.step(st, seat_a, a)
        return None

    def action_name(self, spot, a):
        if a == "c":
            return "call"
        if a == "j":
            return "allin"
        st, hero = self.replay(spot)
        return f"raise_{self.raise_to(st, hero, 'r'):g}bb"


def grid(table, spot):
    R = "AKQJT98765432"
    acts = table.get(spot, {})
    for i, r1 in enumerate(R):
        row = []
        for j, r2 in enumerate(R):
            h = r1 + r2 if i == j else (r1 + r2 + "s" if i < j else r2 + r1 + "o")
            best, bx = ".", 0.34
            for a, rng in acts.items():
                if rng.get(h, 0) > bx:
                    bx, best = rng[h], {"call": "C", "allin": "J"}.get(a, "R")
            row.append(best)
        print("   " + " ".join(row))


def summary(table, spot):
    combos = lambda h: 6 if len(h) == 2 else 4 if h[2] == "s" else 12  # noqa: E731
    return "  ".join(f"{a} {100 * sum(f * combos(h) for h, f in r.items()) / 1326:.1f}%"
                     for a, r in table.get(spot, {}).items())


def merge(players, depth):
    """Add up the saved runs (different seeds) for one table size and depth into one
    table; writes solver/mccfr_out/<players>max_<depth>bb.json."""
    import json
    out = ROOT / "solver" / "mccfr_out"
    runs = sorted(out.glob(f"{players}max_{depth:g}bb_s*.json"))
    total = {}
    for f in runs:
        for k, v in json.loads(f.read_text())["raw"].items():
            acc = total.setdefault(k, [0.0] * len(v))
            if len(acc) == len(v):
                for i, x in enumerate(v):
                    acc[i] += x
    s = Solver(players, depth)
    s.strat = {tuple(k.split("|")): v for k, v in total.items()}
    table = s.table()
    # How much each spot was played (summed weights over its hands): rarely reached spots
    # haven't converged, so the trainer can leave them out.
    weight = {}
    for (spot, hand), v in s.strat.items():
        weight[spot] = weight.get(spot, 0.0) + sum(v)
    top = max(weight.values()) if weight else 1.0
    rel = {spot: w / top for spot, w in weight.items()}
    f = out / f"{players}max_{depth:g}bb.json"
    f.write_text(json.dumps({"players": players, "depth": depth, "runs": len(runs),
                             "table": table, "weight": rel}))
    print(f"merged {len(runs)} runs -> {f}")
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--depth", type=float, default=25)
    ap.add_argument("--iters", type=int, default=100000)
    ap.add_argument("--show", nargs="*", default=["CO", "BTN_COr", "BB_BTNr", "UTG"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--param", nargs="*", default=[], help="model settings, e.g. oop=0.8 r_lo=0.3")
    ap.add_argument("--merge", action="store_true", help="merge the saved runs and show them")
    ap.add_argument("--save", action="store_true",
                    help="write solver/mccfr_out/<players>max_<depth>bb.json for the trainer")
    args = ap.parse_args()
    if args.merge:
        table = merge(args.players, args.depth)
        for spot in args.show:
            print(f"\n{spot} @ {args.depth:g}bb: {summary(table, spot)}")
            grid(table, spot)
        return
    for kv in args.param:
        k, v = kv.split("=")
        setattr(Solver, k, float(v))
    s = Solver(args.players, args.depth, args.seed).run(args.iters, report=max(1, args.iters // 10))
    table = s.table()
    if args.save:
        import json
        out = ROOT / "solver" / "mccfr_out"
        out.mkdir(exist_ok=True)
        f = out / f"{args.players}max_{args.depth:g}bb_s{args.seed}.json"
        raw = {f"{k[0]}|{k[1]}": v for k, v in s.strat.items()}
        f.write_text(json.dumps({"players": args.players, "depth": args.depth,
                                 "iters": args.iters, "table": table, "raw": raw}))
        print("wrote", f)
    for spot in args.show:
        print(f"\n{spot} @ {args.depth:g}bb: {summary(table, spot)}")
        grid(table, spot)


if __name__ == "__main__":
    main()
