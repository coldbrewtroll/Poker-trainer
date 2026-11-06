"""Parameter sweep for the 8-max bot: each variant plays the tuning fields (tough table,
6-handed, starters) at 20 bb, 50 bb and mixed stacks; prints the averages.

    python sweep8.py value_3bet=0.13,0.22 ante_widen=0.15,0.5 --hands 600
"""

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tournament8 import FIELDS, job  # noqa: E402

TUNE_FIELDS = ["tough", "6-handed", "starters"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params", nargs="+")
    ap.add_argument("--bot", default="contender8")
    ap.add_argument("--depths", default="20,50,mixed")
    ap.add_argument("--hands", type=int, default=600)
    ap.add_argument("--fields", default=",".join(TUNE_FIELDS),
                    help="fields from tournament8.FIELDS, e.g. 'v2,v2 6-handed'")
    args = ap.parse_args()
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    base, _, base_params = args.bot.partition(":")
    variants = [args.bot]
    for p in args.params:
        name, values = p.split("=")
        for v in values.split(","):
            variants.append(f"{base}:{base_params + ';' if base_params else ''}{name}={v}")
    depths = [d.strip() for d in args.depths.split(",")]
    jobs = [(var, FIELDS[f], args.hands, 1000 * k + j, d)
            for var in variants for k, f in enumerate(fields) for j, d in enumerate(depths)]
    with Pool() as p:
        res = p.map(job, jobs)
    per = len(fields) * len(depths)
    for i, var in enumerate(variants):
        chunk = res[i * per:(i + 1) * per]
        avg = sum(r[2] for r in chunk) / per
        cells = " ".join(f"{r[2]:+6.1f}" for r in chunk)
        print(f"{avg:+7.2f} avg | {cells} | {var.partition(':')[2] or '(default)'}", flush=True)


if __name__ == "__main__":
    main()
