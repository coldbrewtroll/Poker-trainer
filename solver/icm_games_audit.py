"""ICM audit of the two short-stack games every final-table decision inside them is graded
against (icm_study.solve_sb: small blind vs big blind; icm_study.solve_open: a short player
first in), in dollars.

    python3 solver/icm_games_audit.py --hours 4 --worker 1 --out games_1.jsonl
    python3 solver/icm_games_audit.py --summary games_*.jsonl

Random final tables (2-8 players, any stacks the games apply to, any prize pool). Each game
is solved as the trainer solves it, and for every decision of it:

  regret   for every hand, what its mix gives up against its best action (dollars): the
           solve's distance from an equilibrium, decision by decision.
  grade    hands where the grader would call a losing action right (an action played 10%+
           that is worth less than the best by more than the grader's tolerance), and the
           dollars at stake.
  longer   the same game solved 4x longer: hands whose best answer changes, and what
           playing the old answer costs (dollars, with the longer solve's values).
  unreached decisions no hand gets to in the solve (every action worth the same): if a
           player still gets there, the grade can't tell right from wrong.
  sim      every all-in branch played out card by card: the hands dealt (card removal),
           the others answering with the solve's own strategies, the board run out, the
           chips settled from the betting (blinds, the big blind's dead ante, raises in),
           the stacks valued by brute-force ICM (every finishing order). The mean over
           thousands of deals must agree with the solve's dollar value (z-score)."""

import argparse
import glob
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solver import icm_overnight as O  # noqa: E402  (tk stub, card helpers, brute ICM)

I, FT = O.I, O.FT
NAMES, IDX, COMBOS = O.NAMES, O.IDX, O.COMBOS
evaluate, class_of = O.evaluate, O.class_of
NCOMBO = {h: len(COMBOS[IDX[h]]) for h in NAMES}
LIVE = 0.01      # a decision the others' play leads to at least this often counts as live


def weights_of(node, action, prior=None):
    """Class weights (0-1, by NAMES index) of the hands that take `action` at `node`."""
    w = [0.0] * len(NAMES)
    for h, m in node["mix"].items():
        w[IDX[h]] = m.get(action, 0.0) * (prior[IDX[h]] if prior else 1.0)
    return w


def deal(rng, dead, w):
    got = O.sample_hand(rng, dead, w)
    return got[0] if got else None


def showdown(rng, mine, theirs):
    dead = set(mine) | set(theirs)
    board = O.runout(rng, dead)
    a, b = evaluate(list(mine) + board), evaluate(list(theirs) + board)
    return "win" if a > b else "lose" if a < b else "chop"


def sim_jam(rng, h, deals, outs, hero, resp, call_w, priors, vals):
    """Hero jams class h; responders in order (hands from their priors) call with call_w,
    the first caller plays it out. Mean and standard error of the hero's dollars."""
    xs = []
    for _ in range(deals):
        mine = rng.choice(COMBOS[IDX[h]])
        dead = set(mine)
        value = vals(outs["steal"])[hero]
        for i in resp:
            got = O.sample_hand(rng, dead, priors.get(i) or [1.0] * len(NAMES))
            if got is None:
                continue
            (c1, c2), k = got
            dead |= {c1, c2}
            if rng.random() < call_w[i][k]:
                value = vals(outs[showdown(rng, mine, (c1, c2))][i])[hero]
                break
        xs.append(value)
    return statistics.fmean(xs), statistics.pstdev(xs) / math.sqrt(len(xs))


def sim_call(rng, h, deals, outs, jammer, me, jam_w, vals):
    """Hero (responder `me`) calls jammer's all-in with class h, the jammer holding a hand
    from jam_w. Mean and standard error of the hero's dollars."""
    xs = []
    for _ in range(deals):
        mine = rng.choice(COMBOS[IDX[h]])
        theirs = deal(rng, set(mine), jam_w)
        if theirs is None:
            return None
        r = showdown(rng, theirs, mine)          # (the jammer is the outcomes' hero)
        xs.append(vals(outs[r][me])[me])
    return statistics.fmean(xs), statistics.pstdev(xs) / math.sqrt(len(xs))


def share(nd, act, prior=None):
    """The share of a range (prior: per-hand weights by name) that takes `act` at nd."""
    num = den = 0.0
    for h, m in nd["mix"].items():
        w = NCOMBO[h] * (prior.get(h, 0.0) if prior is not None else 1.0)
        num += w * m.get(act, 0.0)
        den += w
    return num / den if den else 0.0


def reaches(g):
    """{decision: (how often the others' play leads there, {hand: how often the player's
    own earlier play does})}."""
    N, out = g["nodes"], {}
    mix = lambda node, a: {h: m.get(a, 0.0) for h, m in N[node]["mix"].items()}  # noqa: E731
    if g["game"] == "sb":
        limp, rais = mix("sb", "limp"), mix("sb", "raise")
        iso = mix("bb_vs_limp", "iso") if N["bb_vs_limp"]["mix"] else {}
        three = mix("bb_vs_raise", "3bet") if N["bb_vs_raise"]["mix"] else {}
        out["sb"] = (1.0, None)
        out["bb_vs_limp"] = (share(N["sb"], "limp"), None)
        out["bb_vs_raise"] = (share(N["sb"], "raise"), None)
        out["bb_vs_jam"] = (share(N["sb"], "jam"), None)
        out["sb_vs_iso"] = (share(N["bb_vs_limp"], "iso"), limp)
        out["sb_vs_jam_limp"] = (share(N["bb_vs_limp"], "jam"), limp)
        out["sb_vs_3bet"] = (share(N["bb_vs_raise"], "3bet") if N["bb_vs_raise"]["mix"] else 0.0, rais)
        out["sb_vs_jam_raise"] = (share(N["bb_vs_raise"], "jam"), rais)
        out["bb_vs_rejam_iso"] = (share(N["sb_vs_iso"], "jam", limp) if N["sb_vs_iso"]["mix"] else 0.0, iso)
        out["bb_vs_4bet"] = (share(N["sb_vs_3bet"], "jam", rais) if N["sb_vs_3bet"]["mix"] else 0.0, three)
    else:
        opens = mix("open", "open")
        out["open"] = (1.0, None)
        b = g["resp"][-1]
        three = N[f"vs_open:{b}"]
        out["open_vs_3bet"] = (share(three, "3bet") if three["mix"] else 0.0, opens)
        for i in g["resp"]:
            out[f"vs_open:{i}"] = (share(N["open"], "open"), None)
            out[f"vs_jam:{i}"] = (share(N["open"], "jam"), None)
            out[f"open_vs_rejam:{i}"] = (share(N[f"vs_open:{i}"], "jam"), opens)
            mine = mix(f"vs_open:{i}", "3bet") if N[f"vs_open:{i}"]["mix"] else {}
            out[f"vs_4bet:{i}"] = (share(N["open_vs_3bet"], "jam", opens) if N["open_vs_3bet"]["mix"]
                                   else 0.0, mine)
        out["open_vs_rejam"] = (max(out[f"open_vs_rejam:{i}"][0] for i in g["resp"]), opens)
    return out


def node_checks(g, g4, pool, tol):
    """regret / grade / longer / unreached, per decision of game g (g4: solved longer)."""
    out = {}
    R = reaches(g)
    for name, nd in g["nodes"].items():
        if not nd["mix"]:
            continue
        opp, own = R.get(name, (1.0, None))
        evs = [e for e in nd["ev"].values() if e]
        if evs and all(max(e.values()) - min(e.values()) < 1e-6 * pool for e in evs):
            out[name] = {"unreached": True}
            continue
        reg = w = 0.0
        worst = (0.0, None)
        bad_grade, changed = [], []
        for h, m in nd["mix"].items():
            ev = nd["ev"].get(h)
            if not ev:
                continue
            top = max(ev.values())
            r = top - sum(f * ev.get(a, top) for a, f in m.items())
            ow = own.get(h, 0.0) if own is not None else 1.0
            reg += NCOMBO[h] * ow * r
            w += NCOMBO[h] * ow
            if r > worst[0]:
                worst = (r, h)
            t = FT.PREMIUM_MARGIN * pool if FT.premium(h) else tol
            for a, f in m.items():
                if f >= 0.10 and a in ev and ev[a] < top - t:
                    bad_grade.append((h, a, round(f, 3), round(top - ev[a], 2), round(ow, 3)))
            # the grader's best answer (of those within tol of the top, the one played most)
            best = max((a for a in ev if ev[a] >= top - t), key=lambda a: m.get(a, 0.0))
            n4 = g4["nodes"].get(name) if g4 else None
            ev4, m4 = (n4["ev"].get(h), n4["mix"].get(h)) if n4 else (None, None)
            if ev4 and m4:
                top4 = max(ev4.values())
                best4 = max((a for a in ev4 if ev4[a] >= top4 - t), key=lambda a: m4.get(a, 0.0))
                cost = top4 - ev4.get(best, top4)
                if best4 != best and cost > t:
                    changed.append((h, best, best4, round(cost, 2), round(ow, 3)))
        out[name] = {"regret": reg / w if w else 0.0, "worst": [round(worst[0], 2), worst[1]],
                     "bad_grade": bad_grade, "changed": changed, "reach": round(opp, 5)}
    return out


def blinds_posts(st, extra):
    """Chips in before an all-in (blinds, and `extra`: {seat: bb in}), for O.outcomes."""
    n = len(st)
    p = [0.0] * n
    p[n - 2], p[n - 1] = 0.5, 1.0
    for k, v in extra.items():
        p[k] = v
    return p


def sims_sb(rng, g, st, vals, deals, picks):
    """The SB game's all-in branches."""
    n = len(st)
    s, b = n - 2, n - 1
    N = g["nodes"]
    full = [1.0] * len(NAMES)
    res = []
    jam_sb = weights_of(N["sb"], "jam")
    limp = weights_of(N["sb"], "limp")
    rais = weights_of(N["sb"], "raise")
    checks = [
        # (node, action, kind, hero, other, posts extra, call/jam weights, prior of other)
        ("sb", "jam", "jam", s, b, {}, weights_of(N["bb_vs_jam"], "call"), full),
        ("bb_vs_jam", "call", "call", b, s, {}, jam_sb, None),
        ("bb_vs_limp", "jam", "jam", b, s, {s: 1.0}, weights_of(N["sb_vs_jam_limp"], "call"), limp),
        ("sb_vs_jam_limp", "call", "call", s, b, {s: 1.0},
         weights_of(N["bb_vs_limp"], "jam"), None),
        ("bb_vs_raise", "jam", "jam", b, s, {s: I.SB_RAISE_TO},
         weights_of(N["sb_vs_jam_raise"], "call"), rais),
        ("sb_vs_jam_raise", "call", "call", s, b, {s: I.SB_RAISE_TO},
         weights_of(N["bb_vs_raise"], "jam"), None),
    ]
    if N.get("sb_vs_iso", {}).get("mix") and N.get("bb_vs_rejam_iso", {}).get("mix"):
        iso_r = weights_of(N["bb_vs_limp"], "iso")
        checks += [
            ("sb_vs_iso", "jam", "jam", s, b, {s: 1.0, b: I.SB_ISO_TO},
             weights_of(N["bb_vs_rejam_iso"], "call"), iso_r),
            ("bb_vs_rejam_iso", "call", "call", b, s, {s: 1.0, b: I.SB_ISO_TO},
             weights_of(N["sb_vs_iso"], "jam", limp), None),
        ]
    for node, act, kind, hero, other, extra, w, prior in checks:
        nd = N.get(node)
        if not nd or not nd["mix"] or (kind == "call" and not any(w)):
            continue
        if kind == "jam" and prior is not None and not any(prior):
            continue
        res += _sim_node(rng, st, nd, node, act, kind, hero, other, extra, w, prior, vals,
                         deals, picks)
    return res


def sims_open(rng, g, st, vals, deals, picks):
    """The open game's all-in branches."""
    o = g["opener"]
    N = g["nodes"]
    full = [1.0] * len(NAMES)
    res = []
    opens = weights_of(N["open"], "open")
    jams = weights_of(N["open"], "jam")
    # the opener's jam: everyone behind answers in turn
    resp = g["resp"]
    call_w = {i: weights_of(N[f"vs_jam:{i}"], "call") for i in resp}
    outs = O.outcomes(st, o, blinds_posts(st, {}), resp, None)
    for h in pick_hands(rng, N["open"], "jam", picks):
        m, se = sim_jam(rng, h, deals, outs, o, resp, call_w, {i: full for i in resp}, vals)
        res.append(rec("open", "jam", h, N["open"]["ev"][h]["jam"], m, se))
    for i in resp:
        # the responder calling the opener's jam (those before folded; after a call the rest fold)
        nd = N[f"vs_jam:{i}"]
        outs_i = O.outcomes(st, o, blinds_posts(st, {}), [i], None)
        for h in pick_hands(rng, nd, "call", picks // 2 or 1):
            r = sim_call(rng, h, deals, outs_i, o, i, jams, vals)
            if r:
                res.append(rec(f"vs_jam:{i}", "call", h, nd["ev"][h]["call"], *r))
        # the responder re-jamming over the open; the opener answers (the rest fold)
        nd = N[f"vs_open:{i}"]
        # (the opener answers this player's re-jam with its own range against this player)
        ans = N.get(f"open_vs_rejam:{i}") or N["open_vs_rejam"]
        cw = {o: weights_of(ans, "call")}
        outs_r = O.outcomes(st, i, blinds_posts(st, {o: I.OPEN_TO}), [o], None)
        for h in pick_hands(rng, nd, "jam", picks // 2 or 1):
            m, se = sim_jam(rng, h, deals, outs_r, i, [o], cw, {o: opens}, vals)
            res.append(rec(f"vs_open:{i}", "jam", h, nd["ev"][h]["jam"], m, se))
    # the opener calling each responder's re-jam (the value once facing that player's jam)
    for i in resp:
        nd = N.get(f"open_vs_rejam:{i}")
        rj = weights_of(N[f"vs_open:{i}"], "jam")
        if not nd or not any(rj):
            continue
        outs_c = O.outcomes(st, i, blinds_posts(st, {o: I.OPEN_TO}), [o], None)
        for h in pick_hands(rng, nd, "call", picks // 2 or 1):
            r = sim_call(rng, h, deals, outs_c, i, o, rj, vals)
            if r:
                res.append(rec(f"open_vs_rejam:{i}", "call", h, nd["ev"][h]["call"], *r))
    return res


def _sim_node(rng, st, nd, node, act, kind, hero, other, extra, w, prior, vals, deals, picks):
    res = []
    outs_hero = O.outcomes(st, hero if kind == "jam" else other, blinds_posts(st, extra),
                           [other if kind == "jam" else hero], None)
    for h in pick_hands(rng, nd, act, picks):
        if kind == "jam":
            r = sim_jam(rng, h, deals, outs_hero, hero, [other], {other: w}, {other: prior}, vals)
        else:
            r = sim_call(rng, h, deals, outs_hero, other, hero, w, vals)
        if r:
            res.append(rec(node, act, h, nd["ev"][h][act], *r))
    return res


def pick_hands(rng, nd, act, k):
    """Up to k hands that reach this decision and have a value for `act` (the ones that
    take it first, so the check covers what's actually played)."""
    have = [h for h, e in nd["ev"].items() if e and act in e]
    taking = [h for h in have if nd["mix"].get(h, {}).get(act, 0) > 0.05]
    rng.shuffle(taking)
    rest = [h for h in have if h not in taking]
    rng.shuffle(rest)
    return (taking + rest)[:k]


def rec(node, act, h, model, m, se):
    return {"node": node, "act": act, "hand": h, "model": round(model, 2), "sim": round(m, 2),
            "se": round(se, 2), "z": round((m - model) / se, 2) if se > 0 else 0.0}


def random_spot(rng):
    """(game, stacks bb, opener, payouts): stacks where the trainer plays that game."""
    pool = rng.choice([500_000, 1_000_000, 1_500_000, rng.randrange(100_000, 5_000_000)])
    while True:
        n = rng.randint(2, 8)
        st = [round(rng.uniform(2.5, 45), 2) for _ in range(n)]
        if rng.random() < 0.5 or n < 4:
            ante = min(I.ANTE, st[-1])
            eff = min(st[-2], st[-1] - ante)
            if eff < FT.GAME_SB_BB and st[-2] >= 2.0 and st[-1] - ante >= 2.0:
                return "sb", st, None, I.pool_payouts(pool)[:n]
        else:
            o = rng.randrange(0, n - 2)
            st[o] = round(rng.uniform(I.OPEN_MIN_BB, FT.GAME_OPEN_BB), 2)
            eff = min(st[o], max(x for k, x in enumerate(st) if k != o))
            if eff < FT.GAME_OPEN_BB:
                return "open", st, o, I.pool_payouts(pool)[:n]


def solve(game, st, o, pays, mult=1):
    v = I.icm_value(list(pays))
    if game == "sb":
        return I.solve_sb(list(st), v, iters=I.GAME_ITERS * mult)
    return I.solve_open(list(st), v, o, iters=I.OPEN_ITERS * mult)


def run(args):
    rng = random.Random(7000 + args.worker)
    out = open(args.out, "a")
    deadline = time.time() + args.hours * 3600
    k = 0
    while time.time() < deadline:
        k += 1
        try:
            game, st, o, pays = random_spot(rng)
            pool = sum(pays)
            t0 = time.time()
            g = solve(game, st, o, pays)
            if g is None:
                continue
            g4 = solve(game, st, o, pays, 4) if k % 3 == 1 else None
            tol = 1e-4 * pool
            r = {"game": game, "stacks": st, "opener": o, "pool": pool, "n": len(st),
                 "nodes": node_checks(g, g4, pool, tol), "longer": g4 is not None}
            vals = O.Values(pays)
            sims = (sims_sb if game == "sb" else sims_open)(rng, g, st, vals, args.deals,
                                                             args.picks)
            # (only decisions the others' play actually leads to: elsewhere the solve's
            # values are against an average range nobody plays to)
            # (and only hands that play their way there themselves: after a limp the solve
            # never makes, say, the values are a what-if)
            R = reaches(g)
            r["sims"] = [x for x in sims if R.get(x["node"], (1.0, None))[0] >= LIVE
                         and (R.get(x["node"], (1.0, None))[1] or {}).get(x["hand"], 1.0) >= LIVE]
            r["secs"] = round(time.time() - t0, 1)
            out.write(json.dumps(r) + "\n")
            out.flush()
        except Exception:
            import traceback
            out.write(json.dumps({"error": traceback.format_exc()[-1500:]}) + "\n")
            out.flush()
    out.close()


def summary(paths):
    recs, errs = [], []
    for p in paths:
        for line in open(p):
            d = json.loads(line)
            (errs if "error" in d else recs).append(d)
    print(f"spots: {len(recs)} ({sum(r['game'] == 'sb' for r in recs)} small blind vs big "
          f"blind, {sum(r['game'] == 'open' for r in recs)} short first in); errors: {len(errs)}")
    for e in errs[:3]:
        print("  ERROR", e["error"][-600:])
    by = {}
    for r in recs:
        P = r["pool"]
        for name, c in r["nodes"].items():
            key = (r["game"], name.split(":")[0] + (":i" if ":" in name else ""))
            d = by.setdefault(key, {"n": 0, "unreached": 0, "rare": 0, "reg": [], "cost": [],
                                    "worst": (0, None, None), "bad": [], "chg": [], "chg_n": 0})
            d["n"] += 1
            if c.get("unreached"):
                d["unreached"] += 1
                continue
            if c.get("reach", 1.0) < LIVE:
                d["rare"] += 1
                continue
            d["reg"].append(c["regret"] / P)
            d["cost"].append(c["regret"] * c.get("reach", 1.0) / P)
            if c["worst"][0] / P > d["worst"][0]:
                d["worst"] = (c["worst"][0] / P, c["worst"][1], r["stacks"])
            d["bad"] += [(b[3] / P, b, r["stacks"], c.get("reach")) for b in c["bad_grade"]]
            if r["longer"]:
                d["chg_n"] += 1
                d["chg"] += [(x[3] / P, x, r["stacks"], c.get("reach")) for x in c["changed"]]
    print("\nDECISIONS (dollars as a share of the prize pool: 0.01% = $100 of $1M). Live decisions"
          f" only (reached {100 * LIVE:.0f}%+ of the time); 'rare' ones are counted apart.")
    print(f"  {'game / decision':24s} {'live':>5s} {'rare':>5s} {'unr.':>5s} {'regret/hand':>11s} "
          f"{'per game':>9s} {'worst hand':>10s} | {'grade passes a loser':>21s} | {'4x solve flips':>22s}")
    for (game, node), d in sorted(by.items()):
        live = d["n"] - d["unreached"] - d["rare"]
        reg = statistics.fmean(d["reg"]) if d["reg"] else 0.0
        cost = statistics.fmean(d["cost"]) if d["cost"] else 0.0
        badw = max([x[0] for x in d["bad"]], default=0)
        chgw = max([x[0] for x in d["chg"]], default=0)
        print(f"  {game + ' / ' + node:24s} {live:5d} {d['rare']:5d} {d['unreached']:5d} "
              f"{100 * reg:10.4f}% {100 * cost:8.4f}% {100 * d['worst'][0]:9.4f}% | "
              f"{len(d['bad']):6d} (worst {100 * badw:.3f}%) | {len(d['chg']):5d} in {d['chg_n']:4d} "
              f"(worst {100 * chgw:.3f}%)")
    print("  (regret/hand: what a hand's mix gives up against its best action, averaged over the"
          " hands that get there; per game: that times how often the decision comes up)")
    for (game, node), d in sorted(by.items()):
        for x in sorted(d["bad"], key=lambda x: -x[0])[:2]:
            print(f"    grade {game}/{node}: hand/action/freq/$ short/own reach {x[1]} "
                  f"(reached {100 * x[3]:.1f}%) stacks {x[2]}")
        for x in sorted(d["chg"], key=lambda x: -x[0])[:2]:
            print(f"    4x {game}/{node}: hand/old/new/$/own reach {x[1]} (reached {100 * x[3]:.1f}%)"
                  f" stacks {x[2]}")
    sims = [s for r in recs for s in r["sims"]]
    if sims:
        zs = [s["z"] for s in sims]
        print(f"\nSIMULATED ALL-INS: {len(sims)} hand-decisions, mean z {statistics.fmean(zs):+.3f}, "
              f"sd {statistics.pstdev(zs):.3f} (mean 0 and sd 1.0: the solve's dollars agree with "
              f"the cards)")
        node_z = {}
        for s_ in sims:
            node_z.setdefault(s_["node"].split(":")[0] + " " + s_["act"], []).append(s_)
        for k, v in sorted(node_z.items()):
            z = [x["z"] for x in v]
            gap = statistics.fmean(abs(x["sim"] - x["model"]) for x in v)
            print(f"  {k:22s} {len(v):6d}  mean z {statistics.fmean(z):+.3f}  sd {statistics.pstdev(z):.3f}"
                  f"  |z|>4: {sum(abs(x) > 4 for x in z):3d}  mean |$ gap| {gap:8.0f}")
        far = sorted(sims, key=lambda s_: -abs(s_["z"]))[:6]
        for s_ in far:
            r = next(r for r in recs if s_ in r["sims"])
            print(f"    {s_}  {r['game']} stacks {r['stacks']} pool {r['pool']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hours", type=float, default=4)
    ap.add_argument("--worker", type=int, default=1)
    ap.add_argument("--deals", type=int, default=3000)
    ap.add_argument("--picks", type=int, default=4)
    ap.add_argument("--out", default="icm_games.jsonl")
    ap.add_argument("--summary", nargs="*")
    args = ap.parse_args()
    if args.summary:
        summary([p for pat in args.summary for p in glob.glob(pat)])
    else:
        run(args)


if __name__ == "__main__":
    main()
