"""Snapshot of contender8 after the overnight tuning (adaptive steals, c-bets, barrels).

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


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


class Bot(Postflop8Mixin, starter.Bot):
    table = None            # tests can set an imported table on an instance
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
    barrel = 0.6            # turn barrel after a called c-bet (+0.9 bb/100)
    cbet_bluff = 0.8        # flop c-bet bluffs as the preflop raiser, heads-up (+1.3 bb/100)
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
            if table:
                spot = current_spot(s)
                blend = lookup(spot, self.effective(s), table)
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
        for p in s.players:
            if p.seat == s.my_seat or p.folded or p.all_in:
                continue
            m = self.models.get(p.pid)
            f = m.calls_jam_in if p.seat in invested else m.calls_jam_cold
            seen = (f.n - 12) / (f.n - 12 + self.seen_weight)  # 12 = the prior's weight
            out.append((shares.get(p.seat, 1.0), p.bet, p.stack, f.value, seen))
        return out

    def _call_scale(self, s, x):
        return 1.0  # how often each player really calls all-ins is in _responders

    def _shove(self, s, x, floor, shares):
        """EV of going all-in, with opponents calling against the range we'd really shove
        here (at least the top `floor` of hands)."""
        my_total = s.my_stack + s.my_bet
        resp = self._responders(s, x, shares)
        scale = self._call_scale(s, x)
        our = shove_range(s.pot, s.my_stack, my_total, resp, scale, floor)
        return shove_ev(x["h"], s.pot, s.my_stack, my_total, resp, our, scale)

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
            kind = polar(strength, x["h"], 0.14, 0.5, BLUFF_3BET)
            if kind == "value" or (kind == "bluff" and self._bluff_roll()):
                return (self._allin(s) if eff <= 15 else
                        self.raise_to(s, (4 + limpers) * bb))
            return "check", 0
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
        if strength <= share:
            if eff > 40:
                size = self.sb_open_deep if pos == "SB" else self.open_deep
            elif eff > 25:
                size = 2.5 if pos == "SB" else self.open_mid
            else:
                size = 2.2 if pos == "SB" else self.open_short
            return self.raise_to(s, (size + limpers) * bb)
        return "fold", 0

    def vs_open(self, s, x):
        h, strength, eff, pos = x["h"], x["strength"], x["eff"], x["pos"]
        opener, open_bet = x["raises"][0]
        r = self.open_share(s, opener)
        start = next(k for k, (seat, a, _) in enumerate(x["pre"]) if a == "raise")
        callers = sum(1 for seat, a, _ in x["pre"][start:] if a == "call")
        ip = self._ip(s, x, opener)
        blind = pos in ("SB", "BB")
        realise = (self.realise_ip if ip else self.realise_bb if pos == "BB" else
                   self.realise_sb if pos == "SB" else self.realise_oop)
        if len(h) == 3 and h[2] == "o" and h[1] not in "AKQJT":
            realise *= self.offsuit_realise  # dominated offsuit hands play badly
        if eff <= self.small3_bb and len(h) == 3 and h[0] in "AK" and h[1] in "98765432":
            realise *= self.offsuit_realise  # weak-kicker A/K are flat-call traps when short
        eq_call = eq_vs(h, r) ** (1 + 0.6 * callers) * realise
        behind_pen = 0.025 * len(x["behind"])

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
            if ev_shove > max(ev_call, 0.0):
                return self._allin(s)
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

    def jam_share(self, s, opener):
        """How much of all hands the opener jams over a small 3-bet."""
        m = self.models.get(self.pid(s, opener))
        return clamp(self.open_share(s, opener) * 0.25 * m.reraise.value / 0.20, 0.03, 0.3)

    def small_3bet(self, s, x, r, opener, callers, ip):
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
        m = self.models.get(self.pid(s, opener))
        ratio = self.small3_bluff_ip if in_pos else self.small3_bluff_oop
        ratio *= clamp(0.20 / max(m.reraise.value, 0.05), 0.4, 1.5)  # fewer vs frequent jammers
        ratio *= self._fold_mult(s, opener)
        ratio /= 1.5  # bluff_set counts against the widened value share below
        if callers:
            value *= self.squeeze_value
            ratio *= self.squeeze_bluff
        # Value = the hands near the top that call a jam comfortably (so AK ranks ahead
        # of 88: it does better against a jamming range).
        if x["strength"] <= value * 1.5 and eq_vs(x["h"], j) >= need + self.small3_margin:
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
                return ("call", 0) if ev_call > 0 else ("fold", 0)
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
        elif n == 2:
            share = max(self.three_share(s, shover) * 1.5, 0.06)
        elif n == 3:
            share = clamp(m.reraise.value * 0.25, 0.04, 0.4)
        else:
            share = 0.03
        if m.open.value > 0.8:  # shoves (or raises) almost everything
            share = max(share, 0.6)
        if n == 3 and raises[1][0] == s.my_seat and self.plan3:
            # Jammed on after our small 3-bet: value hands call, bluffs fold unless the
            # price is very good.
            share = max(share, self.jam_share(s, raises[0][0]))
            slack = -0.06 if self.plan3 == "call" else 0.02
            return self._call_if(s, x, eq_vs(x["h"], share),
                                 slack + 0.03 * len(x["behind"]))
        return self._call_if(s, x, eq_vs(x["h"], share), 0.03 * len(x["behind"]))
