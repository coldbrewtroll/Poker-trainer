# Solve a spot (heads-up flop solver)

In the trainer: **Spots → Flop sim → ⚙ Solve a spot…**
On its own: `py -3.13 solve_spot.py`

1. **Depth** and **Spot**: every heads-up line your sims have ranges for (`pio_ranges/`,
   built from your Monker sims): single-raised pots (e.g. *CO opens 2.01, BB calls*) and
   3-bet pots, at 20 / 30 / 40 / 50 / 60 bb.
2. **Flop**: type it (`Kh7d2c`) or press Random.
3. **Bet sizes** (% of the pot): the out-of-position player's flop bets, the in-position
   player's flop bets, flop raises, turn bets, river bets. All-in is always an option
   (raising on the turn and river is all-in). The defaults are the ones the trainer's
   78-flop library uses.
4. **Accuracy**: how close to a perfect (unexploitable) strategy, as % of the pot. The
   default 0.5% is accurate enough to study from. The solve runs until it gets there,
   then the strategy opens.

The bar shows the solve's progress: the iteration, and how far it still is from
perfect. **Cancel** stops it.

## Solved once, kept for good

Every solve is saved, so you never wait for the same spot twice. Pressing **Solve** on
a spot, flop and sizes already solved opens the saved solve at once, with no wait. That
includes the same flop with the suits swapped: Kh7d2c and Ks7h2d play the same, so you
see the saved solve with your suits. If the saved solve is less accurate than you asked
(say, a library flop solved to about 1% when you asked for 0.5%), it opens at once and
the button turns into **Solve again**: press it to solve to your accuracy. The new solve
replaces the old one.

## Reading the solve

- **The line** (Flop › BB check › CO bet 1.82 …) and who is to act, with the pot and
  the amount to call.
- **The actions**, with how often the whole range takes each one. Click an action to
  follow that line. **Back** and **Start** take you back up the tree.
- **The 13×13 grid**: each hand's colours show how it plays here. The coloured height
  shows how much of the hand is still in the range on this line, so a short bar means
  most combos of it are gone. Grey hands aren't in the range at all.
- **Click a hand** to see its combos one by one (suits matter: flush draws, blockers).
- **Play this flop**: the Flop sim drill on this exact solve, against the solve.

The saved strategy covers the flop: every decision until the turn card. Every solve is
saved to `postflop/<depth>bb_<spot>/<flop>.json`, so the Flop sim drill, the bots and
your hints use it from then on. **Or open a solved flop** browses every flop solved for
that spot, including the trainer's library.

## Speed and memory

The solver uses every core of your PC at low priority, so the trainer stays smooth. A
deep-stacked solve with several sizes needs more time and memory. As a guide:

| | memory | time to 0.5% (8-core PC) |
|---|---|---|
| 20–30 bb, default sizes | about 1–2 GB | about 2–5 min |
| 40–60 bb, default sizes | about 2–4 GB | about 5–15 min |

Fewer sizes (one flop bet size each, say) solve much faster.

## The solver program

**Not included in this repository.** The trainer runs TexasSolver's console program; get it
from the TexasSolver project (https://github.com/bupticybee/TexasSolver: its source code
builds `console_solver`) and put it in `solver_bin\` with its `resources` folder, or set
the TEXASSOLVER environment variable to its path. Without it, Solve a spot still browses
and plays every flop already solved.

`solver_bin\console_solver.exe` with `solver_bin\resources\compairer\card5_dic_sorted.txt`
is **TexasSolver** by bupticybee: free, open-source software under the GNU AGPL v3
(`solver_bin\LICENSE-TexasSolver.txt`). Source code: https://github.com/bupticybee/TexasSolver
(this build: commit 02c8021, compiled for Windows unchanged). The trainer runs it as a
separate program and reads the strategies it writes. To use another build, put it in
`solver_bin\` with its `resources` folder, or set the TEXASSOLVER environment variable to
the program's path.
