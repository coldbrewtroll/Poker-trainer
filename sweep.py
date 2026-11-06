"""Parameter sweep: each variant of a bot plays the strong pool; prints the averages.

    python sweep.py value_thin=0.55,0.66 semi_bluff=0.15,0.45 --hands 8000
"""

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tournament import job  # noqa: E402

LAG = "contender:semi_bluff=0.7,river_bluff=0.4,value_thin=0.5,margin_base=-0.02"
NIT = "contender:semi_bluff=0.0,river_bluff=0.0,value_thin=0.72,margin_base=0.08"
STRONG_POOL = ["starter", "shortstack", "contender.Classic", "contender", LAG, NIT]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params", nargs="+", help="name=v1,v2,... (each value tried alone)")
    ap.add_argument("--bot", default="contender")
    ap.add_argument("--depths", default="20,50,100")
    ap.add_argument("--hands", type=int, default=8000)
    args = ap.parse_args()

    base, _, base_params = args.bot.partition(":")
    variants = [args.bot]
    for p in args.params:
        name, values = p.split("=")
        for v in values.split(","):
            extra = f"{name}={v}"
            variants.append(f"{base}:{base_params + ',' if base_params else ''}{extra}")
    depths = [int(d) for d in args.depths.split(",")]
    jobs = []
    for vi, var in enumerate(variants):
        for oi, opp in enumerate(STRONG_POOL):
            for d in depths:
                # Same seeds for every variant, so they face identical cards.
                jobs.append((var, opp, args.hands, 1000 * oi + d, d * 2))
    with Pool() as p:
        res = p.map(job, jobs)
    k = 0
    per = len(STRONG_POOL) * len(depths)
    for var in variants:
        chunk = res[k:k + per]
        k += per
        avg = sum(r[2] for r in chunk) / per
        vs_default = [r[2] for r, (_, opp, *_rest) in zip(chunk, jobs[:per]) if opp == "contender"]
        print(f"{avg:+7.2f} bb/100 avg | vs default contender {sum(vs_default) / len(vs_default):+6.1f}"
              f" | {var.partition(':')[2] or '(default)'}", flush=True)


if __name__ == "__main__":
    main()
