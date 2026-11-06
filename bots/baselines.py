"""Simple opponents to test against. A real bot should beat all of these clearly."""

import random


class CallingStation:
    """Never folds, never raises."""

    def act(self, s):
        return ("call", 0) if s.to_call else ("check", 0)


class RandomBot:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def act(self, s):
        r = self.rng.random()
        if s.min_raise_to and r < 0.3:
            return "raise", self.rng.randint(s.min_raise_to, s.max_raise_to)
        if s.to_call and r < 0.5:
            return "fold", 0
        return ("call", 0) if s.to_call else ("check", 0)


class Maniac:
    """Raises the pot every time it can."""

    def act(self, s):
        if s.min_raise_to:
            pot_raise = s.opp_bet + (s.pot + s.to_call)
            return "raise", min(max(pot_raise, s.min_raise_to), s.max_raise_to)
        return ("call", 0) if s.to_call else ("check", 0)
