"""Coach audit: plays whole final tables with the trainer's own machinery in your seat (the
hint, the hand tree's grading, the ICM grade, the ICM Coach popup) and checks everything
the coach says.

    py -3.13 solver/coach_audit.py --minutes 30 --seed 1 --out coach_audit.jsonl
    (Linux, no screen:  xvfb-run -a python3 solver/coach_audit.py ...)

Your seat plays the hint most of the time and something else now and then (so wrong moves
get graded too). At every decision of yours the coach popup is built exactly as the ICM
Coach button builds it, its text read back, and checked:
  - it opens without an error, in reasonable time;
  - no 'nan' / 'None' / 'inf', no percentage over 100, no negative share;
  - the facts: your stack and position, how many players are shorter, the pay jump (next
    one out gets / the next place pays) against the real payouts;
  - all-in decisions: the verdict matches the solve, the hint (your sims with the final
    table's rules) agrees with it, the ICM grade of jamming / folding / calling matches the
    verdict, and a move graded right is never captioned below "Elite";
  - the exploit lines: against wider callers the best jam range is no wider, against
    tighter callers no narrower (and the same for calls against wider / tighter jammers);
  - opening / facing an open / facing a 3-bet: shares and equities in range, each
    player's answers adding up to 100%, the everyone-folds chance the product of the
    folds, the bluff check's break-even between 0 and 100%;
  - after the flop: the hand described as the evaluator sees it, equities in range, the
    'Strong' tip only with 65%+ equity;
  - the hand tree: playing the hint is graded "Elite"; folding AA / KK preflop never is.
Each problem is a JSON line in --out; a summary prints at the end.
"""

import argparse
import json
import random
import re
import sys
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "solver"))

import tkinter as tk  # noqa: E402

import match8  # noqa: E402
import trainer8 as T  # noqa: E402
import final_table as FT  # noqa: E402
import icm_study as I  # noqa: E402
from engine import new_deck  # noqa: E402
from engine8 import Game8  # noqa: E402

BB = match8.BIG_BLIND
PCT = re.compile(r"(?<![+\-\d.])(\d+(?:\.\d+)?)%")
MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?K?")


def ordinal(n):
    return I.ordinal(n)


class Audit:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.flags, self.counts = [], Counter()
        self.times = []
        self.root = tk.Tk()
        self.root.withdraw()
        T.load_study_table()
        if args.flop_sims:
            import flop_sims
            import flopsim_audit
            folder = tempfile.mkdtemp(prefix="coach_sims_")
            flopsim_audit.synthetic(folder, random.Random(args.seed))
            T.FLOP_SIMS = flop_sims.load_all(folder)
        app = T.Trainer.__new__(T.Trainer)   # the trainer's methods, without its window
        app.root, app.mode = self.root, "Final table"
        app.coach_bot = app.advice = app.state = None
        app.analyst = T.Analyst()
        app.hand_sim = app.hand_sim_key = None
        app.hand_key = 0
        app.icm_spot = app.coach_host = None
        app.spot = {"scen": "-"}
        app.director = None
        self.app = app

    def flag(self, rule, ctx, **kw):
        self.flags.append(dict(ctx, rule=rule, **kw))
        self.counts["flag:" + rule] += 1

    # -- the coach popup, read back --
    def coach_text(self):
        app = self.app
        t0 = time.time()
        app.open_icm_coach()
        dt = time.time() - t0
        top = app.coach_host.coach
        cv = next(w for w in top.winfo_children() if isinstance(w, tk.Canvas))
        lines = [cv.itemcget(i, "text") for i in cv.find_all() if cv.type(i) == "text"]
        top.destroy()
        app.coach_host = None
        return lines, dt

    # -- checks --
    def check_text(self, lines, ctx):
        text = "\n".join(lines)
        for bad, pat in (("nan", r"\bnan\b"), ("None", r"\bNone\b"), ("inf", r"\binf\b"),
                         ("brace", r"[{}]")):
            m = re.search(pat, text)
            if m:
                self.flag("bad text", ctx, what=bad,
                          line=next((x for x in lines if re.search(pat, x)), "")[:200])
        for ln in lines:
            for m in PCT.finditer(ln):
                v = float(m.group(1))
                if v > 100.05:
                    self.flag("percentage over 100", ctx, line=ln[:200])
            if "-$" in ln and ("worth -$" in ln and "keep" in ln):
                self.flag("negative money kept", ctx, line=ln[:200])

    def check_facts(self, spot, lines, ctx):
        st, pays = spot["stacks"], sorted(spot["pays"], reverse=True)
        n, me = len(st), spot["hero"]
        title = lines[0] if lines else ""
        pos = I.positions(n)[me]
        if not title.startswith(f"ICM Coach  ·  {pos} {st[me]:g} bb with {spot['hand']}"):
            self.flag("title wrong", ctx, title=title[:120], want=f"{pos} {st[me]:g} {spot['hand']}")
        shorter = sum(1 for k, v in enumerate(st) if v < st[me] and k != me)
        want = f"{shorter} player{'s are' if shorter != 1 else ' is'} shorter than you"
        if not any(x.startswith(want) for x in lines):
            self.flag("shorter count wrong", ctx, want=want)
        # the equity line, number by number, against a brute-force ICM
        from icm_check import icm_brute
        eq = icm_brute(st, spot["pays"])[me]
        pool = sum(spot["pays"])
        want = (f"Your stack is {100 * (st[me] / sum(st)):.1f}% of the chips; under ICM it's "
                f"worth {I.money(eq)}, {100 * eq / pool:.1f}% of the prize money still to be "
                f"paid ({I.money(pool)}).")
        if want not in lines:
            self.flag("equity line wrong", ctx, want=want,
                      got=next((x for x in lines if x.startswith("Your stack is")), None))
        if n >= 2:
            nxt = I.money(pays[n - 1]).replace(".00", "")
            up = I.money(pays[n - 2]).replace(".00", "")
            want = f"The next one out gets {nxt}; {ordinal(n - 1)} pays {up}"
            if not any(want in x for x in lines):
                self.flag("pay jump wrong", ctx, want=want,
                          got=next((x[:120] for x in lines if "next one out" in x), None))

    def check_allin(self, spot, lines, adv, s, ctx):
        res = spot["res"]
        g = res["gain"][spot["hand"]]
        go = "Jam" if spot["kind"] == "jam" else "Call"
        best = go if g > 0 else "Fold"
        verdict = next((x for x in lines if re.match(r"^(Jam|Call|Fold): (jam|call) is worth",
                                                      x)), None)
        if verdict is None or not verdict.startswith(best + ":"):
            self.flag("verdict wrong", ctx, want=best, got=(verdict or "")[:80], gain=round(g, 1))
        # the hint (your sims + final table rules) vs the verdict
        if adv is not None:
            jam = adv[0] == "raise" and adv[1] >= s.max_raise_to - 1
            hint_go = jam if spot["kind"] == "jam" else adv[0] in ("call", "raise")
            hint_fold = adv[0] == "fold"
            if hint_go != (g > 0) and (hint_fold or hint_go) and abs(g) > 0.0005 * sum(
                    spot["pays"]):
                self.flag("hint contradicts the coach", ctx, hint=T.describe(adv, s),
                          verdict=best, gain=round(g))
        # the ICM grade of each answer, and its caption
        for mv in ([("fold", 0)] + ([("raise", s.max_raise_to)] if spot["kind"] == "jam"
                                    else [("call", 0)])):
            sp2 = dict(spot)
            graded = FT.grade(sp2, mv, s)
            if graded is None:
                self.flag("ICM grade missing", ctx, move=list(mv))
                continue
            right, gg = graded
            should = (mv[0] != "fold") == (g > 0)
            if right != should:
                self.flag("ICM grade disagrees with the verdict", ctx, move=list(mv),
                          verdict=best)
            node = {"chosen": ("x", None), "verdict": False,
                    "icm": (right, gg, sum(spot["pays"]))}
            cap = T.caption_for(node)
            if right and cap[0] != T.CAPTIONS[0][0]:
                self.flag("right ICM move not Elite", ctx, move=list(mv), caption=cap[0])
        # exploits: monotone ranges
        ex = spot.get("exploit")
        if ex:
            def share(gains):
                return sum(I.COMBOS[h] for h in gains if gains[h] > 0) / 1326
            base, wide, tight = share(res["gain"]), share(ex["wide"]["gain"]), share(
                ex["tight"]["gain"])
            st = spot["stacks"]
            sp_ = res["spot"]
            deep = st[spot["hero"]] >= 8 and all(st[i] >= 8 for i in sp_.behind) if \
                spot["kind"] == "jam" else st[spot["hero"]] >= 8 and st[spot["jammer"]] >= 8
            if not deep:   # (micro stacks: wider calls mean weaker ranges, no fold equity)
                ok = True
            elif spot["kind"] == "jam":
                ok = wide <= base + 0.01 and tight >= base - 0.01
            else:
                ok = wide >= base - 0.01 and tight <= base + 0.01
            if not ok:
                self.flag("exploit ranges out of order", ctx, kind=spot["kind"],
                          base=round(base, 3), wide=round(wide, 3), tight=round(tight, 3))
            if not any(x.startswith("Exploits") for x in lines):
                self.flag("exploit section missing", ctx)

    def check_analysis(self, spot, lines, ctx):
        o = spot.get("facing")
        if o:
            for pos, stack, f in o["answers"]:
                tot = sum(f.values())
                if abs(tot - 1) > 0.01:
                    self.flag("answers don't add to 100%", ctx, who=pos, total=round(tot, 3))
            p = 1.0
            for _, _, f, *_ in o["answers"]:
                p *= f["fold"]
            if abs(p - o["p_all_fold"]) > 1e-6:
                self.flag("everyone-folds isn't the product of folds", ctx)
            if not 0 < o["open_share"] <= 1 or not 0 <= o["eq"] <= 1:
                self.flag("facing: share / equity out of range", ctx,
                          share=o["open_share"], eq=o["eq"])
        o = spot.get("open2x")
        if o:
            p = 1.0
            for r in o["resp"]:
                tot = r["fold"] + r["call"] + r["raise"] + r["jam"]
                if abs(tot - 1) > 0.01:
                    self.flag("open answers don't add to 100%", ctx, who=r["pos"])
                p *= r["fold"]
            if abs(p - o["p_all_fold"]) > 1e-6:
                self.flag("open: everyone-folds isn't the product", ctx)
            if o.get("spr") is not None and o["spr"] < 0:
                self.flag("open: negative SPR", ctx, spr=o["spr"])
        o = spot.get("vs3bet")
        if o:
            if not 0 < o["share"] <= 1 or not 0 <= o["eq"] <= 1 or not 0 <= o["p_call"] <= 1:
                self.flag("vs 3-bet: share / equity out of range", ctx,
                          share=o["share"], eq=o["eq"], p_call=o["p_call"])
            if o["behind"] < 0:
                self.flag("vs 3-bet: negative stack behind", ctx, behind=o["behind"])
        o = spot.get("post")
        if o:
            from cards import card, category, evaluate
            cat = category(evaluate([card(c) for c in list(spot["cards"]) +
                                     list(spot["state"].board)]))
            if cat != o["cat"]:
                self.flag("postflop: category mismatch", ctx)
            for p in o["opps"]:
                if p["eq"] is not None and not 0 <= p["eq"] <= 1:
                    self.flag("postflop: equity out of range", ctx, eq=p["eq"])
            eqs = [p["eq"] for p in o["opps"] if p["eq"] is not None]
            strong = any(x.startswith("Strong:") for x in lines)
            if strong and eqs and min(eqs) < 0.65:
                self.flag("postflop: 'Strong' tip with less than 65%", ctx, eq=min(eqs))
            made = o["made"].lower()
            names = {0: None, 1: "pair", 2: "two pair", 3: "three of a kind", 4: "straight",
                     5: "flush", 6: "full house", 7: "four of a kind", 8: "straight flush"}
            want = names.get(cat)
            if cat == 7 and "quads" in made:
                want = None
            if want and want.split()[0] not in made and not (cat == 3 and (
                    "set" in made or "trips" in made)) and not (cat == 1 and "pair" in made):
                self.flag("postflop: hand described wrongly", ctx, made=o["made"],
                          category=cat)
        for ln in lines:
            m = re.search(r"needs everyone\s+to fold (\d+)%", ln) or re.search(
                r"needs .* to fold (\d+)%", ln)
            if m and not 0 <= int(m.group(1)) <= 100:
                self.flag("bluff break-even out of range", ctx, line=ln[:160])

    # -- a decision of yours --
    def hero_act(self, s, advisor, ft):
        app = self.app
        app.board_seen_len = len(s.board)
        adv = advisor.act(s)
        sim = app.flop_sim(s)
        if sim is not None:
            adv = sim.pick(s, BB) or adv
            self.counts["decisions on a Pio flop"] += 1
        icm = FT.coach_spot(s, ft)
        app.analyst.record(s, None, adv)
        nodes = app.analyst.flush()
        node = nodes[-1] if nodes else None
        ctx = {"table": self.table_no, "hand": self.hand_no, "pos": s.position,
               "street": s.street, "hole": list(s.hole), "board": list(s.board),
               "stacks": [round(x, 2) for x in icm["stacks"]], "kind": icm["kind"],
               "line": [(st, a, round(x / BB, 2)) for st, _, a, x in s.history
                        if a != "fold"][-6:]}
        self.counts["hero decisions"] += 1
        if self.rng.random() < self.args.coach_rate:
            app.icm_spot, app.advice, app.state = icm, adv, s
            try:
                lines, dt = self.coach_text()
                self.counts["coach popups"] += 1
                self.counts["coach: " + icm["kind"] + (" " + s.street if icm["kind"] == "info"
                                                       else "")] += 1
                self.times.append(dt)
                if dt > 3.0:
                    self.flag("coach slow", ctx, seconds=round(dt, 2))
                self.check_text(lines, ctx)
                self.check_facts(icm, lines, ctx)
                if icm["kind"] != "info":
                    self.check_allin(icm, lines, adv, s, ctx)
                self.check_analysis(icm, lines, ctx)
            except Exception as e:
                self.flag("coach error", ctx, error=repr(e),
                          trace=traceback.format_exc(limit=6)[-1200:])
        # your move: the hint, or now and then something else
        opts = [adv]
        if s.to_call:
            opts.append(("fold", 0))
        opts.append(("call", 0) if s.to_call else ("check", 0))
        if s.min_raise_to:
            opts += [("raise", s.min_raise_to), ("raise", s.max_raise_to)]
        move = adv if self.rng.random() < 0.8 else self.rng.choice(opts)
        # the hand tree's grade
        if node is not None:
            chosen = T.bucket(move, s)
            same = T.kind(move, s) == T.kind(adv, s)
            if same and T.kind(move, s) == "Raise":
                def to(m):
                    return max(s.min_raise_to, min(m[1], s.max_raise_to))
                same = abs(to(move) - to(adv)) <= 0.25 * to(adv)
            node["chosen"], node["verdict"] = chosen, same
            if chosen not in [o[0] for o in node["options"]]:
                node["options"].append((chosen, T.key_label(chosen), 0.0, "#888"))
            graded = FT.grade(dict(icm), move, s)
            if graded is not None:
                node["icm"] = (graded[0], graded[1], sum(icm["pays"]))
            try:
                cap = T.caption_for(node)
            except Exception as e:
                self.flag("caption error", ctx, error=repr(e))
                cap = None
            if cap is not None:
                self.counts["caption " + cap[0]] += 1
                if move == adv and graded is None and cap[0] != T.CAPTIONS[0][0]:
                    self.flag("hint graded below Elite", ctx, caption=cap[0],
                              move=T.describe(move, s))
                if (s.street == "preflop" and move[0] == "fold" and s.to_call and
                        I.hand_class(*s.hole) in ("AA", "KK") and cap[0] == T.CAPTIONS[0][0]):
                    self.flag("AA/KK fold graded Elite", ctx)
                if graded is not None and graded[0] and cap[0] != T.CAPTIONS[0][0]:
                    self.flag("right ICM move captioned below Elite", ctx, caption=cap[0])
        app.analyst.resolve_hero(s, move)
        if s.street == "preflop" and move[0] == "fold" and s.to_call and \
                I.hand_class(*s.hole) in ("AA", "KK") and move == adv:
            self.flag("hint folds AA/KK", ctx)
        return move

    def run(self):
        args, rng = self.args, self.rng
        FT.POLICY.update(on=True, icm=True)
        t_end = time.time() + 60 * args.minutes
        audit = self

        class Seat:
            def __init__(self, bot, hero):
                self.bot, self.hero = bot, hero
                self.advisor = T.make_study(T.STUDY, 4242) if hero else None

            def new_hand(self, n, b):
                self.bot.new_hand(n, b)
                if self.advisor:
                    self.advisor.new_hand(n, b)

            def hand_over(self, r):
                self.bot.hand_over(r)
                if self.advisor:
                    self.advisor.hand_over(r)

            def act(self, s):
                if self.hero:
                    return audit.hero_act(s, self.advisor, audit.ft)
                mv = self.bot.act(s)
                audit.app.analyst.record(s, mv)
                return mv

        plan = None
        if args.tables:   # every preset table in turn, each prize pool and blind setting
            lo, _, hi = args.tables.partition("-")
            nums = list(range(int(lo), int(hi or lo) + 1))
            pools = [500_000, 1_000_000, 1_500_000, 2_750_000]
            levels = [15, 20, 30, 0]
            plan = [(t, pools[(t + r) % 4], levels[(t + 2 * r) % 4])
                    for r in range(args.runs) for t in nums]
        while (plan is None and time.time() < t_end) or plan:
            if plan:
                self.table_no, pool, level = plan.pop(0)
            else:
                self.table_no = rng.randint(1, I.N_TABLES)
                pool = rng.choice([500_000, 1_000_000, 1_500_000, rng.randint(50, 3000) * 1000])
                level = FT.LEVEL_HANDS
            ft = FT.FinalTable(self.table_no, I.pool_payouts(pool), BB)
            ft.level_hands = level
            self.counts[f"pool ${pool // 1000}K"] += 1
            self.counts[f"blinds up every {level or 'never'}"] += 1
            self.ft = ft
            FT.POLICY["table"] = ft
            self.app.ft = ft
            seats_all = {x: Seat(T.make_study(T.STUDY, rng.randrange(10 ** 6)), x == 0)
                         for x in range(8)}
            hands = 0
            while (0 in ft.alive() or args.to_the_end) and len(ft.alive()) > 1 and \
                    hands < args.max_hands and (plan is not None or time.time() < t_end):
                seats, b, stacks = ft.start_hand()
                self.hand_no = hands
                self.app.hand_key += 1
                self.app.analyst.new_hand(hands)
                g = Game8([seats_all[x] for x in seats], big_blind=BB, ante=BB,
                          ante_mode="bb", time_limit_s=1e9)
                try:
                    res = g.play_hand(new_deck(rng), button=b, hand_no=hands, stacks=stacks)
                except Exception as e:
                    self.flag("crash in a hand", {"table": self.table_no}, error=repr(e),
                              trace=traceback.format_exc(limit=6)[-1200:])
                    break
                self.app.analyst.pending = []
                ft.finish_hand(seats, stacks, res)
                hands += 1
                self.counts["hands"] += 1
            self.counts["tables"] += 1
            if 0 not in ft.alive():
                self.counts["you busted"] += 1
            if len(ft.alive()) == 1:
                self.counts["played to a winner"] += 1
                if sum(ft.stacks) and abs(sum(ft.stacks) - ft.stacks[ft.alive()[0]]) > 1e-6:
                    self.flag("chips left at busted seats", {"table": self.table_no})
            elif hands >= args.max_hands:
                self.counts["stopped at the hand limit"] += 1
            if ft.over is None and 0 not in ft.alive():
                self.flag("you busted but the session isn't over", {"table": self.table_no})
            self.root.update()
        self.times.sort()
        if self.times:
            self.counts["coach p50 ms"] = round(1000 * self.times[len(self.times) // 2])
            self.counts["coach p99 ms"] = round(1000 * self.times[int(len(self.times) * 0.99)])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--coach-rate", type=float, default=1.0,
                    help="share of your decisions the coach is opened at")
    ap.add_argument("--no-flop-sims", dest="flop_sims", action="store_false",
                    help="don't add made-up Pio solves (the postflop coach then uses your sims)")
    ap.add_argument("--tables", help="play these preset tables in turn (e.g. 1-100) "
                    "instead of random ones for --minutes")
    ap.add_argument("--runs", type=int, default=1, help="with --tables: times through them")
    ap.add_argument("--to-the-end", action="store_true",
                    help="keep playing after you bust, down to a winner (the bots' side)")
    ap.add_argument("--max-hands", type=int, default=400)
    ap.add_argument("--out", default="coach_audit.jsonl")
    args = ap.parse_args()
    au = Audit(args)
    au.run()
    with open(args.out, "w") as f:
        for fl in au.flags:
            f.write(json.dumps(fl, default=str) + "\n")
    print(json.dumps({"counts": dict(au.counts),
                      "by_rule": Counter(fl["rule"] for fl in au.flags)}, indent=1))


if __name__ == "__main__":
    main()
