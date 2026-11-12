"""The trainer's Final table mode for the web page: the same bots, ranges, ICM rules and
coach as the desktop trainer (trainer8.py), driven one decision at a time.

The page sends commands as JSON and gets the table back as JSON (handle()). A hand can't
pause for your click inside the engine here, so each time you act the hand is replayed
from the start with your moves so far: the bots are seeded per hand, so the replay is the
same hand, and it stops again at your next decision."""

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import tkstub  # noqa: E402

tkstub.install()

import trainer8 as T  # noqa: E402
import final_table as FT  # noqa: E402
import icm_study as I  # noqa: E402
import match8  # noqa: E402
from cards import name as card_name  # noqa: E402
from engine import new_deck  # noqa: E402
from engine8 import Game8  # noqa: E402

# The bots' postflop equity runs for a time budget on the desktop (about 16 samples there).
# Here the same hand is replayed after each of your moves, so it must play the same way
# every time and on every phone: a fixed sample count, about the desktop's strength.
from bots import postflop as _pf, postflop8 as _pf8  # noqa: E402

_pf8.FIXED_SAMPLES = 20


class _Clock:
    """A clock that ticks per look, for the heads-up equity (it has no fixed-count mode)."""
    t = 0.0

    @classmethod
    def perf_counter(cls):
        cls.t += 0.00004
        return cls.t


_pf.time = _Clock

BB = match8.BIG_BLIND
POOLS = {"$500K": 500_000, "$1M": 1_000_000, "$1.5M": 1_500_000}


def bbs(chips):
    return round(chips / BB, 2)


class NeedInput(BaseException):   # (the engine turns an Exception into a fold)
    def __init__(self, state):
        self.state = state


class Recorder:
    """A seat: the bot (or your moves so far), and every action written to the log."""

    def __init__(self, game, phys, bot=None):
        self.g, self.phys, self.bot = game, phys, bot
        self.result = None

    def new_hand(self, n, b):
        if self.bot is not None:
            self.bot.new_hand(n, b)

    def hand_over(self, r):
        self.result = r
        if self.bot is not None and self.g.final_run:
            self.bot.hand_over(r)

    def act(self, s):
        g = self.g
        if self.bot is None:              # you
            i = g.hero_i
            if i >= len(g.moves):
                raise NeedInput(s)
            mv = g.moves[i]
            g.hero_i += 1
        else:
            mv = self.bot.act(s)
        g.log(s, mv)
        g.trace.append((s, mv))
        return mv


class WebGame:
    def __init__(self):
        T.STUDY_TABLE = T.STUDY_TABLE or {}
        self.rng = random.Random()
        self.skip = True
        self.ft = None
        self.app = None
        self.analyst = None
        self.icm_stats = {"spots": 0, "right": 0, "lost": 0.0}
        self.history = []        # your last hands, for the review (newest last)
        self.reviewer = None     # a separate analyst for hand trees

    # -- a session --
    def new(self, table=None, pool="$1M", level_hands=30, skip=True, seed=None):
        self.rng = random.Random(seed)
        table = table or self.rng.randint(1, I.N_TABLES)
        pays = I.pool_payouts(POOLS.get(pool, 1_000_000) if isinstance(pool, str) else pool)
        self.ft = FT.FinalTable(int(table), pays, BB)
        self.ft.level_hands = int(level_hands)
        self.skip = bool(skip)
        FT.POLICY.update(on=True, icm=True, table=self.ft)
        self.bots = {x: T.make_study(T.STUDY, self.rng.randrange(10 ** 6)) for x in range(8)}
        self.advisor = T.make_study(T.STUDY, 4242)
        self.analyst = T.Analyst()
        app = T.Trainer.__new__(T.Trainer)        # the trainer's analyses, no window
        app.root, app.mode = tkstub.Dummy(), "Final table"
        app.coach_bot = app.advice = app.state = None
        app.analyst = self.analyst
        app.hand_sim = app.hand_sim_key = None
        app.hand_key = 0
        app.icm_spot = app.coach_host = None
        app.spot = {"scen": "-"}
        app.director = None
        app.ft = self.ft
        self.app = app
        self.icm_stats = {"spots": 0, "right": 0, "lost": 0.0}
        self.hand = None
        self.history = []
        return self.view()

    # -- a hand --
    def deal(self):
        ft = self.ft
        if ft.over or len(ft.alive()) < 2:
            return self.view()
        seats, b, stacks = ft.start_hand()
        self.hand = {"seats": seats, "button": b, "stacks": list(stacks),
                     "deck": new_deck(self.rng), "no": ft.hands, "note": ft.note,
                     "seed": self.rng.randrange(2 ** 31),
                     "skipped": False, "graded": []}
        self.moves = []
        self.meta = []           # per move of yours: the bot's play and your grade (None: auto)
        self.app.hand_key += 1
        return self.run()

    def log(self, s, mv):
        """One action, with what the table looks like after it."""
        me = next(p for p in s.players if p.seat == s.my_seat)
        a, amt = mv
        bet = me.bet
        if a == "raise" and s.min_raise_to:
            bet = max(s.min_raise_to, min(amt, s.max_raise_to))
            kind = "allin" if bet >= s.max_raise_to else ("raise" if s.opp_bet else "bet")
        elif a in ("call", "raise") and s.to_call:
            bet = me.bet + min(s.to_call, s.my_stack)
            kind = "allin" if s.to_call >= s.my_stack else "call"
        elif a == "fold" and s.to_call:
            kind = "fold"
        else:
            kind = "check"
        put = bet - me.bet
        self.events.append({
            "seat": self.phys_of[s.my_seat], "pos": s.position, "street": s.street,
            "board": list(s.board), "kind": kind, "label": T.describe(mv, s),
            "bet": bbs(bet), "stack": bbs(me.stack - put), "pot": bbs(s.pot + put)})

    def run(self):
        """Play the hand from the start with your moves so far, to your next decision or
        the end."""
        h, ft = self.hand, self.ft
        seats = h["seats"]
        self.phys_of = dict(enumerate(seats))
        self.hero_k = seats.index(0) if 0 in seats else None
        while True:
            self.events, self.trace, self.hero_i, self.final_run = [], [], 0, False
            random.seed(h["seed"])   # the same every replay: what happened stays the same
            recs = [Recorder(self, x, None if x == 0 else self.bots[x]) for x in seats]
            g = Game8(recs, big_blind=BB, ante=BB, ante_mode="bb", time_limit_s=1e9)
            try:
                self.final_run = True
                res = g.play_hand(list(h["deck"]), button=h["button"], hand_no=h["no"],
                                  stacks=list(h["stacks"]))
            except NeedInput as e:
                s = e.state
                if self.skip and self.clear_fold(s):
                    self.moves.append(("fold", 0))
                    self.meta.append(None)
                    h["skipped"] = True
                    continue
                self.decision = s
                return self.view()
            break
        self.decision = None
        r = next((x.result for x in recs if x.result), None)
        info = ft.finish_hand(seats, h["stacks"], res)
        h["result"] = self.result_view(r, res, info)
        self.keep_for_review()
        return self.view()

    def clear_fold(self, s):
        """A preflop hand you'd fold whatever the neighbours (the trainer's Skip folds)."""
        if s.street != "preflop" or not s.to_call or self.moves:
            return False
        grid = self.analyst.grid_for(s)
        hand = T.hand_type_of(s.hole)
        return grid.get(hand) == "Fold" and not T.close_spot(grid, hand, "Medium")

    def act(self, move):
        """Your move: grade it, then play on."""
        s = self.decision
        if s is None:
            return self.view()
        move = (move[0], int(move[1]))
        self.hand["graded"].append(self.grade(s, move))
        self.meta.append({"adv": self.last_adv, "graded": len(self.hand["graded"]) - 1})
        self.moves.append(move)
        return self.run()

    def grade(self, s, move):
        adv = self.last_adv = self.advisor_move(s)
        same = T.kind(move, s) == T.kind(adv, s)
        if same and T.kind(move, s) == "Raise":
            def to(m):
                return max(s.min_raise_to, min(m[1], s.max_raise_to))
            same = abs(to(move) - to(adv)) <= 0.25 * to(adv)
        out = {"you": T.describe(move, s), "hint": T.describe(adv, s)}
        icm = FT.coach_spot(s, self.ft)
        icm = dict(icm)              # (the grade fills in the solves)
        graded = FT.grade(icm, move, s)
        node = {"chosen": T.bucket(move, s), "verdict": same, "options": []}
        if graded is not None:
            right, g = graded
            st = self.icm_stats
            st["spots"] += 1
            st["right"] += right
            if not right:
                st["lost"] += abs(g)
            node["icm"] = (right, g, sum(icm["pays"]))
            if icm["kind"] == "mix":     # a game with traps: the solve's mix
                out["icm"] = FT.mix_verdict(icm, right, g)
            else:
                out["icm"] = FT.allin_verdict(icm, right, g)
            out["icm_right"] = bool(right)
        if same and graded is None:
            cap = T.CAPTIONS[0]
        else:
            try:
                n2 = self.analyst.analyse(s, None, adv)
                n2.update(chosen=T.bucket(move, s), verdict=same)
                if "icm" in node:
                    n2["icm"] = node["icm"]
                if n2["chosen"] not in [o[0] for o in n2["options"]]:
                    n2["options"].append((n2["chosen"], "", 0.0, ""))
                cap = T.caption_for(n2) or T.CAPTIONS[0]
            except Exception:
                cap = T.CAPTIONS[0] if same else T.CAPTIONS[2]
        out["caption"], out["colour"] = cap
        return out

    def advisor_move(self, s):
        self.advisor.plan3 = None
        adv = self.advisor.act(s)
        if s.street == "flop":
            sim = self.app.flop_sim(s)       # (a flop solve for this spot: its play is the hint,
            if sim is not None:              # and the review grades the flop against it)
                adv = sim.pick(s, BB) or adv
        return adv

    # -- the coach --
    def coach(self, deep=False):
        s = self.decision
        if s is None:
            return {"lines": []}
        app = self.app
        spot = FT.coach_spot(s, self.ft)
        if not deep:   # the quick coach: no "open / 3-bet instead" analysis (slow here)
            spot.setdefault("open2x", None)
            spot.setdefault("facing", None)
            spot.setdefault("vs3bet", None)
        app.icm_spot, app.advice, app.state = spot, self.advisor_move(s), s
        self.analyst.new_hand(self.hand["no"])
        app.open_icm_coach()
        top = tkstub.Toplevel.last
        kids = top.winfo_children() if top is not None else []
        cv = kids[0] if kids else None
        lines = []
        for it in (cv.items if cv is not None else []):
            font = it["font"] or ("", 10)
            size = font[1] if len(font) > 1 else 10
            lines.append({"text": it["text"], "colour": it["fill"],
                          "style": "title" if size >= 14 else "head" if size == 11 else
                          "bold" if len(font) > 2 else "text"})
        return {"lines": lines}

    # -- reviewing a hand: the hand tree --
    def keep_for_review(self):
        h = self.hand
        self.history.append({
            "no": h["no"], "trace": list(self.trace), "meta": list(self.meta),
            "phys_of": dict(self.phys_of), "seats": list(h["seats"]),
            "graded": list(h["graded"]), "result": h["result"], "tree": None,
            "start": {str(h["seats"][k]): bbs(st) for k, st in enumerate(h["stacks"])},
            "hole": self.hole(), "level": self.ft.blinds()})
        del self.history[:-20]

    def hole(self):
        h = self.hand
        hk = h["seats"].index(0) if 0 in h["seats"] else None
        return ([card_name(h["deck"][2 * hk]), card_name(h["deck"][2 * hk + 1])]
                if hk is not None else None)

    def review(self, no=None):
        """A hand you played (the last one unless `no`): every decision as the desktop
        trainer's hand tree draws it, each player's range and what the bots' strategy does
        with all of it, your grades, and the showdown."""
        if not self.history:
            return {"hands": [], "tree": None}
        e = next((x for x in self.history if x["no"] == no), self.history[-1])
        if e["tree"] is None:
            e["tree"] = self.build_tree(e)
        r = e["result"] or {}
        listing = [{"no": x["no"], "hole": x["hole"], "won": x["result"]["won"].get("0")
                    if x["result"] else None} for x in reversed(self.history)]
        return {"hands": listing, "no": e["no"], "tree": e["tree"], "hole": e["hole"],
                "start": e["start"], "level": e["level"], "board": r.get("board", []),
                "shown": r.get("shown", {}), "won": r.get("won", {}),
                "folded_to": r.get("folded_to", {}),
                "names": {str(x): self.ft.name(x) for x in e["seats"]}}

    def chart(self):
        """The preflop chart (the desktop's Chart window): what the bots' strategy does with
        every hand at each preflop decision of this hand, your current one included."""
        h = self.hand
        if h is not None and h.get("result") is None:
            s = self.decision
            pending = s if (s is not None and s.street == "preflop") else None
            e = {"no": h["no"], "trace": list(self.trace), "meta": list(self.meta),
                 "phys_of": dict(self.phys_of), "graded": list(h["graded"]), "result": None,
                 "hole": self.hole()}
            nodes = self.build_tree(e, pending=pending, preflop_only=True)
        elif self.history:
            e = self.history[-1]
            if e["tree"] is None:
                e["tree"] = self.build_tree(e)
            nodes = [n for n in e["tree"] if n["street"] == "preflop"]
        else:
            return {"nodes": []}
        return {"nodes": [n for n in nodes if n["grid"]], "hole": e["hole"],
                "live": h is not None and h.get("result") is None}

    def build_tree(self, e, pending=None, preflop_only=False):
        if self.reviewer is None:
            self.reviewer = T.Analyst()
        an = self.reviewer
        an.new_hand(e["no"])
        shown = dict((e["result"] or {}).get("folded_to", {}))
        shown.update((e["result"] or {}).get("shown", {}))
        out, k = [], 0
        steps = list(e["trace"]) + ([(pending, None)] if pending is not None else [])
        for s, mv in steps:
            if preflop_only and s.street != "preflop":
                break
            phys = e["phys_of"][s.my_seat]
            hero = phys == 0
            meta = None
            if hero and mv is None:          # your decision now: the bot's play, nothing chosen
                meta = {"adv": self.advisor_move(s), "graded": None}
            elif hero:
                meta = e["meta"][k] if k < len(e["meta"]) else None
                k += 1
            try:
                node = an.analyse(s, mv, meta["adv"] if meta else None, auto=hero and not meta)
            except Exception:
                node = None
            if not node:
                if mv is None:
                    continue
                if hero or T.describe(mv, s) != "Fold":   # (others' folds aren't drawn)
                    out.append(self.plain_node(s, mv, phys, hero))
                continue
            keys = [o[0] for o in node["options"]]
            opts = [{"label": lab or T.key_label(key), "pct": round(pct, 1), "col": col,
                     "chosen": key == node["chosen"], "bot": hero and key == node["bot"]}
                    for key, lab, pct, col in node["options"]]
            if mv is not None and node["chosen"] not in keys:   # your move: not in the strategy
                opts.append({"label": T.describe(mv, s), "pct": 0.0, "col": "#6b7385",
                             "chosen": True, "bot": False})
            grid = None
            if node.get("cells") and s.street == "preflop":
                grid = {hand: [[keys.index(key), round(f, 3)] for key, f in cell.items()
                               if key in keys]
                        for hand, cell in node["cells"].items()}
            item = {"street": s.street, "board": list(s.board), "seat": phys, "hero": hero,
                    "pos": s.position, "name": "You" if hero else self.ft.name(phys),
                    "pot": bbs(s.pot), "range": round(node["range"], 1), "options": opts,
                    "did": T.describe(mv, s) if mv is not None else None, "grid": grid,
                    "auto": hero and not meta, "now": mv is None, "spot": node.get("spot"),
                    "cards": (list(s.hole) if hero else shown.get(str(phys)))}
            if meta is not None and meta["graded"] is not None:
                g = e["graded"][meta["graded"]]
                item.update(caption=g["caption"], colour=g["colour"], hint=g["hint"],
                            icm=g.get("icm"), icm_right=g.get("icm_right"))
            out.append(item)
        return out

    def plain_node(self, s, mv, phys, hero):
        """A decision without the range analysis (it failed): just what was done."""
        return {"street": s.street, "board": list(s.board), "seat": phys, "hero": hero,
                "pos": s.position, "name": "You" if hero else self.ft.name(phys),
                "pot": bbs(s.pot), "range": None, "options": [], "did": T.describe(mv, s),
                "grid": None, "auto": False, "cards": list(s.hole) if hero else None}

    # -- what the page shows --
    def result_view(self, r, res, info):
        h = self.hand
        seats = h["seats"]
        shown = {self.phys_of[k]: v for k, v in (r or {}).get("shown", {}).items()}
        board = list((r or {}).get("board", []))
        hist = (r or {}).get("history", [])
        last = max((["preflop", "flop", "turn", "river"].index(st) for st, *_ in hist),
                   default=0)
        dealt = [0, 3, 4, 5][last]
        runout = None
        if len(shown) >= 2 and len(board) > dealt:
            try:
                eq = T.runout_equities(shown, board, dealt)
                runout = {"from": dealt, "eq": {str(k): {str(s): round(v, 3)
                                                         for s, v in d.items()}
                                                for k, d in eq.items()}}
            except Exception:
                runout = None
        if not shown:
            board = board[:dealt]
        return {"board": board, "shown": {str(k): v for k, v in shown.items()},
                "folded_to": {str(k): v for k, v in self.folded_to(shown).items()},
                "won": {str(seats[k]): bbs(v) for k, v in enumerate(res)},
                "runout": runout,
                "busts": [{"seat": x, "name": self.ft.name(x), "place": p, "prize": z}
                          for x, p, z in info["busts"]]}

    def folded_to(self, shown):
        """When you folded to a bet or raise: that player's cards (shown to you after the
        hand). Not for hands folded for you (Skip folds), nor a fold to just the blinds."""
        h = self.hand
        out, k = {}, 0
        for s, mv in self.trace:
            if self.phys_of[s.my_seat] != 0:
                continue
            auto = k >= len(self.meta) or self.meta[k] is None
            k += 1
            if mv[0] != "fold" or not s.to_call or auto:
                continue
            raises = [seat for st, seat, a, _ in s.history if st == s.street and a == "raise"]
            if not raises:
                continue
            x = raises[-1]
            phys = self.phys_of[x]
            if phys not in shown:
                out[phys] = [card_name(h["deck"][2 * x]), card_name(h["deck"][2 * x + 1])]
        return out

    def view(self):
        ft = self.ft
        if ft is None:
            return {"ready": False}
        h = self.hand
        eq = ft.equities()
        al = ft.alive()
        players = []
        for x in range(8):
            out = ft.out.get(x)
            players.append({
                "seat": x, "name": ft.name(x), "stack": bbs(ft.stacks[x]),
                "equity": round(eq.get(x, 0.0)), "out": out[0] if out else None,
                "prize": out[1] if out else None})
        v = {"ready": True, "table": ft.table_no if hasattr(ft, "table_no") else None,
             "hands": ft.hands, "blinds": ft.blinds(), "left_in_level": ft.left_in_level(),
             "left": len(al), "pays": ft.pays9, "players": players,
             "big_blind_chips": T.I_WSOP_BB(ft.level),
             "over": ft.over, "icm_stats": self.icm_stats}
        if h is not None:
            seats = h["seats"]
            n = len(seats)
            order = ([h["button"], (h["button"] + 1) % 2] if n == 2 else
                     [(h["button"] + 3 + i) % n for i in range(n)])
            pos = dict(zip(order, T.position_names(n)))
            hk = seats.index(0) if 0 in seats else None
            v["hand"] = {
                "no": h["no"], "note": h["note"], "skipped": h["skipped"],
                "seats": seats, "button": seats[h["button"]],
                "positions": {str(seats[k]): pos[k] for k in range(n)},
                "start": {str(seats[k]): bbs(h["stacks"][k]) for k in range(n)},
                "hole": ([card_name(h["deck"][2 * hk]), card_name(h["deck"][2 * hk + 1])]
                         if hk is not None else None),
                "events": self.events, "graded": h["graded"],
                "result": h.get("result")}
            s = self.decision
            if s is not None:
                adv = self.advisor_move(s)
                sizes = T.Trainer.sizes(self.app, s)
                opts = []
                if s.to_call:
                    opts.append({"label": "Fold", "move": ["fold", 0], "kind": "fold"})
                    opts.append({"label": T.describe(("call", 0), s), "move": ["call", 0],
                                 "kind": "call"})
                else:
                    opts.append({"label": "Check", "move": ["check", 0], "kind": "check"})
                if s.min_raise_to:
                    for to in sizes:
                        opts.append({"label": T.describe(("raise", to), s),
                                     "move": ["raise", to], "kind": "raise"})
                    opts.append({"label": f"All-in {T.bb(s.max_raise_to)}",
                                 "move": ["raise", s.max_raise_to], "kind": "allin"})
                spot = FT.coach_spot(s, ft)
                v["decision"] = {
                    "street": s.street, "board": list(s.board), "pot": bbs(s.pot),
                    "to_call": bbs(min(s.to_call, s.my_stack)), "options": opts,
                    "min": bbs(s.min_raise_to) if s.min_raise_to else None,
                    "max": bbs(s.max_raise_to), "hint": T.describe(adv, s),
                    "hint_move": list(adv), "icm_kind": spot["kind"]}
        return v


GAME = WebGame()


BASE = {}   # the ranges the page ships (the author's sims), before any of your own charts


def load_ranges(path):
    """The study table (your sims) from the JSON the page ships."""
    global BASE
    d = json.loads(Path(path).read_text())
    BASE = {spot: {float(dep): acts for dep, acts in deps.items()} for spot, deps in d.items()}
    T.STUDY_TABLE = {spot: dict(deps) for spot, deps in BASE.items()}
    try:
        T.load_flop_sims()          # (the flop solves in the bundle: read when used)
    except OSError:
        pass
    return len(T.STUDY_TABLE)


def set_charts(folder):
    """Your own study charts, loaded on your device only (they never leave it): the files
    in `folder` (range files named like my_ranges ones, and/or zips of them), on top of the
    shipped ranges, as the desktop trainer does with study_ranges/. None removes them.
    The table is changed in place, so the bots already at the table use them at once."""
    import contextlib
    import io
    import shutil
    import zipfile
    from solver.import_ranges8 import load_folder
    flat = Path("/tmp/my_charts_flat")
    shutil.rmtree(flat, ignore_errors=True)
    flat.mkdir(parents=True)
    files = 0

    def keep(name, data):
        nonlocal files
        name = Path(name).name
        if "__" in Path(name).stem and not name.startswith((".", "_")):
            (flat / name).write_bytes(data)
            files += 1

    if folder:
        for f in sorted(Path(folder).iterdir()):
            if f.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(f) as z:
                        for info in z.infolist():
                            if not info.is_dir() and "__MACOSX" not in info.filename:
                                keep(info.filename, z.read(info))
                except zipfile.BadZipFile:
                    pass
            elif f.is_file():
                keep(f.name, f.read_bytes())
    study = {}
    if files:
        with contextlib.redirect_stdout(io.StringIO()):
            study = load_folder(flat)
    T.STUDY_TABLE.clear()
    for spot, deps in BASE.items():
        T.STUDY_TABLE[spot] = dict(deps)
    for spot, deps in study.items():
        T.STUDY_TABLE.setdefault(spot, {}).update(deps)
    if GAME.analyst is not None:
        GAME.analyst.grid_cache.clear()
    charts = sum(len(acts) for deps in study.values() for acts in deps.values())
    return {"files": files, "charts": charts, "spots": len(study)}


def handle(msg):
    """One command from the page (JSON in, JSON out)."""
    m = json.loads(msg) if isinstance(msg, str) else msg
    cmd = m.get("cmd")
    g = GAME
    if cmd == "new":
        out = g.new(m.get("table"), m.get("pool", "$1M"), m.get("level_hands", 30),
                    m.get("skip", True), m.get("seed"))
    elif cmd == "deal":
        out = g.deal()
    elif cmd == "act":
        out = g.act(m["move"])
    elif cmd == "coach":
        out = g.coach(bool(m.get("deep")))
    elif cmd == "chart":
        out = g.chart()
    elif cmd == "review":
        out = g.review(m.get("no"))
    elif cmd == "charts":
        out = set_charts(m.get("folder"))
    elif cmd == "skip":
        g.skip = bool(m.get("on"))
        out = {"skip": g.skip}
    else:
        out = {"error": f"unknown command {cmd}"}
    return json.dumps(out)
