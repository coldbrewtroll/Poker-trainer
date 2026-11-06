"""Flop sim audit: plays many drill hands from the Pio flop solves and reports anything
that goes wrong.

    py -3.13 solver/flopsim_audit.py                    (your postflop/ folder)
    py -3.13 solver/flopsim_audit.py --hands 3000       (more hands)
    py -3.13 solver/flopsim_audit.py --synthetic        (made-up solves in the export's
                                                         format: a test of the machinery)

Each hand is set up as Spots -> Flop sim does it (the preflop line, both hands from the
solve's ranges, the solved board), then played with random choices for you so every
branch of the tree gets visited, the opponent following the solve. Checked:
  - the preflop line reaches the flop heads-up, with a pot and stacks close to the solve's;
  - your cards and the opponent's are in the solve's ranges;
  - every flop decision is found in the solved tree while the actions are the solve's;
  - the solve's moves are legal (bet sizes within the table's limits);
  - the nearest-flop stand-in (used in Play / Final table) for random boards of the same
    shape: cards map to real, distinct cards and decisions are found;
  - no errors anywhere.
Prints a summary per spot and writes the problems to flopsim_audit.txt.
"""

import argparse
import itertools
import json
import random
import sys
import tempfile
import traceback
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import flop_sims  # noqa: E402
import match8  # noqa: E402
from engine8 import Game8, position_names  # noqa: E402

BB = match8.BIG_BLIND
RANKS, SUITS = "23456789TJQKA", "cdhs"
CARDS = [r + s for r in RANKS for s in SUITS]
HANDS = [CARDS[j] + CARDS[i] for j in range(52) for i in range(j)]   # Pio's order


# ------------------------------------------------------------------ made-up solves

SPOTS = [("SRP", "BTN", "BB", d) for d in (12, 20, 30, 40)] + \
    [("SRP", "SB", "BB", d) for d in (20, 25, 35)] + \
    [("LP", "SB", "BB", d) for d in (12, 30)] + \
    [("SRP", "CO", "BB", 30), ("SRP", "LJ", "BB", 25), ("SRP", "LJ", "BTN", 25),
     ("SRP", "LJ", "BTN", 30), ("SRP", "HJ", "BB", 50), ("SRP", "UTG1", "BB", 10),
     ("SRP", "UTG1", "BTN", 50), ("SRP", "UTG1", "SB", 30), ("3BP", "LJ", "BTN", 30),
     ("3BP", "SB", "BB", 30)]
BOARDS = [["Ks", "7d", "2c"], ["9h", "8h", "6c"], ["Qd", "Qs", "4h"], ["Ac", "Tc", "5c"],
          ["Jd", "Th", "9s"]]


def synthetic(folder, rng, cpb=100):
    """Solves in pio_export's format, for SPOTS x BOARDS (random strategies)."""
    order = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
    for kind, opener, other, depth in SPOTS:
        oop, ip = sorted((opener, other), key=order.index)
        dead = 1.0 + sum(v for p, v in (("SB", 0.5), ("BB", 1.0)) if p not in (opener, other))
        if kind == "LP":
            put = 1.0
            pot = 2 * put + 1.0
        elif kind == "3BP":
            put = rng.choice([6.5, 7.0, 7.5])
            pot = 2 * put + dead
        else:
            put = rng.choice([2.0, 2.2, 2.5])   # (old saves: other open sizes too)
            pot = 2 * put + dead
        stack = depth - put
        name = (f"3BP_{other}_vs_{opener}" if kind == "3BP" else f"{kind}_{opener}_vs_{other}")
        for board in BOARDS:
            nodes = {}
            P, S = pot * cpb, stack * cpb

            def strat(k):
                rows = [[rng.random() ** 2 for _ in HANDS] for _ in range(k)]
                tot = [sum(r[i] for r in rows) or 1 for i in range(len(HANDS))]
                return [[round(1000 * r[i] / tot[i]) for i in range(len(HANDS))] for r in rows]

            def add(node, player, acts, bets):
                nodes[node] = {"player": player, "actions": acts, "bets": bets,
                               "strategy": strat(len(acts))}

            b1, b2 = round(0.33 * P), round(0.75 * P)
            add("r:0", "OOP", ["c", f"b{b1}", f"b{b2}"], [0, 0])
            ib1, ib2 = round(0.5 * P), round(S)
            add("r:0:c", "IP", ["c", f"b{ib1}", f"b{ib2}"], [0, 0])
            for bet, who, path in ((b1, "IP", "r:0"), (b2, "IP", "r:0"),
                                   (ib1, "OOP", "r:0:c")):
                node = f"{path}:b{bet}"
                raise_to = min(round(S), 3 * bet)
                acts = ["f", "c"] + ([f"b{raise_to}"] if raise_to > bet else [])
                bets = [bet, 0] if who == "IP" else [0, bet]
                add(node, who, acts, bets)
                if raise_to > bet:
                    back = "OOP" if who == "IP" else "IP"
                    add(f"{node}:b{raise_to}", back, ["f", "c"],
                        [bet, raise_to] if who == "IP" else [raise_to, bet])
            node = f"r:0:c:b{ib2}"
            add(node, "OOP", ["f", "c"], [0, ib2])
            ranges = {side: [rng.choice([0, 0, 250, 500, 1000]) for _ in HANDS]
                      for side in ("OOP", "IP")}
            d = {"spot": name, "depth": depth, "board": board, "pot": P, "stack": S,
                 "chips_per_bb": cpb, "kind": kind, "opener": opener, "other": other,
                 "oop": oop, "ip": ip, "hands": HANDS, "ranges": ranges, "nodes": nodes,
                 "source": "synthetic"}
            out = Path(folder) / f"{depth:g}bb_{name}"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{''.join(board)}.json").write_text(json.dumps(d))


# ------------------------------------------------------------------ the drill, headless

class Seat:
    """A seat in an audited hand: you (random choices) or another player."""

    def __init__(self, audit, seat):
        self.a, self.seat = audit, seat

    def new_hand(self, *a):
        pass

    def hand_over(self, r):
        pass

    def act(self, s):
        a = self.a
        try:
            return a.act(self.seat, s)
        except Exception as e:   # recorded, then a safe move so the hand goes on
            a.problem("error in a decision", repr(e), traceback.format_exc(limit=3))
            return ("call", 0) if s.to_call else ("check", 0)


def legal(s, move):
    k, amt = move
    if k == "fold":
        return bool(s.to_call)
    if k in ("check", "call"):
        return True
    return bool(s.min_raise_to) and s.min_raise_to <= amt <= s.max_raise_to


class Audit:
    def __init__(self, sims, rng):
        self.sims, self.rng = sims, rng
        self.problems = []
        self.stats = defaultdict(Counter)
        self.mapped, self.key = False, None

    def problem(self, what, detail="", extra=""):
        self.problems.append((self.sim.name if getattr(self, "sim", None) else "-", what,
                              detail, extra))
        self.stats[self.sim.name if getattr(self, "sim", None) else "-"]["problems"] += 1

    def act(self, seat, s):
        st = self.stats[self.key or self.sim.name]
        d = self.director
        if s.street == "preflop":
            mv = d.hero_preflop(s, None) if seat == 0 else d.other_preflop(s, None)
            return mv
        if s.street == "flop":
            if not self.flop_checked and self.mapped:   # a stand-in: find it now
                self.flop_checked = True
                pot = s.pot - sum(p.bet for p in s.players)
                eff = min(p.stack + p.bet for p in s.players if not p.folded)
                m = flop_sims.find(self.group, self.sim.kind, self.sim.opener, self.sim.other,
                                   list(s.board), pot, eff)
                st["flops"] += 1
                if m is None:
                    st["no_match"] += 1
                    self.on_tree = False
                    return ("check", 0) if not s.to_call else ("call", 0)
                st["matched"] += 1
                self.sim = self.director.sim = m
                for hole in self.holes:
                    if m.cards(hole) is None:
                        st["cards_unmapped"] += 1
                    st["hands_mapped"] += 1
            if not self.flop_checked:
                self.flop_checked = True
                live = [p for p in s.players if not p.folded]
                if len(live) != 2 or {p.seat for p in live} != {0, self.vseat}:
                    self.problem("not heads-up on the flop",
                                 f"live {[p.position for p in live]}")
                pot_bb = (s.pot - sum(p.bet for p in s.players)) / BB
                sim_pot = self.sim.d["pot"] / self.sim.cpb
                st["flops"] += 1
                if abs(pot_bb - sim_pot) > max(0.6, 0.12 * sim_pot):
                    st["pot_off"] += 1
                    self.problem("pot differs from the solve's",
                                 f"table {pot_bb:.2f} bb vs solve {sim_pot:.2f} bb")
            node = self.sim.node_for(s, BB)
            in_tree = node is not None
            st["decisions"] += 1
            if not in_tree and self.on_tree:
                st["left_tree"] += 1
                self.problem("decision not found in the solved tree",
                             f"flop line {[(a, amt) for t, _, a, amt in s.history if t == 'flop']}")
            self.on_tree = in_tree
            if seat == self.vseat:
                mv = d.flop_move(s, sample=True)
                if mv is None:
                    if in_tree and not self.mapped:
                        self.problem("the solve had no move for the opponent's cards",
                                     f"{s.hole} at {node}")
                    mv = ("call", 0) if s.to_call else ("check", 0)
                if not legal(s, mv):
                    self.problem("illegal move from the solve", f"{mv} (min {s.min_raise_to}, "
                                 f"max {s.max_raise_to})")
                    mv = ("call", 0) if s.to_call else ("check", 0)
                return mv
            if in_tree:   # you: one of the solve's actions at random (visits every branch)
                n = self.sim.nodes[node]
                mix = self.sim.mix(node, list(s.hole))
                if mix is None and not self.mapped:
                    self.problem("your cards have no strategy at this node", f"{s.hole}")
                code = self.rng.choice(n["actions"])
                mv = self.sim.move(s, code, BB)
                if not legal(s, mv):
                    self.problem("illegal move for a solve action", f"{code} -> {mv}")
                    mv = ("call", 0) if s.to_call else ("check", 0)
                return mv
        # off the tree / later streets: random sensible moves
        opts = [("check", 0)] if not s.to_call else [("fold", 0), ("call", 0)]
        if s.min_raise_to:
            opts.append(("raise", s.min_raise_to))
        return self.rng.choice(opts)

    def play(self, sim):
        self.sim = sim
        n = 8
        names = position_names(n)
        hero = self.rng.choice([sim.opener, sim.other])
        villain = sim.other if hero == sim.opener else sim.opener
        if hero not in names or villain not in names:
            self.problem("position not at an 8-handed table", f"{hero} / {villain}")
            return
        button = next(b for b in range(n) if
                      dict(zip([(b + 3 + k) % n for k in range(n)], names))[0] == hero)
        pos = dict(zip([(button + 3 + k) % n for k in range(n)], names))
        self.vseat = next(x for x, p in pos.items() if p == villain)
        deck = sim.make_deck(self.rng, n, 0, hero, self.vseat)
        if deck is None:
            self.problem("couldn't deal both hands from the ranges")
            return
        from cards import name
        hole = {0: [name(deck[0]), name(deck[1])],
                self.vseat: [name(deck[2 * self.vseat]), name(deck[2 * self.vseat + 1])]}
        for seat, side in ((0, sim.side_of(hero)), (self.vseat, sim.side_of(villain))):
            i = sim.index.get(frozenset(hole[seat]))
            if i is None or sim.d["ranges"][side][i] <= 0:
                self.problem("a hand dealt outside its range", f"{hole[seat]} ({side})")
        self.director = flop_sims.SimDirector(sim, hero, self.vseat, BB, self.rng)
        self.flop_checked, self.on_tree = False, True
        g = Game8([Seat(self, x) for x in range(n)], stack=int(sim.depth * BB), big_blind=BB,
                  ante=match8.ANTE, time_limit_s=1e9)
        try:
            g.play_hand(deck, button=button, hand_no=0)
        except Exception as e:
            self.problem("error in the hand", repr(e), traceback.format_exc(limit=3))
        self.stats[sim.name]["hands"] += 1

    def play_mapped(self, group):
        """A Play-mode hand: random cards and a random flop (not the solved one), a
        different open size, the nearest solve standing in on the flop."""
        sim0 = self.rng.choice(group)
        self.sim, self.group, self.mapped = sim0, group, True
        name = self.key = f"{sim0.kind} {sim0.opener} v {sim0.other} (played, nearest flop)"
        n = 8
        names = position_names(n)
        hero = self.rng.choice([sim0.opener, sim0.other])
        villain = sim0.other if hero == sim0.opener else sim0.opener
        button = next(b for b in range(n) if
                      dict(zip([(b + 3 + k) % n for k in range(n)], names))[0] == hero)
        pos = dict(zip([(button + 3 + k) % n for k in range(n)], names))
        self.vseat = next(x for x, p in pos.items() if p == villain)
        from cards import card
        names52 = self.rng.sample(CARDS, 52)
        deck = [card(c) for c in names52]
        self.holes = [names52[0:2], names52[2 * self.vseat:2 * self.vseat + 2]]
        factor = self.rng.uniform(0.9, 1.3)

        class Director(flop_sims.SimDirector):
            def _preflop(d, s, me_opener):
                mv = super()._preflop(s, me_opener)
                if mv[0] == "raise" and not any(a == "raise" for t, _, a, _ in s.history
                                                if t == "preflop"):
                    mv = ("raise", int(mv[1] * factor))   # an open the solve didn't use
                return mv

        self.director = Director(sim0, hero, self.vseat, BB, self.rng)
        self.flop_checked, self.on_tree = False, True
        depth = sim0.depth * self.rng.uniform(0.8, 1.25)
        g = Game8([Seat(self, x) for x in range(n)], stack=int(depth * BB), big_blind=BB,
                  ante=match8.ANTE, time_limit_s=1e9)
        try:
            g.play_hand(deck, button=button, hand_no=0)
        except Exception as e:
            self.problem("error in the hand", repr(e), traceback.format_exc(limit=3))
        finally:
            self.mapped, self.key = False, None
        self.stats[name]["hands"] += 1

    def nearest(self, sims_by_spot, tries=200):
        """Random boards of each spot through find() (the Play / Final table stand-in)."""
        for (kind, opener, other), group in sims_by_spot.items():
            ok = 0
            for _ in range(tries):
                board = self.rng.sample(CARDS, 3)
                sim0 = group[0]
                pot = sim0.d["pot"] / sim0.cpb * BB
                eff = sim0.d["stack"] / sim0.cpb * BB
                try:
                    m = flop_sims.find(group, kind, opener, other, board, pot, eff)
                except Exception as e:
                    self.sim = sim0
                    self.problem("error in the nearest-flop match", repr(e))
                    continue
                if m is None:
                    continue
                ok += 1
                for combo in itertools.islice(itertools.combinations(
                        [c for c in CARDS if c not in board], 2), 0, None, 97):
                    out = m.cards(list(combo))
                    if out is None:
                        continue
                    if len(set(out)) != 2 or set(out) & set(m.sim.board):
                        self.sim = m.sim
                        self.problem("nearest flop: bad card mapping", f"{combo} -> {out}")
            self.stats[f"{kind} {opener} v {other} (nearest flop)"]["boards"] += tries
            self.stats[f"{kind} {opener} v {other} (nearest flop)"]["matched"] += ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--postflop", default=str(ROOT / "postflop"))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--hands", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="flopsim_audit.txt")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    folder = args.postflop
    if args.synthetic:
        folder = tempfile.mkdtemp(prefix="flopsims_")
        synthetic(folder, rng)
    sims = flop_sims.load_all(folder)
    if not sims:
        sys.exit(f"No flop solves in {folder}. Export some with solver/pio_export.py first.")
    print(f"{len(sims)} solves in {folder}")
    audit = Audit(sims, rng)
    for _ in range(args.hands):
        audit.play(rng.choice(sims))
    groups = defaultdict(list)
    for s in sims:
        groups[(s.kind, s.opener, s.other)].append(s)
    audit.nearest(groups)
    group_list = list(groups.values())
    for _ in range(args.hands):
        audit.play_mapped(audit.rng.choice(group_list))
    lines = []
    for name, st in sorted(audit.stats.items()):
        lines.append(f"{name:42s} " + "  ".join(f"{k} {v}" for k, v in sorted(st.items())))
    kinds = Counter(p[1] for p in audit.problems)
    lines.append("")
    lines.append(f"problems: {len(audit.problems)}")
    for k, v in kinds.most_common():
        lines.append(f"  {v:5d}  {k}")
    lines.append("")
    for p in audit.problems[:300]:
        lines.append(" | ".join(str(x) for x in p[:3]))
        if p[3]:
            lines.append("      " + p[3].replace("\n", "\n      "))
    Path(args.out).write_text("\n".join(lines))
    print("\n".join(lines[: len(audit.stats) + 3 + len(kinds)]))
    print(f"\nfull report: {args.out}")


if __name__ == "__main__":
    main()
