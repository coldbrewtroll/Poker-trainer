"""HRC (HoldemResources Calculator) saved hands straight into the trainer's study charts.

Reads the .zip files HRC keeps in its SavedHands folder (settings.json + one JSON file per
decision in nodes/) and writes one range file per decision and action into study_ranges/,
named like my_ranges (<spot>@<depth>__<action>.txt; see bots/lines8.py for spot names).

    py -3.13 solver/hrc_saves.py                         # every save in ~/HoldemResources/SavedHands
    py -3.13 solver/hrc_saves.py --only "ce*"            # just some of them
    py -3.13 solver/hrc_saves.py path/to/one.zip --dry-run

Only saves the trainer's charts can hold are used: everyone at the table on the same stack
(that stack is the chart's depth), 3 to 8 players, chip EV, and 1 bb of antes in the pot
(a big blind ante of 1 bb, or a regular ante of 1/8 bb at 8-max). ICM solves and tables of
uneven stacks are skipped and listed. Two saves at the same depth and table size: a big
blind ante wins (that's what the trainer plays), then the newer one.

Running it again updates the charts: files written by an earlier run (listed in
study_ranges/_hrc_files.txt) are replaced, and a chart you put there yourself (e.g. GTO
Wizard) for the same spot and depth is kept unless you pass --overwrite.
"""
import argparse
import fnmatch
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver.import_ranges import COMBOS  # noqa: E402

POSITIONS = {8: ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"],
             7: ["UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"],
             6: ["LJ", "HJ", "CO", "BTN", "SB", "BB"],
             5: ["HJ", "CO", "BTN", "SB", "BB"],
             4: ["CO", "BTN", "SB", "BB"],
             3: ["BTN", "SB", "BB"]}
SAVED = Path.home() / "HoldemResources" / "SavedHands"
MANIFEST = "_hrc_files.txt"
MIN_COMBOS = 1.0     # skip decisions the solve reaches with fewer combos than this


def table_of(settings):
    """(players, depth in bb, dead antes in bb, equity model) of a save, or a reason to skip it."""
    hd = settings["handdata"]
    bb, _sb, ante = hd["blinds"]
    stacks = hd["stacks"]
    n = len(stacks)
    eq = settings["eqmodel"]["id"]
    if eq != "chipev":
        return f"{eq} (not chip EV)"
    if len(set(stacks)) != 1:
        return "uneven stacks"
    if n not in POSITIONS:
        return f"{n} players"
    if hd.get("straddleType", "OFF") != "OFF":
        return "straddle"
    bb_ante = hd["anteType"].startswith("BB_ANTE")
    dead = (ante if bb_ante else ante * n) / bb
    if abs(dead - 1.0) > 0.01:
        return f"{dead:g} bb of antes (the trainer plays 1 bb)"
    # What each player has left to bet once the antes are in (HRC's all-in amount): with a
    # big blind ante only the big blind has paid one.
    left = [stacks[0] - (ante if (k == n - 1 or not bb_ante) else 0) for k in range(n)]
    depth = round(max(left) / bb * 2) / 2      # to the nearest half bb
    return n, depth, left, bb, bb_ante


def cap(player, left, folded):
    """The most `player` can usefully bet: their own stack, or the deepest stack still in the
    hand if that's smaller (HRC caps an all-in there, e.g. the small blind's shove at the big
    blind's stack once the big blind has paid a big blind ante)."""
    others = [x for q, x in enumerate(left) if q != player and q not in folded]
    return min(left[player], max(others)) if others else left[player]


def is_allin(amount, most, bb):
    return amount >= most - 0.01 * bb


def spot_of(seq, actor, positions, left, bb):
    """The trainer's spot name for `actor` after the actions in `seq` (folds left out)."""
    items, last_raise, folded = [], -1, set()
    for a in seq:
        if a["type"] == "R":
            last_raise = len(items)
            allin = is_allin(a["amount"], cap(a["player"], left, folded), bb)
            items.append([positions[a["player"]], "a" if allin else "r"])
        elif a["type"] == "C":
            items.append([positions[a["player"]], "c"])
        elif a["type"] == "F":
            folded.add(a["player"])
    for k, it in enumerate(items):
        if it[1] == "a" and k != last_raise:
            it[1] = "r"     # only the latest raise can be an all-in in a spot name
    return "_".join([positions[actor]] + [p + c for p, c in items])


def action_name(a, most, bb):
    if a["type"] in ("C", "X"):
        return "call"
    if a["type"] == "R":
        if is_allin(a["amount"], most, bb):
            return "allin"
        return f"raise_{round(a['amount'] / bb * 20) / 20:g}bb"     # to 0.05 bb
    return None     # fold


def convert(zf, n, left, bb, min_combos=MIN_COMBOS):
    """{(spot, action): {hand: freq}} for every preflop decision in one save."""
    positions = POSITIONS[n]
    out, best = {}, {}      # best: spot -> reach of the node it was taken from
    for name in zf.namelist():
        if not (name.startswith("nodes/") and name.endswith(".json")):
            continue
        nd = json.loads(zf.read(name))
        if nd.get("street", 0) != 0 or not nd.get("hands"):
            continue
        hands = nd["hands"]
        reach = sum(COMBOS.get(h, 0) * v.get("weight", 0.0) for h, v in hands.items())
        if reach < min_combos:
            continue    # (almost) never reached: its strategy is noise
        spot = spot_of(nd["sequence"], nd["player"], positions, left, bb)
        if best.get(spot, -1.0) >= reach:
            continue    # two lines with one name: keep the one the solve reaches more
        for key in [k for k in out if k[0] == spot]:
            del out[key]
        best[spot] = reach
        folded = {a["player"] for a in nd["sequence"] if a["type"] == "F"}
        most = cap(nd["player"], left, folded)
        for i, a in enumerate(nd["actions"]):
            act = action_name(a, most, bb)
            if act is None:
                continue
            freqs = {h: v["played"][i] for h, v in hands.items() if v.get("weight", 0.0) > 1e-9}
            key = (spot, act)
            if key in out:      # two raise sizes that round alike: add them up
                for h, f in freqs.items():
                    out[key][h] = out[key].get(h, 0.0) + f
            else:
                out[key] = freqs
    return out


def range_text(freqs):
    return ",".join(h if f >= 0.9995 else f"{h}:{f:.3f}"
                    for h, f in sorted(freqs.items()) if f >= 0.0005)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("saves", nargs="*", help=f"zip files or folders (default: {SAVED})")
    ap.add_argument("--only", help="only saves whose file name matches, e.g. 'ce*'")
    ap.add_argument("--out", default=str(ROOT / "study_ranges"), help="folder for the range files")
    ap.add_argument("--overwrite", action="store_true",
                    help="also replace charts in --out that this script didn't write")
    ap.add_argument("--min-combos", type=float, default=MIN_COMBOS,
                    help="skip decisions reached with fewer combos than this")
    ap.add_argument("--dry-run", action="store_true", help="list what would be written")
    args = ap.parse_args()

    zips = []
    for src in (args.saves or [SAVED]):
        src = Path(src)
        zips += sorted(src.glob("*.zip")) if src.is_dir() else [src]
    if args.only:
        zips = [z for z in zips if fnmatch.fnmatch(z.name.lower(), args.only.lower())]
    if not zips:
        sys.exit("no HRC saves found")

    # Pick the saves the charts can hold; the newest wins per table size and depth.
    chosen, skipped = {}, {}
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                t = table_of(json.loads(zf.read("settings.json")))
        except (zipfile.BadZipFile, KeyError, ValueError) as e:
            t = f"unreadable ({e})"
        if isinstance(t, str):
            skipped.setdefault(t, []).append(z.name)
            continue
        n, depth, left, bb, bb_ante = t
        rank = (bb_ante, z.stat().st_mtime)    # the trainer plays a big blind ante
        old = chosen.get((n, depth))
        if old and old[3] >= rank:
            skipped.setdefault("another save of the same table won", []).append(z.name)
            continue
        if old:
            skipped.setdefault("another save of the same table won", []).append(old[0].name)
        chosen[(n, depth)] = (z, left, bb, rank)

    out = Path(args.out)
    manifest = out / MANIFEST
    ours = set(manifest.read_text().split()) if manifest.is_file() else set()
    written, kept = set(), 0
    for (n, depth), (z, left, bb, _rank) in sorted(chosen.items()):
        with zipfile.ZipFile(z) as zf:
            table = convert(zf, n, left, bb, args.min_combos)
        if n != 8:
            print(f"note: {z.name} is {n}-handed; its charts share names with 8-max ones")
        files = 0
        for (spot, act), freqs in sorted(table.items()):
            text = range_text(freqs)
            if not text:
                continue
            fname = f"{spot}@{depth:g}__{act}.txt"
            path = out / fname
            if path.exists() and fname not in ours and not args.overwrite:
                kept += 1
                continue
            if not args.dry_run:
                out.mkdir(parents=True, exist_ok=True)
                path.write_text(text + "\n", encoding="utf-8")
            written.add(fname)
            files += 1
        print(f"{z.name}: {n}-max, {depth:g} bb -> {files} range files "
              f"({len({s for s, _ in table})} decisions)")

    # Files from an earlier run that this run didn't write again (a decision the new solve
    # no longer reaches, or a save that's gone) are removed, so the charts match HRC.
    done_depths = {f"@{d:g}__" for (_n, d) in chosen}
    stale = sorted(f for f in ours - written if any(dd in f for dd in done_depths))
    for f in stale:
        if not args.dry_run:
            (out / f).unlink(missing_ok=True)
    if not args.dry_run and (written or ours):
        out.mkdir(parents=True, exist_ok=True)
        manifest.write_text("\n".join(sorted((ours - set(stale)) | written)) + "\n")

    print(f"\n{'would write' if args.dry_run else 'wrote'} {len(written)} range files to {out}"
          + (f", removed {len(stale)} old ones" if stale else "")
          + (f"; kept {kept} charts of your own (--overwrite replaces them)" if kept else ""))
    if skipped:
        print("\nskipped:")
        for why, names in sorted(skipped.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(names[:6]) + (f", ... ({len(names)} in all)" if len(names) > 6 else "")
            print(f"  {why}: {shown}")


if __name__ == "__main__":
    main()
