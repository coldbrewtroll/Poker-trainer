"""ICM check: the trainer's ICM numbers against an independent brute-force version.

    py -3.13 solver/icm_check.py                (everything, a few minutes)
    py -3.13 solver/icm_check.py --tables 250   (how many preset tables to play out)

1. ICM equity: icm_study.icm against every finishing order spelled out (the Malmuth-Harville
   model: the next place goes to each remaining player in proportion to their chips), on
   random tables of 2-9 players, with fewer prizes than players, and with busted players.
   Plus what any correct ICM must do: the equities add up to the prizes, nobody gets more
   than 1st or less than the last prize, more chips are never worth less, equal stacks are
   worth the same.
2. Payouts: every prize pool (the three presets and random custom pools) pays nine places,
   in whole dollars, largest first, adding up to the pool exactly, in the same proportions.
3. Final tables played out (the trainer's bots): every place from 8th to 1st paid exactly
   once, at its payout; players out in the same hand placed by their starting stacks
   (ties split); the prize money (paid + everyone's ICM equity) always adds up to the
   eight places' payouts; chips are never created or lost.
4. The ICM Coach's numbers (coach_facts) at random decisions: your equity, the prize money
   left, your chance of being the next one out, and the risk premium against each player,
   each worked out again independently.
Prints each part's result; exits with an error if anything is off.
"""

import argparse
import itertools
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import icm_study as I  # noqa: E402
import final_table as FT  # noqa: E402
import match8  # noqa: E402

BB = match8.BIG_BLIND
TOL = 1e-6


# ------------------------------------------------------------------ the independent ICM

def icm_brute(stacks, pays):
    """Every finishing order of the players with chips, each with its Malmuth-Harville
    probability; busted players share the places below them equally."""
    n = len(stacks)
    pays = (list(pays) + [0.0] * n)[:n]
    alive = [i for i in range(n) if stacks[i] > 0]
    dead = [i for i in range(n) if stacks[i] <= 0]
    eq = [0.0] * n
    for order in itertools.permutations(alive):
        p, left = 1.0, sum(stacks[i] for i in alive)
        for i in order:
            p *= stacks[i] / left
            left -= stacks[i]
        for place, i in enumerate(order):
            eq[i] += p * pays[place]
    if dead:
        low = pays[len(alive):]
        for i in dead:
            eq[i] = sum(low) / len(low)
    return eq


def need_brute(stacks, pays, i, j):
    """Equity player i needs to call player j's all-in (no blinds), in prize money."""
    a = min(stacks[i], stacks[j])
    w, l = list(stacks), list(stacks)
    w[i], w[j] = stacks[i] + a, stacks[j] - a
    l[i], l[j] = stacks[i] - a, stacks[j] + a
    base, vw, vl = (icm_brute(stacks, pays)[i], icm_brute(w, pays)[i],
                    icm_brute(l, pays)[i])
    return (base - vl) / (vw - vl)


class Check:
    def __init__(self):
        self.fails = []

    def ok(self, cond, what):
        if not cond:
            self.fails.append(what)
            if len(self.fails) <= 20:
                print("   FAIL:", what)
        return cond


# ------------------------------------------------------------------ 1. ICM equity

def part_icm(ck, rng, n_cases):
    worst = 0.0
    for case in range(n_cases):
        n = rng.randint(2, 8 if case % 10 else 9)
        st = [round(rng.uniform(0.5, 100), 2) for _ in range(n)]
        if rng.random() < 0.15:
            st[rng.randrange(n)] = 0.0                       # a busted player
        if sum(1 for x in st if x > 0) < 1:
            continue
        k = rng.randint(1, n)                                # fewer prizes than players
        pays = sorted((round(rng.uniform(1, 1000), 2) for _ in range(k)), reverse=True)
        if rng.random() < 0.5:
            pays = I.pool_payouts(rng.choice([500_000, 1_000_000, 1_500_000]))[:n]
        a, b = I.icm(st, pays), icm_brute(st, pays)
        d = max(abs(x - y) for x, y in zip(a, b)) / max(pays)
        worst = max(worst, d)
        ck.ok(d < 1e-9, f"icm differs from brute force by {d:.2e} (of 1st) at {st} {pays}")
        full = (list(pays) + [0.0] * n)[:n]
        ck.ok(abs(sum(a) - sum(full)) < 1e-6 * sum(full),
              f"equities add to {sum(a)}, prizes {sum(full)}")
        ck.ok(all(min(full) - 1e-9 <= x <= max(full) + 1e-9 for x in a),
              f"an equity outside the prizes at {st}")
        live = [i for i in range(n) if st[i] > 0]
        for i, j in itertools.combinations(live, 2):
            if st[i] > st[j] + 1e-9:
                ck.ok(a[i] >= a[j] - 1e-9, f"more chips worth less: {st[i]} vs {st[j]}")
            if abs(st[i] - st[j]) < 1e-12:
                ck.ok(abs(a[i] - a[j]) < 1e-9, f"equal stacks, different equity at {st}")
    # A worked example everyone can check by hand: 3 players, 50/30/20 chips, 50/30/20 pay.
    ex = I.icm([50, 30, 20], [50, 30, 20])
    want = [1075 / 28, 131 / 4, 202 / 7]    # 38.39, 32.75, 28.86 (worked out exactly)
    ck.ok(all(abs(x - y) < 1e-6 for x, y in zip(ex, want)),
          f"the 50/30/20 example: {ex}")
    return f"{n_cases} random tables, largest difference {worst:.1e} of 1st prize"


# ------------------------------------------------------------------ 2. payouts

def part_payouts(ck, rng):
    pools = [500_000, 1_000_000, 1_500_000] + [rng.randint(1, 50_000) * 100
                                              for _ in range(200)]
    shape = [c / sum(I.CIRCUIT) for c in I.CIRCUIT]
    for pool in pools:
        p = I.pool_payouts(pool)
        ck.ok(len(p) == 9, f"{pool}: {len(p)} places")
        ck.ok(abs(sum(p) - pool) < 1e-6, f"{pool}: pays {sum(p)}")
        ck.ok(all(x == int(x) for x in p), f"{pool}: cents in {p}")
        ck.ok(all(a >= b for a, b in zip(p, p[1:])), f"{pool}: not largest first {p}")
        ck.ok(all(abs(x / pool - s) < 2e-6 * 9 + 1 / pool for x, s in zip(p, shape)),
              f"{pool}: shape {p}")
    one = I.pool_payouts(1_000_000)
    return ("1M pool: " + ", ".join(f"{k + 1}. ${x:,.0f} ({100 * x / 1e6:.1f}%)"
                                     for k, x in enumerate(one)))


# ------------------------------------------------------------------ 3. tables played out

def part_tables(ck, rng, n_tables):
    import trainer8 as T
    from engine import new_deck
    from engine8 import Game8
    T.load_study_table()
    FT.POLICY.update(on=True, icm=True)
    ties = simultaneous = hands_total = 0
    for t in range(1, n_tables + 1):
        pool = [500_000, 1_000_000, 1_500_000, rng.randint(5, 300) * 10_000][t % 4]
        pays9 = I.pool_payouts(pool)
        ft = FT.FinalTable(t, pays9, BB)
        ft.level_hands = [15, 20, 30, 0][(t // 4) % 4]
        FT.POLICY["table"] = ft
        bots = [T.make_study(T.STUDY, rng.randrange(10 ** 6)) for _ in range(8)]
        total = sum(ft.stacks)
        target = sum(pays9[:8])
        hands = 0
        limit = 600 if ft.level_hands else 5000    # (blinds that never go up: heads-up
        while len(ft.alive()) > 1 and hands < limit:  # between deep stacks can go long)
            seats, b, stacks = ft.start_hand()
            if ft.note.startswith("Blinds up"):
                total = sum(ft.stacks)          # (stacks rescale to the new big blind)
            g = Game8([bots[x] for x in seats], big_blind=BB, ante=BB, ante_mode="bb",
                      time_limit_s=1e9)
            res = g.play_hand(new_deck(rng), button=b, hand_no=hands, stacks=stacks)
            info = ft.finish_hand(seats, stacks, res)
            hands += 1
            ck.ok(abs(sum(ft.stacks) - total) < 1e-6, f"table {t}: chips {sum(ft.stacks)} "
                  f"vs {total}")
            eq = ft.equities()
            ck.ok(abs(sum(eq.values()) - target) < 1e-6 * target,
                  f"table {t} hand {hands}: prize money adds to {sum(eq.values()):.2f}, "
                  f"not {target:.2f}")
            if len(info["busts"]) > 1:
                simultaneous += 1
                by_seat = {x: stacks[seats.index(x)] for x, _, _ in info["busts"]}
                for (x1, p1, z1), (x2, p2, z2) in itertools.combinations(info["busts"], 2):
                    if by_seat[x1] == by_seat[x2]:
                        ties += 1
                        ck.ok(p1 == p2 and abs(z1 - z2) < 1e-9,
                              f"table {t}: a tie not split ({p1} {z1} / {p2} {z2})")
                    else:
                        big = (p1, z1) if by_seat[x1] > by_seat[x2] else (p2, z2)
                        small = (p2, z2) if by_seat[x1] > by_seat[x2] else (p1, z1)
                        ck.ok(big[0] < small[0] and big[1] >= small[1],
                              f"table {t}: the bigger stack finished lower")
        hands_total += hands
        if not ck.ok(len(ft.alive()) == 1, f"table {t}: no winner after {hands} hands"):
            continue
        places = sorted(p for p, _ in ft.out.values())
        paid = sum(z for _, z in ft.out.values())
        ck.ok(len(ft.out) == 8, f"table {t}: {len(ft.out)} players placed")
        ck.ok(abs(paid - target) < 1e-6, f"table {t}: paid {paid}, places pay {target}")
        # places 1-8 each once, except players out together with equal stacks: a tie of
        # c players is labelled with the best of the c places it covers (7th and 8th: 7)
        cover, i = [], 0
        while i < len(places):
            c = places.count(places[i])
            cover += list(range(places[i], places[i] + c))
            i += c
        ck.ok(cover == list(range(1, 9)), f"table {t}: places {places}")
        for x, (p, z) in ft.out.items():
            tied = [y for y, (q, _) in ft.out.items() if q == p]
            if len(tied) == 1:
                ck.ok(abs(z - pays9[p - 1]) < 1e-9, f"table {t}: {p} paid {z}")
    return (f"{n_tables} tables to a winner, {hands_total} hands, {simultaneous} hands with "
            f"several players out ({ties} ties)")


# ------------------------------------------------------------------ 4. the coach's numbers

def part_coach(ck, rng, n_spots):
    import trainer8 as T
    from engine import new_deck
    from engine8 import Game8
    T.load_study_table()
    FT.POLICY.update(on=True, icm=True)
    seen = {"info": 0, "jam": 0, "call": 0}
    got = []

    class Grab:
        def __init__(self, bot, hero):
            self.bot, self.hero = bot, hero

        def new_hand(self, *a):
            self.bot.new_hand(*a)

        def hand_over(self, r):
            self.bot.hand_over(r)

        def act(self, s):
            if self.hero and s.street == "preflop":
                got.append(FT.coach_spot(s, FT.POLICY["table"]))
            return self.bot.act(s)

    while sum(seen.values()) < n_spots:
        t = rng.randint(1, I.N_TABLES)
        pays9 = I.pool_payouts(rng.choice([500_000, 1_000_000, 1_500_000]))
        ft = FT.FinalTable(t, pays9, BB)
        FT.POLICY["table"] = ft
        bots = [Grab(T.make_study(T.STUDY, rng.randrange(10 ** 6)), x == 0) for x in range(8)]
        for h in range(rng.randint(0, 25)):
            if len(ft.alive()) < 2 or 0 not in ft.alive():
                break
            got.clear()
            seats, b, stacks = ft.start_hand()
            g = Game8([bots[x] for x in seats], big_blind=BB, ante=BB, ante_mode="bb",
                      time_limit_s=1e9)
            res = g.play_hand(new_deck(rng), button=b, hand_no=h, stacks=stacks)
            for sp in got[:1]:
                FT.ensure_solved(sp)
                f = I.ICMWindow.coach_facts(type("Host", (), {"tspot": sp})())
                st, pays, me = sp["stacks"], sp["pays"], sp["hero"]
                n = len(st)
                eq = icm_brute(st, pays)
                ck.ok(abs(f["eq_now"] - eq[me]) < 0.01, f"coach equity {f['eq_now']} vs {eq[me]}")
                ck.ok(abs(f["pool"] - sum(pays9[:n])) < 0.01,
                      f"prize money left {f['pool']} vs {sum(pays9[:n])}")
                ck.ok(abs(f["chip_share"] - st[me] / sum(st)) < 1e-9, "chip share")
                last = icm_brute(st, [0.0] * (n - 1) + [1.0])[me]
                ck.ok(abs(I.icm(st, [0.0] * (n - 1) + [1.0])[me] - last) < 1e-9,
                      "chance of being next out")
                rp = I.risk_premiums(st, I.icm_value(pays))
                for k in range(n):
                    if k != me:
                        want = 100 * (need_brute(st, pays, me, k) - 0.5)
                        ck.ok(abs(rp[me][k] - want) < 1e-6,
                              f"risk premium vs {k}: {rp[me][k]:.4f} / {want:.4f}")
                if sp["kind"] == "call":
                    sp_ = sp["res"]["spot"]
                    j = sp["jammer"]
                    # the call's prize money: win, lose and fold worked out again
                    ck.ok(abs(f["win"] - sp_.lose[me][me]) < 1e-9 and
                          abs(f["fold"] - sp_.v_steal[me]) < 1e-9, "call: win / fold value")
                    ck.ok(f["jammer"] == I.positions(n)[j], "call: who jammed")
                seen[sp["kind"]] += 1
            ft.finish_hand(seats, stacks, res)
    return f"{sum(seen.values())} decisions ({seen['info']} info, {seen['jam']} jam, " \
           f"{seen['call']} call)"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--icm-cases", type=int, default=3000)
    ap.add_argument("--tables", type=int, default=250)
    ap.add_argument("--coach-spots", type=int, default=400)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    ck = Check()
    for name, fn in (("1. ICM equity", lambda: part_icm(ck, rng, args.icm_cases)),
                     ("2. Payouts", lambda: part_payouts(ck, rng)),
                     ("3. Final tables played out", lambda: part_tables(ck, rng, args.tables)),
                     ("4. The ICM Coach's numbers", lambda: part_coach(ck, rng, args.coach_spots))):
        before = len(ck.fails)
        print(f"{name} ...", flush=True)
        summary = fn()
        bad = len(ck.fails) - before
        print(f"   {'OK' if not bad else f'{bad} PROBLEMS'}: {summary}", flush=True)
    print("\nAll correct." if not ck.fails else f"\n{len(ck.fails)} problems.")
    sys.exit(1 if ck.fails else 0)


if __name__ == "__main__":
    main()
