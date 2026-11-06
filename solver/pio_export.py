"""Exports PioSOLVER flop solves into the trainer (postflop/), through Pio's command interface.

    py -3.13 solver/pio_export.py "D:\\PioSOLVER\\saves"

Reads every .cfr in that folder (and its subfolders). Name the saves
    <spot>_<depth>bb_<board>.cfr        e.g. SRP_BTN_vs_BB_30bb_Ks7d2c.cfr
or keep any names and put them in a folder named <spot>_<depth>bb (e.g.
saves\\SRP_BTN_vs_BB_30bb\\); the board is read from each save. The spot names are the
folders in pio_ranges/. For each save it walks every flop
decision (who acts, each action and size, how often every combo takes it) and both
players' ranges, and writes postflop/<depth>bb_<spot>/<board>.json for the trainer.

It looks for PioSOLVER2-edge.exe in the usual places (D:\\PioSOLVER, C:\\PioSOLVER, ...);
otherwise add --pio "...\\PioSOLVER2-edge.exe".
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solver.pio_probe import END, Pio, find_pio  # noqa: E402

POSTFLOP_ORDER = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
NAME = re.compile(r"^(?P<spot>(?:SRP|3BP|4BP|LP)_[A-Za-z0-9]+_vs_[A-Za-z0-9]+)_(?P<depth>[\d.]+)bb_"
                  r"(?P<board>(?:[2-9TJQKA][cdhs]){3})", re.I)


SPOT_DEPTH = re.compile(r"(?P<spot>(?:SRP|3BP|4BP|LP)_[A-Za-z0-9]+_vs_[A-Za-z0-9]+)_(?P<depth>[\d.]+)bb",
                        re.I)


WRITTEN = {}  # output file -> the save it came from (this run)
PREFLOP_ORDER = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
ALIASES = {"BU": "BTN", "BTN": "BTN", "SB": "SB", "BB": "BB", "CO": "CO", "HJ": "HJ", "LJ": "LJ",
           "MP": "LJ", "MP1": "LJ", "MP2": "HJ", "UTG": "UTG", "EP": "UTG", "UTG1": "UTG1",
           "UTG+1": "UTG1", "UTG2": "LJ", "UTG+2": "LJ"}
POS_RE = re.compile(r"(?<![A-Z0-9])(UTG\+?[12]|UTG|EP|MP[12]?|LJ|HJ|CO|BTN|BU|SB|BB)(?![A-Z0-9])")
SCALES = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000]


def spot_name(kind, opener, other):
    """Our spot names: SRP_<opener>_vs_<caller>, 3BP_<3-bettor>_vs_<opener>,
    4BP_<opener (4-bettor)>_vs_<3-bettor>."""
    if kind == "3BP":
        return f"3BP_{other}_vs_{opener}"
    return f"{kind}_{opener}_vs_{other}"


def parse_loose(name):
    """Any common naming of a spot: 'BU-BB SRP 40bb (Updated Sizings)', 'BB vs BTN 3bet 25bb',
    'CO_v_BB_SRP_30BB'... -> (kind, opener, other, depth) or None. The earlier seat preflop is
    the opener (and, in 3-bet pots, the later one the 3-bettor)."""
    up = name.upper()
    d = re.search(r"(\d+(?:\.\d+)?)\s*BB", up)
    seats = []
    for tok in POS_RE.findall(up):
        p = ALIASES[tok]
        if p not in seats:
            seats.append(p)
    if not d or len(seats) != 2:
        return None
    if re.search(r"LIMP", up):
        kind = "LP"      # a limped pot: the SB completes, the BB checks
    elif re.search(r"(?<![0-9])4[- ]?B(?:ET|P)?(?![A-Z0-9])", up):
        kind = "4BP"
    elif re.search(r"(?<![0-9])3[- ]?B(?:ET|P)?(?![A-Z0-9])", up):
        kind = "3BP"
    else:
        kind = "SRP"
    opener, other = sorted(seats, key=PREFLOP_ORDER.index)
    return kind, opener, other, float(d.group(1))


def spot_of(cfr):
    """(kind, opener, other, depth) from the save's name, or else a folder it's in."""
    for name in [cfr.stem] + [p.name for p in cfr.parents]:
        m = NAME.match(name) or SPOT_DEPTH.match(name)
        if m:
            kind, a, _, b = m.group("spot").upper().split("_")
            opener, other = (b, a) if kind == "3BP" else (a, b)
            return kind, opener, other, float(m.group("depth"))
        loose = parse_loose(name)
        if loose:
            return loose
    return None


def chip_scale(pot, stack, depth):
    """Chips per big blind in this save (saves differ: bb, x10, x100 ...)."""
    guess = (stack + pot / 2) / depth if depth else 100.0
    near = min(SCALES, key=lambda v: abs(v - guess))
    return float(near) if abs(near - guess) <= 0.12 * near else guess


def parse_children(lines):
    """show_children output -> [{id, type, bets: (oop, ip), pot, children}]."""
    out, cur = [], None
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("child "):
            cur = {}
            out.append(cur)
            step = 0
            continue
        if cur is None or not ln:
            continue
        if step == 0:
            cur["id"] = ln
        elif step == 1:
            cur["type"] = ln
        elif step == 3:
            nums = [float(v) for v in ln.split()]
            cur["bets"], cur["pot"] = (nums[0], nums[1]), nums[2]
        elif step == 4 and ln.endswith("children"):
            cur["children"] = int(ln.split()[0])
        step += 1
    return out


def parse_rows(lines, n=1326):
    """Lines of n numbers each (show_strategy: one per action; show_range: one)."""
    rows = []
    for ln in lines:
        vals = ln.split()
        if len(vals) == n:
            try:
                rows.append([float(v) for v in vals])
            except ValueError:
                continue
    return rows


def tree_info(lines):
    info = {}
    for ln in lines:
        m = re.match(r"^#([^#]+)#(.*)$", ln.strip())
        if m:
            info[m.group(1)] = m.group(2).strip()
    return info


def positions(spot):
    _, a, _, b = spot.split("_")
    a, b = a.upper(), b.upper()
    oop, ip = sorted((a, b), key=lambda p: POSTFLOP_ORDER.index(p) if p in POSTFLOP_ORDER else 9)
    return oop, ip


def export_one(pio, cfr, hand_order, out_dir, max_nodes=400):
    # The spot and depth come from the save's name or a folder it's in, in our naming
    # (SRP_BTN_vs_BB_30bb) or any common one (BU-BB SRP 40bb); the board from the save.
    sd = spot_of(cfr)
    if not sd:
        print(f"  skipped {cfr.name}: can't tell the spot. Put it in a folder named like "
              f"SRP_BTN_vs_BB_30bb, or with the two seats, the pot type and the depth in "
              f"its name (e.g. 'BU-BB SRP 40bb')")
        return None
    kind, opener, other, depth = sd
    spot = spot_name(kind, opener, other)
    t0 = time.time()
    ok = pio.ask(f'load_tree "{cfr}"', 900)
    if not any("ok" in ln for ln in ok):
        print(f"  {cfr.name}: Pio couldn't load it: {' '.join(ok)[:200]}")
        return None
    info = tree_info(pio.ask("show_tree_info", 120))
    # The board of the solved tree itself (show_node r:0, 3rd line). show_tree_info's #Board#
    # can be the tree builder's leftover (job-queue saves), so it's only a fallback.
    root = [ln.strip() for ln in pio.ask("show_node r:0", 60)]
    board = []
    for ln in root:
        toks = ln.split()
        if 3 <= len(toks) <= 5 and all(re.fullmatch(r"[2-9TJQKA][cdhs]", t) for t in toks):
            board = toks
            break
    if not board:
        board = info.get("Board", "").split()
    named = re.search(r"((?:[2-9TJQKA][cdhs]){3})$", cfr.stem)
    if named:
        from_name = re.findall(r"[2-9TJQKA][cdhs]", named.group(1))
        if set(from_name) != set(board[:3]):
            print(f"  {cfr.name}: the file name says {''.join(from_name)} but the solve's tree "
                  f"is on {''.join(board)}: skipped (did it load? is it the right file?)")
            return None
    if len(board) != 3:
        print(f"  {cfr.name}: not a flop solve (board {board}): skipped")
        return None
    ranges = {}
    for who in ("OOP", "IP"):
        rows = parse_rows(pio.ask(f"show_range {who} r:0", 120))
        ranges[who] = [round(v * 1000) for v in rows[0]] if rows else []
    nodes, todo = {}, ["r:0"]
    while todo and len(nodes) < max_nodes:
        node = todo.pop(0)
        kids = parse_children(pio.ask(f"show_children {node}", 120))
        if not kids:
            continue
        me = [ln.strip() for ln in pio.ask(f"show_node {node}", 60)]
        player = "OOP" if "OOP_DEC" in me else "IP"
        own = next((ln.split() for ln in me if re.match(r"^[\d.]+ [\d.]+ [\d.]+$", ln)), None)
        rows = parse_rows(pio.ask(f"show_strategy {node}", 120))
        if len(rows) != len(kids):
            print(f"  {cfr.name}: {node}: {len(rows)} strategy rows for {len(kids)} actions")
            continue
        nodes[node] = {
            "player": player,
            "actions": [k["id"].rsplit(":", 1)[1] for k in kids],
            "bets": [float(own[0]), float(own[1])] if own else [0.0, 0.0],
            "strategy": [[round(v * 1000) for v in r] for r in rows],
        }
        for k in kids:  # stay on the flop: decisions only (not turn cards or the end)
            if k.get("type") in ("OOP_DEC", "IP_DEC"):
                todo.append(k["id"])
    oop, ip = positions(spot)
    data = {"spot": spot, "depth": depth, "board": board,
            "pot": float(info.get("Pot", 0)), "stack": float(info.get("EffectiveStacks", 0)),
            "chips_per_bb": chip_scale(float(info.get("Pot", 0)),
                                       float(info.get("EffectiveStacks", 0)), depth),
            "kind": kind, "opener": opener, "other": other,
            "oop": oop, "ip": ip, "hands": hand_order,
            "ranges": ranges, "nodes": nodes, "source": cfr.name}
    folder = out_dir / f"{depth:g}bb_{spot}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{''.join(board)}.json"
    if path in WRITTEN:
        print(f"  {cfr.name}: same spot and board as {WRITTEN[path]}: kept the first one")
        return None
    WRITTEN[path] = cfr.name
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"  {cfr.name}: {len(nodes)} flop decisions -> {path.relative_to(out_dir.parent)} "
          f"({time.time() - t0:.0f}s)")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("saves", help="a .cfr file, or a folder of them")
    ap.add_argument("--pio", help="path to PioSOLVER2-edge.exe")
    ap.add_argument("--out", default=str(ROOT / "postflop"))
    args = ap.parse_args()
    exe = find_pio(args.pio)
    if exe is None or not exe.exists():
        sys.exit('Could not find PioSOLVER2-edge.exe: add --pio "...\\PioSOLVER2-edge.exe"')
    src = Path(args.saves)
    files = [src] if src.is_file() else sorted(src.rglob("*.cfr"))
    if not files:
        sys.exit(f"No .cfr files in {src}")
    out = Path(args.out)
    print(f"{len(files)} save(s); solver {exe}")
    pio = Pio(exe)
    done = 0
    try:
        pio.ask("is_ready", 30, until_end=False)
        pio.ask(f"set_end_string {END}", 30, until_end=False)
        pio.ask("is_ready", 30)
        hand_order = []
        for ln in pio.ask("show_hand_order", 60):
            if len(ln.split()) == 1326:
                hand_order = ln.split()
        if len(hand_order) != 1326:
            sys.exit("Pio didn't give its hand order: send me a screenshot of this window")
        for f in files:
            if export_one(pio, f, hand_order, out):
                done += 1
    finally:
        pio.close()
    print(f"\n{done} of {len(files)} exported into {out}. Start the trainer: Spots -> Flop sim.")


if __name__ == "__main__":
    main()
