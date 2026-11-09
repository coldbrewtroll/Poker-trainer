"""Turns TexasSolver flop solves (its dump_result JSON, flop decisions) into the trainer's
flop-solve files (postflop/<depth>bb_<spot>/<board>.json, the same format as
solver/pio_export.py), so the Flop sim drill, the nearest-flop matcher and the bots use
them like Pio solves.

    py -3.13 solver/tx_export.py RAW_FOLDER          e.g. raw/40bb_SRP_BTN_vs_BB

RAW_FOLDER holds <board>.json (the dump) and <board>.txt (the solver input it came from:
pot, stack and both ranges are read from it). Chips are 100 per big blind, as in Pio."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTFLOP_ORDER = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
sys.path.insert(0, str(ROOT))
from flop_sims import STD_HANDS  # noqa: E402

RANKS = "23456789TJQKA"
SUITS = "cdhs"
HANDS = STD_HANDS                  # (the trainer's standard order, high card first)
INDEX = {frozenset((h[:2], h[2:])): k for k, h in enumerate(HANDS)}


def expand(range_text, board):
    """'AA,AKs:0.5,...' -> weights (0-1000) over HANDS, board cards removed."""
    w = [0] * len(HANDS)
    dead = set(board)
    for item in filter(None, (x.strip() for x in range_text.split(","))):
        name, _, f = item.partition(":")
        f = float(f) if f else 1.0
        r1, r2 = name[0], name[1]
        kind = name[2] if len(name) > 2 else ("" if r1 == r2 else "both")
        for s1 in SUITS:
            for s2 in SUITS:
                a, b = r1 + s1, r2 + s2
                if a == b or a in dead or b in dead:
                    continue
                if r1 == r2 and SUITS.index(s2) <= SUITS.index(s1):
                    continue
                if kind == "s" and s1 != s2 or kind == "o" and s1 == s2:
                    continue
                k = INDEX.get(frozenset((a, b)))
                if k is not None:
                    w[k] = max(w[k], round(f * 1000))
    return w


def spot_parts(spot):
    kind, rest = spot.split("_", 1)
    opener, other = rest.split("_vs_")
    return kind, opener, other


def convert(raw_json, cfg_txt, depth, spot):
    cfg = cfg_txt.read_text()
    get = lambda key: re.search(rf"^{key} (.+)$", cfg, re.M).group(1).strip()   # noqa: E731
    pot, stack = float(get("set_pot")), float(get("set_effective_stack"))
    board = get("set_board").split(",")
    tree = json.loads(raw_json.read_text())
    oop_player = tree["player"]                 # (the first to act on the flop is out of position)
    kind, opener, other = spot_parts(spot)
    oop, ip = sorted((opener, other), key=lambda p: POSTFLOP_ORDER.index(p))
    nodes = {}

    def walk(node, nid, bets):
        if node.get("node_type") != "action_node":
            return
        who = "OOP" if node["player"] == oop_player else "IP"
        acts = node["strategy"]["actions"]
        strat = node["strategy"]["strategy"]
        codes, kids = [], []
        for a in node["actions"]:
            word, _, amt = a.partition(" ")
            if word in ("CHECK", "CALL"):
                code = "c"
            elif word == "FOLD":
                code = "f"
            else:
                code = f"b{int(round(float(amt)))}"
            codes.append(code)
        rows = [[0] * len(HANDS) for _ in acts]
        for combo, freqs in strat.items():
            k = INDEX.get(frozenset((combo[:2], combo[2:])))
            if k is None:
                continue
            for i, f in enumerate(freqs):
                rows[i][k] = round(f * 1000)
        # (the strategy lists actions in the same order as the node's actions)
        nodes[nid] = {"player": who, "actions": codes, "bets": list(bets), "strategy": rows}
        for a, code in zip(node["actions"], codes):
            child = (node.get("childrens") or {}).get(a)
            if not child or child.get("node_type") != "action_node":
                continue
            nb = list(bets)
            me = 0 if who == "OOP" else 1
            if code == "c":
                nb[me] = max(nb)
            elif code.startswith("b"):
                nb[me] = float(code[1:])
            walk(child, f"{nid}:{code}", nb)

    walk(tree, "r:0", [0.0, 0.0])
    ranges = {"OOP": expand(get("set_range_oop"), board), "IP": expand(get("set_range_ip"), board)}
    return {"spot": spot, "depth": float(depth), "board": board, "pot": pot, "stack": stack,
            "chips_per_bb": 100.0, "kind": kind, "opener": opener, "other": other,
            "oop": oop, "ip": ip, "hands": HANDS, "ranges": ranges, "nodes": nodes,
            "source": "TexasSolver"}


def main():
    raw = Path(sys.argv[1])
    m = re.match(r"^([\d.]+)bb_(.+)$", raw.name)
    depth, spot = m.group(1), m.group(2)
    out = ROOT / "postflop" / raw.name
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for js in sorted(raw.glob("*.json")):
        cfg = js.with_suffix(".txt")
        if not cfg.exists():
            continue
        data = convert(js, cfg, depth, spot)
        (out / js.name).write_text(json.dumps(data, separators=(",", ":")))
        n += 1
    print(f"{n} flop solves -> {out}")


if __name__ == "__main__":
    main()
