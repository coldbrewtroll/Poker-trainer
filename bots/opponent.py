"""Opponent model: how often this opponent opens, re-raises and bets, learned from the
hands played so far. Each frequency starts from a typical value (the prior) and moves
toward what the opponent actually does as hands accumulate."""


class Frequency:
    def __init__(self, prior, weight=12):
        self.hits = prior * weight
        self.n = weight

    def add(self, hit):
        self.hits += 1.0 if hit else 0.0
        self.n += 1

    @property
    def value(self):
        return self.hits / self.n


class OpponentModel:
    def __init__(self):
        self.open = Frequency(0.80)        # raises when first to act preflop
        self.three_bet = Frequency(0.12)   # re-raises our open
        self.reraise = Frequency(0.10)     # re-raises any preflop raise (3/4/5-bets)
        self.aggression = Frequency(0.35)  # bets/raises per postflop decision
        self.calls_allin = Frequency(0.45)  # calls when we're all-in preflop
        self.folds_to_bet = Frequency(0.40)  # folds when we bet/raise after the flop

    def learn(self, history, i_was_button, my_stack=None):
        """history: (street, seat, action, amount) with seat 0 = us, 1 = them.
        my_stack: our chips at the start of the hand (to recognise our all-ins)."""
        pre = [(seat, a, amt) for st, seat, a, amt in history if st == "preflop"]
        prev = None
        for k, (seat, a, amt) in enumerate(pre):
            if seat == 1:
                if k == 0 and not i_was_button:
                    self.open.add(a == "raise")
                if prev and prev[:2] == (0, "raise"):
                    if my_stack and prev[2] >= my_stack:
                        self.calls_allin.add(a == "call")
                    else:
                        self.reraise.add(a == "raise")
                        if k == 1 and i_was_button:
                            self.three_bet.add(a == "raise")
            prev = (seat, a, amt)
        prev = None
        for st, seat, a, _ in history:
            if st != "preflop" and seat == 1:
                self.aggression.add(a == "raise")
                if prev == (st, 0, "raise"):
                    self.folds_to_bet.add(a == "fold")
            prev = (st, seat, a)
