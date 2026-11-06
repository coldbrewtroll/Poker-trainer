# Bringing sims and charts into the 8-max bot

The 8-max bot (`contender8`) plays your imported ranges preflop wherever you have them, and
its own built-in strategy everywhere else. Add spots and depths a few at a time; nothing
breaks if one is missing.

## Fastest: a whole MonkerSolver tree at once

1. In MonkerSolver's **Solve** tab, right-click the ranges and choose **Export preflop for
   MonkerViewer**. It writes one `.rng` file per decision into a folder inside
   MonkerSolver's `ranges` folder (search for `*.rng` if you can't see it).
2. Zip that folder, or point the converter at it, and give the stack depth:

```bash
py -3.13 solver/monker_import8.py 20bb.zip --depth 20
```

That writes every spot of the tree into `my_ranges/` with the names below. The trainer
reads `my_ranges/` by itself. For the contest bot as well, also run step 2 below (import).
`--players 6` for a 6-max tree, `--ante 0` for no ante.

## 1. Name each file after the spot

```
<spot>@<stack depth>__<action>.txt
```

**The spot** is the hero's position, then each earlier preflop action that wasn't a fold:
`<position>` + `r` (raise), `c` (call) or `a` (all-in), joined with underscores.
Positions: `UTG UTG1 LJ HJ CO BTN SB BB`.

| Situation | File name |
|---|---|
| UTG opens to 2.3 bb at 100 bb | `UTG@100__raise_2.3bb.txt` |
| CO opens | `CO@100__raise_2.3bb.txt` |
| BTN 3-bets an UTG open to 3x | `BTN_UTGr@100__raise_3x.txt` |
| BTN calls an UTG open | `BTN_UTGr@100__call.txt` |
| BB 3-bets / calls vs a BTN open | `BB_BTNr@100__raise_3.5x.txt`, `BB_BTNr@100__call.txt` |
| BTN calls after UTG opens and CO calls | `BTN_UTGr_COc@100__call.txt` |
| UTG 4-bets / calls vs a BTN 3-bet | `UTG_UTGr_BTNr@100__raise_2.3x.txt`, `UTG_UTGr_BTNr@100__call.txt` |
| CO shoves first in at 10 bb | `CO@10__allin.txt` |
| BB calls a BTN shove at 10 bb | `BB_BTNa@10__call.txt` |
| SB opens vs BB (blind vs blind) | `SB@40__raise_2.5bb.txt` |
| BB vs the SB open | `BB_SBr@40__call.txt`, `BB_SBr@40__raise_3x.txt` |

**Heads-up sims** (like your SB-vs-BB 40 bb one) can keep their heads-up names
(`sb_first@40__raise_2.5bb.txt`, `bb_vs_raise@40__call.txt`, ...); they're read as the
blind-vs-blind spots above.

**Actions:** `fold`, `call` (or `check`), `allin`, `raise`, or sized: `raise_2.3bb` (to
2.3 big blinds), `raise_3x` (3 times the bet faced), `raise_75pct` (75% of the pot).
No fold files needed: anything left out is a fold (or a check when free).

**Range text:** anything MonkerViewer exports (`AhKh@100,...`), or `AA,KK,AKs:0.5,...`,
or `[50]AQo,KJs[/50]`. For a chart that's only an image, send a screenshot to Claude with
the spot, action and depth, and it will type it out.

**PioSolver ranges** work the same way: in PioSolver (or PioViewer) select the strategy
for one action, copy the range as text (it looks like `AA:1,AKs:0.5,...` or
`AsAh:1,AdAc:0.5,...`) and paste it into a `.txt` file named as above. Pio's preflop
solves and its preflop range files are what the bot can use. Postflop sims (`.cfr` files)
can't: they're one flop each, far too big to bring to the contest, and the bot works out
postflop play itself in under 1 ms.

## 2. Import

```bash
cd %USERPROFILE%\Downloads\pokerbot
```
```bash
py -3.13 solver/import_ranges8.py my_ranges
```

It lists every file it read with the share of hands, and warns about skipped files or a
hand whose actions add up to more than 100%. One folder can hold heads-up and 8-max files:
this importer reads both (heads-up names as blind vs blind), and the heads-up importer
(`import_ranges.py`) quietly skips the 8-max names.

## 3. Check it plays

```bash
py -3.13 match8.py contender8 "starter8*7" --depth 100 --hands 2000
```

`--depth` is in big blinds; `--stacks 10-100` gives every seat a random stack instead,
like a tournament. Use fewer opponents for a short table (`"starter8*5"` = 6-handed).
Look for `corrected: 0, errors: 0`.

## What to solve first

Most hands are decided by a few spots, so in order of value:

1. **First-in opens** for every position (7 files per depth)
2. **Big blind and button vs each open** (call / 3-bet)
3. **Opener vs a 3-bet** (call / 4-bet)
4. **Blind vs blind** (your heads-up SB-BB sims cover this)
5. **Short-stack shove / call charts** (10-15 bb)

Depths: as for heads-up, neighbours at most 2x apart get blended (e.g. 15, 20, 30, 40, 60,
100 bb).

## Solver settings (online MTT)

- **Players:** 8 (and separate sims for short tables if you play them, see below)
- **Blinds and antes:** small blind 0.5, big blind 1, **ante 0.125 from every player**
  (12.5%, the usual online tournament structure)
- **Rake:** none
- **Stacks:** equal stacks at each depth you solve
- **Payouts:** chip EV (the bot doesn't model ICM / payout pressure)

## Short tables (6-handed, 5-handed, heads-up)

Positions are named by how many players act after you, so the same names work at any
table size: with fewer players the earliest positions disappear.

| Players | Positions (first to act ... big blind) |
|---|---|
| 8 | UTG, UTG1, LJ, HJ, CO, BTN, SB, BB |
| 7 | UTG1, LJ, HJ, CO, BTN, SB, BB |
| 6 | LJ, HJ, CO, BTN, SB, BB |
| 5 | HJ, CO, BTN, SB, BB |
| 4 | CO, BTN, SB, BB |
| 3 | BTN, SB, BB |
| 2 | SB (button), BB |

So in a **6-max sim**, rename the first position to **LJ** and the second to **HJ** (many
tools call them UTG and MP): a 6-max "UTG open" file is `LJ@100__raise_2.3bb.txt`.
In a **5-handed sim** the first position is **HJ**. An 8-max sim's LJ open is then used for
the first player at a 6-handed table too, which is right: both have 5 players behind.
If you have both an 8-max and a 6-max sim for the same spot, use the one for the table
size you play most (they share a name).
