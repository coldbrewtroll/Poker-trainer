"""Pio flop solves in the trainer (Spots -> Flop sim): the files solver/pio_export.py writes
into postflop/, and the drill that plays them.

A drill hand: the preflop line is played for you (e.g. BTN opens, BB calls), the flop is the
solved board, both hands are dealt from the solve's ranges, the opponent plays the solve's
strategy for its exact cards on the flop, and your flop decisions are graded against it.
From the turn on, the bots play as usual.
"""

import json
import math
from pathlib import Path

from cards import card

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / "postflop"
POSTFLOP_ORDER = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]


import re
from collections import OrderedDict

FOLDER_RE = re.compile(r"^(?P<depth>[\d.]+)bb_(?P<spot>(?:SRP|3BP|4BP|LP)_[A-Z0-9]+_vs_[A-Z0-9]+)$",
                       re.I)
BOARD_RE = re.compile(r"^(?:[2-9TJQKA][cdhs]){3}$")
KEEP_LOADED = 30  # solves held in memory at once (each is ~1 MB once loaded)
_LOADED = OrderedDict()


def load_all(folder=FOLDER):
    """Every solve in postflop/, by name only (postflop/<depth>bb_<spot>/<board>.json): the
    strategies are read when a flop is dealt, so thousands of solves start instantly."""
    sims = []
    for f in sorted(Path(folder).glob("*/*.json")):
        m = FOLDER_RE.match(f.parent.name)
        if m and BOARD_RE.match(f.stem):
            sims.append(FlopSim(path=f, spot=m.group("spot"), depth=float(m.group("depth")),
                                board=[f.stem[i:i + 2] for i in (0, 2, 4)]))
            continue
        try:  # an unusual name: read it to find out what it is
            sims.append(FlopSim(json.loads(f.read_text())))
        except (OSError, ValueError, KeyError):
            continue
    return sims


def roles(spot):
    kind, a, _, b = spot.split("_")
    kind, a, b = kind.upper(), a.upper(), b.upper()
    # In 3-bet pots the name starts with the 3-bettor (3BP_BB_vs_BTN: BTN opens, BB 3-bets).
    opener, other = (b, a) if kind == "3BP" else (a, b)
    return kind, opener, other


class FlopSim:
    def __init__(self, d=None, path=None, spot=None, depth=None, board=None):
        self.path, self._d = path, d
        if d is not None:
            spot, depth, board = d["spot"], d["depth"], d["board"]
        self.spot, self.depth, self.board = spot, float(depth), list(board)
        kind, opener, other = roles(spot)
        if d is not None:
            kind, opener, other = d.get("kind", kind), d.get("opener", opener), d.get("other", other)
        self.kind, self.opener, self.other = kind, opener, other
        self.oop, self.ip = sorted((opener, other), key=lambda p: POSTFLOP_ORDER.index(p)
                                   if p in POSTFLOP_ORDER else 9)
        tag = {"3BP": " 3-bet pot", "4BP": " 4-bet pot", "LP": " limped pot"}.get(self.kind, "")
        self.name = f"{self.depth:g}bb {self.opener} vs {self.other}{tag} {''.join(self.board)}"
        self._index = None

    # The strategies, read on first use; only the most recent KEEP_LOADED stay in memory.
    @property
    def d(self):
        if self._d is None:
            self._d = json.loads(Path(self.path).read_text())
            self._index = None
        if self.path is not None:
            _LOADED[id(self)] = self
            _LOADED.move_to_end(id(self))
            while len(_LOADED) > KEEP_LOADED:
                _, old = _LOADED.popitem(last=False)
                if old is not self and old.path is not None:
                    old._d, old._index = None, None
        return self._d

    @property
    def cpb(self):
        return float(self.d.get("chips_per_bb", 100))

    @property
    def hands(self):
        return self.d["hands"]

    @property
    def nodes(self):
        return self.d["nodes"]

    @property
    def index(self):
        d = self.d
        if self._index is None:
            self._index = {frozenset((h[:2], h[2:])): i for i, h in enumerate(d["hands"])}
        return self._index

    # -- preflop set-up --
    def last_raise_bb(self):
        """The final preflop raise (3-bet, or 4-bet) that leads to the solve's starting pot."""
        dead = 1.0 + sum(v for p, v in (("SB", 0.5), ("BB", 1.0)) if p not in (self.opener, self.other))
        return max(4.0, (self.d["pot"] / self.cpb - dead) / 2)

    def open_bb(self):
        """The open size: in a single-raised pot, the one that leads to the solve's pot
        (older saves used 2.2x / 2.5x opens); otherwise 2x."""
        if self.kind != "SRP":
            return 2.0
        dead = 1.0 + sum(v for p, v in (("SB", 0.5), ("BB", 1.0)) if p not in (self.opener, self.other))
        return min(4.0, max(2.0, (self.d["pot"] / self.cpb - dead) / 2))

    def three_bet_bb(self):
        """The 3-bet size: the last raise in a 3-bet pot; in a 4-bet pot, about 2.3x less."""
        last = self.last_raise_bb()
        return last if self.kind != "4BP" else max(5.0, last / 2.3)

    def side_of(self, pos):
        return "OOP" if pos == self.oop else "IP"

    def deal(self, rng, side, dead):
        """Two card names from `side`'s range (weighted), avoiding `dead`."""
        w = self.d["ranges"][side]
        pool = [(i, wt) for i, wt in enumerate(w) if wt > 0 and
                self.hands[i][:2] not in dead and self.hands[i][2:] not in dead]
        if not pool:
            return None
        i = rng.choices([i for i, _ in pool], [wt for _, wt in pool])[0]
        return [self.hands[i][:2], self.hands[i][2:]]

    def make_deck(self, rng, n, hero_seat, hero_pos, villain_seat):
        """A deck for engine8: hero and villain hands from the ranges, the solved flop."""
        board = list(self.board)
        villain_pos = self.other if hero_pos == self.opener else self.opener
        hero = self.deal(rng, self.side_of(hero_pos), set(board))
        if hero is None:
            return None
        vil = self.deal(rng, self.side_of(villain_pos), set(board) | set(hero))
        if vil is None:
            return None
        used = set(board) | set(hero) | set(vil)
        rest = [c for c in range(52) if c not in {card(x) for x in used}]
        rng.shuffle(rest)
        deck = [None] * 52
        deck[2 * hero_seat:2 * hero_seat + 2] = [card(c) for c in hero]
        deck[2 * villain_seat:2 * villain_seat + 2] = [card(c) for c in vil]
        deck[2 * n:2 * n + 3] = [card(c) for c in board]
        it = iter(rest)
        return [c if c is not None else next(it) for c in deck]

    # -- following the flop --
    def node_for(self, s, big_blind):
        """The solve's node for this flop state, following the actions so far (bets matched
        to the nearest size in the tree), or None if the hand left the tree."""
        node = "r:0"
        for st, seat, a, amt in s.history:
            if st != "flop":
                continue
            n = self.nodes.get(node)
            if n is None:
                return None
            code = self.code_for(n, a, amt, big_blind)
            if code is None:
                return None
            node = f"{node}:{code}"
        return node if node in self.nodes else None

    def code_for(self, n, a, amt, big_blind):
        acts = n["actions"]
        if a == "fold":
            return "f" if "f" in acts else None
        if a in ("check", "call"):
            return "c" if "c" in acts else None
        bets = [x for x in acts if x.startswith("b")]
        if not bets:
            return None
        want = amt / big_blind * self.cpb
        return min(bets, key=lambda x: abs(float(x[1:]) - want))

    def mix(self, node, cards):
        """{action code: frequency} for these two cards at this node (None if not in range)."""
        n = self.nodes.get(node)
        i = self.index.get(frozenset(cards))
        if n is None or i is None:
            return None
        freqs = {a: row[i] / 1000 for a, row in zip(n["actions"], n["strategy"])}
        total = sum(freqs.values())
        if total <= 0:
            return None
        return {a: f / total for a, f in freqs.items()}

    def move(self, s, code, big_blind):
        """An engine move for an action code in state s."""
        if code == "f":
            return ("fold", 0) if s.to_call else ("check", 0)
        if code == "c":
            return ("call", 0) if s.to_call else ("check", 0)
        if not s.min_raise_to:
            return ("call", 0) if s.to_call else ("check", 0)
        to = int(round(float(code[1:]) / self.cpb * big_blind))
        if float(code[1:]) >= self.d["stack"] - 1:
            to = s.max_raise_to
        return ("raise", max(s.min_raise_to, min(s.max_raise_to, to)))

    def pick(self, s, big_blind, rng=None):
        """The solve's move for the player to act: sampled from its mix (rng), or its most
        frequent action (rng=None). None when the hand has left the solved tree."""
        node = self.node_for(s, big_blind)
        if node is None:
            return None
        mix = self.mix(node, list(s.hole))
        if not mix:
            return None
        if rng is None:
            code = max(mix, key=mix.get)
        else:
            r, code = rng.random(), None
            for code, f in mix.items():
                r -= f
                if r <= 0:
                    break
        return self.move(s, code, big_blind)

    def range_mix(self, node, side_cards_dead):
        """Range-wide frequency of each action at a node, and per-combo mixes."""
        n = self.nodes[node]
        side = n["player"]
        w = self.d["ranges"][side]
        tot = {a: 0.0 for a in n["actions"]}
        wsum = 0.0
        for i, wt in enumerate(w):
            if wt <= 0:
                continue
            h = self.hands[i]
            if h[:2] in side_cards_dead or h[2:] in side_cards_dead:
                continue
            row_tot = sum(row[i] for row in n["strategy"])
            if row_tot <= 0:
                continue
            wsum += wt
            for a, row in zip(n["actions"], n["strategy"]):
                tot[a] += wt * row[i] / row_tot
        return {a: v / wsum for a, v in tot.items()} if wsum else {}


class SimDirector:
    """Spots-mode director for a flop sim: plays the preflop line, then the flop by the solve."""

    def __init__(self, sim, hero_pos, villain_seat, big_blind, rng):
        self.sim, self.hero_pos, self.villain = sim, hero_pos, villain_seat
        self.bb, self.rng = big_blind, rng
        self.scen = "Flop sim"

    def _preflop(self, s, me_opener):
        raises = sum(1 for st, _, a, _ in s.history if st == "preflop" and a == "raise")
        if self.sim.kind == "LP":   # a limped pot: the SB completes, the BB checks
            return ("call", 0) if s.to_call else ("check", 0)
        if self.sim.kind == "SRP":
            if me_opener and raises == 0:
                return ("raise", int(round(self.sim.open_bb() * self.bb)))
            return ("call", 0) if s.to_call else ("check", 0)
        if self.sim.kind == "4BP":  # open, 3-bet, 4-bet, call
            if me_opener:
                if raises == 0:
                    return ("raise", 2 * self.bb)
                if raises == 2:
                    return ("raise", int(round(self.sim.last_raise_bb() * self.bb)))
                return ("call", 0)
            if raises == 1:
                return ("raise", int(round(self.sim.three_bet_bb() * self.bb)))
            return ("call", 0) if s.to_call else ("check", 0)
        # 3-bet pot: open, 3-bet, call.
        if me_opener:
            return ("raise", 2 * self.bb) if raises == 0 else ("call", 0)
        if raises == 1:
            return ("raise", int(round(self.sim.three_bet_bb() * self.bb)))
        return ("call", 0) if s.to_call else ("check", 0)

    def hero_preflop(self, s, adv):
        return self._preflop(s, self.hero_pos == self.sim.opener)

    def other_preflop(self, s, bot):
        if s.my_seat != self.villain:
            return ("fold", 0) if s.to_call else ("check", 0)
        return self._preflop(s, self.hero_pos != self.sim.opener)

    def flop_move(self, s, sample):
        if s.street != "flop":
            return None
        return self.sim.pick(s, self.bb, self.rng if sample else None)


# ---------------------------------------------------------------- the nearest solved flop
# In a real hand the flop is rarely one you solved. The closest solve for the same spot
# (same preflop line and positions, nearest stack-to-pot ratio) and the nearest board with
# the same shape (paired or not, suits) stands in: cards are translated rank by rank (the
# board's ranks onto the solved board's, the ranks in between spread in order) and suit by
# suit, and bet sizes by the pot (a third of the pot stays a third of the pot).

RANKS = "23456789TJQKA"
SPR_TOLERANCE = 1.6   # a solve is used if its stack-to-pot ratio is within this factor


def _rank(c):
    return RANKS.index(c[0])


def _board_map(real, solved):
    """(score, rank pairs, suit map) for laying the real flop onto a solved one, or None
    when the two don't have the same shape."""
    import itertools
    best = None
    for perm in itertools.permutations(range(3)):
        sv = [solved[k] for k in perm]
        ok, suits = True, {}
        for i in range(3):
            for j in range(3):
                ri, rj, si, sj = _rank(real[i]), _rank(real[j]), _rank(sv[i]), _rank(sv[j])
                if (ri > rj) != (si > sj) or (ri == rj) != (si == sj):
                    ok = False
            u, v = real[i][1], sv[i][1]
            if suits.get(u, v) != v or (v in suits.values() and suits.get(u) != v):
                ok = False
            suits[u] = v
        if not ok:
            continue
        pairs = sorted({(_rank(real[k]), _rank(sv[k])) for k in range(3)})
        # nearer ranks first; then a board whose gaps hold the real ones (a real rank
        # with no room on the solved board can't be translated: those hands get no plan)
        lost = sum(1 for v in _rank_map(pairs, strict=True).values() if v is None)
        score = sum((3 - k) * abs(_rank(real[k]) - _rank(sv[k])) for k in range(3)) + 3 * lost
        if best is None or score < best[0]:
            best = (score, pairs, dict(suits))
    return best


def _rank_map(pairs, strict=False):
    """Every real rank -> a solved-board rank: board ranks onto board ranks, the ranks in
    between spread in order between them."""
    anchors = [(-1, -1)] + list(pairs) + [(13, 13)]
    out = {r: s for r, s in pairs}
    for (r0, s0), (r1, s1) in zip(anchors, anchors[1:]):
        k = r1 - r0 - 1          # real ranks in this gap
        m = s1 - s0 - 1          # solved ranks free in this gap
        for i in range(k):
            if m <= 0:   # no room between them on the solved board: the nearest free
                out[r0 + 1 + i] = None    # rank past the nearer neighbour (below)
            else:
                out[r0 + 1 + i] = s0 + 1 + min(m - 1, int((i + 0.5) * m / k))
    if strict:
        return out
    board = {sv for _, sv in pairs}
    for (r0, s0), (r1, s1) in zip(anchors, anchors[1:]):
        k = r1 - r0 - 1
        for i in range(k):
            r = r0 + 1 + i
            if out[r] is not None:
                continue
            down = [x for x in range(s0 - 1, -1, -1) if x not in board]
            up = [x for x in range(s1 + 1, 13) if x not in board]
            near_low = (i + 0.5) < k / 2
            pick = (down if near_low else up) or (up if near_low else down)
            out[r] = pick[0] if pick else None
    return out


class Mapped:
    """A solve standing in for a real flop: the FlopSim interface, in real cards and chips."""

    def __init__(self, sim, board, rank_map, suit_map, pot_real):
        self.sim, self.real_board = sim, list(board)
        self.rank_map = rank_map
        free_real = [s for s in "cdhs" if s not in suit_map]
        free_sim = [s for s in "cdhs" if s not in suit_map.values()]
        self.suit_map = dict(suit_map, **dict(zip(free_real, free_sim)))
        self.scale = sim.d["pot"] / pot_real   # solve chips per real chip
        for a in ("spot", "depth", "kind", "opener", "other", "oop", "ip", "board"):
            setattr(self, a, getattr(sim, a))
        exact = sorted(board) == sorted(sim.board)
        self.name = sim.name if exact else f"{sim.name} (for {''.join(board)})"
        self.board_dead = set(sim.board)

    d = property(lambda self: self.sim.d)
    hands = property(lambda self: self.sim.hands)
    nodes = property(lambda self: self.sim.nodes)

    def card(self, c):
        s = self.rank_map.get(_rank(c))
        return None if s is None else RANKS[s] + self.suit_map[c[1]]

    def cards(self, hole):
        out = [self.card(c) for c in hole]
        if None not in out and out[0] == out[1]:   # two real ranks squeezed onto one:
            hi = 0 if _rank(hole[0]) > _rank(hole[1]) else 1      # the higher card one up
            r = RANKS.index(out[hi][0])
            board = {c[0] for c in self.board_dead}
            for x in list(range(r + 1, 13)) + list(range(r - 1, -1, -1)):
                if RANKS[x] not in board:
                    out[hi] = RANKS[x] + out[hi][1]
                    break
        if None in out or out[0] == out[1] or set(out) & self.board_dead:
            return None
        return out

    def side_of(self, pos):
        return self.sim.side_of(pos)

    def node_for(self, s, big_blind):
        node = "r:0"
        for st, seat, a, amt in s.history:
            if st != "flop":
                continue
            n = self.nodes.get(node)
            if n is None:
                return None
            acts = n["actions"]
            if a == "fold":
                code = "f" if "f" in acts else None
            elif a in ("check", "call"):
                code = "c" if "c" in acts else None
            else:
                bets = [x for x in acts if x.startswith("b")]
                code = min(bets, key=lambda x: abs(float(x[1:]) - amt * self.scale)) if bets else None
            if code is None:
                return None
            node = f"{node}:{code}"
        return node if node in self.nodes else None

    def mix(self, node, cards):
        m = self.cards(cards)
        return self.sim.mix(node, m) if m else None

    def range_mix(self, node, dead):
        return self.sim.range_mix(node, self.board_dead)

    def move(self, s, code, big_blind):
        if code == "f":
            return ("fold", 0) if s.to_call else ("check", 0)
        if code == "c" or not s.min_raise_to:
            return ("call", 0) if s.to_call else ("check", 0)
        to = int(round(float(code[1:]) / self.scale))
        if float(code[1:]) >= self.d["stack"] - 1:
            to = s.max_raise_to
        return ("raise", max(s.min_raise_to, min(s.max_raise_to, to)))

    def pick(self, s, big_blind, rng=None):
        node = self.node_for(s, big_blind)
        if node is None:
            return None
        mix = self.mix(node, list(s.hole))
        if not mix:
            return None
        if rng is None:
            code = max(mix, key=mix.get)
        else:
            r, code = rng.random(), None
            for code, f in mix.items():
                r -= f
                if r <= 0:
                    break
        return self.move(s, code, big_blind)


def preflop_line(history):
    """(kind, opener, other) for a heads-up flop from the preflop actions (seat numbers),
    or None for limped / multiway lines. kind: SRP, 3BP, 4BP."""
    acts = [(seat, a) for st, seat, a, _ in history if st == "preflop" and a != "fold"]
    raisers = [seat for seat, a in acts if a == "raise"]
    if not raisers:
        if [a for _, a in acts] == ["call", "check"]:
            return "LP", acts[0][0], acts[1][0]   # the SB limped, the BB checked
        return None
    if acts[0][1] != "raise":
        return None   # limped, then raised
    kind = {1: "SRP", 2: "3BP", 3: "4BP"}.get(len(raisers))
    if kind is None:
        return None
    opener = raisers[0]
    other = raisers[1] if len(raisers) >= 2 else next(seat for seat, a in acts if a == "call")
    if kind == "4BP":
        opener, other = raisers[0], raisers[1]
        if raisers[2] != opener:
            return None
    return kind, opener, other


def find(sims, kind, opener_pos, other_pos, board, pot_flop, eff_stack):
    """The nearest solve for this flop (a Mapped), or None. Positions are names (BTN...),
    pot and stack in real chips."""
    group = [x for x in sims if x.kind == kind and x.opener == opener_pos and
             x.other == other_pos]
    if not group or pot_flop <= 0:
        return None
    spr = eff_stack / pot_flop
    sprs = {}
    for x in group:
        key = x.depth
        if key not in sprs:
            sprs[key] = x.d["stack"] / x.d["pot"]
    depth = min(sprs, key=lambda k: abs(math.log(max(sprs[k], 1e-6) / max(spr, 1e-6))))
    if abs(math.log(max(sprs[depth], 1e-6) / max(spr, 1e-6))) > math.log(SPR_TOLERANCE):
        return None
    best = None
    for x in group:
        if x.depth != depth:
            continue
        m = _board_map(board, x.board)
        if m and (best is None or m[0] < best[0]):
            best = (m[0], x, m)
    if best is None:
        return None
    _, x, (score, pairs, suits) = best
    return Mapped(x, board, _rank_map(pairs), suits, pot_flop)
