"""Solve a spot: pick a heads-up spot (who opened, who called or 3-bet, the depth), the flop
and the bet sizes, and solve it right here with TexasSolver (a free, open-source solver,
run as a separate program: solver_bin/). The solve goes on until it's accurate (0.5% of the
pot by default), then opens in the strategy viewer: the whole flop tree, every hand's
play as a 13x13 grid. Each solve is saved to postflop/, so the Flop sim drill, the bots
and your hints use it from then on. Every solve already in postflop/ can be opened too.

Opened from the trainer (Spots > Flop sim > Solve a spot), or on its own:

    py -3.13 solve_spot.py

Needs solver_bin/console_solver.exe (Windows; console_solver on Linux / macOS) and
solver_bin/resources/compairer/card5_dic_sorted.txt. TexasSolver is AGPL-3.0 software by
bupticybee (https://github.com/bupticybee/TexasSolver): the trainer only runs it and reads
the strategies it writes."""

import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import flop_sims  # noqa: E402
from solver.tx_export import convert  # noqa: E402

RANGES = ROOT / "pio_ranges"
POSTFLOP = ROOT / "postflop"
RUNS = ROOT / "solver_runs"
BIN = ROOT / "solver_bin"
RANKS = "23456789TJQKA"
SUITS = "cdhs"
GRID = "AKQJT98765432"

# The library's sizes (the 78-flop batches use the same): % of the pot
DEFAULT_SIZES = {"oop_flop": "33", "ip_flop": "33,75", "raise": "60", "turn": "66",
                 "river": "75", "allin_threshold": "0.67"}
DEFAULT_ACCURACY = 0.5      # % of the pot


# ------------------------------------------------------------------ spots, configs, the solver

def solver_path():
    """(the solver program, its resources folder), or (None, None) if it isn't installed."""
    env = os.environ.get("TEXASSOLVER")
    for exe in ([Path(env)] if env else []) + [BIN / "console_solver.exe", BIN / "console_solver"]:
        if exe.exists():
            res = exe.parent / "resources"
            if (res / "compairer" / "card5_dic_sorted.txt").exists():
                return exe, res
    return None, None


def spots():
    """{depth (float): [spot folder name, ...]} from pio_ranges/ (your sims' ranges)."""
    out = {}
    for d in sorted(RANGES.glob("*bb")):
        m = re.match(r"^([\d.]+)bb$", d.name)
        if not m:
            continue
        names = sorted(p.name for p in d.iterdir() if (p / "info.txt").exists())
        if names:
            out[float(m.group(1))] = names
    return out


def spot_dir(depth, spot):
    return RANGES / f"{depth:g}bb" / spot


def spot_label(depth, spot):
    """'CO opens 2.01, BB calls' (from the ranges' info.txt)."""
    try:
        first = (spot_dir(depth, spot) / "info.txt").read_text().splitlines()[0]
        return first.split(": ", 1)[1]
    except (OSError, IndexError):
        return spot


def spot_info(depth, spot):
    """(pot bb, effective stack bb, OOP name, IP name, OOP range %, IP range %)."""
    t = (spot_dir(depth, spot) / "info.txt").read_text()
    pot = float(re.search(r"Starting pot: ([\d.]+)", t).group(1))
    eff = float(re.search(r"Effective stack: ([\d.]+)", t).group(1))
    m = re.search(r"OOP: (\w+) \(([\d.]+)% of hands\)\s+IP: (\w+) \(([\d.]+)% of hands\)", t)
    return pot, eff, m.group(1), m.group(3), float(m.group(2)), float(m.group(4))


def parse_board(text):
    """'Kh7d2c' / 'Kh 7d 2c' -> ['Kh', '7d', '2c'], or None if it isn't three real cards."""
    t = re.sub(r"[\s,]", "", text or "")
    if len(t) != 6:
        return None
    cards = [t[i].upper() + t[i + 1].lower() for i in (0, 2, 4)]
    if any(c[0] not in RANKS or c[1] not in SUITS for c in cards) or len(set(cards)) < 3:
        return None
    return cards


def random_board(rng=random):
    deck = [r + s for r in RANKS for s in SUITS]
    return sorted(rng.sample(deck, 3), key=lambda c: -RANKS.index(c[0]))


def sizes_list(text):
    """'33, 75' -> ['33', '75'] (whole or decimal % of the pot)."""
    out = []
    for x in re.split(r"[\s,]+", text or ""):
        if x:
            v = float(x)
            if not 1 <= v <= 1000:
                raise ValueError(f"size {x}% of the pot")
            out.append(f"{v:g}")
    return out


def config_text(depth, spot, board, sizes, accuracy, threads, out_json, max_iter=1000):
    """TexasSolver's input for one flop, in chips (100 per bb, as the trainer's solves)."""
    d = spot_dir(depth, spot)
    pot, eff = spot_info(depth, spot)[:2]
    ip = next(d.glob("IP_*.txt")).read_text().strip()
    oop = next(d.glob("OOP_*.txt")).read_text().strip()
    lines = [f"set_pot {round(pot * 100)}", f"set_effective_stack {round(eff * 100)}",
             f"set_board {','.join(board)}", f"set_range_ip {ip}", f"set_range_oop {oop}"]
    for who in ("oop", "ip"):
        flop = sizes_list(sizes[f"{who}_flop"])
        if flop:
            lines.append(f"set_bet_sizes {who},flop,bet,{','.join(flop)}")
        rz = sizes_list(sizes["raise"])
        if rz:
            lines.append(f"set_bet_sizes {who},flop,raise,{','.join(rz)}")
        lines.append(f"set_bet_sizes {who},flop,allin")
        for street in ("turn", "river"):       # (bets and all-in; raising there is all-in)
            b = sizes_list(sizes[street])
            if b:
                lines.append(f"set_bet_sizes {who},{street},bet,{','.join(b)}")
            lines.append(f"set_bet_sizes {who},{street},allin")
    th = float(sizes.get("allin_threshold") or 0.67)
    lines += [f"set_allin_threshold {th:g}", "build_tree", f"set_thread_num {int(threads)}",
              f"set_accuracy {float(accuracy):g}", f"set_max_iteration {int(max_iter)}",
              "set_print_interval 10", "set_use_isomorphism 1", "start_solve",
              "set_dump_rounds 1", f"dump_result {out_json.name}"]
    return "\n".join(lines) + "\n"


class SolveJob:
    """One solve in the background: the solver program runs at low priority (the trainer
    stays smooth); progress comes back through .events as (kind, value):
    ("iter", n), ("exploit", % of the pot), ("done", path to the trainer's solve file),
    ("error", text), ("cancelled", None)."""

    def __init__(self, depth, spot, board, sizes, accuracy=DEFAULT_ACCURACY, threads=None):
        self.depth, self.spot, self.board = float(depth), spot, list(board)
        self.sizes, self.accuracy = dict(sizes), float(accuracy)
        self.threads = threads or max(1, (os.cpu_count() or 2))
        self.events = queue.Queue()
        self.proc = None
        self.cancelled = False
        self.started = None
        self.log = []
        self.reached = None          # the last accuracy the solver reported (% of the pot)

    @property
    def name(self):
        return "".join(self.board)

    def start(self):
        exe, res = solver_path()
        if exe is None:
            self.events.put(("error", "The solver isn't installed: solver_bin/ needs "
                                      "console_solver.exe and resources/ (see SOLVER.md)."))
            return
        folder = RUNS / f"{self.depth:g}bb_{self.spot}"
        folder.mkdir(parents=True, exist_ok=True)
        cfg = folder / f"{self.name}.txt"
        out = folder / f"{self.name}.json"
        if out.exists():
            out.unlink()
        try:
            cfg.write_text(config_text(self.depth, self.spot, self.board, self.sizes,
                                       self.accuracy, self.threads, out))
        except (ValueError, OSError, StopIteration, AttributeError) as e:
            self.events.put(("error", f"Can't set up the solve: {e}"))
            return
        self.started = time.time()
        threading.Thread(target=self._run, args=(exe, res, cfg, out), daemon=True).start()

    def _run(self, exe, res, cfg, out):
        kw = {}
        if os.name == "nt":       # no console window; below-normal priority
            kw["creationflags"] = 0x08000000 | 0x00004000
        try:
            self.proc = subprocess.Popen([str(exe), "-i", cfg.name, "-r", str(res)],
                                         cwd=str(cfg.parent), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1,
                                         errors="replace", **kw)
            if os.name != "nt":
                try:
                    os.setpriority(os.PRIO_PROCESS, self.proc.pid, 10)
                except (AttributeError, OSError):
                    pass
            for line in self.proc.stdout:
                self.log.append(line.rstrip())
                del self.log[:-200]
                m = re.match(r"^Iter: (\d+)", line)
                if m:
                    self.events.put(("iter", int(m.group(1))))
                m = re.search(r"Total exploitability ([-\d.eE]+) precent", line)
                if m:
                    self.reached = float(m.group(1))
                    self.events.put(("exploit", self.reached))
            code = self.proc.wait()
        except OSError as e:
            self.events.put(("error", f"Couldn't start the solver: {e}"))
            return
        if self.cancelled:
            self.events.put(("cancelled", None))
            return
        if code != 0 or not out.exists():
            tail = "\n".join(self.log[-6:])
            self.events.put(("error", f"The solver stopped (code {code}).\n{tail}"))
            return
        try:
            data = convert(out, cfg, f"{self.depth:g}", self.spot)
            data["sizes"] = self.sizes
            data["accuracy"] = self.accuracy
            data["reached"] = self.reached
            dest = POSTFLOP / f"{self.depth:g}bb_{self.spot}"
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"{self.name}.json"
            path.write_text(json.dumps(data, separators=(",", ":")))
        except Exception as e:      # noqa: BLE001  (a bad dump: say so, keep the trainer up)
            self.events.put(("error", f"Couldn't read the solve: {e}"))
            return
        self.events.put(("done", path))

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# ------------------------------------------------------------------ reading a solve

CARD_I = {r + s: k for k, (r, s) in enumerate((r, s) for r in RANKS for s in SUITS)}


def hand_class(h):
    """'AsKs' -> 'AKs', 'Kd7c' -> 'K7o', '7h7c' -> '77' (h: STD_HANDS order, high card first)."""
    a, b = h[:2], h[2:]
    if RANKS.index(a[0]) < RANKS.index(b[0]):
        a, b = b, a
    if a[0] == b[0]:
        return a[0] + b[0]
    return a[0] + b[0] + ("s" if a[1] == b[1] else "o")


def load_solve(path):
    d = flop_sims.unpack(json.loads(Path(path).read_text()))
    if d["hands"] == "std":
        d["hands"] = flop_sims.STD_HANDS
    return d


class Tree:
    """Walks a solve's flop tree: the line so far, each player's range as the line has
    narrowed it, the pot, and the play of the player to act."""

    def __init__(self, d):
        self.d = d
        self.hands = d["hands"]
        self.cls = [hand_class(h) for h in self.hands]
        self.path = []                       # [(node id, action code)]

    @property
    def nid(self):
        return "r:0" + "".join(f":{a}" for _, a in self.path)

    @property
    def node(self):
        return self.d["nodes"].get(self.nid)

    def reach(self, who):
        """Each hand's weight in `who`'s range here (range x the line's frequencies)."""
        w = [x / 1000 for x in self.d["ranges"][who]]
        for nid, a in self.path:
            nd = self.d["nodes"][nid]
            if nd["player"] != who:
                continue
            row = nd["strategy"][nd["actions"].index(a)]
            w = [x * f / 1000 for x, f in zip(w, row)]
        return w

    def pot_bets(self):
        """(pot before this street's bets, [OOP bet, IP bet]) in chips, here."""
        nd = self.node
        return self.d["pot"], (nd["bets"] if nd else [0.0, 0.0])

    def label(self, a, nd=None):
        nd = nd or self.node
        pot, bets = self.d["pot"], nd["bets"]
        me = 0 if nd["player"] == "OOP" else 1
        if a == "f":
            return "Fold"
        if a == "c":
            return "Call" if max(bets) > bets[me] else "Check"
        amt = float(a[1:])
        bb = amt / 100
        if amt >= self.d["stack"] - 0.5:
            return f"All-in {bb:g}"
        if max(bets) == 0:
            return f"Bet {bb:.3g} ({100 * amt / (pot + sum(bets)):.0f}%)"
        return f"Raise to {bb:.3g}"

    def who_name(self, who):
        return self.d["oop"] if who == "OOP" else self.d["ip"]

    def summary(self):
        """{action: share of the range to act} and per hand class {action: freq} with each
        class's weight, at this node."""
        nd = self.node
        w = self.reach(nd["player"])
        tot = sum(w)
        overall = {}
        by = {}
        for k, a in enumerate(nd["actions"]):
            row = nd["strategy"][k]
            s = sum(x * f / 1000 for x, f in zip(w, row))
            overall[a] = s / tot if tot else 0.0
            for i, (x, f) in enumerate(zip(w, row)):
                if x <= 0:
                    continue
                c = by.setdefault(self.cls[i], {"w": 0.0, "f": {}})
                c["f"][a] = c["f"].get(a, 0.0) + x * f / 1000
        for c in by.values():
            pass
        for i, x in enumerate(w):
            if x > 0:
                by[self.cls[i]]["w"] += x
        for c in by.values():
            if c["w"] > 0:
                c["f"] = {a: v / c["w"] for a, v in c["f"].items()}
        return overall, by, w

    def combos(self, cls):
        """The class's combos here: [(hand, weight, {action: freq})]."""
        nd = self.node
        w = self.reach(nd["player"])
        out = []
        for i, h in enumerate(self.hands):
            if self.cls[i] != cls or w[i] <= 0:
                continue
            out.append((h, w[i], {a: nd["strategy"][k][i] / 1000
                                  for k, a in enumerate(nd["actions"])}))
        return out


def _perms():
    import itertools
    return [dict(zip(SUITS, p)) for p in itertools.permutations(SUITS)]


def same_flop(saved, wanted):
    """The suit swap that turns the saved flop into the one wanted ({suit: suit}), or None
    (Kh7d2c and Ks7h2d play the same: the solve is the same with the suits renamed)."""
    want = sorted(wanted)
    for p in _perms():
        if sorted(c[0] + p[c[1]] for c in saved) == want:
            return p
    return None


def norm_sizes(sizes):
    out = {}
    for k, v in DEFAULT_SIZES.items():
        raw = (sizes or {}).get(k, v)
        out[k] = (f"{float(raw):g}" if k == "allin_threshold" else
                  ",".join(sizes_list(raw)))
    return out


LIBRARY_ACCURACY = 1.0       # (the trainer's batch solves: to about 1% of the pot)


def saved_accuracy(d):
    """How accurate a saved solve is (% of the pot): what it reached, else its target."""
    return d.get("reached") or d.get("accuracy") or LIBRARY_ACCURACY


def find_saved(depth, spot, board, sizes):
    """A saved solve of this spot and flop (or a flop the same but for the suits) with these
    sizes: (path, suit swap, data) or None. The exact flop first, then the most accurate."""
    folder = POSTFLOP / f"{depth:g}bb_{spot}"
    if not folder.exists():
        return None
    want = norm_sizes(sizes)
    found = []
    for f in folder.glob("*.json"):
        cards = [f.stem[i:i + 2] for i in (0, 2, 4)]
        p = same_flop(cards, board)
        if p is None:
            continue
        try:
            d = load_solve(f)
        except (OSError, ValueError, KeyError):
            continue
        if norm_sizes(d.get("sizes")) != want:
            continue
        exact = sorted(cards) == sorted(board)
        found.append((not exact, saved_accuracy(d), f, p, d))
    if not found:
        return None
    found.sort(key=lambda x: (x[0], x[1]))
    _, _, f, p, d = found[0]
    return f, p, d


def renamed(d, p):
    """The solve with its suits renamed by p (the strategies don't change)."""
    if all(k == v for k, v in p.items()):
        return d
    d = dict(d)
    sw = lambda c: c[0] + p[c[1]]                                     # noqa: E731
    d["board"] = [sw(c) for c in d["board"]]
    d["hands"] = [sw(h[:2]) + sw(h[2:]) for h in d["hands"]]
    return d


def library(depth, spot):
    """Boards already solved for this spot (postflop/<depth>bb_<spot>/)."""
    folder = POSTFLOP / f"{depth:g}bb_{spot}"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.exists() else []


# ------------------------------------------------------------------ the window

def action_color(a, label, C):
    if a == "f":
        return C["fold"]
    if a == "c":
        return C["call"]
    if label.startswith("All-in"):
        return C["allin"]
    if label.startswith("Raise"):
        return C["r1"]
    pct = re.search(r"\((\d+)%\)", label)
    return C["r3"] if pct and int(pct.group(1)) < 50 else C["r2"]


class SolveWindow:
    """Setup on the left (spot, depth, flop, sizes, accuracy, Solve), the strategy on the
    right (the line, the actions with how often the range takes each, the 13x13 grid and a
    hand's combos)."""

    CELL = 40
    GX, GY = 380, 168

    def __init__(self, root, colors, font, on_saved=None, play=None):
        import tkinter as tk
        self.tk = tk
        self.C, self.F = colors, font
        self.on_saved, self.play = on_saved, play
        self.top = tk.Toplevel(root) if root is not None else tk.Tk()
        self.top.title("Solve a spot")
        self.top.configure(bg=self.C["bg"])
        self.W = self.GX + 13 * self.CELL + 270
        self.H = 760
        self.top.geometry(f"{self.W}x{self.H}")
        self.top.resizable(False, False)
        self.cv = tk.Canvas(self.top, width=self.W, height=self.H, bg=self.C["bg"],
                            highlightthickness=0)
        self.cv.place(x=0, y=0)
        self.job = None
        self.tree = None
        self.pick = None
        self.force = None            # (a solve to redo even though a less accurate one is saved)
        self.exploit = None
        self.iters = 0
        self.message = ""
        self.sp = spots()
        depths = sorted(self.sp)
        self.depth = tk.StringVar(value=f"{(30.0 if 30.0 in self.sp else depths[0]) if depths else 30:g}")
        self.spot = tk.StringVar()
        self.board = tk.StringVar(value="".join(random_board()))
        self.size_vars = {k: tk.StringVar(value=v) for k, v in DEFAULT_SIZES.items()}
        self.accuracy = tk.StringVar(value=f"{DEFAULT_ACCURACY:g}")
        self.lib = tk.StringVar()
        self.build_form()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.draw()
        self.top.after(150, self.poll)

    # --- the form (tk widgets over the canvas)
    def _lbl(self, text, x, y, size=9, color=None):
        t = self.tk.Label(self.top, text=text, bg=self.C["bg"], fg=color or self.C["dim"],
                          font=self.F(size), anchor="w")
        t.place(x=x, y=y)
        return t

    def _entry(self, var, x, y, w):
        e = self.tk.Entry(self.top, textvariable=var, bg=self.C["card2"], fg=self.C["text"],
                          insertbackground=self.C["text"], relief="flat", font=self.F(11),
                          highlightthickness=1, highlightbackground=self.C["line"],
                          highlightcolor=self.C["accent"])
        e.place(x=x, y=y, width=w, height=26)
        return e

    def _menu(self, var, options, x, y, w, cmd=None):
        m = self.tk.OptionMenu(self.top, var, *(options or [""]),
                               command=(lambda _v: cmd()) if cmd else None)
        m.configure(bg=self.C["card2"], fg=self.C["text"], activebackground=self.C["line"],
                    activeforeground=self.C["text"], relief="flat", highlightthickness=0,
                    font=self.F(10), anchor="w")
        m["menu"].configure(bg=self.C["card2"], fg=self.C["text"], font=self.F(10))
        m.place(x=x, y=y, width=w, height=28)
        return m

    def _btn(self, text, cmd, x, y, w, h=32, color=None):
        b = self.tk.Button(self.top, text=text, command=cmd, bg=color or self.C["card2"],
                           fg="white", activebackground=self.C["line"], activeforeground="white",
                           relief="flat", font=self.F(10, True), cursor="hand2")
        b.place(x=x, y=y, width=w, height=h)
        return b

    def build_form(self):
        x, w = 16, 340
        self._lbl("Depth (bb)", x, 54)
        self.depth_menu = self._menu(self.depth, [f"{d:g}" for d in sorted(self.sp)], x + 90, 50,
                                     80, self.depth_changed)
        self._lbl("Spot", x, 90)
        self.spot_menu = self._menu(self.spot, [], x + 90, 86, w - 90, self.spot_changed)
        self._lbl("Flop", x, 128)
        self._entry(self.board, x + 90, 124, 120)
        self._btn("Random", lambda: self.board.set("".join(random_board())), x + 218, 123, 122, 28)
        self._lbl("Bet sizes, % of the pot (all-in is always there)", x, 166, 9, self.C["text"])
        rows = [("Flop: first to act (OOP)", "oop_flop"), ("Flop: in position (IP)", "ip_flop"),
                ("Flop raises (turn / river: all-in)", "raise"), ("Turn bets", "turn"), ("River bets", "river"),
                ("All-in once a bet is over this x stack", "allin_threshold")]
        for k, (t, key) in enumerate(rows):
            self._lbl(t, x, 192 + 32 * k)
            self._entry(self.size_vars[key], x + 240, 188 + 32 * k, 100)
        self._lbl("Accuracy (% of the pot)", x, 392)
        self._entry(self.accuracy, x + 240, 388, 100)
        self.solve_btn = self._btn("Solve", self.start, x, 428, 165, 38, self.C["accent"])
        self.cancel_btn = self._btn("Cancel", self.cancel, x + 175, 428, 165, 38)
        self._lbl("Or open a solved flop", x, 492, 9, self.C["text"])
        self.lib_menu = self._menu(self.lib, [], x, 514, 230)
        self._btn("Open", self.open_library, x + 240, 514, 100, 28)
        self.depth_changed()

    def depth_changed(self):
        d = float(self.depth.get() or 0)
        names = self.sp.get(d, [])
        menu = self.spot_menu["menu"]
        menu.delete(0, "end")
        self.labels = {spot_label(d, n): n for n in names}
        for lab in self.labels:
            menu.add_command(label=lab, command=lambda v=lab: (self.spot.set(v), self.spot_changed()))
        cur = self.spot.get()
        if cur not in self.labels:
            pref = next((lab for lab, n in self.labels.items() if n == "SRP_CO_vs_BB"), None)
            self.spot.set(pref or (next(iter(self.labels)) if self.labels else ""))
        self.spot_changed()

    def spot_changed(self):
        menu = self.lib_menu["menu"]
        menu.delete(0, "end")
        boards = library(self.cur_depth(), self.cur_spot()) if self.cur_spot() else []
        for b in boards:
            menu.add_command(label=b, command=lambda v=b: self.lib.set(v))
        self.lib.set(boards[0] if boards else "")
        self.draw()

    def cur_depth(self):
        return float(self.depth.get() or 0)

    def cur_spot(self):
        return self.labels.get(self.spot.get()) if hasattr(self, "labels") else None

    # --- solving
    def start(self):
        if self.job and self.job.proc and self.job.proc.poll() is None:
            return
        spot = self.cur_spot()
        board = parse_board(self.board.get())
        if not spot:
            self.message = "Pick a spot."
        elif board is None:
            self.message = "The flop: three cards like Kh7d2c."
        else:
            try:
                acc = float(self.accuracy.get())
                sizes = {k: v.get() for k, v in self.size_vars.items()}
                for k in ("oop_flop", "ip_flop", "raise", "turn", "river"):
                    sizes_list(sizes[k])
                if not 0.05 <= acc <= 20:
                    raise ValueError("accuracy between 0.05 and 20")
            except ValueError as e:
                self.message = f"Check the numbers: {e}"
                self.draw()
                return
            self.board.set("".join(board))
            key = (self.cur_depth(), spot, tuple(board), tuple(sorted(norm_sizes(sizes).items())), acc)
            hit = find_saved(self.cur_depth(), spot, board, sizes)
            if hit is not None and self.force != key:
                f, perm, d = hit
                got = saved_accuracy(d)
                self.tree = Tree(renamed(d, perm))
                self.pick = None
                how = ("" if sorted(f.stem[i:i + 2] for i in (0, 2, 4)) == sorted(board) else
                       f" (solved as {f.stem}: the same flop with the suits swapped)")
                if got <= acc + 1e-9:
                    self.message = f"Already solved to {got:.2f}% of the pot{how}: opened, no wait."
                    self.force = None
                else:
                    self.message = (f"Already solved to about {got:.2g}% of the pot{how}: opened. "
                                    f"Press Solve again to solve it to {acc:g}%.")
                    self.force = key
                self.draw()
                return
            self.force = None
            self.job = SolveJob(self.cur_depth(), spot, board, sizes, acc)
            self.exploit, self.iters, self.message = None, 0, "Building the tree..."
            self.job.start()
        self.draw()

    def cancel(self):
        if self.job:
            self.job.cancel()

    def poll(self):
        try:
            if not self.top.winfo_exists():
                return
        except self.tk.TclError:
            return
        changed = False
        if self.job:
            while True:
                try:
                    kind, v = self.job.events.get_nowait()
                except queue.Empty:
                    break
                changed = True
                if kind == "iter":
                    self.iters = v
                    self.message = "Solving..."
                elif kind == "exploit":
                    self.exploit = v
                elif kind == "error":
                    self.message = v
                    self.job = None
                    break
                elif kind == "cancelled":
                    self.message = "Cancelled."
                    self.job = None
                    break
                elif kind == "done":
                    secs = time.time() - (self.job.started or time.time())
                    self.message = (f"Solved in {secs / 60:.1f} min to {self.exploit:.2f}% of "
                                    f"the pot. Saved for good: next time it opens with no wait, and the "
                                    f"Flop sim drill and the bots use it now."
                                    if self.exploit is not None else "Solved.")
                    self.job = None
                    self.show(v)
                    if self.on_saved:
                        self.on_saved(v)
                    self.spot_changed()
                    break
        if changed or (self.job is not None):
            self.draw()
        self.top.after(400, self.poll)

    def open_library(self):
        b = self.lib.get()
        if b and self.cur_spot():
            self.show(POSTFLOP / f"{self.cur_depth():g}bb_{self.cur_spot()}" / f"{b}.json")

    def show(self, path):
        try:
            self.tree = Tree(load_solve(path))
            self.pick = None
            self.message = self.message if self.job is None and self.message.startswith(
                "Solved") else f"{Path(path).parent.name} {Path(path).stem}"
        except (OSError, ValueError, KeyError) as e:
            self.message = f"Can't open {path}: {e}"
        self.draw()

    def go(self, a):
        self.tree.path.append((self.tree.nid, a))
        if self.tree.node is None:          # (the street ends here: the turn isn't in the file)
            self.tree.path.pop()
            self.message = "That ends the flop: this solve keeps the flop's strategy only."
        self.pick = None
        self.draw()

    def back(self, n=1):
        if self.tree:
            del self.tree.path[max(0, len(self.tree.path) - n):]
            self.pick = None
            self.draw()

    def close(self):
        if self.job:
            self.job.cancel()
        self.top.destroy()

    # --- drawing
    def draw(self):
        cv, C, F = self.cv, self.C, self.F
        cv.delete("all")
        try:
            self.solve_btn.configure(text="Solve again" if self.force else "Solve")
        except (AttributeError, self.tk.TclError):
            pass
        cv.create_text(16, 22, anchor="w", text="Solve a spot", fill=C["text"], font=F(15, True))
        cv.create_text(150, 24, anchor="w", fill=C["dim"], font=F(9),
                       text="heads-up flop, your sims' ranges, solved by TexasSolver")
        exe, _ = solver_path()
        spot, d = self.cur_spot(), self.cur_depth()
        y = 562
        if spot:
            try:
                pot, eff, oop, ip, po, pi = spot_info(d, spot)
                cv.create_text(16, y, anchor="nw", width=340, fill=C["dim"], font=F(9),
                               text=f"Flop pot {pot:.2f} bb, {eff:.2f} bb behind (SPR "
                                    f"{eff / pot:.1f}). {oop} out of position with {po:.0f}% of "
                                    f"hands, {ip} in position with {pi:.0f}%.")
            except (OSError, AttributeError):
                pass
        y += 50
        if exe is None:
            cv.create_text(16, y, anchor="nw", width=340, fill=C["bad"], font=F(9),
                           text="The solver isn't installed: put console_solver.exe and its "
                                "resources folder in solver_bin\\ (see SOLVER.md).")
            y += 40
        if self.job is not None:
            self.draw_progress(16, y, 340)
            y += 56
        if self.message:
            cv.create_text(16, y, anchor="nw", width=340, fill=C["text"], font=F(9),
                           text=self.message)
        if self.tree is None:
            cv.create_text(self.GX + 6.5 * self.CELL, 300, fill=C["faint"], font=F(11),
                           width=480, justify="center",
                           text="Pick a spot, a flop and the sizes, then Solve. Or open a flop "
                                "that's already solved.")
            return
        self.draw_tree()

    def draw_progress(self, x, y, w):
        cv, C, F = self.cv, self.C, self.F
        acc = self.job.accuracy if self.job else float(self.accuracy.get() or 0.5)
        e = self.exploit
        frac = 0.0
        if e is not None and e > 0:
            import math
            # (log scale: from about 100% of the pot down to the target)
            frac = max(0.0, min(1.0, math.log(100 / max(e, acc)) / math.log(100 / acc)))
        cv.create_rectangle(x, y, x + w, y + 12, fill=C["card2"], outline="")
        cv.create_rectangle(x, y, x + w * frac, y + 12, fill=C["accent"], outline="")
        el = time.time() - self.job.started if self.job and self.job.started else 0
        txt = (f"iteration {self.iters}  ·  {e:.2f}% of the pot from perfect (target {acc:g}%)"
               if e is not None else f"iteration {self.iters}  ·  first accuracy check soon")
        cv.create_text(x, y + 18, anchor="nw", fill=C["dim"], font=F(9), width=w,
                       text=f"{txt}  ·  {el / 60:.1f} min")

    def draw_tree(self):
        cv, C, F, t = self.cv, self.C, self.F, self.tree
        d, nd = t.d, t.node
        X = self.GX
        board = " ".join(d["board"])
        cv.create_text(X, 52, anchor="w", fill=C["text"], font=F(13, True),
                       text=f"{d['depth']:g} bb  {d['opener']} v {d['other']}  ·  {board}")
        # the line so far
        parts = ["Flop"]
        for nid, a in t.path:
            n0 = d["nodes"][nid]
            parts.append(f"{t.who_name(n0['player'])} {t.label(a, n0).lower()}")
        cv.create_text(X, 76, anchor="w", fill=C["dim"], font=F(10), text="  ›  ".join(parts))
        if t.path:
            self.canvas_button(X + 13 * self.CELL - 120, 64, 56, 24, "◀ Back", lambda: self.back(1))
            self.canvas_button(X + 13 * self.CELL - 58, 64, 58, 24, "Start", lambda: self.back(99))
        pot, bets = t.pot_bets()
        me = 0 if nd["player"] == "OOP" else 1
        to_call = max(bets) - bets[me]
        cv.create_text(X, 100, anchor="w", fill=C["act"], font=F(11, True),
                       text=f"{t.who_name(nd['player'])} to act" +
                            f"  ·  pot {(pot + sum(bets)) / 100:.2f} bb" +
                            (f"  ·  {to_call / 100:.2f} to call" if to_call > 0 else ""))
        overall, by, w = t.summary()
        bx = X
        for a in nd["actions"]:
            lab = t.label(a)
            col = action_color(a, lab, C)
            txt = f"{lab}  {100 * overall.get(a, 0):.0f}%"
            bw = max(96, 9 * len(txt))
            self.canvas_button(bx, 116, bw, 32, txt, lambda a=a: self.go(a), col)
            bx += bw + 6
        # the 13x13 grid
        size = self.CELL
        mx = max((c["w"] for c in by.values()), default=1.0) or 1.0
        for r, hi in enumerate(GRID):
            for c, lo in enumerate(GRID):
                if r == c:
                    name = hi + lo
                elif r < c:
                    name = hi + lo + "s"
                else:
                    name = lo + hi + "o"
                x0, y0 = X + c * size, self.GY + r * size
                info = by.get(name)
                cv.create_rectangle(x0, y0, x0 + size - 1, y0 + size - 1, fill=C["card"],
                                    outline=C["bg"])
                if info and info["w"] > 0:
                    # bars: each action's share, the cell's height filled by its weight
                    hfill = max(4, (size - 2) * min(1.0, info["w"] / mx) ** 0.5)
                    xx = x0
                    for a in nd["actions"]:
                        f = info["f"].get(a, 0.0)
                        if f <= 0:
                            continue
                        ww = (size - 1) * f
                        cv.create_rectangle(xx, y0 + size - 1 - hfill, xx + ww, y0 + size - 1,
                                            fill=action_color(a, t.label(a), C), outline="")
                        xx += ww
                col = C["text"] if info and info["w"] > 0 else C["faint"]
                tag = f"cell_{name}"
                cv.create_text(x0 + size / 2, y0 + 11, text=name, fill=col, font=F(8, True),
                               tags=(tag,))
                cv.create_rectangle(x0, y0, x0 + size - 1, y0 + size - 1, outline=(
                    C["gold"] if self.pick == name else ""), width=2, tags=(tag,))
                cv.tag_bind(tag, "<Button-1>", lambda e, n=name: self.pick_cell(n))
        # legend / combos
        px = X + 13 * size + 16
        tot = sum(w)
        cv.create_text(px, self.GY, anchor="nw", fill=C["text"], font=F(10, True),
                       text=f"{t.who_name(nd['player'])}'s range here: "
                            f"{100 * tot / 1326:.1f}% of hands")
        yy = self.GY + 24
        for a in nd["actions"]:
            lab = t.label(a)
            cv.create_rectangle(px, yy + 2, px + 12, yy + 14, fill=action_color(a, lab, C),
                                outline="")
            cv.create_text(px + 18, yy, anchor="nw", fill=C["dim"], font=F(9),
                           text=f"{lab}: {100 * overall.get(a, 0):.1f}%")
            yy += 20
        yy += 10
        if self.pick:
            combos = t.combos(self.pick)
            cv.create_text(px, yy, anchor="nw", fill=C["text"], font=F(10, True),
                           text=f"{self.pick}: {len(combos)} combos in the range")
            yy += 22
            for h, wt, fr in sorted(combos, key=lambda z: -z[1])[:16]:
                parts = "  ".join(f"{t.label(a).split(' (')[0]} {100 * f:.0f}%"
                                  for a, f in fr.items() if f >= 0.005)
                cv.create_text(px, yy, anchor="nw", fill=C["dim"], font=F(8),
                               text=f"{h}  ({100 * wt:.0f}% in)  {parts}", width=250)
                yy += 30
        else:
            cv.create_text(px, yy, anchor="nw", fill=C["faint"], font=F(9), width=250,
                           text="Click a hand for its combos. Each cell's colours are how "
                                "the hand plays; the coloured height is how much of it is "
                                "still in the range on this line.")
        if self.play is not None:
            self.canvas_button(px, self.H - 56, 240, 36, "Play this flop  ▶",
                               lambda: self.play(d), C["accent"])

    def pick_cell(self, name):
        self.pick = None if self.pick == name else name
        self.draw()

    def canvas_button(self, x, y, w, h, text, cmd, color=None):
        cv, C = self.cv, self.C
        tag = f"cb{x:.0f}_{y:.0f}"
        cv.create_rectangle(x, y, x + w, y + h, fill=color or C["card2"], outline="", tags=(tag,))
        cv.create_text(x + w / 2, y + h / 2, text=text, fill="white", font=self.F(9, True),
                       tags=(tag,))
        cv.tag_bind(tag, "<Button-1>", lambda e: cmd())


def open_window(root=None, colors=None, font=None, on_saved=None, play=None):
    """The window (from the trainer: its colours and fonts; on its own: defaults)."""
    if colors is None:
        colors = {"bg": "#0f1115", "panel": "#14171d", "card": "#1a1e26", "card2": "#222833",
                  "line": "#2b3240", "text": "#eceff4", "dim": "#8b93a3", "faint": "#525b6c",
                  "accent": "#8b7cf6", "act": "#f5a524", "gold": "#f2c14e", "fold": "#465166",
                  "call": "#2fb57b", "r1": "#d9534f", "r2": "#e2683d", "r3": "#ea8a3a",
                  "allin": "#b3263e", "good": "#2fbf77", "bad": "#ee8a3a"}
    if font is None:
        def font(size, bold=False):
            return ("Segoe UI", size, "bold" if bold else "normal")
    return SolveWindow(root, colors, font, on_saved, play)


if __name__ == "__main__":
    w = open_window()
    w.top.mainloop()
