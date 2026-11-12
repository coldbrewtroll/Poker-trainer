"""Final table sessions in the practice trainer (mode "Final table").

Eight players from one of the 100 preset final tables (icm_study.preset_table; 9th place is
already paid), a prize pool, and full hands: the button moves, the blinds go up every
LEVEL_HANDS hands, players bust and are paid by finishing place, and the session runs until
you bust, win, or end it.

Hands play as in the trainer's Play mode: the bots play your Monker sims preflop and your
Pio solves on the flop. On top come the final table's preflop rules, for every player (the
bots, your hint and the hand tree's ranges, through with_open_policy):
  - first in with less than SHORT_BB effective: push or fold (AA opens 2x);
  - every all-in decision up to JAM_PRACTICAL_BB effective is made by the ICM equilibrium
    (POLICY["icm"]; off: your sims, chip EV): jamming first in, re-jamming over an open
    (against the opener's opening range), and calling an all-in. Opens, calls and small
    3-bets stay your sims'; deeper than JAM_PRACTICAL_BB a first-in jam becomes an open.
Your all-in decisions (jam, fold, or call a jam) are graded in dollars, and the ICM Coach
explains any decision of yours.
"""

import json
import random
import threading
from collections import Counter, OrderedDict
from pathlib import Path

import icm_study as I
from engine8 import position_names

# The other seats get names: yours, and made-up ones (no real players).
PLAYER_NAMES = [
    "Bob Smith", "Chip Stackwell", "Al Inn", "Rick Oshea", "Penny Ante", "Rich Blinders",
    "Seymour Flops", "Max Bett", "Ella Fold", "Callum Down", "Gus Shot", "Seth Mining",
    "Phil Pott", "Cash Gamble", "Sandy Bagger", "Stu Straddle", "Wanda Showdown",
    "Clay Check", "Sal Shuffle", "Barry Bluffington", "Tilda Tilt", "Rocco Rivers",
    "Lucky Lou", "Polly Turner", "Donny Donkworth", "Felty McFelt", "Moe Money",
    "Kat Fish", "Rex Raise", "Dee Ler"]

LEVEL_HANDS = 30      # the blinds go up every 30 hands (0: never), through the WSOP
                      # Circuit Main Event levels in icm_study.WSOP_LEVELS (a 60-minute
                      # level is about 30 hands at a live final table)
SHORT_BB = 12.0       # first in with less than this (effective): push or fold (AA: 2x)
JAM_PRACTICAL_BB = 25.0  # deeper than this, all-ins aren't ICM-solved, and a first-in
                         # jam becomes a normal open
OPEN_X = 2.0          # the open size
# The final table's rules are on (the trainer sets "on" and "table" for each hand; "icm":
# all-in decisions by ICM, else by your sims).
POLICY = {"on": False, "icm": True, "table": None}
SEATS = 8


def integer_chips(values, total):
    """Whole chips that add up to `total` (largest remainders get the odd chips)."""
    base = [int(v) for v in values]
    left = int(round(total)) - sum(base)
    order = sorted(range(len(values)), key=lambda i: -(values[i] - base[i]))
    for i in order[:max(0, left)]:
        base[i] += 1
    return base


class FinalTable:
    def __init__(self, table_no, pays9, bb_chips):
        self.table_no = table_no
        self.pays9 = sorted(pays9, reverse=True)
        self.bb = bb_chips
        st9 = I.preset_table(table_no)
        drop = table_no % 9                    # 9th has busted already
        st8 = st9[:drop] + st9[drop + 1:]
        rot = (table_no * 3) % SEATS            # you (seat 0) get a different stack per table
        st8 = st8[rot:] + st8[:rot]
        self.stacks = [int(round(x * bb_chips)) for x in st8]
        self.button = table_no % SEATS
        self.hands, self.level = 0, 1
        self.level_hands = LEVEL_HANDS          # hands per level (0: the blinds stay)
        self.level_start = 1                    # the hand this level began
        self.out = {}                           # seat -> (place, prize)
        self.over = None                        # ("bust" | "win" | "ended", place, dollars)
        self.icm = {"right": 0, "spots": 0, "lost": 0.0}
        names = random.sample(PLAYER_NAMES, SEATS - 1)       # new faces every session
        self.names = {0: "You", **{x: names[x - 1] for x in range(1, SEATS)}}
        self.leaks = LeakLog()
        self.note = ""
        self.id = random.random()

    # -- who is where --
    def alive(self):
        """Seats still in, in seat order (you, seat 0, first)."""
        return [x for x in range(SEATS) if self.stacks[x] > 0]

    def next_alive(self, seat):
        for d in range(1, SEATS + 1):
            t = (seat + d) % SEATS
            if self.stacks[t] > 0:
                return t
        return seat

    def name(self, seat):
        return self.names.get(seat, f"Seat {seat + 1}")

    def pays(self, n=None):
        n = len(self.alive()) if n is None else n
        return self.pays9[:n]

    def equities(self):
        """Every seat's prize equity in dollars (paid seats: their prize)."""
        al = self.alive()
        eq = dict(zip(al, I.icm([self.stacks[x] for x in al], self.pays(len(al)))))
        for x, (_, prize) in self.out.items():
            eq.setdefault(x, prize)
        return eq

    def left_in_level(self):
        """Hands until the blinds go up (None: they don't)."""
        if not self.level_hands or self.level >= len(I.WSOP_LEVELS):
            return None
        return max(1, self.level_hands - (self.hands - self.level_start))

    def blinds(self):
        return I.blinds_text(self.level)

    # -- a hand --
    def start_hand(self):
        """Move the button, raise the blinds when a level is up. Returns (seats in, the
        button's index among them, starting stacks)."""
        self.note = ""
        if self.hands:
            self.button = self.next_alive(self.button)
        elif self.stacks[self.button] <= 0:
            self.button = self.next_alive(self.button)
        self.hands += 1
        if (self.level_hands and self.hands - self.level_start >= self.level_hands and
                self.level < len(I.WSOP_LEVELS)):
            self.level += 1
            self.level_start = self.hands
            r = I.level_ratio(self.level)
            al = self.alive()
            new = integer_chips([self.stacks[x] / r for x in al], sum(self.stacks) / r)
            for x, v in zip(al, new):
                self.stacks[x] = max(1, v)
            self.note = f"Blinds up: {self.blinds()}"
        al = self.alive()
        return al, al.index(self.button), [self.stacks[x] for x in al]

    def finish_hand(self, seats, start, result):
        """Apply a hand's chip results (per seat in `seats`): busts, places and payouts."""
        before = self.equities()
        new = integer_chips([max(0.0, s + r) for s, r in zip(start, result)], sum(start))
        for x, v in zip(seats, new):
            self.stacks[x] = v
        busts = sorted([k for k, v in enumerate(new) if v <= 0], key=lambda k: start[k])
        n = len(seats)
        out = []
        # Out in the same hand: the smaller starting stack finishes lower; equal starting
        # stacks tie and split the places' prizes (the tournament rule).
        i = 0
        while i < len(busts):
            tied = [k for k in busts[i:] if start[k] == start[busts[i]]]
            places = list(range(n, n - len(tied), -1))
            prize = sum(self.pays9[p - 1] for p in places) / len(tied)
            for k in tied:
                self.out[seats[k]] = (places[-1], prize)
                out.append((seats[k], places[-1], prize))
            n -= len(tied)
            i += len(tied)
        al = self.alive()
        if len(al) == 1:
            self.out[al[0]] = (1, self.pays9[0])
        if 0 in self.out:
            place, prize = self.out[0]
            self.over = ("win" if place == 1 else "bust", place, prize)
        return {"before": before, "after": self.equities(), "busts": out}

    def end(self):
        if not self.over:
            self.over = ("ended", None, self.equities().get(0, 0.0))


# ------------------------------------------------------------ ICM push / fold in a hand

def push_fold_spot(s, ante_bb=1.0):
    """The ICM push/fold reading of a preflop state, or None: ("jam", stacks, me, None)
    when it's folded to this player with less than SHORT_BB effective, ("call", stacks,
    me, jammer) facing a lone first-in all-in. Stacks in bb at the start of the hand, in
    position order (the big blind last), with the big blind ante."""
    if s.street != "preflop":
        return None
    n = len(s.players)
    names = position_names(n)
    by_pos = {p.position: p for p in s.players}
    if any(nm not in by_pos for nm in names):
        return None
    order = [by_pos[nm] for nm in names]
    bb = s.big_blind
    st = []
    for p in order:
        chips = p.stack + p.bet + (s.ante_total if p.position == names[-1] else 0)
        st.append(chips / bb)
    me = next(k for k, p in enumerate(order) if p.seat == s.my_seat)
    acts = [(seat, a, amt) for street, seat, a, amt in s.history
            if street == "preflop" and a != "fold"]
    if not acts:
        if me == n - 1:
            return None
        behind = st[me + 1:]
        eff = min(st[me], max(behind))
        return ("jam", st, me, None) if eff < SHORT_BB else None
    if len(acts) == 1 and acts[0][1] == "raise":
        seat = acts[0][0]
        j = next(k for k, p in enumerate(order) if p.seat == seat)
        jp = order[j]
        if j < me and (jp.all_in or jp.stack == 0 or acts[0][2] >= s.my_stack + s.my_bet):
            return ("call", st, me, j)
    return None


_SOLVES = OrderedDict()
_LOCK = threading.Lock()


def solve(st, pays, who, chips=False, iters=120, posts=None, responders=None, fold_to=None,
          priors=None):
    """solve_jam for `who`, cached (the bots, your hint, the hand tree and the coach all
    ask about the same spot)."""
    key = (tuple(round(x, 4) for x in st), who, tuple(pays), chips, iters,
           tuple(posts) if posts else None, tuple(responders) if responders else None,
           fold_to, tuple(sorted((k, tuple(v)) for k, v in priors.items())) if priors else None)
    with _LOCK:
        hit = _SOLVES.get(key)
    if hit is None:
        val = I.chips_value if chips else I.icm_value(list(pays))
        hit = I.solve_jam(st, val, who, iters=iters, linear=True,   # (later rounds weigh more)
                          posts=list(posts) if posts else None,
                          responders=list(responders) if responders else None,
                          fold_to=fold_to, priors={k: list(v) for k, v in priors.items()}
                          if priors else None)
        with _LOCK:
            _SOLVES[key] = hit
            while len(_SOLVES) > 128:
                _SOLVES.popitem(last=False)
    return hit


class ICMBot:
    """A final-table seat: the bot (your sims, with the final table's preflop rules from
    with_open_policy, which make_study applies)."""

    def __init__(self, bot, table, rng):
        self.bot, self.ft, self.rng = bot, table, rng

    def __getattr__(self, name):
        return getattr(self.bot, name)

    def new_hand(self, hand_no, is_button):
        self.bot.new_hand(hand_no, is_button)

    def hand_over(self, result):
        self.bot.hand_over(result)

    def act(self, s):
        return self.bot.act(s)


def hero_spot(s, table):
    return coach_spot(s, table)


def grade(spot, move, s=None):
    """(right, dollars: going vs folding) for your move in an ICM all-in decision, or None
    when there's nothing to grade (an "info" spot, or a normal-sized bet or call: played on
    as usual)."""
    if spot["kind"] == "info":
        return None
    if spot["kind"] == "mix":
        act = node_action(spot["node"], move, s) if s is not None else None
        ensure_solved(spot)
        res = spot["res"]
        if act is None or res is None or act not in res["ev"][spot["hand"]]:
            return None
        ev, mix = res["ev"][spot["hand"]], res["mix"][spot["hand"]]
        tol = 1e-4 * sum(spot["pays"])          # ($100 in a $1M pool: a coin flip)
        if premium(spot["hand"]):               # (top hands: close options are all fine)
            tol = PREMIUM_MARGIN * sum(spot["pays"])
        top = max(ev.values())
        # the best play: of the actions worth the most (within $tol), the one played most
        best = max((a for a in ev if ev[a] >= top - tol), key=lambda a: mix.get(a, 0.0))
        g = min(0.0, ev[act] - top)
        spot["answer"], spot["best"] = act, best
        return (mix.get(act, 0.0) >= 0.10 or g >= -tol), g
    if move[0] == "fold" or (move[0] == "check" and spot["kind"] == "jam"):
        act = "fold"
    elif move[0] == "call" and spot["kind"] == "call":
        act = "go"
    elif (s is not None and move[0] == "raise" and move[1] >= s.max_raise_to - 1):
        act = "go"
    else:
        return None
    ensure_solved(spot)
    spot["answer"] = act
    g = spot["res"]["gain"][spot["hand"]]
    return (g > 0) == (act == "go") or allin_close(spot, act), g


# An all-in decision this close (a share of the prize pool) is a coin flip: either answer
# is right, as at the game decisions. Within it are the solve's own rounding (a few more
# rounds move such a hand by about this much) and the card removal between the players
# behind, which the solve leaves out.
ALLIN_CLOSE = 1e-4
ALLIN_MIX = 0.10        # a hand the solve plays both ways at least this often: both are right


def go_share(spot):
    """How often the solve goes (jams / calls) with your hand, 0-1, or None."""
    res, h = spot.get("res"), spot.get("hand")
    if not res or h is None:
        return None
    k = I.exact()[1][h]
    w = res.get("call_range") if spot["kind"] == "call" else res.get("jam_range")
    return None if w is None else w[k]


def allin_close(spot, act):
    """True when `act` ("go" / "fold") is a coin flip from the best play: within
    ALLIN_CLOSE of the pool, or a play the solve itself makes ALLIN_MIX of the time."""
    g = spot["res"]["gain"][spot["hand"]]
    if abs(g) <= ALLIN_CLOSE * sum(spot["pays"]):
        return True
    f = go_share(spot)
    if f is None:
        return False
    return f >= ALLIN_MIX if act == "go" else f <= 1 - ALLIN_MIX


def allin_verdict(spot, right, g):
    """The grade's one-liner at a jam / call decision."""
    go = "Jam" if spot["kind"] == "jam" else "Call"
    best = go if g > 0 else "Fold"
    mine = go if spot.get("answer") == "go" else "Fold"
    if not right:
        return f"ICM: {best} {spot['hand']}  \u00b7  wrong, that cost {I.money_k(abs(g))}"
    if mine == best:
        return f"ICM: {best} {spot['hand']}  \u00b7  right ({I.money_k(abs(g))} better)"
    f = go_share(spot)
    if abs(g) > ALLIN_CLOSE * sum(spot["pays"]) and f is not None:
        why = f"the solve plays it both ways ({go.lower()} {100 * f:.0f}%)"
    else:
        why = f"{best} is only {I.money_k(abs(g))} better"
    return f"ICM: {mine} {spot['hand']}  \u00b7  right, a coin flip: {why}"


# ------------------------------------------------------------ the final table's preflop rules

def _first_in(s):
    if s.street != "preflop" or not s.min_raise_to:
        return False
    if any(a != "fold" for street, _, a, _ in s.history if street == "preflop"):
        return False
    return s.position != position_names(len(s.players))[-1]   # not the big blind


def _eff_bb(s):
    me = s.my_stack + s.my_bet
    others = [p.stack + p.bet for p in s.players if p.seat != s.my_seat and not p.folded]
    return min(me, max(others)) / s.big_blind if others else 0.0


def _open_to(s):
    to = int(round(OPEN_X * s.big_blind))
    return ("raise", max(s.min_raise_to, min(s.max_raise_to, to)))


def open_policy(s, move):
    """First in at a final table: under SHORT_BB it's push or fold (AA opens 2x); deeper
    than JAM_PRACTICAL_BB an open-jam becomes a normal open. (With ICM on, icm_layer then
    decides every all-in.)"""
    if not POLICY["on"] or not _first_in(s) or move[0] != "raise":
        return move
    jam = move[1] >= s.max_raise_to - 1
    eff = _eff_bb(s)
    if eff < SHORT_BB:
        if I.hand_class(*s.hole) in TRAP_OPENS:
            return _open_to(s)
        return move if jam else ("raise", s.max_raise_to)
    if jam and eff > JAM_PRACTICAL_BB:
        return _open_to(s)
    return move


# -- ICM all-in decisions: jamming first in, re-jamming over an open, calling an all-in --

def _table(s):
    """(players in position order, start stacks in bb with the BB ante, my index)."""
    n = len(s.players)
    names = position_names(n)
    by_pos = {p.position: p for p in s.players}
    order = [by_pos[nm] for nm in names]
    st = [(p.stack + p.bet + (s.ante_total if k == n - 1 else 0)) / s.big_blind
          for k, p in enumerate(order)]
    me = next(k for k, p in enumerate(order) if p.seat == s.my_seat)
    return order, st, me


def _posts(s, order, upto=None):
    """Each player's bet (bb, blinds included) before preflop action number `upto`."""
    n = len(order)
    bet = {p.seat: 0.0 for p in order}
    ante = s.ante_total
    sb, bb = order[n - 2], order[n - 1]
    bet[sb.seat] = min(s.big_blind // 2, sb.stack + sb.bet)
    bet[bb.seat] = min(s.big_blind, bb.stack + bb.bet + ante - ante)
    acts = [h for h in s.history if h[0] == "preflop"]
    for street, seat, a, amt in acts[:upto]:
        if a in ("call", "raise"):
            bet[seat] = amt
    return [bet[p.seat] / s.big_blind for p in order]


# Under SHORT_BB a 2x open is a trap: the players who make it (the bots) open these, and
# everyone reads a short 2x open as this range. (Only QQ+ / AK was too narrow: against it
# even KK folds a re-jam, and AQs blocks the opener's own hands into a fold.)
TRAP_OPENS = {"AA", "KK", "QQ", "JJ", "TT", "AKs", "AKo", "AQs"}


def open_range(pos, eff_bb):
    """The hands a player opens 2x with (169 weights), as the others read it: under
    SHORT_BB a trap (it's push or fold, and only the biggest hands min-raise); otherwise
    about your sims' open frequency for the position."""
    names = I.exact()[0]
    if eff_bb < SHORT_BB:
        return [1.0 if h in TRAP_OPENS else 0.0 for h in names]
    from bots.contender8 import RFI
    from bots.preflop8 import TOP
    share = RFI.get(pos, 0.25)
    return [1.0 if TOP[h] <= share else 0.0 for h in names]


def icm_spot(s):
    """How ICM reads this preflop decision, or None (no all-in decision to make):
    {"kind": "jam" | "call", "stacks", "me", "jammer", "kw": solve_jam extras}."""
    if s.street != "preflop":
        return None
    order, st, me = _table(s)
    n = len(order)
    acts = [(i, seat, a, amt) for i, (street, seat, a, amt) in
            enumerate(h for h in s.history if h[0] == "preflop") if a != "fold"]
    idx_of = {p.seat: k for k, p in enumerate(order)}
    eff = _eff_bb(s)
    game = _game_spot(order, st, me, acts, idx_of, s)
    if game is not None:
        return game
    if not acts:
        if me == n - 1 or eff > JAM_PRACTICAL_BB:
            return None
        return {"kind": "jam", "stacks": st, "me": me, "jammer": None, "kw": {}, "eff": eff}
    if len(acts) == 1 and acts[0][2] == "raise":
        j = idx_of[acts[0][1]]
        opener = order[j]
        if opener.all_in or opener.stack == 0:          # a lone all-in: call or fold
            return {"kind": "call", "stacks": st, "me": me, "jammer": j, "kw": {}, "eff": eff}
        if eff > JAM_PRACTICAL_BB or s.my_stack <= 0:
            return None
        # Facing an open: re-jam or not (calls and small 3-bets are your sims' game). The
        # opener answers first: they're priced in and hold a real range, and the players
        # still behind call only when the opener lets it go (they'd be caught in between).
        responders = [j] + [k for k in range(me + 1, n) if not order[k].folded and k != me]
        kw = {"posts": tuple(_posts(s, order)), "responders": tuple(responders), "fold_to": j,
              "priors": {j: tuple(open_range(opener.position, min(st[j], max(
                  st[k] for k in range(n) if k != j))))}}
        return {"kind": "jam", "stacks": st, "me": me, "jammer": None, "kw": kw, "eff": eff,
                "opener": j}
    if (len(acts) == 2 and acts[0][2] == "raise" and acts[1][2] == "raise" and
            idx_of[acts[0][1]] == me):
        k = idx_of[acts[1][1]]                           # you opened; k jams over it
        if not (order[k].all_in or order[k].stack == 0):
            return None
        responders = [me] + [r for r in range(k + 1, n) if r != k and r != me]
        kw = {"posts": tuple(_posts(s, order, upto=acts[1][0])), "responders": tuple(responders),
              "fold_to": me, "priors": {me: tuple(open_range(order[me].position, eff))}}
        return {"kind": "call", "stacks": st, "me": me, "jammer": k, "kw": kw, "eff": eff}
    return None


# ------------------------------------------------------------ games with traps
# Two short-stack games solved under ICM with their traps (icm_study.solve_sb / solve_open):
# the small blind vs the big blind (fold, limp, raise to 3 bb, jam; the big blind checks,
# iso-raises to 4 bb or jams over a limp; folds, calls or jams over the raise) up to
# GAME_SB_BB effective, and a short player first in from any other seat (fold, raise to
# 2 bb, jam; the others re-jam or fold, the big blind may call) under GAME_OPEN_BB. Every
# decision inside them is a "mix" spot (the solve's frequencies and values for each action)
# or, facing an all-in, a "call" spot read from the same solve.

GAME_SB_BB = JAM_PRACTICAL_BB
GAME_OPEN_BB = SHORT_BB
SB_GAME_BB = GAME_SB_BB             # (the first version's name)

ACTION_LABEL = {"fold": "Fold", "check": "Check", "call": "Call", "limp": "Limp", "jam": "Jam",
                "raise": f"Raise to {I.SB_RAISE_TO:g}", "iso": f"Raise to {I.SB_ISO_TO:g}",
                "open": f"Raise to {I.OPEN_TO:g}", "3bet": "3-bet"}
# each decision's raise that isn't all-in: (its action name, size in bb)
NODE_RAISE = {"sb": ("raise", I.SB_RAISE_TO), "bb_vs_limp": ("iso", I.SB_ISO_TO),
              "open": ("open", I.OPEN_TO), "bb_vs_raise": ("3bet", I.BB_3BET_TO)}
# Top hands get every option: traps, small raises, jams. When several are close (within
# this share of the prize pool), the bots mix them and your grade counts any of them right.
PREMIUM_SHARE = 0.05
PREMIUM_MARGIN = 0.002


def node_raise(node):
    """(action, size bb) of a decision's raise that isn't all-in, or None."""
    if node in NODE_RAISE:
        return NODE_RAISE[node]
    if node.startswith("vs_open:"):
        return ("3bet", I.THREE_BET_TO)
    return None


def action_label(a, node=None):
    if a == "3bet" and node is not None and node_raise(node):
        return f"Raise to {node_raise(node)[1]:g}"
    return ACTION_LABEL[a]


def premium(hand):
    from bots.preflop8 import TOP
    return TOP[hand] <= PREMIUM_SHARE


def _game_spot(order, st, me, acts, idx_of, s):
    """The decision as part of one of the games, or None."""
    n = len(order)
    if n < 2:
        return None
    sb, bb = n - 2, n - 1
    seq = [(idx_of[seat], a) for _, seat, a, _ in acts]
    allin = [order[idx_of[seat]].all_in or order[idx_of[seat]].stack == 0
             for _, seat, _, _ in acts]
    base = {"stacks": st, "me": me, "kw": {}}
    if me in (sb, bb) and all(k in (sb, bb) for k, _ in seq):
        ante = min(I.ANTE, st[bb])
        eff = min(st[sb], st[bb] - ante)
        if eff < GAME_SB_BB and st[sb] >= 2.0 and st[bb] - ante >= 2.0:
            g = dict(base, game="sb", eff=eff)
            if me == sb and not seq:
                return dict(g, kind="mix", node="sb", jammer=None)
            if me == bb and seq == [(sb, "call")]:
                return dict(g, kind="mix", node="bb_vs_limp", jammer=None)
            if me == bb and seq == [(sb, "raise")]:
                if allin[0]:
                    return dict(g, kind="call", node="bb_vs_jam", jammer=sb)
                return dict(g, kind="mix", node="bb_vs_raise", jammer=None)
            if me == sb and len(seq) == 2 and seq[1] == (bb, "raise"):
                if seq[0] == (sb, "call"):
                    if allin[1]:
                        return dict(g, kind="call", node="sb_vs_jam_limp", jammer=bb)
                    return dict(g, kind="mix", node="sb_vs_iso", jammer=None)
                if seq[0] == (sb, "raise") and not allin[0]:
                    if allin[1]:
                        return dict(g, kind="call", node="sb_vs_jam_raise", jammer=bb)
                    return dict(g, kind="mix", node="sb_vs_3bet", jammer=None)
            if (me == bb and len(seq) == 3 and seq[1] == (bb, "raise") and not allin[1] and
                    seq[2] == (sb, "raise") and allin[2]):
                if seq[0] == (sb, "call"):
                    return dict(g, kind="call", node="bb_vs_rejam_iso", jammer=sb)
                if seq[0] == (sb, "raise") and not allin[0]:
                    return dict(g, kind="call", node="bb_vs_4bet", jammer=sb)
        return None
    if not seq:
        if me < sb:
            eff = min(st[me], max(st[k] for k in range(n) if k != me))
            if eff < GAME_OPEN_BB and st[me] >= I.OPEN_MIN_BB:
                return dict(base, game="open", opener=me, eff=eff, kind="mix", node="open",
                            jammer=None)
        return None
    o, first = seq[0]
    if o < sb and first == "raise":
        eff = min(st[o], max(st[k] for k in range(n) if k != o))
        if eff < GAME_OPEN_BB and st[o] >= I.OPEN_MIN_BB:
            g = dict(base, game="open", opener=o, eff=eff)
            if len(seq) == 1 and me > o:
                if allin[0]:
                    return dict(g, kind="call", node=f"vs_jam:{me}", jammer=o)
                return dict(g, kind="mix", node=f"vs_open:{me}", jammer=None)
            if len(seq) == 2 and me == o and seq[1][1] == "raise" and not allin[0]:
                if allin[1]:
                    return dict(g, kind="call", node="open_vs_rejam", jammer=seq[1][0])
                return dict(g, kind="mix", node="open_vs_3bet", jammer=None, three=seq[1][0])
            if (len(seq) == 3 and seq[1][0] == me and seq[1][1] == "raise" and not allin[1]
                    and seq[2] == (o, "raise") and allin[2]):
                return dict(g, kind="call", node=f"vs_4bet:{me}", jammer=o)
    return None


_GAMES = OrderedDict()


def solve_game(sp, pays, chips=False):
    """The spot's game, solved (cached: the bots, your grade, the chart and the coach all
    ask about the same one)."""
    st = sp["stacks"]
    key = (sp["game"], tuple(round(x, 4) for x in st), sp.get("opener"), tuple(pays), chips)
    with _LOCK:
        hit = _GAMES.get(key)
    if hit is None:
        value = I.chips_value if chips else I.icm_value(list(pays))
        if sp["game"] == "sb":
            hit = I.solve_sb(list(st), value)
        else:
            hit = I.solve_open(list(st), value, sp["opener"])
        with _LOCK:
            _GAMES[key] = hit
            while len(_GAMES) > 64:
                _GAMES.popitem(last=False)
    return hit


def solve_sb(st, pays, chips=False):
    """The SB vs BB game for these stacks (the comparison pack, the first version's name)."""
    return solve_game({"game": "sb", "stacks": list(st)}, pays, chips)


_GAME_RES = OrderedDict()


def game_res(sp, pays, chips=False):
    """What a game spot's grade, coach and bots read: a "mix" spot's node ({"mix", "ev"}),
    or for a "call" spot, call_from's result against the jam range in the game (cached:
    the bots ask at every action)."""
    me = sp.get("me", sp.get("hero"))
    key = (sp["game"], tuple(round(x, 4) for x in sp["stacks"]), sp.get("opener"),
           tuple(pays), chips, sp["kind"], sp["node"], sp.get("jammer"), me)
    with _LOCK:
        hit = _GAME_RES.get(key, False)
    if hit is not False:
        return hit
    out = _game_res(sp, pays, chips)
    with _LOCK:
        _GAME_RES[key] = out
        while len(_GAME_RES) > 256:
            _GAME_RES.popitem(last=False)
    return out


def _game_res(sp, pays, chips=False):
    game = solve_game(sp, pays, chips)
    if game is None:
        return None
    if sp["kind"] == "mix":
        node = game["nodes"].get(sp["node"])
        return node if node and node["mix"] else None
    names = I.exact()[0]
    st = sp["stacks"]
    n = len(st)
    s, b = n - 2, n - 1
    value = I.chips_value if chips else I.icm_value(list(pays))
    nodes, rg = game["nodes"], game["ranges"]

    def gain(node, act, base):
        ev = nodes[node]["ev"]
        return {h: (ev[h][act] - ev[h][base]) if h in ev and act in ev[h] else 0.0
                for h in names}

    def posts(**kv):
        p = [0.0] * n
        p[s], p[b] = 0.5, 1.0
        for k, v in kv.items():
            p[{"s": s, "b": b}.get(k, k) if isinstance(k, str) else k] = v
        return p
    node = sp["node"]
    if node == "bb_vs_jam":
        spot, jr, g = I.Spot(st, value, s), rg["jam"], gain("sb", "jam", "fold")
    elif node == "sb_vs_jam_limp":
        spot = I.Spot(st, value, b, posts=posts(s=1.0, b=1.0), responders=[s], fold_to=b)
        jr, g = rg["bb_jam_limp"], gain("bb_vs_limp", "jam", "check")
    elif node == "sb_vs_jam_raise":
        spot = I.Spot(st, value, b, posts=posts(s=I.SB_RAISE_TO, b=1.0), responders=[s],
                      fold_to=b)
        jr, g = rg["bb_jam_raise"], gain("bb_vs_raise", "jam", "fold")
    elif node == "bb_vs_rejam_iso":
        spot = I.Spot(st, value, s, posts=posts(s=1.0, b=I.SB_ISO_TO), responders=[b],
                      fold_to=s)
        jr, g = rg["limp_rejam"], gain("sb_vs_iso", "jam", "fold")
    elif node == "bb_vs_4bet":
        spot = I.Spot(st, value, s, posts=posts(s=I.SB_RAISE_TO, b=I.BB_3BET_TO), responders=[b],
                      fold_to=s)
        jr, g = rg["sb_4bet"], gain("sb_vs_3bet", "jam", "fold")
    elif node.startswith("vs_4bet:"):
        o, i = game["opener"], sp.get("me", sp.get("hero"))
        p = posts()
        p[o], p[i] = I.OPEN_TO, I.THREE_BET_TO
        spot = I.Spot(st, value, o, posts=p, responders=[i], fold_to=o)
        jr, g = rg["fourbet"], gain("open_vs_3bet", "jam", "fold")
    elif node.startswith("vs_jam:"):
        o = game["opener"]
        spot, jr, g = I.Spot(st, value, o), rg["jam"], gain("open", "jam", "fold")
    elif node == "open_vs_rejam":
        o, i = game["opener"], sp["jammer"]
        p = posts()
        p[o] = I.OPEN_TO
        spot = I.Spot(st, value, i, posts=p, responders=[o], fold_to=o)
        jr, g = rg["rejam"][i], gain(f"vs_open:{i}", "jam", "fold")
    else:
        return None
    return I.call_from({"spot": spot, "jam_range": jr, "gain": g}, sp.get("me", sp.get("hero")))


def node_action(node, move, s):
    """Your move as one of the decision's actions (None: not one of them)."""
    a = move[0]
    if a == "fold":
        return "fold"
    if a == "check":
        return "check"
    if a == "call":
        if s.to_call and s.my_stack <= s.to_call:
            return "jam"         # calling puts the whole stack in: the all-in here
        return "limp" if node == "sb" else "call"
    if a == "raise":
        if move[1] >= s.max_raise_to - 1:
            return "jam"
        nr = node_raise(node)
        return nr[0] if nr else None
    return None


def _raise_to(s, bb):
    if not s.min_raise_to:
        return None
    to = max(s.min_raise_to, int(round(bb * s.big_blind)))
    return to if to < s.max_raise_to else None


def game_sizes(s):
    """The raise buttons at a game decision (besides all-in), or None (not a game spot)."""
    sp = icm_spot(s)
    if sp is None or sp["kind"] != "mix":
        return None
    table = POLICY.get("table")
    node = sp["node"]
    nr = node_raise(node)
    if nr is None:
        return []
    if table is not None:
        res = game_res(sp, table.pays(len(sp["stacks"])))
        if res is None or not any(nr[0] in m for m in res["mix"].values()):
            return []
    to = _raise_to(s, nr[1])
    return [to] if to else []


def sb_raise_to(s):
    """The small blind's 3 bb raise in chips, or None."""
    if (s.my_stack + s.my_bet) / s.big_blind < I.SB_RAISE_MIN_BB:
        return None
    return _raise_to(s, I.SB_RAISE_TO)


def _pick(mix, rng=None):
    """An action from a mix (by random: the bots mix their traps)."""
    x = (rng or random).random()
    for a, f in mix.items():
        x -= f
        if x < 0:
            return a
    return max(mix, key=mix.get)


def premium_mix(mix, ev, pool):
    """A top hand's mix at the table: every option within PREMIUM_MARGIN of the best gets
    played some of the time (traps, small raises, jams), so big hands can't be read."""
    if not ev:
        return mix
    best = max(ev.values())
    close = [a for a in ev if a != "fold" and ev[a] >= best - PREMIUM_MARGIN * pool]
    if len(close) < 2:
        return mix
    w = {a: mix.get(a, 0.0) + 0.15 for a in close}
    t = sum(w.values())
    return {a: x / t for a, x in w.items()}


def _move_for(action, node, s):
    if action == "fold":
        return ("fold", 0) if s.to_call else ("check", 0)
    if action == "check":
        return ("check", 0)
    if action in ("call", "limp"):
        return ("call", 0)
    if action == "jam":
        return ("raise", s.max_raise_to)
    nr = node_raise(node)
    to = _raise_to(s, nr[1]) if nr else None
    return ("raise", to or s.max_raise_to)


def game_layer(s, sp, pays, move):
    """The bots inside the games."""
    from bots.preflop8 import TOP
    hand = I.hand_class(*s.hole)
    if sp["kind"] == "mix":
        n = len(sp["stacks"])
        # (a clear fold with players behind: no solve needed, the phone stays quick)
        if sp["node"] == "open" and TOP[hand] > 0.35 and n - 1 - sp["me"] >= 3:
            return ("fold", 0)
        res = game_res(sp, pays)
        if res is None or hand not in res["mix"]:
            return None
        mix = res["mix"][hand]
        if premium(hand):
            mix = premium_mix(mix, res["ev"].get(hand, {}), sum(pays))
        return _move_for(_pick(mix), sp["node"], s)
    cf = game_res(sp, pays)
    if cf is None:
        return None
    return ("call", 0) if cf["gain"][hand] > 0 else ("fold", 0)


def game_chart(s):
    """For the chart: a game decision's exact strategy, {hand: {move: freq}}, or None."""
    table = POLICY.get("table")
    if table is None:
        return None
    sp = icm_spot(s)
    if sp is None or sp["kind"] != "mix":
        return None
    res = game_res(sp, table.pays(len(sp["stacks"])))
    if res is None:
        return None
    out = {}
    for h, mix in res["mix"].items():
        cell = {}
        for a, f in mix.items():
            mv = _move_for(a, sp["node"], s)
            cell[mv] = cell.get(mv, 0.0) + f
        out[h] = cell
    return out


sb_chart = game_chart


def mix_verdict(spot, right, g):
    """The grade's one-liner at a game decision."""
    res = spot["res"]
    mix = res["mix"][spot["hand"]]
    node = spot.get("node")
    plays = " / ".join(f"{action_label(a, node).lower()} {100 * f:.0f}%" for a, f in
                       sorted(mix.items(), key=lambda kv: -kv[1]) if f >= 0.05)
    if right:
        return f"ICM: {action_label(spot['answer'], node)} {spot['hand']} is right ({plays})"
    return (f"ICM: {action_label(spot['best'], node)} {spot['hand']} was best ({plays}); that "
            f"cost {I.money_k(abs(g))}")


sb_verdict = mix_verdict


def _range_mix(game, node, weights):
    """How a range (per-hand weights) plays at a decision: {action: share}."""
    names = I.exact()[0]
    from bots.preflop8 import COMBOS
    mix = game["nodes"][node]["mix"]
    tot, out = 0.0, {}
    for k, h in enumerate(names):
        w = COMBOS[h] * (weights[k] if weights is not None else 1.0)
        if w <= 0 or h not in mix:
            continue
        tot += w
        for a, f in mix[h].items():
            out[a] = out.get(a, 0.0) + w * f
    return {a: v / tot for a, v in out.items()} if tot else {}


def game_info(spot):
    """The coach's text for a game decision: what the decision is, and how the others play
    around it. [(heading or None, [lines])]."""
    game = solve_game(spot, spot["pays"])
    if game is None:
        return []
    st, me = spot["stacks"], spot["hero"]
    n = len(st)
    pos = I.positions(n)
    node = spot["node"]
    L = lambda m: "  \u00b7  ".join(f"{action_label(a, node).lower()} {100 * f:.0f}%"  # noqa
                                     for a, f in sorted(m.items(), key=lambda kv: -kv[1])
                                     if f >= 0.005)
    sh = lambda w: 100 * I._share_w(w)                                          # noqa: E731
    rg = game["ranges"]
    out = []
    if game["game"] == "sb":
        s, b = n - 2, n - 1
        eff = f"{game['eff']:.1f} bb effective"
        raise_txt = f", raise to {I.SB_RAISE_TO:g} bb" if game["can_raise"] else ""
        iso_txt = f", raise to {I.SB_ISO_TO:g} bb" if game["can_iso"] else ""
        desc = {
            "sb": f"Folded to you in the small blind ({eff} against the {pos[b]}, "
                  f"{st[b]:g} bb). You can fold, limp{raise_txt} or jam; the big blind "
                  f"answers a limp by checking{iso_txt} or jamming.",
            "bb_vs_limp": f"The small blind ({st[s]:g} bb) limped. You can check{iso_txt} or "
                          f"jam. At these stacks its limps mix traps (big hands hoping you "
                          f"jam) with hands too weak to jam.",
            "sb_vs_iso": f"You limped and the big blind raised to {I.SB_ISO_TO:g} bb. Fold, "
                         f"call (the pot goes to the flop) or jam.",
            "bb_vs_raise": f"The small blind ({st[s]:g} bb) raised to {I.SB_RAISE_TO:g} bb. "
                           f"Fold, call" + (f", 3-bet to {I.BB_3BET_TO:g} bb" if game["can_3b"]
                                            else "") + " or jam: its raises mix traps and "
                           f"strong hands with some bluffs.",
            "sb_vs_3bet": f"You raised to {I.SB_RAISE_TO:g} bb and the big blind 3-bet to "
                          f"{I.BB_3BET_TO:g} bb. Fold, call (the pot goes to the flop) or jam."}[node]
        out.append((None, [desc]))
        if node == "sb":
            out.append(("How the big blind answers", [
                "Over a limp: " + L(_range_mix(game, "bb_vs_limp", None)) + ".",
                *(["Over the raise: " + L(_range_mix(game, "bb_vs_raise", None)) + "."]
                  if game["can_raise"] else []),
                f"Against a jam: calls {sh(rg['bb_call_jam']):.0f}% of hands."]))
            out.append(("Your whole range here", [
                f"Jam {sh(rg['jam']):.0f}%  ·  limp {sh(rg['limp']):.0f}%" +
                (f"  ·  raise {sh(rg['raise']):.0f}%" if game["can_raise"] else "") +
                f"  ·  fold {max(0.0, 100 - sh(rg['jam']) - sh(rg['limp']) - sh(rg['raise'])):.0f}% "
                f"of hands. Limps and raises mix traps with weaker hands, so the big blind "
                f"can't attack them freely."]))
        elif node == "bb_vs_limp":
            out.append(("The small blind's limps", [
                f"It limps {sh(rg['limp']):.0f}% of hands. Against your raise to "
                f"{I.SB_ISO_TO:g} bb those limps " + L(_range_mix(game, "sb_vs_iso", rg["limp"]))
                + "." if game["can_iso"] else f"It limps {sh(rg['limp']):.0f}% of hands.",
                "Against your jam: " + L(_range_mix(game, "sb_vs_jam_limp", rg["limp"])) + "."]))
        elif node == "sb_vs_iso":
            out.append(("The big blind's raise", [
                f"It raises over a limp with {sh(rg['bb_iso']):.0f}% of hands. Against your "
                f"jam it " + L(_range_mix(game, "bb_vs_rejam_iso", rg["bb_iso"])) + "."]))
        elif node == "sb_vs_3bet":
            out.append(("The big blind's 3-bet", [
                f"It 3-bets small with {sh(rg['bb_3bet']):.0f}% of hands (traps and bluffs). "
                f"Against your jam it " + L(_range_mix(game, "bb_vs_4bet", rg["bb_3bet"])) + "."]))
        elif node == "bb_vs_raise":
            out.append(("The small blind's raise", [
                f"It raises to {I.SB_RAISE_TO:g} bb with {sh(rg['raise']):.0f}% of hands. "
                f"Against your jam it " + L(_range_mix(game, "sb_vs_jam_raise", rg["raise"])) + "."]))
    else:
        o = game["opener"]
        if node == "open":
            out.append((None, [
                f"Folded to you with {spot.get('eff', st[me]):.1f} bb effective. You can fold, "
                f"raise to {I.OPEN_TO:g} bb or jam. Behind you each player re-jams or folds "
                f"(the big blind may also call), and you call or fold a re-jam. A min-raise "
                f"with a big hand is a trap: it invites a re-jam."]))
            lines = []
            for i in game["resp"]:
                m = _range_mix(game, f"vs_open:{i}", None)
                lines.append(f"{pos[i]} ({st[i]:g} bb) against the raise: " + L(m) + ".")
            out.append(("How the others answer your raise", lines))
            out.append(("Your whole range here", [
                f"Raise {sh(rg['open']):.0f}%  ·  jam {sh(rg['jam']):.0f}%  ·  fold "
                f"{max(0.0, 100 - sh(rg['open']) - sh(rg['jam'])):.0f}% of hands."]))
        elif node == "open_vs_3bet":
            i = spot.get("three", game["resp"][-1])
            out.append((None, [
                f"You raised to {I.OPEN_TO:g} bb and {pos[i]} 3-bet to {I.THREE_BET_TO:g} bb. "
                f"Fold, call (the pot goes to the flop) or jam."]))
            out.append((f"{pos[i]}'s 3-bet", [
                f"It 3-bets small with {sh(rg['threebet'][i]):.0f}% of hands; against your jam "
                f"it " + L(_range_mix(game, f"vs_4bet:{i}", rg["threebet"][i])) + "."]))
        else:
            big = me == n - 1
            out.append((None, [
                f"{pos[o]} ({st[o]:g} bb) raised to {I.OPEN_TO:g} bb. You can fold or jam"
                + (f", call (a trap: you close the action) or 3-bet to {I.THREE_BET_TO:g} bb"
                   if big else "") + ". Short raises like this mix traps (big hands that want "
                f"you to jam) with good hands." + ("" if big else " (With players still to act "
                                                   "behind you, flatting invites a squeeze: it's "
                                                   "jam or fold here.)")]))
            out.append((f"{pos[o]}'s raise", [
                f"It raises {sh(rg['open']):.0f}% of hands here; against a re-jam it " +
                L(_range_mix(game, f"open_vs_rejam:{me}", rg["open"])) + "."]))
    if not game.get("exact"):
        out.append(("Solve", [
            "Solved the quick way (fictitious play): rare lines here (a 4-bet, a jam after "
            "a limp) can be off by 1-2% of the prize pool. Install numpy for the exact solve: "
            "py -3.13 -m pip install numpy"]))
    return out


_CALLS = OrderedDict()


def _call_from(res, me):
    """I.call_from, cached per solve (the bots ask at every action)."""
    key = (id(res), me)
    with _LOCK:
        hit = _CALLS.get(key)
    if hit is None or hit[0] is not res:
        hit = (res, I.call_from(res, me))
        with _LOCK:
            _CALLS[key] = hit
            while len(_CALLS) > 128:
                _CALLS.popitem(last=False)
    return hit[1]


def icm_layer(s, move):
    """At a final table with ICM on: every all-in decision by the ICM equilibrium."""
    table = POLICY.get("table")
    if not POLICY["on"] or not POLICY.get("icm", True) or table is None:
        return move
    sp = icm_spot(s)
    if sp is None:
        return move
    h = I.exact()[1][I.hand_class(*s.hole)]
    pays = table.pays(len(sp["stacks"]))
    if sp.get("game"):
        out = game_layer(s, sp, pays, move)
        return move if out is None else out
    if sp["kind"] == "call":
        res = solve(sp["stacks"], pays, sp["jammer"], **sp["kw"])
        cf = _call_from(res, sp["me"])
        # call when calling beats folding for this hand against the jam range: the same
        # number the coach's verdict and your ICM grade use (I.call_from)
        go = cf["gain"][I.hand_class(*s.hole)] > 0
        if go:
            return ("call", 0)
        return ("fold", 0) if s.to_call else move
    res = solve(sp["stacks"], pays, sp["me"], **sp["kw"])
    jam = move[0] == "raise" and move[1] >= s.max_raise_to - 1
    if res["jam_range"][h] >= 0.5:
        if sp["kw"] or sp["eff"] < SHORT_BB or jam or move[0] == "fold":
            # re-jam, push, the sims' own jam, or a fold the solve jams: your sims assume
            # even stacks; a big stack against short blinds jams wider (coach_audit.py)
            return ("raise", s.max_raise_to)
        return move                               # 12 bb+: the sims' open is fine too
    if jam:
        return _not_a_jam(s, sp)                  # ICM: not a jam
    if not sp["kw"] and sp["eff"] < SHORT_BB and move[0] == "raise" and \
            I.hand_class(*s.hole) not in TRAP_OPENS:
        return ("fold", 0)                        # push / fold: not a push, so a fold
    if sp["kw"] and sp["eff"] < SHORT_BB and move[0] == "call" and s.position != "BB":
        return ("fold", 0)                        # short vs an open: jam or fold, no flat
    return move


def _not_a_jam(s, sp):
    """A hand your sim jams but ICM won't: the SB first in completes instead; facing an
    open, the BB calls, and so does anyone with 12 bb or more (folding TT while the sim
    flats 66 made no sense, solver/preflop_audit.py); anyone else folds."""
    first = not any(a in ("raise", "call") for st, _, a, _ in s.history if st == "preflop")
    if s.position == "SB" and first and s.to_call:
        return ("call", 0)
    if sp["kw"] and s.to_call and (s.position == "BB" or sp["eff"] >= SHORT_BB):
        return ("call", 0)     # facing an open: flat (under 12 bb, only the BB: jam / fold)
    return ("fold", 0) if s.to_call else ("check", 0)


PRICE_GUARD = 0.10       # preflop (final table): a call needing this little is never folded
POST_PRICE_GUARD = 0.06  # after the flop (every mode): the same, at this price
PREMIUMS = {"AA", "KK"}  # never folded preflop (every mode)


def live_pot(s):
    """The pot we can win: a bet bigger than everything we have only counts up to it (a
    224 bb jam into our 24 bb is a 24 bb bet, not 224 bb of pot odds)."""
    mine = s.my_stack + s.my_bet
    excess = sum(max(0, p.bet - mine) for p in s.players if p.seat != s.my_seat)
    return s.pot - excess


def _icm_decides(s):
    return (POLICY["on"] and POLICY.get("icm", True) and POLICY.get("table") is not None and
            icm_spot(s) is not None)


def price_guard(s, move):
    """Folds no hand should make (found by solver/ft_audit.py): AA / KK preflop; preflop at
    a final table, a call any two cards beat (1 bb more into 11 bb after a short stack's
    tiny all-in); after the flop, a call needing 6% or less (a min-raise all-in on the
    river), 10% or less with better than half of all hands, 40% or less with a hand under
    4% of hands beat (trips, sets: see _beaten_by)."""
    if move[0] != "fold" or not s.to_call:
        return move
    to_call = min(s.to_call, s.my_stack)          # (all-in for less: what you put in)
    need = to_call / (live_pot(s) + to_call)
    icm = post_icm(s)
    if icm is not None:          # (after the flop at a final table: the prize-money price)
        need = max(need, icm.need(s.to_call)[1])
    if s.street == "preflop":
        if I.hand_class(*s.hole) in PREMIUMS:
            if s.min_raise_to and s.max_raise_to <= 40 * s.big_blind:
                return ("raise", s.max_raise_to)      # short enough: get it in
            return ("call", 0)
        if POLICY["on"] and need <= PRICE_GUARD and not _icm_decides(s):
            return ("call", 0)
    elif need <= POST_PRICE_GUARD:
        return ("call", 0)
    elif need <= STRONG_NEED:
        beaten = _beaten_by(s)
        if beaten < STRONG_BEATEN or (need <= CHEAP_NEED and beaten < 0.5):
            return ("call", 0)
    return move


STRONG_BEATEN = 0.06   # after the flop: a hand fewer than 6% of random hands beat...
STRONG_NEED = 0.40     # ... doesn't fold to a bet needing 40% or less (heads-up or 3-way)
CHEAP_NEED = 0.10      # and a hand better than half of all hands calls a bet this cheap


def _beaten_by(s):
    """Share of all two-card hands that beat ours on this board (1.0 when it doesn't
    apply: 4+ players in, or before the flop). Trips with a weak kicker on A-A-x-x, a set
    facing a check-raise: the bots folded those (solver/ft_audit.py, 766K hands)."""
    import itertools
    from cards import card, evaluate
    live = [p for p in s.players if p.seat != s.my_seat and not p.folded]
    if len(s.board) < 3 or len(live) > 2:
        return 1.0
    used = set(s.hole) | set(s.board)
    deck = [card(r + x) for r in "23456789TJQKA" for x in "cdhs" if r + x not in used]
    board = [card(c) for c in s.board]
    me = evaluate([card(c) for c in s.hole] + board)
    n = beat = 0
    for a, b in itertools.combinations(deck, 2):
        n += 1
        beat += evaluate([a, b] + board) > me
    return beat / n if n else 1.0


ALLIN_CALL_SHARE = 0.12  # calling is all-in: the last raiser assumed on the top 12%
ALLIN_CALL_EXTRA = 0.05  # ... and this much less equity per other player still in


def allin_for_less(s, move):
    """Calling puts us all-in (a short stack facing raises): nothing is left to play for,
    so call exactly when our equity beats the price. The bots' line logic didn't know it:
    it folded AK with 6 bb facing an open and a 3-bet while 'calling' A5s as a 4-bet bluff
    (solver/preflop_audit.py). Left to ICM where ICM decides (a short stack may fold to
    ladder up)."""
    if s.street != "preflop" or not s.to_call or s.my_stack > s.to_call or \
            move[0] not in ("fold", "call", "raise"):
        return move
    raisers = [seat for st, seat, a, _ in s.history if st == "preflop" and a == "raise"]
    last = next((p for p in s.players if raisers and p.seat == raisers[-1]), None)
    if last is None or last.all_in:
        return move      # facing an all-in: your sims' calling ranges (and ICM) decide
    if _icm_decides(s):
        return move
    from bots.contender import eq_vs
    from bots.shortstack import hand_type
    from cards import card
    live = [p for p in s.players if p.seat != s.my_seat and not p.folded]
    if not live:
        return move
    need = s.my_stack / (live_pot(s) + s.my_stack)
    h = hand_type(card(s.hole[0]), card(s.hole[1]))
    eq = eq_vs(h, ALLIN_CALL_SHARE) - ALLIN_CALL_EXTRA * (len(live) - 1)
    return ("call", 0) if eq > need + 0.02 else ("fold", 0)


def iso_to_bb(eff_bb, limpers=1):
    """A raise over limpers: 3 bb up to 25 bb deep, 3.5 up to 60, 4 deeper; 1 bb more per
    extra limper. (Several of the Monker sims raise 29% pot here, which comes out as a
    2 bb min-raise: a tree setting, not a strategy.)"""
    base = 3.0 if eff_bb <= 25 else 3.5 if eff_bb <= 60 else 4.0
    return base + max(0, limpers - 1)


def sb_open_bb(eff_bb):
    """The SB's open when it's folded to it: 3.5 bb from 40 bb deep, 3 bb from 25 (the
    Monker trees only have a 29%-pot raise there, a 2 bb raise; below 25 bb: as solved)."""
    return 3.5 if eff_bb >= 40 else 3.0 if eff_bb >= 25 else None


def iso_size(s, move):
    """A raise over a limp (every mode): at least the iso size above. The SB's open (folded
    to it): the size above."""
    if s.street != "preflop" or move[0] != "raise" or move[1] >= s.max_raise_to - 1:
        return move
    acts = [a for st, _, a, _ in s.history if st == "preflop" and a != "fold"]
    if not acts and s.position == "SB" and len(s.players) > 2:
        size = sb_open_bb(_eff_bb(s))
        # (only a min-raise: the old trees' 29% raise; a sim with its own small SB open,
        # 2.2 bb in a 40% / 83% tree, keeps it)
        if size is None or move[1] >= size * s.big_blind or move[1] > 2.05 * s.big_blind:
            return move
        to = int(round(size * s.big_blind))
        return ("raise", max(s.min_raise_to, min(s.max_raise_to, to)))
    if not acts or any(a != "call" for a in acts):
        return move   # not a limped pot
    to = int(round(iso_to_bb(_eff_bb(s), len(acts)) * s.big_blind))
    if move[1] >= to:
        return move
    return ("raise", max(s.min_raise_to, min(s.max_raise_to, to)))


COMMIT_SHARE = 0.5   # a preflop raise putting in this much of the stack goes all-in


def commit_jam(s, move):
    """A preflop raise to half the effective stack or more (a 3-bet to 8 bb with 12 bb, a
    4-bet to 25 bb at 45 bb: the sims' fixed sizes at short stacks) is an all-in instead:
    nothing is left to play for, and raise-folding is the worst of both. (Trainer bots, in
    Play and Final table mode; found by solver/preflop_audit.py.)"""
    if move[0] != "raise" or s.street != "preflop" or not s.min_raise_to:
        return move
    if move[1] >= s.max_raise_to - 1:
        return move
    mine = s.my_stack + s.my_bet
    opp = max((p.stack + p.bet for p in s.players if p.seat != s.my_seat and not p.folded),
              default=mine)
    if move[1] >= COMMIT_SHARE * min(mine, opp):
        return ("raise", s.max_raise_to)
    return move


class PostICM:
    """Prize money after the flop at a final table: what calling a bet needs (ICM, not
    chips), and the risk premium against each opponent (how much more equity than chips
    an all-in against them needs). Outcomes are settled as if the hand ended now."""

    def __init__(self, s, pays):
        self.s, self.pays = s, list(pays)
        self.idx = {p.seat: k for k, p in enumerate(s.players)}
        self.stacks = [float(p.stack) for p in s.players]
        self.me = self.idx[s.my_seat]
        self._rp = {}

    def _value(self, st, who=None):
        return I.icm(st, self.pays)[self.me if who is None else who]

    def need(self, to_call, opp_seat=None):
        """(chip need, ICM need) to call `to_call` (capped at our stack) into the pot, the
        bettor `opp_seat` (default: the biggest bet) taking the pot when we fold."""
        s = self.s
        if opp_seat is None:
            opp_seat = max((p for p in s.players if p.seat != s.my_seat and not p.folded),
                           key=lambda p: p.bet).seat
        o, me = self.idx[opp_seat], self.me
        c = min(to_call, s.my_stack)
        refund = to_call - c
        pot = s.pot - refund
        chip = c / (pot + c) if pot + c > 0 else 0.0
        fold, win, lose = list(self.stacks), list(self.stacks), list(self.stacks)
        fold[o] += pot + refund
        win[me] += pot
        win[o] += refund
        lose[me] -= c
        lose[o] += pot + c + refund
        vf, vw, vl = self._value(fold), self._value(win), self._value(lose)
        span = vw - vl
        return chip, ((vf - vl) / span if span > 1e-9 else chip)

    def _premium(self, a, b):
        """Extra equity (over chips) player index a needs to call an all-in of the
        effective stack from b into the pot as it is now (0 or more)."""
        key = (a, b)
        if key not in self._rp:
            c = min(self.stacks[a], self.stacks[b])
            if c <= 0:
                self._rp[key] = 0.0
            else:
                P = self.s.pot
                fold, win, lose = list(self.stacks), list(self.stacks), list(self.stacks)
                fold[b] += P
                win[a] += P + c
                win[b] -= c
                lose[a] -= c
                lose[b] += P + c
                vf, vw, vl = (self._value(x, a) for x in (fold, win, lose))
                span = vw - vl
                icm_need = (vf - vl) / span if span > 1e-9 else 0.5
                self._rp[key] = max(0.0, icm_need - c / (P + 2 * c))
        return self._rp[key]

    def risk_premium(self, opp_seat):
        """Our risk premium against `opp_seat` (what we need extra to call their all-in)."""
        return self._premium(self.me, self.idx[opp_seat])

    def their_premium(self, opp_seat):
        """Theirs against us (what they need extra to call ours)."""
        return self._premium(self.idx[opp_seat], self.me)

    def pressure(self):
        """Ours minus theirs, against whoever still in the hand it's worst: > 0 means we
        carry the risk (bluff less), < 0 means they do (they can't call light)."""
        s = self.s
        live = [p.seat for p in s.players if p.seat != s.my_seat and not p.folded]
        return max((self.risk_premium(x) - self.their_premium(x) for x in live), default=0.0)

    def max_premium(self):
        """Our biggest risk premium against anyone still in the hand."""
        s = self.s
        return max((self.risk_premium(p.seat) for p in s.players
                    if p.seat != s.my_seat and not p.folded), default=0.0)


def post_icm(s):
    """A PostICM for this decision at a final table (after the flop), else None."""
    table = POLICY.get("table")
    if not POLICY["on"] or not POLICY.get("icm", True) or table is None or s.street == "preflop":
        return None
    n = len(s.players)
    if n < 2:
        return None
    return PostICM(s, table.pays(n))


def with_open_policy(bot):
    """The bot, following the final table's preflop rules whenever they're on."""
    bot.icm_post = post_icm        # (after the flop: prize-money prices, see PostICM)
    act = bot.act
    def play(s):
        m0 = iso_size(s, act(s))
        m1 = commit_jam(s, m0)
        out = icm_layer(s, open_policy(s, m1))
        if m1 != m0 and out[0] == "fold" and s.to_call:
            out = ("call", 0)    # a big raise made all-in, which ICM won't: just call
        return legal(s, price_guard(s, allin_for_less(s, out)))
    bot.act = play
    return bot


def legal(s, move):
    """A raise when no raise is possible (all-in for less to call: a 1 bb stack facing an
    open) is a call."""
    if move[0] == "raise" and (not s.min_raise_to or move[1] < s.min_raise_to):
        if not s.min_raise_to:
            return ("call", 0) if s.to_call else ("check", 0)
        return ("raise", min(s.max_raise_to, s.min_raise_to))
    return move


# -- the ICM Coach for any decision of yours --

def coach_spot(s, table):
    """The ICM Coach's record for your decision: an all-in decision ("jam" / "call",
    solved when needed, see ensure_solved) or, for everything else, "info"."""
    order, st, me = _table(s)
    n = len(st)
    ante = [0.0] * n
    ante[n - 1] = min(I.ANTE, st[n - 1])
    out = {"stacks": list(st), "pays": list(table.pays(n)), "hero": me, "jammer": None,
           "hand": I.hand_class(*s.hole), "cards": list(s.hole), "answer": None,
           "ante": ante, "street": s.street, "state": s, "first_in": _first_in(s)}
    sp = icm_spot(s)
    if sp is None:
        live = [k for k, p in enumerate(order) if not p.folded and k != me]
        out.update(kind="info", live=live, to_call=s.to_call / s.big_blind,
                   pot=s.pot / s.big_blind, eff=_eff_bb(s), board=list(s.board))
        if s.street != "preflop" and len(s.players) >= 2:
            pic = PostICM(s, table.pays(len(s.players)))
            if s.to_call:
                out["need_chip"], out["need_icm"] = pic.need(s.to_call)
            out["press"] = pic.pressure()
        return out
    out.update(kind=sp["kind"], jammer=sp["jammer"], kw=sp["kw"], eff=sp["eff"],
               opener=sp.get("opener"), game=sp.get("game"), node=sp.get("node"))
    return out


def ensure_solved(spot):
    if spot["kind"] == "info" or "res" in spot:
        return spot
    st, pays, me, kw = spot["stacks"], spot["pays"], spot["hero"], spot.get("kw", {})
    if spot.get("game"):
        spot["res"] = game_res(spot, pays)
        spot["chip"] = game_res(spot, pays, chips=True)
        if spot["kind"] == "mix":
            spot["info"] = game_info(spot)
        return spot
    if spot["kind"] == "jam":
        spot["res"] = solve(st, pays, me, **kw)
        spot["chip"] = solve(st, pays, me, chips=True, **kw)
    else:
        j = spot["jammer"]
        spot["res"] = I.call_from(solve(st, pays, j, **kw), me)
        spot["chip"] = I.call_from(solve(st, pays, j, chips=True, **kw), me)
    return spot


# ------------------------------------------------------------ the leak tracker

LEAK_FILE = Path(__file__).resolve().parent / "leak_history.json"
ICM_KINDS = {"jam_first": "Jam first in", "rejam": "Re-jam over an open",
             "call": "Call an all-in"}


class LeakLog:
    """Your mistakes in one session: every blunder (what, where, what it cost), how often
    each kind of spot came up, and how often you got it wrong."""

    def __init__(self):
        self.entries = []        # {cat, cost, text, hand, spot}
        self.spots = Counter()   # spot type -> how many you played
        self.saved = False

    def spot(self, kind):
        self.spots[kind] += 1

    def add(self, cat, kind, cost, text, hand):
        self.entries.append({"cat": cat, "spot": kind, "cost": round(float(cost), 2),
                             "text": text, "hand": hand})

    def top(self, k=3):
        return sorted(self.entries, key=lambda e: -e["cost"])[:k]

    def by_type(self):
        """{leak: (times, spots of that kind, dollars lost)}, biggest first."""
        out = {}
        for e in self.entries:
            n, _, cost = out.get(e["cat"], (0, 0, 0.0))
            out[e["cat"]] = (n + 1, self.spots.get(e["spot"], 0), cost + e["cost"])
        return dict(sorted(out.items(), key=lambda kv: -kv[1][2]))

    def lost(self):
        return sum(e["cost"] for e in self.entries)


def save_session(ft, log):
    """Add this session to leak_history.json (once), for reviewing later."""
    if log.saved or not sum(log.spots.values()):   # (nothing played: nothing to keep)
        return
    log.saved = True
    import datetime
    rec = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "table": ft.table_no,
           "pool": round(sum(ft.pays9)), "hands": ft.hands,
           "finish": list(ft.over) if ft.over else None,
           "decisions": sum(log.spots.values()), "blunders": len(log.entries),
           "lost": round(log.lost(), 2), "top": log.top(3),
           "by_type": {k: list(v) for k, v in log.by_type().items()},
           "spots": dict(log.spots)}
    hist = load_history()
    hist.append(rec)
    try:
        LEAK_FILE.write_text(json.dumps(hist[-300:], indent=1))
    except OSError:
        pass


def load_history():
    try:
        return json.loads(LEAK_FILE.read_text())
    except (OSError, ValueError):
        return []


def all_time(hist, current=None):
    """Leak frequencies over every saved session (and the current one)."""
    spots, leaks = Counter(), {}
    recs = list(hist) + ([{"by_type": {k: list(v) for k, v in current.by_type().items()},
                           "spots": dict(current.spots)}] if current else [])
    for r in recs:
        spots.update(r.get("spots", {}))
        for cat, (n, _, cost) in r.get("by_type", {}).items():
            a, c = leaks.get(cat, (0, 0.0))
            leaks[cat] = (a + n, c + cost)
    return spots, dict(sorted(leaks.items(), key=lambda kv: -kv[1][1]))
