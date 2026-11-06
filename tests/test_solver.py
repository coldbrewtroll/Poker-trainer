"""Tests for the push/fold solver, range importer and the bots that use them."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match  # noqa: E402
from bots import ranges, shortstack, starter  # noqa: E402
from engine import Game, new_deck  # noqa: E402
from solver import pushfold  # noqa: E402
from solver.import_ranges import parse  # noqa: E402


def test_pushfold_matches_known_equilibrium():
    names, E, W = pushfold.load()
    combos = W.sum(1) / W.sum()
    p, q, expl = pushfold.solve(E, W, 10.0, iters=2000)
    # Published heads-up Nash at 10 bb: SB pushes ~58%, BB calls ~37%.
    assert 0.55 < (p * combos).sum() < 0.61
    assert 0.34 < (q * combos).sum() < 0.40
    assert expl < 0.002
    i = {n: k for k, n in enumerate(names)}
    assert p[i["AA"]] > 0.99 and p[i["72o"]] < 0.01 and q[i["K2o"]] < 0.5


def test_parse_formats():
    assert parse("AA:1,AKo:0.75,AQs:0.5") == {"AA": 1, "AKo": 0.75, "AQs": 0.5}
    assert parse("AA:100,KK:50") == {"AA": 1, "KK": 0.5}
    assert parse("[50]AA,KK[/50],QQ") == {"AA": 0.5, "KK": 0.5, "QQ": 1}
    assert parse("AQo\n1.0;0.31\n76s\n0.25;-0.01\n") == {"AQo": 1, "76s": 0.25}
    assert parse("AhKh:1,AsKs:1,AdKd:0,AcKc:0") == {"AKs": 0.5}
    assert parse("AK") == {"AKs": 1, "AKo": 1}
    # MonkerViewer export: single combos with @percent
    assert parse("Th9h@100,Ts9s@100,Td9d@0,Tc9c@0,AA@50") == {"T9s": 0.5, "AA": 0.5}


def test_range_bot_follows_table():
    bot = ranges.Bot(seed=1)
    bot.table = {"": {0: {"raise_3bb": {"AA": 1}, "call": {"72o": 1}}},
                 "r": {0: {"call": {"KK": 1}, "allin": {"AA": 1}}}}
    game = Game([bot, starter.Bot(seed=2)], stack=400)  # 200 bb
    rng = random.Random(3)
    for i in range(3000):
        game.play_hand(new_deck(rng), button=i % 2, hand_no=i)
    st = game.stats_for(bot)
    assert st.corrected == 0 and st.errors == 0


def test_lookup_blends_depths():
    t = {"": {20: {"raise": {"AA": 1.0}}, 40: {"raise": {"AA": 0.0}, "allin": {"AA": 1.0}},
              100: {"raise": {"AA": 0.5}}}}
    assert ranges.mix(ranges.lookup("", 20, t), "AA") == {"raise": 1.0}
    m = ranges.mix(ranges.lookup("", 30, t), "AA")  # halfway between 20 and 40
    assert abs(m["raise"] - 0.5) < 1e-9 and abs(m["allin"] - 0.5) < 1e-9
    assert ranges.lookup("", 70, t) is None          # 40 -> 100 gap too wide, not near either
    assert ranges.mix(ranges.lookup("", 110, t), "AA") == {"raise": 0.5}  # within 20% of 100
    assert ranges.lookup("", 10, t) is None


def test_lines_and_actions():
    from bots.lines import current_line, parse_action, parse_line

    class S:
        def __init__(self, acts, opp_stack=100):
            self.history = [("preflop", 0, a, 0) for a in acts]
            self.opp_stack = opp_stack
    assert current_line(S([])) == ""
    assert current_line(S(["raise"])) == "r"
    assert current_line(S(["raise"], opp_stack=0)) == "a"
    assert current_line(S(["raise", "raise", "raise"], opp_stack=0)) == "rra"
    assert current_line(S(["call", "raise"])) == "cr"
    assert parse_line("bb_vs_4bet") == "rrr" and parse_line("line_crr") == "crr"
    assert parse_line("line_rar") is None and parse_line("nonsense") is None
    assert parse_action("Raise_2.5bb") == "raise_2.5bb" and parse_action("check") == "call"
    assert parse_action("call_3x") is None


def test_shortstack_beats_starter_when_short():
    per_pair, stats = match.run(shortstack.Bot(seed=4), starter.Bot(seed=5), 6000, seed=6,
                                stack=20)  # 10 bb
    assert sum(per_pair) > 0
    # Rare slow moves come from the OS pausing the process; demand they stay rare.
    assert stats[0].corrected == 0 and stats[0].slow <= stats[0].decisions / 1000
