Your own solver sims, one file per spot and action (see MONKER_GUIDE_8MAX.md).

The trainer reads this folder by itself. The contest bot only uses it after you import it:
    py -3.13 solver/import_ranges8.py my_ranges
(Tested at 17-23 bb: the 20 bb Monker sim made the contest bot 10-20 bb/100 worse against
simple bots than its built-in strategy, so it is not imported by default.)

20 bb, 8-max, BB ante: converted from a MonkerSolver export with
    py -3.13 solver/monker_import8.py 20bb.zip --depth 20

50 bb, 8-max, BB ante (a snapshot while still solving; re-export later and run
    py -3.13 solver/monker_import8.py 50bb.zip --depth 50
which replaces these 50 bb files).

30 bb, 8-max, BB ante (open 30% = about 2.05 bb, in-position 3-bet 46%, blinds 80%).

40 bb, 8-max, BB ante (open 29% = 2 bb, in-position 3-bet 51%, blinds 80%).

60 bb, 8-max, BB ante (same tree as 20/40 bb: open 29% = 2 bb, 3-bets 51% / 80%, 4-bets all-in only).

15 bb, 8-max, BB ante (same tree as 20 bb: open 29% = 2 bb or all-in, 3-bets 51% / 80% or
all-in; the BB raises 29% = 2 bb over an SB limp in this tree, the trainer plays it 3-4 bb).

All depths re-imported with solver/monker_import8.py --min-reach 1e-6 (the default now):
decisions the solve reaches less than once in a million hands (an all-in nobody makes at
that depth, small 3-bets at 20 bb where everyone jams, ...) are left out, as their
strategies were noise; the bots' own rules play those rare lines. _unreached.txt lists the
files that went: the trainer deletes them once, on its next start.

60 bb replaced (Nov 5) by your two-size tree (partly solved): opens 40% pot (2.4 bb) or 83%
(3.9 bb), the SB 40% (2.2 bb) / 83% (3.5 bb), the BB 100% (4 bb) over an SB limp, 3-bets 62%
/ 100%, 4-bets 50%. The trainer's raise buttons offer the sim's own sizes where it has them.
