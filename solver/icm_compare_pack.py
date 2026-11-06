"""Spots to check the trainer's ICM against HRC (HoldemResources Calculator) or ICMIZER.

    python3 solver/icm_compare_pack.py > ICM_COMPARE.md

For each spot: the settings to type in (stacks, blinds, the big blind ante, payouts), and
the trainer's answers: every player's ICM equity, the first-in jam range, each player's
call range against it, and the jam's value for a few hands. Both programs use the same
ICM model by default (Malmuth-Harville), so the equities should match to the dollar;
ranges can differ a little (see the notes at the end).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    import tkinter  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "web" / "py"))
    import tkstub
    tkstub.install()

import icm_study as I  # noqa: E402
import final_table as FT  # noqa: E402

RANKS = "AKQJT98765432"
PAYS = I.CIRCUIT        # WSOP Circuit $1M final table shape: 1st $250,000 ... 9th $20,200

# (title, stacks in bb in position order with the big blind last, the jammer, the hero
#  facing it or None, a few hands to show the value of)
SPOTS = [
    ("Heads-up, 10 bb each (SB jams)", [10, 10], 0, None, ["K7o", "Q4s", "T9o", "22"]),
    ("3 left: BTN 20, SB 12, BB 6", [20, 12, 6], 0, None, ["A2o", "K9o", "Q8s", "55"]),
    ("4 left: chip leader (CO, 40 bb) first in vs 15 / 10 / 12", [40, 15, 10, 12], 0, None,
     ["A5o", "KTo", "J9s", "33"]),
    ("5 left: SB 8 bb vs a 40 bb big blind", [22, 30, 18, 8, 40], 3, None,
     ["A2o", "K5s", "Q9o", "T8s"]),
    ("6 left: HJ 15 bb first in", [25, 15, 30, 8, 22, 18], 1, None,
     ["A9o", "KJo", "QTs", "66"]),
    ("8 left (full final table): UTG 10 bb", [10, 32, 26, 22, 19, 16, 13, 11], 0, None,
     ["AJo", "KQo", "A8s", "77"]),
    ("7 left: CO 12 bb jams, BB 25 bb to call", [30, 20, 16, 12, 9, 14, 25], 3, 6,
     ["A9o", "KJo", "QJs", "88"]),
    ("7 left: CO 20 bb jams, BB 9 bb to call", [30, 20, 16, 20, 14, 12, 9], 3, 6,
     ["A7o", "K9s", "QTo", "44"]),
]


def compact(weights):
    """A 169-hand range as standard notation (22+, A2s+, KTo+ ...): hands at 50%+."""
    names, idx = I.exact()[0], I.exact()[1]
    on = {h for h in names if weights[idx[h]] >= 0.5}
    out = []
    pairs = [r + r for r in RANKS]          # AA, KK, ... 22
    # pairs: runs, "77+" when a run reaches AA, "99-55" in between, single pairs alone
    i = 0
    while i < len(pairs):
        if pairs[i] in on:
            j = i
            while j + 1 < len(pairs) and pairs[j + 1] in on:
                j += 1
            out.append(f"{pairs[j]}+" if i == 0 and j > 0 else
                       f"{pairs[j]}-{pairs[i]}" if i != j else pairs[i])
            i = j + 1
        else:
            i += 1
    for suit in ("s", "o"):
        for a in range(len(RANKS) - 1):
            hi = RANKS[a]
            ks = [RANKS[b] for b in range(a + 1, len(RANKS))]
            have = [k for k in ks if hi + k + suit in on]
            if not have:
                continue
            # runs of consecutive kickers
            k = 0
            while k < len(ks):
                if ks[k] in have:
                    m = k
                    while m + 1 < len(ks) and ks[m + 1] in have:
                        m += 1
                    if m == len(ks) - 1 and k == 0:
                        out.append(f"{hi}{ks[-1]}{suit}+")
                    elif m == len(ks) - 1:
                        out.append(f"{hi}{ks[k]}{suit}-{hi}{ks[m]}{suit}"
                                   if k != m else f"{hi}{ks[k]}{suit}")
                    elif k == 0:
                        out.append(f"{hi}{ks[m]}{suit}+")
                    else:
                        out.append(f"{hi}{ks[k]}{suit}-{hi}{ks[m]}{suit}" if k != m
                                   else f"{hi}{ks[k]}{suit}")
                    k = m + 1
                else:
                    k += 1
    return ", ".join(out) or "(none)"


def share(weights):
    return 100 * I._share_w(weights)


# Small blind vs big blind with limps and a 3 bb raise (the trainer's traps): (title,
# stacks in position order with the big blind last)
SB_SPOTS = [
    ("4 left: SB 9.9 bb vs a 71 bb big blind (the hand you asked about)",
     [52.5, 10.38, 9.88, 71.25]),
    ("8 left: SB 10 bb vs BB 10.25 bb", [32, 26, 22, 19, 16, 13, 10, 10.25]),
    ("4 left: SB 11 bb vs BB 20 bb", [30, 25, 11, 20]),
    ("3 left: SB 10 bb vs BB 15 bb", [20, 10, 15]),
    ("Heads-up: 11 bb each", [11, 11]),
]
SB_HANDS = ["AA", "KK", "QQ", "TT", "88", "AKs", "AKo", "AQo", "A5o", "K9o", "Q7o", "J4s"]


def sb_section(pays):
    names = {"fold": "fold", "limp": "limp", "raise": f"raise {I.SB_RAISE_TO:g}", "jam": "jam"}
    print("## Small blind vs big blind: limps and the 3 bb raise (traps)\n")
    print("For these, set the tree to: **small blind**: fold, limp, raise to "
          f"{I.SB_RAISE_TO:g} bb, all-in; **big blind** facing a limp: check or all-in; facing "
          f"the raise: fold or all-in; facing the all-in: call or fold; **small blind** facing "
          f"the big blind's all-in: call or fold. Everyone before the small blind folds. "
          f"Same blinds, ante and payouts as above.\n")
    print(f"Postflop (a limped pot the big blind checks): the trainer doesn't play it out; it "
          f"gives the small blind (out of position) {100 * I.SB_LIMP_REALIZE:.0f}% of its "
          f"equity. In HRC pick the nearest postflop model (e.g. equity realization, or "
          f"check down); expect the limp frequencies of weak hands to differ most.\n")
    for k, (title, st) in enumerate(SB_SPOTS, 1):
        n = len(st)
        p = pays[:n]
        pos = I.positions(n)
        g = FT.solve_sb(st, p)
        print(f"### S{k}. {title}\n")
        print("Stacks: " + ", ".join(f"{pos[i]} {st[i]:g}" for i in range(n)) + "\n")
        if g is None:
            print("(Too short for limps or raises: push/fold.)\n")
            continue
        print("| Small blind | % of hands | Range |")
        print("|---|---|---|")
        for a in ("jam", "limp", "raise"):
            if a == "raise" and not g["can_raise"]:
                continue
            print(f"| {names[a].capitalize()} | {share(g['ranges'][a]):.1f}% | "
                  f"`{compact(g['ranges'][a])}` |")
        fold = 100 - sum(share(g["ranges"][a]) for a in ("jam", "limp", "raise"))
        print(f"| Fold | {fold:.1f}% | |\n")
        bb = g["bb"]
        print(f"Big blind: jams over a limp **{share(bb['vs_limp']):.1f}%** "
              f"(`{compact(bb['vs_limp'])}`)" +
              (f"; over the raise **{share(bb['vs_raise']):.1f}%** (`{compact(bb['vs_raise'])}`)"
               if g["can_raise"] else "") +
              f"; calls the jam **{share(bb['vs_jam']):.1f}%** (`{compact(bb['vs_jam'])}`).\n")
        print("Each option's value compared with folding (ICM, $):\n")
        print("| Hand | The trainer plays it | Limp | Raise 3 | Jam |")
        print("|---|---|---|---|---|")
        for h in SB_HANDS:
            mix, ev = g["mix"][h], g["ev"][h]
            plays = " / ".join(f"{names[a]} {100 * f:.0f}%" for a, f in
                               sorted(mix.items(), key=lambda kv: -kv[1]) if f >= 0.05)
            cell = lambda a: f"{ev[a] - ev['fold']:+,.0f}" if a in ev else "-"  # noqa: E731
            print(f"| {h} | {plays} | {cell('limp')} | {cell('raise')} | {cell('jam')} |")
        print()


def main():
    pays = PAYS
    print("# ICM check: the trainer vs HRC / ICMIZER\n")
    print("Settings for every spot: blinds 0.5 / 1 bb, a **big blind ante of 1 bb** (dead "
          "money; in HRC: ante type *Big blind ante*; in ICMIZER: *BB ante*), ICM model "
          "*Malmuth-Harville*, push/fold only (no limps or min-raises), equity model: plain "
          "ICM (no future game / FGS). Payouts (WSOP Circuit, $1M pool): " +
          ", ".join(f"{I.ordinal(k + 1)} ${p:,}" for k, p in enumerate(pays)) +
          ". With fewer players left, use the top places only.\n")
    print("Stacks are in big blinds, listed in seat order from the first to act to the big "
          "blind. Ranges: hands the solve plays at least half the time.\n")
    for n_spot, (title, st, j, hero, hands) in enumerate(SPOTS, 1):
        n = len(st)
        p = pays[:n]
        pos = I.positions(n)
        print(f"## {n_spot}. {title}\n")
        print("| Seat | Stack (bb) | ICM equity |")
        print("|---|---|---|")
        eq = I.icm(st, p)
        for k in range(n):
            print(f"| {pos[k]} | {st[k]:g} | ${eq[k]:,.0f} |")
        print(f"\nPrize money left: ${sum(p):,}\n")
        res = FT.solve(st, p, j)
        chip = FT.solve(st, p, j, chips=True)
        print(f"**{pos[j]} jams first in** (everyone before folds): "
              f"{share(res['jam_range']):.1f}% of hands "
              f"(chip EV: {share(chip['jam_range']):.1f}%)  \n`{compact(res['jam_range'])}`\n")
        for i in res["spot"].behind:
            cs, need = res["calls"][i]
            print(f"- **{pos[i]} calls** {100 * cs:.1f}% (chip EV "
                  f"{100 * chip['calls'][i][0]:.1f}%), needs {100 * need:.1f}% equity  \n"
                  f"  `{compact(res['call_ranges'][i])}`")
        print()
        print(f"Value of jamming over folding for the {pos[j]} (ICM, $): " + ", ".join(
            f"{h} {res['gain'][h]:+,.0f}" for h in hands) + "\n")
        if hero is not None:
            cf = I.call_from(res, hero)
            print(f"**{pos[hero]} facing the jam** (calling vs folding, $): " + ", ".join(
                f"{h} {cf['gain'][h]:+,.0f}" for h in hands) +
                f"  \nCall range {100 * cf['share']:.1f}%, needs {100 * cf['need']:.1f}% equity\n")
    sb_section(pays)
    print("## What can differ, and why\n")
    print("- **Equities** (the tables above) are plain Malmuth-Harville ICM: they should match "
          "HRC and ICMIZER to the dollar. Checked here against a brute-force version that lists "
          "every finishing order.")
    print("- **Ranges**: the trainer solves with 40 rounds of fictitious play; HRC and "
          "ICMIZER iterate to a tighter equilibrium. Hands right at the edge (a jam worth a "
          "few dollars either way) can land on either side.")
    print("- **Overcalls**: the trainer lets the first caller play the hand out against the "
          "jammer (a second caller is rare at these stacks); HRC and ICMIZER model 3-way "
          "all-ins. Expect the small differences in early positions with many players behind.")
    print("- **Blinds folding**: when the jammer folds, the trainer gives the blinds to the big "
          "blind (a walk); both programs do the same in push/fold mode.")


if __name__ == "__main__":
    main()
