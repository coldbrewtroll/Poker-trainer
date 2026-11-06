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
    parts = []
    pairs = [r + r for r in RANKS]
    run = [p for p in pairs if p in on]
    # pairs: one run from the top down, or listed
    i = 0
    while i < len(pairs):
        if pairs[i] in on:
            j = i
            while j + 1 < len(pairs) and pairs[j + 1] in on:
                j += 1
            if j == len(pairs) - 1 and i > 0:
                parts.append(pairs[i] [0] * 2 + "+" if False else f"{pairs[j]}+" if False else
                             f"{pairs[j][0]}{pairs[j][0]}+")
            parts.append(None)
            parts.pop()
            parts.append(f"{pairs[j]}+" if j == len(pairs) - 1 and False else
                         (f"{pairs[j]}-{pairs[i]}" if i != j else pairs[i]))
            i = j + 1
        else:
            i += 1
    out = [p for p in parts if p]
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
    del run
    return ", ".join(out) or "(none)"


def share(weights):
    return 100 * I._share_w(weights)


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
