"""Builds the flop-solve library on your own PC or server, unattended: every spot and depth
your sims have ranges for (pio_ranges/), flop by flop, with TexasSolver (solver_bin/),
the same way "Solve a spot" does. Each finished flop goes straight into postflop/, where
the trainer, the bots and the Flop sim drill use it. Stop it any time (Ctrl+C or close the
window); run it again and it carries on where it stopped (solved flops are skipped).

    py -3.13 solver/library_builder.py                 everything, most useful first
    py -3.13 solver/library_builder.py --list          what's queued, and what's done
    py -3.13 solver/library_builder.py --depths 20,40 --spots SRP_BTN_vs_BB,3BP_BB_vs_BTN
    py -3.13 solver/library_builder.py --flops 187     the bigger flop set (after the 78)
    py -3.13 solver/library_builder.py --hours 10      stop starting new solves after 10 h

Order: the 78-flop set first (the trainer's library), most common flops first, spots by how
often they come up, then the 187-flop set. Several solves run side by side (--jobs), each
with --threads cores, at low priority; by default it fits the jobs to your cores and memory.
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import solve_spot as SS  # noqa: E402

# spots by how often they come up at a table (the rest after, in name order)
SPOT_ORDER = ["SRP_BTN_vs_BB", "SRP_CO_vs_BB", "SRP_SB_vs_BB", "LP_SB_vs_BB", "SRP_HJ_vs_BB", "SRP_LJ_vs_BB",
              "SRP_UTG1_vs_BB", "SRP_UTG_vs_BB", "3BP_BB_vs_BTN", "3BP_SB_vs_BTN", "SRP_CO_vs_BTN",
              "3BP_BB_vs_CO", "3BP_BB_vs_SB", "SRP_HJ_vs_BTN", "SRP_HJ_vs_CO", "3BP_BTN_vs_CO"]
DEPTH_ORDER = [20, 25, 40, 30, 15, 18, 60, 12, 50, 10, 8]
MEM_GB = {8: 0.5, 10: 0.7, 12: 1.0, 15: 1.2, 18: 1.4, 20: 1.5, 25: 2.0, 30: 2.5, 40: 3.0,
          50: 4.0, 60: 5.0}   # (per solve, generous)


def flop_sets(n):
    """The boards to solve, in order: the trainer's 78 (by weight), then the n-flop set."""
    out, seen = [], set()
    files = [ROOT / "solver" / "flop_weights.json"]
    if n > 78:
        files.append(ROOT / "solver" / f"flops{n}.json")
    for f in files:
        for x in json.loads(f.read_text()):
            if x["board"] not in seen:
                seen.add(x["board"])
                out.append(x["board"])
    return out


def total_mem_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2 ** 30
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) / 2 ** 20
    except Exception:
        return 16.0


def done_path(depth, spot, board):
    return SS.POSTFLOP / f"{depth:g}bb_{spot}" / f"{board}.json"


def build_queue(depths, spots, boards):
    have = SS.spots()
    q = []
    for b in boards:                         # (most common flops first, across spots)
        for d in depths:
            for s in spots:
                if s in have.get(float(d), []) and not done_path(d, s, b).exists():
                    q.append((float(d), s, b))
    return q


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--depths", help="e.g. 20,40 (default: every depth with ranges)")
    ap.add_argument("--spots", help="e.g. SRP_BTN_vs_BB,3BP_BB_vs_BTN (default: all)")
    ap.add_argument("--flops", type=int, default=78, help="78 (default) or 187")
    ap.add_argument("--accuracy", type=float, default=SS.DEFAULT_ACCURACY, help="%% of the pot")
    ap.add_argument("--jobs", type=int, help="solves side by side")
    ap.add_argument("--threads", type=int, help="cores per solve")
    ap.add_argument("--hours", type=float, default=0, help="stop starting solves after this")
    ap.add_argument("--list", action="store_true", help="show the queue and stop")
    a = ap.parse_args()
    have = SS.spots()
    depths = ([float(x) for x in a.depths.split(",")] if a.depths else
              sorted(have, key=lambda d: DEPTH_ORDER.index(int(d)) if int(d) in DEPTH_ORDER else 99))
    every = sorted({s for d in have.values() for s in d},
                   key=lambda s: SPOT_ORDER.index(s) if s in SPOT_ORDER else 99)
    spots = a.spots.split(",") if a.spots else every
    q = build_queue(depths, spots, flop_sets(a.flops))
    print(f"{len(q)} flops to solve ({len(depths)} depths x {len(spots)} spots x {a.flops} flops,"
          f" solved ones skipped)")
    if a.list:
        from collections import Counter
        for (d, s), n in sorted(Counter((d, s) for d, s, _ in q).items()):
            print(f"  {d:g}bb {s:16s} {n} to do")
        return
    exe, _ = SS.solver_path()
    if exe is None:
        sys.exit("The solver isn't installed: solver_bin/ needs console_solver(.exe) and "
                 "resources/ (see SOLVER.md).")
    cores = os.cpu_count() or 4
    threads = a.threads or min(16, max(2, cores // 4 if cores >= 16 else cores))
    mem = total_mem_gb()
    worst = max(MEM_GB.get(int(d), 6.0) for d in depths)
    jobs = a.jobs or max(1, min(cores // threads, int(mem * 0.8 // worst)))
    print(f"{cores} cores, {mem:.0f} GB: {jobs} solves at a time, {threads} cores each, "
          f"to {a.accuracy:g}% of the pot. Ctrl+C stops; run again to carry on.")
    stop_at = time.time() + a.hours * 3600 if a.hours else None
    lock = threading.Lock()
    state = {"done": 0, "secs": 0.0, "fail": 0}
    running = set()
    t0 = time.time()
    log = SS.RUNS / "library.log"
    SS.RUNS.mkdir(parents=True, exist_ok=True)

    def worker():
        while True:
            with lock:
                if not q or (stop_at and time.time() > stop_at):
                    return
                d, s, b = q.pop(0)
            if done_path(d, s, b).exists():
                continue
            job = SS.SolveJob(d, s, SS.parse_board(b), SS.DEFAULT_SIZES, a.accuracy, threads)
            start = time.time()
            with lock:
                running.add(job)
            job.start()
            result = None
            while result is None:
                kind, val = job.events.get()
                if kind in ("done", "error", "cancelled"):
                    result = (kind, val)
            took = time.time() - start
            with lock:
                running.discard(job)
                if result[0] == "done":
                    state["done"] += 1
                    state["secs"] += took
                else:
                    state["fail"] += 1
                left = len(q)
                rate = state["done"] / max(1.0, time.time() - t0)       # flops per second
                eta = left / rate / 3600 if rate > 0 else 0
                line = (f"{time.strftime('%H:%M')}  {d:g}bb {s} {b}: "
                        f"{'%.1f min, %.2f%%' % (took / 60, job.reached or 0) if result[0] == 'done' else result[0] + ' ' + str(result[1])[:120]}"
                        f"   [{state['done']} done, {left} left, about {eta:.1f} h to go]")
                print(line, flush=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(jobs)]
    for t in ts:
        t.start()
    try:
        while any(t.is_alive() for t in ts):
            time.sleep(1)
    except KeyboardInterrupt:
        q.clear()
        for job in list(running):
            job.cancel()
        print("Stopping: the solves running now are dropped; the finished ones are kept.")
        return
    print(f"Finished: {state['done']} flops solved, {state['fail']} failed. Restart the trainer "
          "to use them.")


if __name__ == "__main__":
    main()
