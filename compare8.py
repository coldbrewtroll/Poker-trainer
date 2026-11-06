"""Paired comparison of two 8-max bots: both play exactly the same deals, seats and
opponents, so the difference between them is measured with far less noise than two
separate runs.

    python compare8.py contender8 "contender8:flat_small3=0"
    python compare8.py contender8 contender8_v2 --fields "v2,v2 mix" --depths 15,40,100

Fields are names from tournament8.FIELDS (or a field spec like "starter8*7"). Prints, per
cell and overall, how many bb/100 the first bot wins MORE than the second, with a 95% range.
Postflop equity uses a fixed sample count while comparing (POKERBOT_FIXED_SAMPLES, default
20), so a deal plays the same for both bots unless one of them decides differently.
"""

import argparse
import math
import os
import sys
from multiprocessing import Pool
from pathlib import Path

os.environ.setdefault("POKERBOT_FIXED_SAMPLES", "20")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match8  # noqa: E402
from tournament8 import FIELDS  # noqa: E402


def cell(args):
    a, b, field_spec, deals, seed, depth = args
    out = []
    for spec in (a, b):
        hero = match8.make(spec, seed + 1)
        field = [match8.make(nm, seed + 10 + k)
                 for k, nm in enumerate(match8.parse_field(field_spec))]
        kw = {}
        if depth == "mixed":
            kw["stack_range"] = (10, 100)
        else:
            kw["stack"] = int(float(depth) * match8.BIG_BLIND)
        per, _ = match8.run8(hero, field, deals, seed=seed, **kw)
        out.append(per)
    n_seats = len(match8.parse_field(field_spec)) + 1
    diffs = [x - y for x, y in zip(*out)]
    return diffs, n_seats


def stats(diffs, n_seats):
    n = len(diffs)
    mean = sum(diffs) / n
    sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / max(n - 1, 1))
    scale = 100 / n_seats / match8.BIG_BLIND
    return mean * scale, 1.96 * sd / math.sqrt(n) * scale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--fields", default="v2,v2 6-handed,v2 mix")
    ap.add_argument("--depths", default="15,25,40,60,100")
    ap.add_argument("--hands", type=int, default=500, help="deals per cell")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    depths = [d.strip() for d in args.depths.split(",")]
    jobs = [(args.a, args.b, FIELDS.get(f, f), args.hands, args.seed + 1000 * k + j, d)
            for k, f in enumerate(fields) for j, d in enumerate(depths)]
    with Pool() as p:
        res = p.map(cell, jobs)
    print(f"{args.a}  minus  {args.b}  (bb/100, 95% range), {args.hands} deals per cell")
    print(f"{'field':<14}" + "".join(f"{d + ('' if d == 'mixed' else 'bb'):>16}" for d in depths))
    k = 0
    scaled = []
    for f in fields:
        row = ""
        for _ in depths:
            diffs, n_seats = res[k]
            k += 1
            m, ci = stats(diffs, n_seats)
            row += f"{m:+8.1f} ±{ci:5.1f} "
            scale = 100 / n_seats / match8.BIG_BLIND
            scaled.append([d * scale for d in diffs])
        print(f"{f:<14}{row}")
    # Overall: every cell weighted equally.
    means = [sum(c) / len(c) for c in scaled]
    var = [sum((d - m) ** 2 for d in c) / max(len(c) - 1, 1) / len(c) for c, m in zip(scaled, means)]
    overall = sum(means) / len(means)
    ci = 1.96 * math.sqrt(sum(var)) / len(means)
    print(f"overall {overall:+.2f} bb/100  (95% range {overall - ci:+.2f} to {overall + ci:+.2f})")


if __name__ == "__main__":
    main()
