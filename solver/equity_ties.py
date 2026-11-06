"""Rebuilds solver/preflop_equity.json with precise all-in equities AND tie chances for the
169 x 169 hand classes (a split pot is worth more than half a win under ICM, so the
trainer needs ties separately).

    python solver/equity_ties.py --check            # the C evaluator against cards.py
    python solver/equity_ties.py --samples 400000   # ~1-3 minutes per core-quarter

Uses solver/equity_fast.c (compiled with gcc / cc). Writes "equity" (win + tie / 2, as
before) and "tie" to preflop_equity.json, keeping the class order."""
import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cards import RANKS, evaluate  # noqa: E402

SRC = ROOT / "solver" / "equity_fast.c"
JSON = ROOT / "solver" / "preflop_equity.json"


def build(out_dir):
    exe = Path(out_dir) / "equity_fast"
    for cc in ("gcc", "cc", "clang"):
        try:
            subprocess.run([cc, "-O3", "-march=native", "-o", str(exe), str(SRC)], check=True)
            return exe
        except (OSError, subprocess.CalledProcessError):
            continue
    sys.exit("no C compiler")


def combos(h):
    r1, r2 = RANKS.index(h[0]), RANKS.index(h[1])
    out = []
    for s1 in range(4):
        for s2 in range(4):
            if len(h) == 2 and s2 <= s1:
                continue
            if len(h) == 3 and (s1 == s2) != (h[2] == "s"):
                continue
            out.append((r1 * 4 + s1, r2 * 4 + s2))
    return out


def check(exe, n=200000):
    rng = random.Random(1)
    rows, want = [], []
    for _ in range(n):
        cs = rng.sample(range(52), 9)
        a, b = cs[:2] + cs[4:], cs[2:4] + cs[4:]
        x, y = evaluate(a), evaluate(b)
        want.append(1 if x > y else -1 if x < y else 0)
        rows.append(" ".join(map(str, a + b)))
    got = subprocess.run([str(exe), "check"], input="\n".join(rows), capture_output=True,
                         text=True, check=True).stdout.split()
    bad = sum(int(g) != w for g, w in zip(got, want))
    ties = sum(w == 0 for w in want)
    print(f"evaluator check: {n} random showdowns ({ties} ties), {bad} disagreements")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=400000, help="boards per class pair")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--out", default=str(JSON))
    args = ap.parse_args()
    exe = build(args.tmp)
    if not check(exe):
        sys.exit("the C evaluator disagrees with cards.py")
    if args.check:
        return
    d = json.loads(JSON.read_text())
    names = d["names"]
    n = len(names)
    spec = "\n".join(f"{len(c)} " + " ".join(f"{a} {b}" for a, b in c)
                     for c in (combos(h) for h in names))
    # (rows get shorter as i grows: split the triangle into equal work)
    work = [n - i for i in range(n)]
    tot, cuts, acc = sum(work), [0], 0
    for i, w in enumerate(work):
        acc += w
        if acc >= tot * len(cuts) / args.jobs and len(cuts) < args.jobs:
            cuts.append(i + 1)
    cuts.append(n)
    t0 = time.time()
    procs = [subprocess.Popen([str(exe), "run", str(args.samples), str(k + 11), str(a), str(b)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
             for k, (a, b) in enumerate(zip(cuts, cuts[1:]))]
    outs = [p.communicate(spec)[0] for p in procs]
    E = [[0.5] * n for _ in range(n)]
    T = [[0.0] * n for _ in range(n)]
    for line in "".join(outs).splitlines():
        i, j, win, tie, tot_, _np = map(int, line.split())
        e = (win + tie / 2) / tot_
        E[i][j], E[j][i] = e, 1 - e
        T[i][j] = T[j][i] = tie / tot_
    for i in range(n):
        E[i][i] = 0.5
        for j in range(i + 1, n):      # (rounded so each pair still adds up to exactly 1)
            E[i][j] = round(E[i][j], 5)
            E[j][i] = round(1 - E[i][j], 5)
    d["equity"] = [[round(x, 5) for x in row] for row in E]
    d["tie"] = [[round(x, 5) for x in row] for row in T]
    Path(args.out).write_text(json.dumps(d, separators=(",", ":")))
    print(f"{time.time() - t0:.0f}s, wrote {args.out}")


if __name__ == "__main__":
    main()
