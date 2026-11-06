"""The big blind ante is dead money (final tables): it goes into the pot, nobody matches
it, and the big blind doesn't get it back when they lose."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cards import card  # noqa: E402
from engine8 import Game8  # noqa: E402

BOARD = [card(c) for c in ("3s", "8d", "9c", "Jh", "4c")]
AA = [card("As"), card("Ad")]
KQ = [card("Kh"), card("Qh")]
X72 = [card("7c"), card("2d")]
ANTES = [0, 0, 100]          # (seats: UTG, SB, BB; 1 bb = 100)


def settle(holes, committed, folded=(False, True, False), antes=ANTES):
    return Game8._settle(holes, BOARD, list(committed), list(folded), antes)


def test_bb_loses_the_ante_too():
    # UTG raises to 2, the BB calls (1 ante + 2) and loses: UTG takes all 5.5 bb
    assert settle([AA, X72, KQ], [200, 50, 300]) == [350, -50, -300]


def test_bb_wins_everything():
    assert settle([KQ, X72, AA], [200, 50, 300]) == [-200, -50, 250]


def test_jam_called_by_a_bigger_big_blind():
    # a 10 bb jam: the BB risks 10 more (its ante is already in, dead)
    assert settle([AA, X72, KQ], [1000, 50, 1100]) == [1150, -50, -1100]


def test_short_big_blind_side_pot():
    # the BB has 6 bb (1 ante, 5 live) against a 20 bb jam: it can win 5 + 5 + 0.5 + 1
    assert settle([KQ, X72, AA], [2000, 50, 600]) == [-500, -50, 550]


def test_everyone_antes_unchanged():
    # the everyone-antes format (the contest's): equal antes, as before
    out = Game8._settle([AA, X72, KQ], BOARD, [212, 62, 212], [False, True, False])
    assert out == [274, -62, -212]


def test_chips_conserved_in_played_final_table_hands():
    import random
    import match8
    rng = random.Random(5)
    for hand in range(60):
        bots = [match8.make("contender8", hand * 10 + k) for k in range(6)]
        g = Game8(bots, big_blind=100, ante=100, ante_mode="bb", time_limit_s=1e9)
        stacks = [rng.randint(300, 6000) for _ in range(6)]
        from engine import new_deck
        res = g.play_hand(new_deck(rng), button=hand % 6, hand_no=hand, stacks=stacks)
        assert abs(sum(res)) < 1e-6
        assert all(s + r >= 0 for s, r in zip(stacks, res))
