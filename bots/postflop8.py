"""Multiway postflop play for 8-max: simulated equity against every opponent still in.

Each opponent gets a range from their preflop action (opener, 3-bettor, caller, ...),
narrowed by their own bets after the flop, like bots/postflop.py. A sample deals each of
them a hand plus the rest of the board; we win if we beat them all (ties split).
"""

import random
import os
import time

from bots.postflop import _ALL, _connects
from cards import evaluate

BUDGET_S = 0.00015
BUDGET_MULTIWAY_S = 0.00025  # more opponents = more work per sample


def _range_list(share, dead):
    """Combos to sample from: the top `share` of hands. Narrow ranges are filtered for
    dead cards up front; wide ones are returned as a slice of the full list (cheap) and
    clashes are rejected while sampling."""
    n_range = max(20, int(len(_ALL) * share))
    if n_range > 300:
        return _ALL[:n_range] if n_range < len(_ALL) else _ALL
    live = [ab for ab in _ALL[:n_range] if ab[0] not in dead and ab[1] not in dead]
    return live or [ab for ab in _ALL if ab[0] not in dead and ab[1] not in dead]


# For testing only: POKERBOT_FIXED_SAMPLES=n makes every equity estimate use exactly n
# samples instead of a time budget, so two runs of the same deal play identically.
FIXED_SAMPLES = int(os.environ.get("POKERBOT_FIXED_SAMPLES", "0") or 0)
SAMPLE_LOG = []  # filled with sample counts when POKERBOT_SAMPLE_LOG is set
_LOG = bool(os.environ.get("POKERBOT_SAMPLE_LOG"))


def equity_multi(hole, board, opps, rng, budget_s=BUDGET_S, min_samples=8,
                 max_samples=300):
    """opps: [(share, level)] per opponent. Returns our share of pots won."""
    dead = set(hole) | set(board)
    deck = [c for c in range(52) if c not in dead]
    lists = [_range_list(share, dead) for share, _ in opps]
    levels = [lvl for _, lvl in opps]
    need = 5 - len(board)
    my_fixed = hole + board
    board_ranks = {c >> 2 for c in board}
    top_rank = max(board_ranks)
    suit_counts = [0, 0, 0, 0]
    for c in board:
        suit_counts[c & 3] += 1
    mine_river = evaluate(my_fixed) if not need else 0
    wins = 0.0
    n = rejected = 0
    deadline = time.perf_counter() + budget_s
    if FIXED_SAMPLES:
        max_samples, deadline = FIXED_SAMPLES, float("inf")
    while n < max_samples:
        used = set()
        hands = []
        ok = True
        for lst, lvl in zip(lists, levels):
            for _ in range(20):
                a, b = lst[rng.randrange(len(lst))]
                if a in used or b in used or a in dead or b in dead:
                    continue
                if (lvl and rejected < 30 * (n + 1) and
                        not _connects(a, b, board_ranks, top_rank, suit_counts, lvl) and
                        rng.random() > (0.3 if lvl == 1 else 0.15)):
                    rejected += 1
                    continue
                break
            else:
                ok = False
                break
            used.add(a)
            used.add(b)
            hands.append((a, b))
        if not ok:
            rejected += 1
            if rejected > 2000:
                break
            continue
        if need:
            run = []
            while len(run) < need:
                c = deck[rng.randrange(len(deck))]
                if c not in used and c not in run:
                    run.append(c)
            mine = evaluate(my_fixed + run)
            best = max(evaluate([a, b] + board + run) for a, b in hands)
        else:
            mine = mine_river
            best = max(evaluate([a, b] + board) for a, b in hands)
        if mine > best:
            wins += 1.0
        elif mine == best:
            ties = 1 + sum(1 for a, b in hands
                           if evaluate([a, b] + board + (run if need else [])) == mine)
            wins += 1.0 / ties
        n += 1
        if n >= min_samples and n % 2 == 0 and time.perf_counter() > deadline:
            break
    if _LOG:
        SAMPLE_LOG.append(n)
    return wins / n if n else 1.0 / (len(opps) + 1)


def has_equity(hole, board):
    """Something worth continuing with against a bet: a pair or better that uses our cards
    (or a pocket pair), a flush draw, a straight draw (gutshot or open-ended), or two
    overcards on the flop. Draws don't count on the river."""
    hr = [c >> 2 for c in hole]
    br = [c >> 2 for c in board]
    if hr[0] == hr[1] or any(r in br for r in hr):
        return True
    if len(board) >= 5:
        return False
    suits = [c & 3 for c in hole + board]
    for st in range(4):
        if suits.count(st) >= 4 and any((c & 3) == st for c in hole):
            return True
    ranks = set(hr + br)
    if 12 in ranks:
        ranks.add(-1)  # an ace also plays low (wheel draws)
    mine = set(hr) | ({-1} if 12 in hr else set())
    for lo in range(-1, 9):
        window = set(range(lo, lo + 5))
        if len(window & ranks) >= 4 and window & mine:
            return True
    return len(board) == 3 and min(hr) > max(br)


class Postflop8Mixin:
    """Mix into a bot with self.rng, raise_to(), self.models and preflop_shares(s)."""
    min_samples = 8
    value_big = 0.80
    value_thin = 0.50
    semi_bluff = 0.30
    river_bluff = 0.12
    raise_eq = 0.85
    raise_eq_shallow = 0.75
    margin_base = 0.05
    margin_slope = 0.10
    small_bet_margin = 0   # 1: the fixed margin shrinks with the bet (half pot or more:
    #                        all of it), so tiny bets are called with the price they lay
    commit_eq = 0.55
    value_size = 0.85    # bet size (share of the pot) with strong hands
    thin_size = 0.6      # ... with thinner value
    delayed_cbet = 0.0   # >0: turn bet when the flop went check-check and we raised preflop
    barrel = 0.0         # >0: after a called flop c-bet, bet the turn again this often
    cbet_bluff = 0.0     # >0: flop c-bet (1/3 pot) as the preflop raiser, heads-up, this often
    need_equity = 0      # 1: facing a bet, fold hands with no pair, draw or overcards
    thin_adapt = 0.0     # >0: value-bet thinner against players who rarely fold to bets
    call_adapt = 0.0     # >0: call lighter vs aggressive players, tighter vs passive ones

    @staticmethod
    def _was_aggressor(s):
        """True if we made the last preflop raise and nobody has bet on the flop yet."""
        last = None
        for st, seat, a, _ in s.history:
            if st == "preflop" and a == "raise":
                last = seat
            elif st == "flop" and a == "raise":
                return False
        return last == s.my_seat

    @staticmethod
    def _delayed_spot(s):
        """Turn, flop checked through, we made the last preflop raise, no turn bet yet."""
        last = None
        for st, seat, a, _ in s.history:
            if st == "preflop" and a == "raise":
                last = seat
            elif st in ("flop", "turn") and a == "raise":
                return False
        flop = [a for st, _, a, _ in s.history if st == "flop"]
        return last == s.my_seat and flop and all(a == "check" for a in flop)

    @staticmethod
    def _barrel_spot(s):
        """True on the turn after our flop bet was called, before anyone bets the turn."""
        flop = [(seat, a) for st, seat, a, _ in s.history if st == "flop"]
        turn_bets = any(st == "turn" and a == "raise" for st, _, a, _ in s.history)
        bets = [seat for seat, a in flop if a == "raise"]
        return not turn_bets and bets == [s.my_seat] and flop[-1][1] == "call"

    def postflop(self, s, hole, board):
        opps = s.opponents
        shares = self.preflop_shares(s)
        specs = []
        for p in opps:
            bets = sum(1 for st, seat, a, _ in s.history
                       if st != "preflop" and seat == p.seat and a == "raise")
            af = self.models.get(p.pid).aggression.value
            cap = 2 if af < 0.45 else 1 if af < 0.65 else 0
            specs.append((shares.get(p.seat, 1.0), min(bets, cap)))
        k = len(specs)
        budget = BUDGET_S if k == 1 else BUDGET_MULTIWAY_S
        eq = equity_multi(hole, board, specs, self.rng, budget_s=budget,
                          min_samples=self.min_samples if k == 1 else 6)
        # Multiway, the same equity is worth more (fair share is 1/(k+1)), so scale the
        # betting thresholds down; bluff less into several players.
        f = 1.0 if k == 1 else 0.82 if k == 2 else 0.70
        bluff_k = 1.0 if k == 1 else 0.4 if k == 2 else 0.0
        folds = min((self.models.get(p.pid).folds_to_bet.value for p in opps), default=0.45)
        bluff_mult = min(2.0, max(0.0, (folds - 0.15) / 0.3)) * bluff_k
        pot = s.pot
        stack_total = s.my_stack + s.my_bet
        river = len(board) == 5

        if s.to_call == 0:
            if eq >= self.value_big * f:
                return self.raise_to(s, s.my_bet + self.value_size * pot)
            thin = self.value_thin - self.thin_adapt * max(0.0, 0.45 - folds)
            if eq >= thin * f:
                return self.raise_to(s, s.my_bet + self.thin_size * pot)
            if (not river and 0.38 * f <= eq < 0.55 * f and
                    self.rng.random() < self.semi_bluff * bluff_mult):
                return self.raise_to(s, s.my_bet + 0.5 * pot)
            if river and eq < 0.15 and self.rng.random() < self.river_bluff * bluff_mult:
                return self.raise_to(s, s.my_bet + 0.7 * pot)
            if (self.cbet_bluff and k == 1 and len(board) == 3 and self._was_aggressor(s) and
                    self.rng.random() < self.cbet_bluff * min(1.5, bluff_mult)):
                return self.raise_to(s, s.my_bet + 0.33 * pot)
            if (self.delayed_cbet and k == 1 and len(board) == 4 and self._delayed_spot(s) and
                    self.rng.random() < self.delayed_cbet * min(1.5, bluff_mult)):
                return self.raise_to(s, s.my_bet + 0.45 * pot)
            if (self.barrel and k == 1 and len(board) == 4 and self._barrel_spot(s) and
                    self.rng.random() < self.barrel * min(1.5, bluff_mult)):
                return self.raise_to(s, s.my_bet + 0.5 * pot)
            return "check", 0

        to_call = min(s.to_call, s.my_stack) if self.small_bet_margin else s.to_call
        need = to_call / (pot + to_call)   # (all-in for less: the price is what you put in)
        if (self.need_equity and eq < 0.55 * f and not has_equity(hole, board) and
                not (self.small_bet_margin and need <= 0.15)):
            return "fold", 0  # nothing to continue with: no pair, no draw, no overcards
            # (with small_bet_margin, a great price is called on equity alone)
        bet_frac = s.to_call / max(pot - s.to_call, 1)
        base = self.margin_base
        if self.small_bet_margin:
            base *= min(1.0, bet_frac / 0.5)
        margin = base + self.margin_slope * min(bet_frac, 2.0)
        if self.call_adapt:
            bettor = max(opps, key=lambda p: p.bet, default=None)
            if bettor is not None:
                af = self.models.get(bettor.pid).aggression.value
                margin -= self.call_adapt * (af - 0.30)
        committing = s.to_call > 0.4 * stack_total
        if self.small_bet_margin and need <= 0.2:
            committing = False   # what's left is small next to the pot: just the price
        if eq >= self.raise_eq * f or (eq >= self.raise_eq_shallow * f and not committing):
            if s.min_raise_to:
                return self.raise_to(s, s.opp_bet + pot + s.to_call)
            return "call", 0
        if eq >= need + margin and (not committing or eq >= self.commit_eq * f):
            return "call", 0
        return "fold", 0
