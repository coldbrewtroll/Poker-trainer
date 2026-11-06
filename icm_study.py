"""ICM study for the practice table (trainer only; the contest itself is chip EV).

Two tabs:
  ICM              stacks and payouts -> each player's prize equity; push/fold charts for any
                   seat under ICM next to chip EV.
  Mega Satellites  many players, N equal seats. Traditional: the last N players standing win
                   the seats. Landmark: anyone who reaches the target chip count wins a seat
                   and leaves. Seat equity for the players at your table; push/fold charts
                   under satellite pressure.

The maths:
  * ICM (Malmuth-Harville): a player finishes 1st with probability = their share of the
    chips; then the same among the rest for 2nd, and so on. It is the same as a race in
    which each player's finishing time is random with rate = stack (first to finish wins):
    with that form, satellites with hundreds of players are cheap to work out (the players
    at your table one by one, everyone else at the field's average stack).
  * Push/fold: the hero jams first in, the players behind call or fold. Each caller calls
    with the hands whose all-in equity against the hero's range beats what the call needs
    (in prize equity); the hero jams the hands whose EV beats folding against those
    callers. Repeated until both settle (an equilibrium). Only the first caller is played
    out (overcalls ignored), ranges are the strongest hands by all-in equity.
"""

import math
import random

from bots.preflop8 import COMBOS, ORDER, eq_vs
from cards import card, evaluate
from engine8 import position_names

POS9 = ["UTG", "UTG1", "UTG2", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
PRESETS = {  # (kept for reference) name: (stacks in bb, payouts as % of the prize pool)
    "Final table": ([30, 25, 22, 20, 18, 15, 12, 10, 8],
                    [26.6, 19.1, 14.0, 10.8, 8.5, 7.0, 5.7, 4.6, 3.7]),
    "Bubble": ([30, 25, 22, 20, 18, 15, 12, 10], [30, 20, 14, 10, 8, 7, 6, 5]),
    "3-handed": ([30, 20, 10], [50, 30, 20]),
    "Heads-up": ([25, 15], [65, 35]),
}
ANTE = 1.0  # big blind ante, in bb (dead money)
# A standard WSOP Circuit Main Event final table ($1,700 buy-in, ~1,000 entries): the average
# shape of recent Circuit Main Event final tables (TCH Austin, Pompano Beach, Choctaw,
# Turning Stone, Tunica: 2nd = 2/3 of 1st, 3rd ~47%, 4th ~34%, 5th ~25% ... 9th ~8%).
CIRCUIT = [250000, 166650, 117775, 84575, 61475, 46050, 35000, 26600, 20200]
FT_STACKS = [32, 26, 22, 19, 16, 13, 11, 9, 7]
# Prize pools for the final nine, all paid with the Circuit shape above.
POOLS = {"$500K": 500_000, "$1M": 1_000_000, "$1.5M": 1_500_000}
PAY_MODES = list(POOLS) + ["Custom $"]
N_TABLES = 250


def pool_payouts(pool):
    """The nine Circuit-shaped payouts for a prize pool, in whole dollars, summing exactly
    to the pool (the rounding goes to 1st)."""
    total = sum(CIRCUIT)
    pays = [float(round(pool * c / total)) for c in CIRCUIT]
    pays[0] += float(pool) - sum(pays)
    return pays


def table_spots(st):
    """The trainer's decisions at a table: ("jam", hero, None) when it's folded to hero with
    25 bb or less effective, ("call", hero, jammer) when a jam of 25 bb or less effective
    comes in front of hero."""
    n, out = len(st), []
    for hero in range(n - 1):
        if min(st[hero], max(st[hero + 1:])) <= 25:
            out.append(("jam", hero, None))
    for hero in range(1, n):
        for j in range(hero):
            if min(st[j], max(st[j + 1:])) <= 25:
                out.append(("call", hero, j))
    return out


_PRESETS = {}


def preset_table(k):
    """Final table k (1-250): nine stacks of 5-100 bb, the same every time, no two tables
    alike. (Tables 1-100 are the original ones, unchanged.)"""
    if k not in _PRESETS:
        _PRESETS[k] = _make_table(k)
    return list(_PRESETS[k])


def _make_table(k):
    r = random.Random(7919 * k + 17)
    seen = {tuple(sorted(preset_table(j))) for j in range(1, k)} if k > 100 else set()
    while True:
        # 1-100: four shapes (k % 4); 101-250 add two more (k % 6): two chip leaders over
        # a middle field, and a short-stacked field (5-25 bb) where every hand is push/fold
        style = k % 4 if k <= 100 else k % 6
        st = []
        for _ in range(9):
            u = r.random()
            if style == 1:      # a big chip leader and a short-stacked field
                u = u ** 1.6
            elif style == 2:    # deep and even
                u = 0.35 + 0.65 * u
            elif style == 5:    # all short: 5-25 bb
                u = u * 0.537
            st.append(float(round(5 * 20 ** u)))
        if style == 1:
            st[r.randrange(9)] = float(r.randint(70, 100))
        elif style == 4:        # two chip leaders
            a, b = r.sample(range(9), 2)
            st[a], st[b] = float(r.randint(60, 100)), float(r.randint(50, 90))
        if len(table_spots(st)) >= 6 and tuple(sorted(st)) not in seen:
            return st


def money(v, sign=False):
    """$1,234 (cents only under $100; with + / - when sign is set)."""
    s = f"${abs(v):,.0f}" if abs(v) >= 100 else f"${abs(v):,.2f}"
    return ("-" if v < 0 else "+" if sign else "") + s


def money_short(v):
    """Compact signed dollars for chart cells: +$1.2k, -$340."""
    a = abs(v)
    s = f"${a / 1000:.1f}k" if a >= 10000 else f"${a / 1000:.2f}k" if a >= 1000 else f"${a:.0f}"
    return ("-" if v < 0 else "+") + s


TSPEEDS = (("Slow", 1.6), ("Normal", 1.0), ("Fast", 0.4))
TABLE = (450, 378, 390, 196)   # the trainer's table: centre, half width, half height
PANEL_X = 930
DECK = [r + s_ for r in "23456789TJQKA" for s_ in "cdhs"]


LEVEL_HANDS = 15    # the blinds go up every 15 hands, through the levels below
# WSOP Circuit Main Event blinds from the final table on (small blind, big blind; the big
# blind ante equals the big blind). 80K/160K puts a Circuit Main Event's chips (about 1,000
# entries x 40K) at the stacks of the preset tables. Stacks are counted in big blinds, so a
# new level makes every stack (old big blind / new big blind) as deep.
WSOP_LEVELS = [(80_000, 160_000), (100_000, 200_000), (125_000, 250_000), (150_000, 300_000),
               (200_000, 400_000), (250_000, 500_000), (300_000, 600_000), (400_000, 800_000),
               (500_000, 1_000_000), (600_000, 1_200_000), (800_000, 1_600_000),
               (1_000_000, 2_000_000), (1_200_000, 2_400_000), (1_500_000, 3_000_000),
               (2_000_000, 4_000_000), (2_500_000, 5_000_000), (3_000_000, 6_000_000)]


def hand_note(h, spr):
    """A sentence on how hand class h plays after the flop at this stack-to-pot ratio."""
    ranks = "23456789TJQKA"
    a, b = ranks.index(h[0]), ranks.index(h[1])
    if len(h) == 2:
        if a >= 8:
            return f"{h}: a big pair, happy to get the money in at any SPR: build the pot."
        if a >= 5:
            return (f"{h}: a medium pair, often best unimproved at low SPR; deeper, the "
                    f"overcards on most flops make it awkward.")
        return (f"{h}: a small pair wants a set; at SPR {spr:.0f} the set "
                + ("gets paid well." if spr > 6 else "pays too little: it plays better as an "
                   "all-in (or a fold) than as an open-call."))
    suited = h[2] == "s"
    if h[0] == "A":
        if b >= 8:
            return f"{h}: a big ace, top pair with a good kicker: fine at a low SPR."
        return (f"{h}: a weak ace. When it's called it's often dominated by better aces, and "
                f"top pair weak kicker at a low SPR is a stack-off you don't want"
                + (" (suited, it can make the nut flush)." if suited else "."))
    if a >= 8 and b >= 8:
        return (f"{h}: broadways make top pair; at a low SPR top pair is a stack-off, deeper "
                f"be careful against big action.")
    if suited and a - b <= 2 and b >= 3:
        return (f"{h}: suited and connected, flushes and straights; it needs depth to pay "
                f"off (deep is good; short, it misses too often to call off).")
    return (f"{h}: poor playability, it hits rarely and makes weak pairs: a hand that wants "
            f"to win preflop, or fold.")


def chips_k(v):
    return f"{v / 1e6:g}M" if v >= 1e6 else f"{v / 1e3:g}K"


def blinds_text(level):
    """'100K/200K (200K ante)' for level 1, 2, ... of WSOP_LEVELS."""
    sb, bb = WSOP_LEVELS[min(level, len(WSOP_LEVELS)) - 1]
    return f"{chips_k(sb)}/{chips_k(bb)} ({chips_k(bb)} ante)"


def level_ratio(level):
    """How much bigger the big blind of `level` is than the one before it."""
    i = min(level, len(WSOP_LEVELS)) - 1
    return WSOP_LEVELS[i][1] / WSOP_LEVELS[max(0, i - 1)][1]


def ordinal(k):
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def call_from(res, hero):
    """solve_call's answer, from a solve_jam result for the jammer (no second solve)."""
    names = exact()[0]
    sp, rj = res["spot"], _sparse(res["jam_range"])
    win, lose, fold = sp.lose[hero][hero], sp.win[hero][hero], sp.v_steal[hero]
    gain = {}
    for h in range(len(names)):
        eq = range_eq(h, rj)
        gain[names[h]] = eq * win + (1 - eq) * lose - fold
    return {"gain": gain, "share": _share([h for h in ORDER if gain[h] > 0]),
            # the share of hands in the jam range the equities are worked out against
            # (the solve's average range), so "jams 12%" and "60% more hands" mean it
            "jam_share": _share_w(res["jam_range"]), "need": sp.need(hero), "spot": sp,
            "jam_range": res["jam_range"], "hero": hero, "jam_gain": res["gain"]}


def fmt_bb(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def money_k(v):
    return f"${v / 1000:,.1f}K" if abs(v) >= 1000 else f"${v:,.0f}"


def hand_class(c1, c2):
    """'AKs' / 'AKo' / 'QQ' for two card names."""
    order = "23456789TJQKA"
    a, b = sorted((c1, c2), key=lambda c: -order.index(c[0]))
    if a[0] == b[0]:
        return a[0] + b[0]
    return a[0] + b[0] + ("s" if a[1] == b[1] else "o")


def rand_combo(h, dead, rng):
    """Two random card names of hand class h, avoiding dead cards."""
    r1, r2 = h[0], h[1]
    opts = []
    for s1 in "cdhs":
        for s2 in "cdhs":
            if len(h) == 2 and s2 <= s1:
                continue
            if len(h) == 3 and (s1 == s2) != (h[2] == "s"):
                continue
            a, b = r1 + s1, r2 + s2
            if a not in dead and b not in dead:
                opts.append([a, b])
    return rng.choice(opts) if opts else None


def positions(n):
    """Seat names for n players, the big blind last."""
    if n == 2:
        return ["SB", "BB"]  # heads-up: the button is the small blind
    return POS9 if n == 9 else position_names(n)


# ------------------------------------------------------------------ prize equity

def icm(stacks, payouts):
    """Each player's prize equity (same units as payouts), Malmuth-Harville, exact. Busted
    players (0 chips) share the lowest places' prizes."""
    n = len(stacks)
    pays = (list(payouts) + [0.0] * n)[:n]
    alive = [i for i in range(n) if stacks[i] > 1e-9]
    out = [0.0] * n
    busted = [i for i in range(n) if i not in alive]
    if busted:
        low = pays[len(alive):]
        for i in busted:
            out[i] = sum(low) / len(low)
    pays = pays[:len(alive)]
    memo = {}

    def rec(mask, place):
        if place >= len(pays) or not mask:
            return {}
        if mask in memo:
            return memo[mask]
        members = [k for k in range(len(alive)) if mask >> k & 1]
        total = sum(stacks[alive[k]] for k in members)
        res = {alive[k]: 0.0 for k in members}
        for k in members:
            p = stacks[alive[k]] / total
            res[alive[k]] += p * pays[place]
            for j, v in rec(mask & ~(1 << k), place + 1).items():
                res[j] += p * v
        memo[mask] = res
        return res

    for i, v in rec((1 << len(alive)) - 1, 0).items():
        out[i] = v
    return out


def _binom_cdf(m, q, x):
    """P(Binomial(m, q) <= x)."""
    if x < 0:
        return 0.0
    if x >= m:
        return 1.0
    if q <= 0:
        return 1.0
    if q >= 1:
        return 0.0
    var = m * q * (1 - q)
    if var > 25:  # normal approximation (with continuity correction)
        z = (x + 0.5 - m * q) / math.sqrt(var)
        return 0.5 * math.erfc(-z / math.sqrt(2))
    total, lq, l1 = 0.0, math.log(q), math.log1p(-q)
    lf = math.lgamma(m + 1)
    for k in range(0, int(x) + 1):
        total += math.exp(lf - math.lgamma(k + 1) - math.lgamma(m - k + 1) + k * lq +
                          (m - k) * l1)
    return min(1.0, total)


def satellite(stacks, seats, field_n=0, field_avg=0.0, leftover=0.0, players=None,
              points=48):
    """Seat equity (1 = a seat) of the table players: P(fewer than `seats` players finish
    ahead) plus `leftover` (in seats) x P(exactly `seats` finish ahead). The rest of the
    field: field_n players with field_avg chips each. players: which table seats to work
    out (default all; the others come back as None)."""
    n = len(stacks)
    out = [None] * n
    for i in (range(n) if players is None else players):
        si = stacks[i]
        if si <= 1e-9:
            out[i] = 0.0  # busted: behind everyone
            continue
        seat = left = 0.0
        for k in range(points):
            p = (k + 0.5) / points  # p = chance i is still "running" at time t, i.e. P(T_i > t)
            dist = [1.0]
            for j in range(n):
                if j == i or stacks[j] <= 1e-9:
                    continue
                qj = 1.0 - p ** (stacks[j] / si)  # j already finished ahead of time t
                new = [0.0] * (len(dist) + 1)
                for c, v in enumerate(dist):
                    new[c] += v * (1 - qj)
                    new[c + 1] += v * qj
                dist = new
            qf = 1.0 - p ** (field_avg / si) if field_n and field_avg > 0 else 0.0
            for c, v in enumerate(dist):
                if v < 1e-12:
                    continue
                need = seats - 1 - c  # at most this many of the field may finish ahead
                cdf = _binom_cdf(field_n, qf, need) if field_n else (1.0 if need >= 0 else 0.0)
                seat += v * cdf
                if leftover:
                    nxt = _binom_cdf(field_n, qf, need + 1) if field_n else (
                        1.0 if need + 1 >= 0 else 0.0)
                    left += v * (nxt - cdf)
        out[i] = (seat + leftover * left) / points
    return out


# ------------------------------------------------------------------ push / fold

def posts(stacks):
    """Chips each player has in before the action: small blind, big blind + ante."""
    n = len(stacks)
    p = [0.0] * n
    if n >= 2:
        p[n - 2] = min(0.5, stacks[n - 2])
        p[n - 1] = min(1.0 + ANTE, stacks[n - 1])
    return p


class Spot:
    """Outcomes of a first-in jam by `hero` (stacks in bb). value(stacks, who) gives the
    equity of the players in `who` after those stacks (prize equity, or chips).

    Blinds count towards a player's all-in; the big blind ante is dead money (it doesn't),
    so the big blind can risk only its stack minus the ante, and the bigger stack gets the
    rest back."""

    def __init__(self, stacks, value, hero, posts=None, responders=None, fold_to=None):
        """posts: what each player has in already (bb, blinds and opens; counts towards
        their all-in), default the blinds. responders: who can call the jam, in order
        (default: everyone behind). fold_to: who takes the pot if the hero folds (default
        the big blind)."""
        self.stacks, self.hero = list(stacks), hero
        n = len(stacks)
        self.behind = list(responders) if responders is not None else list(range(hero + 1, n))
        blind = [0.0] * n
        ante = [0.0] * n
        if n >= 2:
            blind[n - 2] = min(0.5, stacks[n - 2])
            ante[n - 1] = min(ANTE, stacks[n - 1])
            blind[n - 1] = min(1.0, stacks[n - 1] - ante[n - 1])
        if posts is not None:
            blind = [min(p, s - a) for p, s, a in zip(posts, stacks, ante)]
        self.post = [b + a for b, a in zip(blind, ante)]
        after = [s - p for s, p in zip(self.stacks, self.post)]
        pot = sum(self.post)
        steal = after[:]
        steal[hero] += pot           # everyone folds to the jam
        fold = after[:]
        fold[n - 1 if fold_to is None else fold_to] += pot   # the hero folds: (roughly)
        # the big blind (or the opener) wins what's in
        need = [hero] + self.behind
        self.v_steal, self.v_fold = value(steal, need), value(fold, [hero])
        self.win, self.lose = {}, {}
        for i in self.behind:
            w = self._showdown(stacks, blind, ante, after, hero, i, hero)
            l = self._showdown(stacks, blind, ante, after, hero, i, i)
            self.win[i], self.lose[i] = value(w, [hero, i]), value(l, [hero, i])

    @staticmethod
    def _showdown(stacks, blind, ante, after, hero, i, winner):
        """The stacks after `hero` and `i` are all in and `winner` wins: the pots settled
        layer by layer like the engine (each can win from every other player only as much
        as they put in themselves; the ante is dead, it goes to the winner). Usually that's
        the whole pot; not when a blind or an open already in is bigger than a short
        all-in's stack."""
        live = [s - a for s, a in zip(stacks, ante)]
        commit = list(blind)
        commit[hero], commit[i] = live[hero], live[i]
        out = list(after)
        out[hero] = out[i] = 0.0
        prev = 0.0
        for lv in sorted({c for c in commit if c > 0}):
            layer = sum(min(c, lv) - min(c, prev) for c in commit)
            elig = [k for k in (hero, i) if commit[k] >= lv]
            out[winner if (winner in elig or not elig) else elig[0]] += layer
            prev = lv
        out[winner] += sum(ante)
        return out

    def need(self, i):
        """Equity caller i needs to call the jam (their fold value: the hero's steal)."""
        win_i, lose_i, fold_i = self.lose[i][i], self.win[i][i], self.v_steal[i]
        span = win_i - lose_i
        return (fold_i - lose_i) / span if span > 1e-12 else 1.0


# ------------------------------------------------------------------ exact equities

_EXACT = None


def exact():
    """169x169 all-in equities (ties as half) and card-removal weights: W[i][j] = how many
    combos of hand j fit with one combo of hand i (out of 1225 = 50 choose 2)."""
    global _EXACT
    if _EXACT is None:
        import json
        from pathlib import Path
        d = json.loads((Path(__file__).resolve().parent / "solver" /
                        "preflop_equity.json").read_text())
        names, E = d["names"], d["equity"]
        ranks, suits = "23456789TJQKA", "cdhs"

        def masks(h):
            r1, r2 = ranks.index(h[0]), ranks.index(h[1])
            out = []
            for s1 in range(4):
                for s2 in range(4):
                    if len(h) == 2 and s2 <= s1:
                        continue
                    if len(h) == 3 and (s1 == s2) != (h[2] == "s"):
                        continue
                    a, b = r1 * 4 + s1, r2 * 4 + s2
                    if a != b:
                        out.append((1 << a) | (1 << b))
            return out
        m = [masks(h) for h in names]
        W = [[sum(1 for x in mi for y in mj if not x & y) / len(mi) for mj in m] for mi in m]
        _EXACT = (names, {h: i for i, h in enumerate(names)}, E, W)
    return _EXACT


def range_eq(i, rng):
    """Equity of hand i against a range [(j, weight)], with card removal."""
    _, _, E, W = exact()
    num = den = 0.0
    Ei, Wi = E[i], W[i]
    for j, w in rng:
        ww = Wi[j] * w
        num += ww * Ei[j]
        den += ww
    return num / den if den > 0 else 0.5


def range_prob(i, rng):
    """Chance a random hand (given we hold hand i) is in the range: its combos / 1225."""
    W = exact()[3][i]
    return sum(W[j] * w for j, w in rng) / 1225.0


def _share(hands):
    return sum(COMBOS[h] for h in hands) / 1326


def _sparse(weights):
    return [(j, w) for j, w in enumerate(weights) if w > 1e-6]


def _share_w(weights):
    names = exact()[0]
    return sum(COMBOS[names[j]] * w for j, w in enumerate(weights)) / 1326


_COLS = None


def _cols():
    """Columns of W and W*E (W[h][j] for every h, for each j): adding one hand j to a range
    updates every hand's equity against it in one pass."""
    global _COLS
    if _COLS is None:
        names, _, E, W = exact()
        n = len(names)
        _COLS = ([[W[h][j] for h in range(n)] for j in range(n)],
                 [[W[h][j] * E[h][j] for h in range(n)] for j in range(n)])
    return _COLS


class _Avg:
    """A fictitious-play average range a_t = (1 - 1/t) a + br/t, with every hand's
    card-removal-weighted mass (den) and equity sum (num) against it kept up to date:
    a best response changes by a few hands per round, so only those columns are added."""

    def __init__(self, n, weight=None):
        self.n = n
        self.w = weight                       # per-hand prior weight (or None)
        self.avg = [0.0] * n
        self.num, self.den = [0.0] * n, [0.0] * n          # against the average
        self.bnum, self.bden = [0.0] * n, [0.0] * n        # against the current best resp.
        self.br = [0.0] * n

    def _add(self, j, coef):
        WT, WET = _cols()
        if self.w is not None:
            coef *= self.w[j]
        if coef == 0.0:
            return
        wt, we, bn, bd = WT[j], WET[j], self.bnum, self.bden
        for h in range(self.n):
            bd[h] += coef * wt[h]
            bn[h] += coef * we[h]

    def step(self, br, t):
        for j in range(self.n):
            d = br[j] - self.br[j]
            if d:
                self._add(j, d)
        self.br = list(br)
        a, b = 1 - 1 / t, 1 / t
        self.avg = [a * x + b * y for x, y in zip(self.avg, br)]
        self.num = [a * x + b * y for x, y in zip(self.num, self.bnum)]
        self.den = [a * x + b * y for x, y in zip(self.den, self.bden)]

    def eq(self, h):
        d = self.den[h]
        return self.num[h] / d if d > 1e-12 else 0.5

    def prob(self, h):
        return self.den[h] / 1225.0


def solve_jam(stacks, value, hero, iters=40, posts=None, responders=None, fold_to=None,
              priors=None):
    """Push/fold equilibrium for a first-in jam (fictitious play: each side's range is the
    average of its best answers to the other's). Exact hand-vs-range equities with card
    removal; the first caller plays the hand out (overcalls are rare and left out).
    Returns the hero's gain from jamming over folding for every hand (value units), the
    hero's jam share, and each caller's (call share, equity needed). (Same results as
    _solve_jam_reference, about 10x faster: ranges are updated incrementally.)"""
    names, idx, E, W = exact()
    sp = Spot(stacks, value, hero, posts, responders, fold_to)
    me = hero
    priors = priors or {}
    prior_mass = {i: [range_prob(h, _sparse(w)) for h in range(len(names))]
                  for i, w in priors.items()}
    need = {i: sp.need(i) for i in sp.behind}
    n = len(names)
    from bots.preflop8 import TOP
    jam0 = [1.0 if TOP[names[j]] <= 0.3 else 0.0 for j in range(n)]
    J = _Avg(n)
    J.step(jam0, 1)                      # the starting guess (what round 1 answers)
    C = {i: _Avg(n, list(priors[i]) if i in priors else None) for i in sp.behind}
    gain = [0.0] * n
    win = {i: sp.win[i][me] for i in sp.behind}
    lose = {i: sp.lose[i][me] for i in sp.behind}
    steal, fold = sp.v_steal[me], sp.v_fold[me]
    for t in range(1, iters + 1):
        eqc = [J.eq(c) for c in range(n)]
        for i in sp.behind:
            C[i].step([1.0 if eqc[c] >= need[i] else 0.0 for c in range(n)], t)
        rows = [(C[i].num, C[i].den, prior_mass.get(i), win[i], lose[i]) for i in sp.behind]
        for h in range(n):
            ev, p_reach = 0.0, 1.0
            for num, den, pm, wi, li in rows:
                d = den[h]
                pc = d / 1225.0
                if pm is not None:
                    m = pm[h]
                    pc = pc / m if m > 0 else 0.0
                if pc > 0:
                    eq = num[h] / d if d > 1e-12 else 0.5
                    ev += p_reach * pc * (eq * wi + (1 - eq) * li)
                p_reach *= 1 - pc
            gain[h] = ev + p_reach * steal - fold
        J.step([1.0 if g > 0 else 0.0 for g in gain], t)   # (round 1 drops the guess)
    jam = J.avg
    calls = {i: C[i].avg for i in sp.behind}
    out = {names[h]: gain[h] for h in range(n)}

    def call_share(i):
        if i not in priors:
            return _share_w(calls[i])
        tot = _share_w(priors[i])
        return _share_w([c * p for c, p in zip(calls[i], priors[i])]) / tot if tot else 0.0
    return {"gain": out, "share": _share([h for h in ORDER if out[h] > 0]),
            "calls": {i: (call_share(i), need[i]) for i in sp.behind}, "spot": sp,
            "jam_range": jam, "call_ranges": calls, "priors": priors}


def _solve_jam_reference(stacks, value, hero, iters=40, posts=None, responders=None,
                         fold_to=None, priors=None):
    """Push/fold equilibrium for a first-in jam (fictitious play: each side's range is the
    average of its best answers to the other's). Exact hand-vs-range equities with card
    removal; the first caller plays the hand out (overcalls are rare and left out).
    Returns the hero's gain from jamming over folding for every hand (value units), the
    hero's jam share, and each caller's (call share, equity needed)."""
    names, idx, E, W = exact()
    sp = Spot(stacks, value, hero, posts, responders, fold_to)
    me = hero
    priors = priors or {}   # responder -> the hands they can hold (e.g. an opener's range)
    prior_mass = {i: [range_prob(h, _sparse(w)) for h in range(len(names))]
                  for i, w in priors.items()}
    need = {i: sp.need(i) for i in sp.behind}
    n = len(names)
    from bots.preflop8 import TOP
    jam = [1.0 if TOP[names[j]] <= 0.3 else 0.0 for j in range(n)]
    calls = {i: [0.0] * n for i in sp.behind}
    gain = [0.0] * n
    for t in range(1, iters + 1):
        rj = _sparse(jam)
        eqc = [range_eq(c, rj) for c in range(n)]
        for i in sp.behind:
            br = [1.0 if eqc[c] >= need[i] else 0.0 for c in range(n)]
            calls[i] = [(1 - 1 / t) * a + b / t for a, b in zip(calls[i], br)]
        sc = {i: _sparse([c * p for c, p in zip(calls[i], priors[i])] if i in priors
                         else calls[i]) for i in sp.behind}
        for h in range(n):
            ev, p_reach = 0.0, 1.0
            for i in sp.behind:
                pc = range_prob(h, sc[i])
                if i in priors:   # given what they can hold
                    m = prior_mass[i][h]
                    pc = pc / m if m > 0 else 0.0
                if pc > 0:
                    eq = range_eq(h, sc[i])
                    ev += p_reach * pc * (eq * sp.win[i][me] + (1 - eq) * sp.lose[i][me])
                p_reach *= 1 - pc
            gain[h] = ev + p_reach * sp.v_steal[me] - sp.v_fold[me]
        br = [1.0 if g > 0 else 0.0 for g in gain]
        jam = [(1 - 1 / t) * a + b / t for a, b in zip(jam, br)]
    out = {names[h]: gain[h] for h in range(n)}
    def call_share(i):
        if i not in priors:
            return _share_w(calls[i])
        tot = _share_w(priors[i])
        return _share_w([c * p for c, p in zip(calls[i], priors[i])]) / tot if tot else 0.0
    return {"gain": out, "share": _share([h for h in ORDER if out[h] > 0]),
            "calls": {i: (call_share(i), need[i]) for i in sp.behind}, "spot": sp,
            "jam_range": jam, "call_ranges": calls, "priors": priors}


# ------------------------------------------------------------------ exploits
# The equilibrium assumes everyone plays it. Real players don't: some call too much, some
# fold too much. These are the best answers to an opponent who calls (or jams) a share of
# hands `scale` times the equilibrium's, from the same spot, so the coach can say what
# changes against a calling station or a nit.

EXPLOIT_SCALES = (("wide", 1.6), ("tight", 0.55))


def _top_share(score, share, prior=None):
    """Weights for the best `share` of hands by score (within prior, if any)."""
    names = exact()[0]
    n = len(names)
    w = list(prior) if prior else [1.0] * n
    total = sum(COMBOS[names[j]] * w[j] for j in range(n))
    target = max(0.0, min(1.0, share)) * total
    out, got = [0.0] * n, 0.0
    for j in sorted(range(n), key=lambda j: -score[j]):
        c = COMBOS[names[j]] * w[j]
        if c <= 0 or got >= target - 1e-9:
            continue
        take = min(1.0, (target - got) / c)
        out[j] = take * w[j]
        got += c * take
    return out


def exploit_jam(res, scales=EXPLOIT_SCALES):
    """{label: {"gain": {hand: value}, "calls": {i: share}}}: the hero's gain from jamming,
    for every hand, if each responder called `scale` times as many hands as in `res` (the
    equilibrium), the best of their hands by equity against the hero's jam range."""
    names = exact()[0]
    n = len(names)
    sp = res["spot"]
    me = sp.hero
    priors = res.get("priors") or {}
    rj = _sparse(res["jam_range"])
    eqc = [range_eq(c, rj) for c in range(n)]
    pmass = {i: [range_prob(h, _sparse(w)) for h in range(n)] for i, w in priors.items()}
    out = {}
    for label, scale in scales:
        ranges, shares = {}, {}
        for i in sp.behind:
            base = res["calls"][i][0]
            # (a stack that's all-in anyway calls almost everything: it isn't a style)
            share = base if (base >= 0.9 or sp.stacks[i] < 5) else base * scale
            ranges[i] = _sparse(_top_share(eqc, share, priors.get(i)))
            shares[i] = min(1.0, share)
        gain = {}
        for h in range(n):
            ev, p_reach = 0.0, 1.0
            for i in sp.behind:
                pc = range_prob(h, ranges[i])
                if i in pmass:
                    m = pmass[i][h]
                    pc = pc / m if m > 0 else 0.0
                if pc > 0:
                    eq = range_eq(h, ranges[i])
                    ev += p_reach * pc * (eq * sp.win[i][me] + (1 - eq) * sp.lose[i][me])
                p_reach *= 1 - pc
            gain[names[h]] = ev + p_reach * sp.v_steal[me] - sp.v_fold[me]
        out[label] = {"gain": gain, "calls": shares, "scale": scale}
    return out


def exploit_call(res, scales=EXPLOIT_SCALES):
    """For a call_from result: the hero's gain from calling, for every hand, if the jammer
    jammed `scale` times as many hands (their best ones by their own jam gain)."""
    names = exact()[0]
    n = len(names)
    sp, hero = res["spot"], res["hero"]
    jam_gain = res["jam_gain"]
    score = [jam_gain[names[j]] for j in range(n)]
    win, lose, fold = sp.lose[hero][hero], sp.win[hero][hero], sp.v_steal[hero]
    out = {}
    for label, scale in scales:
        rj = _sparse(_top_share(score, res["jam_share"] * scale))
        gain = {}
        for h in range(n):
            eq = range_eq(h, rj)
            gain[names[h]] = eq * win + (1 - eq) * lose - fold
        out[label] = {"gain": gain, "share": min(1.0, res["jam_share"] * scale), "scale": scale}
    return out


def flips(base, other, k=6):
    """(hands that become plays, hands that stop being plays) going from gains `base` to
    `other`, the clearest first."""
    add = sorted((h for h in base if base[h] <= 0 < other[h]), key=lambda h: -other[h])
    drop = sorted((h for h in base if other[h] <= 0 < base[h]), key=lambda h: other[h])
    return add[:k], drop[:k]


def call_weights(res, i):
    """Responder i's calling hands as sparse weights (within their range, if they have one)."""
    w = res["call_ranges"][i]
    p = res.get("priors", {}).get(i)
    return _sparse([a * b for a, b in zip(w, p)] if p else w)


def call_prob(res, i, h):
    """Chance responder i calls (holds a calling hand), given we hold hand index h."""
    pc = range_prob(h, call_weights(res, i))
    p = res.get("priors", {}).get(i)
    if p:
        m = range_prob(h, _sparse(p))
        pc = pc / m if m > 0 else 0.0
    return pc


def solve_call(stacks, value, jammer, hero):
    """The hero facing the jammer's equilibrium first-in jam (everyone between folded):
    the gain from calling over folding for every hand, against the jammer's real range."""
    names = exact()[0]
    res = solve_jam(stacks, value, jammer)
    sp, rj = res["spot"], _sparse(res["jam_range"])
    win, lose, fold = sp.lose[hero][hero], sp.win[hero][hero], sp.v_steal[hero]
    gain = {}
    for h in range(len(names)):
        eq = range_eq(h, rj)
        gain[names[h]] = eq * win + (1 - eq) * lose - fold
    return {"gain": gain, "share": _share([h for h in ORDER if gain[h] > 0]),
            "jam_share": res["share"], "need": sp.need(hero), "spot": sp,
            "jam_range": res["jam_range"]}


# ------------------------------------------------------------------ games with traps
# Push/fold leaves out the traps: a big hand that limps or min-raises so the others put the
# chips in. Two short-stack games are solved here under ICM, by fictitious play like
# solve_jam (exact hand-vs-range equities with card removal; later rounds weigh more):
#
#   solve_sb    folded to the small blind: fold, limp, raise to 3 bb or jam. The big blind
#               answers a limp by checking, raising to 4 bb (an iso-raise) or jamming; the
#               raise by folding, calling or jamming; a jam by calling or folding. The small
#               blind answers the iso-raise by folding, calling or jamming, and a jam by
#               calling or folding; the big blind then calls or folds that re-jam.
#   solve_open  folded to a short player (not a blind): fold, raise to 2 bb or jam. Everyone
#               behind answers the raise by re-jamming or folding (the big blind may also
#               call) and a jam by calling or folding; the opener calls or folds a re-jam.
#
# Pots that go to the flop without an all-in are checked down: the player out of position
# gets OOP_REALIZE of its equity, the other the rest. Strong hands trap (limp, raise) when
# that earns more than jamming, at the frequencies that make it work.

SB_RAISE_TO = 3.0        # the small blind's raise (bb)
SB_RAISE_MIN_BB = 8.5    # from this stack (bb): the raise is at most about a third of it
SB_ISO_TO = 4.0          # the big blind's raise over a limp (bb)
BB_3BET_TO = 7.5         # the big blind's small 3-bet over the small blind's raise (bb)
BB_3BET_MIN_BB = 15.0    # from this effective stack (bb)
SB_ISO_MIN_BB = 10.0     # from this effective stack (bb)
OPEN_TO = 2.0            # the min-raise first in (bb)
OPEN_MIN_BB = 6.0        # from this stack (bb)
OOP_REALIZE = 0.9        # checked-down limped pots: the player out of position (the small
#                          blind) gets this share of its equity, the other the rest
CALLER_REALIZE = 0.8     # a raised pot called out of position (the big blind calling a raise,
#                          the small blind calling the iso-raise): the caller's share
SB_LIMP_REALIZE = OOP_REALIZE
SB_ACTIONS = ("fold", "limp", "raise", "jam")
GAME_ITERS = 150
OPEN_ITERS = 120         # (the min-raise game: more players, a little less precision)


def _real(eq, base):
    """The share of a checked-down pot a hand with equity `eq` gets out of position: weak
    hands realize less of their equity (they check-fold, get bet off), strong ones nearly
    all of the base share."""
    eq = max(0.0, min(1.0, eq))
    return base * eq * math.sqrt(min(1.0, 2 * eq))


def _sums(avgs, h):
    return sum(a.num[h] for a in avgs), sum(a.den[h] for a in avgs)


def _eqd(num, den):
    return num / den if den > 1e-12 else 0.5


def _norm_mix(d):
    t = sum(d.values())
    return {k: v / t for k, v in d.items()} if t > 1e-12 else d


def solve_sb(stacks, value, iters=GAME_ITERS, linear=True):
    """The small blind first in against the big blind (everyone else folded). stacks: bb in
    position order, the big blind last (with its ante). Returns the strategies and values
    at every decision of the game ("nodes": {node: {"mix": {hand: {action: freq}}, "ev":
    {hand: {action: value}}}}) plus ranges for the all-in calls, or None when it doesn't
    apply (too short to limp)."""
    names, idx, E, W = exact()
    n = len(stacks)
    s, b = n - 2, n - 1
    ante = [0.0] * n
    ante[b] = min(ANTE, stacks[b])
    blind = [0.0] * n
    blind[s] = min(0.5, stacks[s])
    blind[b] = min(1.0, stacks[b] - ante[b])
    live_b = stacks[b] - ante[b]
    if stacks[s] < 2.0 or live_b < 2.0 or blind[b] < 1.0:
        return None
    eff = min(stacks[s], live_b)
    can_raise = stacks[s] >= SB_RAISE_MIN_BB and eff > SB_RAISE_TO + 1
    can_iso = eff >= SB_ISO_MIN_BB
    can_3b = can_raise and eff >= BB_3BET_MIN_BB and eff > BB_3BET_TO + 2
    after = [x - y - z for x, y, z in zip(stacks, blind, ante)]

    def moved(ds, db):
        st = list(stacks)
        st[s] += ds
        st[b] += db
        return st
    a_b = ante[b]
    st = {"fold": moved(-blind[s], blind[s]),
          "win": moved(blind[b] + a_b, -(blind[b] + a_b)),
          "lose_limp": moved(-blind[b], blind[b]),
          "win_iso": moved(SB_ISO_TO + a_b, -(SB_ISO_TO + a_b)),
          "lose_iso": moved(-SB_ISO_TO, SB_ISO_TO),
          "win_raise": moved(SB_RAISE_TO + a_b, -(SB_RAISE_TO + a_b)),
          "lose_raise": moved(-SB_RAISE_TO, SB_RAISE_TO),
          "win_3b": moved(BB_3BET_TO + a_b, -(BB_3BET_TO + a_b)),
          "lose_3b": moved(-BB_3BET_TO, BB_3BET_TO),
          "aw": Spot._showdown(stacks, blind, ante, after, s, b, s),
          "al": Spot._showdown(stacks, blind, ante, after, s, b, b)}
    V = {k: value(v, [s, b]) for k, v in st.items()}
    vs = {k: v[s] for k, v in V.items()}
    vb = {k: v[b] for k, v in V.items()}
    nh = len(names)
    R, RC_ = OOP_REALIZE, CALLER_REALIZE
    from bots.preflop8 import TOP
    top = lambda share: [1.0 if TOP[names[j]] <= share else 0.0 for j in range(nh)]  # noqa

    # the small blind's plans: limp (answering the iso-raise r: f/c/j, a jam q: c/f), raise
    # (answering a jam q: c/f), jam
    LP = {(r, q): _Avg(nh) for r in "fcj" for q in "cf"}
    RP = {(r3, q): _Avg(nh) for r3 in "fcj" for q in "cf"}   # raise: vs a 3-bet r3, a jam q
    J = _Avg(nh)
    # the big blind's: over a limp check X / iso-raise and call (Ic) or fold (If) a re-jam /
    # jam (JL); over the raise call (RC) / jam (RJ); against a jam call (C)
    X, Ic, If, JL, RC, RJ, C = (_Avg(nh) for _ in range(7))
    B3c, B3f = _Avg(nh), _Avg(nh)        # the big blind 3-bets small and calls / folds a jam
    X.step(top(0.6), 1)
    JL.step([1.0 - x for x in top(0.6)], 1)
    RJ.step(top(0.2), 1)
    C.step(top(0.35), 1)

    def sb_vals(h):
        """The small blind's values at every decision for hand h, against the big blind's
        average strategy: {node: {action: value}}."""
        out = {}
        nc, dc = C.num[h], C.den[h]
        pc = dc / 1225.0
        eqc = _eqd(nc, dc)
        out["sb"] = {"fold": vs["fold"],
                     "jam": (1 - pc) * vs["win"] + pc * (eqc * vs["aw"] + (1 - eqc) * vs["al"])}
        # limp
        px = X.den[h] / 1225.0
        eqx = _eqd(X.num[h], X.den[h])
        sx = _real(eqx, R)
        chk = sx * vs["win"] + (1 - sx) * vs["lose_limp"]
        ni, di = _sums((Ic, If), h)
        pi = di / 1225.0
        eqi = _eqd(ni, di)
        pcall = Ic.den[h] / di if di > 1e-12 else 0.0
        eq_ic = _eqd(Ic.num[h], Ic.den[h])
        iso = {"fold": vs["lose_limp"],
               "call": _real(eqi, RC_) * vs["win_iso"] + (1 - _real(eqi, RC_)) * vs["lose_iso"],
               "jam": (1 - pcall) * vs["win_iso"] + pcall * (eq_ic * vs["aw"] + (1 - eq_ic) * vs["al"])}
        pj = JL.den[h] / 1225.0
        eqj = _eqd(JL.num[h], JL.den[h])
        vjl = {"call": eqj * vs["aw"] + (1 - eqj) * vs["al"], "fold": vs["lose_limp"]}
        out["sb_vs_iso"], out["sb_vs_jam_limp"] = iso, vjl
        out["sb"]["limp"] = px * chk + pi * max(iso.values()) + pj * max(vjl.values())
        if can_raise:
            prc = RC.den[h] / 1225.0
            eqr = _eqd(RC.num[h], RC.den[h])
            prj = RJ.den[h] / 1225.0
            eqrj = _eqd(RJ.num[h], RJ.den[h])
            vjr = {"call": eqrj * vs["aw"] + (1 - eqrj) * vs["al"], "fold": vs["lose_raise"]}
            out["sb_vs_jam_raise"] = vjr
            sr = _real(eqr, R)
            chk_r = sr * vs["win_raise"] + (1 - sr) * vs["lose_raise"]
            ev_r = prc * chk_r + prj * max(vjr.values())
            p3 = 0.0
            if can_3b:
                d3 = B3c.den[h] + B3f.den[h]
                p3 = d3 / 1225.0
                eq3 = _eqd(B3c.num[h] + B3f.num[h], d3)
                pc3 = B3c.den[h] / d3 if d3 > 1e-12 else 0.0
                eqc3 = _eqd(B3c.num[h], B3c.den[h])
                s3 = _real(eq3, RC_)                      # (the SB calls out of position)
                v3 = {"fold": vs["lose_raise"],
                      "call": s3 * vs["win_3b"] + (1 - s3) * vs["lose_3b"],
                      "jam": (1 - pc3) * vs["win_3b"] + pc3 * (eqc3 * vs["aw"] + (1 - eqc3) * vs["al"])}
                out["sb_vs_3bet"] = v3
                ev_r += p3 * max(v3.values())
            out["sb"]["raise"] = max(0.0, 1 - prc - prj - p3) * vs["win"] + ev_r
        return out

    def bb_vals(j):
        """The big blind's values at every decision for hand j, against the small blind's
        average strategy (None where the small blind never gets there)."""
        out = {}
        dl = sum(a.den[j] for a in LP.values())
        if dl > 1e-12:
            eqb = sum(a.num[j] for a in LP.values()) / dl          # the BB's equity
            share = 1 - _real(1 - eqb, R)                           # (the SB out of position)
            node = {"check": share * vb["lose_limp"] + (1 - share) * vb["win"]}
            calls = [LP[(r, "c")] for r in "fcj"]
            nc, dc = _sums(calls, j)
            pc = dc / dl
            eqc = _eqd(nc, dc)
            node["jam"] = (1 - pc) * vb["lose_limp"] + pc * (eqc * vb["al"] + (1 - eqc) * vb["aw"])
            rejam = None
            if can_iso:
                parts = {}
                for r in "fcj":
                    nr, dr = _sums([LP[(r, "c")], LP[(r, "f")]], j)
                    parts[r] = (nr, dr)
                f_ = parts["f"][1] / dl
                c_ = parts["c"][1] / dl
                j_ = parts["j"][1] / dl
                eqc2 = _eqd(*parts["c"])
                sh2 = 1 - _real(1 - eqc2, RC_)
                ev_c = sh2 * vb["lose_iso"] + (1 - sh2) * vb["win_iso"]
                eqj2 = _eqd(*parts["j"])
                rejam = {"call": eqj2 * vb["al"] + (1 - eqj2) * vb["aw"], "fold": vb["win_iso"]}
                node["iso"] = f_ * vb["lose_limp"] + c_ * ev_c + j_ * max(rejam.values())
            out["bb_vs_limp"] = node
            out["bb_vs_rejam_iso"] = rejam
        nr_, dr = _sums(list(RP.values()), j)
        if can_raise and dr > 1e-12:
            eqb = nr_ / dr
            share = 1 - _real(1 - eqb, R)
            nc, dc = _sums([RP[(r3, "c")] for r3 in "fcj"], j)
            pc = dc / dr
            eqc = _eqd(nc, dc)
            node = {"fold": vb["win"],
                    "call": share * vb["lose_raise"] + (1 - share) * vb["win_raise"],
                    "jam": (1 - pc) * vb["lose_raise"] + pc * (eqc * vb["al"] + (1 - eqc) * vb["aw"])}
            four = None
            if can_3b:
                parts = {r3: _sums([RP[(r3, "c")], RP[(r3, "f")]], j) for r3 in "fcj"}
                f_, c_, j_ = (parts[x][1] / dr for x in "fcj")
                eqcl = _eqd(*parts["c"])
                sh = 1 - _real(1 - eqcl, RC_)              # (the SB calls out of position)
                eqj4 = _eqd(*parts["j"])
                four = {"call": eqj4 * vb["al"] + (1 - eqj4) * vb["aw"], "fold": vb["win_3b"]}
                node["3bet"] = (f_ * vb["lose_raise"] + c_ * (sh * vb["lose_3b"] + (1 - sh) * vb["win_3b"])
                                + j_ * max(four.values()))
            out["bb_vs_raise"], out["bb_vs_4bet"] = node, four
        if J.den[j] > 1e-12:
            eqb = J.num[j] / J.den[j]
            out["bb_vs_jam"] = {"call": eqb * vb["al"] + (1 - eqb) * vb["aw"], "fold": vb["win"]}
        return out

    for t in range(2, iters + 2):
        tw = (t + 1) / 2 if linear else t     # (later rounds weigh more)
        brs = {k: [0.0] * nh for k in list(LP) + [("R",) + k for k in RP] + ["J"]}
        for h in range(nh):
            v = sb_vals(h)
            first = max(v["sb"], key=v["sb"].get)
            if first == "limp":
                r = max(v["sb_vs_iso"], key=v["sb_vs_iso"].get)[0] if can_iso else "f"
                q = max(v["sb_vs_jam_limp"], key=v["sb_vs_jam_limp"].get)[0]
                brs[(r, q)][h] = 1.0
            elif first == "raise":
                q = max(v["sb_vs_jam_raise"], key=v["sb_vs_jam_raise"].get)[0]
                r3 = max(v["sb_vs_3bet"], key=v["sb_vs_3bet"].get)[0] if can_3b else "f"
                brs[("R", r3, q)][h] = 1.0
            elif first == "jam":
                brs["J"][h] = 1.0
        for k, a in LP.items():
            a.step(brs[k], tw)
        for k, a in RP.items():
            a.step(brs[("R",) + k], tw)
        J.step(brs["J"], tw)
        bx, bic, bif, bjl, brc, brj, bc, b3c, b3f = ([0.0] * nh for _ in range(9))
        for j in range(nh):
            v = bb_vals(j)
            node = v.get("bb_vs_limp")
            if node:
                best = max(node, key=node.get)
                if best == "check":
                    bx[j] = 1.0
                elif best == "jam":
                    bjl[j] = 1.0
                else:
                    rj = v["bb_vs_rejam_iso"]
                    (bic if rj["call"] > rj["fold"] else bif)[j] = 1.0
            else:
                bx[j] = 1.0
            node = v.get("bb_vs_raise")
            if node:
                best = max(node, key=node.get)
                if best == "call":
                    brc[j] = 1.0
                elif best == "jam":
                    brj[j] = 1.0
                elif best == "3bet":
                    f4 = v["bb_vs_4bet"]
                    (b3c if f4["call"] > f4["fold"] else b3f)[j] = 1.0
            node = v.get("bb_vs_jam")
            if node and node["call"] > node["fold"]:
                bc[j] = 1.0
        for a, br in ((X, bx), (Ic, bic), (If, bif), (JL, bjl), (RC, brc), (RJ, brj), (C, bc),
                      (B3c, b3c), (B3f, b3f)):
            a.step(br, tw)

    # the strategies and values at every decision
    nodes = {k: {"mix": {}, "ev": {}} for k in (
        "sb", "sb_vs_iso", "sb_vs_jam_limp", "sb_vs_jam_raise", "sb_vs_3bet",
        "bb_vs_limp", "bb_vs_rejam_iso", "bb_vs_raise", "bb_vs_4bet", "bb_vs_jam")}
    for h in range(nh):
        hn = names[h]
        v = sb_vals(h)
        L = sum(a.avg[h] for a in LP.values())
        Rr = sum(a.avg[h] for a in RP.values())
        m = {"limp": L, "jam": J.avg[h]}
        if can_raise:
            m["raise"] = Rr
        m["fold"] = max(0.0, 1.0 - sum(m.values()))
        nodes["sb"]["mix"][hn], nodes["sb"]["ev"][hn] = m, v["sb"]
        if L > 1e-9:
            nodes["sb_vs_iso"]["mix"][hn] = _norm_mix(
                {a: sum(LP[(a[0], q)].avg[h] for q in "cf") for a in ("fold", "call", "jam")})
            nodes["sb_vs_jam_limp"]["mix"][hn] = _norm_mix(
                {a: sum(LP[(r, a[0])].avg[h] for r in "fcj") for a in ("call", "fold")})
        else:     # (never limps: what it would do)
            nodes["sb_vs_iso"]["mix"][hn] = {max(v["sb_vs_iso"], key=v["sb_vs_iso"].get): 1.0}
            nodes["sb_vs_jam_limp"]["mix"][hn] = {
                max(v["sb_vs_jam_limp"], key=v["sb_vs_jam_limp"].get): 1.0}
        nodes["sb_vs_iso"]["ev"][hn] = v["sb_vs_iso"]
        nodes["sb_vs_jam_limp"]["ev"][hn] = v["sb_vs_jam_limp"]
        if can_raise:
            nodes["sb_vs_jam_raise"]["mix"][hn] = (
                _norm_mix({a: sum(RP[(r3, a[0])].avg[h] for r3 in "fcj") for a in ("call", "fold")})
                if Rr > 1e-9 else {max(v["sb_vs_jam_raise"], key=v["sb_vs_jam_raise"].get): 1.0})
            nodes["sb_vs_jam_raise"]["ev"][hn] = v["sb_vs_jam_raise"]
            if can_3b:
                nodes["sb_vs_3bet"]["mix"][hn] = (
                    _norm_mix({a: sum(RP[(a[0], q)].avg[h] for q in "cf") for a in ("fold", "call", "jam")})
                    if Rr > 1e-9 else {max(v["sb_vs_3bet"], key=v["sb_vs_3bet"].get): 1.0})
                nodes["sb_vs_3bet"]["ev"][hn] = v["sb_vs_3bet"]
        w = bb_vals(h)
        if w.get("bb_vs_limp"):
            mm = {"check": X.avg[h], "jam": JL.avg[h]}
            if can_iso:
                mm["iso"] = Ic.avg[h] + If.avg[h]
            nodes["bb_vs_limp"]["mix"][hn] = _norm_mix(mm)
            nodes["bb_vs_limp"]["ev"][hn] = w["bb_vs_limp"]
        if w.get("bb_vs_rejam_iso"):
            ii = Ic.avg[h] + If.avg[h]
            nodes["bb_vs_rejam_iso"]["mix"][hn] = (
                _norm_mix({"call": Ic.avg[h], "fold": If.avg[h]}) if ii > 1e-9 else
                {max(w["bb_vs_rejam_iso"], key=w["bb_vs_rejam_iso"].get): 1.0})
            nodes["bb_vs_rejam_iso"]["ev"][hn] = w["bb_vs_rejam_iso"]
        if w.get("bb_vs_raise"):
            b3 = B3c.avg[h] + B3f.avg[h]
            mm = {"fold": max(0.0, 1 - RC.avg[h] - RJ.avg[h] - b3), "call": RC.avg[h], "jam": RJ.avg[h]}
            if can_3b:
                mm["3bet"] = b3
            nodes["bb_vs_raise"]["mix"][hn] = _norm_mix(mm)
            nodes["bb_vs_raise"]["ev"][hn] = w["bb_vs_raise"]
            if can_3b and w.get("bb_vs_4bet"):
                nodes["bb_vs_4bet"]["mix"][hn] = (
                    _norm_mix({"call": B3c.avg[h], "fold": B3f.avg[h]}) if b3 > 1e-9 else
                    {max(w["bb_vs_4bet"], key=w["bb_vs_4bet"].get): 1.0})
                nodes["bb_vs_4bet"]["ev"][hn] = w["bb_vs_4bet"]
        if w.get("bb_vs_jam"):
            nodes["bb_vs_jam"]["mix"][hn] = _norm_mix({"call": C.avg[h], "fold": 1 - C.avg[h]})
            nodes["bb_vs_jam"]["ev"][hn] = w["bb_vs_jam"]
    limp = [sum(a.avg[h] for a in LP.values()) for h in range(nh)]
    ranges = {
        "limp": limp, "raise": [sum(a.avg[h] for a in RP.values()) for h in range(nh)],
        "sb_4bet": [sum(RP[("j", q)].avg[h] for q in "cf") for h in range(nh)],
        "bb_3bet": [B3c.avg[h] + B3f.avg[h] for h in range(nh)],
        "jam": list(J.avg),
        "limp_rejam": [sum(LP[("j", q)].avg[h] for q in "cf") for h in range(nh)],
        "bb_iso": [Ic.avg[h] + If.avg[h] for h in range(nh)],
        "bb_jam_limp": list(JL.avg), "bb_jam_raise": list(RJ.avg), "bb_call_jam": list(C.avg)}
    return {"game": "sb", "nodes": nodes, "ranges": ranges, "can_raise": can_raise,
            "can_iso": can_iso, "can_3b": can_3b, "eff": eff, "stacks": list(stacks), "s": s, "b": b,
            "values": {"sb": vs, "bb": vb},
            # (the first decision, under the names the first version used)
            "mix": nodes["sb"]["mix"], "ev": nodes["sb"]["ev"],
            "bb": {"vs_limp": [nodes["bb_vs_limp"]["mix"].get(names[h], {}).get("jam", 0.0)
                               for h in range(nh)],
                   "vs_raise": ranges["bb_jam_raise"], "vs_jam": ranges["bb_call_jam"]}}


THREE_BET_TO = 5.0       # a small 3-bet over the min-raise (bb)
THREE_BET_MIN_BB = 10.0  # from this effective stack (bb)


def solve_open(stacks, value, opener, iters=OPEN_ITERS, linear=True):
    """A short player first in (not a blind; everyone before folded): fold, raise to 2 bb or
    jam. Everyone behind, in turn, answers the raise by folding, calling (a trap: the pot
    goes to the flop), 3-betting to 5 bb or jamming, and a jam by calling or folding. The
    opener answers a 3-bet by folding, calling or jamming (and the 3-bettor then calls or
    folds), a jam by calling or folding. The first player to put chips in ends it (the rest
    fold: no squeezes or overcalls). Returns {"nodes": {"open", "open_vs_3bet",
    "open_vs_rejam", "vs_open:i", "vs_4bet:i", "vs_jam:i"}, "ranges": ...} or None."""
    names, idx, E, W = exact()
    n = len(stacks)
    o = opener
    if not 0 <= o < n - 2:
        return None
    b, sbi = n - 1, n - 2
    ante = [0.0] * n
    ante[b] = min(ANTE, stacks[b])
    blind = [0.0] * n
    blind[sbi] = min(0.5, stacks[sbi])
    blind[b] = min(1.0, stacks[b] - ante[b])
    if stacks[o] < OPEN_MIN_BB:
        return None
    resp = list(range(o + 1, n))
    after = [x - y - z for x, y, z in zip(stacks, blind, ante)]
    nh = len(names)
    OR, CR = OOP_REALIZE, CALLER_REALIZE

    def pot(contrib, winner):
        """Stacks after a pot (everyone's chips in, the blinds and the ante included) that
        `winner` takes."""
        st = list(stacks)
        c = {sbi: blind[sbi], b: blind[b]}
        c.update(contrib)
        tot = ante[b]
        st[b] -= ante[b]
        for k, x in c.items():
            st[k] -= x
            tot += x
        st[winner] += tot
        return st
    everyone = list(range(n))
    val = lambda st: value(st, everyone)            # noqa: E731
    v_steal = val(pot({o: 0.0}, o))
    v_fold = val(pot({}, b))                         # (the opener folds: the BB takes it)
    can3 = {}
    V = {}
    for i in resp:
        bl = blind[i]
        # flats and small 3-bets: only the big blind, who closes the action (anyone else has
        # players behind who'd squeeze, which this game doesn't model: there it's jam or fold)
        can3[i] = (i == b and min(stacks[o], stacks[i] - ante[i]) >= THREE_BET_MIN_BB and
                   stacks[o] > THREE_BET_TO + 1 and stacks[i] - ante[i] > THREE_BET_TO + 1)
        V[i] = {"aw": val(Spot._showdown(stacks, blind, ante, after, o, i, o)),
                "al": val(Spot._showdown(stacks, blind, ante, after, o, i, i)),
                "ofold": val(pot({o: OPEN_TO, i: bl}, i)),          # o folds to i
                "tfold": val(pot({o: OPEN_TO, i: THREE_BET_TO}, o)),  # i folds to the 4-bet
                "ccw": val(pot({o: OPEN_TO, i: OPEN_TO}, o)),       # the raise called: o wins
                "ccl": val(pot({o: OPEN_TO, i: OPEN_TO}, i)),
                "c3w": val(pot({o: THREE_BET_TO, i: THREE_BET_TO}, o)),   # the 3-bet called
                "c3l": val(pot({o: THREE_BET_TO, i: THREE_BET_TO}, i))}
    oop_blind = {i: i >= sbi for i in resp}          # (the blinds act first after the flop)
    from bots.preflop8 import TOP
    top = lambda share: [1.0 if TOP[names[j]] <= share else 0.0 for j in range(nh)]  # noqa
    # the opener's plans: open (answering a 3-bet r3: f/c/j, a re-jam rj: c/f), jam
    OP = {(r3, rj): _Avg(nh) for r3 in "fcj" for rj in "cf"}
    J = _Avg(nh)
    # each responder: call / 3-bet and call (Bc) or fold (Bf) the 4-bet / jam over the
    # raise; call the opener's jam (CJ)
    RC = {i: _Avg(nh) for i in resp}
    BC = {i: _Avg(nh) for i in resp}
    BF = {i: _Avg(nh) for i in resp}
    RJ = {i: _Avg(nh) for i in resp}
    CJ = {i: _Avg(nh) for i in resp}
    # a realistic start (fictitious play keeps its early rounds in the average): the opener
    # raises about 10% and jams 5%; behind, flats about 8%, 3-bets 3%, jams 3%
    band = lambda lo, hi: [1.0 if lo < TOP[names[j]] <= hi else 0.0 for j in range(nh)]  # noqa
    OP[("j", "c")].step(top(0.05), 1)
    OP[("f", "f")].step(band(0.05, 0.10), 1)
    J.step(band(0.10, 0.15), 1)
    for i in resp:
        BC[i].step(top(0.03), 1)
        RJ[i].step(band(0.03, 0.06), 1)
        if i == b:
            RC[i].step(band(0.06, 0.45), 1)
        CJ[i].step(top(0.12), 1)

    # (flat per-responder numbers for the opener's side: o_vals runs a million times)
    RL = []
    for i in resp:
        v = V[i]
        RL.append((CJ[i], RC[i], BC[i], BF[i], RJ[i], oop_blind[i],
                   v["aw"][o], v["al"][o], v["ofold"][o], v["tfold"][o],
                   v["ccw"][o], v["ccl"][o], v["c3w"][o], v["c3l"][o]))
    o_steal, o_fold = v_steal[o], v_fold[o]

    def o_fast(h):
        """(jam, base, 3-bet answers f/c/j, re-jam answers c/f) for the opener's hand h."""
        ev, reach = 0.0, 1.0
        for (cj, rc, bc, bf, rj, oopb, aw, al, ofo, tfo, ccw, ccl, c3w, c3l) in RL:
            d = cj.den[h]
            if d > 1e-12:
                pc = d / 1225.0
                eq = cj.num[h] / d
                ev += reach * pc * (eq * aw + (1 - eq) * al)
                reach *= 1 - pc
        jam = ev + reach * o_steal
        base = t3f = t3c = t3j = tjc = tjf = 0.0
        reach = 1.0
        for (cj, rc, bc, bf, rj, oopb, aw, al, ofo, tfo, ccw, ccl, c3w, c3l) in RL:
            step = 0.0
            d = rc.den[h]
            if d > 1e-12:                         # the raise called: checked down
                p = d / 1225.0
                eq = rc.num[h] / d
                share = (1 - _real(1 - eq, CR)) if oopb else _real(eq, OR)
                base += reach * p * (share * ccw + (1 - share) * ccl)
                step += p
            dc, df = bc.den[h], bf.den[h]
            d3 = dc + df
            if d3 > 1e-12:                        # 3-bet
                p = d3 / 1225.0
                eq3 = (bc.num[h] + bf.num[h]) / d3
                share = (1 - _real(1 - eq3, OR)) if oopb else _real(eq3, CR)
                pcall = dc / d3
                eqc = bc.num[h] / dc if dc > 1e-12 else 0.5
                rp = reach * p
                t3f += rp * ofo
                t3c += rp * (share * c3w + (1 - share) * c3l)
                t3j += rp * ((1 - pcall) * tfo + pcall * (eqc * aw + (1 - eqc) * al))
                step += p
            d = rj.den[h]
            if d > 1e-12:                         # re-jam
                p = d / 1225.0
                eq = rj.num[h] / d
                rp = reach * p
                tjc += rp * (eq * aw + (1 - eq) * al)
                tjf += rp * ofo
                step += p
            reach *= max(0.0, 1 - step)
        base += reach * o_steal
        return jam, base, t3f, t3c, t3j, tjc, tjf

    def o_vals(h):
        """The opener's values for hand h: {"fold", "jam", "open"} and its best answers."""
        jam, base, t3f, t3c, t3j, tjc, tjf = o_fast(h)
        t3 = {"f": t3f, "c": t3c, "j": t3j}
        tj = {"c": tjc, "f": tjf}
        r3 = max(t3, key=t3.get)
        rj = max(tj, key=tj.get)
        return ({"fold": o_fold, "jam": jam, "open": base + t3[r3] + tj[rj]},
                {k: base + x + tj[rj] for k, x in t3.items()},
                {k: base + t3[r3] + x for k, x in tj.items()}, r3, rj)

    AGG = {}

    def refresh_aggs():
        """The opener's open range and its parts, (num, den) for every responder hand, once
        per round (every responder reads them)."""
        for j in range(nh):
            tot = [0.0, 0.0]
            by3 = {r: [0.0, 0.0] for r in "fcj"}
            byj = {r: [0.0, 0.0] for r in "cf"}
            for (r3, rj), a in OP.items():
                x, y = a.num[j], a.den[j]
                tot[0] += x
                tot[1] += y
                by3[r3][0] += x
                by3[r3][1] += y
                byj[rj][0] += x
                byj[rj][1] += y
            AGG[j] = (tot, by3, byj)

    def o_ranges(j):
        return AGG[j]

    def r_vals(i, j):
        """Responder i with hand j: against the raise ({fold, call, 3bet, jam} and its answer
        to a 4-bet), against a 4-bet, against the opener's jam."""
        out = {}
        v = V[i]
        tot, by3, byj = o_ranges(j)
        dO = tot[1]
        if dO > 1e-12:
            eq = tot[0] / dO                      # i's equity against the raise
            share = _real(eq, CR) if oop_blind[i] else 1 - _real(1 - eq, OR)
            node = {"fold": v_steal[i]}
            if i == b:                            # (the big blind can call: it closes it)
                node["call"] = share * v["ccl"][i] + (1 - share) * v["ccw"][i]
            pc = byj["c"][1] / dO
            eqc = _eqd(*byj["c"])
            node["jam"] = (1 - pc) * v["ofold"][i] + pc * (eqc * v["al"][i] + (1 - eqc) * v["aw"][i])
            four = None
            if can3[i]:
                pf, pcl, pj = by3["f"][1] / dO, by3["c"][1] / dO, by3["j"][1] / dO
                eqcl = _eqd(*by3["c"])
                sh = _real(eqcl, OR) if oop_blind[i] else 1 - _real(1 - eqcl, CR)
                eqj = _eqd(*by3["j"])
                four = {"call": eqj * v["al"][i] + (1 - eqj) * v["aw"][i], "fold": v["tfold"][i]}
                node["3bet"] = (pf * v["ofold"][i] + pcl * (sh * v["c3l"][i] + (1 - sh) * v["c3w"][i])
                                + pj * max(four.values()))
            out["vs_open"], out["vs_4bet"] = node, four
        if J.den[j] > 1e-12:
            eq = J.num[j] / J.den[j]
            out["vs_jam"] = {"call": eq * v["al"][i] + (1 - eq) * v["aw"][i], "fold": v_steal[i]}
        return out

    for t in range(2, iters + 2):
        tw = (t + 1) / 2 if linear else t
        bo = {k: [0.0] * nh for k in OP}
        bj = [0.0] * nh
        for h in range(nh):
            jam, base, t3f, t3c, t3j, tjc, tjf = o_fast(h)
            if t3f >= t3c and t3f >= t3j:
                r3, x3 = "f", t3f
            elif t3c >= t3j:
                r3, x3 = "c", t3c
            else:
                r3, x3 = "j", t3j
            rj, xj = ("c", tjc) if tjc > tjf else ("f", tjf)
            opn = base + x3 + xj
            if opn > jam and opn > o_fold:
                bo[(r3, rj)][h] = 1.0
            elif jam > o_fold:
                bj[h] = 1.0
        for k, a in OP.items():
            a.step(bo[k], tw)
        J.step(bj, tw)
        refresh_aggs()
        for i in resp:
            bc, bbc, bbf, brj, bcj = ([0.0] * nh for _ in range(5))
            for j in range(nh):
                w = r_vals(i, j)
                node = w.get("vs_open")
                if node:
                    best = max(node, key=node.get)
                    if best == "call":
                        bc[j] = 1.0
                    elif best == "jam":
                        brj[j] = 1.0
                    elif best == "3bet":
                        f4 = w["vs_4bet"]
                        (bbc if f4["call"] > f4["fold"] else bbf)[j] = 1.0
                node = w.get("vs_jam")
                if node and node["call"] > node["fold"]:
                    bcj[j] = 1.0
            RC[i].step(bc, tw)
            BC[i].step(bbc, tw)
            BF[i].step(bbf, tw)
            RJ[i].step(brj, tw)
            CJ[i].step(bcj, tw)

    refresh_aggs()
    nodes = {k: {"mix": {}, "ev": {}} for k in ("open", "open_vs_3bet", "open_vs_rejam")}
    for i in resp:
        for k in ("vs_open", "vs_4bet", "vs_jam"):
            nodes[f"{k}:{i}"] = {"mix": {}, "ev": {}}
    for h in range(nh):
        hn = names[h]
        v, ev3, evj, r3, rj = o_vals(h)
        opn = sum(a.avg[h] for a in OP.values())
        m = {"open": opn, "jam": J.avg[h]}
        m["fold"] = max(0.0, 1 - sum(m.values()))
        nodes["open"]["mix"][hn], nodes["open"]["ev"][hn] = m, v
        if opn > 1e-9:
            nodes["open_vs_3bet"]["mix"][hn] = _norm_mix(
                {{"f": "fold", "c": "call", "j": "jam"}[x]: sum(OP[(x, y)].avg[h] for y in "cf")
                 for x in "fcj"})
            nodes["open_vs_rejam"]["mix"][hn] = _norm_mix(
                {{"c": "call", "f": "fold"}[y]: sum(OP[(x, y)].avg[h] for x in "fcj") for y in "cf"})
        else:
            nodes["open_vs_3bet"]["mix"][hn] = {{"f": "fold", "c": "call", "j": "jam"}[r3]: 1.0}
            nodes["open_vs_rejam"]["mix"][hn] = {{"c": "call", "f": "fold"}[rj]: 1.0}
        # (the opener's values at those decisions, relative: what each answer adds)
        nodes["open_vs_3bet"]["ev"][hn] = {{"f": "fold", "c": "call", "j": "jam"}[k]: x
                                           for k, x in ev3.items()}
        nodes["open_vs_rejam"]["ev"][hn] = {{"c": "call", "f": "fold"}[k]: x for k, x in evj.items()}
        for i in resp:
            w = r_vals(i, h)
            if w.get("vs_open"):
                mm = {"call": RC[i].avg[h], "3bet": BC[i].avg[h] + BF[i].avg[h], "jam": RJ[i].avg[h]}
                if not can3[i]:
                    mm.pop("3bet")
                if i != b:
                    mm.pop("call")
                mm["fold"] = max(0.0, 1 - sum(mm.values()))
                nodes[f"vs_open:{i}"]["mix"][hn] = mm
                nodes[f"vs_open:{i}"]["ev"][hn] = w["vs_open"]
            if w.get("vs_4bet"):
                b3 = BC[i].avg[h] + BF[i].avg[h]
                nodes[f"vs_4bet:{i}"]["mix"][hn] = (
                    _norm_mix({"call": BC[i].avg[h], "fold": BF[i].avg[h]}) if b3 > 1e-9 else
                    {max(w["vs_4bet"], key=w["vs_4bet"].get): 1.0})
                nodes[f"vs_4bet:{i}"]["ev"][hn] = w["vs_4bet"]
            if w.get("vs_jam"):
                nodes[f"vs_jam:{i}"]["mix"][hn] = {"call": CJ[i].avg[h], "fold": 1 - CJ[i].avg[h]}
                nodes[f"vs_jam:{i}"]["ev"][hn] = w["vs_jam"]
    ranges = {"open": [sum(a.avg[h] for a in OP.values()) for h in range(nh)],
              "jam": list(J.avg),
              "fourbet": [sum(OP[("j", y)].avg[h] for y in "cf") for h in range(nh)],
              "rejam": {i: list(RJ[i].avg) for i in resp},
              "threebet": {i: [BC[i].avg[h] + BF[i].avg[h] for h in range(nh)] for i in resp},
              "flat": {i: list(RC[i].avg) for i in resp},
              "call_jam": {i: list(CJ[i].avg) for i in resp},
              "bb_call_open": list(RC[b].avg)}
    return {"game": "open", "nodes": nodes, "ranges": ranges, "opener": o, "resp": resp,
            "stacks": list(stacks), "can3": can3}


def risk_premiums(stacks, value):
    """rp[i][j]: extra equity (percentage points) player i needs, compared with chip EV, to
    call an all-in from player j (no blinds in: the pure premium). Chip EV needs 50%."""
    n = len(stacks)
    base = value(stacks, list(range(n)))
    rp = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            a = min(stacks[i], stacks[j])
            w, l = list(stacks), list(stacks)
            w[i], w[j] = stacks[i] + a, stacks[j] - a
            l[i], l[j] = stacks[i] - a, stacks[j] + a
            vw, vl = value(w, [i])[i], value(l, [i])[i]
            need = (base[i] - vl) / (vw - vl) if vw - vl > 1e-12 else 1.0
            rp[i][j] = 100 * (need - 0.5)
    return rp


def landmark(stacks, target):
    """Seat equity in a landmark satellite: reach `target` chips and you win a seat. Chips
    are fair bets on average, so the chance of getting there is your share of the target;
    chips above it are worth nothing (you leave with the seat)."""
    return [min(1.0, s / target) if target > 0 else 0.0 for s in stacks]


def landmark_value(target):
    def value(stacks, who):
        eq = landmark(stacks, target)
        return {i: 100 * eq[i] for i in who}  # % of a seat
    return value


def chips_value(stacks, who):
    return {i: stacks[i] for i in who}


def icm_value(payouts):
    def value(stacks, who):
        eq = icm(stacks, payouts)
        return {i: eq[i] for i in who}
    return value


def sat_value(seats, field_n, field_avg, leftover):
    def value(stacks, who):
        eq = satellite(stacks, seats, field_n, field_avg, leftover, players=who)
        return {i: 100 * eq[i] for i in who}  # % of a seat
    return value


# ------------------------------------------------------------------ the window

def open_window(app):
    if getattr(app, "icm_win", None) is not None and app.icm_win.alive:
        app.icm_win.top.lift()
        return
    app.icm_win = ICMWindow(app)


def embedded(app, x, y, w, h):
    """The ICM study as a tab of the trainer's own window: a canvas the trainer shows over
    its table (place) and hides again; the layout is drawn as usual and scaled to fit."""
    win = getattr(app, "icm_win", None)
    if win is None:
        win = app.icm_win = ICMWindow(app, embed=(x, y, w, h))
    return win


def _scaled(T, k):
    """The trainer's drawing kit with fonts made k times smaller (the canvas is scaled by
    k after drawing, so text sizes come out as in the full-size window)."""
    import types

    def F(size, bold=False):
        return T.F(max(6, round(size * k)), bold)

    def FH(size):
        return T.FH(max(6, round(size * k)))

    def text_w(text, font):
        return T.text_w(text, font) / k

    def app_button(win, x, y, w, h, text, colour, cmd, fg="white"):
        cv = win.cv
        tag = f"cb{x}_{y}"
        body = win.app.rrect_on(cv, x, y, x + w, y + h, 8, fill=colour, outline="", tags=(tag,))
        cv.create_text(x + w / 2, y + h / 2, text=text, fill=fg, font=FH(9), tags=(tag,))
        cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
        cv.tag_bind(tag, "<Enter>", lambda e: (cv.itemconfigure(body, fill=T.shade(colour, 0.1)),
                                               cv.configure(cursor="hand2")))
        cv.tag_bind(tag, "<Leave>", lambda e: (cv.itemconfigure(body, fill=colour),
                                               cv.configure(cursor="")))
    return types.SimpleNamespace(C=T.C, F=F, FH=FH, text_w=text_w, app_button=app_button,
                                 RANKS=T.RANKS, combo_for=T.combo_for)


class ICMWindow:
    CW, CH = 40, 31
    W, H = 1330, 780

    def __init__(self, app, embed=None):
        import tkinter as tk
        import trainer8 as T
        self.T = self.T_full = T
        self.app = app
        self.k = 1.0
        if embed is not None:   # a tab of the trainer's window
            x, y, w, h = embed
            self.k = min(w / self.W, h / self.H)
            self.T = _scaled(T, self.k)
            self.top = app.root
            self.cv = tk.Canvas(app.root, width=w, height=h, bg=T.C["bg"], highlightthickness=0)
            self.place_at = (x, y)
        else:
            self.top = tk.Toplevel(app.root)
            self.top.title("ICM study")
            self.top.configure(bg=T.C["bg"])
            self.top.resizable(False, False)
            self.cv = tk.Canvas(self.top, width=self.W, height=self.H, bg=T.C["bg"],
                                highlightthickness=0)
            self.cv.pack()
            self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.embedded = embed is not None
        self.alive = True
        self.tab = "ICM"
        self.pay_mode = "$1M"   # a prize pool from POOLS, or "Custom $"
        self.custom = pool_payouts(POOLS[self.pay_mode])
        self.stacks = list(FT_STACKS)
        self.hero = len(self.stacks) - 3  # the button
        self.scenario, self.jammer = "jam", 0
        # Mega satellite settings.
        self.sat_type = "Traditional"
        self.sat = {"players": 120, "seats": 40, "avg": 25, "leftover": 0, "target": 0}
        self.sat_stacks = [30, 25, 22, 20, 18, 15, 12, 10]
        self.quiz, self.score = None, [0, 0]
        self.view = "chart"   # "chart" (push/fold) or "matrix" (risk premiums)
        self.entries = []
        for k in range(9):  # Custom $: one box per place
            e = tk.Entry(self.top, font=self.T.F(10), bg=T.C["card"], fg=T.C["text"],
                         width=9 if self.embedded else 10,
                         insertbackground=T.C["text"], relief="flat", justify="right")
            e.insert(0, f"{self.custom[k]:,.0f}")
            e.bind("<Return>", lambda ev: self.apply_payouts())
            self.entries.append(e)
        # Custom $: or type one prize pool and fill the nine places with the standard shape.
        self.pool_entry = tk.Entry(self.top, font=self.T.F(10), bg=T.C["card"], fg=T.C["text"],
                                   width=10 if self.embedded else 12, insertbackground=T.C["text"], relief="flat",
                                   justify="right")
        self.pool_entry.insert(0, "2,000,000")
        self.pool_entry.bind("<Return>", lambda ev: self.fill_from_pool())
        self.trainer_init()
        self.solve()

    def close(self):
        if self.embedded:
            self.hide()
            return
        self.alive = False
        self.top.destroy()

    def resize(self, x, y, w, h):
        """The trainer's window changed size: fit the tab to it."""
        if not self.embedded:
            return
        import trainer8 as T
        self.k = min(w / self.W, h / self.H)
        self.T = _scaled(T, self.k)
        self.place_at = (x, y)
        self.cv.configure(width=w, height=h)
        if self.app.icm_view:
            self.draw()

    def show(self):
        self.cv.place(x=self.place_at[0], y=self.place_at[1])
        self.cv.tk.call("raise", self.cv._w)   # (a canvas's own lift() raises items)
        for e in self.entries + [self.pool_entry]:
            e.lift()   # the payout boxes sit on this canvas
        self.draw()

    def hide(self):
        self.cv.delete("all")   # (also takes the payout boxes off)
        self.cv.place_forget()

    # -- state --
    def cur_stacks(self):
        return self.stacks if self.tab == "ICM" else self.sat_stacks

    def pays(self, n=None):
        """Prize money for the places still to be paid (the top n), in dollars."""
        src = pool_payouts(POOLS[self.pay_mode]) if self.pay_mode in POOLS else self.custom
        n = len(self.stacks) if n is None else n
        return sorted(src, reverse=True)[:n]

    def apply_payouts(self):
        vals = []
        for e in self.entries:
            try:
                vals.append(max(0.0, float(e.get().replace("$", "").replace(",", "").strip() or 0)))
            except ValueError:
                vals.append(0.0)
        if sum(vals) > 0:
            self.custom = vals
            self.pay_mode = "Custom $"
            self.refresh()

    def fill_from_pool(self):
        """Custom $: the nine places from one typed prize pool, standard shape."""
        try:
            pool = float(self.pool_entry.get().replace("$", "").replace(",", "").strip())
        except ValueError:
            return
        if pool <= 0:
            return
        self.custom = pool_payouts(pool)
        for e, v in zip(self.entries, self.custom):
            e.delete(0, "end")
            e.insert(0, f"{v:,.0f}")
        self.pay_mode = "Custom $"
        self.refresh()

    def refresh(self):
        if self.tab == "ICM Trainer":
            self.trainer_resolve()
        else:
            self.solve()

    def set_pay_mode(self, m):
        self.pay_mode = m
        if m in POOLS:  # Custom $ starts from the last pool picked
            self.custom = pool_payouts(POOLS[m])
            for e, v in zip(self.entries, self.custom):
                e.delete(0, "end")
                e.insert(0, f"{v:,.0f}")
        self.refresh()

    def players_left(self, d):
        n = min(9, max(2, len(self.stacks) + d))
        while len(self.stacks) < n:
            self.stacks.insert(0, 20.0)   # a new seat at the front (UTG side)
        while len(self.stacks) > n:
            self.stacks.pop(0)
        self.hero = min(self.hero, n - 2)
        self.scenario, self.jammer = "jam", 0
        self.solve()

    def set_tab(self, t):
        if t == "ICM Trainer" and hasattr(self.app, "ft_play_from_icm"):
            # In the trainer, the ICM trainer is the Final table mode: full hands, every
            # street (the push/fold-only drill is for the stand-alone window).
            self.app.set_mode("Final table")
            return
        self.tab = t
        if t == "ICM Trainer":
            if self.sess is None:
                self.trainer_new_table()
            self.draw()
            return
        n = len(self.cur_stacks())
        self.hero = min(self.hero, n - 2)
        self.scenario, self.jammer = "jam", 0
        self.solve()

    def set_all(self, bb):
        st = self.cur_stacks()
        st[:] = [float(bb)] * len(st)
        self.solve()

    def bump(self, i, d):
        st = self.cur_stacks()
        st[i] = max(1.0, st[i] + d)
        self.solve()

    def bump_sat(self, key, d):
        s = self.sat
        lim = {"players": (2, 5000), "seats": (1, 4999), "avg": (1, 500), "leftover": (0, 100),
               "target": (1, 100000)}
        if key == "target" and not s["target"]:
            s["target"] = self.auto_target()
        s[key] = min(lim[key][1], max(lim[key][0], s[key] + d))
        s["seats"] = min(s["seats"], s["players"] - 1)
        s["players"] = max(s["players"], len(self.sat_stacks))
        self.solve()

    def set_sat_type(self, t):
        self.sat_type = t
        self.solve()

    def set_hero(self, i):
        n = len(self.cur_stacks())
        if self.scenario == "jam" and i == n - 1:
            return  # the big blind can't jam first in
        if self.scenario == "call" and i == 0:
            return  # nobody in front to jam
        self.hero = i
        if self.scenario == "call" and self.jammer >= i:
            self.jammer = i - 1
        self.solve()

    def set_scenario(self, sc):
        self.scenario = sc
        n = len(self.cur_stacks())
        if sc == "call":
            self.hero = max(self.hero, 1)
            self.jammer = min(self.jammer, self.hero - 1)
        elif self.hero == n - 1:
            self.hero = n - 2
        self.solve()

    def set_jammer(self, i):
        self.jammer = i
        self.solve()

    def set_view(self, v):
        self.view = v
        self.draw()

    def auto_target(self):
        """Total chips / seats: the usual landmark target."""
        s = self.sat
        total = sum(self.sat_stacks) + max(0, s["players"] - len(self.sat_stacks)) * s["avg"]
        return round(total / s["seats"])

    def target(self):
        return self.sat["target"] or self.auto_target()

    def value_fn(self):
        if self.tab != "ICM" and self.sat_type == "Landmark":
            return landmark_value(self.target())
        if self.tab == "ICM":
            return icm_value(self.pays())  # dollars
        s = self.sat
        field_n = max(0, s["players"] - len(self.sat_stacks))
        return sat_value(s["seats"], field_n, s["avg"], s["leftover"] / 100)

    def solve(self):
        self.quiz = None
        st, val = self.cur_stacks(), self.value_fn()
        if self.scenario == "jam":
            self.res = solve_jam(st, val, self.hero)
            self.chip = solve_jam(st, chips_value, self.hero)
        else:
            self.res = solve_call(st, val, self.jammer, self.hero)
            self.chip = solve_call(st, chips_value, self.jammer, self.hero)
        self.draw()

    # -- quiz --
    def new_quiz(self):
        g, gc = self.res["gain"], self.chip["gain"]
        scale = max(abs(v) for v in g.values()) or 1
        close = [h for h in ORDER if abs(g[h]) < 0.15 * scale or (g[h] > 0) != (gc[h] > 0)]
        pool = close or ORDER
        h = random.choice([h for h in pool for _ in range(COMBOS[h])])
        self.quiz = {"hand": h, "answer": None}
        self.draw()

    def answer(self, act):
        if not self.quiz or self.quiz["answer"]:
            return
        self.quiz["answer"] = act
        self.score[0] += (self.res["gain"][self.quiz["hand"]] > 0) == (act == "go")
        self.score[1] += 1
        self.draw()

    # -- drawing --
    def draw(self):
        self._draw()
        if self.k != 1.0:
            self.cv.scale("all", 0, 0, self.k, self.k)

    def _draw(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        cv.delete("all")
        cv.create_text(20, 26, text="ICM study", fill=C["text"], anchor="w", font=FH(15))
        x = 140
        for t in ("ICM", "ICM Trainer", "Mega Satellites"):
            label = "ICM Trainer \u25b6" if (t == "ICM Trainer" and self.embedded) else t
            w = T.text_w(label, FH(10)) + 26
            on = t == self.tab
            T.app_button(self, x, 12, w, 28, label, C["accent"] if on else C["card2"],
                         lambda v=t: self.set_tab(v), fg="white" if on else C["dim"])
            x += w + 8
        if self.tab == "ICM Trainer":
            self.draw_trainer()
            return
        if self.tab == "ICM":
            self.draw_icm_setup()
        else:
            self.draw_sat_setup()
        x = self.W - 300
        for v, label in (("chart", "Push / fold"), ("matrix", "Risk premiums")):
            on = v == self.view
            T.app_button(self, x, 12, 136, 28, label, C["accent"] if on else C["card2"],
                         lambda v=v: self.set_view(v), fg="white" if on else C["dim"])
            x += 142
        if self.view == "chart":
            self.draw_spot()
        else:
            self.draw_matrix()

    def draw_matrix(self):
        """Risk premium of every player (rows) calling an all-in from every other (columns)."""
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        st = self.cur_stacks()
        n = len(st)
        pos = positions(n)
        rp = risk_premiums(st, self.value_fn())
        x0, y0 = 470, 60
        cv.create_text(x0, y0, text="Risk premium matrix", fill=C["text"], anchor="w",
                       font=FH(12))
        cv.create_text(x0, y0 + 16, text="Extra equity (percentage points over chip EV's 50%) "
                       "the player in the row needs to call an all-in\nfrom the player in the "
                       "column, with no blinds in the pot.", fill=C["dim"], anchor="nw", font=F(9))
        cw, ch = min(84, 700 // (n + 1)), 44
        gx, gy = x0 + 70, y0 + 70
        cv.create_text(gx + n * cw / 2, gy - 22, text="all-in from →", fill=C["faint"],
                       font=F(8))
        for j in range(n):
            cv.create_text(gx + j * cw + cw / 2, gy - 8, text=f"{pos[j]} {st[j]:g}",
                           fill=C["dim"], font=FH(8))
        vals = [v for row in rp for v in row if v is not None]
        top = max(vals + [1.0])
        for i in range(n):
            y = gy + i * ch
            cv.create_text(gx - 8, y + ch / 2, text=f"{pos[i]} {st[i]:g}", fill=C["dim"],
                           anchor="e", font=FH(8))
            for j in range(n):
                x = gx + j * cw
                v = rp[i][j]
                if v is None:
                    cv.create_rectangle(x, y, x + cw - 2, y + ch - 2, fill="#13161c", outline="")
                    continue
                t = max(0.0, min(1.0, v / top))
                # green (no premium) -> amber -> red (big premium)
                r = int(47 + t * (224 - 47))
                g = int(168 - t * (168 - 82))
                b = int(106 - t * (106 - 82))
                cv.create_rectangle(x, y, x + cw - 2, y + ch - 2, fill=f"#{r:02x}{g:02x}{b:02x}",
                                    outline="")
                cv.create_text(x + cw / 2 - 1, y + ch / 2 - 1, text=f"{v:+.1f}", fill="white",
                               font=FH(10))
        y = gy + n * ch + 16
        lines = ["How to read it: a premium of +8 means calling needs 58% equity instead of 50%.",
                 "The reddest cells are medium stacks calling the big stacks that cover them:",
                 "that's who the big stacks can pressure. Calls against short stacks cost little",
                 "(small premiums in their columns). Pot odds lower what everyone needs; the",
                 "premium is the extra on top."]
        for k, line in enumerate(lines):
            cv.create_text(x0, y + k * 18, text=line, fill=C["faint"], anchor="w", font=F(9))

    def stack_rows(self, y0, eq, eq_label, fmt):
        """The table players: seat (click = you), stack with -/+, chips %, equity."""
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        st = self.cur_stacks()
        n = len(st)
        pos = positions(n)
        total = sum(st)
        cv.create_text(20, y0, text="Seat (click: you)", fill=C["faint"], anchor="w", font=F(8))
        cv.create_text(150, y0, text="Stack (bb)", fill=C["faint"], anchor="center", font=F(8))
        cv.create_text(262, y0, text="Chips", fill=C["faint"], anchor="e", font=F(8))
        cv.create_text(370, y0, text=eq_label, fill=C["faint"], anchor="e", font=F(8))
        for i in range(n):
            y = y0 + 16 + i * 33
            hero = i == self.hero
            jam = self.scenario == "call" and i == self.jammer
            fill = C["accent"] if hero else C["gold"] if jam else C["card2"]
            T.app_button(self, 20, y, 62, 26, pos[i], fill, lambda v=i: self.set_hero(v),
                         fg="white" if (hero or jam) else C["dim"])
            T.app_button(self, 92, y, 26, 26, "−", C["card"], lambda v=i: self.bump(v, -1))
            cv.create_text(150, y + 13, text=f"{st[i]:g}", fill=C["text"], font=FH(11))
            T.app_button(self, 182, y, 26, 26, "+", C["card"], lambda v=i: self.bump(v, 1))
            cv.create_text(262, y + 13, text=f"{100 * st[i] / total:.1f}%", fill=C["dim"],
                           anchor="e", font=F(10))
            cv.create_text(370, y + 13, text=fmt(eq[i]), fill=C["text"], anchor="e",
                           font=FH(10))
        y = y0 + 16 + n * 33 + 4
        cv.create_text(20, y + 12, text="All", fill=C["dim"], anchor="w", font=F(9))
        for k, bbv in enumerate((10, 15, 20, 25, 30, 40)):
            T.app_button(self, 50 + k * 50, y, 44, 24, f"{bbv}", C["card"],
                         lambda v=bbv: self.set_all(v), fg=C["dim"])
        return y + 36

    def draw_icm_setup(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        x = self.draw_pay_buttons(20, 52)
        cv.create_text(x + 10, 65, text="Players left", fill=C["dim"], anchor="w", font=F(9))
        T.app_button(self, x + 86, 52, 26, 26, "\u2212", C["card"], lambda: self.players_left(-1))
        cv.create_text(x + 128, 65, text=f"{len(self.stacks)}", fill=C["text"], font=FH(11))
        T.app_button(self, x + 144, 52, 26, 26, "+", C["card"], lambda: self.players_left(1))
        eq = icm(self.stacks, self.pays())
        y = self.stack_rows(96, eq, "ICM equity", money)
        pays = self.pays(9)
        n = len(self.stacks)
        y = self.draw_payouts(20, y, n)
        paid = sorted(pays, reverse=True)[n:]
        note = (f"{n} left: places {n + 1}-9 already paid ({money(sum(paid)).replace('.00', '')})"
                f"  \u00b7  " if paid else f"{n} left  \u00b7  ")
        cv.create_text(20, y, text=note + f"blinds 0.5/1 + {ANTE:g} bb big blind ante",
                       fill=C["faint"], anchor="w", font=F(8))

    def draw_pay_buttons(self, x, y):
        T = self.T
        C, FH = T.C, T.FH
        for m in PAY_MODES:
            w = T.text_w(m, FH(9)) + 24
            on = m == self.pay_mode
            T.app_button(self, x, y, w, 26, m, C["accent"] if on else C["card2"],
                         lambda v=m: self.set_pay_mode(v), fg="white" if on else C["dim"])
            x += w + 6
        return x

    def draw_payouts(self, x, y, n, colw=140):
        """The nine payouts: a list for a prize pool, or the boxes for Custom $."""
        T, cv = self.T, self.cv
        C, F = T.C, T.F
        pays = self.pays(9)
        if self.pay_mode == "Custom $":
            cv.create_text(x, y, text="Your payouts, 1st to 9th (Enter or Apply)",
                           fill=C["dim"], anchor="w", font=F(9))
            for k, e in enumerate(self.entries):
                cx, cy = x + (k % 3) * colw, y + 14 + (k // 3) * 30
                cv.create_text(cx, cy + 11, text=f"{k + 1}.", fill=C["faint"], anchor="w",
                               font=F(9))
                cv.create_window(cx + 22, cy, window=e, anchor="nw")
            T.app_button(self, x, y + 108, 80, 24, "Apply", C["card2"], self.apply_payouts)
            cv.create_text(x + 96, y + 120, text=f"Total {money(sum(pays)).replace('.00', '')}",
                           fill=C["dim"], anchor="w", font=F(9))
            cv.create_text(x, y + 152, text="or a prize pool $", fill=C["faint"], anchor="w",
                           font=F(9))
            cv.create_window(x + 104, y + 141, window=self.pool_entry, anchor="nw")
            T.app_button(self, x + 222, y + 140, 90, 24, "Fill 1-9", C["card2"],
                         self.fill_from_pool)
            return y + 182
        cv.create_text(x, y, text=f"{self.pay_mode} prize pool, WSOP Circuit final table "
                       f"payouts", fill=C["dim"], anchor="w", font=F(9))
        for k, p in enumerate(pays):
            cx, cy = x + (k % 3) * colw, y + 16 + (k // 3) * 20
            col = C["text"] if k < n else C["faint"]
            cv.create_text(cx, cy, text=f"{k + 1}. {money(p).replace('.00', '')}", fill=col,
                           anchor="w", font=F(9))
        return y + 80

    def draw_sat_setup(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        x = 20
        for t in ("Traditional", "Landmark"):
            w = T.text_w(t, FH(9)) + 24
            on = t == self.sat_type
            T.app_button(self, x, 52, w, 26, t, C["accent"] if on else C["card2"],
                         lambda v=t: self.set_sat_type(v), fg="white" if on else C["dim"])
            x += w + 6
        note = ("the last players standing win the seats: survival is everything"
                if self.sat_type == "Traditional" else
                "reach the target chip count and you win a seat: chips above it are worth nothing")
        cv.create_text(20, 90, text=note, fill=C["faint"], anchor="w", font=F(8))
        s = self.sat
        rows = [("Players left", "players", (-10, 10)), ("Seats", "seats", (-5, 5)),
                ("Field average (bb)", "avg", (-5, 5))]
        if self.sat_type == "Landmark":
            rows.append(("Target (bb)", "target", (-5, 5)))
        y = 104
        for label, key, (dn, up) in rows:
            cv.create_text(20, y + 12, text=label, fill=C["dim"], anchor="w", font=F(9))
            T.app_button(self, 170, y, 26, 24, "−", C["card"],
                         lambda k=key, d=dn: self.bump_sat(k, d))
            shown = self.target() if key == "target" else s[key]
            cv.create_text(232, y + 12, text=f"{shown:g}", fill=C["text"], font=FH(11))
            if key == "target" and not s["target"]:
                cv.create_text(300, y + 12, text="auto: total chips / seats", fill=C["faint"],
                               anchor="w", font=F(8))
            T.app_button(self, 268, y, 26, 24, "+", C["card"],
                         lambda k=key, d=up: self.bump_sat(k, d))
            y += 30
        if self.sat_type == "Traditional":
            info = (f"{s['players'] - s['seats']} more players bust before the seats are won  "
                    f"·  {s['seats'] / s['players']:.0%} of the field gets a seat")
            field_n = max(0, s["players"] - len(self.sat_stacks))
            eq = [100 * v for v in satellite(self.sat_stacks, s["seats"], field_n, s["avg"],
                                             s["leftover"] / 100)]
        else:
            info = (f"a seat at {self.target():g} bb  ·  below it chips count at face value; "
                    f"above it they're worth nothing")
            eq = [100 * v for v in landmark(self.sat_stacks, self.target())]
            if max(self.sat_stacks) * 2 < self.target():
                info += ("\nEveryone here is far below the target, so this plays like chip EV: "
                         "set the target near a stack to see the pressure.")
        cv.create_text(20, y + 6, text=info, fill=C["faint"], anchor="w", font=F(8))
        y = self.stack_rows(y + 26, eq, "Seat equity", lambda v: f"{v:.1f}%")
        cv.create_text(20, y, text=("Seat equity: chance of a seat. The rest of the field is "
                                    "modelled\nat its average stack." if self.sat_type ==
                                    "Traditional" else "Seat equity: your stack / the target "
                                    "(capped at a seat): your chance\nof reaching it, if chips "
                                    "are fair bets."), fill=C["faint"], anchor="nw", font=F(8))

    def draw_spot(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        st = self.cur_stacks()
        n = len(st)
        pos = positions(n)
        x0 = 470
        for k, (sc, label) in enumerate((("jam", "Jam first in"), ("call", "Call a jam"))):
            on = sc == self.scenario
            T.app_button(self, x0 + k * 128, 52, 120, 26, label,
                         C["accent"] if on else C["card2"], lambda v=sc: self.set_scenario(v),
                         fg="white" if on else C["dim"])
        hero = pos[self.hero]
        if self.scenario == "call":
            cv.create_text(x0 + 268, 65, text="Jammer:", fill=C["dim"], anchor="w", font=F(9))
            x = x0 + 326
            for i in range(self.hero):
                on = i == self.jammer
                T.app_button(self, x, 52, 50, 26, pos[i], C["gold"] if on else C["card2"],
                             lambda v=i: self.set_jammer(v), fg="white" if on else C["dim"])
                x += 54
            title = (f"{hero} ({st[self.hero]:g} bb) facing a {pos[self.jammer]} jam "
                     f"({st[self.jammer]:g} bb), folded to {hero}")
            go = "Call"
        else:
            title = f"{hero} ({st[self.hero]:g} bb), folded to {hero}: jam or fold"
            go = "Jam"
        unit = "dollars" if self.tab == "ICM" else "% of a seat"
        cell = money_short if self.tab == "ICM" else (lambda v: f"{v:+.2f}")
        full = (lambda v: money(v, sign=True)) if self.tab == "ICM" else (
            lambda v: f"{v:+.2f}% of a seat")
        model = "ICM" if self.tab == "ICM" else self.sat_type.lower() + " satellite"
        cv.create_text(x0, 96, text=title, fill=C["text"], anchor="w", font=FH(11))
        cv.create_text(x0, 118, text=f"{model}: {go.lower()} {100 * self.res['share']:.1f}% "
                       f"of hands   ·   chip EV: {100 * self.chip['share']:.1f}%",
                       fill=C["dim"], anchor="w", font=F(10))
        gx, gy = x0, 136
        green, blue, orange = "#2fa86a", "#3b5a8c", "#f0a43a"
        for r, r1 in enumerate(T.RANKS):
            for c, r2 in enumerate(T.RANKS):
                h = r1 + r2 if r == c else (r1 + r2 + "s" if r < c else r2 + r1 + "o")
                x1, y1 = gx + c * self.CW, gy + r * self.CH
                x2, y2 = x1 + self.CW - 2, y1 + self.CH - 2
                g = self.res["gain"][h]
                cv.create_rectangle(x1, y1, x2, y2, fill=green if g > 0 else blue, outline="")
                if (g > 0) != (self.chip["gain"][h] > 0):
                    cv.create_polygon(x2 - 10, y1, x2, y1, x2, y1 + 10, fill=orange, outline="")
                if self.quiz and self.quiz["hand"] == h and self.quiz["answer"]:
                    cv.create_rectangle(x1 - 1, y1 - 1, x2 + 1, y2 + 1, outline="white", width=3)
                cv.create_text((x1 + x2) / 2, y1 + 10, text=h, fill="white", font=FH(8))
                cv.create_text((x1 + x2) / 2, y1 + 22, text=cell(g), fill="#dfe6ee",
                               font=F(7))
        y = gy + 13 * self.CH + 8
        cv.create_rectangle(gx, y + 2, gx + 12, y + 14, fill=green, outline="")
        cv.create_text(gx + 18, y + 8, text=go, fill=C["dim"], anchor="w", font=F(8))
        cv.create_rectangle(gx + 60, y + 2, gx + 72, y + 14, fill=blue, outline="")
        cv.create_text(gx + 78, y + 8, text="Fold", fill=C["dim"], anchor="w", font=F(8))
        cv.create_polygon(gx + 116, y + 2, gx + 128, y + 2, gx + 128, y + 14, fill=orange,
                          outline="")
        cv.create_text(gx + 134, y + 8, text=f"chip EV plays it the other way.   Numbers: "
                       f"{go.lower()} minus fold, {unit}", fill=C["dim"], anchor="w", font=F(8))
        # Risk premium.
        x1 = gx + 13 * self.CW + 18
        cv.create_text(x1, 136, text="Risk premium", fill=C["text"], anchor="w", font=FH(11))
        cv.create_text(x1, 154, text="equity needed to call the jam", fill=C["faint"],
                       anchor="w", font=F(8))
        yy = 176
        if self.scenario == "jam":
            rows = [(pos[i], cs, nd, self.chip["calls"][i][1], self.chip["calls"][i][0])
                    for i, (cs, nd) in self.res["calls"].items()]
        else:
            rows = [(pos[self.hero] + " (you)", self.res["share"], self.res["need"],
                     self.chip["need"], self.chip["share"])]
        for name, cs, nd, nd_c, cs_c in rows:
            cv.create_text(x1, yy, text=name, fill=C["text"], anchor="w", font=FH(10))
            cv.create_text(x1 + 62, yy, text=f"needs {100 * nd:.0f}%  (chip EV {100 * nd_c:.0f}%)",
                           fill=C["dim"], anchor="w", font=F(9))
            cv.create_text(x1, yy + 15, text=f"calls {100 * cs:.0f}% (chip EV {100 * cs_c:.0f}%)"
                           f"   premium {100 * (nd - nd_c):+.1f}%", fill=C["faint"], anchor="w",
                           font=F(8))
            yy += 36
        # Quiz.
        qy = max(yy + 8, 480)
        T.app_button(self, x1, qy, 150, 30, "Quiz me", C["accent"], self.new_quiz)
        cv.create_text(x1 + 160, qy + 15, text=f"{self.score[0]} / {self.score[1]}",
                       fill=C["dim"], anchor="w", font=F(10))
        if self.quiz:
            h = self.quiz["hand"]
            for k, c in enumerate(T.combo_for(h, set())):
                self.app.card_on(cv, x1 + k * 40, qy + 44, c, 36, 50)
            if not self.quiz["answer"]:
                T.app_button(self, x1, qy + 104, 90, 30, go.upper(), "#2fa86a",
                             lambda: self.answer("go"))
                T.app_button(self, x1 + 98, qy + 104, 90, 30, "FOLD", "#3b5a8c",
                             lambda: self.answer("fold"))
            else:
                g, gc = self.res["gain"][h], self.chip["gain"][h]
                right = (g > 0) == (self.quiz["answer"] == "go")
                cv.create_text(x1, qy + 110, text=("Right: " if right else "Wrong: ") +
                               (go if g > 0 else "Fold"), fill="#3ecf8e" if right else "#ef6b73",
                               anchor="w", font=FH(12))
                cv.create_text(x1, qy + 132, text=f"{go} vs fold: {full(g)}",
                               fill=C["dim"], anchor="w", font=F(9))
                cv.create_text(x1, qy + 148, text=f"chip EV: {gc:+.2f} bb "
                               f"({go.lower() if gc > 0 else 'fold'})", fill=C["dim"],
                               anchor="w", font=F(9))

    # ================================================================ ICM Trainer
    # A whole final table, hand after hand: 100 preset 9-handed tables (stacks 5-100 bb), a
    # prize pool ($500K / $1M / $1.5M on the Circuit payout shape, or Custom $). The button
    # moves, blinds go up every LEVEL_HANDS hands, every pot is jam / call / fold with ICM
    # equilibrium ranges, players bust and get paid, until you bust, win, or end the session.
    # Each of your decisions is graded in dollars; "ICM Coach" explains the latest one.

    def trainer_init(self):
        self.table_no = None
        self.sess = None        # the final table being played
        self.live = None        # the hand being played
        self.tspot = None       # your latest decision (graded; the coach explains it)
        self.tscore = {"right": 0, "spots": 0, "lost": 0.0}
        self.table_score = {}   # table number -> [right, decisions, $ lost]
        self.picking = False
        self.coach = None
        self.trng = random.Random()
        self.hand_ver = 0       # a new hand (or ending the session) cancels old timers
        self.gen = None
        self.tspeed = "Normal"

    def close_coach(self):
        if self.coach is not None:
            try:
                self.coach.destroy()
            except Exception:
                pass
            self.coach = None

    def trainer_set_table(self, k):
        """Sit down at table k (1-100) for a new session."""
        self.table_no = (k - 1) % N_TABLES + 1
        self.picking = False
        self.close_coach()
        self.tspot = None
        self.sess = {"stacks": preset_table(self.table_no), "hero": (self.table_no * 4) % 9,
                     "button": 6, "hands": 0, "level": 1, "out": {}, "over": None,
                     "right": 0, "spots": 0, "lost": 0.0, "note": ""}
        self.trainer_next_hand(first=True)

    def trainer_new_table(self):
        """A random one of the 100 tables (not the one you're at)."""
        k = self.trng.randint(1, N_TABLES)
        while k == self.table_no:
            k = self.trng.randint(1, N_TABLES)
        self.trainer_set_table(k)

    def trainer_step_table(self, d):
        self.trainer_set_table((self.table_no or 1) + d)

    def trainer_replay_table(self):
        self.trainer_set_table(self.table_no or 1)

    def trainer_pick(self, on=True):
        self.picking = on
        self.draw()

    def trainer_end(self):
        ss = self.sess
        if ss is None or ss["over"]:
            return
        self.hand_ver += 1
        self.gen = None
        al = self.alive_seats()
        eq = dict(zip(al, icm([ss["stacks"][x] for x in al], self.pays(len(al)))))
        ss["over"] = ("ended", None, eq.get(ss["hero"], 0.0))
        if self.live:
            self.live["stage"] = "done"
        self.draw()

    @staticmethod
    def effective_first_in(st, hero):
        behind = st[hero + 1:]
        return min(st[hero], max(behind)) if behind else 0.0

    # -- seats and the button --
    def alive_seats(self):
        return [x for x in range(9) if self.sess["stacks"][x] > 1e-9]

    def next_alive(self, seat):
        for d in range(1, 10):
            t = (seat + d) % 9
            if self.sess["stacks"][t] > 1e-9:
                return t
        return seat

    def hand_order(self):
        """Seats in preflop action order: first to act ... small blind, big blind."""
        b = self.sess["button"]
        n = len(self.alive_seats())
        if n == 2:  # heads-up: the button is the small blind and acts first
            return [b, self.next_alive(b)]
        bb_seat = self.next_alive(self.next_alive(b))
        order, t = [], bb_seat
        for _ in range(n):
            t = self.next_alive(t)
            order.append(t)
        return order

    def trainer_next_hand(self, first=False):
        ss = self.sess
        if ss is None or ss["over"]:
            return
        self.close_coach()
        ss["note"] = ""
        if not first:
            ss["button"] = self.next_alive(ss["button"])
        ss["hands"] += 1
        if ss["hands"] > 1 and (ss["hands"] - 1) % LEVEL_HANDS == 0 and \
                ss["level"] < len(WSOP_LEVELS):
            ss["level"] += 1
            r = level_ratio(ss["level"])
            ss["stacks"] = [x / r for x in ss["stacks"]]
            ss["note"] = f"Blinds up: {blinds_text(ss['level'])}"
        self.trainer_start_hand()

    def trainer_start_hand(self):
        ss, r = self.sess, self.trng
        order = self.hand_order()
        n = len(order)
        st = [ss["stacks"][x] for x in order]
        blind, ante = [0.0] * n, [0.0] * n
        blind[n - 2], ante[n - 1] = min(0.5, st[n - 2]), min(ANTE, st[n - 1])
        blind[n - 1] = min(1.0, st[n - 1] - ante[n - 1])
        pos = positions(n)
        deck = list(DECK)
        r.shuffle(deck)
        seats = [None] * 9
        for i, x in enumerate(order):
            seats[x] = {"pos": pos[i], "stack": st[i] - blind[i] - ante[i], "bet": blind[i],
                        "folded": False, "action": "", "cards": [deck.pop(), deck.pop()],
                        "reveal": False, "result": ""}
        pays = self.pays(n)
        self.live = {"order": order, "st": st, "pos": pos, "blind": blind, "ante": ante,
                     "pays": pays, "seats": seats, "deck": deck, "board": [], "turn": order[0],
                     "stage": "watch", "caption": ss["note"], "dead": sum(ante),
                     "button": ss["button"], "before": dict(zip(order, icm(st, pays))),
                     "after": None, "decided": None, "auto": True}
        self.hand_ver += 1
        self.gen = self._hand()
        self._pump(None, self.hand_ver)

    def _wait(self, sec):
        return max(1, int(1000 * sec * dict(TSPEEDS)[self.tspeed]))

    def set_tspeed(self, v):
        self.tspeed = v
        self.draw()

    def _pump(self, value=None, ver=None):
        """Run the hand to its next pause (a delay, or your decision)."""
        if ver != self.hand_ver or not self.alive or self.gen is None:
            return
        try:
            out = self.gen.send(value)
        except StopIteration:
            self.gen = None
            self.draw()
            lv, ss = self.live, self.sess
            if lv and lv["auto"] and not ss["over"]:
                self.top.after(self._wait(1.4), lambda: self._auto_next(ver))
            return
        self.draw()
        if out != "hero":
            self.top.after(self._wait(out), lambda: self._pump(None, ver))

    def _auto_next(self, ver):
        if ver == self.hand_ver and self.alive and self.live and self.live["stage"] == "done":
            self.trainer_next_hand()

    def _decision(self, kind, i, jammer, res, chip):
        lv = self.live
        x = lv["order"][i]
        cards = lv["seats"][x]["cards"]
        self.tspot = {"kind": kind, "hero": i, "jammer": jammer, "stacks": list(lv["st"]),
                      "pays": lv["pays"], "res": res, "chip": chip,
                      "hand": hand_class(*cards), "cards": list(cards), "answer": None,
                      "ante": list(lv["ante"])}
        lv["stage"] = "hero"

    def trainer_resolve(self, new_hand=False):
        """A new prize pool mid-hand: re-price the decision you're facing."""
        lv, sp = self.live, self.tspot
        if lv is None:
            return
        lv["pays"] = self.pays(len(lv["order"]))
        if sp is not None and lv["stage"] == "hero":
            st, val = sp["stacks"], icm_value(lv["pays"])
            sp["pays"] = lv["pays"]
            if sp["kind"] == "jam":
                sp["res"] = solve_jam(st, val, sp["hero"])
            else:
                sp["res"] = call_from(solve_jam(st, val, sp["jammer"]), sp["hero"])
        self.draw()

    def trainer_answer(self, act):
        sp, lv, ss = self.tspot, self.live, self.sess
        if sp is None or sp["answer"] or lv is None or lv["stage"] != "hero":
            return
        g = sp["res"]["gain"][sp["hand"]]
        right = (g > 0) == (act == "go")
        sp["answer"] = act
        ts = self.table_score.setdefault(self.table_no, [0, 0, 0.0])
        for rec in (self.tscore, ss):
            rec["spots"] += 1
            rec["right"] += right
            if not right:
                rec["lost"] += abs(g)
        ts[1] += 1
        ts[0] += right
        if not right:
            ts[2] += abs(g)
        lv["decided"] = (act, right)
        lv["auto"] = right and act == "fold"   # stop after a mistake or when you're all-in
        lv["stage"] = "watch"
        self._pump(act, self.hand_ver)

    # -- one hand, as a generator: yields a pause in seconds, or "hero" for your decision --
    def _hand(self):
        lv, ss, r = self.live, self.sess, self.trng
        order, st = lv["order"], lv["st"]
        n = len(order)
        idx = exact()[1]
        val = icm_value(lv["pays"])
        me = order.index(ss["hero"])
        seats = lv["seats"]
        yield 0.7
        jammer, jam_res = None, None
        for i in range(n - 1):
            x = order[i]
            lv["turn"] = x
            if i == me:
                res = solve_jam(st, val, i)
                self._decision("jam", i, None, res, solve_jam(st, chips_value, i))
                act = yield "hero"
            else:
                yield 0.35
                res = solve_jam(st, val, i, iters=30)
                cls = idx[hand_class(*seats[x]["cards"])]
                act = "go" if r.random() < res["jam_range"][cls] else "fold"
            if act == "go":
                self._act(x, "jam")
                jammer, jam_res = i, res
                yield 0.8
                break
            self._act(x, "fold")
            yield 0.15
        if jammer is None:  # folded round to the big blind
            lv["turn"] = None
            self._settle(None, None, n - 1)
            return
        caller = None
        for k in range(jammer + 1, n):
            x = order[k]
            lv["turn"] = x
            if caller is not None:
                self._act(x, "fold")
                yield 0.25
                continue
            to = min(st[k] - lv["ante"][k], st[jammer] - lv["ante"][jammer])
            if k == me:
                chip = call_from(solve_jam(st, chips_value, jammer, iters=30), k)
                self._decision("call", k, jammer, call_from(jam_res, k), chip)
                act = yield "hero"
            elif seats[x]["stack"] <= 1e-9:
                act = "go"   # all-in from the blinds already
            else:
                yield 0.35
                cls = idx[hand_class(*seats[x]["cards"])]
                act = "go" if r.random() < jam_res["call_ranges"][k][cls] else "fold"
            if act == "go":
                self._act(x, "call", to)
                caller = k
                yield 0.8
            else:
                self._act(x, "fold")
                yield 0.3
        lv["turn"] = None
        if caller is None:
            if lv["decided"] and lv["decided"][0] == "fold" and lv["order"][jammer] != ss["hero"]:
                j = seats[order[jammer]]
                j["reveal"] = True
                lv["caption"] = f"{j['pos']} had {hand_class(*j['cards'])}"
                yield 0.8
            self._settle(jammer, None, jammer)
            return
        p, q = order[jammer], order[caller]
        for x in (p, q):
            seats[x]["reveal"] = True
        names, idx2, E, W = exact()
        hp, hq = hand_class(*seats[p]["cards"]), hand_class(*seats[q]["cards"])
        e = E[idx2[hp]][idx2[hq]]
        lv["caption"] = (f"{seats[p]['pos']} {hp} {100 * e:.0f}%   vs   "
                         f"{seats[q]['pos']} {hq} {100 * (1 - e):.0f}%")
        if ss["hero"] in (p, q):
            lv["auto"] = False
        yield 1.3
        for k in (3, 1, 1):
            for _ in range(k):
                lv["board"].append(lv["deck"].pop())
            yield 1.1
        self._settle(jammer, caller, None)

    def _act(self, x, what, amount=None):
        d = self.live["seats"][x]
        if what == "fold":
            d["folded"], d["action"] = True, "Fold"
        elif what == "jam":
            d["bet"] += d["stack"]
            d["stack"] = 0.0
            d["action"] = "All-in"
        elif what == "call":
            add = max(0.0, min(d["stack"], amount - d["bet"]))
            d["bet"] += add
            d["stack"] -= add
            d["action"] = "Call" if d["stack"] > 1e-9 else "Call all-in"

    def _settle(self, p, q, winner):
        """Final stacks (Spot's rules), prize equity before and after, busts and payouts."""
        lv, ss = self.live, self.sess
        order, st, blind, ante = lv["order"], lv["st"], lv["blind"], lv["ante"]
        n = len(order)
        post = [b_ + a_ for b_, a_ in zip(blind, ante)]
        final = [x - y for x, y in zip(st, post)]
        pot = sum(post)
        if q is None:
            final[winner] += pot
            if not lv["caption"] or lv["caption"] == ss["note"]:
                lv["caption"] = f"{lv['pos'][winner]} wins {fmt_bb(pot)} bb"
        else:
            live_p, live_q = st[p] - ante[p], st[q] - ante[q]
            a_ = min(live_p, live_q)
            total = 2 * a_ + pot - blind[p] - blind[q]
            board = [card(c) for c in lv["board"]]
            cp, cq = (lv["seats"][order[k]]["cards"] for k in (p, q))
            sp_ = evaluate([card(c) for c in cp] + board)
            sq_ = evaluate([card(c) for c in cq] + board)
            final[p], final[q] = live_p - a_, live_q - a_
            if sp_ == sq_:
                final[p] += total / 2
                final[q] += total / 2
                lv["caption"] = "Split pot"
            else:
                w = p if sp_ > sq_ else q
                final[w] += total
                lv["caption"] = f"{lv['pos'][w]} wins {fmt_bb(total)} bb"
        for i, x in enumerate(order):
            ss["stacks"][x] = max(0.0, final[i])
        # Busts: places n, n-1, ... (the bigger starting stack finishes higher).
        all9 = self.pays(9)
        bust = sorted([i for i in range(n) if final[i] <= 1e-9], key=lambda i: st[i])
        place = n
        for i in bust:
            ss["out"][order[i]] = (place, all9[place - 1])
            place -= 1
        al = self.alive_seats()
        after = dict(zip(al, icm([ss["stacks"][x] for x in al], self.pays(len(al)))))
        for x in ss["out"]:
            after.setdefault(x, ss["out"][x][1])
        if len(al) == 1:
            ss["out"][al[0]] = (1, all9[0])
            after[al[0]] = all9[0]
        lv["after"] = after
        for i, x in enumerate(order):
            d = lv["seats"][x]
            d["stack"], d["bet"] = ss["stacks"][x], 0.0
            delta = after[x] - lv["before"][x]
            d["result"] = (("+" if delta > 0 else "-") + f"${abs(delta):,.0f}"
                           if abs(delta) >= 0.5 else "")
            if x in ss["out"] and ss["out"][x][0] > 1:
                pl, prize = ss["out"][x]
                d["action"] = f"Out {ordinal(pl)}: {money(prize).replace('.00', '')}"
                d["folded"] = False
        hero = ss["hero"]
        if hero in ss["out"]:
            pl, prize = ss["out"][hero]
            ss["over"] = ("win" if pl == 1 else "bust", pl, prize)
        lv["stage"], lv["turn"] = "done", None

    # -- the coach --
    def coach_facts(self):
        """Numbers the coach explains, for the spot's hand."""
        sp = self.tspot
        names, idx, E, W = exact()
        h = idx[sp["hand"]]
        st, pays = sp["stacks"], sp["pays"]
        pos = positions(len(st))
        me = sp["hero"]
        eq_now = icm(st, pays)
        f = {"pos": pos, "me": me, "stack": st[me], "eq_now": eq_now[me],
             "chip_share": st[me] / sum(st), "pool": sum(pays), "kind": sp["kind"]}
        f["shorter"] = sum(1 for k, v in enumerate(st) if v < st[me] and k != me)
        if sp["kind"] == "info":   # no all-in decision: equity, pot odds, premiums
            rp = risk_premiums(st, icm_value(pays))
            f["opps"] = [{"seat": pos[k], "stack": st[k], "mine": rp[me][k], "theirs": rp[k][me],
                          "covers": st[k] >= st[me]} for k in sp.get("live", [])]
            f.update(to_call=sp.get("to_call", 0.0), pot=sp.get("pot", 0.0),
                     street=sp.get("street", "preflop"), eff=sp.get("eff", st[me]))
            return f
        if sp["kind"] == "mix":          # a decision inside a game with traps
            res, chip = sp["res"], sp["chip"]
            hand = sp["hand"]
            ev = res["ev"][hand]
            f["mix"] = {"node": sp["node"], "mix": res["mix"][hand], "ev": ev,
                        "chip_mix": (chip or {}).get("mix", {}).get(hand),
                        "info": sp.get("info", [])}
            base = "fold" if "fold" in ev else "check"
            f["gain"] = max(ev.values()) - ev[base]
            return f
        res, chip = sp["res"], sp["chip"]
        f["gain"], f["gain_chips"] = res["gain"][sp["hand"]], chip["gain"][sp["hand"]]
        spot = res["spot"]
        if sp["kind"] == "jam":
            rows, p_reach = [], 1.0
            for i in spot.behind:
                pc = call_prob(res, i, h)
                eq = range_eq(h, call_weights(res, i))
                need_me = (spot.v_fold[me] - spot.lose[i][me]) / max(
                    1e-9, spot.win[i][me] - spot.lose[i][me])
                cs = chip["spot"]
                need_chip = (cs.v_fold[me] - cs.lose[i][me]) / max(
                    1e-9, cs.win[i][me] - cs.lose[i][me])
                rows.append({"seat": pos[i], "stack": st[i], "p_call": p_reach * pc,
                             "range": res["calls"][i][0], "eq": eq, "need": need_me,
                             "need_chip": need_chip, "win": spot.win[i][me],
                             "lose": spot.lose[i][me], "their_need": res["calls"][i][1],
                             "their_need_chip": chip["calls"][i][1]})
                p_reach *= 1 - pc
            f["callers"], f["p_fold"] = rows, p_reach
            f["steal"], f["fold"] = spot.v_steal[me], spot.v_fold[me]
            f["pot"] = sum(spot.post)
            op = sp.get("opener")
            if op is not None:
                f["opener"], f["open_to"] = pos[op], spot.post[op] - spot.ante[op] \
                    if hasattr(spot, "ante") else spot.post[op]
                prior = res.get("priors", {}).get(op)
                f["open_share"] = (_share_w(prior) if prior else None)
            f["eff"] = sp.get("eff", st[me])
        else:
            j = sp["jammer"]
            rng = _sparse(res["jam_range"])
            f["jammer"], f["jammer_stack"] = pos[j], st[j]
            f["jam_share"] = res["jam_share"]
            f["eq"] = range_eq(h, rng)
            f["need"], f["need_chip"] = res["need"], chip["need"]
            f["win"], f["lose"] = spot.lose[me][me], spot.win[me][me]
            f["fold"] = spot.v_steal[me]
        return f

    def open_coach(self):
        if self.tspot is None:
            return
        import tkinter as tk
        T = getattr(self, "T_full", self.T)   # the popup is full size
        C, F, FH = T.C, T.F, T.FH
        if self.coach is not None:
            try:
                self.coach.destroy()
            except tk.TclError:
                pass
        f = self.coach_facts()
        top = tk.Toplevel(self.top)
        top.title("ICM Coach")
        top.configure(bg=C["bg"])
        self.coach = top
        cv = tk.Canvas(top, width=640, height=640, bg=C["bg"], highlightthickness=0)
        bar = tk.Scrollbar(top, command=cv.yview)
        cv.configure(yscrollcommand=bar.set)
        cv.pack(side="left", fill="both", expand=True)
        top.bind("<MouseWheel>", lambda e: cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        top.bind("<Button-4>", lambda e: cv.yview_scroll(-1, "units"))
        top.bind("<Button-5>", lambda e: cv.yview_scroll(1, "units"))

        def fit(height):   # long advice scrolls instead of running off the screen
            view = min(height, max(400, top.winfo_screenheight() - 120))
            cv.configure(height=view, scrollregion=(0, 0, 640, height))
            if view < height:
                bar.pack(side="right", fill="y")
        y = 22
        hand = self.tspot["hand"]

        def head(t):
            nonlocal y
            y += 8
            cv.create_text(20, y, text=t, fill=C["gold"], anchor="w", font=FH(11))
            y += 22

        def line(t, col=None, bold=False):
            nonlocal y
            item = cv.create_text(28, y, text=t, fill=col or C["text"], anchor="nw",
                                  font=FH(10) if bold else F(10), width=590)
            box = cv.bbox(item)
            y = (box[3] if box else y + 16) + 6

        cv.create_text(20, y, text=f"ICM Coach  \u00b7  {f['pos'][f['me']]} {f['stack']:g} bb with "
                       f"{hand}", fill=C["text"], anchor="w", font=FH(14))
        y += 30
        head("Your equity")
        line(f"Your stack is {100 * f['chip_share']:.1f}% of the chips; under ICM it's worth "
             f"{money(f['eq_now'])}, {100 * f['eq_now'] / f['pool']:.1f}% of the prize money "
             f"still to be paid ({money(f['pool'])}).")
        line(f"{f['shorter']} player{'s are' if f['shorter'] != 1 else ' is'} shorter than you"
             + (": every one who busts first moves you up the ladder for free." if f['shorter']
                else ": you're the shortest stack, so the ladder works against waiting."))
        if f["kind"] == "mix":
            self._coach_mix(f, hand, head, line, C)
            fit(y + 20)
            return
        if f["kind"] == "info":
            self._coach_info(f, head, line, C)
            fit(y + 20)
            return
        best_go = f["gain"] > 0
        if f["kind"] == "jam":
            go = "Jam"
            if f.get("opener"):
                line(f"{f['opener']} opened to {f['open_to']:g} bb. Here's re-jamming (all-in) "
                     f"against folding; calling or a small 3-bet are played by your sims (chip "
                     f"EV)." + (f" Their 2x open range: about {100 * f['open_share']:.0f}% of "
                                f"hands." if f.get("open_share") else ""), col=C["dim"])
            elif f.get("eff", 0) >= 12:
                line(f"With {f['eff']:.0f} bb effective you can open 2x or jam: here's jamming "
                     f"against folding (the 2x open is your sims' play).", col=C["dim"])
            head("Risk premium")
            for c in f["callers"][:4]:
                line(f"If {c['seat']} ({c['stack']:g} bb) calls, you need {100 * c['need']:.1f}% "
                     f"equity to break even in prize money vs {100 * c['need_chip']:.1f}% in "
                     f"chips: a premium of {100 * (c['need'] - c['need_chip']):+.1f} points.")
            callers = sorted(f["callers"], key=lambda c: -c["p_call"])
            head("Jam: the plusses")
            line(f"Everyone folds {100 * f['p_fold']:.0f}% of the time and you win the "
                 f"{f['pot']:g} bb in the pot: worth "
                 f"{money(f['steal'] - f['fold'], sign=True)} to you.")
            for c in f["callers"]:
                if c["their_need"] - c["their_need_chip"] > 0.03:
                    line(f"{c['seat']} needs {100 * c['their_need']:.0f}% equity to call you "
                         f"(chip EV {100 * c['their_need_chip']:.0f}%): ICM makes them call "
                         f"tighter, so your jam folds them more.")
                    break
            head("Jam: the minuses")
            for c in callers[:3]:
                if c["p_call"] < 0.01:
                    continue
                line(f"{c['seat']} calls {100 * c['p_call']:.0f}% of the time (with the top "
                     f"{100 * c['range']:.0f}% of hands); you have {100 * c['eq']:.0f}% against "
                     f"that range. Win: {money(c['win'])}; lose: {money(c['lose'])}.")
            head("Fold")
            line(f"No risk: you keep {money(f['fold'])} (you only lose what you've put in).")
            if self.tspot.get("open2x"):
                self._coach_open(self.tspot["open2x"], head, line, C)
            if self.tspot.get("facing"):
                self._coach_facing(self.tspot["facing"], head, line, C)
        else:
            go = "Call"
            head("Risk premium")
            line(f"Calling {f['jammer']}'s jam, you need {100 * f['need']:.1f}% equity to break "
                 f"even in prize money vs {100 * f['need_chip']:.1f}% in chips: a premium of "
                 f"{100 * (f['need'] - f['need_chip']):+.1f} points.")
            head("Call: the plusses")
            line(f"{f['jammer']} ({f['jammer_stack']:g} bb) jams the top {100 * f['jam_share']:.0f}% "
                 f"of hands here; {hand} has {100 * f['eq']:.1f}% equity against that range.")
            line(f"Win and you're worth {money(f['win'])}.")
            head("Call: the minuses")
            line(f"Lose and you're worth {money(f['lose'])}"
                 + (" (that's busting)." if f["lose"] <= min(self.tspot['pays']) + 0.01 else "."))
            head("Fold")
            line(f"You keep {money(f['fold'])}.")
        self._coach_exploit(f, go, head, line, C)
        head("Pay jump: fold for the pay-up?")
        st, pays = self.tspot["stacks"], sorted(self.tspot["pays"], reverse=True)
        n = len(st)
        me = f["me"]
        jump = pays[n - 2] - pays[n - 1]
        p_next = icm(st, [0.0] * (n - 1) + [1.0])[me]   # chance you finish 9th (next out)
        shorter = [f"{f['pos'][k]} {st[k]:g} bb" for k in sorted(range(n), key=lambda k: st[k])
                   if st[k] < st[me] and k != me]
        line(f"The next one out gets {money(pays[n - 1]).replace('.00', '')}; "
             f"{ordinal(n - 1)} pays "
             f"{money(pays[n - 2]).replace('.00', '')}: a jump of "
             f"{money(jump).replace('.00', '')}. You're about {100 * p_next:.0f}% to be the "
             f"next player out" + (f" ({', '.join(shorter[:3])} "
                                   f"{'is' if len(shorter) == 1 else 'are'} shorter)."
                                   if shorter else ", and nobody is shorter than you."))
        if best_go:
            line(f"Worth folding for it? No. The ladder is already priced into these numbers and "
                 f"{"jamming" if go == "Jam" else "calling"} still earns {money(f['gain'])} more than folding. Take the "
                 f"spot.", col="#3ecf8e")
        elif shorter:
            line(f"Worth folding for it? Yes. With shorter stacks around you, folding and letting "
                 f"them bust first is worth {money(-f['gain'])} more than {"jamming" if go == "Jam" else "calling"} "
                 f"here.", col="#ef6b73")
        else:
            line(f"Worth folding for it? Here, yes, but not because of the ladder: you're the "
                 f"short stack, so waiting won't climb you up. This hand just isn't good enough "
                 f"({"jamming" if go == "Jam" else "calling"} loses {money(-f['gain'])}), so wait for a better one, soon.",
                 col="#ef6b73")
        head("Verdict")
        verdict = go if best_go else "Fold"
        line(f"{verdict}: {go.lower()} is worth {money(f['gain'], sign=True)} compared with folding.",
             col="#3ecf8e" if best_go else "#ef6b73", bold=True)
        chip_go = f["gain_chips"] > 0
        if chip_go != best_go:
            line(f"Chip EV says the opposite ({go.lower() if chip_go else 'fold'}, "
                 f"{f['gain_chips']:+.2f} bb): this is a hand where ICM changes the decision.")
        else:
            line(f"Chip EV agrees ({f['gain_chips']:+.2f} bb).", col=C["dim"])
        fit(y + 20)

    def _coach_exploit(self, f, go, head, line, C):
        """Against players who don't play the equilibrium: how the answer changes if they
        call (or jam) wider or tighter, with this hand and for the range."""
        sp = self.tspot
        hand = sp["hand"]
        res = sp["res"]
        try:
            ex = exploit_jam(res) if go == "Jam" else exploit_call(res)
        except Exception:      # (a coach line is never worth an error)
            return
        sp["exploit"] = ex
        base = res["gain"]
        verb = "jamming" if go == "Jam" else "calling"
        head("Exploits: if they don't play the equilibrium")
        if go == "Jam":
            who = ", ".join(f"{f['pos'][i]} {100 * res['calls'][i][0]:.0f}%"
                            for i in res["spot"].behind[:3])
            line(f"The numbers above assume everyone calls the equilibrium share ({who}). "
                 f"Real players drift from it:", col=C["dim"])
            cases = (("wide", "If they call wider (calling stations: 60% more hands)"),
                     ("tight", "If they call tighter (nits: about half as many hands)"))
        else:
            line(f"The numbers above assume {f['jammer']} jams the equilibrium "
                 f"{100 * res['jam_share']:.0f}% of hands. Real players drift from it:",
                 col=C["dim"])
            cases = (("wide", f"If {f['jammer']} jams wider (a maniac: 60% more hands)"),
                     ("tight", f"If {f['jammer']} jams tighter (a nit: about half as many)"))
        for key, text in cases:
            g = ex[key]["gain"]
            add, drop = flips(base, g)
            good = g[hand] > 0
            msg = (f"{text}, {verb} {hand} is worth {money(g[hand], sign=True)} against "
                   f"folding: {'still ' if good == (base[hand] > 0) else ''}"
                   f"{go.lower() if good else 'fold'}.")
            share = sum(COMBOS[h] for h in g if g[h] > 0) / 1326
            if add:
                msg += (" Every hand becomes a " + go.lower() + "." if share > 0.97 else
                        f" Add: {', '.join(add)}.")
            if drop:
                msg += f" Drop: {', '.join(drop)}."
            col = "#3ecf8e" if good and base[hand] <= 0 else "#ef6b73" if (
                not good and base[hand] > 0) else None
            line(msg, col=col)
        flip = [k for k, _ in cases if (ex[k]["gain"][hand] > 0) != (base[hand] > 0)]
        if flip:
            k = flip[0]
            who = ("calling stations" if k == "wide" else "nits") if go == "Jam" else (
                "a maniac" if k == "wide" else "a nit")
            line(f"The exploit with {hand}: against {who}, "
                 f"{go.lower() if ex[k]['gain'][hand] > 0 else 'fold'} instead of the "
                 f"equilibrium's {go.lower() if base[hand] > 0 else 'fold'}.", bold=True)
        else:
            line(f"With {hand} the answer holds either way: "
                 f"{go.lower() if base[hand] > 0 else 'fold'}.", col=C["dim"])

    def _coach_bluff(self, steal, fail, p_fold, what, line, C):
        """A raise as a pure bluff: the fold rate it needs (in prize money) against the one
        the players behind really have (your sims' bots)."""
        if steal <= 0 or fail >= 0 or steal - fail <= 1e-9:
            return
        need = -fail / (steal - fail)
        p = p_fold
        if p >= need + 0.03:
            verdict = (f"they over-fold: {what} shows a profit as a pure bluff, before your "
                       f"hand's equity is even counted. Raise your bluffs here (blockers: "
                       f"suited aces, suited kings).")
        elif p <= need - 0.03:
            verdict = (f"they defend enough that {what} needs real equity or blockers; "
                       f"against tighter players, folding more than {100 * need:.0f}%, bluff "
                       f"more.")
        else:
            verdict = "right on the line: any equity your hand has decides it."
        line(f"Exploit check: as a pure bluff (giving up to any action) {what} needs everyone "
             f"to fold {100 * need:.0f}% of the time; here they fold {100 * p:.0f}%: "
             + verdict, col=C["gold"])

    def _coach_post(self, o, head, line, C):
        """After the flop: what you have, against what, at what SPR, and the play."""
        head(f"Your hand on the {o['street']}")
        line(f"{o['hand']}: {o['made']}.")
        for p in o["opps"]:
            if p["eq"] is None:
                continue
            line(f"Against {p['pos']}'s range (about {p['range']:.0f}% of hands, as they've "
                 f"played it) you have {100 * p['eq']:.0f}% equity.")
        if o.get("multi") is not None:
            line(f"Against all of them at once: {100 * o['multi']:.0f}%.")
        spr = o["spr"]
        line(f"Pot {o['pot']:.1f} bb, {o['behind']:.1f} bb behind: SPR {spr:.1f}.")
        eqs = [p["eq"] for p in o["opps"] if p["eq"] is not None]
        eq = min(eqs) if eqs else None
        if eq is not None:
            if eq >= 0.65:
                tip = ("Strong: build the pot, you're happy to get it in"
                       + (" (and at this SPR it goes in naturally)." if spr < 3 else "."))
            elif eq >= 0.45:
                tip = ("Medium strength: thin value or pot control; big pots against players "
                       "who cover you cost the ICM premium.")
            elif "draw" in o["made"]:
                tip = ("A draw: price it against the pot odds; semi-bluffing is better when "
                       "they can fold, and worse against players who cover you.")
            else:
                tip = ("Behind most of their range: check, give up, or bluff only where they "
                       "fold a lot.")
            line(tip, col=C["dim"])
        src = o["solve"] or "your sims"
        line(f"The play ({src}): {o['play']}.", bold=True)
        if o.get("to_call"):
            need = o["to_call"] / (o["pot"] + o["to_call"])
            line(f"Exploit check: calling {o['to_call']:g} bb needs {100 * need:.0f}% equity. "
                 f"The solve defends against a balanced bettor; most players bluff less than "
                 f"that at a final table, so fold your thinnest bluff-catchers against them, "
                 f"and call wider only against players you've seen over-bluff.", col=C["gold"])
        elif o.get("bet"):
            b, p = o["bet"], o["pot"]
            need = b / (p + b) if p + b > 0 else 0.0
            what = "all-in" if o.get("bet_allin") else "bet"
            ratio = (f"{b / p:.1f}x pot" if b >= 1.5 * p else f"{100 * b / p:.0f}% pot")
            line(f"Exploit check: the {what} of {b:.1f} bb into {p:.1f} bb ({ratio}) needs "
                 f"{100 * need:.0f}% folds to profit as a pure bluff. Against "
                 f"players who over-fold (short stacks protecting their ladder spot), bluff "
                 f"more than this; against calling stations, keep the value bets and drop the "
                 f"bluffs.", col=C["gold"])
        else:
            line("Exploit check: checking here. A bluff of 1/3 pot needs 25% folds, 2/3 pot "
                 "40%, pot 50%: against players who over-fold to bets, a bluff is "
                 "profitable when they fold more than that; against calling stations, check "
                 "your air and bet your value.", col=C["gold"])

    def _coach_vs3bet(self, o, head, line, C):
        """You opened and got 3-bet: fold, flat, a small 4-bet, or a jam."""
        head(f"{o['who']} 3-bets to {o['to3']:g} bb over your {o['open_to']:g} bb open")
        line(f"{o['who']} ({o['who_stack']:g} bb) 3-bets about {100 * o['share']:.0f}% of hands "
             f"against your open; {o['hand']} has {100 * o['eq']:.0f}% equity against that range.")
        line(f"Your sims (the hint) play {o['hand']}: {o['sims']}.", bold=True)
        head("Fold")
        line(f"You keep {money(o['fold'])}: the {o['open_to']:g} bb open is the whole cost.")
        head("Flat")
        line(f"The flop pot is about {o['pot_flat']:.1f} bb with {o['behind']:.1f} bb behind: "
             f"SPR {o['spr']:.1f}, you'd be {'in' if o['ip'] else 'out of'} position. "
             + ("At this SPR most flops you continue on put you all-in: flat hands that are "
                "happy to get it in, or that fold cleanly." if o["spr"] < 3 else
                "Hands that play well postflop (pairs, suited broadways and connectors) and "
                "that you don't want to fold to a 5-bet jam."))
        if o.get("small"):
            f = o["small"]
            head(f"4-bet to {o['to4']:g} bb")
            line(f"Against it {o['who']} folds {100 * f['fold']:.0f}%, calls {100 * f['call']:.0f}%"
                 f", jams {100 * (f['jam'] + f['raise']):.0f}% (of its 3-bet range). A small 4-bet "
                 f"folds out bluffs and keeps the hands that dominate you in; the jam answer puts "
                 f"you to a call off under ICM.")
            risk = o["to4"] - o["open_to"]
            pot_before = o["pot_flat"] - 2 * o["to3"] + o["open_to"] + o["to3"]
            need = risk / (risk + pot_before) if risk + pot_before > 0 else 1.0
            line(f"Exploit check: as a bluff the 4-bet risks {risk:g} bb to win "
                 f"{pot_before:.1f} bb, so it needs {o['who']} to fold {100 * need:.0f}% (in "
                 f"chips); they fold {100 * f['fold']:.0f}% of their 3-bets here: "
                 + ("they over-fold, so 4-bet bluff (best with an ace or king blocker)."
                    if f["fold"] >= need + 0.03 else
                    "not enough for a pure bluff; 4-bet for value, or bluff only against "
                    "players who fold more." if f["fold"] <= need - 0.03 else
                    "right on the line."), col=C["gold"])
        head(f"4-bet all-in ({o['jam_to']:g} bb)")
        line(f"{o['who']} calls {100 * o['p_call']:.0f}% of its 3-bet range; you have "
             f"{100 * o['eq_call']:.0f}% against the hands that call. Getting it all in against "
             f"them you need {50 + o['rp']:.1f}% (chip EV 50%), a premium of {o['rp']:+.1f} points.")
        g = o["jam_gain"]
        line(f"Jamming is worth {money(g, sign=True)} compared with folding.",
             col="#3ecf8e" if g > 0 else "#ef6b73", bold=True)
        line(hand_note(o["hand"], o["spr"]))

    def _coach_facing(self, o, head, line, C):
        """Facing one open: the opener's range, your sims' play, a 3-bet, and a call."""
        head(f"Facing {o['opener']}'s open to {o['open_to']:g} bb")
        line(f"{o['opener']} opens about {100 * o['open_share']:.0f}% of hands here; "
             f"{o['hand']} has {100 * o['eq']:.0f}% equity against that range.")
        line(f"Your sims (the hint) play {o['hand']}: {o['sims']}.", bold=True)
        head(f"3-bet to {o['to3']:g} bb?")
        line(f"It risks {o['to3']:g} bb, not your stack: 3-betting and folding to a 4-bet costs "
             f"{money(-o['three_fold'])}. If everyone folds you win the {o['pot']:g} bb in the "
             f"pot, worth {money(o['steal'], sign=True)}; that happens about "
             f"{100 * o['p_all_fold']:.0f}% of the time.")
        for pos, stack, f in o["answers"]:
            if 1 - f["fold"] < 0.005:
                continue
            parts = [f"{lbl} {100 * f[a]:.0f}%" for a, lbl in
                     (("call", "calls"), ("raise", "4-bets"), ("jam", "jams")) if f[a] >= 0.005]
            whose = " (of its opening range)" if pos == o["opener"] else ""
            line(f"{pos} ({stack:g} bb) folds {100 * f['fold']:.0f}%{whose}, " + ", ".join(parts)
                 + ".")
        self._coach_bluff(o["steal"], o["three_fold"], o["p_all_fold"],
                          f"a 3-bet to {o['to3']:g} bb", line, C)
        spr = o["spr3"]
        desc = ("committed: the rest goes in on most flops" if spr < 2.5 else
                "medium: top pair is usually a stack-off" if spr < 6 else "deep")
        line(f"Called, the flop pot is about {o['pot3']:.1f} bb with {o['behind3']:.1f} bb behind: "
             f"SPR {spr:.1f}, {desc}; you'd be {'in' if o['ip'] else 'out of'} position.")
        head("Call instead?")
        line(f"Calling {o['open_to']:g} bb, the flop pot is about {o['pot_call']:.1f} bb with "
             f"{o['behind_call']:.1f} bb behind (SPR {o['spr_call']:.1f})"
             + (f"; the {o['squeezers']} player{'s' if o['squeezers'] != 1 else ''} behind "
                f"can still squeeze." if o["squeezers"] else "."))
        line(hand_note(o["hand"], spr))

    def _coach_open(self, o, head, line, C):
        """'Open 2x instead?': the open in prize money, how the others answer it, and how
        the hand plays on if it's called."""
        head(f"Open to {o['to']:g} bb instead?")
        if o.get("sims"):
            line(f"Your sims (the hint) play {o['hand']}: {o['sims']}.", bold=True)
        line(f"A {o['to']:g} bb open risks {o['to']:g} bb, not your stack: open-folding to a "
             f"re-raise costs {money(-o['open_fold'])}. If everyone folds you win the "
             f"{o['pot']:g} bb in the pot, worth {money(o['steal'], sign=True)}; that happens "
             f"about {100 * o['p_all_fold']:.0f}% of the time.")
        for r in o["resp"]:
            if 1 - r["fold"] < 0.005:
                continue
            parts = [f"{lbl} {100 * r[a]:.0f}%" for a, lbl in
                     (("call", "calls"), ("raise", "3-bets"), ("jam", "re-jams")) if r[a] >= 0.005]
            line(f"{r['pos']} ({r['stack']:g} bb) folds {100 * r['fold']:.0f}%, "
                 + ", ".join(parts) + ".")
        self._coach_bluff(o["steal"], o["open_fold"], o["p_all_fold"],
                          f"a {o['to']:g} bb open", line, C)
        if any(r["jam"] >= 0.02 for r in o["resp"]):
            line("Against a re-jam you're back to an all-in call under ICM: the open works best "
                 "with hands that can call it, or that fold easily (losing just the open).",
                 col=C["dim"])
        head("Postflop playability")
        if "spr" in o:
            spr = o["spr"]
            desc = ("committed: any more money in and you're all-in, so play for stacks or "
                    "let it go" if spr < 2.5 else
                    "medium: top pair is usually a stack-off" if spr < 6 else
                    "deep: hand strength, draws and position all matter")
            line(f"Called by the {o['caller']} (it calls {100 * o['caller_call']:.0f}% of hands), "
                 f"the flop pot is about {o['flop_pot']:.1f} bb with {o['behind']:.1f} bb behind: "
                 f"SPR {spr:.1f}, {desc}.")
            line("You'd have position (you act last after the flop)." if o["ip"] else
                 "You'd be out of position after the flop.")
        line(hand_note(o["hand"], o.get("spr", 5.0)))

    def _coach_mix(self, f, hand, head, line, C):
        """A decision inside a game with traps: what it is, how the solve plays your hand
        and what each option is worth, how the others play around it."""
        import final_table as FT
        d = f["mix"]
        node = d["node"]
        names = {a: FT.action_label(a, node) for a in FT.ACTION_LABEL}
        info = d["info"]
        for title, lines in info[:1]:
            for t in lines:
                line(t, col=C["dim"])
        head(f"Your hand: {hand}")
        mix, ev = d["mix"], d["ev"]
        line("The solve plays it: " + "  \u00b7  ".join(
            f"{names[a]} {100 * mix[a]:.0f}%" for a in sorted(mix, key=lambda a: -mix[a])
            if mix[a] >= 0.01), bold=True)
        base = "fold" if "fold" in ev else "check"
        others = [a for a in ev if a != base]
        line(f"Worth, compared with {names[base].lower()}ing: " + "  \u00b7  ".join(
            f"{names[a]} {money(ev[a] - ev[base], sign=True)}" for a in others) + ".")
        best = max(ev, key=ev.get)
        tol = max(1.0, 1e-4 * f["pool"])
        if FT.premium(hand):
            close = [a for a in ev if a != "fold" and
                     ev[a] >= ev[best] - FT.PREMIUM_MARGIN * f["pool"]]
            if len(close) > 1:
                line(f"A top-5% hand: {', '.join(names[a] for a in close)} are all within "
                     f"{money(FT.PREMIUM_MARGIN * f['pool'])} of the best here. Mix them (traps, "
                     f"small raises, jams), so nobody can read your big hands from your line.",
                     col=C["gold"])
        tied = [a for a in ev if ev[a] >= ev[best] - tol]
        traps = ("limp", "raise", "iso", "open", "check", "call", "3bet")
        if len(tied) > 1:
            line(f"{' and '.join(names[a] for a in tied)} are worth the same here (within "
                 f"{money(tol)}): mix them, as the solve does, so the others can't tell your "
                 f"strong hands from the rest.", col=C["gold"])
        elif best in traps and "jam" in ev and ev["jam"] > ev[base]:
            line(f"A trap: {names[best].lower()} earns {money(ev[best] - ev['jam'])} more than "
                 f"jamming. Let them put the chips in.", col=C["gold"])
        elif best == "jam":
            alt = max((ev[a] for a in ev if a not in ("jam", base)), default=None)
            if alt is not None and alt > ev[base]:
                line(f"Jam: it earns {money(ev['jam'] - alt)} more than the alternatives. "
                     f"Your hand does well enough when called, and folds out too much "
                     f"equity to want to see a flop.", col=C["gold"])
        if d.get("chip_mix"):
            cm = d["chip_mix"]
            line("In chips (no ICM) it would be: " + "  \u00b7  ".join(
                f"{names[a]} {100 * cm[a]:.0f}%" for a in sorted(cm, key=lambda a: -cm[a])
                if cm[a] >= 0.01) + ".", col=C["dim"])
        for title, lines in info[1:]:
            head(title)
            for t in lines:
                line(t)

    def _coach_info(self, f, head, line, C):
        """The coach for a decision that isn't all-in or fold: first the decision itself
        (opening, facing an open, facing a 3-bet, after the flop), then what ICM adds."""
        sp = self.tspot
        street = f["street"]
        analysed = False
        for key, fn in (("open2x", self._coach_open), ("facing", self._coach_facing),
                        ("vs3bet", self._coach_vs3bet), ("post", self._coach_post)):
            if sp.get(key):
                fn(sp[key], head, line, C)
                analysed = True
        if f["to_call"] > 0 and not analysed:
            need = f["to_call"] / (f["pot"] + f["to_call"])
            head("Pot odds")
            line(f"Calling {f['to_call']:g} bb into {f['pot']:g} bb needs {100 * need:.1f}% equity "
                 f"in chips. When your stack is at risk, ICM adds the premium below on top.")
        if f["opps"]:
            # The players this decision is about first (the opener, the 3-bettor), then the
            # biggest premiums: three at most.
            key_pos = {(sp.get("facing") or {}).get("opener"), (sp.get("vs3bet") or {}).get("who")}
            opps = sorted(f["opps"], key=lambda o: (o["seat"] not in key_pos, -o["mine"]))[:3]
            head("Risk premium (getting all-in)")
            for o in opps:
                rel = "covers you" if o["covers"] else "you cover them"
                line(f"{o['seat']} ({o['stack']:g} bb, {rel}): you need {50 + o['mine']:.1f}% "
                     f"(chip EV 50%), {o['mine']:+.1f} points; they need "
                     f"{50 + o['theirs']:.1f}% against you.")
        st, pays = sp["stacks"], sorted(sp["pays"], reverse=True)
        n = len(st)
        me = f["me"]
        if n >= 2:
            head("Pay jump")
            jump = pays[n - 2] - pays[n - 1]
            p_next = icm(st, [0.0] * (n - 1) + [1.0])[me]
            line(f"The next one out gets {money(pays[n - 1]).replace('.00', '')}; "
                 f"{ordinal(n - 1)} pays {money(pays[n - 2]).replace('.00', '')}: a jump of "
                 f"{money(jump).replace('.00', '')}. You're about {100 * p_next:.0f}% to be the "
                 f"next player out.")
        head("What ICM changes here")
        worst = max((o["mine"] for o in f["opps"] if o["covers"]), default=None)
        if street == "preflop":
            line(f"With {f['eff']:.0f} bb effective the bet sizes are your sims' (the hint); ICM "
                 f"comes in as the pot grows against your stack: the bigger it gets, the more the "
                 f"premiums above count.")
        else:
            src = "your Pio solve" if (sp.get("post") or {}).get("solve") else "your sims"
            line(f"The bet sizes come from {src} (chip EV). What ICM adds: get your stack in "
                 f"only with a margin over chip EV against players who cover you, and lean on "
                 f"the ones you cover: they're the ones paying the premium.")
        if worst is not None and worst > 3:
            line(f"Against a player who covers you here, that margin is about {worst:.0f} "
                 f"points of equity.", col=C["gold"])

    # -- drawing --
    def seat_xy(self, k, inward=0.0):
        """A point on the stadium rail: you (k = 0) at the bottom, then clockwise."""
        cx, cy, a, b = TABLE
        straight = 2 * (a - b)
        d = (k / 9) * (2 * straight + 2 * math.pi * b)
        for kind_, length in (("line", straight / 2), ("arc_l", math.pi * b), ("top", straight),
                              ("arc_r", math.pi * b), ("line2", straight / 2)):
            if d <= length:
                break
            d -= length
        if kind_ == "line":
            x, y = cx - d, cy + b
        elif kind_ == "arc_l":
            t = math.pi / 2 + d / b
            x, y = cx - (a - b) + b * math.cos(t), cy + b * math.sin(t)
        elif kind_ == "top":
            x, y = cx - (a - b) + d, cy - b
        elif kind_ == "arc_r":
            t = -math.pi / 2 + d / b
            x, y = cx + (a - b) + b * math.cos(t), cy + b * math.sin(t)
        else:
            x, y = cx + (a - b) - d, cy + b
        if inward:
            x += (cx - x) * inward
            y += (cy - y) * inward * 1.4
        return x, y

    def stadium(self, cx, cy, a, b, **kw):
        pts = []
        for k in range(19):
            t = -math.pi / 2 + math.pi * k / 18
            pts += [cx + (a - b) + b * math.cos(t), cy + b * math.sin(t)]
        for k in range(19):
            t = math.pi / 2 + math.pi * k / 18
            pts += [cx - (a - b) + b * math.cos(t), cy + b * math.sin(t)]
        return self.cv.create_polygon(pts, **kw)

    def draw_trainer(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        if self.sess is None or self.live is None:
            self.trainer_new_table()
            return
        x = self.draw_pay_buttons(20, 52) + 24
        cv.create_text(x, 65, text="Final table", fill=C["dim"], anchor="w", font=F(9))
        x += 76
        T.app_button(self, x, 52, 26, 26, "◀", C["card"], lambda: self.trainer_step_table(-1))
        cv.create_text(x + 78, 65, text=f"#{self.table_no} of {N_TABLES}", fill=C["text"],
                       font=FH(11))
        T.app_button(self, x + 130, 52, 26, 26, "▶", C["card"],
                     lambda: self.trainer_step_table(1))
        T.app_button(self, x + 166, 52, 120, 26, "Random table", C["card2"],
                     self.trainer_new_table)
        T.app_button(self, x + 294, 52, 120, 26, f"All {N_TABLES} tables", C["card2"],
                     lambda: self.trainer_pick(not self.picking))
        if hasattr(self.app, "ft_play_from_icm"):   # this table, full hands, in the trainer
            T.app_button(self, x + 426, 52, 190, 26, "Play full hands \u25b6", C["gold"],
                         lambda: self.app.ft_play_from_icm(self.table_no, self.pay_mode),
                         fg="#14171c")
        sx = self.W - 3 * 70 - 20
        for v, _ in TSPEEDS:
            on = v == self.tspeed
            T.app_button(self, sx, 52, 64, 26, v, C["accent"] if on else C["card2"],
                         lambda v=v: self.set_tspeed(v), fg="white" if on else C["dim"])
            sx += 70
        if self.picking:
            self.draw_table_grid()
        else:
            self.draw_live_table()
            self.draw_action_bar()
            if self.sess["over"]:
                self.draw_summary()
        self.draw_trainer_panel()

    def draw_live_table(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        lv, ss = self.live, self.sess
        cx, cy, a, b = TABLE
        self.stadium(cx, cy, a + 14, b + 14, fill="#12151a", outline="")
        self.stadium(cx, cy, a, b, fill=C["table"], outline=C["table_line"], width=2)
        self.stadium(cx, cy, a - 70, b - 62, fill="", outline="#1a1e25", width=1)
        pot = 0 if lv["stage"] == "done" else lv["dead"] + sum(
            d["bet"] for d in lv["seats"] if d)
        left = LEVEL_HANDS - (ss["hands"] - 1) % LEVEL_HANDS
        cv.create_text(cx, cy - 76, text=f"Hand {ss['hands']}  \u00b7  {blinds_text(ss['level'])}"
                       f" (up in {left})  \u00b7  {len(lv['order'])} left  \u00b7  "
                       f"{self.pay_label()}", fill=C["dim"], font=F(10))
        cv.create_text(cx, cy - 52, text=f"{fmt_bb(pot)} bb" if pot else "", fill=C["text"],
                       font=FH(18))
        for k in range(5):
            x = cx - 128 + k * 52
            if k < len(lv["board"]):
                self.app.card_on(cv, x, cy - 30, lv["board"][k], 48, 66)
            else:
                self.app.rrect_on(cv, x, cy - 30, x + 48, cy + 36, 6, fill="",
                                  outline="#20252e", width=1)
        if lv["caption"]:
            w = T.text_w(lv["caption"], FH(12)) + 36
            self.app.rrect_on(cv, cx - w / 2, cy + 44, cx + w / 2, cy + 72, 14,
                              fill=C["card2"], outline="")
            cv.create_text(cx, cy + 58, text=lv["caption"], fill=C["text"], font=FH(12))
        eq = lv["after"] if lv["after"] else lv["before"]
        for x in range(9):
            self.draw_live_seat(x, eq.get(x))

    def draw_live_seat(self, x_seat, eq):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        lv, ss = self.live, self.sess
        d = lv["seats"][x_seat]
        k = (x_seat - ss["hero"]) % 9
        x, y = self.seat_xy(k)
        me = k == 0
        r = 34 if me else 30
        if d is None:  # out before this hand
            pl, prize = ss["out"].get(x_seat, (0, 0.0))
            cv.create_oval(x - r, y - r, x + r, y + r, fill="#101216", outline="#1c2027", width=2)
            cv.create_text(x, y - 8, text=f"Out {ordinal(pl)}" if pl else "Empty",
                           fill=C["faint"], font=FH(9))
            cv.create_text(x, y + 9, text=money_k(prize), fill=C["faint"], font=F(9))
            return
        folded = d["folded"]
        cw, ch = (44, 62) if me else (26, 36)
        right_side = x <= TABLE[0] + 40
        cxs = x + r + 6 if right_side else x - r - 6 - 2 * cw - 3
        if not folded or d["reveal"] or me:
            for n_ in range(2):
                c = d["cards"][n_] if (me or d["reveal"]) else None
                self.app.card_on(cv, cxs + n_ * (cw + 3), y - ch / 2, c, cw, ch,
                                 dim=bool(folded and (d["reveal"] or me)))
        turn = lv["turn"] == x_seat and lv["stage"] != "done"
        ring = C["act"] if turn else (C["accent"] if me else C["table_line"])
        if folded:
            ring = "#1f242c"
        cv.create_oval(x - r - 3, y - r + 1, x + r + 3, y + r + 5, fill=C["shadow"], outline="")
        cv.create_oval(x - r, y - r, x + r, y + r, fill=C["seat"] if not folded else "#121418",
                       outline=ring, width=3 if (turn or me) else 2)
        col = C["faint"] if folded else C["text"]
        cv.create_text(x, y - 10, text=d["pos"], fill=col, font=FH(11 if me else 10))
        cv.create_text(x, y + 9, text=f"{fmt_bb(d['stack'])} bb", fill=col,
                       font=FH(11 if me else 10))
        if eq is not None:
            cv.create_text(x, y + r + 11, text=money_k(eq), fill=C["gold"] if me else C["dim"],
                           font=FH(10) if me else F(9))
        if d["action"]:
            word = d["action"].split()[0].rstrip(":")
            bcol = {"Fold": "#3a4252", "Call": C["call"], "All-in": C["allin"],
                    "Out": "#5a2430"}.get(word, C["r2"])
            ty = y - r - 14
            w = T.text_w(d["action"], FH(9)) + 16
            self.app.rrect_on(cv, x - w / 2, ty - 10, x + w / 2, ty + 10, 6, fill=bcol, outline="")
            cv.create_text(x, ty, text=d["action"], fill="white", font=FH(9))
        if d["bet"] > 0:
            bx, by = self.seat_xy(k, 0.36)
            cv.create_oval(bx - 9, by - 9, bx + 9, by + 9, fill=C["accent"] if me else "#5aa7ff",
                           outline="")
            cv.create_oval(bx - 5, by - 5, bx + 5, by + 5, fill="", outline="white")
            cv.create_text(bx + 14, by, text=f"{fmt_bb(d['bet'])} bb", fill=C["text"], anchor="w",
                           font=FH(10))
        if x_seat == lv["button"]:
            dx, dy = self.seat_xy(k, 0.2)
            dx += -r - 4 if x >= TABLE[0] else r + 4
            cv.create_oval(dx - 10, dy - 10, dx + 10, dy + 10, fill="white", outline="")
            cv.create_text(dx, dy, text="D", fill="#111111", font=FH(9))
        if d["result"]:
            good = d["result"].startswith("+")
            ty = y - r - 38
            w = T.text_w(d["result"], FH(10)) + 18
            self.app.rrect_on(cv, x - w / 2, ty - 11, x + w / 2, ty + 11, 8,
                              fill=C["good"] if good else C["bad"], outline="")
            cv.create_text(x, ty, text=d["result"], fill="white", font=FH(10))

    def decision_amount(self):
        sp = self.tspot
        st, hero = sp["stacks"], sp["hero"]
        if sp["kind"] == "jam":
            return st[hero]
        return min(st[hero] - sp["ante"][hero], st[sp["jammer"]])

    def draw_action_bar(self):
        T, cv = self.T, self.cv
        C, F = T.C, T.F
        sp, lv, ss = self.tspot, self.live, self.sess
        y, x0 = 676, 60
        if ss["over"]:
            return
        if lv["stage"] == "hero":
            go = "Jam" if sp["kind"] == "jam" else "Call"
            T.app_button(self, x0, y, 190, 56, f"{go.upper()} {fmt_bb(self.decision_amount())} bb",
                         "#2fa86a", lambda: self.trainer_answer("go"))
            T.app_button(self, x0 + 206, y, 190, 56, "FOLD", "#3b5a8c",
                         lambda: self.trainer_answer("fold"))
        elif lv["stage"] == "done":
            T.app_button(self, x0, y, 190, 56, "NEXT HAND", C["accent"], self.trainer_next_hand)
            if lv["auto"]:
                cv.create_text(x0 + 206, y + 28, text="dealing the next hand…",
                               fill=C["faint"], anchor="w", font=F(10))
        else:
            cv.create_text(x0, y + 28, text="Playing…", fill=C["dim"], anchor="w", font=F(11))
        if sp is not None:
            T.app_button(self, x0 + 420, y, 170, 56, "ICM Coach", C["gold"], self.open_coach,
                         fg="#14171c")
        T.app_button(self, x0 + 610, y, 150, 56, "End session", C["card2"], self.trainer_end,
                     fg=C["dim"])

    def draw_summary(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        ss = self.sess
        kind, pl, prize = ss["over"]
        cx, cy = TABLE[0], TABLE[1]
        w, h = 520, 250
        self.app.rrect_on(cv, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 18,
                          fill=C["panel"], outline=C["gold"], width=2)
        if kind == "win":
            title, col = f"You won the final table!  {money(prize).replace('.00', '')}", "#3ecf8e"
        elif kind == "bust":
            title, col = f"You finished {ordinal(pl)}:  {money(prize).replace('.00', '')}", C["gold"]
        else:
            title, col = f"Session ended: your stack was worth {money(prize)}", C["text"]
        cv.create_text(cx, cy - h / 2 + 36, text=title, fill=col, font=FH(15))
        pct = f"{100 * ss['right'] / ss['spots']:.0f}%" if ss["spots"] else "-"
        cv.create_text(cx, cy - h / 2 + 72, text=f"{ss['hands']} hands  ·  decisions "
                       f"{ss['right']} / {ss['spots']} right ({pct})  ·  mistakes cost "
                       f"{money(ss['lost'])}", fill=C["dim"], font=F(10))
        outs = sorted(ss["out"].items(), key=lambda kv: kv[1][0])
        txt = "   ".join(f"{ordinal(p_)} {('You' if x == ss['hero'] else 'seat ' + str(x + 1))}"
                       for x, (p_, _) in outs[:9])
        cv.create_text(cx, cy - h / 2 + 100, text=txt, fill=C["faint"], font=F(9), width=w - 40)
        T.app_button(self, cx - 240, cy + h / 2 - 80, 150, 50, "Play it again", C["accent"],
                     self.trainer_replay_table)
        T.app_button(self, cx - 75, cy + h / 2 - 80, 150, 50, "Next table ▶", C["card2"],
                     lambda: self.trainer_step_table(1))
        T.app_button(self, cx + 90, cy + h / 2 - 80, 150, 50, "Random table", C["card2"],
                     self.trainer_new_table)

    def pay_label(self):
        return self.pay_mode if self.pay_mode in POOLS else \
            money(sum(self.pays(9))).replace(".00", "")

    def trainer_title(self):
        sp = self.tspot
        st, hero = sp["stacks"], sp["hero"]
        pos = positions(len(st))
        if sp["kind"] == "jam":
            eff = self.effective_first_in(st, hero)
            return (f"Folded to you in the {pos[hero]} with {fmt_bb(st[hero])} bb "
                    f"({fmt_bb(eff)} bb effective). Jam or fold?")
        j = sp["jammer"]
        between = "everyone in between folds" if hero - j > 1 else "it's on you"
        return (f"{pos[j]} jams {fmt_bb(min(st[j], max(st[j + 1:])))} bb, {between}: you're in "
                f"the {pos[hero]} with {fmt_bb(st[hero])} bb. Call or fold?")

    def draw_trainer_panel(self):
        T, cv = self.T, self.cv
        C, F, FH = T.C, T.F, T.FH
        sp, lv, ss = self.tspot, self.live, self.sess
        x, y = PANEL_X, 96
        cv.create_rectangle(x - 16, 86, self.W, self.H, fill=C["panel"], outline="")
        cv.create_text(x, y, text=f"Push/fold drill  \u00b7  table #{self.table_no}  \u00b7  "
                       f"{self.pay_label()}  \u00b7  {len(self.alive_seats())} left",
                       fill=C["text"], anchor="w", font=FH(12))
        y += 22
        hero = ss["hero"]
        eq = (lv["after"] or lv["before"]).get(hero)
        if eq is not None:
            cv.create_text(x, y, text=f"Your stack: {fmt_bb(ss['stacks'][hero])} bb, worth "
                           f"{money(eq)}", fill=C["gold"], anchor="nw", font=F(10))
            y += 20
        pct = f"{100 * ss['right'] / ss['spots']:.0f}%" if ss["spots"] else "-"
        cv.create_text(x, y, text=f"This session: {ss['right']} / {ss['spots']} right ({pct})  "
                       f"·  mistakes cost {money(ss['lost'])}", fill=C["dim"], anchor="nw",
                       font=F(9))
        s_ = self.tscore
        cv.create_text(x, y + 16, text=f"All sessions: {s_['right']} / {s_['spots']} right  "
                       f"·  mistakes cost {money(s_['lost'])}", fill=C["faint"], anchor="nw",
                       font=F(9))
        y = self.draw_payouts(x, y + 46, len(self.alive_seats()), colw=128)
        if sp is None:
            return
        item = cv.create_text(x, y, text=("Your decision: " if lv["stage"] == "hero" else
                                          "Your last decision: ") + self.trainer_title(),
                              fill=C["text"], anchor="nw", font=F(10), width=380)
        y = cv.bbox(item)[3] + 8
        if not sp["answer"]:
            return
        go = "Jam" if sp["kind"] == "jam" else "Call"
        g = sp["res"]["gain"][sp["hand"]]
        gc = sp["chip"]["gain"][sp["hand"]]
        right = (g > 0) == (sp["answer"] == "go")
        best = go if g > 0 else "Fold"
        cv.create_text(x, y, text=("Right: " if right else "Wrong: ") + best + f" {sp['hand']}",
                       fill="#3ecf8e" if right else "#ef6b73", anchor="nw", font=FH(14))
        y += 26
        cv.create_text(x, y, text=f"{go} vs fold: {money(g, sign=True)}"
                       + ("" if right else f"  (cost you {money(abs(g))})"),
                       fill=C["text"], anchor="nw", font=F(10))
        y += 18
        cv.create_text(x, y, text=f"Chip EV {gc:+.2f} bb ({go.lower() if gc > 0 else 'fold'})"
                       + ("  ·  ICM flips this one" if (gc > 0) != (g > 0) else ""),
                       fill=C["dim"], anchor="nw", font=F(9))
        gy = max(y + 34, self.H - 13 * 17 - 10)
        self.draw_mini_chart(x, gy, sp, go, 28, 17)

    def draw_table_grid(self):
        """The preset tables, 100 to a page: click one to sit down."""
        T, cv = self.T, self.cv
        C, F = T.C, T.F
        x0, y0, cw, ch = 30, 120, 86, 62
        cv.create_text(x0, 102, text="Pick a final table (short stack / chip leader, in bb). "
                       "This starts a new session.", fill=C["dim"], anchor="w", font=F(10))
        page = getattr(self, "grid_page", (self.table_no - 1) // 100)
        pages = (N_TABLES + 99) // 100
        for p in range(pages):
            lo, hi = 100 * p + 1, min(N_TABLES, 100 * p + 100)
            on = p == page
            T.app_button(self, x0 + 10 * cw - 3 * 96 + p * 96 - 6, 90, 90, 24, f"{lo}-{hi}",
                         C["accent"] if on else C["card2"],
                         lambda v=p: self._grid_page(v), fg="white" if on else C["dim"])
        for k in range(100 * page + 1, min(N_TABLES, 100 * page + 100) + 1):
            r, c = divmod(k - 1 - 100 * page, 10)
            x, y = x0 + c * cw, y0 + r * ch
            st = preset_table(k)
            ts = self.table_score.get(k)
            on = k == self.table_no
            T.app_button(self, x, y, cw - 6, ch - 6, f"#{k}", C["accent"] if on else C["card2"],
                         lambda v=k: self.trainer_set_table(v), fg="white" if on else C["text"])
            info = f"{min(st):g} / {max(st):g}" + (f"  ✓{ts[1]}" if ts else "")
            cv.create_text(x + (cw - 6) / 2, y + ch - 18, text=info,
                           fill="white" if on else C["dim"], font=F(7))

    def _grid_page(self, p):
        self.grid_page = p
        self.draw()

    def draw_mini_chart(self, gx, gy, sp, go, cw=30, ch=22):
        T, cv = self.T, self.cv
        C, F = T.C, T.F
        cv.create_text(gx, gy - 10, text=f"{go} chart under ICM ({100 * sp['res']['share']:.0f}% of "
                       f"hands; chip EV {100 * sp['chip']['share']:.0f}%)", fill=C["dim"],
                       anchor="w", font=F(8))
        for r, r1 in enumerate(T.RANKS):
            for c, r2 in enumerate(T.RANKS):
                h = r1 + r2 if r == c else (r1 + r2 + "s" if r < c else r2 + r1 + "o")
                x1, y1 = gx + c * cw, gy + r * ch
                g = sp["res"]["gain"][h]
                cv.create_rectangle(x1, y1, x1 + cw - 2, y1 + ch - 2,
                                    fill="#2fa86a" if g > 0 else "#3b5a8c",
                                    outline="white" if h == sp["hand"] else "",
                                    width=2 if h == sp["hand"] else 1)
                cv.create_text(x1 + cw / 2 - 1, y1 + ch / 2 - 1, text=h, fill="white", font=F(6))



class CoachHost:
    """The ICM Coach popup outside the ICM window (the trainer's Final table mode): a
    decision record as hero_spot (final_table.py) makes it."""
    coach_facts = ICMWindow.coach_facts
    open_coach = ICMWindow.open_coach
    _coach_info = ICMWindow._coach_info
    _coach_open = ICMWindow._coach_open
    _coach_facing = ICMWindow._coach_facing
    _coach_post = ICMWindow._coach_post
    _coach_vs3bet = ICMWindow._coach_vs3bet
    _coach_exploit = ICMWindow._coach_exploit
    _coach_bluff = ICMWindow._coach_bluff
    _coach_mix = ICMWindow._coach_mix

    def __init__(self, T, parent, spot):
        self.T, self.top, self.tspot, self.coach = T, parent, spot, None
