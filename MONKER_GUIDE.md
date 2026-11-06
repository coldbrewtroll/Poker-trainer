# Bringing your MonkerSolver sims into the bot

The bot plays your sims preflop wherever you've imported them, and its own built-in
strategy everywhere else. You can add sims one at a time; nothing breaks if a spot or a
depth is missing.

## 1. Export one range per spot and action

In MonkerViewer, open a sim, click a spot (node), and copy the range for one action.
Paste it into Notepad and save it in the folder `pokerbot\my_ranges` with a name like:

```
<spot>@<stack depth>__<action>.txt
```

(two underscores before the action). Examples for a 100 bb sim with a 2.5 bb open:

| Spot in the sim | Action | File name |
|---|---|---|
| Small blind, first to act | raise to 2.5 bb | `sb_first@100__raise_2.5bb.txt` |
| Small blind, first to act | call (limp) | `sb_first@100__call.txt` |
| Big blind vs the open | 3-bet to 3x | `bb_vs_raise@100__raise_3x.txt` |
| Big blind vs the open | call | `bb_vs_raise@100__call.txt` |
| Small blind vs the 3-bet | 4-bet to 2.2x | `sb_vs_3bet@100__raise_2.2x.txt` |
| Small blind vs the 3-bet | call | `sb_vs_3bet@100__call.txt` |
| Big blind vs the 4-bet | all-in | `bb_vs_4bet@100__allin.txt` |
| Big blind vs the 4-bet | call | `bb_vs_4bet@100__call.txt` |
| Small blind vs an all-in 5-bet | call | `sb_vs_5bet_allin@100__call.txt` |

You don't need fold files: whatever a spot's ranges leave out is a fold (or a check when
it's free).

**All spot names** (the code in brackets is the action so far: c = call, r = raise,
a = all-in; you can also write `line_<code>`, e.g. `line_crr`):

```
sb_first ()            bb_vs_limp (c)          bb_vs_raise (r)       bb_vs_allin (a)
sb_vs_3bet (rr)        sb_vs_3bet_allin (ra)   bb_vs_4bet (rrr)      bb_vs_4bet_allin (rra)
sb_vs_5bet (rrrr)      sb_vs_5bet_allin (rrra) sb_limp_vs_raise (cr) sb_limp_vs_allin (ca)
bb_vs_limp_3bet (crr)  bb_vs_limp_reshove (cra)
```

**Action names:** `fold`, `call` (or `check`), `allin`, `raise`, or a sized raise:
`raise_2.5bb` (to 2.5 big blinds), `raise_3x` (3 times the bet you face),
`raise_75pct` (75% of the pot). If a spot in your sim has two raise sizes, make one file
for each.

## 2. Import

```bash
cd %USERPROFILE%\Downloads\pokerbot
```
```bash
py -3.13 solver/import_ranges.py my_ranges
```

It lists every file it read with the share of hands in each range, and warns about
anything it skipped or any hand whose actions add up to more than 100%. Push/fold sims
are also compared with the kit's own solve.

## 3. Check it plays

```bash
py -3.13 match.py contender contender.Classic --stack 200 --hands 20000
```

`--stack` is in chips with a 2-chip big blind, so 200 = 100 bb, 100 = 50 bb, 40 = 20 bb.
Look for `corrected: 0, errors: 0`.

## Which depths to solve

Between two solved depths the bot blends them (when the deeper is at most twice the
shallower), and it uses a sim within 20% of its depth. So a good set is something like
**10, 15, 20, 30, 40, 60, 80, 100 bb**: each step is under 2x, covering everything from push/fold
to deep play. Anything not covered falls back to the built-in strategy, which is already
solid (see RESULTS.md).

If your sims use antes or different blinds than the contest, the ranges won't fit the
game; solve with the contest's exact structure.
