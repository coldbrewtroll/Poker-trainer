"""HRC (HoldemResources Calculator) solves, as converted to JSON (hrc/*.json), into the
trainer's range files: one file per decision and action, named like my_ranges /
study_ranges (<spot>@<depth>__<action>.txt). Only equal-stack tables (the depth is the
stack); a spot already in study_ranges at that depth (e.g. a GTO Wizard chart) is kept.

    py -3.13 solver/hrc_import.py hrc/hrc_chip_ev.json --depths 8,10,12,18,25 --out study_ranges
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def spot_of(actor, line, stacks, positions):
    """'UTG:F UTG1:R2.0 LJ:C2.0' -> 'HJ_UTG1r_LJc' for actor HJ (folds left out)."""
    parts = [actor]
    if line and not line.startswith("("):
        for step in line.split():
            who, _, act = step.partition(":")
            if act.startswith("F"):
                continue
            if act.startswith("R"):
                amt = float(act[1:])
                parts.append(who + ("a" if amt >= stacks[positions.index(who)] - 1e-6 else "r"))
            elif act.startswith("C"):
                parts.append(who + "c")
            elif act.startswith("X"):
                continue
    return "_".join(parts)


def action_name(act, stack):
    if act.startswith(("C", "X")):
        return "call"
    if act.startswith("R"):
        amt = float(act[1:])
        return "allin" if amt >= stack - 1e-6 else f"raise_{amt:g}bb"
    return None


def convert(path, depths, out, keep_existing=True):
    d = json.loads(Path(path).read_text())
    hands = re.findall(r"\b[AKQJT2-9]{2}[so]?\b", d["format"]["hands"])
    assert len(hands) == 169, len(hands)
    written = 0
    for t in d["tables"]:
        st = t["stacks_bb"]
        if len(set(st)) != 1 or st[0] not in depths:
            continue
        depth = st[0]
        pos = t["positions"]
        for nd in t["nodes"]:
            spot = spot_of(nd["actor"], nd["line"], st, pos)
            if keep_existing and any(out.glob(f"{spot}@{depth:g}__*.txt")):
                continue
            stack = st[pos.index(nd["actor"])]
            files = {}
            for act in nd["actions"]:
                name = action_name(act, stack)
                if name is None:
                    continue
                items = []
                for h, f in zip(hands, nd["freq_pct"].get(act) or []):
                    if f is None:
                        continue
                    f = f / 100
                    if f >= 0.995:
                        items.append(h)
                    elif f >= 0.02:
                        items.append(f"{h}:{f:.2f}")
                if items:
                    files.setdefault(name, []).extend(items)
            for name, items in files.items():
                (out / f"{spot}@{depth:g}__{name}.txt").write_text(",".join(items) + "\n")
                written += 1
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("--depths", required=True)
    ap.add_argument("--out", default=str(ROOT / "study_ranges"))
    a = ap.parse_args()
    n = convert(a.json, [float(x) for x in a.depths.split(",")], Path(a.out))
    print(f"{n} range files written to {a.out}")
