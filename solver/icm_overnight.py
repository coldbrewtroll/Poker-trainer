"""Overnight ICM audit: every dollar figure of the final table's all-in decisions, worked
out again independently, plus a simulation of the real hands.

    python3 solver/icm_overnight.py --hours 7 --worker 1 --out results_1.jsonl
    python3 solver/icm_overnight.py --summary results_*.jsonl

Each worker loops until the time is up over spots of two kinds: random ones (2-8 players,
any stacks, any prize pool, a first-in jam or a call of a jam) and real ones read from
final tables the bots play (re-jams over an open, calls of a jam over your own open).
For every spot it checks:

  chips    the stacks after every outcome (everyone folds, you fold, each caller wins or
           loses), worked out from the betting itself (blinds, the big blind ante as dead
           money, raises already in), valued with a brute-force ICM (every finishing order
           spelled out): against the trainer's Spot values.
  model    the trainer's jam / call gain for all 169 hands, recomputed card by card from
           the solve's ranges: must match (it's the number behind your grade, the bots'
           all-in decisions and the coach's verdict).
  combos   the same per actual combo (the trainer averages over a hand's combos first):
           how much that shortcut moves the numbers, and whether it ever flips a decision.
  sim      the real thing: deal the cards, the callers call with the solve's ranges, run
           the board out, pay the chips, value the stacks by brute-force ICM. The average
           gain over thousands of deals must agree with the trainer's number; a "flip" is a
           hand where the simulation is confidently on the other side of zero.
  converge the same spot solved 10x longer (400 rounds): hands whose decision changes,
           and what that change is worth.
  exploit  the coach's exploit numbers (opponents calling / jamming wider or tighter):
           the scaled ranges and the gains recomputed.
  reading  (real spots) how the spot was read from the table: stacks with the ante,
           your seat, the jammer, the blinds and raises in, who can still call, who takes
           the pot if you fold, the opener's range; your grade (right / wrong, dollars);
           and whether the bot in the seat did what the ICM answer says (when another
           rule overrides it, which one).
Once, split over the workers: the 169 x 169 all-in equity table against simulated
showdowns, and its card-removal weights against counting the combos.
"""

import argparse
import glob
import itertools
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    import tkinter  # noqa: F401
except ImportError:      # (no Tk here: the phone version's stand-in, same trainer code)
    sys.path.insert(0, str(ROOT / "web" / "py"))
    import tkstub
    tkstub.install()

import icm_study as I  # noqa: E402
import final_table as FT  # noqa: E402
import match8  # noqa: E402
from cards import card, evaluate  # noqa: E402

BB = match8.BIG_BLIND
RANKS = "23456789TJQKA"
SUITS = "cdhs"


# ------------------------------------------------------------------ hands and combos

def combos_of(h):
    """Every two-card combo (card ints) of a hand class like 'AKs', 'T9o', '77'."""
    r1, r2 = h[0], h[1]
    out = []
    for s1 in SUITS:
        for s2 in SUITS:
            if r1 == r2 and SUITS.index(s2) <= SUITS.index(s1):
                continue
            if r1 != r2 and len(h) == 3 and (s1 == s2) != (h[2] == "s"):
                continue
            out.append((card(r1 + s1), card(r2 + s2)))
    return out


NAMES = I.exact()[0]
IDX = {h: i for i, h in enumerate(NAMES)}
E = I.exact()[2]
T = I.ties()        # split-pot chances (a chop is valued as a chop, not as half a win)
COMBOS = [combos_of(h) for h in NAMES]
ALL = [(c, k) for k, cs in enumerate(COMBOS) for c in cs]       # (combo, class index)


def class_of(c1, c2):
    a, b = sorted((c1, c2), key=lambda c: -(c >> 2))
    ra, rb = RANKS[a >> 2], RANKS[b >> 2]
    if ra == rb:
        return IDX[ra + rb]
    return IDX[ra + rb + ("s" if (a & 3) == (b & 3) else "o")]


# check our own card numbering agrees with cards.card()
assert all(class_of(*c) == k for c, k in ALL)


# ------------------------------------------------------------------ independent ICM

def icm_brute(stacks, pays):
    """Malmuth-Harville by spelling out every finishing order; busted players share the
    places below the others equally."""
    n = len(stacks)
    pays = (list(pays) + [0.0] * n)[:n]
    alive = [i for i in range(n) if stacks[i] > 1e-9]
    dead = [i for i in range(n) if stacks[i] <= 1e-9]
    eq = [0.0] * n
    tot0 = sum(stacks[i] for i in alive)
    for order in itertools.permutations(alive):
        p, left = 1.0, tot0
        for i in order:
            p *= stacks[i] / left
            left -= stacks[i]
            if p == 0:
                break
        for place, i in enumerate(order):
            eq[i] += p * pays[place]
    if dead:
        low = pays[len(alive):]
        for i in dead:
            eq[i] = sum(low) / len(dead)
    return eq


class Values:
    """Brute-force ICM values, cached per stack vector."""

    def __init__(self, pays, chips=False):
        self.pays, self.chips, self.cache = list(pays), chips, {}

    def __call__(self, stacks):
        key = tuple(round(x, 9) for x in stacks)
        if key not in self.cache:
            self.cache[key] = list(stacks) if self.chips else icm_brute(stacks, self.pays)
        return self.cache[key]


# ------------------------------------------------------------------ chips, from the betting

def outcomes(stacks, hero, posts=None, responders=None, fold_to=None, ante=I.ANTE):
    """The stacks after each way a first-in jam by `hero` can go, from the betting:
    each player has `posts` in (blinds / an open; they count towards an all-in), the big
    blind (last seat) has also put in the ante, which is dead (nobody matches it; it goes
    to whoever wins the pot). The hero jams everything; a caller calls everything; the
    pots are settled layer by layer like the engine (engine8._settle).
    Returns {"steal": stacks, "fold": stacks, "win": {i: stacks}, "lose": {i: stacks},
    "chop": {i: stacks}}."""
    n = len(stacks)
    a = [0.0] * n
    a[n - 1] = min(ante, stacks[n - 1])
    if posts is None:
        bet = [0.0] * n
        if n >= 2:
            bet[n - 2] = min(0.5, stacks[n - 2])
            bet[n - 1] = min(1.0, stacks[n - 1] - a[n - 1])
    else:
        bet = [min(p, s - x) for p, s, x in zip(posts, stacks, a)]
    put = [b + x for b, x in zip(bet, a)]            # what each has in the pot
    behind = list(responders) if responders is not None else list(range(hero + 1, n))
    pot = sum(put)
    left = [s - p for s, p in zip(stacks, put)]

    steal = left[:]
    steal[hero] += pot
    fold = left[:]
    fold[n - 1 if fold_to is None else fold_to] += pot

    def showdown(i, winner):
        # hero and i all in (live chips: everything but the ante); the rest stay as posted
        live = [x - y for x, y in zip(stacks, a)]       # what each can bet
        commit = bet[:]
        commit[hero], commit[i] = live[hero], live[i]
        dead = sum(a)
        out = left[:]
        out[hero], out[i] = stacks[hero] - commit[hero] - a[hero], stacks[i] - commit[i] - a[i]
        levels = sorted({c for c in commit if c > 0})
        prev = 0.0
        for lv in levels:
            layer = sum(min(c, lv) - min(c, prev) for c in commit)
            elig = [k for k in (hero, i) if commit[k] >= lv]
            if not elig:                    # only folded money here: to the two in
                elig = [hero, i]
            if winner == "chop" and len(elig) == 2:
                out[hero] += layer / 2
                out[i] += layer / 2
            elif len(elig) == 1:
                out[elig[0]] += layer
            else:
                out[hero if winner == "hero" else i] += layer
            prev = lv
        if winner == "chop":
            out[hero] += dead / 2
            out[i] += dead / 2
        else:
            out[hero if winner == "hero" else i] += dead
        return out

    win = {i: showdown(i, "hero") for i in behind}
    lose = {i: showdown(i, "them") for i in behind}
    chop = {i: showdown(i, "chop") for i in behind}
    for d in (steal, fold, *win.values(), *lose.values(), *chop.values()):
        assert abs(sum(d) - sum(stacks)) < 1e-9, "chips not conserved in an outcome"
    return {"steal": steal, "fold": fold, "win": win, "lose": lose, "chop": chop,
            "behind": behind}


# ------------------------------------------------------------------ the gain, card by card

def compat_tables():
    """For every hero combo: the list of opponent combos that don't share a card."""
    return None


def three_way(num, tnum, den, hv, i):
    """The hero's value of a showdown with i: win, lose or split, each at its own value
    (equities count ties as half: wins = eq - tie / 2)."""
    if den <= 0:
        return 0.5 * (hv["win"][i] + hv["lose"][i])
    eq, tie = num / den, tnum / den
    return (eq - tie / 2) * hv["win"][i] + (1 - eq - tie / 2) * hv["lose"][i] + tie * hv["chop"][i]


def gains_by_combo(ranges, priors, behind, hero_vals, against_jam=False, jam_w=None):
    """The hero's gain for each of the 169 hands, worked out over the hero's actual combos
    and every opponent combo that fits with them.
    ranges: {i: per-class call weights} for each responder; priors: {i: per-class weights}
    of the hands they can hold. hero_vals: {"steal", "fold", "win": {i}, "lose": {i}}
    (values for the hero).
    Returns (class-aggregated gains, the trainer's way; per-combo-averaged gains)."""
    agg, per = [0.0] * 169, [0.0] * 169
    for h in range(169):
        mine = COMBOS[h]
        # sums over hero combos, class-aggregated like the trainer's W / E tables
        for_class = {}
        per_combo_ev = 0.0
        for (c1, c2) in mine:
            ev, reach = 0.0, 1.0
            for i in behind:
                w, pr = ranges[i], priors.get(i) if priors else None
                num = den = pden = tnum = 0.0
                for (o1, o2), k in ALL:
                    if o1 in (c1, c2) or o2 in (c1, c2):
                        continue
                    wk = w[k] * (pr[k] if pr else 1.0)
                    if pr:
                        pden += pr[k]
                    if wk:
                        den += wk
                        num += wk * E[h][k]
                        tnum += wk * T[h][k]
                pc = (den / pden if pden > 0 else 0.0) if pr else den / 1225.0
                s = for_class.setdefault(i, [0.0, 0.0, 0.0, 0.0])
                s[0] += num
                s[1] += den
                s[2] += pden if pr else 1225.0
                s[3] += tnum
                if pc > 0:
                    ev += reach * pc * three_way(num, tnum, den, hero_vals, i)
                reach *= 1 - pc
            per_combo_ev += ev + reach * hero_vals["steal"]
        per[h] = per_combo_ev / len(mine) - hero_vals["fold"]
        ev, reach = 0.0, 1.0
        for i in behind:
            num, den, pden, tnum = for_class[i]
            pc = den / pden if pden > 0 else 0.0
            if pc > 0:
                ev += reach * pc * three_way(num, tnum, den, hero_vals, i)
            reach *= 1 - pc
        agg[h] = ev + reach * hero_vals["steal"] - hero_vals["fold"]
    return agg, per


def call_gains_by_combo(jam_w, win_v, lose_v, fold_v, chop_v):
    """Calling a jam from a jam range (per-class weights): gain for each of 169 hands,
    class-aggregated (the trainer's way) and per combo."""
    hv = {"win": {0: win_v}, "lose": {0: lose_v}, "chop": {0: chop_v}}
    agg, per = [0.0] * 169, [0.0] * 169
    for h in range(169):
        tn = td = tt = 0.0
        pc_sum = 0.0
        for (c1, c2) in COMBOS[h]:
            num = den = tnum = 0.0
            for (o1, o2), k in ALL:
                if jam_w[k] and not (o1 in (c1, c2) or o2 in (c1, c2)):
                    den += jam_w[k]
                    num += jam_w[k] * E[h][k]
                    tnum += jam_w[k] * T[h][k]
            tn += num
            td += den
            tt += tnum
            pc_sum += three_way(num, tnum, den, hv, 0) - fold_v
        agg[h] = three_way(tn, tt, td, hv, 0) - fold_v
        per[h] = pc_sum / len(COMBOS[h])
    return agg, per


# ------------------------------------------------------------------ simulating the hands

def sample_hand(rng, dead, weights):
    """A random hand (two cards not in `dead`), accepted with probability weights[class]."""
    if not any(weights):
        return None
    for _ in range(5000):
        a, b = rng.sample(range(52), 2)
        if a in dead or b in dead:
            continue
        k = class_of(a, b)
        if rng.random() < weights[k]:
            return (a, b), k
    return None


def runout(rng, dead):
    deck = [c for c in range(52) if c not in dead]
    return rng.sample(deck, 5)


def sim_jam(rng, h, n_deals, behind, call_w, priors, vals, stacks_out):
    """The hero jams hand class h: deal everything, the responders (in order) hold random
    hands (from their range when they have one) and call with the solve's call weights,
    the first caller plays it out. Returns (mean gain over folding, standard error)."""
    hero_v = lambda st: vals(st)                                     # noqa: E731
    v_steal = vals(stacks_out["steal"])[HERO[0]]
    v_fold = vals(stacks_out["fold"])[HERO[0]]
    res = []
    for _ in range(n_deals):
        c1, c2 = rng.choice(COMBOS[h])
        dead = {c1, c2}
        value = v_steal
        for i in behind:
            pr = priors.get(i) if priors else None
            got = sample_hand(rng, dead, pr) if pr else None
            if pr and got is None:
                continue
            if not pr:
                a, b = rng.sample([c for c in range(52) if c not in dead], 2)
                got = ((a, b), class_of(a, b))
            (o1, o2), k = got
            dead |= {o1, o2}
            if rng.random() < call_w[i][k]:
                board = runout(rng, dead)
                mine, theirs = evaluate([c1, c2] + board), evaluate([o1, o2] + board)
                key = "win" if mine > theirs else "lose" if mine < theirs else "chop"
                value = hero_v(stacks_out[key][i])[HERO[0]]
                break
        res.append(value - v_fold)
    m = statistics.fmean(res)
    se = statistics.pstdev(res) / math.sqrt(len(res))
    return m, se


def sim_call(rng, h, n_deals, jam_w, stacks_out, jammer, hero, vals):
    """The hero calls a jam with hand class h, the jammer holding a random hand from the
    jam range. Returns (mean gain over folding, standard error)."""
    v_fold = vals(stacks_out["steal"])[hero]
    res = []
    for _ in range(n_deals):
        c1, c2 = rng.choice(COMBOS[h])
        got = sample_hand(rng, {c1, c2}, jam_w)
        if got is None:
            continue
        (o1, o2), _ = got
        board = runout(rng, {c1, c2, o1, o2})
        mine, theirs = evaluate([c1, c2] + board), evaluate([o1, o2] + board)
        # the jammer is "hero" of the outcomes; the caller is responder `hero`
        key = "lose" if mine > theirs else "win" if mine < theirs else "chop"
        res.append(vals(stacks_out[key][hero])[hero] - v_fold)
    m = statistics.fmean(res)
    se = statistics.pstdev(res) / math.sqrt(len(res))
    return m, se


HERO = [0]


# ------------------------------------------------------------------ one spot

def norm(w):
    return [max(0.0, min(1.0, x)) for x in w]


def audit_spot(rng, spot, pays, out, deals, converge, label):
    """All the checks for one spot. spot: {"kind", "stacks", "me", "jammer", "kw"}."""
    st, me, kind = spot["stacks"], spot["me"], spot["kind"]
    kw = spot.get("kw") or {}
    who = me if kind == "jam" else spot["jammer"]
    pool = sum(pays[:len(st)])
    rec = {"src": label, "kind": kind, "n": len(st), "stacks": [round(x, 2) for x in st],
           "who": who, "me": me, "pool": round(pool), "re": bool(kw), "fail": []}
    t0 = time.time()
    res = FT.solve(st, pays, who, **kw)
    sp = res["spot"]
    vals = Values(pays)
    o = outcomes(st, who, kw.get("posts"), kw.get("responders"), kw.get("fold_to"))
    tol = 1e-6 * pool + 1e-6

    # chips -> values, against the trainer's Spot
    bad = []
    hv = vals(o["steal"])
    for k in [who] + o["behind"]:
        if abs(hv[k] - sp.v_steal[k]) > tol:
            bad.append(("steal", k, hv[k], sp.v_steal[k]))
    if abs(vals(o["fold"])[who] - sp.v_fold[who]) > tol:
        bad.append(("fold", who, vals(o["fold"])[who], sp.v_fold[who]))
    for i in o["behind"]:
        for key, mine in (("win", sp.win[i]), ("lose", sp.lose[i]), ("chop", sp.chop[i])):
            v = vals(o[key][i])
            for k in (who, i):
                if abs(v[k] - mine[k]) > tol:
                    bad.append((key, i, k, v[k], mine[k]))
    if bad:
        rec["fail"].append({"chips": bad[:4]})

    hero_vals = {"steal": sp.v_steal[who], "fold": sp.v_fold[who],
                 "win": {i: sp.win[i][who] for i in o["behind"]},
                 "lose": {i: sp.lose[i][who] for i in o["behind"]},
                 "chop": {i: vals(o["chop"][i])[who] for i in o["behind"]}}
    priors = {i: norm(w) for i, w in (res.get("priors") or {}).items()}
    calls = {i: norm(res["call_ranges"][i]) for i in o["behind"]}

    if kind == "jam":
        agg, per = gains_by_combo(calls, priors, o["behind"], hero_vals)
        app = [res["gain"][NAMES[h]] for h in range(169)]
    else:
        cf = I.call_from(res, me)
        jam_w = norm(res["jam_range"])
        agg, per = call_gains_by_combo(jam_w, sp.lose[me][me], sp.win[me][me], sp.v_steal[me],
                                       vals(o["chop"][me])[me])
        app = [cf["gain"][NAMES[h]] for h in range(169)]
    worst = max(abs(a - b) for a, b in zip(agg, app))
    rec["model_diff"] = worst
    if worst > 1e-6 * pool + 1e-3:
        rec["fail"].append({"model": worst})
    # combos: the class-averaging shortcut
    w = [len(COMBOS[h]) for h in range(169)]
    flips = [h for h in range(169) if (per[h] > 0) != (app[h] > 0)]
    rec["combo_maxdiff"] = max(abs(a - b) for a, b in zip(per, app))
    rec["combo_flips"] = sum(w[h] for h in flips) / 1326
    rec["combo_flip_cost"] = max((abs(per[h]) for h in flips), default=0.0)

    # the simulation: two random hands and the most marginal one
    order = sorted(range(169), key=lambda h: abs(app[h]))
    picks = [order[0], rng.randrange(169), rng.randrange(169)]
    sims = []
    if kind == "jam":
        HERO[0] = who
        for h in picks:
            m, se = sim_jam(rng, h, deals, o["behind"], calls, priors, vals, o)
            sims.append((NAMES[h], app[h], m, se))
    else:
        for h in picks:
            m, se = sim_call(rng, h, deals, jam_w, o, who, me, vals)
            sims.append((NAMES[h], app[h], m, se))
    rec["sim"] = [(h, round(a, 2), round(m, 2), round(se, 2)) for h, a, m, se in sims]
    for h, a, m, se in sims:
        z = (m - a) / se if se > 0 else 0.0
        if abs(z) > 5 and abs(m - a) > 0.002 * pool:
            rec["fail"].append({"sim": (h, a, m, se)})

    # converge: 2000 rounds (the trainer's solve: FT.solve, 120)
    if converge:
        long = I.solve_jam(st, I.icm_value(list(pays)), who, iters=2000, linear=True,
                           posts=list(kw["posts"]) if kw.get("posts") else None,
                           responders=list(kw["responders"]) if kw.get("responders") else None,
                           fold_to=kw.get("fold_to"),
                           priors={k: list(v) for k, v in kw["priors"].items()}
                           if kw.get("priors") else None)
        if kind == "jam":
            g400 = [long["gain"][NAMES[h]] for h in range(169)]
        else:
            g400 = [I.call_from(long, me)["gain"][NAMES[h]] for h in range(169)]
        fl = [h for h in range(169) if (g400[h] > 0) != (app[h] > 0)]
        rec["conv_flips"] = sum(w[h] for h in fl) / 1326
        rec["conv_flip_cost"] = max((abs(g400[h]) for h in fl), default=0.0)
        rec["conv_flip_hands"] = [NAMES[h] for h in fl][:6]
        # the grade: what an answer it counts right loses by the long solve
        close = FT.ALLIN_CLOSE * pool
        share = res["jam_range"] if kind == "jam" else res["call_ranges"][me]
        worst = 0.0
        for h in range(169):
            ok_go = app[h] > 0 or abs(app[h]) <= close or share[h] >= FT.ALLIN_MIX
            ok_fold = app[h] <= 0 or abs(app[h]) <= close or share[h] <= 1 - FT.ALLIN_MIX
            if ok_go:
                worst = max(worst, -g400[h])
            if ok_fold:
                worst = max(worst, g400[h])
        rec["grade_cost"] = worst

    # exploits (the coach's): recompute the scaled ranges and gains
    try:
        if kind == "jam":
            ex = I.exploit_jam(res)
            n = 169
            eqc = [0.0] * n
            rj = I._sparse(res["jam_range"])
            for c in range(n):
                eqc[c] = I.range_eq(c, rj)
            for label_, scale in I.EXPLOIT_SCALES:
                rngs = {}
                for i in o["behind"]:
                    base = res["calls"][i][0]
                    share = base if (base >= 0.9 or st[i] < 5) else base * scale
                    rngs[i] = norm(top_share(eqc, share, priors.get(i)))
                agg2, _ = gains_by_combo(rngs, priors, o["behind"], hero_vals)
                d = max(abs(agg2[h] - ex[label_]["gain"][NAMES[h]]) for h in range(169))
                rec[f"exploit_{label_}"] = d
                if d > 1e-6 * pool + 1e-3:
                    rec["fail"].append({"exploit": (label_, d)})
        else:
            ex = I.exploit_call(cf)
            score = [res["gain"][NAMES[j]] for j in range(169)]
            for label_, scale in I.EXPLOIT_SCALES:
                jw = norm(top_share(score, cf["jam_share"] * scale))
                agg2, _ = call_gains_by_combo(jw, sp.lose[me][me], sp.win[me][me], sp.v_steal[me],
                                              vals(o["chop"][me])[me])
                d = max(abs(agg2[h] - ex[label_]["gain"][NAMES[h]]) for h in range(169))
                rec[f"exploit_{label_}"] = d
                if d > 1e-6 * pool + 1e-3:
                    rec["fail"].append({"exploit": (label_, d)})
    except Exception as e:      # (recorded, not fatal)
        rec["fail"].append({"exploit_error": repr(e)})
    if rec["fail"] or rec.get("grade_cost", 0) > 0.0003 * pool:   # (to rebuild the spot)
        rec["spot"] = {"stacks": list(st), "me": me, "who": who, "kind": kind, "pays": list(pays),
                       "kw": {k: (list(v) if isinstance(v, tuple) else
                                  {str(a): list(b) for a, b in v.items()} if isinstance(v, dict)
                                  else v) for k, v in kw.items()}}
    rec["secs"] = round(time.time() - t0, 1)
    out.write(json.dumps(rec) + "\n")
    out.flush()
    return rec


def top_share(score, share, prior=None):
    """The best `share` of all combos by score (within the prior's hands), independently:
    whole classes in score order, the last one partly."""
    w = list(prior) if prior else [1.0] * 169
    total = sum(len(COMBOS[j]) * w[j] for j in range(169))
    want = max(0.0, min(1.0, share)) * total
    out, got = [0.0] * 169, 0.0
    for j in sorted(range(169), key=lambda j: -score[j]):
        c = len(COMBOS[j]) * w[j]
        if c <= 0 or got >= want - 1e-9:
            continue
        take = min(1.0, (want - got) / c)
        out[j] = take * w[j]
        got += c * take
    return out


# ------------------------------------------------------------------ spots

def random_spot(rng):
    n = rng.randint(2, 8)
    stacks = [round(rng.choice([rng.uniform(1, 8), rng.uniform(4, 25), rng.uniform(10, 60)]), 2)
              for _ in range(n)]
    pool = rng.choice([500_000, 1_000_000, 1_500_000, rng.randint(5, 500) * 10_000])
    pays = I.pool_payouts(pool)[:n]
    if rng.random() < 0.55 or n == 2:
        me = rng.randrange(n - 1)
        return {"kind": "jam", "stacks": stacks, "me": me, "jammer": None, "kw": {}}, pays
    j = rng.randrange(n - 1)
    me = rng.randrange(j + 1, n)
    return {"kind": "call", "stacks": stacks, "me": me, "jammer": j, "kw": {}}, pays


class Harvest:
    """Plays final tables with the trainer's bots and collects the spots ICM decides, the
    way each player's decision is read, and what the bot then did."""

    def __init__(self, rng):
        import trainer8 as T
        self.T = T
        T.load_study_table()
        FT.POLICY.update(on=True, icm=True)
        self.rng = rng
        self.queue = []
        self.reads = []

    def more(self):
        from engine import new_deck
        from engine8 import Game8
        T, rng = self.T, self.rng
        t = rng.randint(1, I.N_TABLES)
        pays9 = I.pool_payouts(rng.choice([500_000, 1_000_000, 1_500_000,
                                           rng.randint(5, 300) * 10_000]))
        ft = FT.FinalTable(t, pays9, BB)
        ft.level_hands = rng.choice([15, 20, 30])
        FT.POLICY["table"] = ft
        harvest = self

        class Seat:
            def __init__(self, bot):
                self.bot = bot

            def new_hand(self, *a):
                self.bot.new_hand(*a)

            def hand_over(self, r):
                self.bot.hand_over(r)

            def act(self, s):
                sp = FT.icm_spot(s) if s.street == "preflop" else None
                mv = self.bot.act(s)
                if sp is not None and sp.get("game"):
                    sp = None    # (the SB vs BB game: its own solve, checked separately)
                if sp is not None:
                    harvest.reads.append((s, sp, mv, list(ft.pays(len(sp["stacks"])))))
                return mv

        bots = [Seat(T.make_study(T.STUDY, rng.randrange(10 ** 6))) for _ in range(8)]
        for h in range(rng.randint(20, 120)):
            if len(ft.alive()) < 2:
                break
            seats, b, stacks = ft.start_hand()
            g = Game8([bots[x] for x in seats], big_blind=BB, ante=BB, ante_mode="bb",
                      time_limit_s=1e9)
            res = g.play_hand(new_deck(rng), button=b, hand_no=h, stacks=stacks)
            ft.finish_hand(seats, stacks, res)


def read_check(s, sp, mv, pays):
    """How the spot was read from the table, worked out again from the raw state; the
    grade; what the bot did. Returns a list of problems and a note on the bot."""
    from cards import name  # noqa: F401
    probs = []
    names = I.positions(len(s.players))
    pos_names = __import__("trainer8").position_names(len(s.players))
    by = {p.position: p for p in s.players}
    order = [by[nm] for nm in pos_names]
    n = len(order)
    st = [(p.stack + p.bet + (s.ante_total if k == n - 1 else 0)) / s.big_blind
          for k, p in enumerate(order)]
    me = next(k for k, p in enumerate(order) if p.seat == s.my_seat)
    if [round(x, 6) for x in st] != [round(x, 6) for x in sp["stacks"]]:
        probs.append(("stacks", st, sp["stacks"]))
    if me != sp["me"]:
        probs.append(("me", me, sp["me"]))
    acts = [(seat, a, amt) for street, seat, a, amt in s.history
            if street == "preflop" and a != "fold"]
    idx = {p.seat: k for k, p in enumerate(order)}
    eff = min(s.my_stack + s.my_bet, max(p.stack + p.bet for p in s.players
                                        if p.seat != s.my_seat and not p.folded)) / s.big_blind
    if not acts:
        want = "jam"
        if sp["kind"] != "jam" or sp["kw"]:
            probs.append(("kind", sp["kind"], "first-in jam"))
    elif len(acts) == 1:
        j = idx[acts[0][0]]
        jp = order[j]
        if jp.all_in or jp.stack == 0:
            want = "call"
            if sp["kind"] != "call" or sp["jammer"] != j:
                probs.append(("call read", sp["kind"], sp["jammer"], j))
        else:
            want = "rejam"
            later = [k for k in range(me + 1, n) if not order[k].folded and k != me]
            if sp["kind"] != "jam" or tuple(sp["kw"].get("responders", ())) != tuple([j] + later):
                probs.append(("re-jam responders", sp["kw"].get("responders"), [j] + later))
            if sp["kw"].get("fold_to") != j:
                probs.append(("re-jam fold_to", sp["kw"].get("fold_to"), j))
            # what's in: the blinds, and the open
            want_posts = [0.0] * n
            want_posts[n - 2] = min(s.big_blind // 2, order[n - 2].stack + order[n - 2].bet) / s.big_blind
            want_posts[n - 1] = min(s.big_blind, order[n - 1].stack + order[n - 1].bet) / s.big_blind
            want_posts[j] = acts[0][2] / s.big_blind
            if [round(x, 6) for x in want_posts] != [round(x, 6) for x in sp["kw"]["posts"]]:
                probs.append(("posts", sp["kw"]["posts"], want_posts))
    else:
        want = "call over my open"
    # the grade: right iff (gain > 0) == going, or it's a coin flip (FT.allin_close)
    spot = FT.coach_spot(s, FT.POLICY["table"])
    if spot["kind"] in ("jam", "call"):
        g_go = FT.grade(dict(spot), ("raise", s.max_raise_to) if spot["kind"] == "jam"
                        else ("call", 0), s)
        g_fold = FT.grade(dict(spot), ("fold", 0), s)
        if g_go and g_fold:
            solved = FT.ensure_solved(dict(spot))
            both = g_go[0] and FT.allin_close(solved, "go") and FT.allin_close(solved, "fold")
            if g_go[0] == g_fold[0] and not both:     # (a coin flip: both are right)
                probs.append(("grade: going and folding graded the same", g_go, g_fold))
            if abs(g_go[1] - g_fold[1]) > 1e-9:
                probs.append(("grade: dollars differ", g_go[1], g_fold[1]))
    # the bot: did it do what the ICM answer says?
    note = None
    try:
        hand = I.hand_class(*s.hole)
        if sp["kind"] == "call":
            res = FT.solve(sp["stacks"], pays, sp["jammer"], **sp["kw"])
            go = I.call_from(res, sp["me"])["gain"][hand] > 0
            did = mv[0] == "call" or (mv[0] == "raise")       # (calling a jam: call or more)
            if go != did:
                price = s.to_call / (s.pot + s.to_call) if s.to_call else 0
                note = ("bot vs ICM (call)", hand, go, mv[0],
                        "price guard" if price <= FT.PRICE_GUARD else
                        "premium" if hand in FT.PREMIUMS else "other")
        else:
            res = FT.solve(sp["stacks"], pays, sp["me"], **sp["kw"])
            jam = res["jam_range"][I.exact()[1][hand]] >= 0.5
            # all in: a jam, or a call / complete that puts the whole stack in
            did = (mv[0] == "raise" and mv[1] >= s.max_raise_to - 1) or \
                (mv[0] == "call" and s.to_call and s.my_stack <= s.to_call)
            if jam and not did and (sp["kw"] or sp["eff"] < FT.SHORT_BB):
                note = ("bot vs ICM (jam)", hand, jam, list(mv), "not jammed")
            elif did and not jam:
                note = ("bot vs ICM (jam)", hand, jam, list(mv), "jammed a non-jam")
    except Exception as e:
        note = ("bot check error", repr(e))
    return probs, note, want


# ------------------------------------------------------------------ the equity table

def equity_table_part(rng, worker, workers, out, samples=3000, budget_s=1800):
    """Every worker checks its share of the 169 x 169 table against simulated showdowns
    (in random order, until the budget runs out), and the card-removal weights exactly."""
    W = I.exact()[3]
    t0 = time.time()
    pairs = [(i, j) for i in range(169) for j in range(169) if (i * 169 + j) % workers == worker % workers]
    rng.shuffle(pairs)
    done = bad = 0
    worst = 0.0
    for i, j in pairs:
        # card removal: combos of j that fit with each combo of i, averaged
        fit = statistics.fmean(sum(1 for (a, b) in COMBOS[j] if a not in c and b not in c)
                               for c in COMBOS[i])
        if abs(fit - W[i][j]) > 1e-9:
            out.write(json.dumps({"src": "W", "i": NAMES[i], "j": NAMES[j], "mine": fit,
                                  "app": W[i][j], "fail": ["W"]}) + "\n")
            bad += 1
        if time.time() - t0 > budget_s:
            continue
        pairs_ok = [(x, y) for x in COMBOS[i] for y in COMBOS[j] if not set(x) & set(y)]
        if not pairs_ok:
            continue
        won = 0.0
        for _ in range(samples):
            x, y = rng.choice(pairs_ok)
            board = rng.sample([c for c in range(52) if c not in x and c not in y], 5)
            a, b = evaluate(list(x) + board), evaluate(list(y) + board)
            won += 1.0 if a > b else 0.5 if a == b else 0.0
        eq = won / samples
        se = math.sqrt(max(eq * (1 - eq), 0.01) / samples)
        d = eq - E[i][j]
        worst = max(worst, abs(d) / se)
        done += 1
        if abs(d) > 5 * se:
            bad += 1
            out.write(json.dumps({"src": "E", "i": NAMES[i], "j": NAMES[j], "sim": eq,
                                  "app": E[i][j], "se": se, "fail": ["E"]}) + "\n")
    sym = max(abs(E[i][j] + E[j][i] - 1) for i in range(169) for j in range(169))
    out.write(json.dumps({"src": "E-summary", "pairs_simulated": done, "pairs_W": len(pairs),
                          "worst_z": worst, "symmetry": sym, "bad": bad,
                          "fail": ["E-sym"] if sym > 1e-9 else []}) + "\n")
    out.flush()


# ------------------------------------------------------------------ main loop

def run(args):
    rng = random.Random(1000 + args.worker)
    out = open(args.out, "a")
    deadline = time.time() + args.hours * 3600
    equity_table_part(rng, args.worker, args.workers, out, budget_s=args.table_budget)
    harv = Harvest(rng)
    k = 0
    while time.time() < deadline:
        k += 1
        try:
            if k % 2:
                spot, pays = random_spot(rng)
                audit_spot(rng, spot, pays, out, args.deals, k % 6 == 1, "random")
            else:
                if not harv.queue:
                    harv.reads.clear()
                    harv.more()
                    for s, sp, mv, pays in harv.reads:
                        probs, note, want = read_check(s, sp, mv, pays)
                        out.write(json.dumps({"src": "read", "want": want,
                                              "fail": [str(p) for p in probs],
                                              "bot": note}) + "\n")
                        harv.queue.append((sp, pays))
                    rng.shuffle(harv.queue)
                    del harv.queue[12:]          # (a sample of each table's spots)
                if harv.queue:
                    sp, pays = harv.queue.pop()
                    audit_spot(rng, {"kind": sp["kind"], "stacks": sp["stacks"], "me": sp["me"],
                                     "jammer": sp["jammer"], "kw": sp["kw"]}, pays, out,
                               args.deals, k % 6 == 2, "table")
        except Exception:
            import traceback
            out.write(json.dumps({"src": "error", "fail": [traceback.format_exc()[-1500:]]}) + "\n")
            out.flush()
    out.close()


def summary(paths):
    recs = []
    for p in paths:
        for line in open(p):
            try:
                recs.append(json.loads(line))
            except ValueError:
                pass
    spots = [r for r in recs if r.get("src") in ("random", "table")]
    reads = [r for r in recs if r.get("src") == "read"]
    print(f"spots audited: {len(spots)} ({sum(r['src'] == 'table' for r in spots)} from played "
          f"tables, {sum(r['kind'] == 'call' for r in spots)} calls, "
          f"{sum(r.get('re', False) for r in spots)} re-jams over an open)")
    sims = [s for r in spots for s in r.get("sim", [])]
    print(f"simulated hands: {len(sims)} (thousands of deals each)")
    fails = [r for r in recs if r.get("fail")]
    kinds = {}
    for r in fails:
        for f in r["fail"]:
            key = list(f.keys())[0] if isinstance(f, dict) else str(f)[:40]
            kinds.setdefault(key, []).append(r)
    print("problems:", {k: len(v) for k, v in kinds.items()} or "none")
    for k, v in kinds.items():
        for r in v[:3]:
            print("  -", k, json.dumps({x: r[x] for x in r if x not in ("sim",)})[:600])
    md = [r["model_diff"] for r in spots if "model_diff" in r]
    if md:
        print(f"model recompute: worst difference ${max(md):.6f}")
    cf = [r["combo_flips"] for r in spots if "combo_flips" in r]
    if cf:
        print(f"combo-averaging shortcut: hands flipped (share of combos) mean "
              f"{100 * statistics.fmean(cf):.3f}%, worst {100 * max(cf):.2f}%; worst value "
              f"of a flipped hand ${max(r['combo_flip_cost'] for r in spots):,.0f}; as % of "
              f"the pool {100 * max(r['combo_flip_cost'] / r['pool'] for r in spots):.3f}%")
    conv = [r for r in spots if "conv_flips" in r]
    if conv:
        print(f"trainer's solve vs a long one ({len(conv)} spots): hands that change mean "
              f"{100 * statistics.fmean(r['conv_flips'] for r in conv):.3f}% of combos, worst "
              f"{100 * max(r['conv_flips'] for r in conv):.2f}%; worst value of a changed hand "
              f"${max(r['conv_flip_cost'] for r in conv):,.0f} "
              f"({100 * max(r['conv_flip_cost'] / r['pool'] for r in conv):.3f}% of the pool)")
        gc = [r for r in conv if "grade_cost" in r]
        if gc:
            bad = [r for r in gc if r["grade_cost"] > 0.0003 * r["pool"]]
            print(f"  the grade by the long solve: worst loss of an answer graded right "
                  f"{100 * max(r['grade_cost'] / r['pool'] for r in gc):.4f}% of the pool; "
                  f"over 0.03%: {len(bad)} of {len(gc)}")
    if sims:
        z = [(m - a) / se for h, a, m, se in sims if se > 0]
        print(f"simulation vs trainer: mean z {statistics.fmean(z):+.3f}, sd {statistics.pstdev(z):.3f} "
              f"(1.0 = perfect agreement), |z|>4: {sum(abs(x) > 4 for x in z)} of {len(z)}")
        flips = [(h, a, m, se) for h, a, m, se in sims if se > 0 and (m > 0) != (a > 0)
                 and abs(m) > 4 * se]
        print(f"  confident flips (simulation sure the decision is the other way): {len(flips)}")
        for f in flips[:5]:
            print("   ", f)
    if reads:
        bots = [r["bot"] for r in reads if r.get("bot")]
        from collections import Counter
        print(f"table reads: {len(reads)}, read problems: {sum(bool(r['fail']) for r in reads)}; "
              f"bot differs from the ICM answer: {len(bots)} "
              f"({dict(Counter((b[0], b[-1]) for b in bots))})")
    for r in recs:
        if r.get("src") == "E-summary":
            print("equity table:", {k: r[k] for k in r if k != "src"})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hours", type=float, default=7)
    ap.add_argument("--worker", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--deals", type=int, default=4000)
    ap.add_argument("--table-budget", type=float, default=2400)
    ap.add_argument("--out", default="icm_overnight.jsonl")
    ap.add_argument("--summary", nargs="*")
    args = ap.parse_args()
    if args.summary:
        summary([p for pat in args.summary for p in glob.glob(pat)])
    else:
        run(args)


if __name__ == "__main__":
    main()
