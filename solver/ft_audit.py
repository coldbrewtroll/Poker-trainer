"""Final table sanity audit: looks for plays that make no sense.

Two parts, run as separate processes (they can run in parallel):

    py -3.13 solver/ft_audit.py solver --minutes 60 --seed 1 --out audit_solver_1.jsonl
    py -3.13 solver/ft_audit.py game   --minutes 60 --seed 2 --out audit_game_2.jsonl

solver: random final-table spots (2-8 left, 3-40 bb, every position): first-in jams,
re-jams over a 2x open (against the opener's range) and calls of an all-in, solved by
icm_study's ICM equilibrium. Every hand of every spot is checked against rules any
correct answer keeps: a hand never does better than one that dominates it (A2 vs A3,
22 vs 33, offsuit vs suited), AA and KK always get it in, covered callers don't get a
negative risk premium.

game: whole final tables played by the trainer's bots (your sims + the final table's
rules, as in Final table mode). Each decision is checked: folding when checking is
free, folding getting huge odds, AA / KK folding preflop, folding a set or better,
opening and folding to a jam when committed; plus chip conservation and slow decisions.

Each problem found is one JSON line in --out; a summary prints at the end.
"""

import argparse
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import icm_study as I  # noqa: E402
import final_table as FT  # noqa: E402
from engine8 import position_names  # noqa: E402

RANKS = "23456789TJQKA"


def dominance_pairs():
    """(better, worse) hand pairs: higher pair; same top card, higher kicker, same
    suitedness; suited over offsuit."""
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


WHEEL = {"A5", "A4", "A3", "A2"}   # wheel aces: they can beat A6-A9 (straights, blockers)


def check_gain(kind, gain, ctx, flags, tol, material):
    """A dominated hand must not be in when the hand dominating it is out (by a margin
    that matters: `material`, in value units)."""
    for better, worse in DOM:
        if worse[:2] in WHEEL and better[0] == "A" and better[1] in "6789TJ":
            continue   # (wheel aces out-play dominated middle aces: straights, live cards)
        if gain[worse] > tol and gain[better] < -tol and gain[worse] - gain[better] > material:
            flags.append(dict(ctx, rule=f"{kind}: dominance", better=better, worse=worse,
                              g_better=round(gain[better], 2), g_worse=round(gain[worse], 2)))
    for h in ("AA", "KK"):
        if gain[h] < -tol:
            flags.append(dict(ctx, rule=f"{kind}: {h} out", gain=round(gain[h], 2)))


def check_calls(res, ctx, flags):
    names = I.exact()[0]
    idx = {h: i for i, h in enumerate(names)}
    for i, rng in res["call_ranges"].items():
        prior = res.get("priors", {}).get(i)
        for better, worse in DOM:
            if worse[:2] in WHEEL and better[0] == "A" and better[1] in "6789TJ":
                continue
            b, w = idx[better], idx[worse]
            if prior and (not prior[b] or not prior[w]):
                continue
            if rng[w] >= 0.75 and rng[b] <= 0.25:
                flags.append(dict(ctx, rule="call: dominance", caller=i, better=better,
                                  worse=worse))
        if rng[idx["AA"]] < 0.5 and (not prior or prior[idx["AA"]]):
            flags.append(dict(ctx, rule="call: AA folds", caller=i))


def solver_audit(args):
    rng = random.Random(args.seed)
    flags, n_spots, t_end = [], Counter(), time.time() + 60 * args.minutes
    while time.time() < t_end:
        n = rng.randint(2, 8)
        st = [round(3 * (40 / 3) ** rng.random(), 1) for _ in range(n)]
        if rng.random() < 0.15:   # a monster stack
            st[rng.randrange(n)] = round(rng.uniform(60, 120), 1)
        standard = rng.random() < 0.8
        pays = I.pool_payouts(1_000_000)[:n] if standard else \
            sorted((rng.uniform(1, 10) for _ in range(n)), reverse=True)
        val, scale = I.icm_value(pays), sum(pays)
        tol, material = 1e-4 * scale, 2e-3 * scale
        kind = rng.choice(["jam", "jam", "rejam"]) if n >= 3 else "jam"
        ctx = {"stacks": st, "n": n, "pays": [round(p) for p in pays], "standard": standard}
        try:
            if kind == "jam":
                hero = rng.randrange(n - 1)
                ctx.update(kind="jam", hero=hero, pos=I.positions(n)[hero])
                res = I.solve_jam(st, val, hero)
                chip = I.solve_jam(st, I.chips_value, hero)
                check_gain("jam", res["gain"], ctx, flags, tol, material)
                if standard:
                    check_calls(res, ctx, flags)
                sp = res["spot"]
                for i in sp.behind:   # risk premium of a covered caller
                    if st[i] <= st[hero] and res["calls"][i][1] < chip["calls"][i][1] - 0.02:
                        flags.append(dict(ctx, rule="negative risk premium", caller=i,
                                          need_icm=round(res["calls"][i][1], 3),
                                          need_chip=round(chip["calls"][i][1], 3)))
            else:
                j = rng.randrange(n - 2)
                hero = rng.randrange(j + 1, n)
                posts = [0.0] * n
                posts[n - 2], posts[n - 1] = 0.5, 1.0
                posts[j] = 2.0
                eff_j = min(st[j], max(st[k] for k in range(n) if k != j))
                prior = FT.open_range(I.positions(n)[j] if n > 2 else "SB", eff_j)
                responders = [j] + [k for k in range(hero + 1, n)]
                ctx.update(kind="rejam", hero=hero, opener=j, pos=I.positions(n)[hero])
                res = I.solve_jam(st, val, hero, posts=posts, responders=responders, fold_to=j,
                                  priors={j: prior})
                check_gain("rejam", res["gain"], ctx, flags, tol, material)
            n_spots[kind] += 1
        except Exception as e:   # a crash is a finding too
            flags.append(dict(ctx, rule="crash", error=repr(e)))
    return flags, dict(n_spots)


# ------------------------------------------------------------------ the game audit

def hand_class(c1, c2):
    return I.hand_class(c1, c2)


def game_audit(args):
    import match8
    import trainer8 as T
    from cards import card, evaluate, category
    from engine import new_deck
    from engine8 import Game8
    T.load_study_table()
    BB = match8.BIG_BLIND
    rng = random.Random(args.seed)
    flags, counts = [], Counter()
    t_end = time.time() + 60 * args.minutes
    table_no = rng.randint(1, I.N_TABLES)
    timing = []

    class Watch:
        def __init__(self, bot, seat):
            self.bot, self.seat = bot, seat

        def new_hand(self, *a):
            self.bot.new_hand(*a)

        def hand_over(self, r):
            self.bot.hand_over(r)

        def act(self, s):
            t0 = time.time()
            mv = self.bot.act(s)
            dt = time.time() - t0
            timing.append(dt)
            counts["decisions"] += 1
            ctx = {"table": table_no, "pos": s.position, "street": s.street,
                   "hole": list(s.hole), "board": list(s.board),
                   "stack_bb": round((s.my_stack + s.my_bet) / BB, 2),
                   "to_call_bb": round(s.to_call / BB, 2), "pot_bb": round(s.pot / BB, 2),
                   "move": list(mv),
                   "line": [(st, a, round(amt / BB, 2)) for st, _, a, amt in s.history
                            if a != "fold"][-6:]}
            if dt > 1.0:
                flags.append(dict(ctx, rule="slow decision", seconds=round(dt, 2)))
            if mv[0] == "fold" and s.to_call == 0:
                flags.append(dict(ctx, rule="folds when it can check"))
            if mv[0] == "fold" and s.to_call:
                need = s.to_call / (s.pot + s.to_call)
                hc = hand_class(*s.hole)
                if s.street == "preflop":
                    if hc in ("AA", "KK"):
                        flags.append(dict(ctx, rule=f"{hc} folds preflop", need=round(need, 3)))
                    if need <= 0.10:
                        flags.append(dict(ctx, rule="folds getting huge odds",
                                          need=round(need, 3)))
                    me_raised = any(seat == s.my_seat and a == "raise" for st_, seat, a, _ in
                                    s.history if st_ == "preflop")
                    if me_raised and s.my_bet >= 0.4 * (s.my_stack + s.my_bet):
                        flags.append(dict(ctx, rule="raise-folds committed",
                                          committed=round(s.my_bet / (s.my_stack + s.my_bet), 2)))
                else:
                    cat = category(evaluate([card(c) for c in list(s.hole) + list(s.board)]))
                    ranks_b = [c[0] for c in s.board]
                    most = max(ranks_b.count(r) for r in ranks_b)
                    on_board = category(evaluate([card(c) for c in s.board])) if \
                        len(s.board) >= 5 else (3 if most >= 3 else 1 if most == 2 else 0)
                    if cat >= 3 and cat > on_board:   # (your cards make it, not the board)
                        flags.append(dict(ctx, rule="folds trips/set or better",
                                          category=cat, need=round(need, 3)))
                    elif need <= 0.10:
                        flags.append(dict(ctx, rule="folds postflop getting huge odds",
                                          need=round(need, 3)))
            counts[f"{s.street}:{mv[0]}"] += 1
            return mv

    FT.POLICY.update(on=True, icm=True)
    while time.time() < t_end:
        table_no = rng.randint(1, I.N_TABLES)
        ft = FT.FinalTable(table_no, I.pool_payouts(1_000_000), BB)
        FT.POLICY["table"] = ft
        bots = {x: Watch(T.make_study(T.STUDY, rng.randrange(10 ** 6)), x) for x in range(8)}
        hands = 0
        while len(ft.alive()) > 1 and hands < 400 and time.time() < t_end:
            seats, b, stacks = ft.start_hand()
            g = Game8([bots[x] for x in seats], big_blind=BB, ante=BB, ante_mode="bb",
                      time_limit_s=1e9)
            before = sum(stacks)
            try:
                res = g.play_hand(new_deck(rng), button=b, hand_no=hands, stacks=stacks)
            except Exception as e:
                flags.append({"rule": "crash in a hand", "error": repr(e), "table": table_no})
                break
            if abs(sum(res)) > 1e-6:
                flags.append({"rule": "chips not conserved", "table": table_no,
                              "sum": sum(res)})
            ft.finish_hand(seats, stacks, res)
            if any(v < 0 for v in ft.stacks) or abs(sum(ft.stacks) - before) > 2:
                flags.append({"rule": "stack error", "table": table_no,
                              "stacks": list(ft.stacks)})
            hands += 1
            counts["hands"] += 1
        counts["tables"] += 1
    timing.sort()
    counts["p99_ms"] = round(1000 * timing[int(len(timing) * 0.99)]) if timing else 0
    return flags, dict(counts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("part", choices=["solver", "game"])
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="ft_audit.jsonl")
    args = ap.parse_args()
    flags, counts = (solver_audit if args.part == "solver" else game_audit)(args)
    with open(args.out, "w") as f:
        for fl in flags:
            f.write(json.dumps(fl) + "\n")
    print(json.dumps({"part": args.part, "counts": counts,
                      "by_rule": Counter(fl["rule"] for fl in flags)}, indent=1))


if __name__ == "__main__":
    main()
