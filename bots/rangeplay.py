"""Range-aware equity for the trainer's bots: each opponent's range is followed through the
hand (their preflop range, narrowed by every check, bet, call and raise after the flop),
and our equity is worked out against what they can still hold.

How an action narrows a range: every combo is sorted into a hand class on the board as it
was then (strong / top pair / medium pair / draw / overcards-backdoors / air) and weighted
by how likely a sensible player takes that action with that class (LIKELY below: big bets
are mostly value, small bets mix value and draws, checks are mostly medium hands and air,
calls are pairs and draws, raises are strong hands and some draws).

Used by bots/postflop8.py (range_engine=1); the bot's own rules sit on top."""

import bisect

from cards import evaluate
from bots.postflop import _ALL

STRONG, TOP, MEDIUM, DRAW, EQUITY, AIR = range(6)
CLASS_NAMES = ("strong", "top", "medium", "draw", "equity", "air")

# P(action | class): bet big (> 0.55 pot), bet small, check, call a bet, raise a bet
LIKELY = {
    "bet_big":   (0.85, 0.70, 0.05, 0.25, 0.10, 0.10),
    "bet_small": (0.35, 0.35, 0.20, 0.60, 0.45, 0.25),
    "check":     (0.30, 0.45, 0.85, 0.50, 0.65, 0.75),
    "call":      (0.55, 0.90, 0.55, 0.70, 0.25, 0.04),
    "raise":     (0.65, 0.12, 0.02, 0.25, 0.05, 0.04),
}
WINDOWS = [frozenset(range(lo, lo + 5)) for lo in range(-1, 9)]   # (-1: the ace low)
INDEX = {ab: i for i, ab in enumerate(_ALL)}


class Board:
    """What a board looks like, for classifying many combos on it quickly."""

    def __init__(self, board):
        self.cards = list(board)
        self.ranks = [c >> 2 for c in board]
        self.rset = set(self.ranks)
        self.top = max(self.ranks)
        self.second = sorted(self.rset, reverse=True)[1] if len(self.rset) > 1 else -2
        self.counts = {r: self.ranks.count(r) for r in self.rset}
        self.trips = {r for r, n in self.counts.items() if n >= 3}
        self.suits = [0, 0, 0, 0]
        for c in board:
            self.suits[c & 3] += 1
        self.river = len(board) == 5
        self.flop = len(board) == 3
        low = self.rset | ({-1} if 12 in self.rset else set())
        self.low = low
        # windows already 3+ on the board (a straight / straight draw needs our cards)
        self.win3 = [w for w in WINDOWS if len(w & low) >= 3]


def classify(a, b, B):
    """Hand class of combo (a, b) on board B (a Board)."""
    ra, rb = a >> 2, b >> 2
    sa, sb = a & 3, b & 3
    # flushes and straights that use our cards
    for s in (sa, sb) if sa != sb else (sa,):
        n = B.suits[s] + (sa == s) + (sb == s)
        if n >= 5:
            return STRONG
    mine = {ra, rb}
    if 12 in mine:
        mine.add(-1)
    have = B.low | mine
    for w in B.win3:
        if w <= have and w & mine and not w <= B.low:
            return STRONG
    # pairs and better
    if ra == rb:
        if ra in B.rset or B.trips:
            return STRONG                         # set / full house
        if ra > B.top:
            return TOP                            # overpair
        made = MEDIUM
    else:
        hits = [r for r in (ra, rb) if r in B.rset]
        if len(hits) == 2:
            return STRONG                         # two pair with both cards
        if hits:
            r = hits[0]
            if B.counts[r] >= 2 or B.trips:
                return STRONG                     # trips / full house
            made = TOP if r == B.top else MEDIUM
        else:
            made = None
    if made is not None:
        return made
    if not B.river:
        for s in (sa, sb) if sa != sb else (sa,):
            n = B.suits[s] + (sa == s) + (sb == s)
            if n == 4:
                return DRAW
        for w in WINDOWS:
            if len(w & have) >= 4 and w & mine:
                return DRAW
        if B.flop:
            for s in (sa, sb) if sa != sb else (sa,):
                if B.suits[s] + (sa == s) + (sb == s) == 3:
                    return EQUITY                 # backdoor flush draw
        if min(ra, rb) > B.top:
            return EQUITY                         # two overcards
        return AIR
    return EQUITY if min(ra, rb) > B.top else AIR


def preflop_weights(share, caller):
    """Starting weights over _ALL (strongest first): the top `share`, a softer edge, and
    for a caller of a raise the very top thinned (those hands raise instead)."""
    n = len(_ALL)
    k = max(20, int(n * min(1.0, share)))
    edge = int(k * 0.15)
    w = [0.0] * n
    for i in range(min(n, k + edge)):
        w[i] = 1.0 if i < k else 0.5
    if caller:
        for i in range(int(n * 0.025)):
            w[i] *= 0.25
    return w


def _street_actions(history, street):
    return [(seat, a, amt) for st, seat, a, amt in history if st == street]


def narrow(w, seat, history, board, pot_now, bets_now):
    """Weights `w` (a list over _ALL, changed in place) after `seat`'s actions after the
    flop. pot_now / bets_now: the pot and each seat's bet this street now (to size the
    earlier bets: the pot is rebuilt street by street)."""
    streets = [("flop", 3), ("turn", 4), ("river", 5)]
    streets = [(st, k) for st, k in streets if len(board) >= k]
    # the pot at the start of each street, rebuilt backwards from now
    added = {}
    for st, _ in streets:
        level, contrib = 0, {}
        for sx, a, amt in _street_actions(history, st):
            if a == "raise":
                contrib[sx] = amt
                level = amt
            elif a == "call":
                contrib[sx] = level
        added[st] = sum(contrib.values())
    start = {}
    p = pot_now - sum(bets_now)
    for st, _ in reversed(streets):
        if st == streets[-1][0]:
            start[st] = p
        else:
            nxt = streets[[x for x, _ in streets].index(st) + 1][0]
            start[st] = start[nxt] - added[st]
    for st, k in streets:
        acts = _street_actions(history, st)
        if not any(sx == seat for sx, _, _ in acts):
            continue
        B = Board(board[:k])
        level, pot, contrib = 0, max(1.0, start[st]), {}
        kinds = []
        for sx, a, amt in acts:
            if sx == seat:
                if a == "raise":
                    kinds.append("raise" if level else
                                 "bet_big" if (amt / pot) > 0.55 else "bet_small")
                elif a == "call":
                    kinds.append("call")
                elif a == "check":
                    kinds.append("check")
            if a == "raise":
                pot += amt - contrib.get(sx, 0)
                contrib[sx] = amt
                level = amt
            elif a == "call":
                pot += level - contrib.get(sx, 0)
                contrib[sx] = level
        if not kinds:
            continue
        lik = [LIKELY[x] for x in kinds]
        dead = set(board[:k])
        for i, (x, y) in enumerate(_ALL):
            wi = w[i]
            if wi <= 0.0:
                continue
            if x in dead or y in dead:
                w[i] = 0.0
                continue
            c = classify(x, y, B)
            for row in lik:
                wi *= row[c]
            w[i] = wi
    return w


class Sampler:
    """Weighted draws of an opponent's combo, avoiding dead cards."""

    def __init__(self, w, dead):
        self.combos, self.cum = [], []
        t = 0.0
        for i, (x, y) in enumerate(_ALL):
            wi = w[i]
            if wi > 0.0 and x not in dead and y not in dead:
                t += wi
                self.combos.append((x, y))
                self.cum.append(t)
        self.total = t

    def draw(self, rng):
        if not self.combos:
            return None
        return self.combos[bisect.bisect_left(self.cum, rng.random() * self.total)]


def equity(hole, board, weights_list, rng, samples=300):
    """Our share of the pot against opponents holding these weighted ranges."""
    dead = set(hole) | set(board)
    samplers = [Sampler(w, dead) for w in weights_list]
    if any(sm.total <= 0 for sm in samplers):
        return None
    need = 5 - len(board)
    if need == 0 and len(samplers) == 1:          # the river heads-up: exact
        mine = evaluate(hole + board)
        sm = samplers[0]
        won = tot = 0.0
        prev = 0.0
        for (x, y), c in zip(sm.combos, sm.cum):
            wt = c - prev
            prev = c
            theirs = evaluate([x, y] + board)
            won += wt * (1.0 if mine > theirs else 0.5 if mine == theirs else 0.0)
            tot += wt
        return won / tot if tot else None
    deck = [c for c in range(52) if c not in dead]
    won = 0.0
    n = 0
    tries = 0
    while n < samples and tries < samples * 5:
        tries += 1
        used = set()
        hands = []
        for sm in samplers:
            h = sm.draw(rng)
            if h is None or h[0] in used or h[1] in used:
                hands = None
                break
            used.update(h)
            hands.append(h)
        if hands is None:
            continue
        run = []
        while len(run) < need:
            c = deck[int(rng.random() * len(deck))]
            if c not in used and c not in run:
                run.append(c)
        full = board + run
        mine = evaluate(hole + full)
        best, ties = 0, 0
        for x, y in hands:
            v = evaluate([x, y] + full)
            if v > best:
                best, ties = v, 1
            elif v == best:
                ties += 1
        if mine > best:
            won += 1.0
        elif mine == best:
            won += 1.0 / (ties + 1)
        n += 1
    return won / n if n else None


def call_weights(w, board):
    """The part of a range that calls (or raises) a bet now: weights x P(continue)."""
    B = Board(board)
    out = [0.0] * len(w)
    for i, (x, y) in enumerate(_ALL):
        wi = w[i]
        if wi > 0.0:
            c = classify(x, y, B)
            out[i] = wi * min(1.0, LIKELY["call"][c] + LIKELY["raise"][c])
    return out


def fold_share(w, board, dead):
    """Share of the range that folds to a bet now."""
    B = Board(board)
    tot = keep = 0.0
    for i, (x, y) in enumerate(_ALL):
        wi = w[i]
        if wi > 0.0 and x not in dead and y not in dead:
            c = classify(x, y, B)
            tot += wi
            keep += wi * min(1.0, LIKELY["call"][c] + LIKELY["raise"][c])
    return 1.0 - keep / tot if tot else 0.0
