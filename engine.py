"""Heads-up No-Limit Hold'em engine for testing bots.

A bot is any object with act(state) -> (action, amount):
    ("fold", 0)  ("check", 0)  ("call", 0)  ("raise", raise_to)
raise_to is the bot's TOTAL bet on this street after raising (not the increment).
Optional hooks: new_hand(hand_no, is_button) and hand_over(result).

The button posts the small blind and acts first preflop; the big blind acts first after.
Illegal actions are corrected (check when facing a bet -> fold, raise below the minimum ->
minimum, etc.) and counted, so a buggy bot shows up in the report instead of crashing.
"""

import random
import time
from dataclasses import dataclass, field

from cards import evaluate, name

STREETS = ("preflop", "flop", "turn", "river")


@dataclass
class State:
    """Everything a bot may see when it's its turn."""
    hole: list            # e.g. ["As", "Kd"]
    board: list           # 0, 3, 4 or 5 cards
    street: str
    pot: int              # all chips committed this hand, including this street's bets
    to_call: int
    min_raise_to: int     # 0 when raising isn't possible
    max_raise_to: int     # all-in
    my_stack: int         # chips behind (not yet bet)
    opp_stack: int
    my_bet: int           # this street
    opp_bet: int
    is_button: bool
    big_blind: int
    history: list = field(default_factory=list)  # (street, seat, action, amount); seat 0 = you


@dataclass
class Stats:
    decisions: int = 0
    total_s: float = 0.0
    max_s: float = 0.0
    slow: int = 0          # decisions over the time limit
    corrected: int = 0     # illegal actions that had to be fixed
    errors: int = 0        # exceptions raised by the bot (treated as fold)


class Game:
    def __init__(self, bots, stack=200, big_blind=2, time_limit_s=0.001, strict_time=False):
        self.bots = bots
        self.stack = stack
        self.bb = big_blind
        self.sb = big_blind // 2
        self.time_limit = time_limit_s
        self.strict_time = strict_time  # if True, a slow decision is a fold
        self.stats = {}  # id(bot) -> Stats, so stats follow the bot when seats swap

    def play_hand(self, deck, button, hand_no=0):
        """Plays one hand with stacks reset. deck is a list of 52 card ints.
        Returns the chip result for seats 0 and 1."""
        stacks = [self.stack, self.stack]
        committed = [0, 0]
        holes = [[deck[0], deck[2]], [deck[1], deck[3]]]
        board_cards = deck[4:9]
        history = []
        folded = None
        for seat in (0, 1):
            hook = getattr(self.bots[seat], "new_hand", None)
            if hook:
                hook(hand_no, seat == button)

        bb_seat = 1 - button
        for street_i, street in enumerate(STREETS):
            board = board_cards[:(0, 3, 4, 5)[street_i]]
            bets = [0, 0]
            if street == "preflop":
                for seat, amt in ((button, self.sb), (bb_seat, self.bb)):
                    amt = min(amt, stacks[seat])
                    stacks[seat] -= amt
                    bets[seat] = amt
                first = button
            else:
                first = bb_seat
            if 0 in stacks and street != "preflop":
                committed = [committed[0] + bets[0], committed[1] + bets[1]]
                continue  # someone is all-in: just deal the rest
            folded = self._betting(street, board, holes, stacks, bets, committed, first,
                                   button, history)
            # Return an uncalled part of a bet.
            hi = 0 if bets[0] > bets[1] else 1
            extra = bets[hi] - bets[1 - hi]
            if extra and (folded is not None or stacks[1 - hi] == 0):
                bets[hi] -= extra
                stacks[hi] += extra
            committed = [committed[0] + bets[0], committed[1] + bets[1]]
            if folded is not None:
                break

        pot = committed[0] + committed[1]
        if folded is not None:
            winner = 1 - folded
            result = [0, 0]
            result[winner] = pot - committed[winner]
            result[folded] = -committed[folded]
            shown = None
        else:
            s0 = evaluate(holes[0] + board_cards)
            s1 = evaluate(holes[1] + board_cards)
            if s0 == s1:
                result = [pot / 2 - committed[0], pot / 2 - committed[1]]
            else:
                w = 0 if s0 > s1 else 1
                result = [0, 0]
                result[w] = pot - committed[w]
                result[1 - w] = -committed[1 - w]
            shown = [[name(c) for c in h] for h in holes]
        for seat in (0, 1):
            hook = getattr(self.bots[seat], "hand_over", None)
            if hook:
                hook({"result": result[seat], "board": [name(c) for c in board_cards],
                      "opp_cards": shown[1 - seat] if shown else None,
                      "history": self._view(history, seat)})
        assert abs(result[0] + result[1]) < 1e-9
        return result

    @staticmethod
    def _view(history, seat):
        return [(st, 0 if s == seat else 1, a, amt) for st, s, a, amt in history]

    def _betting(self, street, board, holes, stacks, bets, committed, first, button,
                 history):
        """Runs one betting round. Returns the seat that folded, or None."""
        acted = [False, False]
        last_raise = self.bb
        p = first
        while True:
            o = 1 - p
            if stacks[p] == 0:  # all-in players don't act
                acted[p] = True
            else:
                to_call = max(0, bets[o] - bets[p])
                max_to = bets[p] + stacks[p]
                can_raise = stacks[o] > 0 and max_to > bets[o]
                min_to = min(bets[o] + last_raise, max_to) if can_raise else 0
                state = State(
                    hole=[name(c) for c in holes[p]], board=[name(c) for c in board],
                    street=street, pot=sum(committed) + bets[0] + bets[1], to_call=to_call,
                    min_raise_to=min_to, max_raise_to=max_to if can_raise else 0,
                    my_stack=stacks[p], opp_stack=stacks[o], my_bet=bets[p], opp_bet=bets[o],
                    is_button=p == button, big_blind=self.bb,
                    history=self._view(history, p))
                action, amount = self._ask(p, state)

                if action == "raise" and not can_raise:
                    action = "call"
                    self.stats_for(self.bots[p]).corrected += 1
                if action == "check" and to_call > 0:
                    action = "fold"
                    self.stats_for(self.bots[p]).corrected += 1
                if action == "call" and to_call == 0:
                    action = "check"
                if action == "fold" and to_call == 0:
                    action = "check"  # folding for free is never right; treat as check
                if action == "fold":
                    history.append((street, p, "fold", 0))
                    return p
                if action == "check":
                    history.append((street, p, "check", 0))
                elif action == "call":
                    amt = min(to_call, stacks[p])
                    stacks[p] -= amt
                    bets[p] += amt
                    history.append((street, p, "call", bets[p]))
                else:  # raise
                    try:
                        raise_to = int(amount)
                    except (TypeError, ValueError):
                        raise_to = min_to
                        self.stats_for(self.bots[p]).corrected += 1
                    if raise_to < min_to or raise_to > max_to:
                        self.stats_for(self.bots[p]).corrected += 1
                        raise_to = min(max(raise_to, min_to), max_to)
                    last_raise = max(last_raise, raise_to - bets[o])
                    stacks[p] -= raise_to - bets[p]
                    bets[p] = raise_to
                    history.append((street, p, "raise", raise_to))
                    acted = [False, False]
                acted[p] = True

            if acted[0] and acted[1]:
                if bets[0] == bets[1] or stacks[0] == 0 or stacks[1] == 0:
                    return None
            if stacks[0] == 0 and stacks[1] == 0:
                return None
            p = o

    def stats_for(self, bot) -> Stats:
        return self.stats.setdefault(id(bot), Stats())

    def _ask(self, seat, state):
        st = self.stats_for(self.bots[seat])
        t0 = time.perf_counter()
        try:
            action, amount = self.bots[seat].act(state)
        except Exception:
            action, amount = "fold", 0
            st.errors += 1
        dt = time.perf_counter() - t0
        st.decisions += 1
        st.total_s += dt
        st.max_s = max(st.max_s, dt)
        if dt > self.time_limit:
            st.slow += 1
            if self.strict_time:
                return "fold", 0
        if action not in ("fold", "check", "call", "raise"):
            st.corrected += 1
            return "fold", 0
        return action, amount


def new_deck(rng: random.Random):
    deck = list(range(52))
    rng.shuffle(deck)
    return deck
