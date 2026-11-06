"""Plays one bot against several 8-max fields at several depths, in parallel.

    python tournament8.py contender8
    python tournament8.py "contender8:value_3bet=0.22" --depths 50,100 --hands 1500

Bot specs as in tournament.py (module[.Class][:attr=value,...]).
"""

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

LAG8 = "contender8:semi_bluff=0.6;river_bluff=0.3;value_3bet=0.25;bluff_3bet_ip=1.2;value_thin=0.45"
NIT8 = "contender8:semi_bluff=0.0;river_bluff=0.0;value_3bet=0.10;bluff_3bet_ip=0.2;bluff_3bet_oop=0.1;value_thin=0.6"
FIELDS = {
    "starters": "starter8*7",
    "weak mix": "baselines.Maniac*2,baselines.CallingStation*2,baselines.RandomBot,starter8*2",
    "tough": f"contender8*3,{LAG8}*2,{NIT8}*2",
    "itself": "contender8*7",
    "6-handed": f"contender8*2,{LAG8},{NIT8},starter8",
    "5-handed": f"contender8,{LAG8},{NIT8},starter8",
    "heads-up": "contender8",
    # The saved previous version, to check that a change really is an improvement.
    "v2": "contender8_v2*7",
    "v2 6-handed": "contender8_v2*5",
    "v2 heads-up": "contender8_v2",
    "v2 mix": "contender8_v2*3,{0},{1},starter8*2".format(
        LAG8.replace("contender8:", "contender8_v2:"), NIT8.replace("contender8:", "contender8_v2:")),
}


def job(args):
    import match8
    hero_spec, field_spec, deals, seed, depth = args
    hero = match8.make(hero_spec, seed + 1)
    field = [match8.make(nm, seed + 10 + k) for k, nm in enumerate(match8.parse_field(field_spec))]
    if depth == "mixed":  # tournament-style: every seat a random 10-100 bb each deal
        per, st = match8.run8(hero, field, deals, seed, stack_range=(10, 100))
    else:
        per, st = match8.run8(hero, field, deals, seed, int(depth) * match8.BIG_BLIND)
    bb, ci = match8.summarise(per, len(field) + 1)
    return field_spec, depth, bb, ci, st.slow, st.decisions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot")
    ap.add_argument("--fields", default=",".join(FIELDS), help="names from: " + ", ".join(FIELDS))
    ap.add_argument("--depths", default="20,50,100,mixed",
                    help="stack depths in bb; 'mixed' = random 10-100 bb per seat")
    ap.add_argument("--hands", type=int, default=1500, help="deals per cell (x8 seatings)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    depths = [d.strip() for d in args.depths.split(",")]
    jobs = [(args.bot, FIELDS[f], args.hands, args.seed + 1000 * k + j, d)
            for k, f in enumerate(fields) for j, d in enumerate(depths)]
    with Pool() as p:
        res = p.map(job, jobs)
    cell = {(r[0], r[1]): r for r in res}
    print(f"{args.bot}, online tournament settings (ante 12.5% of the big blind each, no "
          f"rake), "
          f"{args.hands} deals x every seating per cell")
    print(f"{'field':<10}" + "".join(f"{d + ('' if d == 'mixed' else 'bb'):>19}"
                                     for d in depths))
    total = []
    for f in fields:
        row = "".join(f"{cell[(FIELDS[f], d)][2]:+11.1f} ±{cell[(FIELDS[f], d)][3]:5.1f}  "
                      for d in depths)
        print(f"{f:<10}{row}")
        total += [cell[(FIELDS[f], d)][2] for d in depths]
    slow = sum(r[4] for r in res)
    dec = sum(r[5] for r in res)
    print(f"average {sum(total) / len(total):+.1f} bb/100; moves over 1 ms: {slow} of {dec}")


if __name__ == "__main__":
    main()
