"""Starter bot + solved push/fold charts: when the effective stack is short, it plays
the heads-up push/fold equilibrium preflop (all-in or fold, call or fold); otherwise it
plays exactly like the starter bot. Table lookup, so still microseconds per move."""

from bots import starter
from bots.pushfold_table import CALL, MAX_DEPTH, PUSH
from cards import RANKS, card

# bb. Measured against the starter bot (20k duplicate hands each): +15 bb/100 at 5 bb,
# +7 at 8 bb, +3 at 10 bb, break-even at 12 bb, -6 at 15 bb. Deeper than ~10 bb, smaller
# raises with postflop play beat shoving.
SHORT = 10.0


def hand_type(c1: int, c2: int) -> str:
    r1, r2 = c1 >> 2, c2 >> 2
    if r1 < r2:
        r1, r2 = r2, r1
    if r1 == r2:
        return RANKS[r1] * 2
    return RANKS[r1] + RANKS[r2] + ("s" if (c1 & 3) == (c2 & 3) else "o")


class Bot(starter.Bot):
    def act(self, s):
        if s.street == "preflop":
            bb = s.big_blind
            eff = min(s.my_stack + s.my_bet, s.opp_stack + s.opp_bet) / bb
            if eff <= min(SHORT, MAX_DEPTH):
                ht = hand_type(*(card(c) for c in s.hole))
                if s.is_button and not s.history:  # first to act: blinds only
                    if PUSH[ht] >= eff:
                        return "raise", s.max_raise_to
                    return "fold", 0
                if s.to_call and s.opp_stack == 0:  # facing an all-in
                    return ("call", 0) if CALL[ht] >= eff else ("fold", 0)
        return super().act(s)
