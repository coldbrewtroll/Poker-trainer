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

# The other seats get names: yours, and players from recent WSOP Circuit Main Event final
# tables (Hard Rock Tulsa, Choctaw, Harrah's Cherokee, Commerce, Horseshoe Las Vegas).
PLAYER_NAMES = [
    "Michael Perrone", "Maxwell Young", "Blake W", "Shannon S", "Jeremy B", "David J",
    "DJ Alexander", "Trung Pham", "Steven Snyder", "Matt Berkey", "Chance Kornuth",
    "Michael Phizicky", "Will Givens", "Eric Salazar", "David Shaw",
    "John Dollinger", "Russell Brooks", "Iman Alsaden", "Alex Cruz", "Alex Rindone",
    "Tuyen Dieu", "Toby Boas", "Artur Papazyan", "Luis Concha", "Adam Swan", "Phil Laak",
    "Matthew Blagg", "Eric Yanovsky", "Michael Persky", "Brek Schutten"]

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


def solve(st, pays, who, chips=False, iters=40, posts=None, responders=None, fold_to=None,
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
        hit = I.solve_jam(st, val, who, iters=iters, posts=list(posts) if posts else None,
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
    if spot["kind"] in ("info", "sb_limp"):
        return None
    if spot["kind"] == "sb":
        act = sb_action(move, s) if s is not None else None
        ensure_solved(spot)
        res = spot["res"]
        if act is None or res is None or act not in res["ev"][spot["hand"]]:
            return None
        ev, mix = res["ev"][spot["hand"]], res["mix"][spot["hand"]]
        tol = 1e-4 * sum(spot["pays"])          # ($100 in a $1M pool: a coin flip)
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
    return (g > 0) == (act == "go"), g


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
    sb = _sb_spot(order, st, me, acts, idx_of, s)
    if sb is not None:
        return sb
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


# ------------------------------------------------------------ small blind vs big blind
# Folded to a short small blind: fold, limp, a 3 bb raise or a jam, each hand by the ICM
# solve of the whole SB vs BB game (icm_study.solve_sb): strong hands limp or raise as
# traps when the big blind attacks enough to make it pay. The big blind answers from the
# same solve.

SB_GAME_BB = SHORT_BB     # under this effective (bb): the SB vs BB game


def _sb_spot(order, st, me, acts, idx_of, s):
    n = len(order)
    if n < 2:
        return None
    sb, bb = n - 2, n - 1
    if me not in (sb, bb) or any(idx_of[a[1]] not in (sb, bb) for a in acts):
        return None
    eff = min(st[sb], st[bb])
    if eff >= SB_GAME_BB or I.solve_sb is None:
        return None
    ante = min(I.ANTE, st[bb])
    if st[sb] < 2.0 or st[bb] - ante < 2.0:
        return None
    base = {"stacks": st, "me": me, "eff": eff, "kw": {}}
    seq = [(idx_of[seat], a) for _, seat, a, _ in acts]
    allin = [order[idx_of[seat]].all_in or order[idx_of[seat]].stack == 0
             for _, seat, _, _ in acts]
    if me == sb and not seq:
        return dict(base, kind="sb", jammer=None)
    if me == bb and seq == [(sb, "call")]:
        return dict(base, kind="sb_limp", jammer=None)
    if me == bb and seq == [(sb, "raise")]:
        if allin[0]:
            return dict(base, kind="call", jammer=sb, sb="jam")
        return dict(base, kind="jam", jammer=None, sb="raise", opener=sb)
    if me == sb and len(seq) == 2 and seq[1] == (bb, "raise") and allin[1]:
        if seq[0] == (sb, "call"):
            return dict(base, kind="call", jammer=bb, sb="limp")
        if seq[0] == (sb, "raise") and not allin[0]:
            return dict(base, kind="call", jammer=bb, sb="raise")
    return None


_SB = OrderedDict()


def solve_sb(st, pays, chips=False):
    """icm_study.solve_sb, cached."""
    key = (tuple(round(x, 4) for x in st), tuple(pays), chips)
    with _LOCK:
        hit = _SB.get(key)
    if hit is None:
        hit = I.solve_sb(list(st), I.chips_value if chips else I.icm_value(list(pays)))
        with _LOCK:
            _SB[key] = hit
            while len(_SB) > 64:
                _SB.popitem(last=False)
    return hit


def sb_res(sp, pays, chips=False):
    """The solve behind a spot of the SB vs BB game, shaped like solve_jam's result (a re-jam
    over the SB's raise) or call_from's (calling a jam), so the coach, your grade and the
    bots read it as usual. Kind "sb" (the SB first in): solve_sb's own result."""
    st = sp["stacks"]
    game = solve_sb(st, pays, chips)
    if sp["kind"] == "sb" or game is None:
        return game
    names = I.exact()[0]
    n = len(st)
    s, b = n - 2, n - 1
    value = I.chips_value if chips else I.icm_value(list(pays))
    node = sp["sb"]
    if node == "jam":                      # the BB facing the SB's jam
        spot = I.Spot(st, value, s)
        jam_gain = {h: game["ev"][h]["jam"] - game["ev"][h]["fold"] for h in names}
        return I.call_from({"spot": spot, "jam_range": game["ranges"]["jam"], "gain": jam_gain}, b)
    posts = [0.0] * n
    posts[s] = I.SB_RAISE_TO if node == "raise" else 1.0
    posts[b] = 1.0
    if node in ("limp", "raise") and sp["kind"] == "call":    # the SB facing the BB's jam
        spot = I.Spot(st, value, b, posts=posts, responders=[s], fold_to=b)
        jam_w = game["bb"]["vs_limp" if node == "limp" else "vs_raise"]
        jam_gain = {h: game["bb_ev"]["vs_" + node][h] for h in names}
        return I.call_from({"spot": spot, "jam_range": jam_w, "gain": jam_gain}, s)
    # the BB facing the SB's raise: jam or fold (a re-jam over an open, from the solve)
    spot = I.Spot(st, value, b, posts=posts, responders=[s], fold_to=s)
    gain = {h: game["bb_ev"]["vs_raise"][h] for h in names}
    prior = game["ranges"]["raise"]
    calls = game["sb_calls"]["raise"]
    tot = I._share_w(prior)
    share = I._share_w([c * p for c, p in zip(calls, prior)]) / tot if tot else 0.0
    return {"gain": gain, "share": I._share([h for h in I.ORDER if gain[h] > 0]),
            "calls": {s: (share, spot.need(s))}, "spot": spot,
            "jam_range": game["bb"]["vs_raise"], "call_ranges": {s: calls},
            "priors": {s: prior}}


def sb_action(move, s):
    """Your move in the SB vs BB game as one of its actions."""
    if move[0] == "fold":
        return "fold"
    if move[0] == "call" or move[0] == "check":
        return "limp"
    if move[0] == "raise":
        return "jam" if move[1] >= s.max_raise_to - 1 else "raise"
    return None


def sb_raise_to(s):
    """The 3 bb raise in chips (within what's legal), or None when it isn't a real option."""
    if not s.min_raise_to or (s.my_stack + s.my_bet) / s.big_blind < I.SB_RAISE_MIN_BB:
        return None
    to = int(round(I.SB_RAISE_TO * s.big_blind))
    to = max(s.min_raise_to, to)
    return to if to < s.max_raise_to else None


def _pick(mix, rng=None):
    """An action from a mix (by random: the bots mix their traps)."""
    import random as _r
    x = (rng or _r).random()
    for a, f in mix.items():
        x -= f
        if x < 0:
            return a
    return max(mix, key=mix.get)


def sb_layer(s, sp, pays, move):
    """The bots in the SB vs BB game."""
    hand = I.hand_class(*s.hole)
    h = I.exact()[1][hand]
    game = solve_sb(sp["stacks"], pays)
    if game is None:
        return None
    if sp["kind"] == "sb":
        a = _pick(game["mix"][hand])
        if a == "fold":
            return ("fold", 0)
        if a == "limp":
            return ("call", 0)
        if a == "raise":
            to = sb_raise_to(s)
            return ("raise", to) if to else ("raise", s.max_raise_to)
        return ("raise", s.max_raise_to)
    if sp["kind"] == "sb_limp":
        import random as _r
        return ("raise", s.max_raise_to) if _r.random() < game["bb"]["vs_limp"][h] else ("check", 0)
    if sp["kind"] == "jam" and sp.get("sb") == "raise":
        import random as _r
        return ("raise", s.max_raise_to) if _r.random() < game["bb"]["vs_raise"][h] else ("fold", 0)
    return None


def sb_chart(s):
    """For the chart: the SB vs BB game's exact strategy at this decision, {hand: {move:
    freq}}, or None (not that game)."""
    table = POLICY.get("table")
    if table is None:
        return None
    sp = icm_spot(s)
    if sp is None or not (sp["kind"] in ("sb", "sb_limp") or
                          (sp["kind"] == "jam" and sp.get("sb") == "raise")):
        return None
    game = solve_sb(sp["stacks"], table.pays(len(sp["stacks"])))
    if game is None:
        return None
    names = I.exact()[0]
    out = {}
    if sp["kind"] == "sb":
        to = sb_raise_to(s) or s.max_raise_to
        moves = {"fold": ("fold", 0), "limp": ("call", 0), "raise": ("raise", to),
                 "jam": ("raise", s.max_raise_to)}
        for h in names:
            out[h] = {moves[a]: f for a, f in game["mix"][h].items()}
        return out
    key = "vs_limp" if sp["kind"] == "sb_limp" else "vs_raise"
    other = ("check", 0) if sp["kind"] == "sb_limp" else ("fold", 0)
    for k, h in enumerate(names):
        p = game["bb"][key][k]
        out[h] = {("raise", s.max_raise_to): p, other: 1 - p}
    return out


def sb_verdict(spot, right, g):
    """The grade's one-liner for the SB vs BB game."""
    names = {"fold": "Fold", "limp": "Limp", "raise": f"Raise to {I.SB_RAISE_TO:g}",
             "jam": "Jam"}
    res = spot["res"]
    mix = res["mix"][spot["hand"]]
    plays = " / ".join(f"{names[a].lower()} {100 * f:.0f}%" for a, f in
                       sorted(mix.items(), key=lambda kv: -kv[1]) if f >= 0.05)
    if right:
        return f"ICM: {names[spot['answer']]} {spot['hand']} is right ({plays})"
    return (f"ICM: {names[spot['best']]} {spot['hand']} was best ({plays}); that cost "
            f"{I.money_k(abs(g))}")


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
    if sp["kind"] in ("sb", "sb_limp") or (sp["kind"] == "jam" and sp.get("sb")):
        out = sb_layer(s, sp, pays, move)
        return move if out is None else out
    if sp["kind"] == "call":
        if sp.get("sb"):
            cf = sb_res(sp, pays)
        else:
            res = solve(sp["stacks"], pays, sp["jammer"], **sp["kw"])
            cf = I.call_from(res, sp["me"])
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


STRONG_BEATEN = 0.04   # after the flop: a hand fewer than 4% of random hands beat...
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


def with_open_policy(bot):
    """The bot, following the final table's preflop rules whenever they're on."""
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
        return out
    if sp["kind"] == "sb_limp":     # the BB facing a limp: the solve's answer, no grade
        live = [k for k, p in enumerate(order) if not p.folded and k != me]
        out.update(kind="info", live=live, to_call=s.to_call / s.big_blind,
                   pot=s.pot / s.big_blind, eff=_eff_bb(s), board=list(s.board),
                   sb_limp=True)
        return out
    out.update(kind=sp["kind"], jammer=sp["jammer"], kw=sp["kw"], eff=sp["eff"],
               opener=sp.get("opener"), sb=sp.get("sb"))
    return out


def ensure_solved(spot):
    if spot["kind"] == "info" or "res" in spot:
        return spot
    st, pays, me, kw = spot["stacks"], spot["pays"], spot["hero"], spot.get("kw", {})
    if spot["kind"] == "sb" or spot.get("sb"):
        sp = dict(spot, stacks=st)
        spot["res"] = sb_res(sp, pays)
        spot["chip"] = sb_res(sp, pays, chips=True)
        return spot
    if spot["kind"] == "jam":
        spot["res"] = solve(st, pays, me, iters=40, **kw)
        spot["chip"] = solve(st, pays, me, chips=True, iters=40, **kw)
    else:
        j = spot["jammer"]
        spot["res"] = I.call_from(solve(st, pays, j, iters=40, **kw), me)
        spot["chip"] = I.call_from(solve(st, pays, j, chips=True, iters=40, **kw), me)
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
