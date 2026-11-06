# Results

## Overnight tuning: current contender8 vs v2

Measured with `compare8.py`: both versions play exactly the same deals, seats and
opponents, with the random choices fixed per hand, so a difference only shows where they
decide differently. Two identical bots score exactly 0.0 ± 0.0. 500 deals per cell,
bb/100 that the current bot wins **more** than v2 at the same table, with a 95% range.

| Table | 10 bb | 15 bb | 25 bb | 40 bb | 60 bb | 100 bb |
|---|---|---|---|---|---|---|
| 7 copies of v2 | 0.0 | +3.8 ± 2.7 | +4.9 ± 3.0 | +7.2 ± 5.2 | +5.0 ± 5.1 | +5.0 ± 8.1 |
| 6-handed, v2 copies | 0.0 | +3.6 ± 3.1 | +3.3 ± 3.5 | +5.4 ± 5.9 | +7.3 ± 7.8 | +7.6 ± 12.4 |
| heads-up vs v2 | 0.0 | +6.6 ± 5.3 | +3.7 ± 4.2 | -10.0 ± 22.4 | +1.1 ± 7.2 | -0.1 ± 7.8 |
| v2 mix (v2, loose, tight, starters) | 0.0 | +3.4 ± 3.2 | +3.6 ± 4.8 | +4.4 ± 7.3 | +0.9 ± 6.1 | -1.6 ± 14.2 |
| 7 starter8 bots | +0.5 ± 0.9 | +16.4 ± 8.1 | +22.5 ± 9.8 | +19.9 ± 8.4 | +17.7 ± 8.7 | +11.5 ± 12.9 |
| tough | 0.0 | +4.4 ± 2.7 | +5.0 ± 5.0 | +2.5 ± 3.7 | +9.6 ± 5.4 | +0.1 ± 7.9 |

**Overall: +4.86 bb/100 (95% range +3.62 to +6.10).** At 10 bb both play the same
shove-or-fold strategy. (`contender8_v3` is the snapshot from before the river change;
it measured +4.56 on another set of deals.)

What made the difference (each kept only after winning on two separate sets of deals):
- **Adaptive steals** (+2.3): open wider when the players still to act fold to opens more
  than usual. It only ever widens: a version that also tightened against players who
  defend a lot lost heads-up and against good defenders.
- **Flop c-bet bluffs** (+1.3): as the preflop raiser, heads-up, bet a third of the pot
  with weak hands too, more often against players who fold to bets.
- **Turn barrels** (+0.9): after a called c-bet, bet half the pot again sometimes.
- **River bluffs** (+1.5): when checked to on the river with a hand that can't win, bet
  70% of the pot far more often, in proportion to how often that player folds to bets
  (no bluffs at all against calling stations: exactly 0.0 change against the weak mix).

- **Sane all-in ranges** (+1.3 vs a strict version, about level with the old one): the
  all-in EV assumed opponents put us on the range the maths found (e.g. the top 9% when
  re-jamming over a CO open at 25 bb) but then jammed every hand that came out positive
  under that assumption, which could be 70% of hands. Now it only jams hands within twice
  that range (the trainer's preflop chart showed it). Against opponents who call jams with
  any good hand, the old ranges would have lost a lot.

- **More solver-like short-stack ranges** (level: -0.3 ± 1.0 bb/100, kept because they
  match solved MTT ranges better): at 25 bb or less, small 3-bets for value only with
  JJ+, AKs, AQs (wider vs wide openers), bluffs with A5s, A8s-A6s, A4s, K9s, Q9s, A9s;
  medium pairs and offsuit big aces jam; flat calls weighted by hand shape (suited
  connectors play well, offsuit broadways and 22-33 badly); in position, suited hands and
  44-77 call unless the jam is clearly better.

- **Postflop: no continuing on air** (level: +0.2 ± 0.6 bb/100, kept): facing a bet, the
  bot folds unless it has a pair or better with its own cards, a flush draw, a straight
  draw, or (flop only) two overcards, or is clearly ahead anyway.
- **Big blind defence** (tested, contest bot unchanged): the old maths defended almost
  every hand in the big blind (even 72o vs UTG), because it gave junk the same share of
  its equity as good hands. A strength-dependent version (56% vs CO, 71% vs BTN, 34% vs
  UTG at 40 bb, close to solved ranges) costs 1-3 bb/100 against the test bots, which
  play badly after the flop. The trainer uses the solver-like version; the contest bot
  keeps the wide one (`blind_curve`).

Tried and not kept (within noise or worse, all measured the same way): about 40 setting
changes, including wider or narrower value 3-bets, value / thin bet sizes, delayed c-bets, more or fewer 3-bet / 4-bet / squeeze
bluffs, cold 4-bet width, re-shove depths, open sizes at every depth, equity
realisation by position, flatting small 3-bets in position (-1.5), thinner value bets
and lighter calls against aggressive players. The hand-tuned re-raise ranges were already
close to their best against these opponents.

## 8-max, online tournament settings (12.5% antes, no rake)

contender8 against different tables, 800 deals per cell, each replayed with contender8 in
every seat; "mixed" = every seat a random 10-100 bb stack each deal. bb/100 with a 95% range.

| Table | 15 bb | 25 bb | 50 bb | 100 bb | mixed |
|---|---|---|---|---|---|
| 7 starter8 bots | +54.8 ± 13.1 | +21.7 ± 10.9 | +11.3 ± 12.5 | +29.2 ± 16.2 | +16.2 ± 11.4 |
| weak mix (maniacs, stations, random, starters) | +86.0 ± 46.1 | +131.5 ± 68.7 | +291.2 ± 95.6 | +494.8 ± 171.4 | +149.9 ± 83.1 |
| tough (contender8 copies + loose and tight variants) | +5.3 ± 5.9 | -4.0 ± 7.8 | -1.7 ± 15.3 | +8.4 ± 19.3 | -1.5 ± 10.9 |
| 7 copies of itself | +1.0 ± 5.8 | -2.8 ± 7.5 | -8.6 ± 10.4 | -19.1 ± 17.2 | +0.8 ± 11.3 |
| 6-handed (mixed strong) | +4.5 ± 9.9 | +0.3 ± 13.6 | +15.5 ± 16.5 | +8.2 ± 22.1 | +16.2 ± 15.5 |
| 5-handed (mixed strong) | +11.5 ± 12.0 | +27.5 ± 15.2 | +15.9 ± 21.5 | +1.6 ± 25.3 | +1.9 ± 19.9 |
| heads-up vs itself | +1.4 ± 8.5 | +5.6 ± 10.9 | -6.7 ± 26.9 | +8.2 ± 38.4 | +4.5 ± 17.7 |

Against itself the true result is zero, so those rows show the noise (100 bb swings most).
The tough and 6-handed tables now hold the new contender8, so they're harder than before.
About 1 move in 4,000 went over 1 ms (machine pauses; an all-in decision with 7 players
behind thinks up to about 0.4 ms the first time a spot comes up, then it's cached).

### Re-raise rework (current) vs the previous version (`contender8_v1`)

Same tables for both, 600 deals per cell:

| Table | 15 bb | 25 bb | 50 bb | 100 bb | mixed |
|---|---|---|---|---|---|
| starters: new / old | +43.3 / +28.2 | +25.4 / +21.1 | +14.1 / +22.1 | +13.3 / +20.6 | +28.4 / +21.7 |
| tough: new / old | +0.5 / -7.0 | +1.6 / -10.7 | +4.4 / +9.3 | +4.3 / -3.6 | -1.8 / +0.5 |
| 6-handed: new / old | +14.8 / +12.2 | +12.6 / -9.3 | -3.8 / -3.1 | -16.7 / -11.1 | +9.4 / -10.7 |
| heads-up vs the new one | 0 / -7.2 | 0 / -14.4 | 0 / -20.1 | 0 / -4.8 | 0 / -19.8 |

That was before the last fix (bluffing re-raises only as much as each opponent folds to
them), which took the starters cells at 50 / 100 bb / mixed to about +20 to +22 in tuning.
At one table of seven `contender8_v1` bots (after both got the opponent-model fix below),
the new one is level: +1.9, -1.3, +4.5, +0.8 bb/100 at 15 / 20 / 25 / 50 bb, all within noise.

What changed:
- **Every re-raise spot is polarized:** value hands plus blocker bluffs (A5s-A2s, suited
  kings, suited connectors), at a bluff share that depends on position and on how often
  that opponent folds to re-raises (no bluffs against players who never fold).
- **25 bb or less:** small 3-bets (2.65x in position, 3.2x out of position). The value part
  calls a jam, the bluff part folds to one; middling hands re-jam, call or fold by EV.
- **All-ins priced with fold equity**, against the range we'd really shove (not a fixed
  guess), using each opponent's real all-in call rate.
- **Opponent-model fix:** with antes, an all-in is the stack minus the ante, so all-ins
  weren't being recognised and "calls all-ins" never learned. The rate is now also split
  by whether the player already had chips in (an opener facing a re-jam calls about 90%
  of the time) or not (blinds, cold players: far less).

Tuning sweeps (tough / 6-handed / starters; differences of 1-2 bb/100 are noise): a wider
value 3-bet range (0.25-0.45 of the opener's range) helped against starters but lost more
in the tough fields, so it stays 0.17; bluffing more with small 3-bets (1.0 bluff combo
per value combo in position) was worse (-3.4), and less (0.4) about the same.

## Heads-up

All numbers are big blinds won per 100 hands (bb/100) in **duplicate** matches (each deck
played twice with seats swapped, so card luck mostly cancels), with a 95% range.

## Contender against the pool (10,000 hands per cell)

| Opponent | 10 bb | 20 bb | 50 bb | 100 bb |
|---|---|---|---|---|
| CallingStation | +95.4 ± 8.2 | +294.0 ± 12.4 | +551.6 ± 25.3 | +648.1 ± 30.3 |
| Maniac | +38.8 ± 9.5 | +40.6 ± 19.5 | +441.2 ± 46.3 | +375.8 ± 91.9 |
| RandomBot | +64.3 ± 8.4 | +59.2 ± 11.9 | +175.3 ± 25.1 | +335.1 ± 46.1 |
| starter | +41.1 ± 5.5 | +36.8 ± 6.0 | +39.6 ± 9.3 | +34.0 ± 14.5 |
| shortstack | 0.0 (same play) | +27.3 ± 6.2 | +31.1 ± 9.7 | +34.0 ± 13.5 |
| contender.Classic | 0.0 (same play) | +15.4 ± 5.5 | +14.8 ± 9.2 | +26.0 ± 15.9 |
| contender (itself) | 0.0 | +8.1 ± 4.6 | +2.1 ± 8.5 | -3.3 ± 12.6 |

Against itself the results should be zero; the +8.1 at 20 bb is a statistical outlier
(earlier runs of the same cell gave +0.2, -2.7 and -1.4), expected now and then among 28
cells. At 10 bb the
three solved-chart bots play identically (and none of the opponents is unusual enough to
trigger the opponent-model adjustments), hence exactly 0.

## What each change was worth

| Change | Measured effect |
|---|---|
| Solved push/fold charts at short stacks | vs starter: +15 at 5 bb, +7 at 8 bb, +3 at 10 bb; break-even 12 bb, worse at 15 bb, so used at 10 bb or less |
| Built-in deep preflop (ranges by strength, calls priced by equity) | vs previous bot: +22 at 20 bb, +14 at 50 bb, +13 at 100 bb |
| Simulated postflop equity (vs rule of thumb) | +4.5 at 20 bb, +9.8 at 50 bb, +18.7 at 100 bb |
| Thinking budget 0.25 ms -> 0.15 ms | no measurable loss (+4.4 ± 9.5 head to head), fewer slow moves |
| Opponent model | vs Maniac: -99 -> +47 at 20 bb, -35 -> +358 at 50 bb |
| Tuning (thinner value bets, more equity needed vs big bets, wider 3-bets) | strong-pool average +9.3 -> about +19 to +21 |
| Short-stack exploits (shove-or-fold vs raises, adjust to clear tendencies) | vs Maniac at 5 bb: -9.9 -> +10.9; vs starter at 5 bb: +34 -> +63 |
| Folds-to-bet model (bluff more vs folders, value-bet thinner vs callers) | small gains across the pool (e.g. vs Classic at 50 bb +19 -> +23/+29), no losses |
| Bug fix: range narrowing switched off after 200 samples, so fast decisions ignored the opponent's bets | ace-weak-kicker vs two bets now 62% instead of 83%; re-sweep confirmed settings |

Tuning used `sweep.py` against a strong pool: starter, shortstack, Classic, contender, a
loose-aggressive and a tight variant of contender, at 20/50/100 bb with 8,000 hands per
pairing. Values were picked from the middle of the good region, not the single best
result, to avoid overfitting the pool.

## Caveats

- All opponents here are bots from this kit. Real contest bots will be different; the
  opponent model is there to adapt, but expect smaller margins against strong bots.
- Heads-up only, stacks reset every hand, blinds 1/2, no antes.
- Timing: average 0.1 ms per move; about 1 move in 2,500 went over 1 ms on this shared
  machine, the same rate as plain busywork with no poker code (machine pauses).


## Trainer rules fitted to your 20 bb Monker sim

Share of all 1326 hands whose main action differs from the reference, above what the
reference's own mixing makes unavoidable (lower is better), with the trainer's built-in
rules only (no sims loaded):

| Spots | before | after |
|---|---|---|
| BB vs an open, 20 bb (7 spots) | 23.4% | 10.9% |
| SB vs an open, 20 bb (6) | 12.1% | 7.9% |
| In position vs an open, 20 bb (15) | 8.2% | 6.7% |
| Opens incl. SB first in, 20 bb (7) | 14.5% | 5.0% |
| 25 bb GTO Wizard charts (6) | 3.2% | 3.9% |
| All 41 | 12.0% | 6.9% |

The contest bot's defaults are unchanged (all of this is the trainer's STUDY profile).


## Fix: calling all-ins multiway

Facing an all-in, the bot compared its hand with the shover's range only, while the pot
it was priced against held the money of everyone who had already called. So with two or
three callers in, it overcalled with almost anything (33, KJo). Now each caller counts too,
with a range tighter than the shover's, plus a little extra for invested players still to
act. Checked against GTO Wizard 20 bb (UTG1 opens, HJ calls, CO jams): the SB now calls
exactly 99+, AQs+, AKo like the solver. Paired test, fixed vs old contest bot (500 deals
per cell): +3.2 bb/100 overall, +11 bb/100 against the v2 field at 15 and 25 bb.


## Fix: jams over a squeeze

When we opened, someone called and a third player squeezed, the bot jammed most of its
opening range (86% of hands in the test spot; the 20 bb sim jams 3%). Two causes: the caller
of our open was assumed to hold any two cards (so would fold to a jam), and the jam EV
stopped at the first caller (the squeezer, with 9 bb in, was assumed to fold behind him).
Now the caller's range is sized from our open, and later players can overcall (half as
often per caller already in; the pot is then shared multiway). Same spot now: 3.8% jams.
At most three players to respond use the multiway form (0.12 ms); open shoves into a
full table keep the quick form. Paired test vs the previous contest bot: -0.6 bb/100
(95% range -2.0 to +0.8), i.e. no measurable change against the test fields, where
squeezes are rare; kept for removing a clear error.

Trainer profile: the BB overcalling an open plus a caller defends wider (caller_exp_bb),
and the SB that limped calls the BB's raise like the sim (67-79% mismatch before, ~10% now).
