"""Tests for the 8-max engine: positions, betting order, side pots, chip conservation."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bots.baselines import CallingStation, Maniac, RandomBot  # noqa: E402
from cards import card  # noqa: E402
from engine import new_deck  # noqa: E402
from engine8 import Game8, position_names  # noqa: E402


class Script:
    """Plays a fixed list of actions, recording what it saw."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.seen = []

    def act(self, s):
        self.seen.append(s)
        return self.actions.pop(0) if self.actions else (("check", 0) if not s.to_call
                                                         else ("fold", 0))


def deck_with(holes, board):
    """A deck giving seat i holes[i] and then the board."""
    cards = [card(c) for h in holes for c in h] + [card(c) for c in board]
    rest = [c for c in range(52) if c not in cards]
    return cards + rest


def test_position_names():
    assert position_names(8) == ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
    assert position_names(6) == ["LJ", "HJ", "CO", "BTN", "SB", "BB"]
    assert position_names(2) == ["BTN", "BB"]


def test_blinds_and_order_8max():
    bots = [Script([]) for _ in range(8)]
    game = Game8(bots, stack=200)
    game.play_hand(new_deck(random.Random(1)), button=3)
    s = bots[6].seen[0]  # button 3 -> SB 4, BB 5, UTG 6 acts first
    assert s.position == "UTG" and s.my_seat == 6 and s.history == []
    assert s.to_call == 2 and s.pot == 3
    positions = {p.seat: p.position for p in s.players}
    assert positions[3] == "BTN" and positions[4] == "SB" and positions[5] == "BB"


def test_side_pots():
    # Seat 0 short all-in with the best hand, seats 1 and 2 deeper; seat 1 beats seat 2.
    holes = [["Ah", "Ad"], ["Kh", "Kd"], ["Qh", "Qd"]]
    deck = deck_with(holes, ["2c", "7s", "9d", "3c", "4s"])
    bots = [CallingStation(), Maniac(), CallingStation()]
    game = Game8(bots, stack=200)
    r = game.play_hand(deck, button=0, stacks=[50, 200, 200])
    # Seat 0 wins the main pot (3 x 50 = 150, net +100); seat 1 wins everything above
    # 50 from seat 2, so seat 2 loses its whole 200 and seat 1 nets +100.
    assert r == [100, 100, -200]


def test_uncalled_bet_returned_and_split_pot():
    holes = [["Ah", "Kd"], ["As", "Kc"]]
    deck = deck_with(holes, ["2c", "7s", "9d", "3c", "4s"])  # same hand: split
    game = Game8([CallingStation(), CallingStation()], stack=100)
    assert game.play_hand(deck, button=0) == [0.0, 0.0]


def test_random_play_conserves_chips_and_stays_legal():
    rng = random.Random(2)
    for n in (2, 3, 6, 8):
        bots = [RandomBot(seed=k) if k % 3 else Maniac() for k in range(n)]
        game = Game8(bots, stack=200)
        for i in range(300):
            stacks = [rng.choice((20, 60, 200, 400)) for _ in range(n)]
            r = game.play_hand(new_deck(rng), button=i % n, stacks=stacks)
            assert abs(sum(r)) < 1e-6
            assert all(-stacks[s] - 1e-9 <= r[s] for s in range(n))


def test_spot_names_and_hu_aliases():
    from bots.lines8 import parse_spot
    assert parse_spot("btn_utgr_coc") == "BTN_UTGr_COc"
    assert parse_spot("bb_vs_raise") == "BB_SBr"
    assert parse_spot("sb_vs_3bet_allin") == "SB_SBr_BBa"
    assert parse_spot("XX_UTGr") is None and parse_spot("BTN_UTGq") is None


def test_current_spot_from_play():
    from bots.lines8 import current_spot
    bots = [Script([]) for _ in range(8)]
    bots[6].actions = [("raise", 5)]       # UTG opens
    bots[0].actions = [("call", 0)]        # LJ calls (seat 0 is LJ with button 3)
    game = Game8(bots, stack=200)
    game.play_hand(new_deck(random.Random(5)), button=3)
    spot_at_btn = current_spot(bots[3].seen[0])
    assert spot_at_btn == "BTN_UTGr_LJc"


def test_contender8_legal_in_many_fields():
    import match8
    from bots import contender8, starter8
    for field in ([Maniac() for _ in range(7)], [CallingStation() for _ in range(3)],
                  [RandomBot(seed=k) for k in range(5)] + [starter8.Bot(seed=9)],
                  [contender8.Bot(seed=k) for k in range(7)]):
        for stack in (16, 40, 200):
            hero = contender8.Bot(seed=1)
            per, st = match8.run8(hero, field, 40, seed=stack, stack=stack)
            assert st.corrected == 0 and st.errors == 0, (len(field), stack)


def test_big_blind_ante():
    bots = [Script([]) for _ in range(8)]
    game = Game8(bots, stack=200, ante=2, ante_mode="bb")
    r = game.play_hand(new_deck(random.Random(1)), button=3)
    s = bots[6].seen[0]
    assert s.pot == 3 + 2 and s.ante_total == 2 and s.to_call == 2
    # Everyone folds to the big blind, who wins blinds + ante back: SB loses 1.
    assert r[4] == -1 and r[5] == 1 and sum(r) == 0


def test_each_ante_and_short_stack_covering_ante():
    bots = [CallingStation() for _ in range(4)]
    game = Game8(bots, stack=200, ante=1, ante_mode="each")
    rng = random.Random(3)
    for i in range(200):
        r = game.play_hand(new_deck(rng), button=i % 4, stacks=[1, 3, 50, 200])
        assert abs(sum(r)) < 1e-6


class Jammer:
    """Opens to 2 bb, then goes all-in over any re-raise."""

    def act(self, s):
        if s.to_call == 0 or s.my_bet == 0 and s.to_call <= s.big_blind:
            return ("raise", 2 * s.big_blind) if s.min_raise_to else ("check", 0)
        return ("raise", s.max_raise_to) if s.min_raise_to else ("call", 0)


def small_3bet_hand(hero_hole, hero_seat):
    """20 bb, 8-max, button seat 5: the CO (seat 4) opens and jams over a 3-bet. Returns
    the hero's actions."""
    from bots import contender8

    hero = contender8.Bot(seed=3)
    hero.bluff_freq = 1.0
    seen = []
    act = hero.act

    def spy(s):
        move = act(s)
        seen.append((s.opp_bet, move))
        return move

    hero.act = spy
    bots = [Script([]) for _ in range(8)]
    bots[4] = Jammer()
    bots[hero_seat] = hero
    holes = [["2c", "3d"], ["2d", "3c"], ["2h", "3s"], ["2s", "3h"], ["9c", "8d"],
             ["4c", "7d"], ["4d", "7c"], ["4h", "7s"]]
    holes[hero_seat] = hero_hole
    game = Game8(bots, stack=160, big_blind=8, ante=1)
    hero.new_hand(0, hero_seat == 5)
    game.play_hand(deck_with(holes, ["Kd", "Qd", "Jc", "6s", "5h"]), button=5)
    return seen


def test_small_3bet_sizes_and_plans():
    # In position (button): 2.65x the 2 bb open = 5.3 bb; value calls the jam.
    seen = small_3bet_hand(["Ah", "As"], 5)
    assert seen[0][1] == ("raise", int(2.65 * 16))
    assert seen[1][1] == ("call", 0)
    # The bluff part (A5s) 3-bets the same size and folds to the jam.
    seen = small_3bet_hand(["Ah", "5h"], 5)
    assert seen[0][1] == ("raise", int(2.65 * 16))
    assert seen[1][1] == ("fold", 0)
    # Out of position (big blind): 3.2x.
    seen = small_3bet_hand(["Kh", "Ks"], 7)
    assert seen[0][1] == ("raise", int(3.2 * 16))
    assert seen[1][1] == ("call", 0)
    # A middling hand jams instead (no small 3-bet).
    seen = small_3bet_hand(["5s", "5c"], 5)
    assert seen[0][1][1] == 159 and len(seen) == 1  # all-in (1 chip went in the ante)


def test_models_learn_allins_with_antes_and_folds_to_reraises():
    from bots.opponent8 import Models
    m = Models()
    # Seat 0 jams 159 of 160 chips (1 went in the ante); seat 1 calls cold.
    m.learn({"my_seat": 2, "pids": [0, 1, 2], "stacks": [160, 160, 160], "antes": [1, 1, 1],
             "history": [("preflop", 0, "raise", 159), ("preflop", 1, "call", 0),
                         ("preflop", 2, "fold", 0)]})
    assert m.get(1).calls_jam_cold.n == 13 and m.get(1).calls_jam_cold.value > 0.12
    # Without antes reported, a raise of 97%+ of the stack still counts as all-in.
    m.learn({"my_seat": 2, "pids": [0, 1, 2], "stacks": [160, 160, 160],
             "history": [("preflop", 0, "raise", 158), ("preflop", 1, "fold", 0)]})
    assert m.get(1).calls_jam_cold.n == 14
    # Seat 0 opens, seat 1 3-bets, seat 0 folds: one fold to a re-raise.
    m.learn({"my_seat": 2, "pids": [0, 1, 2], "stacks": [800, 800, 800], "antes": [1, 1, 1],
             "history": [("preflop", 0, "raise", 16), ("preflop", 1, "raise", 48),
                         ("preflop", 2, "fold", 0), ("preflop", 0, "fold", 0)]})
    f = m.get(0).folds_to_reraise
    assert f.n == 13 and f.value > 0.55
