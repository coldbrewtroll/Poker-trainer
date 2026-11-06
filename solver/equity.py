"""Preflop all-in equity between the 169 starting-hand types, plus exact combo weights.

    python solver/equity.py --samples 1000      # ~2 minutes on 4 cores

Writes solver/preflop_equity.npz with:
    names    169 hand names ("AA", "AKs", "AKo", ...)
    equity   equity[i, j] = chance hand i beats hand j all-in preflop (ties count half)
    weight   weight[i, j] = how many card combinations of i vs j can be dealt together
             (card removal: AA vs AKs is 4*... fewer than two unrelated hands)
"""

import argparse
import random
import sys
from multiprocessing import Pool
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cards import RANKS, evaluate  # noqa: E402


def hand_types():
    """(name, list of 2-card combos) for all 169 types, strongest-looking first."""
    out = []
    for hi in range(12, -1, -1):
        for lo in range(hi, -1, -1):
            if hi == lo:
                combos = [(hi * 4 + a, hi * 4 + b) for a in range(4) for b in range(a + 1, 4)]
                out.append((RANKS[hi] * 2, combos))
            else:
                suited = [(hi * 4 + s, lo * 4 + s) for s in range(4)]
                off = [(hi * 4 + a, lo * 4 + b) for a in range(4) for b in range(4) if a != b]
                out.append((RANKS[hi] + RANKS[lo] + "s", suited))
                out.append((RANKS[hi] + RANKS[lo] + "o", off))
    return out


def type_of(c1: int, c2: int) -> str:
    """Hand-type name of two card ints, e.g. (As, Kd) -> "AKo"."""
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 < r2:
        r1, r2 = r2, r1
    if r1 == r2:
        return RANKS[r1] * 2
    return RANKS[r1] + RANKS[r2] + ("s" if (c1 & 3) == (c2 & 3) else "o")


def _matchup(args):
    i, j, combos_i, combos_j, samples, seed = args
    rng = random.Random(seed)
    pairs = [(a, b) for a in combos_i for b in combos_j if len({*a, *b}) == 4]
    if not pairs:
        return i, j, 0.5, 0
    total = 0.0
    for _ in range(samples):
        a, b = pairs[rng.randrange(len(pairs))]
        used = {*a, *b}
        board = []
        while len(board) < 5:
            c = rng.randrange(52)
            if c not in used:
                used.add(c)
                board.append(c)
        sa, sb = evaluate([*a, *board]), evaluate([*b, *board])
        total += 1.0 if sa > sb else 0.5 if sa == sb else 0.0
    return i, j, total / samples, len(pairs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=1000, help="random boards per matchup")
    ap.add_argument("--out", default=str(ROOT / "solver" / "preflop_equity.npz"))
    args = ap.parse_args()

    types = hand_types()
    n = len(types)
    jobs = [(i, j, types[i][1], types[j][1], args.samples, i * 1000 + j)
            for i in range(n) for j in range(i, n)]
    import numpy as np  # only needed for building the table
    equity = np.full((n, n), 0.5)
    weight = np.zeros((n, n))
    with Pool() as pool:
        for k, (i, j, eq, w) in enumerate(pool.imap_unordered(_matchup, jobs, chunksize=64)):
            equity[i, j], equity[j, i] = eq, 1 - eq
            weight[i, j] = weight[j, i] = w
            if k % 2000 == 0:
                print(f"{k}/{len(jobs)} matchups", flush=True)
    for i in range(n):  # a hand type against itself splits on average
        equity[i, i] = 0.5
    np.savez(args.out, names=np.array([t[0] for t in types]), equity=equity, weight=weight)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
