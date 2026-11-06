"""Starter bot: small enough to rewrite from memory in a 60-minute build, and every
decision takes microseconds.

Preflop: Chen formula score -> open / 3-bet / call / fold thresholds.
Postflop: a 0-1 hand strength from what the hole cards add to the board, plus draws,
compared against the price (pot odds) to decide check / bet / call / raise / fold.
"""

import gc
import random

from cards import card, category, evaluate, straight_high

CHEN_HIGH = {12: 10, 11: 8, 10: 7, 9: 6}  # A K Q J; others are (rank + 2) / 2


def chen(hole):
    """Bill Chen's preflop score: about -1 (72o) to 20 (AA)."""
    (r1, s1), (r2, s2) = sorted(((c >> 2, c & 3) for c in hole), reverse=True)
    score = CHEN_HIGH.get(r1, (r1 + 2) / 2)
    if r1 == r2:
        return max(score * 2, 5)
    if s1 == s2:
        score += 2
    gap = r1 - r2 - 1
    score -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and r1 < 10:  # both below queen and connected / one-gapped
        score += 1
    return score


def strength(hole, board):
    """0..1 estimate of how good the hand is on this board."""
    score = evaluate(hole + board)
    cat = category(score)
    bcat = board_category(board)
    if cat >= 4 and cat > bcat:
        s = 0.95          # straight or better that uses our cards
    elif cat == 3 and bcat < 3:
        s = 0.85          # set / trips
    elif cat == 2 and bcat == 0:
        s = 0.75          # two pair using both hole cards
    elif cat == 1 and bcat == 0:
        pair = score >> 12 & 15
        top = max(c >> 2 for c in board)
        s = 0.7 if pair > top else 0.6 if pair == top else 0.4  # over / top / lower pair
    elif cat > bcat:
        s = 0.5           # one pair on top of a paired board
    else:
        s = 0.15          # nothing beyond what the board shows
    if len(board) < 5:
        s += draw_bonus(hole, board)
    return min(s, 0.99)


def board_category(board):
    if len(board) == 5:
        return category(evaluate(board))
    counts = {}
    for c in board:
        counts[c >> 2] = counts.get(c >> 2, 0) + 1
    n = sorted(counts.values(), reverse=True)
    if n[0] >= 3:
        return 3 if n[0] == 3 else 7
    if n[0] == 2:
        return 2 if len(n) > 1 and n[1] == 2 else 1
    return 0


def draw_bonus(hole, board):
    """Flush draw +0.2, open-ended straight draw +0.15 (only if our cards are part of it)."""
    bonus = 0.0
    cards = hole + board
    for suit in range(4):
        if sum(c & 3 == suit for c in cards) == 4 and any(c & 3 == suit for c in hole):
            bonus += 0.2
    mask = hole_mask = 0
    for c in cards:
        mask |= 1 << (c >> 2)
    for c in hole:
        hole_mask |= 1 << (c >> 2)
    if straight_high(mask) < 0:
        for low in range(1, 9):  # 4 in a row with room on both ends (not A-2-3-4 / J-Q-K-A)
            run = 0b1111 << low
            if mask << 1 & run == run and hole_mask << 1 & run:
                bonus += 0.15
                break
    return bonus


class Bot:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        # Python's garbage collector can pause a move for a millisecond or more. Turn
        # automatic collection off and collect between hands instead, outside the clock.
        gc.disable()

    def new_hand(self, hand_no, is_button):
        # Between hands, off the clock: a quick young-object sweep every hand, a full
        # one now and then. If your contest's engine has no per-hand hook, do this when
        # a hand ends instead.
        self.hands = getattr(self, "hands", 0) + 1
        gc.collect(0 if self.hands % 500 else 2)

    def act(self, s):
        hole = [card(c) for c in s.hole]
        board = [card(c) for c in s.board]
        bb = s.big_blind
        if s.street == "preflop":
            return self.preflop(s, hole, bb)
        return self.postflop(s, hole, board)

    def raise_to(self, s, amount):
        if not s.min_raise_to:
            return ("call", 0) if s.to_call else ("check", 0)
        return "raise", int(min(max(amount, s.min_raise_to), s.max_raise_to))

    def preflop(self, s, hole, bb):
        score = chen(hole)
        facing_raise = s.opp_bet > bb
        if not facing_raise:
            if s.is_button:  # first in from the small blind: open wide
                if score >= 5:
                    return self.raise_to(s, 2.5 * bb)
                return ("call", 0) if score >= 3 else ("fold", 0)
            # big blind after a limp
            if score >= 8:
                return self.raise_to(s, 4 * bb)
            return "check", 0
        # facing a raise
        if score >= 12:
            return self.raise_to(s, 3 * s.opp_bet)
        if score >= 8 or (score >= 6 and s.to_call <= 2 * bb):
            return "call", 0
        return "fold", 0

    def postflop(self, s, hole, board):
        st = strength(hole, board)
        pot = s.pot
        if s.to_call == 0:
            if st >= 0.6:
                return self.raise_to(s, s.my_bet + 0.66 * pot)
            if st >= 0.3 and self.rng.random() < 0.35:  # semi-bluff draws sometimes
                return self.raise_to(s, s.my_bet + 0.5 * pot)
            return "check", 0
        price = s.to_call / (pot + s.to_call)
        if st >= 0.85:
            return self.raise_to(s, s.opp_bet + pot + s.to_call)
        if st >= price + 0.15:
            return "call", 0
        return "fold", 0
