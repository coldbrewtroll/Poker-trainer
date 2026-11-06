"""python -m pytest tests -q"""

import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cards import card, category, evaluate  # noqa: E402
from engine import Game, new_deck  # noqa: E402
from bots.baselines import CallingStation, Maniac, RandomBot  # noqa: E402
from bots.starter import Bot as Starter, chen  # noqa: E402
import match  # noqa: E402


def slow_eval5(cards):
    """Obviously-correct 5-card evaluator used to check the fast one."""
    ranks = sorted((c >> 2 for c in cards), reverse=True)
    flush = len({c & 3 for c in cards}) == 1
    uniq = sorted(set(ranks), reverse=True)
    straight = -1
    if len(uniq) == 5 and uniq[0] - uniq[4] == 4:
        straight = uniq[0]
    if uniq == [12, 3, 2, 1, 0]:
        straight = 3
    groups = sorted(((ranks.count(r), r) for r in uniq), reverse=True)
    shape = [g[0] for g in groups]
    order = [g[1] for g in groups]
    if straight >= 0 and flush:
        return (8, [straight])
    if shape == [4, 1]:
        return (7, order)
    if shape == [3, 2]:
        return (6, order)
    if flush:
        return (5, ranks)
    if straight >= 0:
        return (4, [straight])
    if shape == [3, 1, 1]:
        return (3, order)
    if shape == [2, 2, 1]:
        return (2, order)
    if shape == [2, 1, 1, 1]:
        return (1, order)
    return (0, ranks)


def slow_eval7(cards):
    return max(slow_eval5(c) for c in itertools.combinations(cards, 5))


def test_evaluator_matches_brute_force():
    rng = random.Random(1)
    for _ in range(3000):
        a = rng.sample(range(52), 9)
        board = a[4:]
        h1, h2 = a[:2] + board, a[2:4] + board
        fast = (evaluate(h1) > evaluate(h2)) - (evaluate(h1) < evaluate(h2))
        slow = (slow_eval7(h1) > slow_eval7(h2)) - (slow_eval7(h1) < slow_eval7(h2))
        assert fast == slow, ([c for c in h1], [c for c in h2])
        assert category(evaluate(h1)) == slow_eval7(h1)[0]


def test_known_hands():
    def ev(s):
        return evaluate([card(x) for x in s.split()])
    assert category(ev("As Ks Qs Js Ts 2c 3d")) == 8
    assert category(ev("5h 4h 3h 2h Ah Kc Kd")) == 8      # steel wheel
    assert ev("Ac 2d 3h 4s 5c Kd 9h") < ev("2c 3d 4h 5s 6c Kd 9h")  # wheel < six-high
    assert category(ev("Kc Kd Kh 2s 2c 9d 9h")) == 6       # trips + two pairs
    assert ev("Kc Kd Kh 2s 2c 9d 9h") > ev("Kc Kd Kh 2s 2c 8d 3h")
    assert ev("Ac Ad Kh Ks Qc Qd 2h") > ev("Ac Ad Kh Ks Jc Jd 2h")  # kicker from third pair
    assert chen([card("As"), card("Ad")]) == 20
    assert chen([card("7c"), card("2d")]) < 1


def test_chips_conserved_and_bots_never_corrected():
    rng = random.Random(3)
    for opp in (CallingStation(), Maniac(), RandomBot(seed=4)):
        bot = Starter(seed=5)
        game = Game([bot, opp])
        for i in range(400):
            r = game.play_hand(new_deck(rng), button=i % 2)
            assert abs(r[0] + r[1]) < 1e-9 and abs(r[0]) <= 200
        st = game.stats_for(bot)
        assert st.corrected == 0 and st.errors == 0
        assert st.slow <= max(1, st.decisions // 1000), f"{st.slow} moves over 1 ms"


def test_duplicate_gives_both_bots_identical_hands():
    """Identical bots must break exactly even in duplicate."""
    per_pair, _ = match.run(CallingStation(), CallingStation(), 400, seed=7)
    assert all(abs(x) < 1e-9 for x in per_pair)


def test_starter_beats_baselines():
    for opp in (CallingStation(), Maniac(), RandomBot(seed=9)):
        per_pair, _ = match.run(Starter(seed=8), opp, 1000, seed=11)
        assert sum(per_pair) > 0, type(opp).__name__
