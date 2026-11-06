"""The main bot: plays well from 1 to 100+ big blinds with no imported data, and uses your
own solver ranges wherever you've imported them.

Preflop, in order of priority:
  1. your imported sims (bots/range_table.py), blended between solved depths
  2. at 10 bb or less: the solved push/fold equilibrium
  3. otherwise the built-in strategy below: ranges as a share of hands by strength, sized
     by stack depth, and every call priced with real equity against the opponent's likely
     range versus the pot odds
Postflop: simulated equity against the opponent's likely range (see bots/postflop.py).
Classic is the same bot with the starter bot's rule-of-thumb postflop play, for comparison.
"""

from bots import ranges
from bots.postflop import PostflopMixin
from bots.preflop_data import EQ_VS_TOP, TOP
from bots.pushfold_table import PUSH
from bots.lines import current_line
from bots.opponent import OpponentModel
from bots.shortstack import hand_type
from cards import card

# Share of all hands (by strength) for each part of the built-in strategy. Tune freely.
OPEN = 0.80          # small blind opens this much
BB_RAISE_LIMP = 0.35  # big blind raises a limp with this much
THREE_BET = 0.20     # big blind re-raises an open (tuned: 0.12 -> 0.20; 0.18-0.26 all similar)
FOUR_BET = 0.045     # small blind re-raises a 3-bet
FIVE_BET = 0.025     # big blind goes all-in over a 4-bet
# Equity actually realised after the flop when calling a raise (out of position < 1).
REALISE_OOP = 0.80
REALISE_IP = 0.92


def combos(h):
    return 6 if len(h) == 2 else 4 if h[2] == "s" else 12


def shove_share(eff):
    """Share of hands the push/fold equilibrium shoves at this depth (open shove range)."""
    return sum(combos(h) for h, d in PUSH.items() if d >= eff) / 1326


def eq_vs(h, share):
    """Equity of hand type h against the strongest `share` of hands."""
    pct = min(100, max(2, int(round(share * 50)) * 2))
    return EQ_VS_TOP[pct][h]


class Classic(ranges.Bot):
    # Tunable copies of the module settings (tournament.py / sweep.py can override them).
    open_share = OPEN
    limp_raise_share = BB_RAISE_LIMP
    three_bet_share = THREE_BET
    four_bet_share = FOUR_BET
    five_bet_share = FIVE_BET
    realise_oop = REALISE_OOP
    realise_ip = REALISE_IP
    def __init__(self, seed=None):
        super().__init__(seed)
        self.opp = OpponentModel()
        self.was_button = False

    def new_hand(self, hand_no, is_button):
        super().new_hand(hand_no, is_button)
        self.was_button = is_button
        self.start_stack = None

    def hand_over(self, result):
        self.opp.learn(result["history"], self.was_button, self.start_stack)

    def act(self, s):
        if s.street == "preflop":
            if self.start_stack is None:
                self.start_stack = s.my_stack + s.my_bet
            move = self.exploit_short(s)
            if move:
                return move
        return super().act(s)

    def exploit_short(self, s):
        """At 10 bb or less, step away from the solved charts only once the opponent
        clearly plays differently from them (enough hands, far from typical)."""
        bb = s.big_blind
        eff = min(s.my_stack + s.my_bet, s.opp_stack + s.opp_bet) / bb
        if eff > 10:
            return None
        h = hand_type(*map(card, s.hole))
        line = current_line(s)
        calls = self.opp.calls_allin
        if line == "" and calls.n >= 40 and abs(calls.value - 0.45) > 0.2:
            c = calls.value  # they call this share of the time, with their best hands
            ev_push = (1 - c) * 1.0 + c * (eq_vs(h, c) * 2 * eff - eff)
            return ("raise", s.max_raise_to) if ev_push > -0.5 else ("fold", 0)
        if line == "a" and self.opp.open.n >= 40 and self.opp.open.value > 0.9:
            need = s.to_call / (s.pot + s.to_call)
            return ("call", 0) if eq_vs(h, self.opp.open.value) >= need else ("fold", 0)
        return None

    def preflop(self, s, hole, bb):
        """Built-in strategy (only reached when no imported range and stacks > 10 bb)."""
        h = hand_type(*hole)
        strength = TOP[h]  # 0 = best hand, 1 = worst
        eff = min(s.my_stack + s.my_bet, s.opp_stack + s.opp_bet) / bb
        line = current_line(s)
        need = s.to_call / (s.pot + s.to_call) if s.to_call else 0.0

        def call_if(equity):
            if s.to_call == 0:
                return "check", 0
            return ("call", 0) if equity >= need else ("fold", 0)

        def raise_to(chips):
            return self.raise_to(s, chips)

        allin = ("raise", s.max_raise_to) if s.min_raise_to else ("call", 0)

        if eff <= 12 and s.to_call and s.opp_stack > 0 and line not in ("", "c"):
            # Short and facing a raise: all-in or fold, priced as if they call.
            share = {"r": max(self.open_share, self.opp.open.value)}.get(
                line, max(self.three_bet_share, self.opp.reraise.value))
            total = s.my_stack + s.my_bet
            need_allin = (total - s.my_bet) / (2 * total)
            return allin if eq_vs(h, share) >= need_allin else ("fold", 0)

        if line == "":  # small blind, first in
            if strength > self.open_share:
                return "fold", 0
            if eff <= 15 and strength <= shove_share(eff):
                return allin
            size = 2.0 if eff <= 25 else 2.2 if eff <= 45 else 2.5
            return raise_to(size * bb)

        if line == "c":  # big blind vs a limp
            if strength <= self.limp_raise_share:
                return allin if eff <= 15 else raise_to(4 * bb)
            return "check", 0

        # What this opponent actually raises with, learned as we play (see opponent.py).
        opens = max(self.open_share, self.opp.open.value)
        three_bets = max(self.three_bet_share, self.opp.three_bet.value)
        reraises = max(self.four_bet_share, self.opp.reraise.value)

        if line == "r":  # big blind vs an open raise
            # Re-raise more against someone who opens nearly everything.
            if strength <= max(self.three_bet_share, 0.3 * (opens - self.open_share) / (1 - self.open_share) * 0.5 + self.three_bet_share):
                return allin if eff <= 30 else raise_to(3 * s.opp_bet)
            return call_if(eq_vs(h, opens) * self.realise_oop)

        if line == "rr":  # small blind vs a 3-bet
            if strength <= max(self.four_bet_share, 0.35 * three_bets):
                return allin if eff <= 60 else raise_to(2.2 * s.opp_bet)
            return call_if(eq_vs(h, three_bets) * self.realise_ip)

        if line == "rrr":  # big blind vs a 4-bet
            if strength <= max(self.five_bet_share, 0.5 * reraises):
                return allin
            return call_if(eq_vs(h, reraises) * self.realise_oop)

        if line == "cr":  # we limped (only from imported ranges) and got raised
            if strength <= self.four_bet_share:
                return allin
            return call_if(eq_vs(h, self.limp_raise_share) * self.realise_ip)

        if line.endswith("a"):  # facing an all-in: pure pot odds against their range
            share = {"a": max(shove_share(eff), self.opp.open.value - 0.2),
                     "ca": self.limp_raise_share, "ra": three_bets,
                     "rra": reraises, "cra": reraises}.get(line, max(self.five_bet_share, reraises))
            return call_if(eq_vs(h, share))

        if s.to_call == 0:
            return "check", 0
        # Deeper raise wars we don't model: continue only with the very top.
        return allin if strength <= 0.02 else ("fold", 0)


class Bot(PostflopMixin, Classic):
    pass

