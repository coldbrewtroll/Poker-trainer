"""Tests for the main contest bot: legal at every depth, learns opponents, uses sims."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match  # noqa: E402
from bots import contender  # noqa: E402
from bots.baselines import CallingStation, Maniac, RandomBot  # noqa: E402
from bots.opponent import OpponentModel  # noqa: E402
from bots.postflop import equity  # noqa: E402
from cards import card  # noqa: E402
from engine import Game, new_deck  # noqa: E402


def test_legal_and_fast_at_every_depth():
    rng = random.Random(1)
    decisions = slow = 0
    for stack in (6, 16, 24, 40, 100, 200, 300):
        for opp in (Maniac(), CallingStation(), RandomBot(seed=2), contender.Bot(seed=3)):
            bot = contender.Bot(seed=4)
            game = Game([bot, opp], stack=stack)
            for i in range(150):
                r = game.play_hand(new_deck(rng), button=i % 2, hand_no=i)
                assert abs(r[0] + r[1]) < 1e-9
            st = game.stats_for(bot)
            assert st.corrected == 0 and st.errors == 0, (stack, type(opp).__name__)
            assert st.total_s / st.decisions < 0.0004  # average well under the budget
            decisions += st.decisions
            slow += st.slow
    # The odd slow move is the machine pausing us (plain busywork goes over 1 ms about
    # 1 in 4000 here, in bursts), so judge it over the whole run, not one short match.
    assert slow <= max(10, decisions // 200), f"{slow} of {decisions} moves over 1 ms"


def test_model_learns_a_maniac():
    bot = contender.Bot(seed=5)
    game = Game([bot, Maniac()], stack=200)
    rng = random.Random(6)
    for i in range(300):
        game.play_hand(new_deck(rng), button=i % 2, hand_no=i)
    assert bot.opp.open.value > 0.9 and bot.opp.aggression.value > 0.8


def test_model_counts_allin_calls():
    m = OpponentModel()
    for _ in range(50):  # we shove from the button, they call
        m.learn([("preflop", 0, "raise", 20), ("preflop", 1, "call", 20)], True, my_stack=20)
    assert m.calls_allin.value > 0.85 and m.three_bet.n == 12  # not counted as a 3-bet chance


def test_equity_is_sensible():
    rng = random.Random(7)
    def eq(hole, board, share=1.0, level=0):
        return sum(equity([card(c) for c in hole], [card(c) for c in board], share, rng,
                          level=level, budget_s=0.002, min_samples=200) for _ in range(5)) / 5
    assert eq(["Ah", "Ad"], ["As", "7c", "2d"]) > 0.95          # top set
    assert eq(["7h", "2d"], ["Ks", "Qc", "Jd", "9h", "3s"]) < 0.15  # nothing on the river
    nut_draw = eq(["Ah", "Kh"], ["7h", "6h", "2c"])
    assert 0.5 < nut_draw < 0.75
    # Against a range that bet twice, a weak top pair is worth less.
    assert eq(["Ac", "5d"], ["As", "Jc", "8d"], level=2) < eq(["Ac", "5d"], ["As", "Jc", "8d"])


def test_contender_beats_previous_bots():
    for opp in ("shortstack", "contender.Classic"):
        per_pair, _ = match.run(contender.Bot(seed=8), match.load(opp, 9), 4000, seed=10,
                                stack=100)
        assert sum(per_pair) > 0, opp


def test_imported_tree_is_played_without_corrections():
    bot = contender.Bot(seed=11)
    everyone = {t: 1.0 for t in ("AA", "KK", "QQ", "AKs", "AKo", "72o", "T9s")}
    bot.table = {"": {100: {"raise_2.5bb": everyone}},
                 "r": {100: {"raise_3x": {"AA": 1, "KK": 1}, "call": {"T9s": 1, "AKo": 1}}},
                 "rr": {100: {"raise_2.2x": {"AA": 1}, "call": {"QQ": 1}}},
                 "rrr": {100: {"allin": {"AA": 1}, "call": {"KK": 0.5}}}}
    game = Game([bot, contender.Bot(seed=12)], stack=200)
    rng = random.Random(13)
    for i in range(1500):
        game.play_hand(new_deck(rng), button=i % 2, hand_no=i)
    st = game.stats_for(bot)
    assert st.corrected == 0 and st.errors == 0
