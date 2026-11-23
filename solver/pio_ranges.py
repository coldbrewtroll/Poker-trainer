"""Pio range files for heads-up postflop solves, from your 8-max preflop ranges: your Monker
sims (my_ranges/) and your study charts (study_ranges/: GTO Wizard, HRC), which win where
both have a spot at a depth, as in the trainer.

For each spot (a single-raised or 3-bet pot between two seats) and each depth you have a
sim for, writes the two players' preflop ranges as Pio range text, plus the pot and
effective stack the flop starts with, ready to paste into PioSOLVER's tree builder:

    py -3.13 solver/pio_ranges.py                  (all spots, all depths -> pio_ranges/)
    py -3.13 solver/pio_ranges.py --depths 25,40   (only these depths)

Each hand's weight is how often it gets to the flop on that line: e.g. for a 3-bet pot the
opener's open frequency x their frequency of calling the 3-bet. Sizes follow your Monker
tree (pot-% raises, big blind ante as dead money, blinds 0.5 / 1).
"""

import argparse
import contextlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solver.import_ranges import TYPES  # noqa: E402
from solver.import_ranges8 import load_folder  # noqa: E402

POSTFLOP_ORDER = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
ANTE = 1.0
# (folder name, opener, caller-or-3-bettor, kind)
SPOTS = [("SRP_%s_vs_BB" % p, p, "BB", "srp") for p in ("UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB")]
SPOTS += [("SRP_CO_vs_BTN", "CO", "BTN", "srp"), ("SRP_HJ_vs_CO", "HJ", "CO", "srp"),
          ("SRP_HJ_vs_BTN", "HJ", "BTN", "srp")]
SPOTS += [("3BP_BB_vs_BTN", "BTN", "BB", "3bp"), ("3BP_SB_vs_BTN", "BTN", "SB", "3bp"),
          ("3BP_BTN_vs_CO", "CO", "BTN", "3bp"), ("3BP_BB_vs_CO", "CO", "BB", "3bp"),
          ("3BP_BB_vs_SB", "SB", "BB", "3bp")]
SPOTS += [("LP_SB_vs_BB", "SB", "BB", "lp")]     # the SB limps, the BB checks


def raise_pct(acts):
    """A spot's sized raise (not all-in): (action name, size, unit) with unit "pct" (Monker's
    pot-% raise) or "bb" (raise to that many bb: GTO Wizard, HRC). With several sizes, the
    one played most."""
    best = None
    for a, w in acts.items():
        m = re.match(r"raise_([\d.]+)(pct|bb)$", a)
        if m:
            share = sum(v * (6 if len(h) == 2 else 4 if h[2] == "s" else 12) for h, v in w.items())
            if best is None or share > best[0]:
                best = (share, a, float(m.group(1)), m.group(2))
    return (best[1], (best[2], best[3])) if best else (None, None)


def raise_to(top, my_bet, pot, size):
    """The raise's size in bb: Monker's pot-% raise (to = current bet + pct x (pot after
    calling)), or a raise to so many bb."""
    val, unit = size
    if unit == "bb":
        return val
    return top + val / 100 * (pot + top - my_bet)


def blind(pos):
    return {"SB": 0.5, "BB": 1.0}.get(pos, 0.0)


def weights(acts, name):
    return acts.get(name, {}) if name else {}


def build(table, opener, other, kind, depth):
    """(OOP pos, IP pos, {pos: {hand: weight}}, pot, effective stack, line) or None."""
    first = table.get(opener, {}).get(depth)
    if kind == "lp":                     # the SB limps, the BB checks
        vs = table.get(f"{other}_{opener}c", {}).get(depth)
        if not first or not vs or "call" not in first or "call" not in vs:
            return None
        dead = ANTE
        ranges = {opener: dict(first["call"]), other: dict(vs["call"])}
        ranges = {p: {h: w for h, w in r.items() if w >= 0.001} for p, r in ranges.items()}
        return opener, other, ranges, 2.0 + dead, depth - 1.0, f"{opener} limps, {other} checks"
    vs = table.get(f"{other}_{opener}r", {}).get(depth)
    if not first or not vs:
        return None
    open_name, open_pct = raise_pct(first)
    if not open_name:
        return None
    dead = ANTE + sum(blind(p) for p in ("SB", "BB") if p not in (opener, other))
    pot0 = ANTE + 0.5 + 1.0  # blinds and ante before the open
    open_to = raise_to(1.0, blind(opener), pot0, open_pct)
    w_open = weights(first, open_name)
    if kind == "srp":
        ranges = {opener: dict(w_open), other: dict(weights(vs, "call"))}
        pot, eff, line = 2 * open_to + dead, depth - open_to, f"{opener} opens {open_to:.2f}, {other} calls"
    else:
        three_name, three_pct = raise_pct(vs)
        back = table.get(f"{opener}_{opener}r_{other}r", {}).get(depth)
        if not three_name or not back:
            return None
        pot1 = pot0 + open_to - blind(opener)
        three_to = raise_to(open_to, blind(other), pot1, three_pct)
        w_call = weights(back, "call")
        ranges = {opener: {h: w_open.get(h, 0) * w_call.get(h, 0) for h in TYPES},
                  other: dict(weights(vs, three_name))}
        pot, eff = 2 * three_to + dead, depth - three_to
        line = f"{opener} opens {open_to:.2f}, {other} 3-bets {three_to:.2f}, {opener} calls"
    ranges = {p: {h: w for h, w in r.items() if w >= 0.001} for p, r in ranges.items()}
    oop, ip = sorted((opener, other), key=POSTFLOP_ORDER.index)
    return oop, ip, ranges, pot, eff, line


def pio_text(r):
    return ",".join(h if r[h] >= 0.9995 else f"{h}:{r[h]:.3f}" for h in TYPES if h in r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sims", default=f"{ROOT / 'my_ranges'},{ROOT / 'study_ranges'}",
                    help="range folders, later ones win (default: my_ranges, study_ranges)")
    ap.add_argument("--out", default=str(ROOT / "pio_ranges"))
    ap.add_argument("--depths", help="comma-separated, default: every depth you have")
    args = ap.parse_args()
    # each folder on its own, and all of them together (later folders win): a spot's two
    # ranges come from one source when one has both (a GTO Wizard open against a Monker
    # defence built for a different open would be a mismatch), else from the mix
    tables, table = [], {}
    with contextlib.redirect_stdout(io.StringIO()):
        for folder in args.sims.split(","):
            if Path(folder).is_dir():
                t = load_folder(folder)
                tables.append(t)
                for spot, depths_ in t.items():
                    table.setdefault(spot, {}).update(depths_)
    depths = sorted({d for spot in table.values() for d in spot if d > 0})
    if args.depths:
        want = {float(d) for d in args.depths.split(",")}
        depths = [d for d in depths if d in want]
    out = Path(args.out)
    print(f"{'spot':16s} {'bb':>4s} {'pot':>6s} {'stack':>6s}  OOP / IP ranges")
    for d in depths:
        for name, opener, other, kind in SPOTS:
            res = None
            for t in reversed(tables):          # (the later folders first)
                res = build(t, opener, other, kind, d)
                if res:
                    break
            res = res or build(table, opener, other, kind, d)
            if not res:
                continue
            oop, ip, ranges, pot, eff, line = res
            if eff < 0.3 * pot:
                continue  # (all in, or as good as, before the flop: nothing to solve)
            share = {p: sum(w * (6 if len(h) == 2 else 4 if h[2] == "s" else 12)
                            for h, w in r.items()) / 13.26 for p, r in ranges.items()}
            if min(share.values()) < 0.5:
                continue  # (almost) never reached at this depth: e.g. 3-bets that are all-ins
            folder = out / f"{d:g}bb" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"OOP_{oop}.txt").write_text(pio_text(ranges[oop]) + "\n")
            (folder / f"IP_{ip}.txt").write_text(pio_text(ranges[ip]) + "\n")
            (folder / "info.txt").write_text(
                f"{name} at {d:g} bb: {line}\n"
                f"Starting pot: {pot:.2f} bb   Effective stack: {eff:.2f} bb\n"
                f"OOP: {oop} ({share[oop]:.1f}% of hands)   IP: {ip} ({share[ip]:.1f}% of hands)\n"
                f"In PioSOLVER: paste OOP_{oop}.txt as the OOP range and IP_{ip}.txt as the IP "
                f"range,\nset the pot to {pot:.2f} and the effective stack to {eff:.2f} "
                f"(or x100 if you use chips).\n")
            print(f"{name:16s} {d:4g} {pot:6.2f} {eff:6.2f}  {oop} {share[oop]:.1f}% / "
                  f"{ip} {share[ip]:.1f}%")
    print(f"\nwritten to {out}/<depth>bb/<spot>/ (OOP_*.txt, IP_*.txt, info.txt)")


if __name__ == "__main__":
    main()
