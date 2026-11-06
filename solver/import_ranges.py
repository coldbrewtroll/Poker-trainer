"""Turns exported solver ranges (MonkerSolver/MonkerViewer, GTO+, PIO-style strings) into a
lookup table a bot can use with no files at run time.

Put one file per (spot, action) in a folder, named  <spot>@<bb>__<action>.<ext>
(two underscores), e.g.  sb_first@100__raise_2.5bb.txt  or  bb_vs_raise@100__call.txt.
The @<bb> part says which stack depth the sim was solved at; leave it out for ranges meant
for any depth. Between two solved depths the bot blends them; outside them it uses the
nearest one if close, else its built-in strategy.

Spots: a friendly name or line_<code>, where the code is the preflop action so far
(c = call, r = raise, a = all-in; the small blind acts first):

    sb_first (""), bb_vs_limp (c), bb_vs_raise (r), bb_vs_allin (a),
    sb_vs_3bet (rr), sb_vs_3bet_allin (ra), bb_vs_4bet (rrr), bb_vs_4bet_allin (rra),
    sb_vs_5bet (rrrr), sb_vs_5bet_allin (rrra), sb_limp_vs_raise (cr),
    sb_limp_vs_allin (ca), bb_vs_limp_3bet (crr), bb_vs_limp_reshove (cra)
    e.g. line_crr for a spot without a name.

Actions: fold, call (check = call), allin, raise, or a sized raise: raise_2.5bb (to 2.5
big blinds), raise_3x (3 times the bet faced), raise_75pct (75% of the pot). Several raise
sizes in one spot are fine. Anything the ranges leave out is fold (or check when free).

    python solver/import_ranges.py my_ranges/        -> writes bots/range_table.py

Accepted range formats (detected automatically):
    AA:1,AKs:0.75,AQo:0.5          frequencies 0-1 or 0-100
    Th9h@100,4h3h@89,AA@50         MonkerSolver/MonkerViewer export (weights in %)
    [75]AKs,AQs[/75],KK            bracket weights (PokerStove / Flopzilla style)
    AA / 1.0;0.53 / AKs / 0.75;... two-line form: a hand line, then "frequency;ev"
    AhKh:0.5,AsKs:1                single combos, averaged into hand types
    AA,KK,QQ                       plain list = 100%
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solver.equity import hand_types, type_of  # noqa: E402
from cards import card  # noqa: E402
from bots.lines import describe, parse_action, parse_line  # noqa: E402
from bots.lines8 import parse_spot  # noqa: E402

TYPES = [name for name, _ in hand_types()]
COMBOS = {name: len(c) for name, c in hand_types()}
HAND = re.compile(r"^([2-9TJQKA][cdhs][2-9TJQKA][cdhs]|[2-9TJQKA]{2}[so]?)$", re.I)


def _norm_hand(h: str) -> str:
    h = h.strip()
    if len(h) == 4 and h[1] in "cdhs":
        return h[0].upper() + h[1] + h[2].upper() + h[3]
    return h[:2].upper() + h[2:].lower()


def expand(hand: str):
    """'AK' (no suffix) means both AKs and AKo; pairs and typed hands map to themselves."""
    if len(hand) == 2 and hand[0] != hand[1]:
        hi, lo = sorted(hand, key="23456789TJQKA".index, reverse=True)
        return [hi + lo + "s", hi + lo + "o"]
    if len(hand) == 3:
        hi, lo = sorted(hand[:2], key="23456789TJQKA".index, reverse=True)
        return [hi + lo + hand[2]]
    return [hand]


def parse(text: str) -> dict:
    """Range text -> {hand_type: frequency 0..1}."""
    entries = []  # (hand, written weight or None for a plain hand = 100%)

    def add(hand, w):
        hand = _norm_hand(hand)
        if not HAND.match(hand):
            raise ValueError(f"not a hand: {hand!r}")
        entries.append((hand, w))

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    two_line = (len(lines) >= 2 and HAND.match(_norm_hand(lines[0])) and
                re.match(r"^[\d.]+(;|$)", lines[1]))
    if two_line:
        for h, w in zip(lines[0::2], lines[1::2]):
            add(h, float(w.split(";")[0]))
    else:
        body = " ".join(lines)
        block = re.compile(r"\[([\d.]+)\](.*?)\[/\1\]")
        for m in block.finditer(body):
            for h in re.split(r"[,\s]+", m.group(2).strip()):
                if h:
                    add(h, float(m.group(1)))
        for item in re.split(r"[,\s]+", block.sub("", body)):
            if item:
                hand, _, w = item.replace("@", ":").partition(":")  # AA:0.5 or Monker's AA@50
                add(hand, float(w) if w else None)

    # A file is either 0-1 or 0-100 throughout; any written weight above 1 means percent.
    scale = 100.0 if any(w is not None and w > 1.0001 for _, w in entries) else 1.0
    weights, listed = {}, {}
    for hand, w in entries:
        f = 1.0 if w is None else w / scale
        if len(hand) == 4:  # a single combo
            t = type_of(card(hand[:2]), card(hand[2:]))
            weights[t] = weights.get(t, 0.0) + f
            listed[t] = listed.get(t, 0) + 1
        else:
            for t in expand(hand):
                weights[t] = f * COMBOS[t]
                listed[t] = COMBOS[t]
    # Combos a combo-level export leaves out count as 0.
    return {t: round(min(1.0, w / COMBOS[t]), 4) for t, w in weights.items()}


def compare_pushfold(table):
    """Checks imported push/fold ranges against this kit's own solved equilibrium."""
    from solver import pushfold
    names, E, W = pushfold.load()
    idx = {n: i for i, n in enumerate(names)}
    for situation, action, solved_side in (("", "allin", 0), ("a", "call", 1)):
        for depth, acts in table.get(situation, {}).items():
            if not depth or action not in acts:
                continue
            p, q, _ = pushfold.solve(E, W, depth, iters=2000)
            mine = p if solved_side == 0 else q
            theirs = acts[action]
            diff = sorted(((abs(theirs.get(t, 0) - mine[idx[t]]), t) for t in TYPES),
                          reverse=True)
            agree = sum(COMBOS[t] * (1 - d) for d, t in diff) / 1326
            print(f"{describe(situation)}@{depth:g} {action}: {agree:.1%} of hands agree with the "
                  "kit's own solve")
            big = [f"{t} (yours {theirs.get(t, 0):.0%}, solver {mine[idx[t]]:.0%})"
                   for d, t in diff if d >= 0.5][:8]
            if big:
                print("   biggest differences: " + ", ".join(big))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=str(ROOT / "bots" / "range_table.py"))
    args = ap.parse_args()

    # table[situation][depth][action] = {hand type: frequency}; depth 0 = any depth
    table = {}
    for f in sorted(Path(args.folder).glob("*")):
        if not f.is_file() or "__" not in f.stem:
            continue
        spot, action_name = f.stem.split("__", 1)
        spot_name, _, depth = spot.partition("@")
        try:
            depth = float(depth.lower().removesuffix("bb")) if depth else 0.0
        except ValueError:
            depth = -1
        situation, action = parse_line(spot_name), parse_action(action_name)
        if situation is None and parse_spot(spot_name):
            continue  # an 8-max spot name: that's import_ranges8.py's job
        if situation is None or action is None or depth < 0:
            print(f"skipped {f.name}: expected <spot>@<bb>__<action>, e.g. "
                  "bb_vs_raise@100__call.txt (see the top of this script for names)")
            continue
        try:
            rng = parse(f.read_text(encoding="utf-8", errors="replace"))
        except ValueError as e:
            print(f"skipped {f.name}: {e}")
            continue
        table.setdefault(situation, {}).setdefault(depth, {})[action] = rng
        share = sum(rng.get(t, 0) * COMBOS[t] for t in TYPES) / 1326
        at = f"@{depth:g}bb" if depth else ""
        print(f"{describe(situation) + at:24s} {action:12s} {len(rng):3d} hand types, "
              f"{share:5.1%} of hands")

    for situation, depths in table.items():  # an action mix must not exceed 100%
        for depth, acts in depths.items():
            for t in TYPES:
                total = sum(a.get(t, 0) for a in acts.values())
                if total > 1.02:
                    print(f"warning: {describe(situation)}@{depth:g} {t} adds up to "
                          f"{total:.0%} across actions")

    with open(args.out, "w") as f:
        f.write('"""Generated by solver/import_ranges.py from your own solver exports.\n'
                'TABLE[line code][stack depth in bb, 0 = any][action][hand type] = how\n'
                'often to take that action. Line codes: see bots/lines.py."""\n\n')
        f.write("TABLE = {\n")
        for situation, depths in table.items():
            f.write(f"    {situation!r}: {{  # {describe(situation)}\n")
            for depth, acts in sorted(depths.items()):
                f.write(f"        {depth:g}: {{\n")
                for action, rng in acts.items():
                    items = ", ".join(f"{t!r}: {v:g}" for t, v in rng.items() if v > 0)
                    f.write(f"            {action!r}: {{{items}}},\n")
                f.write("        },\n")
            f.write("    },\n")
        f.write("}\n")
    print(f"wrote {args.out}")
    compare_pushfold(table)

if __name__ == "__main__":
    main()
