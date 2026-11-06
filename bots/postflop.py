"""Postflop play from simulated equity, inside a fixed time budget.

equity(): deals random opponent hands (from the range their preflop action suggests) and
random remaining board cards, and counts how often we win, for as long as the budget
allows (0.15 ms: roughly 10-35 samples). Decisions compare that equity with pot odds, with a
safety margin that grows with how much the opponent has bet, and a stricter bar before
committing most of a deep stack.
"""

import random
import time

from bots.preflop_data import TOP
from bots.shortstack import hand_type
from cards import card, evaluate

# All 1326 two-card combos, strongest first (by preflop strength), for range sampling.
_ALL = sorted(((a, b) for a in range(52) for b in range(a + 1, 52)),
              key=lambda ab: TOP[hand_type(*ab)])
BUDGET_S = 0.00015  # measured: as strong as 0.25 ms, fewer moves caught by OS pauses


def _connects(a, b, board_ranks, top_rank, suit_counts, level):
    """Quick check that an opponent hand fits their betting: level 1 = any pair or a
    flush draw; level 2+ = top pair or better (overpair, two pair, set)."""
    ra, rb = a >> 2, b >> 2
    if level >= 2:
        if ra == rb:
            return ra > top_rank or ra in board_ranks
        return (ra == top_rank or rb == top_rank or
                (ra in board_ranks and rb in board_ranks))
    if ra == rb or ra in board_ranks or rb in board_ranks:
        return True
    return (a & 3) == (b & 3) and suit_counts[a & 3] >= 2


def equity(hole, board, opp_share, rng, level=0, budget_s=BUDGET_S, min_samples=12,
           max_samples=300):
    """Share of pots won (ties half) against a hand from the top `opp_share` of hands
    that also fits `level` bets/raises after the flop (with some bluffs left in)."""
    dead = set(hole) | set(board)
    deck = [c for c in range(52) if c not in dead]
    n_range = max(20, int(len(_ALL) * opp_share))
    # Narrow ranges: list the combos still possible once, instead of drawing and
    # throwing away ones that clash with our cards (which can take many tries).
    live = None
    if n_range <= 300:
        live = [ab for ab in _ALL[:n_range] if ab[0] not in dead and ab[1] not in dead]
        if not live:
            live = [ab for ab in _ALL if ab[0] not in dead and ab[1] not in dead]
    need = 5 - len(board)
    my_fixed = hole + board
    board_ranks = {c >> 2 for c in board}
    top_rank = max(board_ranks)
    suit_counts = [0, 0, 0, 0]
    for c in board:
        suit_counts[c & 3] += 1
    bluff_keep = 0.3 if level == 1 else 0.15
    mine_river = evaluate(my_fixed) if not need else 0
    wins = 0.0
    n = rejected = 0
    deadline = time.perf_counter() + budget_s
    while n < max_samples:
        if live:
            a, b = live[rng.randrange(len(live))]
        else:
            a, b = _ALL[rng.randrange(n_range)]
            if a in dead or b in dead:
                continue
        # Filter by how they bet; stop filtering only if almost nothing fits (so the loop
        # can't stall), not after a fixed number of tries.
        if level and rejected < 30 * (n + 1) and not _connects(a, b, board_ranks, top_rank,
                                                                suit_counts, level):
            if rng.random() > bluff_keep:
                rejected += 1
                continue
        if need:
            run = []
            while len(run) < need:
                c = deck[rng.randrange(len(deck))]
                if c != a and c != b and c not in run:
                    run.append(c)
            mine = evaluate(my_fixed + run)
            theirs = evaluate([a, b] + board + run)
        else:
            mine = mine_river
            theirs = evaluate([a, b] + board)
        wins += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
        n += 1
        if n >= min_samples and n % 4 == 0 and time.perf_counter() > deadline:
            break
    return wins / n


def opp_preflop_share(s):
    """Rough share of hands the opponent still holds, from how preflop went."""
    pre = [(seat, a) for st, seat, a, _ in s.history if st == "preflop"]
    raises = [seat for seat, a in pre if a == "raise"]
    if not raises:
        return 1.0 if s.is_button else 0.85  # limped pot
    last = raises[-1]
    n = len(raises)
    if last == 1:  # opponent made the last raise
        return {1: 0.8 if not s.is_button else 0.35, 2: 0.12, 3: 0.045}.get(n, 0.025)
    return {1: 0.55, 2: 0.25, 3: 0.06}.get(n, 0.03)  # opponent called our raise


class PostflopMixin:
    """Mix into a bot that has self.rng and raise_to(); replaces its postflop().
    The thresholds are class attributes so experiments can tune them."""
    budget_s = BUDGET_S   # time for the equity simulation per decision
    min_samples = 8
    value_big = 0.80      # bet ~0.9 pot with at least this equity
    value_thin = 0.50     # bet ~0.6 pot with at least this (tuned: 0.62 -> 0.50)
    semi_bluff = 0.30     # how often to bet 38-55% equity on flop/turn
    river_bluff = 0.12    # how often to bluff the river with < 15% equity
    raise_eq = 0.85       # raise a bet with this much equity
    raise_eq_shallow = 0.75  # ... or this much when it doesn't commit the stack
    margin_base = 0.05    # extra equity required over pot odds to call (tuned)
    margin_slope = 0.10   # ... plus this per pot-sized bet faced (tuned)
    commit_eq = 0.55      # minimum equity to put in 40%+ of the stack

    def postflop(self, s, hole, board):
        share = opp_preflop_share(s)
        # Bets/raises the opponent made after the flop narrow their range to hands that
        # connect with the board - less so against an opponent who bets all the time.
        aggression = sum(1 for st, seat, a, _ in s.history
                         if st != "preflop" and seat == 1 and a == "raise")
        model = getattr(self, "opp", None)
        af = model.aggression.value if model else 0.35
        cap = 2 if af < 0.45 else 1 if af < 0.65 else 0
        if model and model.open.value > 0.9:
            share = max(share, model.open.value - 0.1)  # raises almost everything preflop
        eq = equity(hole, board, share, self.rng, level=min(aggression, cap),
                    budget_s=self.budget_s, min_samples=self.min_samples)
        pot = s.pot
        stack_total = s.my_stack + s.my_bet
        river = len(board) == 5
        # Bluff more against someone who folds a lot, less against one who never does;
        # and value-bet thinner against those who don't fold.
        folds = model.folds_to_bet.value if model else 0.40
        bluff_mult = min(2.0, max(0.0, (folds - 0.15) / 0.25))
        value_thin = self.value_thin - (0.06 if folds < 0.2 else 0.0)

        if s.to_call == 0:
            if eq >= self.value_big:
                return self.raise_to(s, s.my_bet + 0.9 * pot)
            if eq >= value_thin:
                return self.raise_to(s, s.my_bet + 0.6 * pot)
            if (not river and 0.38 <= eq < 0.55 and
                    self.rng.random() < self.semi_bluff * bluff_mult):
                return self.raise_to(s, s.my_bet + 0.5 * pot)
            if river and eq < 0.15 and self.rng.random() < self.river_bluff * bluff_mult:
                return self.raise_to(s, s.my_bet + 0.7 * pot)
            return "check", 0

        need = s.to_call / (pot + s.to_call)
        bet_frac = s.to_call / max(pot - s.to_call, 1)
        margin = self.margin_base + self.margin_slope * min(bet_frac, 2.0)
        committing = s.to_call > 0.4 * stack_total
        if eq >= self.raise_eq or (eq >= self.raise_eq_shallow and not committing):
            if s.min_raise_to:
                return self.raise_to(s, s.opp_bet + pot + s.to_call)
            return "call", 0
        if eq >= need + margin and (not committing or eq >= self.commit_eq):
            return "call", 0
        return "fold", 0
