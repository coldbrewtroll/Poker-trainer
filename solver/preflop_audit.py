"""Preflop audit: asks the trainer's bot about every starting hand in many preflop spots
and reports anything that doesn't make sense.

    py -3.13 solver/preflop_audit.py sweep --out pre_sweep.jsonl
    py -3.13 solver/preflop_audit.py play  --samples 1000000 --seed 1 --out pre_play.jsonl
    py -3.13 solver/preflop_audit.py ft    --samples 1000000 --seed 2 --out pre_ft.jsonl

Every spot is asked with all 1326 two-card hands (one sample = one hand in one spot), so the
whole range of the spot is seen at once.

sweep: fixed lines at every stack depth from 8 to 65 bb in half-bb steps, the way Play mode
    deals them (your sims, chip EV): first in from every position, the BB against an open
    from each position, the BB against an SB limp, the SB and the BTN against an open, the
    opener against a 3-bet, the BB against a BTN all-in. Also checks that the ranges change
    smoothly with the stack depth (no jumps between neighbouring depths).
play: spots from whole hands at Play mode tables (random stacks 10-100 bb).
ft:   spots from whole final tables (Final table mode: ICM, the final table's rules).

Checked in every spot: moves are legal; no fold when checking is free; AA never folds and
KK hardly ever; at a player's first decision, a hand never calls when a hand that
dominates it folds (A9 in, AT out; bluff raises with the weaker hand are fine); no
raise that puts in half the stack without going all-in; raises over a limp of 3 bb or more;
no open-limps outside the SB; 3-bets at least twice the raise; no fold getting a huge price;
no decision slower than a second. Each problem is a JSON line in --out; a summary prints at
the end.
"""

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import match8  # noqa: E402
from engine8 import Game8  # noqa: E402

BB = match8.BIG_BLIND
RANKS, SUITS = "23456789TJQKA", "cdhs"
CARDS = [r + s for r in RANKS for s in SUITS]
ALL = [(CARDS[i], CARDS[j]) for i in range(52) for j in range(i)]


def hclass(a, b):
    ra, rb = sorted((a[0], b[0]), key=RANKS.index, reverse=True)
    if ra == rb:
        return ra + rb
    return ra + rb + ("s" if a[1] == b[1] else "o")


CLASSES = sorted({hclass(a, b) for a, b in ALL})


def dominance_pairs():
    out = []
    for a in range(13):
        for b in range(a):
            out.append((RANKS[a] * 2, RANKS[b] * 2))
    for hi in range(13):
        for k1 in range(hi):
            for k2 in range(k1):
                for s in "so":
                    out.append((RANKS[hi] + RANKS[k1] + s, RANKS[hi] + RANKS[k2] + s))
    for hi in range(13):
        for lo in range(hi):
            out.append((RANKS[hi] + RANKS[lo] + "s", RANKS[hi] + RANKS[lo] + "o"))
    return out


DOM = dominance_pairs()
WHEEL = set("5432")


def wheel_ok(better, worse):
    """Wheel aces (and suited wheel cards) out-play some middle kickers: blockers,
    straights. Not counted as dominance problems."""
    if worse[0] == "A" and worse[1] in WHEEL and better[0] == "A" and better[1] in "6789TJ":
        return True
    return False


class Audit:
    def __init__(self, seed):
        import trainer8 as T
        T.load_study_table()
        self.T = T
        self.bot = T.make_study(T.STUDY, seed)
        self.flags, self.counts = [], Counter()
        self.samples = 0
        self.slow = 0
        self.timing = []

    def flag(self, rule, ctx, **kw):
        self.flags.append(dict(ctx, rule=rule, **kw))

    def ask(self, s):
        """{class: Counter(action)} and raise sizes for every hand in this spot."""
        res = defaultdict(Counter)
        sizes = defaultdict(list)
        bad = []
        for a, b in ALL:
            self.bot.plan3 = None
            t0 = time.perf_counter()
            try:
                mv = self.bot.act(replace(s, hole=[a, b]))
            except Exception as e:   # pragma: no cover - a finding
                bad.append(("error", repr(e), a + b))
                continue
            dt = time.perf_counter() - t0
            if dt > 1.0:
                bad.append(("slow", round(dt, 2), a + b))
            self.timing.append(dt)
            self.samples += 1
            h = hclass(a, b)
            k = mv[0]
            if k == "raise":
                if not s.min_raise_to or not (s.min_raise_to <= mv[1] <= s.max_raise_to):
                    bad.append(("illegal", list(mv), a + b))
                key = "jam" if mv[1] >= s.max_raise_to - 1 else "raise"
                if key == "raise":
                    sizes[h].append(mv[1])
            elif k == "fold":
                key = "fold"
                if not s.to_call:
                    bad.append(("fold when it can check", None, a + b))
            elif k in ("call", "check"):
                key = "call"
            else:
                bad.append(("unknown move", list(mv), a + b))
                key = "fold"
            res[h][key] += 1
        return res, sizes, bad

    def context(self, s, tag):
        line = [(p, a, round(x / BB, 2)) for st, seat, a, x in s.history if a != "fold"
                for p in [next(q.position for q in s.players if q.seat == seat)]]
        eff = min(s.my_stack + s.my_bet,
                  max((p.stack + p.bet for p in s.players if p.seat != s.my_seat
                       and not p.folded), default=0))
        return {"part": tag, "pos": s.position, "players": len(s.players),
                "stack_bb": round((s.my_stack + s.my_bet) / BB, 2),
                "eff_bb": round(eff / BB, 2), "to_call_bb": round(s.to_call / BB, 2),
                "pot_bb": round(s.pot / BB, 2), "line": line,
                "stacks": {p.position: round((p.stack + p.bet) / BB, 2) for p in s.players}}

    def check(self, s, tag):
        res, sizes, bad = self.ask(s)
        ctx = self.context(s, tag)
        self.counts["spots"] += 1
        for kind, what, hand in bad[:5]:
            self.flag(kind, ctx, detail=what, hand=hand)
        if len(bad) > 5:
            self.flag("more of the above", ctx, n=len(bad))
        freq = {h: {k: v / sum(c.values()) for k, v in c.items()} for h, c in res.items()}
        play = {h: 1 - f.get("fold", 0) for h, f in freq.items()}
        if s.to_call:
            if freq.get("AA", {}).get("fold", 0) > 0:
                self.flag("AA folds", ctx, fold=round(freq["AA"]["fold"], 2))
            if freq.get("KK", {}).get("fold", 0) > 0.5:
                self.flag("KK folds", ctx, fold=round(freq["KK"]["fold"], 2))
            need = s.to_call / (s.pot + s.to_call)
            if need <= 0.10:
                folded = [h for h in CLASSES if freq[h].get("fold", 0) > 0.5]
                if folded:
                    self.flag("folds getting a huge price", ctx, need=round(need, 3),
                              hands=len(folded), e_g=folded[:6])
        # dominance: a hand plays while the hand dominating it folds
        # (a weaker hand raising as a bluff while the stronger one folds is normal in
        # polarized ranges, K5s 3-bets and K8s folds: counted, not flagged; a weaker hand
        # that calls while the stronger one folds is a problem)
        dom, bluff = [], 0
        acted = any(seat == s.my_seat and st == "preflop" for st, seat, a, _ in s.history)
        for better, worse in ([] if acted else DOM):   # (2nd decisions: most hands
            # never get there, e.g. 65s raised instead of limping, so no order to keep)
            if wheel_ok(better, worse):
                continue
            if play[worse] >= 0.75 and play[better] <= 0.25:
                f = freq[worse]
                if f.get("raise", 0) + f.get("jam", 0) >= f.get("call", 0):
                    bluff += 1
                else:
                    dom.append((better, worse))
        if bluff:
            self.counts["spots with polarized bluff raises"] += 1
        if dom:
            self.counts["spots with dominance"] += 1
            self.flag("dominance: weaker hand calls, stronger folds", ctx, n=len(dom),
                      e_g=dom[:6])
        # sizes
        voluntary = [(st, seat, a, x) for st, seat, a, x in s.history
                     if st == "preflop" and a in ("raise", "call")]
        raises = [x for st, seat, a, x in voluntary if a == "raise"]
        limps = [x for st, seat, a, x in voluntary if a == "call"]
        eff = ctx["eff_bb"] * BB
        all_sizes = [x for v in sizes.values() for x in v]
        if all_sizes:
            big = [x for x in all_sizes if x >= 0.5 * eff and x < s.max_raise_to - 1]
            if big:
                self.flag("raise of half the stack, not all-in", ctx,
                          raise_to_bb=round(max(big) / BB, 2), n=len(big))
            if not raises and limps:
                small = [x for x in all_sizes if x < 2.9 * BB]
                if small:
                    self.flag("small raise over a limp", ctx,
                              raise_to_bb=round(min(small) / BB, 2), n=len(small))
            if len(raises) == 1:
                small = [x for x in all_sizes if x < 2 * raises[0] - 1]
                if small:
                    self.flag("3-bet less than twice the raise", ctx,
                              raise_to_bb=round(min(small) / BB, 2),
                              open_bb=round(raises[0] / BB, 2), n=len(small))
        if not voluntary and s.position != "SB" and len(s.players) > 2 and s.to_call and \
                s.my_stack > s.to_call:   # (all-in for less is a call, not a limp)
            limp = [h for h in CLASSES if freq[h].get("call", 0) > 0]
            if limp:
                self.flag("open-limps", ctx, hands=len(limp), e_g=limp[:6])
        return freq, sizes


# ------------------------------------------------------------------ sweep: fixed lines

ORDER8 = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]


def sb_open(d):
    return 3.5 if d >= 40 else 3.0 if d >= 25 else 2.0


def three_bet(d, ip):
    if d < 25:
        return 2.0 * (2.65 if ip else 3.2)
    return 2.0 * (3.0 if ip else 4.0)


def lines():
    """(name, target position, {position: [(action, bb), ...]})."""
    out = []
    for p in ORDER8[:-1]:
        out.append((f"{p} first in", p, {}))
    for p in ORDER8[:-2]:
        out.append((f"BB vs {p} open", "BB", {p: [("raise", 2.0)]}))
    out.append(("BB vs SB open", "BB", {"SB": [("raise", None)]}))
    out.append(("BB vs SB limp", "BB", {"SB": [("call", 0)]}))
    out.append(("SB vs BTN open", "SB", {"BTN": [("raise", 2.0)]}))
    out.append(("BTN vs CO open", "BTN", {"CO": [("raise", 2.0)]}))
    out.append(("CO open vs BTN 3-bet", "CO", {"CO": [("raise", 2.0), "TARGET"],
                                              "BTN": [("raise", "3b_ip")]}))
    out.append(("BTN open vs BB 3-bet", "BTN", {"BTN": [("raise", 2.0), "TARGET"],
                                               "BB": [("raise", "3b_oop")]}))
    out.append(("BB vs BTN all-in", "BB", {"BTN": [("raise", "allin")]}))
    return out


class Script:
    def __init__(self, plan, target, depth, cb):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.target, self.depth, self.cb = target, depth, cb
        self.done = False

    def new_hand(self, *a):
        pass

    def hand_over(self, r):
        pass

    def act(self, s):
        p = s.position
        steps = self.plan.get(p)
        if steps:
            step = steps.pop(0)
            if step != "TARGET":
                a, x = step
                if a == "call":
                    return ("call", 0)
                bb = {None: sb_open(self.depth), "3b_ip": three_bet(self.depth, True),
                      "3b_oop": three_bet(self.depth, False)}.get(x, x)
                to = s.max_raise_to if x == "allin" else int(round(bb * BB))
                return ("raise", max(s.min_raise_to, min(s.max_raise_to, to)))
        if p == self.target and not self.done and not self.plan.get(p):
            self.done = True
            self.cb(s)
        return ("fold", 0) if s.to_call else ("check", 0)


def sweep(args):
    from engine import new_deck
    au = Audit(args.seed)
    depths = [x / 2 for x in range(16, 131)]   # 8 .. 65 bb
    table = {}
    for name, target, plan in lines():
        rows = {}
        for d in depths:
            got = {}

            def cb(s, d=d, got=got):
                freq, sizes = au.check(s, f"sweep {name}")
                got["freq"] = freq

            seats = [Script(plan, target, d, cb) for _ in range(8)]
            g = Game8(seats, stack=int(d * BB), big_blind=BB, ante=match8.ANTE,
                      time_limit_s=1e9)
            g.play_hand(new_deck(random.Random(1)), button=5, hand_no=0)
            if "freq" not in got:
                au.flag("line not reached", {"part": "sweep", "line": name, "depth": d})
                continue
            f = got["freq"]
            tot = lambda k: sum(f[h].get(k, 0) * (6 if len(h) == 2 else 4 if h[2] == "s" else 12)
                                for h in f) / 1326
            rows[d] = {"play": 1 - tot("fold"), "jam": tot("jam"), "raise": tot("raise"),
                       "call": tot("call")}
        table[name] = rows
        ds = sorted(rows)
        for a, b in zip(ds, ds[1:]):
            for k in ("play", "jam"):
                jump = rows[b][k] - rows[a][k]
                if abs(jump) > 0.08:
                    au.flag("jump between depths", {"part": "sweep", "line": name},
                            what=k, from_bb=a, to_bb=b, before=round(rows[a][k], 3),
                            after=round(rows[b][k], 3))
        print(f"  {name:24s} done ({au.samples:,} samples so far)", flush=True)
    show = [10, 12, 13, 15, 17, 20, 25, 30, 40, 50, 60]
    print("\n" + f"{'line':24s} " + " ".join(f"{d:>9g}" for d in show))
    for name, rows in table.items():
        cells = []
        for d in show:
            r = rows.get(d)
            cells.append(f"{100 * r['play']:4.0f}/{100 * r['jam']:<4.0f}" if r else "    -    ")
        print(f"{name:24s} " + " ".join(cells))
    print("(each cell: % of hands that play / % that go all-in)")
    return au, table


# ------------------------------------------------------------------ whole tables

class Watch:
    def __init__(self, bot, au, tag, rng, rate):
        self.bot, self.au, self.tag, self.rng, self.rate = bot, au, tag, rng, rate

    def new_hand(self, *a):
        self.bot.new_hand(*a)

    def hand_over(self, r):
        self.bot.hand_over(r)

    def act(self, s):
        if s.street == "preflop":
            raises = sum(1 for st, _, a, _ in s.history if st == "preflop" and a == "raise")
            p = self.rate * (1 if raises == 0 else 4 if raises == 1 else 10)
            if self.rng.random() < p:
                self.au.check(s, self.tag)
        return self.bot.act(s)


def tables(args):
    import final_table as FT
    import icm_study as I
    from engine import new_deck
    au = Audit(args.seed)
    T = au.T
    rng = random.Random(args.seed)
    ft = args.part == "ft"
    FT.POLICY.update(on=ft, icm=True, table=None)
    t0 = time.time()
    while au.samples < args.samples:
        if ft:
            table = FT.FinalTable(rng.randint(1, I.N_TABLES), I.pool_payouts(1_000_000), BB)
            FT.POLICY["table"] = table
        bots = [Watch(T.make_study(T.STUDY, rng.randrange(10 ** 6)), au, args.part, rng,
                      args.rate) for _ in range(8)]
        hands = 0
        depth = rng.uniform(10, 100)
        stacks = [int(depth * BB)] * 8
        while au.samples < args.samples and hands < (400 if ft else 30):
            if ft:
                if len(table.alive()) < 2:
                    break
                seats, b, st = table.start_hand()
                g = Game8([bots[x] for x in seats], big_blind=BB, ante=BB, ante_mode="bb",
                          time_limit_s=1e9)
                res = g.play_hand(new_deck(rng), button=b, hand_no=hands, stacks=st)
                table.finish_hand(seats, st, res)
            else:
                live = [x for x in range(8) if stacks[x] >= BB]
                if len(live) < 2:
                    break
                g = Game8([bots[x] for x in live], big_blind=BB, ante=match8.ANTE,
                          time_limit_s=1e9)
                res = g.play_hand(new_deck(rng), button=hands % len(live), hand_no=hands,
                                  stacks=[stacks[x] for x in live])
                for x, r in zip(live, res):
                    stacks[x] += r
            hands += 1
            au.counts["hands"] += 1
        if time.time() - t0 > 60:
            t0 = time.time()
            print(f"  {au.samples:,} samples, {au.counts['spots']} spots", flush=True)
    return au, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("part", choices=["sweep", "play", "ft"])
    ap.add_argument("--samples", type=int, default=500_000)
    ap.add_argument("--rate", type=float, default=0.03, help="share of preflop decisions asked")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="preflop_audit.jsonl")
    args = ap.parse_args()
    t0 = time.time()
    au, table = (sweep if args.part == "sweep" else tables)(args)
    with open(args.out, "w") as f:
        for fl in au.flags:
            f.write(json.dumps(fl) + "\n")
        if table:
            f.write(json.dumps({"rule": "(table)", "table": {k: {str(d): v for d, v in r.items()}
                                                            for k, r in table.items()}}) + "\n")
    au.timing.sort()
    p99 = au.timing[int(len(au.timing) * 0.99)] * 1000 if au.timing else 0
    print(json.dumps({"part": args.part, "samples": au.samples, "counts": dict(au.counts),
                      "minutes": round((time.time() - t0) / 60, 1), "p99_ms": round(p99, 2),
                      "by_rule": Counter(fl["rule"] for fl in au.flags)}, indent=1))


if __name__ == "__main__":
    main()
