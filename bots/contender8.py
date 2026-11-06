"""The 8-max contest bot.

Preflop, in order of priority:
  1. your imported sims (bots/range_table8.py, made by solver/import_ranges8.py), blended
     between solved stack depths; heads-up sims cover the blind-vs-blind spots
  2. built-in strategy for every spot (bots/preflop8.py): opens by position, isolation
     raises over limps, 3-bets / squeezes / 4-bets / 5-bets as polarized value + bluff
     ranges sized for the stack depth, calls priced with equity against the raiser's
     range, and every all-in (open shove, re-shove, 4-bet shove) priced with fold equity
Postflop: simulated equity against every opponent still in (bots/postflop8.py).
Every opponent gets their own model (bots/opponent8.py).
"""

import math

from bots import starter
from bots.contender import eq_vs
from bots.lines import raise_amount
from bots.lines8 import current_spot
from bots.opponent8 import Models
from bots.postflop8 import Postflop8Mixin
from bots.preflop8 import (BLUFF_3BET, BLUFF_4BET, BLUFF_5BET, polar, shove_ev,
                             shove_range)
from bots.preflop_data import TOP
from bots.ranges import lookup, mix
from bots.shortstack import hand_type
from cards import card

_PIPS = "23456789TJQKA"


def open_score(h, depth):
    """How good hand h is to open (higher = better), the way solver opening ranges rank
    hands: pairs by rank, high cards with a decent kicker, suited hands, and suited
    connectors for playability (more so deeper); offsuit hands with gaps rank low."""
    hi = _PIPS.index(h[0])
    if len(h) == 2:
        return 16.0 + 1.0 * hi + (1.0 if depth >= 60 else 0.0) * (12 - hi) / 12
    lo = _PIPS.index(h[1])
    gap = hi - lo
    deep = 1.0 + 0.35 * (min(depth, 100) - 25) / 75
    s = 1.2 * hi + 0.95 * lo
    conn = {1: 2.6, 2: 1.6, 3: 0.7}.get(gap, 0.0)
    if h[2] == "s":
        s += 3.0 * deep + conn * deep
        if h[0] == "A" and lo <= 3:
            s += 0.9                   # wheel aces
        if gap <= 2 and lo >= 2 and h[0] not in "AK":
            s += 2.6 * deep            # suited connectors and one-gappers
    else:
        s += 0.9 * conn
        if gap >= 3 and h[0] != "A":
            s -= 1.2                   # dominated, disconnected offsuit hands
    return s


_OPEN_CUM = {}
for _d in (15, 25, 40, 60, 100):
    _cum, _t = {}, 0
    for _h in sorted(TOP, key=lambda h, d=_d: -open_score(h, d)):
        _t += 6 if len(_h) == 2 else 4 if _h[2] == "s" else 12
        _cum[_h] = _t / 1326
    _OPEN_CUM[_d] = _cum


def open_strength(h, eff):
    """Share of hands at least as good to open as h at this depth (0 = best)."""
    d = min(_OPEN_CUM, key=lambda k: abs(math.log(k / max(eff, 1))))
    return _OPEN_CUM[d][h]


try:
    from bots.range_table8 import TABLE as TABLE8
except ImportError:  # no 8-max ranges imported yet
    TABLE8 = {}

# Share of hands opened first-in by position at 100 bb (roughly what solvers do).
RFI = {"UTG": 0.14, "UTG1": 0.16, "LJ": 0.19, "HJ": 0.23, "CO": 0.29, "BTN": 0.43,
       "SB": 0.40}
POSTFLOP_ORDER = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
# Unsized raises from imported ranges, by how many raises came before.
DEFAULT_SIZES = {0: (2.3, "bb"), 1: (3.0, "x"), 2: (2.3, "x"), 3: (2.2, "x")}


# Small 3-bets at 25 bb or less. Value (3-bet and call a jam): the very top, widening
# against wide openers; medium pairs and offsuit big aces would rather jam. Bluffs (3-bet
# and fold to a jam): suited aces and suited kings / queens with blockers and playability.
SMALL3_VALUE = ["AA", "KK", "QQ", "AKs", "JJ", "AQs", "KQs", "AJs", "TT"]
# Study profile (small3_lists=2): the order GTO-style charts pick small 3-bet bluffs in.
SMALL3_BLUFF_STUDY = ["A5s", "A8s", "A9s", "K9s", "KTs", "A4s", "Q9s", "K8s", "A3s", "Q8s",
                      "A2s", "A7s", "A6s", "J9s", "K7s", "K6s", "K5s"]
SMALL3_BLUFF = ["A5s", "A8s", "A7s", "A6s", "A4s", "K9s", "Q9s", "A9s", "J9s", "K8s",
                "A3s", "Q8s"]
RANK_ORDER = "23456789TJQKA"


def playability(h):
    """Share of equity a flat call keeps, by hand shape: suited connectors play well after
    the flop, offsuit broadways get dominated, the smallest pairs rarely improve."""
    if len(h) == 2:
        return 0.93 if h[0] in "23" else 1.0
    gap = RANK_ORDER.index(h[0]) - RANK_ORDER.index(h[1])
    if h[2] == "s":
        return 1.08 if gap == 1 else 1.04 if gap == 2 else 1.0
    if h[0] in "AKQJ" and h[1] in "KQJT":
        return 0.94
    return 1.0


def multiway_equity(eqs):
    """Rough pot share against several opponents from the heads-up equity against each
    (odds against add up). Close to a full simulation: AA vs three tight ranges 0.61
    (exact 0.58), 99 vs two 0.25 (0.29)."""
    if len(eqs) == 1:
        return eqs[0]
    odds = sum((1 - e) / e for e in (clamp(e, 0.01, 0.99) for e in eqs))
    return 1 / (1 + odds)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


class Bot(Postflop8Mixin, starter.Bot):
    table = None            # tests can set an imported table on an instance
    overcall = 0.5          # jams: after a call, later players still call this much as often
    allin_caller_share = 0.6  # facing an all-in: someone who called it has this share of the
                              # shover's range
    allin_waiting_pen = 0.02  # ... extra equity needed per invested player still to act
    use_table = 1           # 0: ignore imported sims (for comparing against them)
    deep_jam_bb = 0         # >0: an open all-in deeper than this holds a tighter range:
    deep_jam_k = 0.2        # share deep_jam_k x deep_jam_bb / jam size,
    deep_jam_floor = 0.06   # at least this
    stretch_down = 0        # 1: between short_bb and your shallowest sim (10-12.5 bb under
                            # a 15 bb sim), play that sim instead of the built-in rules
    value_thin = 0.46       # tuned for 8-max (heads-up default 0.50)
    short_bb = 10           # open shove-or-fold at or below this many big blinds
    ante_widen = 0.30       # open 30% wider per big blind of ante in the pot
    # Re-raise ranges (shares of the raiser's range) and bluffs per value combo.
    value_3bet = 0.17
    bluff_3bet_ip = 0.8
    bluff_3bet_oop = 0.6
    value_4bet = 0.33
    bluff_4bet = 0.6
    value_5bet = 0.5
    bluff_freq = 0.75       # a bluff hand re-raises this often (else plays as normal)
    reshove_bb = 30         # re-raise all-in (not small) at or below this depth
    # Small 3-bets at 25 bb or less: raise to 2.65x the open in position, 3.2x out of
    # position. Value hands plan to call a jam, bluffs plan to fold to one.
    small3_bb = 25
    small3_ip = 2.65
    small3_oop = 3.2
    small3_value = 0.14     # value part, as a share of the opener's range
    small3_bluff_ip = 0.7   # bluff combos per value combo
    small3_bluff_oop = 0.5
    small3_margin = 0.06    # value hands need this much more equity than the jam price
    offsuit_realise = 0.88  # equity realised by offsuit hands with a weak second card
    # Share of its equity a flatted hand gets to keep, by position.
    realise_ip = 0.95
    realise_bb = 0.85
    realise_sb = 0.72
    realise_oop = 0.80
    realise_3bet_ip = 0.92  # calling a 3-bet
    realise_3bet_oop = 0.80
    flat_small3 = 0         # 1 = at 25 bb or less, call a small 3-bet in position (tested: -1.5)
    small3_commit = 0.4     # above this share of the stack, jam instead of a small 3-bet
    fourbet_shove_bb = 50   # 4-bet all-in at or below this depth
    open_deep = 2.3         # open size (bb) above 40 bb deep; the small blind's
    sb_open_deep = 3.0
    open_mid = 2.1          # 25-40 bb
    open_short = 2.0        # 25 bb or less
    squeeze_value = 0.8     # with callers in: value part x this, bluffs x squeeze_bluff
    squeeze_bluff = 0.6
    cold4_value = 0.012     # cold 4-bet (open and 3-bet in front): top share of hands
    need_equity = 1         # facing a bet: no pair / draw / overcards = fold
    river_bluff = 1.0       # river bluffs when checked to (x how often they fold; +1.5 bb/100)
    barrel = 0.6            # turn barrel after a called c-bet (+0.9 bb/100)
    cbet_bluff = 0.8        # flop c-bet bluffs as the preflop raiser, heads-up (+1.3 bb/100)
    open_mc = 0             # 1: opening ranges by open_score (playability), not all-in
                            # equity; the trainer uses it (contest default until tested)
    blind_curve = 0         # 1: blinds keep equity by hand strength (solver-like defence; the
                            # trainer uses it; off for the contest: -1 to -3 bb/100 vs the test bots)
    curve_lo = 0.46
    curve_slope = 1.6
    curve_pivot = 0.305
    curve_sb = 0.9
    curve_suited = 0.06
    small3_lists = 1        # small 3-bets from SMALL3_VALUE / SMALL3_BLUFF (0 = by strength)
    defend_vs3 = 0.45       # vs a small 3-bet: jam at least this share of our opening range
    call_pref_all = 0       # 1: the call preference applies to every hand in position
    behind_pen = 0.025      # equity needed per player still to act behind a flat call
    call_pref = 1.6         # in position: call over jam unless the jam EV is this much more
    shape_realise = 1       # flat calls adjusted by hand shape (playability)
    jam_in_range = 1        # only jam hands inside (jam_cap x) the assumed all-in range
    jam_cap = 2.0           # (tested: 2.0 beat 1.0 by +1.3 bb/100, and the uncapped jams)
    limp_game = 0           # 1: the SB limps (study profile), the BB plays against limps
    limp_max_bb = 60        # ... at or below this depth
    limp_lo, limp_slope = 0.8, 2.0  # share of equity a limped hand keeps (by equity)
    limp_pref = 1.0         # weight on limping / checking against a jam
    sb_raise_value = 0.005  # SB first in: raise this top share (AA)
    sb_trap = 0.03          # ... limp the next best (big pairs) to trap
    sb_jam_bb = 30          # ... jam or limp at or below this depth
    sb_raise_deep = 0.15    # ... deeper: raise this share (by open strength), limp the rest
    limp_pref_suited = 1.0  # ... times this for suited hands (they play well limped)
    iso_value = 0.0         # BB vs a limp: raise this top share before thinking of a jam
    limp_range = 0.65       # the BB's guess at how wide the SB limps
    iso_jam_margin = 0.0    # BB vs a limp: a jam must beat checking by this much (x pot)
    bb_iso = 0.25           # BB vs a limp: raise this share of hands (by open strength)
    sb_curve_lo, sb_curve_slope = 0.55, 0.0  # the SB's own curve (slope 0 = same as the BB)
    caller_exp = 0.6        # calling an open with others already in: equity ** (1 + this x callers)
    caller_exp_bb = -1.0    # ... the big blind's own (-1 = the same)
    limped_real = 0.0       # >0: the SB that limped then faces a raise keeps this much equity
    limped_call_pref = 1.0  # ... and weighs a call this much against a jam
    curve_hc = 0.0          # blind_curve: offsuit hands count this much more equity per high card
    curve_conn = 0.0        # ... and this much more when connected
    bb_pair_jam = 0         # 1: the BB jams 77-JJ rather than calling (no call_pref_bb)
    call_pref_bb = 1.0      # BB heads-up vs the opener: call unless a jam is this much better
    ace_off_pref = 1.0      # from the blinds, offsuit aces weigh a call this much against a jam
    pair_jam = 0            # 1: pairs 66-TT don't get call_pref (they'd rather jam short)
    short_suited_pen = 0.0  # at small3_bb or less, suited hands below T keep this much less
    behind_bw = 1.0         # behind_pen multiplier for suited broadways (they can go on)
    force_slack = 1.0       # over the jam quota, 3-bet small only within this of the jam price
    ace_call = 0            # 1: with the jam quota on, suited aces call in position
    small3_value_abs = 0.0  # >0: small 3-bet value share, whatever the opener's range
    small3_blind = 1.0      # ... times this from the blinds (they jam more instead)
    small3_total = 0.0      # >0: bluffs top the small 3-bets up to this share of hands
    jam_width_depth = 0.0   # ... times (25 bb / stack) to this power: shorter stacks jam more
    jam_width_k = 0.0       # >0: re-jam at most k * r^2.5 of hands over an open of width r
                            # (on top of the small 3-bet value hands); the rest small 3-bet
    steal_min = 1.0         # lowest factor the steal adjustment may apply
    steal_adapt = 1.0       # open wider vs players behind who fold a lot (0 = off; +5 bb/100)
    value_adapt = 0.0       # >0: wider value 3-bets vs players who don't fold to them
    seen_weight = 20        # all-ins seen before a player's real call rate counts half

    def __init__(self, seed=None):
        super().__init__(seed)
        self.seed_base = seed if isinstance(seed, int) else 0
        self.models = Models()
        self.plan3 = None

    def new_hand(self, hand_no, is_button):
        super().new_hand(hand_no, is_button)
        # Fresh random stream per hand: the same deal plays the same unless a decision
        # differs, which makes comparisons between versions far less noisy.
        self.rng.seed(self.seed_base * 1000003 + hand_no)
        self.plan3 = None   # "call" / "fold" to a jam after our small 3-bet

    def hand_over(self, result):
        self.models.learn(result)

    # ---------- helpers ----------
    def pos(self, s, seat=None):
        p = next(pl.position for pl in s.players if pl.seat == (s.my_seat if seat is None
                                                                  else seat))
        return "SB" if (p == "BTN" and len(s.players) == 2) else p

    def pid(self, s, seat):
        return next(pl.pid for pl in s.players if pl.seat == seat)

    def open_share(self, s, seat):
        """How much of their range this player opens with, from position and history."""
        m = self.models.get(self.pid(s, seat))
        base = RFI.get(self.pos(s, seat), 0.25)
        return clamp(base * m.open.value / 0.25, 0.03, 1.0)

    def three_share(self, s, seat):
        m = self.models.get(self.pid(s, seat))
        return clamp(m.three_bet.value * 1.0, 0.02, 0.6)

    def effective(self, s):
        mine = s.my_stack + s.my_bet
        theirs = max((p.stack + p.bet for p in s.opponents), default=0)
        return min(mine, theirs) / s.big_blind

    def preflop_shares(self, s):
        """Range share each opponent still holds, from their preflop actions."""
        shares = {}
        raises = 0
        last_raise_share = 1.0
        for st, seat, a, _ in s.history:
            if st != "preflop":
                break
            if seat == s.my_seat:
                if a == "raise":
                    raises += 1
                    # Callers of our raise hold a range sized from it, not any two cards.
                    last_raise_share = (self.open_share(s, seat) if raises == 1 else
                                        self.three_share(s, seat) if raises == 2 else 0.035)
                continue
            if a == "raise":
                raises += 1
                share = (self.open_share(s, seat) if raises == 1 else
                         self.three_share(s, seat) if raises == 2 else
                         0.035 if raises == 3 else 0.02)
                shares[seat] = share
                last_raise_share = share
            elif a == "call":
                shares[seat] = clamp(last_raise_share * 1.4 if raises else 0.5, 0.03, 1.0)
            elif a == "check":
                shares.setdefault(seat, 1.0)
        for p in s.opponents:
            if p.seat not in shares:  # big blind who never had to act, etc.
                shares[p.seat] = 1.0 if p.position == "BB" else 0.6
        return shares

    # ---------- decisions ----------
    def act(self, s):
        if s.street == "preflop":
            table = TABLE8 if self.table is None else self.table
            if table and self.use_table:
                spot = current_spot(s)
                eff = self.effective(s)
                blend = lookup(spot, eff, table)
                if not blend and self.stretch_down and eff > self.short_bb:
                    depths = [d for d in table.get(spot, {}) if d > 0]
                    if depths and eff < min(depths) <= 1.5 * eff:
                        blend = [(1.0, table[spot][min(depths)])]
                if blend:
                    return self.play_mix(s, mix(blend, hand_type(*map(card, s.hole))))
        return super().act(s)

    def play_mix(self, s, freqs):
        r = self.rng.random()
        for action, f in freqs.items():
            r -= f
            if r < 0:
                if action == "fold":
                    return ("fold", 0) if s.to_call else ("check", 0)
                if action == "call" or not s.min_raise_to:
                    return ("call", 0) if s.to_call else ("check", 0)
                n_raises = sum(1 for st, _, a, _ in s.history
                               if st == "preflop" and a == "raise")
                default = DEFAULT_SIZES.get(n_raises, (2.0, "x"))
                return "raise", raise_amount(s, action, default)
        return ("fold", 0) if s.to_call else ("check", 0)

    # ---------- built-in preflop ----------
    def preflop(self, s, hole, bb):
        h = hand_type(*hole)
        x = {
            "h": h, "strength": TOP[h], "eff": self.effective(s), "pos": self.pos(s),
            "bb": bb,
        }
        pre = [(seat, a, amt) for st, seat, a, amt in s.history if st == "preflop"]
        x["raises"] = [(seat, amt) for seat, a, amt in pre if a == "raise"]
        acted = {seat for seat, _, _ in pre}
        x["behind"] = [p for p in s.opponents if p.seat not in acted and not p.all_in]
        x["need"] = s.to_call / (s.pot + s.to_call) if s.to_call else 0.0
        x["pre"] = pre
        raises = x["raises"]
        if raises and any(p.seat == raises[-1][0] and p.all_in for p in s.players):
            return self.vs_allin(s, x)
        n = len(raises)
        if n == 0:
            return self.unopened(s, x)
        if n == 1:
            return self.vs_open(s, x)
        if n == 2:
            return self.vs_3bet(s, x)
        if n == 3:
            return self.vs_4bet(s, x)
        return self.vs_5bet(s, x)

    # -- small helpers --
    def _allin(self, s):
        return ("raise", s.max_raise_to) if s.min_raise_to else ("call", 0)

    def _call_if(self, s, x, equity, extra=0.0):
        if s.to_call == 0:
            return "check", 0
        return ("call", 0) if equity >= x["need"] + extra else ("fold", 0)

    def _ip(self, s, x, seat):
        pos = x["pos"]
        return (pos not in ("SB", "BB") and
                POSTFLOP_ORDER.index(pos) > POSTFLOP_ORDER.index(self.pos(s, seat)))

    def _responders(self, s, x, shares):
        """Everyone still to respond to an all-in by us, as shove_ev wants them."""
        out = []
        invested = {seat for seat, a, _ in x["pre"] if a in ("raise", "call")}
        est = None
        for p in s.players:
            if p.seat == s.my_seat or p.folded or p.all_in:
                continue
            m = self.models.get(p.pid)
            f = m.calls_jam_in if p.seat in invested else m.calls_jam_cold
            seen = (f.n - 12) / (f.n - 12 + self.seen_weight)  # 12 = the prior's weight
            share = shares.get(p.seat)
            if share is None and p.seat in invested:
                # Someone who called (or raised) earlier holds a real range, not any two
                # cards: without this, jams over a squeeze looked like they always worked.
                est = est if est is not None else self.preflop_shares(s)
                share = est.get(p.seat, 1.0)
            out.append((1.0 if share is None else share, p.bet, p.stack, f.value, seen))
        return out

    def _call_scale(self, s, x):
        return 1.0  # how often each player really calls all-ins is in _responders

    def _shove(self, s, x, floor, shares):
        """EV of going all-in, with opponents calling against the range we'd really shove
        here (at least the top `floor` of hands)."""
        my_total = s.my_stack + s.my_bet
        resp = self._responders(s, x, shares)
        scale = self._call_scale(s, x)
        # Overcalls matter after a squeeze (few players left, money in); with more players
        # to act (open shoves), the quick heads-up form keeps the move well under 1 ms.
        ov = self.overcall if len(resp) <= 3 else 0.0
        our = shove_range(s.pot, s.my_stack, my_total, resp, scale, floor, ov)
        if self.jam_in_range and x["strength"] > our * self.jam_cap:
            # Outside the range opponents are assumed to call against: jamming it anyway
            # would make the real range far wider than the one the EV was worked out for.
            return -1.0
        return shove_ev(x["h"], s.pot, s.my_stack, my_total, resp, our, scale, ov)

    def _steal_adj(self, x):
        """Open wider when the players still to act fold to opens more than usual (and
        tighter when they defend a lot). 1.0 until there's history on them."""
        if not self.steal_adapt:
            return 1.0
        actual = prior = 1.0
        for p in x["behind"]:
            m = self.models.get(p.pid)
            actual *= max(0.05, 1 - m.three_bet.value - m.cold_call.value)
            prior *= 0.81  # the models' starting guess: 7% 3-bets, 12% calls
        # Only ever wider: the starting guess (81% folds per player) is below what real
        # blinds fold, so a "tighter" signal mostly means normal defending (tested: losing).
        return clamp((actual / prior) ** self.steal_adapt, self.steal_min, 1.6)

    def _value_adj(self, s, seat):
        """3-bet more hands for value against players who rarely fold to re-raises."""
        if not self.value_adapt:
            return 1.0
        f = self.models.get(self.pid(s, seat)).folds_to_reraise.value
        return clamp(1 + (0.55 - f) * self.value_adapt, 0.85, 1.4)

    def _fold_mult(self, s, seat):
        """Bluff more against players who fold to re-raises, none against those who don't."""
        f = self.models.get(self.pid(s, seat)).folds_to_reraise.value
        return clamp((f - 0.25) / 0.30, 0.0, 1.4)

    def _bluff_roll(self):
        return self.rng.random() < self.bluff_freq

    # -- spots --
    def unopened(self, s, x):
        bb, eff, pos, strength = x["bb"], x["eff"], x["pos"], x["strength"]
        limpers = sum(1 for _, a, _ in x["pre"] if a == "call")
        if s.to_call == 0:  # big blind after limps (or a walk)
            if not limpers or not s.min_raise_to:
                return "check", 0
            if self.limp_game and pos == "BB" and limpers == 1 and eff <= self.limp_max_bb:
                return self.bb_vs_limp(s, x)
            kind = polar(strength, x["h"], 0.14, 0.5, BLUFF_3BET)
            if kind == "value" or (kind == "bluff" and self._bluff_roll()):
                return (self._allin(s) if eff <= 15 else
                        self.raise_to(s, (4 + limpers) * bb))
            return "check", 0
        if (self.limp_game and pos == "SB" and not limpers and eff <= self.limp_max_bb and
                len(s.players) > 2):
            return self.sb_first_in(s, x)
        if eff <= self.short_bb:  # open shove or fold
            our = 0.04
            shares = {p.seat: 1.0 for p in x["behind"]}
            for seat, a, _ in x["pre"]:
                if a == "call":
                    shares[seat] = 0.5
            return self._allin(s) if self._shove(s, x, our, shares) > 0 else ("fold", 0)
        ante_bb = s.ante_total / bb
        share = min(0.95, RFI.get(pos, 0.2) * (1 + self.ante_widen * ante_bb) *
                    (0.6 if limpers else 1.0) * self._steal_adj(x))
        if self.open_mc:
            strength = open_strength(x["h"], eff)
        if strength <= share:
            if eff > 40:
                size = self.sb_open_deep if pos == "SB" else self.open_deep
            elif eff > 25:
                size = 2.5 if pos == "SB" else self.open_mid
            else:
                size = 2.2 if pos == "SB" else self.open_short
            return self.raise_to(s, (size + limpers) * bb)
        return "fold", 0

    def _flat_equity(self, x, h, r, callers, ip):
        """Equity a flat call of an open (range share r) with hand h really keeps."""
        eff, pos = x["eff"], x["pos"]
        blind = pos in ("SB", "BB")
        realise = (self.realise_ip if ip else self.realise_bb if pos == "BB" else
                   self.realise_sb if pos == "SB" else self.realise_oop)
        if blind and self.blind_curve:
            # Out of position, weak hands keep far less of their equity than strong ones
            # (they rarely make a hand worth going on with and get bluffed off): the share
            # kept rises with the hand's equity against the opener's range.
            eq0 = eq_vs(h, r)
            lo, slope = self.curve_lo, self.curve_slope
            if pos == "SB" and self.sb_curve_slope:
                lo, slope = self.sb_curve_lo, self.sb_curve_slope
            if len(h) == 3 and h[2] == "o" and pos == "BB":
                # High cards make top pairs that hold, connected cards make straights.
                eq0 += self.curve_hc * {"A": 1.0, "K": 1.0, "Q": 0.6, "J": 0.3}.get(h[0], 0.0)
                gap = RANK_ORDER.index(h[0]) - RANK_ORDER.index(h[1])
                eq0 += self.curve_conn * (1.0 if gap == 1 else 0.6 if gap == 2 else 0.0)
            realise = clamp(lo + slope * (eq0 - self.curve_pivot), lo, 1.05) * (
                1.0 if pos == "BB" else self.curve_sb)
            if len(h) == 3 and h[2] == "s":
                realise += self.curve_suited
        if len(h) == 3 and h[2] == "o" and h[1] not in "AKQJT" and not (
                blind and self.blind_curve):
            realise *= self.offsuit_realise  # dominated offsuit hands play badly
        if eff <= self.small3_bb and len(h) == 3 and h[0] in "AK" and h[1] in "98765432":
            realise *= self.offsuit_realise  # weak-kicker A/K are flat-call traps when short
        if self.shape_realise:
            realise *= playability(h)
        if (self.short_suited_pen and eff <= self.small3_bb and len(h) == 3 and
                h[2] == "s" and h[0] in "98765432"):
            realise *= 1 - self.short_suited_pen
        if self.limped_real and pos == "SB" and x.get("limped"):
            # We limped and the big blind raised: a capped range, a good price, and we've
            # seen the flop cheaply already in our plans: keep most of the equity.
            realise = self.limped_real
        exp = self.caller_exp_bb if (pos == "BB" and self.caller_exp_bb >= 0) else self.caller_exp
        return eq_vs(h, r) ** (1 + exp * callers) * realise

    def _behind_pen(self, x, h):
        pen = self.behind_pen * len(x["behind"])
        if len(h) == 3 and h[2] == "s" and h[1] in "AKQJT":
            pen *= self.behind_bw
        return pen

    def _flat_ev(self, s, x, h, r, callers, ip):
        if not s.to_call:
            return 0.0
        eq = self._flat_equity(x, h, r, callers, ip) - self._behind_pen(x, h)
        return eq * (s.pot + s.to_call) - s.to_call

    # -- the limping game (study profile): SB first in, and the BB against its limp --
    def _limp_realise(self, eq):
        return clamp(self.limp_lo + self.limp_slope * (eq - 0.5), 0.2, 1.1)

    def _limp_pref(self, h):
        """How much a hand would rather limp (or check) than jam."""
        if h[0] == "A" and h[-1] == "o":
            return self.limp_pref * self.ace_off_pref  # blockers, poor flops: jam them
        if h[-1] == "s":
            return self.limp_pref * self.limp_pref_suited
        return self.limp_pref

    def sb_first_in(self, s, x):
        """Folded to the small blind: raise the very top, limp-trap big pairs, jam the
        hands that do better jamming than limping (blockers, small pairs), limp the rest
        that can play, fold the worst."""
        h, bb, eff, strength = x["h"], x["bb"], x["eff"], x["strength"]
        if strength <= self.sb_raise_value:
            return self.raise_to(s, (2.0 if eff <= 30 else 2.5) * bb)
        eq = eq_vs(h, 1.0)
        ev_limp = eq * self._limp_realise(eq) * (s.pot + s.to_call) - s.to_call
        if strength <= self.sb_trap:
            return "call", 0
        if eff <= self.sb_jam_bb:
            bb_seat = next((p.seat for p in s.opponents if p.position == "BB"), None)
            ev_shove = self._shove(s, x, 0.03, {bb_seat: 1.0} if bb_seat is not None else {})
            if ev_shove > max(ev_limp * self._limp_pref(h), 0.0):
                return self._allin(s)
        elif open_strength(h, eff) <= self.sb_raise_deep:
            return self.raise_to(s, 3.0 * bb)
        return ("call", 0) if ev_limp > 0 else ("fold", 0)

    def bb_vs_limp(self, s, x):
        """The small blind limped: jam where that beats checking, raise the hands that
        play best, check the rest."""
        h, bb, eff = x["h"], x["bb"], x["eff"]
        limper = next(seat for seat, a, _ in x["pre"] if a == "call")
        r = self.limp_range
        if x["strength"] <= self.iso_value:
            return self.raise_to(s, (2.0 if eff <= 30 else 3.0) * bb)
        eq = eq_vs(h, r)
        ev_check = eq * self._limp_realise(eq) * s.pot
        if eff <= self.sb_jam_bb:
            ev_shove = self._shove(s, x, 0.03, {limper: r})
            if ev_shove > ev_check * self._limp_pref(h) + self.iso_jam_margin * s.pot:
                return self._allin(s)
        if open_strength(h, eff) <= self.bb_iso:
            return self.raise_to(s, (2.0 if eff <= 30 else 3.0) * bb)
        return "check", 0

    def vs_open(self, s, x):
        h, strength, eff, pos = x["h"], x["strength"], x["eff"], x["pos"]
        opener, open_bet = x["raises"][0]
        r = self.open_share(s, opener)
        start = next(k for k, (seat, a, _) in enumerate(x["pre"]) if a == "raise")
        callers = sum(1 for seat, a, _ in x["pre"][start:] if a == "call")
        ip = self._ip(s, x, opener)
        blind = pos in ("SB", "BB")
        x["limped"] = any(seat == s.my_seat and a == "call" for seat, a, _ in x["pre"][:start])
        eq_call = self._flat_equity(x, h, r, callers, ip)
        behind_pen = self._behind_pen(x, h)

        self._vfrac = 0.0
        if eff <= self.small3_bb:
            move = self.small_3bet(s, x, r, opener, callers, ip)
            if move:
                return move
        if eff <= self.reshove_bb:
            # Re-shove, call or fold: compare the EV of each.
            shares = {opener: r}
            for seat, a, _ in x["pre"][start:]:
                if a == "call":
                    shares[seat] = clamp(r * 1.4, 0.05, 1.0)
            our = 0.03
            ev_shove = self._shove(s, x, our, shares)
            ev_call = ((eq_call - behind_pen) * (s.pot + s.to_call) - s.to_call
                       if s.to_call else 0.0)
            # In position, hands that play well after the flop (suited hands, pairs 44-77)
            # would rather call than jam unless the jam is clearly better.
            pref = 1.0
            if ip and (self.call_pref_all or h in ("44", "55", "66", "77") or
                       (len(h) == 3 and h[2] == "s")):
                pref = self.call_pref
            if self.pair_jam and h in ("66", "77", "88", "99", "TT"):
                pref = 1.0
            if x["limped"] and self.limped_real:
                pref = self.limped_call_pref
            if pos == "BB" and not callers:
                pref = self.call_pref_bb  # the big blind closes the action: calls keep more
                if self.bb_pair_jam and len(h) == 2 and h[0] in "789TJ":
                    pref = 1.0  # middle pairs hate playing out of position: jam them
            if blind and h[0] == "A" and h[-1] == "o":
                pref *= self.ace_off_pref  # offsuit aces: blockers, poor flops: jam them
            if ev_shove > max(ev_call * pref, 0.0):
                quota = self.jam_width_k * r ** 2.5 * (25.0 / max(eff, 5.0)) ** self.jam_width_depth
                ace = (self.ace_call and self.jam_width_k and ip and h[0] == "A" and
                       h[-1] == "s" and ev_call > 0)
                if not ace and (not self.jam_width_k or strength <= self._vfrac + quota):
                    return self._allin(s)
                # Against a tight open only the very top jams. Beyond that, hands that
                # play well after the flop call in position; the rest 3-bet small.
                suited_or_pair = len(h) == 2 or h[2] == "s"
                if ip and suited_or_pair and ev_call > 0:
                    return "call", 0
                if eff <= self.small3_bb:
                    move = self.small_3bet(s, x, r, opener, callers, ip, force_value=True)
                    if move:
                        return move
            return ("call", 0) if ev_call > 0 else ("fold", 0)

        value = clamp(self.value_3bet * r * (1.15 if blind else 1.0) * self._value_adj(s, opener),
                      0.02, 0.14)
        ratio = (self.bluff_3bet_ip if ip else self.bluff_3bet_oop) * self._fold_mult(s, opener)
        if callers:  # squeeze: tighter value, fewer bluffs
            value *= self.squeeze_value
            ratio *= self.squeeze_bluff
        kind = polar(strength, h, value, ratio, BLUFF_3BET)
        if kind == "value" or (kind == "bluff" and self._bluff_roll()):
            bvb = pos == "BB" and self.pos(s, opener) == "SB"
            mult = 3.0 if (ip or bvb) else 3.8 if blind else 3.3
            return self.raise_to(s, mult * s.opp_bet + callers * s.opp_bet)
        return self._call_if(s, x, eq_call, behind_pen)

    def open_share_of_me(self, s, x):
        """Roughly how wide we opened from this position (share of all hands)."""
        ante_bb = s.ante_total / s.big_blind
        return min(0.95, RFI.get(x["pos"], 0.25) * (1 + self.ante_widen * ante_bb))

    def jam_share(self, s, opener):
        """How much of all hands the opener jams over a small 3-bet."""
        m = self.models.get(self.pid(s, opener))
        return clamp(self.open_share(s, opener) * 0.25 * m.reraise.value / 0.20, 0.03, 0.3)

    def small_3bet(self, s, x, r, opener, callers, ip, force_value=False):
        """A non-all-in 3-bet at 25 bb or less, or None to jam / call / fold instead.

        Value: the top of our range that has enough equity to call the opener's jam
        (plan "call"). Bluffs: blockers and suited hands that fold to a jam (plan "fold").
        Everything else is left to the re-shove / call / fold comparison."""
        bvb = x["pos"] == "BB" and self.pos(s, opener) == "SB"
        in_pos = ip or bvb
        to = ((self.small3_ip if in_pos else self.small3_oop) + callers) * s.opp_bet
        my_total = s.my_stack + s.my_bet
        if to > self.small3_commit * my_total or not s.min_raise_to:
            return None
        # Price of calling a jam after raising to `to`.
        jammer = next(p for p in s.players if p.seat == opener)
        jam_to = min(my_total, jammer.stack + jammer.bet)
        final_pot = s.pot - jammer.bet - s.my_bet + 2 * jam_to
        need = (jam_to - to) / max(final_pot, 1)
        j = self.jam_share(s, opener)
        blind = x["pos"] in ("SB", "BB")
        value = clamp(self.small3_value * r * (1.15 if blind else 1.0) *
                      self._value_adj(s, opener), 0.025, 0.08)
        if self.small3_value_abs:  # a fixed value share (study profile)
            value = self.small3_value_abs * (self.small3_blind if blind else 1.0)
        m = self.models.get(self.pid(s, opener))
        ratio = self.small3_bluff_ip if in_pos else self.small3_bluff_oop
        ratio *= clamp(0.20 / max(m.reraise.value, 0.05), 0.4, 1.5)  # fewer vs frequent jammers
        ratio *= self._fold_mult(s, opener)
        ratio /= 1.5  # bluff_set counts against the widened value share below
        if callers:
            value *= self.squeeze_value
            ratio *= self.squeeze_bluff
        if force_value:
            eq_j = eq_vs(x["h"], j)
            if eq_j < need - self.force_slack:
                return None
            self.plan3 = "call" if eq_j >= need else "fold"
            return self.raise_to(s, to)
        if self.small3_lists:
            # Value: the top of SMALL3_VALUE, as many combos as the value share allows.
            vset, combos = set(), 0
            for v in SMALL3_VALUE:
                if combos >= value * 1326:
                    break
                vset.add(v)
                combos += 6 if len(v) == 2 else 4 if v[2] == "s" else 12
            self._vfrac = combos / 1326
            if x["h"] in vset and eq_vs(x["h"], j) >= need + self.small3_margin:
                kind = "value"
            else:
                wanted = ratio * 1.5 * combos
                if self.small3_total:  # bluffs fill a fixed total small 3-bet share
                    wanted = max(0.0, self.small3_total * 1326 - combos)
                bluffs, got = set(), 0
                for b in SMALL3_BLUFF_STUDY if self.small3_lists == 2 else SMALL3_BLUFF:
                    if got >= wanted:
                        break
                    if self.small3_lists == 2 and self._flat_ev(s, x, b, r, callers, ip) > 0:
                        continue  # bluff with hands that would fold, not ones that call
                    bluffs.add(b)
                    got += 4
                kind = "bluff" if x["h"] in bluffs else None
        # Value = the hands near the top that call a jam comfortably (so AK ranks ahead
        # of 88: it does better against a jamming range).
        elif x["strength"] <= value * 1.5 and eq_vs(x["h"], j) >= need + self.small3_margin:
            kind = "value"
        else:
            kind = polar(x["strength"], x["h"], value * 1.5, ratio, BLUFF_3BET, 0.6)
            kind = "bluff" if kind == "bluff" else None
        if kind == "value" or (kind == "bluff" and self._bluff_roll()):
            self.plan3 = "call" if kind == "value" else "fold"
            return self.raise_to(s, to)
        return None

    def vs_3bet(self, s, x):
        h, strength, eff = x["h"], x["strength"], x["eff"]
        (opener, _), (three_bettor, _) = x["raises"]
        t = self.three_share(s, three_bettor)
        if self.pos(s, three_bettor) in ("SB", "BB"):
            t = min(0.6, t * 1.3)  # blinds 3-bet wider against late opens
        ip = self._ip(s, x, three_bettor)
        i_opened = opener == s.my_seat
        i_called = any(seat == s.my_seat and a == "call" for seat, a, _ in x["pre"])
        shares = {three_bettor: t}

        if i_opened:
            if eff <= self.fourbet_shove_bb:
                our = 0.025
                ev_shove = self._shove(s, x, our, shares)
                eq = eq_vs(h, t) * (self.realise_3bet_ip if ip else self.realise_3bet_oop)
                ev_call = eq * (s.pot + s.to_call) - s.to_call
                small = s.to_call <= 0.3 * (s.my_stack + s.my_bet)
                if eff <= 25 and not (self.flat_small3 and ip and small):
                    ev_call = -1  # too shallow to call and play; shove or fold
                if ev_shove > max(ev_call, 0.0):
                    return self._allin(s)
                if ev_call > 0:
                    return "call", 0
                if self.defend_vs3 and small:
                    # Against a small 3-bet, don't fold so much of the opening range that
                    # any two cards become a profitable 3-bet: jam the top share of it.
                    mine = self.open_share_of_me(s, x)
                    if x["strength"] <= mine * self.defend_vs3:
                        return self._allin(s)
                return "fold", 0
            value = clamp(self.value_4bet * t, 0.012, 0.06)
            kind = polar(strength, h, value, self.bluff_4bet * self._fold_mult(s, three_bettor),
                         BLUFF_4BET, 0.5)
            if kind == "value" or (kind == "bluff" and self._bluff_roll()):
                return self.raise_to(s, (2.2 if ip else 2.5) * s.opp_bet)
            return self._call_if(s, x, eq_vs(h, t) * (self.realise_3bet_ip if ip else
                                                      self.realise_3bet_oop),
                                 0.02 * len(x["behind"]))

        if i_called:  # we called the open and got squeezed
            if strength <= 0.015:
                return (self._allin(s) if eff <= self.fourbet_shove_bb
                        else self.raise_to(s, 2.3 * s.opp_bet))
            if strength <= 0.06:
                return self._call_if(s, x, eq_vs(h, t) * 0.8, 0.04)
            return "fold", 0

        # Cold: an open and a 3-bet in front of us.
        kind = polar(strength, h, self.cold4_value, 0.3, BLUFF_5BET)
        if kind == "value" or (kind == "bluff" and self._bluff_roll()):
            return (self._allin(s) if eff <= self.fourbet_shove_bb
                    else self.raise_to(s, 2.3 * s.opp_bet))
        if strength <= 0.035 and eff > 40:
            return self._call_if(s, x, eq_vs(h, t) * 0.85, 0.05)
        return "fold", 0

    def vs_4bet(self, s, x):
        h, strength, eff = x["h"], x["strength"], x["eff"]
        four_bettor = x["raises"][2][0]
        m = self.models.get(self.pid(s, four_bettor))
        f = clamp(m.reraise.value * 0.15, 0.015, 0.25)
        i_three_bet = x["raises"][1][0] == s.my_seat
        if i_three_bet and self.plan3:  # a small 4-bet over our small 3-bet: same plan
            if self.plan3 == "call":
                return self._allin(s)
            return self._call_if(s, x, eq_vs(h, f * 3), 0.02)
        if not i_three_bet:
            return self._allin(s) if strength <= 0.01 else ("fold", 0)
        value = max(0.012, self.value_5bet * f)
        if strength <= value:
            return self._allin(s)
        if (h in BLUFF_5BET and f >= 0.04 and
                self.rng.random() < 0.15 * self._fold_mult(s, four_bettor)):
            return self._allin(s)
        ip = self._ip(s, x, four_bettor)
        if eff > 80 and ip:
            return self._call_if(s, x, eq_vs(h, f) * 0.85)
        return "fold", 0

    def vs_5bet(self, s, x):
        if x["strength"] <= 0.015:
            return self._allin(s)
        return self._call_if(s, x, eq_vs(x["h"], 0.02))

    def vs_allin(self, s, x):
        raises = x["raises"]
        n = len(raises)
        shover = raises[-1][0]
        m = self.models.get(self.pid(s, shover))
        if n == 1:
            share = max(self.open_share(s, shover) * 1.3, 0.08)
            jam_bb = raises[0][1] / s.big_blind
            if self.deep_jam_bb and jam_bb > self.deep_jam_bb:
                # An open all-in deeper than your sims ever jam: a strong range, tighter
                # the deeper it is (not the whole opening range x 1.3).
                share = min(share, max(self.deep_jam_floor,
                                       self.deep_jam_k * self.deep_jam_bb / jam_bb))
        elif n == 2:
            share = max(self.three_share(s, shover) * 1.5, 0.06)
        elif n == 3:
            share = clamp(m.reraise.value * 0.25, 0.04, 0.4)
        else:
            share = 0.03
        if m.open.value > 0.8:  # shoves (or raises) almost everything
            share = max(share, 0.6)
        # Everyone who already called the all-in is in the pot too, with a range tighter
        # than the shover's; and players who have money in and still have to act can
        # still wake up with a hand. (Heads-up against the shover, this is just eq_vs.)
        h = x["h"]
        k = max(i for i, (seat, a, _) in enumerate(x["pre"]) if a == "raise")
        callers = sum(1 for seat, a, _ in x["pre"][k + 1:] if a == "call")
        invested = {seat for seat, a, _ in x["pre"] if a in ("raise", "call")}
        top = max(p.bet for p in s.players)
        waiting = sum(1 for p in s.opponents if p.seat in invested and not p.folded and
                      not p.all_in and p.bet < top)
        extra = 0.03 * len(x["behind"]) + self.allin_waiting_pen * waiting
        if n == 3 and raises[1][0] == s.my_seat and self.plan3:
            # Jammed on after our small 3-bet: value hands call, bluffs fold unless the
            # price is very good.
            share = max(share, self.jam_share(s, raises[0][0]))
            extra += -0.06 if self.plan3 == "call" else 0.02
        eqs = [eq_vs(h, share)] + [eq_vs(h, clamp(share * self.allin_caller_share, 0.015,
                                                  share))] * callers
        return self._call_if(s, x, multiway_equity(eqs), extra)
