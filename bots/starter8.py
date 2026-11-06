"""Simple 8-max baseline: Chen-score preflop with thresholds by position, the starter
bot's postflop play. A sensible but beatable opponent for testing."""

from bots import starter
from bots.starter import chen

OPEN_MIN = {"UTG": 9, "UTG1": 8.5, "LJ": 8, "HJ": 7, "CO": 6, "BTN": 5, "SB": 5.5, "BB": 99}


class Bot(starter.Bot):
    def preflop(self, s, hole, bb):
        score = chen(hole)
        raises = sum(1 for st, _, a, _ in s.history if st == "preflop" and a == "raise")
        if raises == 0:
            if s.to_call == 0:  # big blind, limped or folded to
                return self.raise_to(s, 4 * bb) if score >= 9 else ("check", 0)
            if score >= OPEN_MIN.get(s.position, 7):
                return self.raise_to(s, 2.5 * bb)
            return "fold", 0
        if raises == 1:
            if score >= 12:
                return self.raise_to(s, 3 * s.opp_bet)
            return ("call", 0) if score >= 9 else ("fold", 0)
        if score >= 14:
            return self.raise_to(s, 2.3 * s.opp_bet)
        return ("call", 0) if score >= 11 else ("fold", 0)
