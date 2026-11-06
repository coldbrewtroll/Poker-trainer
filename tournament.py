"""Plays one bot against a pool of opponents at several stack depths, in parallel, and
reports bb/100 for each pairing plus the average.

    python tournament.py contender
    python tournament.py "contender:value_thin=0.58,semi_bluff=0.4" --depths 50,100
    python tournament.py contender --pool starter,contender.Classic --hands 10000

A bot spec is module[.Class][:attr=value,...]; the attributes are set on the bot, which
is how parameters are tuned without editing code. Use ; instead of , between settings
inside an 8-max field list (where commas separate the players).
"""

import argparse
import math
import re
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_POOL = ["baselines.CallingStation", "baselines.Maniac", "baselines.RandomBot",
                "starter", "shortstack", "contender.Classic", "contender"]


def make(spec, seed):
    import match
    name, _, params = spec.partition(":")
    bot = match.load(name, seed)
    for kv in filter(None, re.split(r"[,;]", params)):
        k, v = kv.split("=")
        setattr(bot, k.strip(), type(getattr(bot, k.strip()))(v))
    return bot


def job(args):
    import match
    a, b, hands, seed, stack = args
    per_pair, stats = match.run(make(a, seed + 1), make(b, seed + 2), hands, seed, stack)
    n = len(per_pair)
    mean = sum(per_pair) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in per_pair) / max(n - 1, 1))
    scale = 1 / 2 / 2 * 100  # two hands per pair, big blind = 2 chips, per 100 hands
    return b, stack, mean * scale, 1.96 * sd / math.sqrt(n) * scale, stats[0].slow


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot")
    ap.add_argument("--pool", default=",".join(DEFAULT_POOL))
    ap.add_argument("--depths", default="20,50,100", help="stack depths in big blinds")
    ap.add_argument("--hands", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    pool = [p for p in args.pool.split(",") if p]
    depths = [int(d) for d in args.depths.split(",")]
    jobs = [(args.bot, opp, args.hands, args.seed + 101 * k, d * 2)
            for k, (opp, d) in enumerate((o, d) for o in pool for d in depths)]
    with Pool() as p:
        results = p.map(job, jobs)
    table = {}
    for opp, stack, bb100, ci, slow in results:
        table[(opp, stack // 2)] = (bb100, ci, slow)
    w = max(len(o) for o in pool)
    print(f"{args.bot} vs pool ({args.hands} duplicate hands each)")
    print(" " * w + "".join(f"{d:>16}bb" for d in depths))
    for opp in pool:
        cells = "".join(f"{table[(opp, d)][0]:+10.1f} ±{table[(opp, d)][1]:5.1f}  "
                        for d in depths)
        print(f"{opp:<{w}} {cells}")
    avg = {d: sum(table[(o, d)][0] for o in pool) / len(pool) for d in depths}
    print(f"{'AVERAGE':<{w}} " + "".join(f"{avg[d]:+10.1f}         " for d in depths))
    print(f"overall average: {sum(avg.values()) / len(avg):+.2f} bb/100; "
          f"moves over 1 ms: {sum(v[2] for v in table.values())}")


if __name__ == "__main__":
    main()
