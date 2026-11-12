"""Exact solves of the final table's preflop games (CFR+), for icm_study.solve_sb and
icm_study.solve_open.

The games are solved over their whole tree, hand class against hand class (169 x 169,
card removal and all-in equities from icm_study.exact()), with CFR+: every round each
player's regrets at every decision are updated for every hand, weighted by how often the
other side's hands get there, and the average strategy weighs each round by how often the
player's own hand gets there (and by the round: later rounds count more). Unlike fictitious
play, a line only a few hands reach (a 4-bet, a jam after a limp) converges too.

Payoffs: a showdown pays each player its equity of the two values (win / lose); a pot
checked down after a call pays the out-of-position player _real(equity, base) of it (the
same realization model as icm_study, applied hand against hand).

Needs numpy (the work is a few dozen 169 x 169 matrix products per round, for hundreds of
rounds). Without it, icm_study keeps its fictitious-play solves (available() says which)."""

import math

try:
    import numpy as np
except Exception:                      # pragma: no cover
    np = None

# Rounds: the small blind's game, and the open game (each round there: the opener's update,
# then each responder's). Checked against 3000-round solves of 16 of the audit's spots: at
# these, no decision's best play is off by more than 0.01% of the prize pool, and every
# action's value is within 0.1% of the pool (most within 0.03%). About 1 s and 2 s on a PC.
SB_ROUNDS = 1000
OPEN_ROUNDS = 1500
import sys as _sys
if _sys.platform == "emscripten":      # (the phone app: numpy there is several times slower)
    SB_ROUNDS, OPEN_ROUNDS = 500, 600
AVG_POW = 1      # (the average weighs round t as t ** AVG_POW)


def available():
    """True when the exact solves can run (numpy is installed)."""
    return np is not None


_MATS = {}


def _real(eq, base):
    eq = max(0.0, min(1.0, eq))
    return base * eq * math.sqrt(min(1.0, 2 * eq))


def mats(key):
    """Row player's matrices (rows: own hand, columns: the other's hand), card-removal
    weighted: "W" (how many of the other's combos fit with one of own), "WE" (times own
    equity), ("Wr", base) / ("Wq", base): times the share of a checked-down pot the row
    player gets when it is the one out of position (_real(eq)) / the other one is
    (1 - _real(1 - eq))."""
    m = _MATS.get(key)
    if m is None:
        import icm_study as I
        _, _, E, W = I.exact()
        W_, E_ = np.array(W, dtype=float), np.array(E, dtype=float)
        if key == "W":
            m = W_
        elif key == "WE":
            m = W_ * E_
        elif key[0] == "Wr":
            m = W_ * np.vectorize(lambda e: _real(e, key[1]))(E_)
        elif key[0] == "Wq":
            m = W_ * np.vectorize(lambda e: 1 - _real(1 - e, key[1]))(E_)
        else:
            raise KeyError(key)
        _MATS[key] = m
    return m


def combos():
    """Combos of each hand class (6 pairs, 4 suited, 12 offsuit)."""
    import icm_study as I
    return np.array([6 if len(h) == 2 else 4 if h[2] == "s" else 12 for h in I.exact()[0]],
                    dtype=float)


def scale_of(value, stacks):
    """The prize pool (or the chips) these values share out."""
    return sum(value(list(stacks), list(range(len(stacks)))).values())


def _rm(reg):
    """Regret matching on rows of regrets (actions x hands)."""
    pos = np.maximum(reg, 0.0)
    tot = pos.sum(axis=0)
    k = reg.shape[0]
    return np.where(tot > 0, pos / np.where(tot > 0, tot, 1.0), 1.0 / k)


def _norm_rows(cum):
    tot = cum.sum(axis=0)
    k = cum.shape[0]
    return np.where(tot > 0, cum / np.where(tot > 0, tot, 1.0), 1.0 / k)


# ------------------------------------------------------------------ a two-player game tree

class Node:
    """A decision: player p ("A" or "B") chooses among acts; kids are Nodes or Terms."""

    def __init__(self, p, name, acts, kids):
        self.p, self.name, self.acts, self.kids = p, name, list(acts), list(kids)


class Term:
    """An end (each is its own object: passes key their vectors by id). For each player, a
    list of (matrix key, coefficient): its payoff for own hand h against the other's hand k
    is sum(coef * M[h][k] / W[h][k])."""

    def __init__(self, a, b):
        self.pay = {"A": a, "B": b}


def fixed(va, vb):
    """Chips move without a showdown: each player gets a fixed value."""
    return Term([("W", va)], [("W", vb)])


def show(va_w, va_l, vb_w, vb_l):
    """All in: A wins with its equity. va_w / va_l: A's value when A wins / loses;
    vb_w / vb_l: B's value when A wins / loses."""
    return Term([("W", va_l), ("WE", va_w - va_l)], [("W", vb_w), ("WE", vb_l - vb_w)])


def checked(oop, base, va_w, va_l, vb_w, vb_l):
    """Called and checked down: the out-of-position player ("A" or "B") gets _real(equity,
    base) of the pot. va_w / va_l: A's value when A takes the pot / B does; vb_w / vb_l
    the same for B."""
    ka, kb = (("Wr", base), ("Wq", base)) if oop == "A" else (("Wq", base), ("Wr", base))
    return Term([("W", va_l), (ka, va_w - va_l)], [("W", vb_w), (kb, vb_l - vb_w)])


class Game:
    """CFR+ (alternating updates, regrets floored at 0, linear averaging) on a two-player
    tree. Hands: 169 classes each side, prior weight 1 (every combo)."""

    def __init__(self, root, n=169):
        self.root, self.n = root, n
        self.nodes, stack = [], [root]
        while stack:
            x = stack.pop()
            if isinstance(x, Node):
                self.nodes.append(x)
                stack.extend(x.kids)
        for x in self.nodes:
            x.reg = np.zeros((len(x.acts), n))
            x.cum = np.zeros((len(x.acts), n))
        self.rounds = 0

    def reaches(self, who, strat):
        """{id(node or end): reach of `who`'s hands} under strat {id(node): rows}."""
        out = {}

        def go(x, r):
            out[id(x)] = r
            if isinstance(x, Term):
                return
            if x.p == who:
                rows = strat[id(x)]
                for a, kid in enumerate(x.kids):
                    go(kid, r * rows[a])
            else:
                for kid in x.kids:
                    go(kid, r)
        go(self.root, np.ones(self.n))
        return out

    def values(self, who, opp_reach, strat=None, best=False, per_action=None):
        """`who`'s counterfactual values (summed over the other's hands, card-removal
        weighted) at every node against opp_reach; its own later decisions by strat, or
        with best its best answers. per_action: filled with {id(node): [vector per action]}
        at who's decisions."""
        n = self.n

        def go(x):
            if isinstance(x, Term):
                r = opp_reach[id(x)]
                out = np.zeros(n)
                if not r.any():
                    return out
                for key, c in x.pay[who]:
                    if c:
                        out += c * (mats(key) @ r)
                return out
            vals = [go(kid) for kid in x.kids]
            if x.p != who:
                return sum(vals)
            if per_action is not None:
                per_action[id(x)] = vals
            if best:
                return np.max(vals, axis=0)
            rows = strat[id(x)]
            return sum(rows[a] * v for a, v in enumerate(vals))
        return go(self.root)

    def solve(self, rounds):
        for t in range(1, rounds + 1):
            for who in ("A", "B"):
                opp = "B" if who == "A" else "A"
                cur = {id(x): _rm(x.reg) for x in self.nodes}
                orr = self.reaches(opp, cur)
                own = self.reaches(who, cur)
                pa = {}
                self.values(who, orr, cur, per_action=pa)
                for x in self.nodes:
                    if x.p != who:
                        continue
                    vals = np.array(pa[id(x)])
                    rows = cur[id(x)]
                    base = (rows * vals).sum(axis=0)
                    x.reg = np.maximum(0.0, x.reg + vals - base)
                    x.cum += t ** AVG_POW * own[id(x)] * rows
            self.rounds = t
        return {id(x): _norm_rows(x.cum) for x in self.nodes}

    def exploit(self, avg):
        """Each player's gain from its best answers against the other's average, per hand
        (combo-weighted over all its hands; value units)."""
        out = {}
        W = mats("W")
        cw = combos()
        norm = W @ np.ones(self.n)
        for who in ("A", "B"):
            opp = "B" if who == "A" else "A"
            orr = self.reaches(opp, avg)
            gain = (self.values(who, orr, best=True) - self.values(who, orr, avg)) / norm
            out[who] = float((cw * gain).sum() / cw.sum())
        return out


def _results(game, avg, labels):
    """Per decision: {"mix": {hand: {action: freq}}, "ev": {hand: {action: value}}}. ev:
    each action's value once at that decision (the player's later decisions its best
    answers to the other's average strategy), divided by how often the other's hands get
    there. A decision the other side never leads to is left empty for the player facing
    it; one the player's own hands never reach shows what each hand would do there."""
    import icm_study as I
    names = I.exact()[0]
    W = mats("W")
    nodes = {}
    for who in ("A", "B"):
        opp = "B" if who == "A" else "A"
        orr = game.reaches(opp, avg)
        own = game.reaches(who, avg)
        pa = {}
        game.values(who, orr, best=True, per_action=pa)
        for x in game.nodes:
            if x.p != who:
                continue
            out = {"mix": {}, "ev": {}}
            nodes[labels[x.name]] = out
            norm = W @ orr[id(x)]
            if norm.sum() <= 1e-9 * game.n:
                continue
            vals = pa[id(x)]
            reach, rows = own[id(x)], avg[id(x)]
            for h in range(game.n):
                d = norm[h]
                if d <= 1e-12:
                    continue
                ev = {a: float(vals[k][h] / d) for k, a in enumerate(x.acts)}
                if reach[h] > 1e-9:
                    mix = {a: float(rows[k][h]) for k, a in enumerate(x.acts)}
                else:
                    mix = {max(ev, key=ev.get): 1.0}
                out["mix"][names[h]], out["ev"][names[h]] = mix, ev
    return nodes


# ------------------------------------------------------------------ the SB vs BB game

SB_LABELS = {"S0": "sb", "Siso": "sb_vs_iso", "Sjl": "sb_vs_jam_limp", "Srj": "sb_vs_jam_raise",
             "S3b": "sb_vs_3bet", "Bl": "bb_vs_limp", "Bri": "bb_vs_rejam_iso",
             "Br": "bb_vs_raise", "B4": "bb_vs_4bet", "Bj": "bb_vs_jam"}


def sb_tree(can_raise, can_iso, can_3b, vs, vb):
    """The game tree: A = the small blind (out of position after the flop), B = the big
    blind. vs / vb: each end's value for the small / big blind (icm_study._sb_setup)."""
    from icm_study import OOP_REALIZE as R, CALLER_REALIZE as RC

    def fx(k):
        return fixed(vs[k], vb[k])

    def allin():
        return show(vs["aw"], vs["al"], vb["aw"], vb["al"])

    def chk(base, w, l):
        return checked("A", base, vs[w], vs[l], vb[w], vb[l])
    acts_l, kids_l = ["check"], [chk(R, "win", "lose_limp")]
    if can_iso:
        rejam = Node("B", "Bri", ["call", "fold"], [allin(), fx("win_iso")])
        acts_l.append("iso")
        kids_l.append(Node("A", "Siso", ["fold", "call", "jam"],
                           [fx("lose_limp"), chk(RC, "win_iso", "lose_iso"), rejam]))
    acts_l.append("jam")
    kids_l.append(Node("A", "Sjl", ["call", "fold"], [allin(), fx("lose_limp")]))
    root_acts, root_kids = ["fold", "limp"], [fx("fold"), Node("B", "Bl", acts_l, kids_l)]
    if can_raise:
        acts_r = ["fold", "call", "jam"]
        kids_r = [fx("win"), chk(R, "win_raise", "lose_raise"),
                  Node("A", "Srj", ["call", "fold"], [allin(), fx("lose_raise")])]
        if can_3b:
            four = Node("B", "B4", ["call", "fold"], [allin(), fx("win_3b")])
            acts_r.append("3bet")
            kids_r.append(Node("A", "S3b", ["fold", "call", "jam"],
                               [fx("lose_raise"), chk(RC, "win_3b", "lose_3b"), four]))
        root_acts.append("raise")
        root_kids.append(Node("B", "Br", acts_r, kids_r))
    root_acts.append("jam")
    root_kids.append(Node("B", "Bj", ["call", "fold"], [allin(), fx("win")]))
    return Node("A", "S0", root_acts, root_kids)


def solve_sb(stacks, value, rounds=None):
    """icm_study.solve_sb's game, solved exactly (same result format), or None."""
    import icm_study as I
    setup = I._sb_setup(stacks, value)
    if setup is None:
        return None
    s, b, can_raise, can_iso, can_3b, eff, vs, vb = setup
    names = I.exact()[0]
    nh = len(names)
    game = Game(sb_tree(can_raise, can_iso, can_3b, vs, vb), nh)
    avg = game.solve(rounds or SB_ROUNDS)
    nodes = _results(game, avg, SB_LABELS)
    for k in SB_LABELS.values():
        nodes.setdefault(k, {"mix": {}, "ev": {}})
    I._clean(nodes, value, stacks)
    by = {x.name: x for x in game.nodes}

    def line(*path):
        """How often each hand takes a line of its own actions [(decision, action)]."""
        r = np.ones(nh)
        for nm, act in path:
            x = by[nm]
            r = r * avg[id(x)][x.acts.index(act)]
        return [float(v) for v in r]
    zero = [0.0] * nh
    ranges = {"limp": line(("S0", "limp")), "jam": line(("S0", "jam")),
              "raise": line(("S0", "raise")) if can_raise else zero,
              "sb_4bet": line(("S0", "raise"), ("S3b", "jam")) if can_3b else zero,
              "bb_3bet": line(("Br", "3bet")) if can_3b else zero,
              "limp_rejam": line(("S0", "limp"), ("Siso", "jam")) if can_iso else zero,
              "bb_iso": line(("Bl", "iso")) if can_iso else zero,
              "bb_jam_limp": line(("Bl", "jam")),
              "bb_jam_raise": line(("Br", "jam")) if can_raise else zero,
              "bb_call_jam": line(("Bj", "call"))}
    return {"game": "sb", "nodes": nodes, "ranges": ranges, "can_raise": can_raise,
            "can_iso": can_iso, "can_3b": can_3b, "eff": eff, "stacks": list(stacks), "s": s, "b": b,
            "values": {"sb": vs, "bb": vb},
            "mix": nodes["sb"]["mix"], "ev": nodes["sb"]["ev"],
            "bb": {"vs_limp": [nodes["bb_vs_limp"]["mix"].get(names[h], {}).get("jam", 0.0)
                               for h in range(nh)],
                   "vs_raise": ranges["bb_jam_raise"], "vs_jam": ranges["bb_call_jam"]},
            "exact": True, "rounds": game.rounds, "exploit": game.exploit(avg)}


# ------------------------------------------------------------------ the short first-in game

class OpenGame:
    """The short opener first in (icm_study.solve_open's game): the opener folds, opens or
    jams; the players behind answer in turn, and the first to put chips in plays it out with
    the opener (the rest fold). Card removal between the opener and each responder (the
    responders' hands independent of one another, as in the fictitious-play solve). Each
    player's fold is worth what happens after it: the players behind may still play the
    opener, and that moves the folder's ICM equity too."""

    N = 1225.0

    def __init__(self, S):
        from icm_study import OOP_REALIZE as OR, CALLER_REALIZE as CR
        self.S = S
        self.o, self.b, self.resp = S["o"], S["b"], list(S["resp"])
        self.n = n = 169
        self.can3 = {k: bool(S["can3"].get(k)) for k in self.resp}
        self.flat = {k: k == self.b for k in self.resp}
        # checked-down shares (row player's): the raise called, a 3-bet called; the
        # opener's rows (mo_) and the responder's (mk_)
        self.mo_c, self.mk_c, self.mo_3, self.mk_3 = {}, {}, {}, {}
        for k in self.resp:
            if S["oop_blind"][k]:        # the responder (a blind) is out of position
                self.mo_c[k], self.mk_c[k] = ("Wq", CR), ("Wr", CR)
                self.mo_3[k], self.mk_3[k] = ("Wq", OR), ("Wr", OR)
            else:
                self.mo_c[k], self.mk_c[k] = ("Wr", OR), ("Wq", OR)
                self.mo_3[k], self.mk_3[k] = ("Wr", CR), ("Wq", CR)
        self.acts = {"root": ["fold", "open", "jam"]}
        if any(self.can3.values()):
            self.acts["o3"] = ["fold", "call", "jam"]
        for k in self.resp:
            self.acts[f"orj:{k}"] = ["call", "fold"]
        for k in self.resp:
            self.acts[f"op:{k}"] = (["fold"] + (["call"] if self.flat[k] else []) + ["jam"] +
                                    (["3bet"] if self.can3[k] else []))
            if self.can3[k]:
                self.acts[f"f4:{k}"] = ["call", "fold"]
            self.acts[f"cj:{k}"] = ["call", "fold"]
        self.reg = {x: np.zeros((len(a), n)) for x, a in self.acts.items()}
        self.cum = {x: np.zeros((len(a), n)) for x, a in self.acts.items()}
        self.groups = [[x for x in self.acts if x in ("root", "o3") or x.startswith("orj:")]]
        for k in self.resp:
            self.groups.append([x for x in self.acts if x.endswith(f":{k}") and not x.startswith("orj:")])
        self.rounds = 0

    def a(self, sig, node, act):
        return sig[node][self.acts[node].index(act)]

    def summary(self, sig, k):
        """Responder k's play as the opener's hands see it (per opener hand: how often k
        calls the jam / jams / flats / 3-bets over the open, card-removal weighted, and the
        opener's equity or pot share in each)."""
        W, WE, N = mats("W"), mats("WE"), self.N
        q = {}
        cj = self.a(sig, f"cj:{k}", "call")
        q["CJ"], q["CJE"] = W @ cj / N, WE @ cj / N
        jk = self.a(sig, f"op:{k}", "jam")
        q["J"], q["JE"] = W @ jk / N, WE @ jk / N
        F = 1.0 - q["J"]
        if self.flat[k]:
            ck = self.a(sig, f"op:{k}", "call")
            q["C"], q["CS"] = W @ ck / N, mats(self.mo_c[k]) @ ck / N
            F = F - q["C"]
        if self.can3[k]:
            tk = self.a(sig, f"op:{k}", "3bet")
            q["T"], q["TS"] = W @ tk / N, mats(self.mo_3[k]) @ tk / N
            tc = tk * self.a(sig, f"f4:{k}", "call")
            q["TC"], q["TCE"] = W @ tc / N, WE @ tc / N
            F = F - q["T"]
        q["F"] = np.maximum(0.0, F)
        return q

    def evaluate(self, sig, best=False, only=None, Q=None):
        """Decisions' action values against strategies sig ({node: rows}): every player's,
        or only the opener's ("o") or one responder's (k). Q: the responders' summaries
        (computed from sig when not given). The opener's values: per hand, counterfactual
        (the responders' play that leads there included, its own not). A responder's:
        summed over the opener's hands that get there (card-removal weighted); "_k_reach"
        has those reach vectors. With best, each player's own later decisions are its best
        answers (the values shown), else sig's."""
        S, o, n = self.S, self.o, self.n
        V, steal, ofv = S["V"], S["v_steal"], S["v_fold"]
        W, WE = mats("W"), mats("WE")
        if Q is None:
            Q = {k: self.summary(sig, k) for k in self.resp}

        def mixed(node, vals):
            rows = sig[node]
            return sum(rows[self.acts[node].index(a)] * v for a, v in vals.items())

        def pick(node, vals, mine):
            """A decision's value to the player making it (mine: best answer if best)."""
            if best and mine:
                return np.max(list(vals.values()), axis=0)
            return mixed(node, vals)

        def lines(who, mine, ks):
            """Per responder k in ks, k's line's value to player `who` given the opener's
            hand: the call, the 3-bet answers, the re-jam answers (opener's decisions)."""
            out = {}
            for k in ks:
                q, Vk = Q[k], V[k]
                pv = {key: Vk[key][who] for key in Vk}
                c = {}
                if self.flat[k]:
                    c["call"] = q["C"] * pv["ccl"] + q["CS"] * (pv["ccw"] - pv["ccl"])
                if self.can3[k]:
                    c["v3"] = {"fold": q["T"] * pv["ofold"],
                               "call": q["T"] * pv["c3l"] + q["TS"] * (pv["c3w"] - pv["c3l"]),
                               "jam": (q["T"] - q["TC"]) * pv["tfold"] + q["TCE"] * pv["aw"]
                               + (q["TC"] - q["TCE"]) * pv["al"]}
                c["vrj"] = {"call": q["JE"] * pv["aw"] + (q["J"] - q["JE"]) * pv["al"],
                            "fold": q["J"] * pv["ofold"]}
                part = c.get("call", 0.0)
                if "v3" in c:
                    part = part + pick("o3", c["v3"], mine)
                c["part"] = part + pick(f"orj:{k}", c["vrj"], mine)
                out[k] = c
            return out

        res = {"_Q": Q, "_k_reach": {}}
        if only in (None, "o"):
            self._opener(res, Q, lines, o, n, V, steal, ofv)
        if only == "o":
            return res
        self._responders(res, sig, Q, lines, pick, only, n, V, steal, W, WE)
        return res

    def _opener(self, res, Q, lines, o, n, V, steal, ofv):
        lo = lines(o, True, self.resp)
        r = np.ones(n)
        jamv = np.zeros(n)
        for k in self.resp:
            q, Vk = Q[k], V[k]
            jamv += r * (q["CJE"] * Vk["aw"][o] + (q["CJ"] - q["CJE"]) * Vk["al"][o])
            r = r * (1 - q["CJ"])
        jamv += r * steal[o]
        r = np.ones(n)
        openv = np.zeros(n)
        o3 = {a: np.zeros(n) for a in ("fold", "call", "jam")}
        rk = {}
        for k in self.resp:
            c = lo[k]
            rk[k] = r
            if "v3" in c:
                for a_, v in c["v3"].items():
                    o3[a_] = o3[a_] + r * v
            res[f"orj:{k}"] = {a_: r * v for a_, v in c["vrj"].items()}
            openv += r * c["part"]
            r = r * Q[k]["F"]
        openv += r * steal[o]
        res["root"] = {"fold": np.full(n, ofv[o]), "open": openv, "jam": jamv}
        if "o3" in self.acts:
            res["o3"] = o3
        res["_o_reach"] = rk

    def _responders(self, res, sig, Q, lines, pick, only, n, V, steal, W, WE):
        o_open, o_jam = self.a(sig, "root", "open"), self.a(sig, "root", "jam")
        pre_f, pre_j = np.ones(n), np.ones(n)
        for i, k in enumerate(self.resp):
            if only is not None and only != k:
                pre_f = pre_f * Q[k]["F"]
                pre_j = pre_j * (1 - Q[k]["CJ"])
                continue
            Vk = V[k]
            lk = lines(k, False, self.resp[i + 1:])
            # what k gets after folding: the players behind play on with the opener
            g_open, g_jam = np.zeros(n), np.zeros(n)
            ro, rj = np.ones(n), np.ones(n)
            for m in self.resp[i + 1:]:
                q, Vm = Q[m], V[m]
                g_open += ro * lk[m]["part"]
                ro = ro * q["F"]
                g_jam += rj * (q["CJE"] * Vm["aw"][k] + (q["CJ"] - q["CJE"]) * Vm["al"][k])
                rj = rj * (1 - q["CJ"])
            g_open += ro * steal[k]
            g_jam += rj * steal[k]
            y = o_open * pre_f                # the opener's hands that get to k
            z = o_jam * pre_j
            s_rjc, s_rjf = self.a(sig, f"orj:{k}", "call"), self.a(sig, f"orj:{k}", "fold")
            vals = {"fold": W @ (y * g_open)}
            if self.flat[k]:
                vals["call"] = Vk["ccw"][k] * (W @ y) + (Vk["ccl"][k] - Vk["ccw"][k]) * (mats(self.mk_c[k]) @ y)
            vals["jam"] = (W @ (y * (Vk["ofold"][k] * s_rjf + Vk["aw"][k] * s_rjc))
                           + (Vk["al"][k] - Vk["aw"][k]) * (WE @ (y * s_rjc)))
            if self.can3[k]:
                s3f, s3c, s3j = (self.a(sig, "o3", x) for x in ("fold", "call", "jam"))
                u = y * s3j
                f4 = {"call": Vk["aw"][k] * (W @ u) + (Vk["al"][k] - Vk["aw"][k]) * (WE @ u),
                      "fold": Vk["tfold"][k] * (W @ u)}
                v3 = (W @ (y * (Vk["ofold"][k] * s3f + Vk["c3w"][k] * s3c))
                      + (Vk["c3l"][k] - Vk["c3w"][k]) * (mats(self.mk_3[k]) @ (y * s3c)))
                vals["3bet"] = v3 + pick(f"f4:{k}", f4, True)
                res[f"f4:{k}"] = f4
                res["_k_reach"][f"f4:{k}"] = u
            res[f"op:{k}"] = vals
            res["_k_reach"][f"op:{k}"] = y
            res[f"cj:{k}"] = {"call": Vk["aw"][k] * (W @ z) + (Vk["al"][k] - Vk["aw"][k]) * (WE @ z),
                              "fold": W @ (z * g_jam)}
            res["_k_reach"][f"cj:{k}"] = z
            pre_f = pre_f * Q[k]["F"]
            pre_j = pre_j * (1 - Q[k]["CJ"])

    def own_reach(self, sig, node):
        """How often a player's own hands get to its decision `node`."""
        if node == "o3" or node.startswith("orj:"):
            return self.a(sig, "root", "open")
        if node.startswith("f4:"):
            return self.a(sig, "op:" + node[3:], "3bet")
        return np.ones(self.n)

    def solve(self, rounds):
        """CFR+: the opener updates, then the responders (alternating), linear averaging."""
        sig = {x: _rm(r) for x, r in self.reg.items()}
        Q = {k: self.summary(sig, k) for k in self.resp}
        for t in range(1, rounds + 1):
            for who, grp in zip(["o"] + self.resp, self.groups):
                res = self.evaluate(sig, only=who, Q=Q)
                for x in grp:
                    acts = self.acts[x]
                    vals = np.array([res[x][a] for a in acts])
                    rows = sig[x]
                    base = (rows * vals).sum(axis=0)
                    self.reg[x] = np.maximum(0.0, self.reg[x] + vals - base)
                    self.cum[x] += t ** AVG_POW * self.own_reach(sig, x) * rows
                for x in grp:
                    sig[x] = _rm(self.reg[x])
                if who != "o":
                    Q[who] = self.summary(sig, who)
            self.rounds = t
        return {x: _norm_rows(c) for x, c in self.cum.items()}

    def exploit(self, avg):
        """Each player's gain from its best answers against the others' averages, per hand
        (combo-weighted over all its hands; value units)."""
        cw = combos()
        tot = cw.sum()
        a_, b_ = self.evaluate(avg), self.evaluate(avg, best=True)

        def mixed(res, node):
            return sum(avg[node][i] * res[node][a] for i, a in enumerate(self.acts[node]))

        def bestv(res, node):
            return np.max([res[node][a] for a in self.acts[node]], axis=0)
        out = {"opener": float((cw * (bestv(b_, "root") - mixed(a_, "root"))).sum() / tot)}
        for k in self.resp:
            g = sum((cw * (bestv(b_, nd) - mixed(a_, nd))).sum() / self.N
                    for nd in (f"op:{k}", f"cj:{k}"))
            out[k] = float(g / tot)
        return out


def solve_open(stacks, value, opener, rounds=None):
    """icm_study.solve_open's game, solved exactly (same result format), or None."""
    import icm_study as I
    S = I._open_setup(stacks, value, opener)
    if S is None:
        return None
    names = I.exact()[0]
    nh = len(names)
    g = OpenGame(S)
    avg = g.solve(rounds or OPEN_ROUNDS)
    o, b, resp = g.o, g.b, g.resp
    res = g.evaluate(avg, best=True)
    Q, reach_o, kr = res["_Q"], res["_o_reach"], res["_k_reach"]
    W, WE = mats("W"), mats("WE")
    nodes = {k: {"mix": {}, "ev": {}} for k in ("open", "open_vs_3bet", "open_vs_rejam")}
    for k in resp:
        for x in ("vs_open", "vs_4bet", "vs_jam", "open_vs_rejam"):
            nodes[f"{x}:{k}"] = {"mix": {}, "ev": {}}
    A = lambda node, act: g.a(avg, node, act)      # noqa: E731
    opn = A("root", "open")
    for h in range(nh):
        hn = names[h]
        v = {a: float(res["root"][a][h]) for a in ("fold", "open", "jam")}
        nodes["open"]["mix"][hn] = {a: float(A("root", a)[h]) for a in ("fold", "open", "jam")}
        nodes["open"]["ev"][hn] = v
        # the answers: each one's value once facing that bet (never faced: all the same)
        if "o3" in g.acts:
            p3 = reach_o[b][h] * Q[b]["T"][h] if g.can3.get(b) else 0.0
            ev3 = ({a: float(res["o3"][a][h] / p3) for a in ("fold", "call", "jam")} if p3 > 1e-12
                   else {a: v["open"] for a in ("fold", "call", "jam")})
            mix3 = ({a: float(A("o3", a)[h]) for a in ("fold", "call", "jam")} if opn[h] > 1e-9
                    else {max(ev3, key=ev3.get): 1.0})
            nodes["open_vs_3bet"]["mix"][hn], nodes["open_vs_3bet"]["ev"][hn] = mix3, ev3
        agg_m, agg_v, agg_w = {"call": 0.0, "fold": 0.0}, {"call": 0.0, "fold": 0.0}, 0.0
        for k in resp:
            r = float(reach_o[k][h] * Q[k]["J"][h])
            ei = ({a: float(res[f"orj:{k}"][a][h] / r) for a in ("call", "fold")} if r > 1e-12
                  else {"call": v["open"], "fold": v["open"]})
            if opn[h] > 1e-9:
                mi = {a: float(A(f"orj:{k}", a)[h]) for a in ("call", "fold")}
            else:
                mi = {"call": 1.0, "fold": 0.0} if ei["call"] > ei["fold"] else {"call": 0.0, "fold": 1.0}
            nodes[f"open_vs_rejam:{k}"]["mix"][hn], nodes[f"open_vs_rejam:{k}"]["ev"][hn] = mi, ei
            if r > 1e-12:
                agg_w += r
                for a in ("call", "fold"):
                    agg_m[a] += r * mi[a]
                    agg_v[a] += r * ei[a]
        nodes["open_vs_rejam"]["mix"][hn] = ({a: x / agg_w for a, x in agg_m.items()} if agg_w
                                            else {"fold": 1.0})
        nodes["open_vs_rejam"]["ev"][hn] = ({a: x / agg_w for a, x in agg_v.items()} if agg_w
                                           else {"call": v["open"], "fold": v["open"]})
    for k in resp:
        Vk = S["V"][k]
        for node, name in ((f"op:{k}", f"vs_open:{k}"), (f"f4:{k}", f"vs_4bet:{k}"),
                           (f"cj:{k}", f"vs_jam:{k}")):
            if node not in g.acts:
                continue
            vals, reach = res[node], kr[node]
            norm = W @ reach
            if node.startswith("f4:") and norm.sum() <= 1e-9 * nh:
                # (the opener never 4-bets: as if it jammed its whole open range)
                u = kr[f"op:{k}"]
                vals = {"call": Vk["aw"][k] * (W @ u) + (Vk["al"][k] - Vk["aw"][k]) * (WE @ u),
                        "fold": Vk["tfold"][k] * (W @ u)}
                norm = W @ u
            if norm.sum() <= 1e-9 * nh:
                continue
            own = g.own_reach(avg, node)
            for h in range(nh):
                d = norm[h]
                if d <= 1e-12:
                    continue
                ev = {a: float(vals[a][h] / d) for a in g.acts[node]}
                mix = ({a: float(A(node, a)[h]) for a in g.acts[node]} if own[h] > 1e-9
                       else {max(ev, key=ev.get): 1.0})
                nodes[name]["mix"][names[h]], nodes[name]["ev"][names[h]] = mix, ev
    I._clean(nodes, value, stacks)
    fl = lambda v: [float(x) for x in v]           # noqa: E731
    zero = [0.0] * nh
    ranges = {"open": fl(opn),
              "open_call": {k: fl(opn * A(f"orj:{k}", "call")) for k in resp},
              "jam": fl(A("root", "jam")),
              "fourbet": fl(opn * A("o3", "jam")) if "o3" in g.acts else zero,
              "rejam": {k: fl(A(f"op:{k}", "jam")) for k in resp},
              "threebet": {k: (fl(A(f"op:{k}", "3bet")) if g.can3[k] else zero) for k in resp},
              "flat": {k: (fl(A(f"op:{k}", "call")) if g.flat[k] else zero) for k in resp},
              "call_jam": {k: fl(A(f"cj:{k}", "call")) for k in resp},
              "bb_call_open": fl(A(f"op:{b}", "call")) if b in resp and g.flat[b] else zero}
    return {"game": "open", "nodes": nodes, "ranges": ranges, "opener": o, "resp": resp,
            "stacks": list(stacks), "can3": S["can3"], "exact": True, "rounds": g.rounds,
            "exploit": g.exploit(avg)}
