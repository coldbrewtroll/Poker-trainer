"""8-max preflop strategy: every raise / re-raise spot, polarized value + bluff ranges, and
all-ins that account for fold equity.

Ranges are shares of all hands by strength (TOP in preflop_data: 0 = AA ... 1 = 32o).
Re-raise ranges are polarized: a value part (the strongest hands) plus a bluff part
taken from hands that would otherwise fold but block strong hands or play well (suited
wheel aces, suited kings, suited connectors), sized as a ratio of the value part.

All-ins use shove_ev(): for every player still to respond, estimate how much of their
range calls at the price they're getting (the weakest hand that still has enough equity
against our shoving range), then EV = fold equity + equity when called.
"""

from bisect import bisect_left

from bots.contender import eq_vs
from bots.preflop_data import TOP

COMBOS = {}
for _h in TOP:
    COMBOS[_h] = 6 if len(_h) == 2 else 4 if _h[2] == "s" else 12
ORDER = sorted(TOP, key=TOP.get)            # strongest first
CUM = [TOP[h] for h in ORDER]               # cumulative share at each hand

# Bluff candidates, best first: blockers to the strongest hands and good playability.
BLUFF_3BET = ["A5s", "A4s", "A3s", "A2s", "K9s", "K8s", "K7s", "K6s", "Q9s", "J9s", "T8s",
              "97s", "86s", "75s", "65s", "54s", "K5s", "Q8s", "A8o", "KJo", "QJo", "64s"]
BLUFF_4BET = ["A5s", "A4s", "A3s", "A2s", "K9s", "K8s", "KTs", "A9s", "A8s", "K7s", "Q9s",
              "J9s", "T9s", "KJo"]
BLUFF_5BET = ["A5s", "A4s"]


def marginal(share):
    """The weakest hand inside the top `share` of hands."""
    k = min(len(ORDER) - 1, bisect_left(CUM, share))
    return ORDER[k]


_CALL_CACHE = {}


def call_share(range_share, vs_share, need):
    """How much of a range (its top `range_share`) has at least `need` equity against
    the top `vs_share`: the share of hands that would call an all-in at that price.
    Inputs are rounded (to 0.5%) and results cached: the same spots come up again and again."""
    key = (round(range_share * 200), round(vs_share * 200), round(need * 200))
    hit = _CALL_CACHE.get(key)
    if hit is None:
        if len(_CALL_CACHE) > 200000:
            _CALL_CACHE.clear()
        hit = _CALL_CACHE[key] = _call_share(key[0] / 200, key[1] / 200, key[2] / 200)
    return min(hit, range_share)


def _call_share(range_share, vs_share, need):
    lo, hi = 0.0, range_share
    if eq_vs(marginal(min(range_share, 1.0)), vs_share) >= need:
        return range_share
    if eq_vs(ORDER[0], vs_share) < need:
        return 0.0
    for _ in range(9):
        mid = (lo + hi) / 2
        if eq_vs(marginal(mid), vs_share) >= need:
            lo = mid
        else:
            hi = mid
    return lo


def bluff_set(order, combos_wanted, value_share, max_strength=1.0):
    """Bluff hands from `order` (skipping value hands) until about `combos_wanted`."""
    out, total = set(), 0.0
    if combos_wanted <= 0:
        return out
    for h in order:
        if TOP[h] <= value_share or TOP[h] > max_strength:
            continue
        out.add(h)
        total += COMBOS[h]
        if total >= combos_wanted:
            break
    return out


def shove_ev(hand, pot, my_risk, my_total, responders, our_share, call_scale=1.0,
             overcall=0.0):
    """EV in chips of going all-in (folding = 0).

    responders: [(range_share, bet_already, stack_behind[, seen_rate, seen_weight])] for
    each player who still has to respond, in order. our_share: how wide our all-in range
    looks to them. seen_rate: how often this player has actually called all-ins, blended in
    with weight seen_weight (0..1) in place of the price-based estimate.
    call_scale: >1 if they call more than the price justifies (from the opponent model).
    overcall: after someone calls, each later player still calls this much as often per
    caller already in (0 = the first call ends it, as heads-up); their ranges then all
    share the pot (see _shove_multi).
    """
    if overcall > 0 and len(responders) > 1:
        return _shove_multi(hand, pot, my_risk, my_total, responders, our_share, call_scale,
                            overcall)
    ev = 0.0
    p_reach = 1.0  # chance everyone before this responder folded
    for r, bet, behind, *seen in responders:
        cost = min(behind, max(0, my_total - bet))
        if cost <= 0:
            continue
        final_pot = pot + my_risk + cost
        need = cost / final_pot
        c = min(r, call_share(r, our_share, need) * call_scale)
        p_call = c / r if r > 0 else 0.0
        if seen and seen[1] > 0:
            p_call = (1 - seen[1]) * p_call + seen[1] * seen[0]
            c = max(c, min(r, p_call * r))  # calling wider means a weaker calling range
        if p_call > 0:
            # If they're shorter, only their stack is at risk for us.
            risk = min(my_risk, my_risk - (my_total - bet - cost))
            pot_called = pot + risk + cost
            ev += p_reach * p_call * (eq_vs(hand, max(c, 0.02)) * pot_called - risk)
        p_reach *= 1 - p_call
    return ev + p_reach * pot


def _call_odds(r, bet, behind, seen, pot, my_risk, my_total, our_share, call_scale):
    """(chance this responder calls, the range they call with, their cost, our risk)."""
    cost = min(behind, max(0, my_total - bet))
    if cost <= 0:
        return 0.0, 0.0, 0, 0
    need = cost / (pot + my_risk + cost)
    c = min(r, call_share(r, our_share, need) * call_scale)
    p_call = c / r if r > 0 else 0.0
    if seen and seen[1] > 0:
        p_call = (1 - seen[1]) * p_call + seen[1] * seen[0]
        c = max(c, min(r, p_call * r))
    risk = min(my_risk, my_risk - (my_total - bet - cost))  # only their stack if shorter
    return p_call, c, cost, risk


def _shove_multi(hand, pot, my_risk, my_total, responders, our_share, call_scale, overcall):
    """shove_ev when later players can still call after someone has: walks the call / fold
    branches (pruning unlikely ones) and splits the pot multiway when several call."""
    odds = [_call_odds(r, bet, behind, seen, pot, my_risk, my_total, our_share, call_scale)
            for r, bet, behind, *seen in responders]

    def walk(i, p, called):
        if p < 0.005:
            return 0.0
        if i == len(odds):
            if not called:
                return p * pot
            eqs = [eq_vs(hand, max(c, 0.02)) for c, _, _ in called]
            if len(eqs) == 1:
                eq = eqs[0]
            else:
                o = sum((1 - e) / e for e in (min(max(e, 0.01), 0.99) for e in eqs))
                eq = 1 / (1 + o)
            risk = max(rk for _, _, rk in called)
            return p * (eq * (pot + risk + sum(min(ct, risk) for _, ct, _ in called)) - risk)
        p_call, c, cost, risk = odds[i]
        if called:
            p_call *= overcall ** len(called)
        if p_call <= 0 or len(called) >= 2:  # a third caller changes little: keep it fast
            return walk(i + 1, p, called)
        return (walk(i + 1, p * (1 - p_call), called) +
                walk(i + 1, p * p_call, called + [(c, cost, risk)]))

    return walk(0, 1.0, [])


def polar(strength, hand, value_share, bluff_ratio, order, max_bluff_strength=0.75):
    """'value' / 'bluff' / None for a polarized re-raise range."""
    if strength <= value_share:
        return "value"
    value_combos = value_share * 1326
    if hand in bluff_set(order, bluff_ratio * value_combos, value_share, max_bluff_strength):
        return "bluff"
    return None


_RANGE_CACHE = {}


def shove_range(pot, my_risk, my_total, responders, call_scale=1.0, floor=0.02, overcall=0.0):
    """The widest all-in range (share of hands) whose weakest hand still has EV > 0 when
    opponents call against that same range: a range that stays profitable even after
    they see how wide we shove. Cached, so a repeat spot costs nothing."""
    key = (pot, my_risk, my_total, tuple(tuple(round(v, 3) for v in t) for t in responders),
           round(call_scale, 2), floor, overcall)
    hit = _RANGE_CACHE.get(key)
    if hit is not None:
        return hit

    def ok(share):
        return shove_ev(marginal(share), pot, my_risk, my_total, responders, share,
                        call_scale, overcall) > 0

    if ok(1.0):
        out = 1.0
    elif not ok(floor):
        out = floor
    else:
        lo, hi = floor, 1.0
        for _ in range(5):  # to about 3% of hands: plenty, and keeps a cold spot fast
            mid = (lo + hi) / 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        out = lo
    if len(_RANGE_CACHE) > 50000:
        _RANGE_CACHE.clear()
    _RANGE_CACHE[key] = out
    return out
