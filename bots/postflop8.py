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
    # Out of position to the last aggressor (e.g. the big blind after calling a raise):
    # 1 = check to them almost always instead of leading (no donk bets with medium hands).
    # Leads left: strong hands donk_strong of the time (else check-raise), top pair with a
    # good kicker / an overpair donk_good, draws donk_draw, air donk_air. Off by default.
    donk_check = 0
    donk_strong = 0.6
    donk_good = 0.3
    donk_draw = 0.35
    donk_air = 0.03
    donk_texture = 0     # 1: lead only on flops that hit the caller's range (nine-high or
    #                      lower, unpaired, connected); everywhere else check it all
    # Facing the preflop raiser's flop c-bet after checking to them, heads-up:
    xr = 0               # 1: check-raise small (xr_small x the bet) or large (about pot)
    xr_value = 0.85      # ... with sets / two pair / trips this often (else call)
    xr_good = 0.12       # ... with an overpair / good top pair
    xr_draw = 0.35       # ... with a flush draw / open-ender / gutshot (a semi-bluff)
    xr_air = 0.05        # ... with no pair or draw but a backdoor flush draw (a bluff)
    xr_small = 3.0       # small check-raise: to this many times the bet
    xr_large_share = 0.5  # share of check-raises that are large (about pot)
    # The preflop raiser's flop c-bet, heads-up (1: on). Two sizes: big for value (top pair
    # or better), small with hands that are likely behind but have good turns and rivers
    # (draws, overcards, backdoors); medium pairs mostly check back for showdown.
    cbet_plan = 0
    cbet_big = 0.75      # big size (share of the pot)
    cbet_small = 0.33    # small size
    cb_strong = 0.9      # sets / two pair / trips: big this often (else check, a trap)
    cb_top = 0.8         # overpair / top pair: big this often
    cb_draw = 0.75       # flush draw / straight draw: small this often
    cb_equity = 0.6      # overcards or a backdoor flush draw: small this often
    cb_medium = 0.2      # middle / bottom pair, underpair: small this often (else check)
    cb_air = 0.3         # nothing at all: small this often
    # After our bet was called (flop c-bet, then turn barrel), heads-up, first to bet: the
    # next street by hand type and by what the new card did (1: on).
    barrel_plan = 0
    # Every other spot where we may bet first (probes, delayed c-bets, stabs, multiway and
    # limped pots), by hand type instead of raw equity against unnarrowed ranges (1: on).
    gen_plan = 0
    # River, last to act and checked to (1: on): top pair or better always bets for value,
    # at least half pot (river_top); second pair mixes small bets (river_second_small, at
    # river_second share) with checks; everything else follows the plans above.
    river_ip = 0
    # Facing a bet (1: on): top pair or better calls when the price (ICM included) is
    # cheap_need or better; a full house we hold calls any single bet up to a pot.
    cheap_call = 0
    cheap_need = 0.22
    cheap_any = 0.11       # ... and any pair (any draw before the river) at this price or better
    river_top = 0.55       # top pair, any kicker
    river_good = 0.65      # overpair / top pair with a good kicker
    river_strong = 0.75    # two pair, sets, straights, flushes, full houses
    river_second = 0.4     # second pair: bet this often...
    river_second_small = 0.33   # ... this size
    bp_big = 0.75        # big size (share of the pot)
    bp_small = 0.45      # small turn size
    bp_thin = 0.5        # river thin-value size
    bp_strong = 0.9      # sets / two pair / straights / flushes: big this often
    bp_top = 0.7         # top pair / overpair: big on the turn this often (blank turn)
    bp_top_bad = 0.35    # ... when the turn completes a flush or straight (smaller then)
    bp_draw = 0.6        # draws still live on the turn: small this often
    bp_medium = 0.12     # weaker pairs: small this often (else check)
    bp_scare = 0.45      # air on a scare turn (jack or higher overcard): small this often
    bp_blank = 0.12      # air on a blank turn
    rv_top_good = 0.55   # river: overpair / top pair with a good kicker, thin value
    rv_top_weak = 0.3    # river: top pair, weak kicker
    rv_bluff = 0.3       # river: missed draws / air, big bluff
    # Facing a turn / river bet after calling the last street, heads-up, from cont_min_bb
    # deep (1: on): top pair or better continues; flush draws / open-enders call up to about
    # pot, gutshots small bets; weaker pairs call small bets and catch bluffs vs big ones.
    cont_later = 0
    cl_small = 0.55      # a bet up to this share of the pot is "small"
    cl_draw_max = 1.05   # flush draws / open-enders call bets up to this share of the pot
    cl_medium_big = 0.4  # weaker pairs call a big turn bet this often
    cl_river_medium = 0.35  # weaker pairs call a river bet this often (bluff-catchers)
    # Prize money (a final table): icm_post(s) gives a PostICM (final_table.py) or None.
    # Calls are priced in prize money; bluffs shrink as our risk premium against them
    # outweighs theirs against us (gone at icm_bluff_scale) and grow (to 1.5x) when theirs
    # is bigger; thin value needs our premium more equity; the continue rules hold only
    # while ICM adds under icm_slack to the equity a call needs, or the hand still has it.
    icm_post = None
    icm_bluff_scale = 0.15
    icm_slack = 0.03
    _bf = 1.0            # (this decision's bluff factor)
    cont_rule = 0        # 1: never fold any pair, gutshot or better draw, or backdoor
    cont_min_bb = 15.0   #    flush draw to the c-bet, from this effective stack (bb)

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

    @staticmethod
    def _donk_spot(s):
        """True when we're first to bet this street and the last aggressor (whoever bet or
        raised last on the previous street) is still in and acts after us: leading here is
        a donk bet. (After a street checked through, betting is a probe: not this.)"""
        streets = ["preflop", "flop", "turn", "river"]
        cur = streets.index(s.street)
        if cur == 0 or s.to_call:
            return False
        prev = streets[cur - 1]
        agg = None
        for st, seat, a, _ in s.history:
            if st == prev and a == "raise":
                agg = seat
        if agg is None or agg == s.my_seat:
            return False
        p = next((p for p in s.players if p.seat == agg), None)
        if p is None or p.folded or p.all_in:
            return False
        acted = any(st == s.street and seat == agg for st, seat, _, _ in s.history)
        return not acted

    @staticmethod
    def _made_class(hole, board):
        """What we hold, by hand type: "strong" (set, trips, two pair with both cards),
        "good" (overpair, top pair with a jack-or-better kicker), "medium" (any other pair),
        "draw" (flush draw, open-ender or gutshot; not on the river) or "air"."""
        hr = sorted((c >> 2 for c in hole), reverse=True)
        br = [c >> 2 for c in board]
        top = max(br)
        trips = [r for r in set(br) if br.count(r) >= 3]
        if trips:      # (trips on the board: a pair of ours or pairing the board is a full house)
            pair = hr[0] if hr[0] == hr[1] else max((r for r in hr if r in br and r not in trips),
                                                    default=None)
            if any(r in trips for r in hr):
                return "strong"                  # quads
            if pair is not None:
                return "strong" if pair >= 6 else "good"
        if hr[0] == hr[1]:
            if hr[0] in br:
                return "strong"
            return "good" if hr[0] > top else "medium"
        hits = [r for r in hr if r in br]
        if len(hits) == 2:
            return "strong"
        if hits:
            r = hits[0]
            if br.count(r) >= 2:
                return "strong"            # trips
            kick = hr[1] if hr[0] == r else hr[0]
            return "good" if r == top and kick >= 9 else "medium"
        if len(board) >= 5:
            return "air"
        suits = [c & 3 for c in hole + board]
        for st in range(4):
            if suits.count(st) >= 4 and any((c & 3) == st for c in hole):
                return "draw"
        ranks = set(hr + br)
        if 12 in ranks:
            ranks.add(-1)
        mine = set(hr) | ({-1} if 12 in hr else set())
        for lo in range(-1, 9):
            window = set(range(lo, lo + 5))
            if len(window & ranks) >= 4 and window & mine:
                return "draw"
        return "air"

    @staticmethod
    def _caller_texture(board):
        """A flop that hits the preflop caller's range more than the raiser's: nine-high or
        lower, unpaired, all three ranks within five (e.g. 8-7-5, 6-4-3, 9-7-5)."""
        br = sorted({c >> 2 for c in board[:3]})
        return len(br) == 3 and br[-1] <= 7 and br[-1] - br[0] <= 4

    @staticmethod
    def _cbet_spot(s):
        """Flop, heads-up: we checked, the preflop raiser bet, and it's on us."""
        if s.street != "flop" or not s.to_call:
            return None
        live = [p for p in s.players if not p.folded]
        if len(live) != 2:
            return None
        agg = None
        for st, seat, a, _ in s.history:
            if st == "preflop" and a == "raise":
                agg = seat
        fl = [(seat, a) for st, seat, a, _ in s.history if st == "flop"]
        if agg is None or agg == s.my_seat or fl != [(s.my_seat, "check"), (agg, "raise")]:
            return None
        return next(p for p in s.players if p.seat == agg)

    @staticmethod
    def _continue_hand(hole, board):
        """Any pair (ours), any gutshot or better straight draw, any flush draw or backdoor
        flush draw (three of a suit with one of ours)."""
        hr = [c >> 2 for c in hole]
        br = [c >> 2 for c in board]
        if hr[0] == hr[1] or any(r in br for r in hr):
            return True
        suits = [c & 3 for c in hole + board]
        for st in range(4):
            if suits.count(st) >= 3 and any((c & 3) == st for c in hole):
                return True
        ranks = set(hr + br)
        if 12 in ranks:
            ranks.add(-1)
        mine = set(hr) | ({-1} if 12 in hr else set())
        for lo in range(-1, 9):
            window = set(range(lo, lo + 5))
            if len(window & ranks) >= 4 and window & mine:
                return True
        return False

    def _cbet_class(self, hole, board):
        """For the c-bet: "strong", "top" (overpair / top pair, any kicker), "medium" (a
        weaker pair), "draw", "equity" (two overcards or a backdoor flush draw) or "air"."""
        cls = self._made_class(hole, board)
        if cls in ("strong", "draw"):
            return cls
        hr = [c >> 2 for c in hole]
        top = max(c >> 2 for c in board)
        if cls == "good" or (hr[0] != hr[1] and top in hr):
            return "top"
        if cls == "medium":
            return "medium"
        if min(hr) > top or self._continue_hand(hole, board):
            return "equity"
        return "air"

    @staticmethod
    def _followup_spot(s):
        """Turn or river, heads-up: on the last street we bet and got called (no raise), and
        nobody has bet this street yet."""
        streets = ["preflop", "flop", "turn", "river"]
        cur = streets.index(s.street)
        if cur < 2 or s.to_call or len([p for p in s.players if not p.folded]) != 2:
            return False
        prev = [(seat, a) for st, seat, a, _ in s.history if st == streets[cur - 1]]
        bets = [seat for seat, a in prev if a == "raise"]
        return bets == [s.my_seat] and prev[-1][1] == "call"

    @staticmethod
    def _completes(hole, board):
        """(we have a flush or straight, the new card completed one we don't have)."""
        def flush(cards, need_hole):
            suits = [c & 3 for c in cards]
            for st in range(4):
                if suits.count(st) >= 5 and (not need_hole or any((c & 3) == st for c in hole)):
                    return True
            return False

        def straight(ranks, mine=None):
            rs = set(ranks)
            if 12 in rs:
                rs.add(-1)
            for lo in range(-1, 9):
                w = set(range(lo, lo + 5))
                if w <= rs and (mine is None or w & mine):
                    return True
            return False
        hr = [c >> 2 for c in hole]
        br = [c >> 2 for c in board]
        mine = set(hr) | ({-1} if 12 in hr else set())
        we = flush(hole + board, True) or straight(hr + br, mine)
        # (on the board: three of a suit, or four ranks inside a five-window)
        suits = [c & 3 for c in board]
        three = any(suits.count(st) >= 3 for st in range(4))
        rs = set(br) | ({-1} if 12 in br else set())
        four = any(len(set(range(lo, lo + 5)) & rs) >= 4 for lo in range(-1, 9))
        prev_suits = [c & 3 for c in board[:-1]]
        prs = set(br[:-1]) | ({-1} if 12 in br[:-1] else set())
        was3 = any(prev_suits.count(st) >= 3 for st in range(4))
        was4 = any(len(set(range(lo, lo + 5)) & prs) >= 4 for lo in range(-1, 9))
        return we, (three and not was3) or (four and not was4)

    def _barrel(self, s, hole, board, pot):
        """The turn / river plan (barrel_plan): a move, or None to fall through."""
        made, scary = self._completes(hole, board)
        cls = "strong" if made else self._cbet_class(hole, board)
        river = len(board) == 5
        new = board[-1] >> 2
        r = self.rng.random()
        bet = lambda frac: self.raise_to(s, s.my_bet + frac * pot)   # noqa: E731
        last = any(st == s.street and seat != s.my_seat for st, seat, _, _ in s.history)
        if cls == "strong":
            if river and last:
                return bet(self.bp_big)          # (checked to on the river: nothing to trap)
            return bet(self.bp_big) if r < self.bp_strong else ("check", 0)
        if cls == "top":
            if river:
                if scary:
                    return "check", 0
                good = self._made_class(hole, board) == "good"
                p = self.rv_top_good if good else self.rv_top_weak * self._semi()
                return bet(self.bp_thin) if r < p else ("check", 0)
            if scary:
                return bet(self.bp_small) if r < self.bp_top_bad else ("check", 0)
            return bet(self.bp_big) if r < self.bp_top else ("check", 0)
        if cls == "medium":
            if river:
                return "check", 0
            return bet(self.bp_small) if r < self.bp_medium * self._bf else ("check", 0)
        if river:          # missed draws and air: a big bluff now and then
            return bet(self.bp_big) if r < self.rv_bluff * self._bf else ("check", 0)
        if cls == "draw":
            return bet(self.bp_small) if r < self.bp_draw * self._semi() else ("check", 0)
        flop_top = max(c >> 2 for c in board[:3])
        scare = new > flop_top and new >= 9
        p = (self.bp_scare if scare else self.bp_blank) * self._bf
        return bet(self.bp_small) if r < p else ("check", 0)

    @staticmethod
    def _draw_kind(hole, board):
        """"flush" / "open" (open-ender or double gutter) / "gut" / None: what we draw to,
        with one of our cards (turn only: no draws on the river)."""
        if len(board) >= 5:
            return None
        suits = [c & 3 for c in hole + board]
        for st in range(4):
            if suits.count(st) == 4 and any((c & 3) == st for c in hole):
                return "flush"
        hr = [c >> 2 for c in hole]
        br = [c >> 2 for c in board]
        have = set(hr + br)
        mine = set(hr)

        def straights(rs):
            rs = set(rs) | ({-1} if 12 in rs else set())
            for lo in range(-1, 9):
                w = set(range(lo, lo + 5))
                if w <= rs:
                    yield w
        if any(True for _ in straights(have)):
            return None
        outs = 0
        for r in range(13):
            if r in have:
                continue
            if any(w & (mine | ({-1} if 12 in mine else set()))
                   for w in straights(have | {r})):
                outs += 1
        return "open" if outs >= 2 else "gut" if outs == 1 else None

    @staticmethod
    def _checked_to_last(s):
        """River-style "in position": everyone else still in has checked this street to us."""
        others = [p.seat for p in s.players if not p.folded and p.seat != s.my_seat]
        acts = {seat: a for st, seat, a, _ in s.history if st == s.street}
        return bool(others) and all(acts.get(x) == "check" for x in others)

    @staticmethod
    def _second_pair(hole, board):
        """A pair with the board's second card, or a pocket pair between its top two."""
        hr = [c >> 2 for c in hole]
        br = sorted({c >> 2 for c in board}, reverse=True)
        if len(br) < 2:
            return False
        if hr[0] == hr[1]:
            return br[1] < hr[0] < br[0]
        return br[1] in hr and br[0] not in hr

    def _river_value(self, s, hole, board, pot):
        """river_ip: a move, or None to leave it to the other plans."""
        made, _ = self._completes(hole, board)
        mc = self._made_class(hole, board)
        bet = lambda frac: self.raise_to(s, s.my_bet + frac * pot)   # noqa: E731
        if made or mc == "strong":
            return bet(self.river_strong)
        if mc == "good":
            return bet(self.river_good)
        if self._cbet_class(hole, board) == "top":
            return bet(self.river_top)
        if self._second_pair(hole, board):
            if self.rng.random() < self.river_second:
                return bet(self.river_second_small)
            return "check", 0
        return None

    def _general(self, s, hole, board, pot, k):
        """gen_plan: bet or check by hand type, street, position and players."""
        made, scary = self._completes(hole, board)
        cls = "strong" if made else self._cbet_class(hole, board)
        river = len(board) == 5
        flop = len(board) == 3
        hu = k == 1
        checked_to = any(st == s.street and seat != s.my_seat for st, seat, _, _ in s.history)
        pos = 1.0 if checked_to else 0.6        # (first to act: less bluffing)
        r = self.rng.random()
        bet = lambda frac: self.raise_to(s, s.my_bet + frac * pot)   # noqa: E731
        if cls == "strong":
            if river and checked_to and hu:
                return bet(0.75)
            return bet(0.75) if r < 0.85 else ("check", 0)
        if cls == "top":
            good = self._made_class(hole, board) == "good"
            if river:
                if scary:
                    p = 0.2 if good else 0.0
                else:
                    p = (0.6 if good else 0.35) * (1.0 if hu else 0.6)
                return bet(0.5) if r < p * (1.0 if checked_to else 0.8) else ("check", 0)
            p = (0.75 if hu else 0.6) * (0.6 if scary else 1.0)
            return bet(0.6) if r < p else ("check", 0)
        if cls == "medium":
            p = (0.12 if flop else 0.08 if not river else 0.05) * (1.0 if hu else 0.3)
            if river and not checked_to:
                p = 0.0
            return bet(0.33) if r < p * self._bf else ("check", 0)
        if cls == "draw" and not river:
            p = (0.4 if hu else 0.2) * pos
            return bet(0.5) if r < p * self._semi() else ("check", 0)
        # overcards, backdoors, air, busted draws
        if river:
            p = (0.22 if hu else 0.03) * pos
            return bet(0.75) if r < p * self._bf else ("check", 0)
        if cls == "equity":
            p = (0.3 if flop else 0.2) * (1.0 if hu else 0.25) * pos
        else:
            p = (0.2 if flop else 0.15) * (1.0 if hu else 0.2) * pos
        return bet(0.33 if flop else 0.5) if r < p * self._bf else ("check", 0)

    def _later_defence(self, s, hole, board, pot):
        """cont_later: "call", or None for the usual logic."""
        streets = ["preflop", "flop", "turn", "river"]
        cur = streets.index(s.street)
        if cur < 2 or not s.to_call:
            return None
        live = [p for p in s.players if not p.folded]
        if len(live) != 2:
            return None
        now = [(seat, a) for st, seat, a, _ in s.history if st == s.street]
        nb = [seat for seat, a in now if a == "raise"]
        if len(nb) != 1 or nb[0] == s.my_seat:     # (one bet, not a raise of ours)
            return None
        opp = next(p for p in s.players if p.seat == nb[0])
        eff = min(s.my_stack + s.my_bet, opp.stack + opp.bet) / s.big_blind
        if eff < self.cont_min_bb or s.to_call >= 0.6 * s.my_stack:
            return None
        frac = s.to_call / max(1, pot - s.to_call)
        made, _ = self._completes(hole, board)
        cls = "strong" if made else self._cbet_class(hole, board)
        if cls in ("strong", "top"):
            return "call"
        river = cur == 3
        if cls == "medium":
            if river:
                return "call" if self.rng.random() < self.cl_river_medium else None
            if frac <= self.cl_small or self.rng.random() < self.cl_medium_big:
                return "call"
            return None
        d = self._draw_kind(hole, board)
        if d in ("flush", "open") and frac <= self.cl_draw_max:
            return "call"
        if d == "gut" and frac <= self.cl_small:
            return "call"
        return None

    def _semi(self):
        """Semi-bluffs (real equity) shrink half as fast as bluffs under ICM."""
        return 0.5 + 0.5 * self._bf

    def postflop(self, s, hole, board):
        icm = self.icm_post(s) if self.icm_post else None
        rp = icm.max_premium() if icm else 0.0
        press = icm.pressure() if icm else 0.0
        # bluffs: fewer when we carry the risk, a few more when they do (capped at 1.5x)
        self._bf = (min(1.5, max(0.0, 1.0 - press / self.icm_bluff_scale))
                    if self.icm_bluff_scale else 1.0)
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

        if (s.to_call == 0 and self.river_ip and len(board) == 5 and
                self._checked_to_last(s)):
            mv = self._river_value(s, hole, board, pot)
            if mv is not None:
                return mv

        if s.to_call == 0 and self.donk_check and self._donk_spot(s):
            r = self.rng.random()
            cls = self._made_class(hole, board)
            if self.donk_texture and not self._caller_texture(board):
                return "check", 0          # (check it all: strong hands check-raise)
            # (by hand type: the quick equity estimate is too noisy to sort one-pair hands)
            if cls == "strong":
                lead, size = r < self.donk_strong, self.value_size
            elif cls == "good":
                lead, size = r < self.donk_good, self.thin_size
            elif cls == "draw":
                lead, size = r < self.donk_draw * min(1.0, bluff_mult) * self._semi(), 0.5
            elif cls == "air":
                lead, size = r < self.donk_air * min(1.0, bluff_mult) * self._bf, 0.5
            else:
                lead, size = False, 0.0
            if lead:
                return self.raise_to(s, s.my_bet + size * pot)
            return "check", 0

        if (s.to_call == 0 and self.cbet_plan and k == 1 and len(board) == 3 and
                self._was_aggressor(s)):
            cls = self._cbet_class(hole, board)
            r = self.rng.random()
            if cls in ("strong", "top"):
                p = self.cb_strong if cls == "strong" else self.cb_top
                size = self.cbet_big
            else:
                p = {"draw": self.cb_draw * self._semi(), "equity": self.cb_equity * self._bf,
                     "medium": self.cb_medium * self._bf, "air": self.cb_air * self._bf}[cls]
                size = self.cbet_small
            if r < p:
                return self.raise_to(s, s.my_bet + size * pot)
            return "check", 0

        if s.to_call == 0 and self.barrel_plan and self._followup_spot(s):
            return self._barrel(s, hole, board, pot)
        if s.to_call == 0 and self.gen_plan:
            return self._general(s, hole, board, pot, k)

        if s.to_call == 0:
            if eq >= self.value_big * f:
                return self.raise_to(s, s.my_bet + self.value_size * pot)
            thin = self.value_thin - self.thin_adapt * max(0.0, 0.45 - folds) + 0.5 * rp
            if eq >= thin * f:
                return self.raise_to(s, s.my_bet + self.thin_size * pot)
            if (not river and 0.38 * f <= eq < 0.55 * f and
                    self.rng.random() < self.semi_bluff * bluff_mult * self._semi()):
                return self.raise_to(s, s.my_bet + 0.5 * pot)
            if river and eq < 0.15 and self.rng.random() < self.river_bluff * bluff_mult * self._bf:
                return self.raise_to(s, s.my_bet + 0.7 * pot)
            if (self.cbet_bluff and k == 1 and len(board) == 3 and self._was_aggressor(s) and
                    self.rng.random() < self.cbet_bluff * min(1.5, bluff_mult) * self._bf):
                return self.raise_to(s, s.my_bet + 0.33 * pot)
            if (self.delayed_cbet and k == 1 and len(board) == 4 and self._delayed_spot(s) and
                    self.rng.random() < self.delayed_cbet * min(1.5, bluff_mult) * self._bf):
                return self.raise_to(s, s.my_bet + 0.45 * pot)
            if (self.barrel and k == 1 and len(board) == 4 and self._barrel_spot(s) and
                    self.rng.random() < self.barrel * min(1.5, bluff_mult) * self._bf):
                return self.raise_to(s, s.my_bet + 0.5 * pot)
            return "check", 0

        need_chip, need_icm = icm.need(s.to_call) if icm else (None, None)
        icm_extra = max(0.0, need_icm - need_chip) if icm else 0.0
        icm_ok = icm_extra <= self.icm_slack or eq >= need_icm   # (rules may force a call)
        cb = self._cbet_spot(s) if (self.xr or self.cont_rule) else None
        if cb is not None:
            eff = min(s.my_stack + s.my_bet, cb.stack + cb.bet) / s.big_blind
            cls = self._made_class(hole, board)
            if self.xr and s.min_raise_to:
                p_xr = {"strong": self.xr_value, "good": self.xr_good,
                        "draw": self.xr_draw * self._semi()}.get(cls, 0.0)
                if cls == "air" and self._continue_hand(hole, board):
                    p_xr = self.xr_air * self._bf  # (air with a backdoor flush draw)
                if self.rng.random() < p_xr:
                    if self.rng.random() < self.xr_large_share:
                        to = s.opp_bet + pot + s.to_call            # about pot
                    else:
                        to = self.xr_small * s.opp_bet
                    if to >= 0.6 * (s.my_stack + s.my_bet):
                        to = s.max_raise_to                       # (that much: all in)
                    return self.raise_to(s, to)
                if cls in ("strong", "good") and icm_ok:
                    return "call", 0               # (the rest of them: call)
            if (self.cont_rule and icm_ok and eff >= self.cont_min_bb and
                    s.to_call < 0.5 * s.my_stack and self._continue_hand(hole, board)):
                return "call", 0

        if self.cheap_call:
            price = need_icm if icm else min(s.to_call, s.my_stack) / (pot + min(s.to_call, s.my_stack))
            made, _ = self._completes(hole, board)
            cls = "strong" if made else self._cbet_class(hole, board)
            sc = evaluate(hole + board)
            full = (sc >> 20) >= 6 and (len(board) < 5 or sc != evaluate(board))
            raises = sum(1 for st, _, a, _ in s.history if st == s.street and a == "raise")
            frac = s.to_call / max(1, pot - s.to_call)
            if cls in ("strong", "top") and price <= self.cheap_need:
                return "call", 0
            hr = [c >> 2 for c in hole]
            paired = hr[0] == hr[1] or any(r in [c >> 2 for c in board] for r in hr)
            if price <= self.cheap_any and (paired or (len(board) < 5 and cls in ("draw", "equity"))):
                return "call", 0          # (9:1 or better: any pair, or a draw before the river)
            if full and raises <= 1 and frac <= 1.05:
                return "call", 0

        if self.cont_later and icm_ok:
            d = self._later_defence(s, hole, board, pot)
            if d == "call":
                # (strong hands may still raise: the usual logic decides that first)
                if self._completes(hole, board)[0] or self._made_class(hole, board) == "strong":
                    if s.min_raise_to and self.rng.random() < 0.45:
                        return self.raise_to(s, s.opp_bet + pot + s.to_call)
                return "call", 0

        to_call = min(s.to_call, s.my_stack) if self.small_bet_margin else s.to_call
        need = to_call / (pot + to_call)   # (all-in for less: the price is what you put in)
        need += icm_extra                  # (prize money: the risk premium on this call)
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
