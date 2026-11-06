# Poker Trainer: 8-max preflop, postflop and ICM final tables

A practice table for No-Limit Hold'em tournaments: play hands against bots that follow
real solver ranges (MonkerSolver, 15-60 bb), get a hand-by-hand grade, and play whole
WSOP-Circuit-style final tables with an ICM Coach that explains every decision.

**Play in your browser (iPhone and Android too):** https://coldbrewtroll.github.io/Poker-trainer/
— the Final table mode with the ICM Coach, nothing to install. On iPhone open it in Safari,
then Share → Add to Home Screen to keep it like an app.

## Try it (Windows)

1. Install **Python 3.12 or newer** from https://www.python.org/downloads/ (free). In the
   installer, tick **"Add python.exe to PATH"**.
2. On this page click the green **Code** button, then **Download ZIP**, and unzip it
   anywhere (right-click the zip, **Extract All**).
3. In the unzipped folder, double-click **Poker Trainer.bat**. The first start takes about
   a minute while it reads the ranges.

Mac / Linux: install Python 3.12+ (with Tk), then in the folder run `python3 trainer8.py`.

Nothing else to install, no account, nothing goes online.

### What's in it

- **Play**: 8-max hands at the depth you pick; the hint shows what the solver ranges do.
- **Drill / Spots**: the same preflop spot over and over, or set-up flop spots.
- **Final table**: 250 preset final tables (WSOP Circuit payouts and blinds), played until
  you bust or win. Avatars for every player, TV-style board with win % on all-in run-outs,
  Turbo speed, and "Skip folds" to skip the hands that are clear folds.
- **ICM Coach** (button at the table): your equity and the pay jumps, risk premiums,
  jam / call / fold in prize money, what a 2x open or 3-bet does, and exploits against
  players who call or jam too wide or too tight.
- **Leaks**: your biggest blunders per session and over time.
- **ICM** tab: ICM calculator, push/fold charts under ICM, satellites.

The ranges are the author's own MonkerSolver sims (in `my_ranges/`). Player avatars:
"Open Peeps" by Pablo Stanley (CC0), made with DiceBear.

---

# pokerbot practice kit

No-Limit Hold'em bots, engines and tools for a timed pokerbot contest: **8-max online
tournament play** (from 8 players down to heads-up, 12.5% antes, no rake) plus the
original heads-up kit. Every move fits in about 1 ms on plain Python.

## Practice trainer (play against the bots yourself)

Double-click **`Poker Trainer.bat`** (or run `python trainer8.py`). A table opens with you
and 7 bots. Click Fold / Check-Call / a bet size / All-in, or drag the slider for your
own size (keys: F, C, 1-3, A, Space for the next hand). After every decision the panel on
the right shows what the bot would have done in your seat and keeps a score of how often
you made the same play. The strip along the top shows the hand's action so far.

| Mode | What it does |
|---|---|
| **Play** | normal hands; every hand starts with the same stacks (pick the depth in the Coach tab; `--players 6` shorter table, `--bots starter8` easier) |
| **Drill** | quick preflop practice at random depths (10-25, 25-50, 50-100 bb or all), only in the spot types you pick: any, facing an open, facing a 3-bet, facing a 4-bet |
| **Spots** | one exact spot over and over: pick the situation (facing an open / defend and play the flop / open and play the flop / open and face a 3-bet / first in), your position, the villain's position and the depth, e.g. **BB defends vs a CO open, 40 bb, single-raised pot**. Everyone else folds, the villain only opens / calls / 3-bets with hands the bot really would, and in the flop spots you start on the flop with a hand the bot really plays that way |

**Difficulty** (Drill and Spots): **Medium** only deals hands that are close decisions
preflop: a hand a pip or two away (kicker up or down 1-2, top card up or down 1) is
played differently. So T9o in the CO or A5s as a 3-bet bluff
come up, but never an obvious fold like 92o in the HJ or an obvious raise like AA.
**Expert** only deals the hands right on the edge (kicker 1 pip). **Postflop** (top bar, Drill mode, on by default):
your drilled hands go on to the river with you playing every street; off, Drill is
preflop only and the rest of the hand is played for you.

Your Monker sims are preflop only: postflop play (sizes and frequencies) comes from the
bots' own postflop logic, not a solver.

**After you fold** the hand pauses: **Watch hand** (W) plays the rest out at bot speed,
**Next hand** (Space) skips it. With **Fast fold** on (top bar, the default) it skips by
itself after 4 seconds if you don't choose; with it off it waits for you. Once a skipped
hand ends, press **R** (Review this hand) to keep it on screen and study the hand tree.
**Bot speed**: Slow 1.6 s, Normal 1 s, Fast 0.35 s per bot action. After you act, the next player takes
a moment longer (about 1.6 s at Normal), so you can think while they decide.

**Your sims in the trainer**: everything in `my_ranges/` is played by the trainer's bots
(and shown in its charts and hand tree) at that depth, blended between depths you have. A
whole MonkerSolver tree converts in one go: see MONKER_GUIDE_8MAX.md. Your 20, 30, 40, 50 and 60 bb
8-max sims (and 15 bb) are already in. Where you have no sim or chart, the trainer's bots use built-in rules
fitted to the ones you do have (the SB limps, traps and jams first in; the BB defends wide
and raises or jams against a limp). They are close, not solver-exact: see RESULTS.md.

**Final table** mode plays whole final tables (100 preset tables, WSOP Circuit blinds and
payouts) with your sims, the final table's ICM rules and your Pio flop solves. The **ICM
Coach** button explains every decision: your equity and the ladder, the risk premium
against each player, jam / call / fold in prize money with a verdict, what a 2x open, a
3-bet or a 4-bet does (how the others answer it, the SPR it leaves), and **exploits**: how
the answer changes against players who call or jam wider or tighter than the equilibrium
(this hand, and the hands to add or drop), and whether a bluff-sized open, 3-bet, 4-bet
or bet shows a profit against how often the others fold. **Leaks** keeps your biggest
blunders per session and across sessions.

**ICM study** (the **ICM** button in the top bar; for your own tournament study, since the
contest itself is chip EV): set each stack and the payouts (presets: final table, bubble,
3-handed, heads-up, or type your own), see every player's prize equity against their share
of the chips, and push/fold charts for any seat under ICM next to chip EV, with the risk
premium (extra equity a call needs), plus a quiz on the close hands. The **Mega Satellites**
tab does the same for satellites with hundreds of players and N equal seats: *Traditional*
(the last N players standing win the seats) or *Landmark* (anyone who reaches the target
chip count wins a seat and leaves: below the target chips count at face value, above it
they're worth nothing). The **Risk premiums** view shows, for every pair of players, the
extra equity one needs to call an all-in from the other compared with chip EV. Push/fold there is a simplified equilibrium (first caller only, ranges by all-in
equity), close to what ICM tools show but not a full solver.

**Postflop solves (PioSOLVER)**: your Monker sims are 8-max preflop; most 8-max flops are
heads-up, so the postflop side is solved heads-up in Pio from your 8-max ranges.
`py -3.13 solver/pio_ranges.py` writes, for every spot (single-raised pots of each seat vs
the BB, CO vs BTN, HJ vs CO/BTN, and the main 3-bet pots) and every depth you have a sim
for, the two Pio range files and the starting pot and stack (`pio_ranges/<depth>bb/<spot>/`).
Multiway pots stay with the bots' own postflop logic.
Old Pio saves work too: folders named the usual ways (e.g. `BU-BB SRP 40bb`,
`BB vs BTN 3bet 25bb`, `CO_v_BB_SRP_30BB`) are understood, each save's chip scale is worked
out from its pot and stacks, and single-raised, 3-bet and 4-bet pots are supported (limped
pots are skipped). In the trainer, pick the solved spot, then a random or a chosen flop.

Then solve a spot in PioSOLVER (Edge or Pro) with those ranges, save each flop as
`<spot>_<depth>bb_<board>.cfr` (e.g. `SRP_BTN_vs_BB_30bb_Ks7d2c.cfr`) and run
`py -3.13 solver/pio_export.py "D:\PioSOLVER\saves"`: it reads every save through Pio's
command interface and writes the flop strategies into `postflop/`. In the trainer,
**Spots -> Flop sim** deals a solved spot (preflop played for you, both hands from the
solve's ranges, the solved flop): the opponent plays the solve on the flop, your flop
decisions are graded against it (an action the solve takes at least 25% of the time with
your exact cards counts as right) and the hand tree shows the solve's frequencies. From
the turn on, the bots play.

To check the coach, run `py -3.13 solver/coach_audit.py --minutes 30`: it plays whole
final tables through the trainer's own hint, hand tree, ICM grade and ICM Coach popup
(windows flash open and shut while it runs) and writes anything wrong (a wrong fact,
verdict or grade, a number out of range, an error) to `coach_audit.jsonl`.

To check the preflop play, run `py -3.13 solver/preflop_audit.py sweep` (every line at
every depth from 8 to 65 bb), `... play --samples 1000000` (Play mode tables) or
`... ft --samples 1000000` (final tables): each spot is asked with all 1326 hands and
anything odd (illegal moves, AA folding, a hand calling while a better one folds, half-stack
raises, folds at a huge price) goes to `preflop_audit.jsonl`.

To check your exported solves, run `py -3.13 solver/flopsim_audit.py`: it plays a few
thousand Flop sim hands from them, plus Play-mode hands on random flops that use the nearest
solve, and writes anything wrong (a pot that doesn't match, a decision missing from the
tree, a move the table won't accept, an error) to `flopsim_audit.txt`.

**Decision details**: click any card in the hand tree to open it in its own window: each
action with how often it's taken, its combos, its EV (your decisions) and the average
equity of the hands that take it; the player's hand and whole range against the ranges
still in; and the range as a 13x13 grid coloured by action with every hand's equity. **Playable only** (Play mode, on by default): you're only dealt
hands that are opens or close to the edge of the range (or continue / are close facing a
raise); obvious folds such as 73o or T2o are never dealt to you.

**Hand tree** (right-hand panel): every decision of the hand as a card, joined like a
flow chart: what the bot does with that player's whole range there (the bar and the
percentages), the action actually taken, and on your cards your play next to the bot's
(gold dot). Ranges narrow as the hand goes on. **Chart** (or click a card) opens a separate
window with the 13x13 preflop chart for any preflop decision of the hand: every hand
coloured by the bot's action, split where it mixes, your hand outlined. The charts are the
bot's own strategy, which uses your imported sims (`my_ranges`) wherever you have them.

The trainer's bots (the coach, the tree, the charts and your opponents) play the **sound
version** of the strategy: a solver-like big blind defence (about 56% vs a CO open at
40 bb, so no J4o) and no continuing after the flop without a pair, a draw or overcards.
The contest bot shares everything except the big blind defence, which it keeps wider
because that measured 1-3 bb/100 better against the test bots; for the solver-like
defence in the contest too, set `blind_curve = 1` in `bots/contender8.py`.

**EV of each option**: at your decisions the trainer plays each option (fold, call, each
raise size, all-in) out a few hundred times in the background, with the opponents' hands
drawn from their current ranges and the same samples for every option, and shows each
option's average result in bb against folding (after you act, or straight away with
Hint on). These are EVs against these bots playing the rest of the hand, not solver EVs.

**Hole cards**: when a hand ends, every bot's cards are shown (folded hands muted), at the
table and on their cards in the hand tree.

**Replay hand** (Z) after any hand: the same cards, seats and stacks again; the bots make
the same choices until you play differently. Replays don't count in your results.

**Previous hand** (◀ button on the table, or B / ←) steps back through your last 50 hands:
the table, the cards everyone held and the full hand tree. → goes forward, Esc returns. The
table waits while you look back, so you come back to the same decision.

**Study charts** (`study_ranges/`, not included here): put charts from your own study
tools in that folder and the trainer uses them too (one file per spot and action, as in
`my_ranges/`). The contest bot never reads them.
In the browser version: **Settings → My charts** loads a zip of them on your phone only
(kept in that browser, never uploaded).

**Grades**: every decision gets one: the bot's play is an **Elite pro decision**; otherwise
your option's rank (by simulated EV, or by how often the bot's range takes it while EVs
are still coming in) makes it a **Shitreg** (second best), **Dustreg** (third) or **Fish**
(worst option) decision.

**Bet sizes**: one button per spot, the bot's own size (e.g. a 3-bet of 2.65x in position or
3.2x from the blinds at 25 bb or less); drag the slider for any other size.

**Hint** shows the bot's play before you act. The bot's play is your contender8 bot, not
a solver: use it to learn and check the bot's strategy. Fonts: it uses Bahnschrift and
Segoe UI (both come with Windows 10 and 11), or Inter if you have it installed.
`play8.py` is the same game in plain text.

## Preflop solvers (experimental)

| Tool | What it is |
|---|---|
| `solver/dcfr_preflop.py BTN CO 25` | Discounted CFR over all 169 hands for one spot, heads-up after the open: exact all-in equities with card removal, a model for pots that see a flop. Solves in a second |
| `solver/mccfr8.py --players 8 --depth 25 --iters 3000000 --seed 1 --save` | Monte Carlo CFR (external sampling) over the whole 8-max or 6-max preflop game: opens, cold calls, small 3-bets and squeezes, jams. Run several seeds, then `--merge`. Runs faster with PyPy (`pypy3 solver/mccfr8.py ...`). 3 million iterations take about 30-80 minutes |

How they compare with GTO Wizard charts at 25 bb (share of hands with the same main action):
the 8-max solver 88% on the CO open and 90% on BTN vs CO; the trainer's built-in logic 96%
and 88.5%. Neither solves the postflop game, which is what makes A2o a fold and 65s an
open, so they stay experimental: the trainer only uses solved tables with `--solved`. For
real solver ranges, import MonkerSolver / Pio sims (my_ranges) or put study charts in
study_ranges.

## 8-max (online MTT): `bots/contender8.py`

```bash
python match8.py contender8 "starter8*7" --depth 100 --hands 2000    # one table
python match8.py contender8 "starter8*5" --stacks 10-100             # 6-handed, uneven stacks
python tournament8.py contender8                                     # every field and depth
python solver/import_ranges8.py my_ranges                            # your sims and charts
```

| Part | How it plays |
|---|---|
| Your sims / charts | used first wherever imported (see [MONKER_GUIDE_8MAX.md](MONKER_GUIDE_8MAX.md)); heads-up SB-BB sims cover blind vs blind |
| Short stacks (10 bb or less) | shove-or-fold, priced with how many players are left to act and how often each calls an all-in |
| 25 bb or less vs an open | small 3-bets: 2.65x in position, 3.2x out of position. Value hands plan to call a jam, bluffs (suited wheel aces, suited kings, ...) plan to fold to one; middling hands re-jam, call or fold by EV |
| All-ins | every open shove, re-shove and 4-bet shove priced with fold equity, against the range we'd really shove and each player's real all-in call rate (split: after putting chips in / cold) |
| Deeper | opens by position (wider with antes), polarized 3-bets/4-bets/5-bets (value + blocker bluffs) as a share of the raiser's range, calls priced with equity vs that range, tighter with callers in and players behind |
| Postflop | simulated equity against every opponent still in, each with their own range narrowed by their bets; multiway thresholds and less bluffing into several players; c-bet bluffs and turn barrels as the preflop raiser, scaled by how often that opponent folds |
| Opponents | each tracked separately (opens, 3-bets, calls, aggression, folds to bets and to re-raises, all-in calls); opens get wider against tables that fold too much |

`match8.py` replays every deal with the hero in every seat, so luck and position cancel.
Table size = number of opponents + 1. Chips use an 8-chip big blind so the 1/8 ante is
exact; `--depth`/`--stacks` are in big blinds. Results: [RESULTS.md](RESULTS.md).

## Heads-up

The heads-up kit below is unchanged (`engine.py`, `match.py`, `bots/contender.py`).

```bash
python match.py contender starter --hands 10000 --stack 200     # one matchup at 100 bb
python tournament.py contender                                 # vs the whole pool, 20/50/100 bb
python -m pytest tests -q                                      # 17 tests
```

On Windows use `py -3.13` in place of `python`.

## The contest bot: `bots/contender.py`

| Part | How it plays |
|---|---|
| Preflop, your sims imported | your ranges, mixed at the solved frequencies, blended between solved depths |
| Preflop, 10 bb or less | solved push/fold equilibrium (`solver/pushfold.py`), shove-or-fold vs raises |
| Preflop, deeper | ranges by hand strength sized for the stack depth; every call priced with real equity against the opponent's likely range |
| Postflop | simulated equity in 0.15 ms against the opponent's range, narrowed by their bets; pot odds plus a safety margin that grows with bet size; extra care before committing a deep stack |
| Opponent model | learns how often the opponent opens, 3-bets, re-raises, bets and calls all-ins, and adjusts (e.g. calls a maniac much wider) |

Results and how they were measured: [RESULTS.md](RESULTS.md).
How to bring in your MonkerSolver sims: [MONKER_GUIDE.md](MONKER_GUIDE.md).

## Tools

| File | What it does |
|---|---|
| `match.py` | one matchup in **duplicate** (each deck played twice, seats swapped, so luck cancels); bb/100 with a 95% range, timing, illegal-move and error counts |
| `tournament.py` | one bot vs a pool at several depths, in parallel; `bot:param=value` overrides settings without editing code |
| `sweep.py` | tries setting values against a strong pool, for tuning |
| `compare8.py` | two 8-max bots on exactly the same deals (paired): how much better one is, with far less noise than separate runs |
| `solver/equity.py` | all-in equity between the 169 starting hands (+ card-removal weights) |
| `solver/pushfold.py` | solves heads-up push/fold (fictitious play, exploitability < 0.001 bb/hand), `--show 10` prints charts |
| `solver/build_preflop_tables.py` | hand-strength ranking + equity vs top-x% ranges, as plain Python data |
| `solver/import_ranges.py` | your solver exports -> `bots/range_table.py`, compared with the kit's own push/fold solve |

## Bots

| Bot | Notes |
|---|---|
| `contender` | the one to use |
| `contender.Classic` | contender with rule-of-thumb postflop (for comparison) |
| `ranges`, `shortstack`, `starter` | earlier layers: imported ranges / push/fold / Chen + pot odds |
| `baselines.CallingStation`, `.Maniac`, `.RandomBot` | weak test opponents |

A bot is a class with `act(state) -> (action, amount)`; `("raise", n)` means raise to a
total of n this street. Optional hooks: `new_hand(hand_no, is_button)`, `hand_over(result)`.

## Speed

Plain Python: hand evaluation about 2.8 us, contender about 0.1 ms per move on average.
On this (shared cloud) machine about 1 move in 2,500 goes over 1 ms; plain busywork with no
poker code does the same (1 in 4,000), so it's the machine pausing the process. If the
contest is strict about every single move, lower `budget_s` in `bots/postflop.py`
(e.g. to 0.0001) or use `contender.Classic` postflop (about 0.01 ms per move).

Garbage collection is switched off during moves and run between hands (`new_hand`). If
the contest engine has no per-hand hook, call `gc.collect()` yourself when a hand ends.

## Before the contest

Check the real rules: heads-up or more players, starting stacks, blinds/antes, how bots
connect, time limits, and whether pre-made data (range tables) may be brought to the
60-minute build. Adapt `engine.py` to match the real interface and rehearse with it.

## 60-minute build plan (if you must write it from memory)

1. **0-10 min: legal and safe.** Never crash, never act illegally; wrap in try/except.
2. **10-25 min: preflop.** Chen score with open / 3-bet / call / fold thresholds by position;
   push/fold at short stacks.
3. **25-45 min: postflop.** Hand strength plus draws vs pot odds `to_call / (pot + to_call)`;
   need more equity against bigger bets.
4. **45-55 min: sizing and aggression.** Value-bet thin (~50% equity) at about 2/3 pot, raise
   strong hands, some semi-bluffs.
5. **Last 5 min: test** against the baselines with `--strict-time`.
