"""Converts a MonkerSolver preflop export ("Export preflop for MonkerViewer": a folder of
.rng files, or that folder zipped) into the kit's range files, one per spot and action,
ready for solver/import_ranges8.py.

    py -3.13 solver/monker_import8.py 20bb.zip --depth 20
    py -3.13 solver/import_ranges8.py my_ranges

Run it again on a newer export of the same sim (e.g. after solving longer) and it replaces
that depth's files in my_ranges/ (--keep to keep them).

Monker names each file after the actions taken so far, one code per action, starting
with UTG: 0 fold, 1 call (or check), 3 all-in, 4xxxx raise xxxx% of the pot (40029 =
29% of the pot after calling, an open to 2 bb). The file is the range of the player who
took the last action: every hand with how often it got there AND took that action. The
strategy is that divided by the hand's total over all the actions at that decision.

Assumes the usual MTT tree: everyone starts with --depth big blinds, blinds 0.5 / 1 and
--ante big blinds of dead ante (1 = a big blind ante), 8 players (--players for fewer).
"""

import argparse
import io
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

POSITIONS = {8: ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"],
             7: ["UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"],
             6: ["LJ", "HJ", "CO", "BTN", "SB", "BB"],
             5: ["HJ", "CO", "BTN", "SB", "BB"],
             4: ["CO", "BTN", "SB", "BB"],
             3: ["BTN", "SB", "BB"]}
NAME = re.compile(r"^\d+(\.\d+)*$")


def read_export(src):
    """{tuple of action codes: {hand: weight}} from a folder or a .zip of .rng files."""
    out = {}
    src = Path(src)
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            items = [(Path(n).name, z.read(n)) for n in z.namelist()
                     if n.lower().endswith(".rng")]
    else:
        items = [(f.name, f.read_bytes()) for f in sorted(src.rglob("*.rng"))]
    for fname, data in items:
        stem = fname[:-4]
        if not NAME.match(stem):
            continue
        words = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace").read().split()
        rng = {}
        for k in range(0, len(words) - 1, 2):
            try:
                rng[words[k]] = float(words[k + 1].split(";")[0])
            except ValueError:
                continue
        out[tuple(stem.split("."))] = rng
    return out


class Hand:
    """Replays Monker action codes: who acts, what each action is in the kit's terms."""

    def __init__(self, players, depth, ante):
        self.pos = POSITIONS[players]
        n = len(self.pos)
        self.stack = [float(depth)] * n
        self.bet = [0.0] * n
        self.folded = [False] * n
        self.dead = ante
        for seat, blind in ((n - 2, 0.5), (n - 1, 1.0)):
            self.bet[seat] = blind
            self.stack[seat] -= blind
        self.to_act = 0
        self.acted = set()
        self.items = []          # (position, 'r' | 'c' | 'a') for actions that aren't folds
        self.last_raise = -1     # index in items of the latest raise

    def live(self, seat):
        return not self.folded[seat] and self.stack[seat] > 0

    def kit_action(self, code):
        """What the actor's code is, in the kit's file names (and its line letter)."""
        seat, top = self.to_act, max(self.bet)
        all_in = self.bet[seat] + self.stack[seat]
        if code == "0":
            return "fold", None
        if code == "1":
            return "call", "c"
        if code == "3":
            return ("allin", "a") if all_in > top else ("call", "c")
        if code.startswith("4") and len(code) == 5:
            pct = int(code[1:])
            pot = sum(self.bet) + self.dead
            to = top + pct / 100 * (pot + top - self.bet[seat])
            if to >= all_in - 1e-9:
                return "allin", "a"
            return f"raise_{pct}pct", "r"
        raise ValueError(f"unknown action code {code}")

    def play(self, code):
        seat, top = self.to_act, max(self.bet)
        action, letter = self.kit_action(code)
        if action == "fold":
            self.folded[seat] = True
        elif action == "call":
            pay = min(top - self.bet[seat], self.stack[seat])
            self.bet[seat] += pay
            self.stack[seat] -= pay
        else:
            if action == "allin":
                to = self.bet[seat] + self.stack[seat]
            else:
                pct = int(code[1:])
                pot = sum(self.bet) + self.dead
                to = top + pct / 100 * (pot + top - self.bet[seat])
            self.stack[seat] -= to - self.bet[seat]
            self.bet[seat] = to
            self.acted = set()
            self.last_raise = len(self.items)
        self.acted.add(seat)
        if letter:
            self.items.append((self.pos[seat], letter))
        self.advance()

    def advance(self):
        n, top = len(self.pos), max(self.bet)
        for k in range(1, n + 1):
            seat = (self.to_act + k) % n
            if self.live(seat) and (seat not in self.acted or self.bet[seat] < top):
                self.to_act = seat
                return
        self.to_act = None  # the betting is over

    def spot(self):
        """The kit's spot name for the player to act (bots/lines8.py)."""
        parts = [self.pos[self.to_act]]
        for k, (p, letter) in enumerate(self.items):
            if letter == "a" and k != self.last_raise:
                letter = "r"  # only the latest raise can be an all-in in a spot name
            parts.append(p + letter)
        return "_".join(parts)


MIN_REACH = 1e-6   # skip decisions the solve reaches less often than this (see convert)


def convert(export, players, depth, ante, min_combos, min_reach=MIN_REACH):
    """{(spot, action): {hand: freq}} and a list of problems.

    A decision is kept only if the solve really reaches it: the product of how often each
    earlier non-fold action is taken (an all-in nobody makes at this depth, or a call of it
    nobody makes, gives ~0) is at least `min_reach`. Below about one in a million the
    solver has barely trained the spot and its strategy is noise (measured on the 15-30 bb
    sims: 0-3 hands out of order per 1000 pairs above, 35-150 below); the bots' own rules
    play those rare lines instead."""
    from solver.import_ranges import COMBOS
    children = defaultdict(dict)
    for path, rng in export.items():
        children[path[:-1]][path[-1]] = rng

    sizes, totals = {}, {}

    def size(rng):
        k = id(rng)      # (each file's combo count, worked out once)
        if k not in sizes:
            sizes[k] = sum(COMBOS.get(h, 0) * w for h, w in rng.items())
        return sizes[k]

    out, problems = {}, []
    for prefix, kids in children.items():
        hand = Hand(players, depth, ante)
        reach = 1.0
        try:
            for i, code in enumerate(prefix):
                if hand.to_act is None:
                    raise ValueError("actions after the betting ended")
                if hand.kit_action(code)[0] != "fold":
                    sib = children.get(prefix[:i], {})
                    tot = totals.get(prefix[:i])
                    if tot is None:
                        tot = totals[prefix[:i]] = sum(size(r) for r in sib.values())
                    reach *= size(sib[code]) / tot if tot and code in sib else 0.0
                hand.play(code)
            if hand.to_act is None:
                raise ValueError("no one left to act")
            spot = hand.spot()
            names = {code: hand.kit_action(code)[0] for code in kids}
        except ValueError as e:
            problems.append(f"{'.'.join(prefix) or '(root)'}: {e}")
            continue
        if reach < min_reach:
            continue  # the solve (almost) never gets here
        hands = set().union(*kids.values())
        totals = {h: sum(r.get(h, 0.0) for r in kids.values()) for h in hands}
        reach = sum(COMBOS.get(h, 0) * min(1.0, t) for h, t in totals.items())
        if reach < min_combos:
            continue  # (almost) never reached
        for code, rng in kids.items():
            action = names[code]
            if action == "fold":
                continue
            freqs = {h: rng.get(h, 0.0) / t for h, t in totals.items() if t > 1e-9}
            key = (spot, action)
            if key in out:
                problems.append(f"{spot} {action}: reached two ways, kept the first")
                continue
            out[key] = freqs
    return out, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("export", help="the exported folder, or a .zip of it")
    ap.add_argument("--depth", type=float, help="stack depth in bb (default: from the name)")
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--ante", type=float, default=1.0, help="dead ante in bb (1 = BB ante)")
    ap.add_argument("--out", default="my_ranges", help="folder for the range files")
    ap.add_argument("--keep", action="store_true",
                    help="keep older files of this depth in --out (default: replace them)")
    ap.add_argument("--min-reach", type=float, default=MIN_REACH,
                    help="skip decisions the solve reaches less often than this")
    ap.add_argument("--min-combos", type=float, default=1.0,
                    help="skip decisions reached with fewer combos than this")
    args = ap.parse_args()
    depth = args.depth
    if depth is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*bb", Path(args.export).name, re.I)
        if not m:
            sys.exit("give the stack depth: --depth 20")
        depth = float(m.group(1))
    export = read_export(args.export)
    if not export:
        sys.exit("no Monker .rng files found there")
    table, problems = convert(export, args.players, depth, args.ante, args.min_combos,
                              args.min_reach)
    if not table:
        for p in problems[:20]:
            print("  note:", p)
        sys.exit(f"read {len(export)} Monker files but couldn't convert any decision: "
                 f"nothing written, your {depth:g} bb files are untouched")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    old = set(out.glob(f"*@{depth:g}__*.txt"))
    written = set()
    for (spot, action), freqs in sorted(table.items()):
        text = ",".join(h if f >= 0.9995 else f"{h}:{f:.3f}"
                        for h, f in sorted(freqs.items()) if f >= 0.0005)
        path = out / f"{spot}@{depth:g}__{action}.txt"
        path.write_text(text + "\n", encoding="utf-8")
        written.add(path)
    stale = old - written
    if stale and not args.keep:
        # A newer export of the same sim replaces the older one completely (removed only
        # once the new files are written).
        for f in stale:
            f.unlink()
        print(f"replaced the older {depth:g} bb files in {out}/ ({len(stale)} not in the "
              f"new export removed)")
    spots = {s for s, _ in table}
    print(f"read {len(export)} Monker files; wrote {len(table)} range files for "
          f"{len(spots)} spots at {depth:g} bb into {out}/")
    for p in problems[:20]:
        print("  note:", p)
    if len(problems) > 20:
        print(f"  ... and {len(problems) - 20} more notes")
    print("next:  py -3.13 solver/import_ranges8.py", out)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
