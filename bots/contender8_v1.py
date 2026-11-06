"""Snapshot of contender8 before the polarized preflop rework, kept for comparison."""
"""The 8-max contest bot.

Preflop, in order of priority:
  1. your imported sims (bots/range_table8.py, made by solver/import_ranges8.py), blended
     between solved stack depths; heads-up sims cover the blind-vs-blind spots
  2. at 12 bb or less: shove-or-fold, priced with how many players are left to act and
     how often each of them calls an all-in
  3. otherwise built-in ranges by position (opens, 3-bets, 4-bets, defends), each call
     priced with real equity against the raiser's likely range, tightened for callers
     already in and players still to act
Postflop: simulated equity against every opponent still in (bots/postflop8.py).
Every opponent gets their own model (bots/opponent8.py).
"""

from bots import starter
from bots.contender import eq_vs
from bots.lines import raise_amount
from bots.lines8 import current_spot
from bots.opponent8 import Models
from bots.postflop8 import Postflop8Mixin
from bots.preflop_data import TOP
from bots.ranges import lookup, mix
from bots.shortstack import hand_type
from cards import card

try:
    from bots.range_table8 import TABLE as TABLE8  # noqa: F401
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
    three_bet_mult = 0.45   # 3-bet this share of the opener's range (tuned)
    four_bet_mult = 0.35
    short_bb = 10           # shove-or-fold at or below this many big blinds (tuned)
    ante_widen = 0.30       # open 30% wider per big blind of ante in the pot

    def __init__(self, seed=None):
        super().__init__(seed)
        self.models = Models()

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

    def preflop(self, s, hole, bb):
        h = hand_type(*hole)
        strength = TOP[h]
        eff = self.effective(s)
        pos = self.pos(s)
        pre = [(seat, a, amt) for st, seat, a, amt in s.history if st == "preflop"]
        raises = [(seat, amt) for seat, a, amt in pre if a == "raise"]
        acted = {seat for seat, _, _ in pre}
        behind = [p for p in s.opponents if p.seat not in acted and not p.all_in]
        need = s.to_call / (s.pot + s.to_call) if s.to_call else 0.0
        allin = ("raise", s.max_raise_to) if s.min_raise_to else ("call", 0)
        facing_allin = bool(raises) and any(p.seat == raises[-1][0] and p.all_in
                                            for p in s.players)

        def call_if(equity, extra=0.0):
            if s.to_call == 0:
                return "check", 0
            return ("call", 0) if equity >= need + extra else ("fold", 0)

        if facing_allin:
            n = len(raises)
            shover = raises[-1][0]
            share = (max(self.open_share(s, shover) * 1.3, 0.08) if n == 1 else
                     max(self.three_share(s, shover) * 1.3, 0.05) if n == 2 else 0.04)
            return call_if(eq_vs(h, share), 0.03 * len(behind))

        if eff <= self.short_bb:
            return self.short(s, h, strength, eff, raises, behind)

        if not raises:
            limpers = sum(1 for _, a, _ in pre if a == "call")
            if s.to_call == 0:  # big blind with only limps (or a walk)
                if strength <= 0.15 and s.min_raise_to:
                    return self.raise_to(s, (4 + limpers) * bb)
                return "check", 0
            # Antes make the pot bigger, so stealing it pays more: open wider.
            ante_bb = s.ante_total / bb
            share = min(0.95, RFI.get(pos, 0.2) * (1 + self.ante_widen * ante_bb) *
                        (0.6 if limpers else 1.0))
            if strength <= share:
                size = (3.0 if pos == "SB" else 2.3) if eff > 40 else 2.0
                return self.raise_to(s, (size + limpers) * bb)
            return "fold", 0

        n = len(raises)
        raiser = raises[-1][0]
        callers = sum(1 for seat, a, _ in pre[pre.index((raiser, "raise", raises[-1][1])):]
                      if a == "call")
        i_raised = any(seat == s.my_seat for seat, _ in raises)
        ip = (pos not in ("SB", "BB") and
              POSTFLOP_ORDER.index(pos) > POSTFLOP_ORDER.index(self.pos(s, raiser)))
        if n == 1:
            opener = self.open_share(s, raiser)
            three = clamp(self.three_bet_mult * opener * (1.15 if pos in ("SB", "BB") else 1),
                          0.025, 0.16) * (0.7 if callers else 1.0)
            if strength <= three:
                mult = 3.0 if ip else 3.8
                return self.raise_to(s, mult * s.opp_bet + callers * s.opp_bet)
            realise = 0.95 if ip else 0.85 if pos == "BB" else 0.72
            eq = eq_vs(h, opener) ** (1 + 0.6 * callers)
            return call_if(eq * realise, 0.025 * len(behind))
        if n == 2:
            three = self.three_share(s, raiser)
            if i_raised:  # we opened and got 3-bet
                four = clamp(self.four_bet_mult * three, 0.015, 0.07)
                if strength <= four:
                    return allin if eff <= 60 else self.raise_to(s, 2.3 * s.opp_bet)
                return call_if(eq_vs(h, three) * (0.9 if ip else 0.8), 0.02 * len(behind))
            if strength <= 0.02:  # cold: someone opened, someone else 3-bet
                return allin if eff <= 60 else self.raise_to(s, 2.3 * s.opp_bet)
            return call_if(eq_vs(h, three) * 0.8, 0.05) if strength <= 0.04 else ("fold", 0)
        if n == 3:
            if strength <= 0.015:
                return allin
            return call_if(eq_vs(h, 0.035) * 0.85) if i_raised else ("fold", 0)
        return allin if strength <= 0.01 else call_if(eq_vs(h, 0.02))

    def short(self, s, h, strength, eff, raises, behind):
        """Shove or fold with 12 bb or less."""
        allin = ("raise", s.max_raise_to) if s.min_raise_to else ("call", 0)
        if not raises:
            if s.to_call == 0 and not s.min_raise_to:
                return "check", 0
            calls = [self.models.get(p.pid).calls_allin.value for p in behind]
            p_no_call = 1.0
            for c in calls:
                p_no_call *= 1 - c
            c_mean = sum(calls) / len(calls) if calls else 0.25
            final_pot = s.pot + s.my_stack + (s.my_stack + s.my_bet)
            ev_shove = (p_no_call * s.pot +
                        (1 - p_no_call) * (eq_vs(h, max(c_mean, 0.02)) * final_pot - s.my_stack))
            if ev_shove > 0:
                return allin
            return ("check", 0) if s.to_call == 0 else ("fold", 0)
        raiser = raises[-1][0]
        share = (self.open_share(s, raiser) if len(raises) == 1
                 else self.three_share(s, raiser))
        raiser_bet = max(p.bet for p in s.players)
        final_pot = s.pot + s.my_stack + max(0, s.my_stack + s.my_bet - raiser_bet)
        need_allin = s.my_stack / final_pot + 0.03 * len(behind)
        return allin if eq_vs(h, share) >= need_allin else ("fold", 0)
