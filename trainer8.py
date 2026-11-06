"""Poker practice trainer: play 8-max (or shorter) tournament hands against the bots in a
window, and see after every decision what the bot would have done.

    python trainer8.py                         # you + 7 contender8 bots
    python trainer8.py --players 6
    python trainer8.py --bots starter8         # easier opponents

Modes (switch at the top):
  Play   full hands at a normal table; every hand starts with the same stacks (choose the
         depth in the Coach tab, or --depth)
  Drill  fast preflop practice: random stack depths, you only decide preflop in the spot
         types you pick (any / facing an open / facing a 3-bet / facing a 4-bet)
  Spots  one exact spot over and over: pick the situation, your position, the villain's
         position and the stack depth (e.g. BB defends vs a CO open, 40 bb, play the flop).
         Everyone else folds; the villain only opens / calls / 3-bets with hands the bot
         really would, and in "defend" / "open -> flop" spots you start on the flop with
         a hand the bot really plays that way.

Difficulty (Drill and Spots): Medium only deals hands that are close decisions preflop: a
hand a pip or two away (kicker up / down 1-2, top card up / down 1) is played
differently, so never an obvious fold like 92o in the HJ or an obvious raise like AA.
Expert: only the hands right on the edge (kicker 1 pip).

Fast fold (top bar): after you fold, the rest of the hand plays instantly and the next hand
is dealt by itself; press R (Review this hand) to keep it up and study the hand tree.
Playable only (Play mode): you're only dealt hands that are opens, or close to the edge of
the opening range (or that continue / are close facing a raise). Obvious folds such as 73o
or T2o are never dealt to you; your chips don't change on them.

Replay hand (Z), after any hand: the same cards, seats and stacks again, and the bots
make the same choices until you play differently. Replays don't count in your results.

At the end of every hand all the bots' hole cards are shown (folded hands muted), at the
table and on their cards in the hand tree.

Every decision gets a grade: the bot's play = "Elite pro decision"; otherwise by how your
option ranks (by simulated EV, or by how often the bot's range takes it while the EVs are
still coming in): second best "Shitreg decision", third "Dustreg decision", the worst
option "Fish decision".

Keys: F fold, C check/call, 1 2 3 bet sizes, A all-in, R review, Z replay, Space next hand.
The "bot's play" is your contender8 bot's choice in your seat: a reference to learn its
strategy from, not a solver result. Everything runs on your computer; nothing connects
anywhere. Online tournament antes (12.5% of the big blind each), no rake.
"""

import argparse
import math
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards import card, evaluate, name  # noqa: E402
from engine import new_deck  # noqa: E402
from engine8 import Game8, position_names  # noqa: E402
import match8  # noqa: E402
from bots.preflop8 import COMBOS, ORDER  # noqa: E402
import final_table as FTL  # noqa: E402
import flop_sims  # noqa: E402

BB = match8.BIG_BLIND
W, H = 1280, 760          # the window (it can be made bigger: see Trainer.apply_size)
TABLE_W = 920            # the right-hand panel starts here
BASE_W, BASE_H, BASE_TABLE_W = W, H, TABLE_W
TOP, STRIP, BOTTOM = 52, 50, 128   # top bar, hand timeline, action bar heights

C = {  # colours: graphite with violet (you) and amber (player to act)
    "bg": "#0f1115", "panel": "#14171d", "card": "#1a1e26", "card2": "#222833",
    "line": "#2b3240", "text": "#eceff4", "dim": "#8b93a3", "faint": "#525b6c",
    "accent": "#8b7cf6", "act": "#f5a524", "gold": "#f2c14e", "shadow": "#07080b",
    "table": "#13161b", "table_line": "#2e3441", "seat": "#181c23",
    "fold": "#465166", "call": "#2fb57b", "r1": "#d9534f", "r2": "#e2683d", "r3": "#ea8a3a",
    "allin": "#b3263e", "good": "#2fbf77", "bad": "#ee8a3a",
}
SUIT = {"s": ("\u2660", "#4a5162"), "h": ("\u2665", "#c9303f"),
        "d": ("\u2666", "#2e6bd3"), "c": ("\u2663", "#2b9658")}  # four-colour deck
SUIT_TV = {"s": ("\u2660", "#15171c"), "h": ("\u2665", "#d0142c"),
           "d": ("\u2666", "#1559c7"), "c": ("\u2663", "#1d8a46")}  # on a white face
DRILL_DEPTHS = {"10-25": [10, 12, 15, 18, 20, 25], "25-50": [25, 30, 35, 40, 50],
                "50-100": [50, 60, 75, 100],
                "All": [10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100]}
SPOT_DEPTHS = [10, 15, 20, 25, 30, 40, 50, 60, 80, 100]
SCENARIOS = ["Facing open", "Defend \u2192 flop", "Open \u2192 flop", "Open vs 3-bet",
             "First in", "Flop sim"]
SHORT = {"Facing open": "vs open", "Defend \u2192 flop": "SRP", "Open \u2192 flop": "SRP",
         "Open vs 3-bet": "vs 3-bet", "First in": "first in", "Flop sim": "flop sim"}
AVATARS = Path(__file__).resolve().parent / "avatars"   # Final table players' pictures
FLOP_SIMS = []  # Pio flop solves (postflop/, see flop_sims.py), loaded at start
# Fonts: replaced by the best ones installed (pick_font). HEAD is for numbers, buttons and
# titles, TEXT for everything else: friendly humanist faces (Inter if installed, else
# Segoe UI, which every Windows 10 / 11 PC has).
HEAD, HEAD_BOLD, TEXT, MONO = "Segoe UI", True, "Segoe UI", "Consolas"


def pick_font(root):
    global HEAD, HEAD_BOLD, TEXT, MONO
    fams = set(tkfont.families(root))

    def first(names, default):
        return next((f for f in names if f in fams), default)

    TEXT = first(("Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text",
                  "Helvetica Neue", "Roboto", "Ubuntu", "DejaVu Sans"), "TkDefaultFont")
    semibold = first(("Inter SemiBold", "Inter Semi Bold", "Segoe UI Semibold",
                      "SF Pro Display Semibold"), None)
    if semibold:
        HEAD, HEAD_BOLD = semibold, False
    else:  # the body font in bold
        HEAD, HEAD_BOLD = TEXT, True
    MONO = first(("Cascadia Mono", "JetBrains Mono", "Consolas", "Menlo", "DejaVu Sans Mono"),
                 "TkFixedFont")


_MEASURE = {}


_WIDTHS = {}


def text_w(text, font):
    """Width in pixels of text in a font tuple (cached font objects and widths)."""
    w = _WIDTHS.get((text, font))
    if w is not None:
        return w
    if len(_WIDTHS) > 5000:
        _WIDTHS.clear()
    f = _MEASURE.get(font)
    if f is None:
        fam, size, *rest = font
        f = _MEASURE[font] = tkfont.Font(family=fam, size=size,
                                         weight="bold" if rest else "normal")
    w = _WIDTHS[(text, font)] = f.measure(text)
    return w


def F(size, bold=False):
    """Body text font."""
    return (TEXT, size, "bold") if bold else (TEXT, size)


def FH(size):
    """Heading / number font."""
    return (HEAD, size, "bold") if HEAD_BOLD else (HEAD, size)


def shade(hex_colour, f):
    """Lighter (f > 0) or darker (f < 0) version of a colour."""
    if not hex_colour.startswith("#"):
        hex_colour = {"white": "#ffffff", "black": "#000000"}.get(hex_colour, "#ffffff")
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    if f >= 0:
        r, g, b = (int(v + (255 - v) * f) for v in (r, g, b))
    else:
        r, g, b = (int(v * (1 + f)) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


FELT = ["#0c3127", "#0e372c", "#103d31", "#124336", "#14493b", "#164f40", "#185544",
        "#1a5a48"]   # the felt, rail edge to centre
SEAT_COLOURS = ["#8b7cf6", "#e0784f", "#3fa7d6", "#e2b93b", "#5cc28f", "#d65c8a", "#6d8de0",
                "#c98b4b"]


def initials(name):
    if name == "You":
        return "YOU"
    parts = [p for p in name.replace(".", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() if parts else "?"


def money_k(v):
    """$12,345 (whole dollars)."""
    return f"${v:,.0f}"


def ordinal(k):
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def bb(chips):
    v = chips / BB
    return f"{v:.2f}".rstrip("0").rstrip(".")


def describe(move, s):
    a, amt = move
    if a == "raise" and s.min_raise_to:
        to = max(s.min_raise_to, min(amt, s.max_raise_to))
        if to >= s.max_raise_to:
            return "All-in"
        return f"{'Raise to' if s.opp_bet else 'Bet'} {bb(to)}"
    if a in ("call", "raise"):
        return f"Call {bb(min(s.to_call, s.my_stack))}" if s.to_call else "Check"
    if a == "check" or not s.to_call:
        return "Check"
    return "Fold"


def kind(move, s):
    d = describe(move, s)
    return "Raise" if d.startswith(("Raise", "Bet")) else d.split()[0]


def raises_before(s):
    return sum(1 for st, _, a, _ in s.history if st == "preflop" and a == "raise")


def seat_positions(n, button):
    """Same seating as the engine: seat -> position name."""
    if n == 2:
        order = [button, (button + 1) % 2]
    else:
        order = [(button + 3 + k) % n for k in range(n)]
    names = position_names(n)
    return {seat: names[k] for k, seat in enumerate(order)}


class Redeal(BaseException):
    """Cards that don't fit the chosen spot: throw the hand away and deal again."""


# ---------- spot setup (Spots mode) ----------

class Director:
    """Steers the preflop action into the chosen spot."""

    def __init__(self, scenario, villain_seat):
        self.scen, self.villain = scenario, villain_seat

    def hero_preflop(self, s, adv):
        """Your preflop move if it's played for you, None to ask you; may Redeal."""
        n = raises_before(s)
        sc = self.scen
        if sc == "Defend → flop" and n == 1:
            if adv[0] == "call":
                return adv
            raise Redeal
        if sc in ("Open → flop", "Open vs 3-bet") and n == 0:
            if s.position == "SB":  # the study SB mostly limps: open with a raise-or-fold SB
                adv = self.raiser().act(s)
            if adv[0] == "raise" and adv[1] < s.max_raise_to:
                return adv
            raise Redeal
        return None

    def raiser(self):
        """A study bot whose SB raises or folds first in (no limps, no imported sims)."""
        if getattr(self, "sb_raiser", None) is None:
            self.sb_raiser = make_study(STUDY + ";limp_game=0", 4242)
            self.sb_raiser.table = {}
        return self.sb_raiser

    def other_preflop(self, s, bot):
        """A bot's preflop move: fold, or the villain's move if it fits the spot."""
        sc = self.scen
        hero_acted = any(seat == 0 for st, seat, _, _ in s.history if st == "preflop")
        if sc == "First in":
            if not hero_acted:
                return ("fold", 0) if s.to_call else ("check", 0)
            return bot.act(s)
        if s.my_seat != self.villain:
            return ("fold", 0) if s.to_call else ("check", 0)
        n = raises_before(s)
        if sc in ("Facing open", "Defend → flop") and n == 0 and s.position == "SB":
            # The study SB mostly limps; for a drill against its open, use a raise-or-fold SB.
            bot = self.raiser()
        move = bot.act(s)
        if sc in ("Facing open", "Defend → flop") and n == 0:
            ok = move[0] == "raise" and (sc == "Facing open" or move[1] < s.max_raise_to)
            if not ok:
                raise Redeal
        elif sc == "Open → flop" and n == 1:
            if move[0] != "call":
                raise Redeal
        elif sc == "Open vs 3-bet" and n == 1:
            if move[0] != "raise":
                raise Redeal
        return move


def spot_problem(n, scen, hero, villain):
    """Why this spot can't be dealt, or None if it can."""
    order = position_names(n)
    if hero not in order:
        return f"{hero} isn't at a {n}-handed table"
    if scen == "First in":
        return "The big blind can't be first in" if hero == "BB" else None
    if villain not in order:
        return f"{villain} isn't at a {n}-handed table"
    if villain == hero:
        return "Pick a villain in another seat"
    hi, vi = order.index(hero), order.index(villain)
    if scen in ("Facing open", "Defend → flop") and vi > hi:
        return f"The opener must act before you: pick a villain before {hero}"
    if scen in ("Open → flop", "Open vs 3-bet") and vi < hi:
        return f"The villain must act after you: pick a seat after {hero}"
    return None


# ---------- decision tree: what the bot does with each player's whole range ----------

ACT_COLOURS = {"Fold": "#4f6fa6", "Check": "#2ea873", "Call": "#2ea873",
               "All-in": "#c8478a"}
RAISE_COLOURS = ["#e3b23c", "#e48b3b", "#d9663f"]
ORDER_RANK = {"Fold": 0, "Check": 1, "Call": 2, "Bet": 3, "Raise": 3, "All-in": 5}
SUIT_ORDER = "shdc"


def sim_node(sim, s, move, adv, auto):
    """A hand-tree card for a flop decision from a Pio solve: the solve's range-wide
    frequencies, each hand type's mix, and this player's own cards' mix."""
    node_id = sim.node_for(s, BB)
    if node_id is None:
        return None
    n = sim.nodes[node_id]
    dead = getattr(sim, "board_dead", None) or set(s.board)
    freq = sim.range_mix(node_id, dead)
    keys = {}
    for code in n["actions"]:
        keys[code] = bucket(sim.move(s, code, BB), s)
    totals = {}
    for code, f in freq.items():
        totals[keys[code]] = totals.get(keys[code], 0.0) + f
    ordered = sorted(totals, key=lambda k: (ORDER_RANK.get(k[0], 4), k[1] or 0))
    raise_keys = [k for k in ordered if k[1] is not None]
    options = []
    for k in ordered:
        col = (RAISE_COLOURS[min(raise_keys.index(k), 2)] if k in raise_keys
               else ACT_COLOURS.get(k[0], "#888888"))
        options.append((k, key_label(k), 100.0 * totals[k], col))
    side = n["player"]
    w = sim.d["ranges"][side]
    cells, wts, types = {}, {}, set()
    for i, wt in enumerate(w):
        h = sim.hands[i]
        if wt <= 0 or h[:2] in dead or h[2:] in dead:
            continue
        row_tot = sum(row[i] for row in n["strategy"])
        if row_tot <= 0:
            continue
        t = hand_type_of([h[:2], h[2:]])
        types.add(t)
        cell = cells.setdefault(t, {})
        for code, row in zip(n["actions"], n["strategy"]):
            cell[keys[code]] = cell.get(keys[code], 0.0) + wt * row[i] / row_tot
        wts[t] = wts.get(t, 0.0) + wt
    for t, cell in cells.items():
        for k in cell:
            cell[k] /= wts[t]
    mine = sim.mix(node_id, list(s.hole)) or {}
    own = {}
    for code, f in mine.items():
        own[keys[code]] = own.get(keys[code], 0.0) + f
    other = "IP" if side == "OOP" else "OOP"
    opp_pos = sim.ip if side == "OOP" else sim.oop
    opp_types = sorted({hand_type_of([sim.hands[i][:2], sim.hands[i][2:]])
                        for i, wt in enumerate(sim.d["ranges"][other]) if wt > 0})
    combos = sum(wt for i, wt in enumerate(w) if wt > 0 and sim.hands[i][:2] not in dead and
                 sim.hands[i][2:] not in dead) / 1000
    return {"seat": s.my_seat, "pos": s.position, "hero": s.my_seat == 0, "street": s.street,
            "board": list(s.board), "pot": s.pot, "options": options,
            "chosen": bucket(move, s) if move is not None else None,
            "bot": bucket(adv, s) if adv is not None else None, "auto": auto, "verdict": None,
            "range": 100 * combos / 1326, "grid": None, "hole": list(s.hole),
            "spot": f"{s.position} \u00b7 flop (Pio solve{sim_label(sim, s)})", "cells": cells,
            "range_hands": sorted(types), "opps": [(opp_pos, opp_types)], "sim_mix": own}


def describe_hand(hole, board):
    """'top set', 'top pair (good kicker)', 'flush draw', ... for two cards on a board."""
    from cards import category, CATEGORIES
    ranks = "23456789TJQKA"
    hv = [ranks.index(c[0]) for c in hole]
    bv = sorted((ranks.index(c[0]) for c in board), reverse=True)
    cat = category(evaluate([card(c) for c in hole + board]))
    words = []
    if cat == 1:   # one pair
        if hv[0] == hv[1]:
            words.append("an overpair" if hv[0] > bv[0] else "an underpair" if hv[0] < bv[-1]
                         else "a middle pocket pair")
        else:
            paired = [v for v in hv if v in bv]
            if paired:
                top = max(paired)
                kick = min(hv) if hv[0] != hv[1] else hv[0]
                kick = [v for v in hv if v != top][0] if any(v != top for v in hv) else top
                if top == bv[0]:
                    words.append("top pair, " + ("good kicker" if kick >= 9 else "weak kicker"))
                elif top == bv[1]:
                    words.append("second pair")
                else:
                    words.append("a low pair")
            else:
                words.append("a pair on the board")
    elif cat == 3:
        words.append("a set" if hv[0] == hv[1] else "trips")
    else:
        name = CATEGORIES[cat]
        if not cat:
            words.append("no pair")
        elif name in ("quads", "two pair"):     # (no article: 'quads', not 'a quads')
            words.append(name)
        else:
            words.append(f"{'an' if name[0] in 'aeiou' else 'a'} {name}")
    if cat < 5 and len(board) < 5:
        suits = [c[1] for c in hole + board]
        if any(suits.count(x) == 4 and x in (hole[0][1], hole[1][1]) for x in "cdhs"):
            words.append("a flush draw")
        vals = set(hv + bv)
        if 12 in vals:
            vals.add(-1)
        for lo in range(-1, 9):
            window = set(range(lo, lo + 5))
            if len(window & vals) == 4 and window & set(hv) and cat < 4:
                words.append("a straight draw")
                break
    return " with ".join(words) if len(words) < 3 else f"{words[0]} with {words[1]} and {words[2]}"


def sim_label(sim, s):
    """' of Ks7d2c' when a nearest solved flop stands in for the real one."""
    if sorted(sim.board) == sorted(s.board):
        return ""
    return f" of {''.join(sim.board)}"


def combo_for(h, dead):
    """Two card names for hand type h (e.g. 'AKs') that avoid the dead cards."""
    r1, r2 = h[0], h[1]
    if len(h) == 2:
        free = [s for s in SUIT_ORDER if r1 + s not in dead]
        return [r1 + free[0], r1 + free[1]] if len(free) >= 2 else None
    for s1 in SUIT_ORDER:
        for s2 in SUIT_ORDER:
            if (s1 == s2) != (h[2] == "s"):
                continue
            a, b = r1 + s1, r2 + s2
            if a not in dead and b not in dead:
                return [a, b]
    return None


def bucket(move, s):
    """(word, size in bb or None) for a move: the tree groups moves this way."""
    d = describe(move, s)
    if d.startswith(("Raise", "Bet")):
        to = max(s.min_raise_to, min(move[1], s.max_raise_to)) / BB
        size = round(to, 1) if to < 10 else round(to * 2) / 2
        return ("Raise" if s.opp_bet else "Bet", size)
    return (d.split()[0], None)


def family(key):
    return "Raise" if key[0] in ("Raise", "Bet") else key[0]


def key_label(key):
    word, size = key
    return f"{word} {size:g}" if size is not None else word


PIPS = "23456789TJQKA"


def neighbours(hand, level):
    """Hands a pip or two away: the kicker up or down (1-2 pips on Medium, 1 on Expert)
    and the top card up or down one. (Not the suited / offsuit twin: Q4s opening doesn't
    make Q4o a close decision.)"""
    k = 1 if level == "Expert" else 2
    out = []
    if len(hand) == 2:
        i = PIPS.index(hand[0])
        for d in range(1, k + 1):
            for j in (i - d, i + d):
                if 0 <= j < 13:
                    out.append(PIPS[j] * 2)
        return out
    hi, lo, kind_ = PIPS.index(hand[0]), PIPS.index(hand[1]), hand[2]
    for d in range(1, k + 1):
        for j in (lo - d, lo + d):
            if 0 <= j < hi:
                out.append(PIPS[hi] + PIPS[j] + kind_)
    if level != "Expert":
        for j in (hi - 1, hi + 1):
            if lo < j < 13:
                out.append(PIPS[j] + PIPS[lo] + kind_)
    return out


def close_spot(grid, hand, level):
    """True if this hand is a real decision for the bot here: a hand a pip or two away
    (see neighbours) is played differently. 73o or T2o, where every neighbour is a fold
    too, isn't; nor is AA, where every neighbour raises."""
    if hand not in grid:
        return True
    mine = grid[hand]
    return any(grid.get(n, mine) != mine for n in neighbours(hand, level))


class Analyst:
    """For the tree: runs a separate copy of the bot over every hand a player could still
    have, so each decision shows the bot's whole-range strategy (and narrows the range by
    the action actually taken)."""

    def __init__(self):
        self.bot = make_study(STUDY, 777)
        self.rng = random.Random(99)
        self.grid_cache = {}
        self.new_hand(0)

    def new_hand(self, hand_no):
        self.ranges, self.plans = {}, {}
        self.pending = []          # decisions not yet analysed (before you see the hand)
        self.last_hero = None      # (per-hand actions) for your latest decision
        self.rng.seed(hand_no)
        self.bot.new_hand(hand_no, False)

    def grid_for(self, s):
        """What the bot does with every hand in this exact spot (cached: in Spots mode the
        same spot comes up again and again). {hand: action family}."""
        key = (FTL.POLICY["on"], s.position, len(s.players), s.my_stack, s.my_bet, s.pot,
               tuple((seat, a, amt) for st, seat, a, amt in s.history if st == "preflop"))
        hit = self.grid_cache.get(key)
        if hit is not None:
            return hit
        out = {}
        for h in ORDER:
            combo = combo_for(h, set())
            self.bot.plan3 = None
            try:
                out[h] = family(bucket(self.bot.act(replace(s, hole=combo)), s))
            except Exception:
                continue
        if len(self.grid_cache) > 2000:
            self.grid_cache.clear()
        self.grid_cache[key] = out
        return out

    def record(self, s, move, adv=None, auto=False):
        self.pending.append((s, move, adv, auto))

    def flush(self):
        nodes = [self.analyse(*p) for p in self.pending]
        self.pending = []
        return [n for n in nodes if n]

    def analyse(self, s, move, adv=None, auto=False):
        d = getattr(self, "director", None)
        sim = d.sim if (d is not None and hasattr(d, "sim")) else getattr(self, "hand_sim", None)
        if sim is not None and s.street == "flop":
            node = sim_node(sim, s, move, adv, auto)
            if node is not None:
                return node
        seat, hero = s.my_seat, s.my_seat == 0
        if move is not None and not hero and describe(move, s) == "Fold":
            return None  # folds by others aren't drawn (they leave the hand)
        hands = self.ranges.get(seat, ORDER)
        exact_mix = FTL.game_chart(s) if (s.street == "preflop" and FTL.POLICY.get("on")) else None
        if exact_mix is not None:
            return self.analyse_mix(s, move, adv, auto, exact_mix)
        if s.street != "preflop" and len(hands) > 36:
            hands = self.rng.sample(list(hands), 36)
        dead = set(s.board)
        preflop = s.street == "preflop"
        samples = 3 if preflop else 1  # the bot mixes some hands (bluffs): show how often
        weights, per_hand, grid = {}, {}, {}
        for h in hands:
            combo = combo_for(h, dead)
            if not combo:
                continue
            cell = {}
            for _ in range(samples):
                self.bot.plan3 = self.plans.get((seat, h))
                try:
                    mv = self.bot.act(replace(s, hole=combo))
                except Exception:
                    continue
                k = bucket(mv, s)
                cell[k] = cell.get(k, 0) + 1.0 / samples
                per_hand.setdefault(h, k)
            self.plans[(seat, h)] = getattr(self.bot, "plan3", None)
            for k, f in cell.items():
                weights[k] = weights.get(k, 0) + COMBOS[h] * f
            grid[h] = cell
        # At most two raise sizes: fold the rarer sizes into the nearest kept one.
        raises = sorted((k for k in weights if k[1] is not None), key=lambda k: -weights[k])
        for k in raises[2:]:
            near = min(raises[:2], key=lambda r: abs(r[1] - k[1]))
            weights[near] += weights.pop(k)
            for h, v in per_hand.items():
                if v == k:
                    per_hand[h] = near
            for cell in grid.values():
                if k in cell:
                    cell[near] = cell.get(near, 0) + cell.pop(k)
        total = sum(weights.values()) or 1
        keys = sorted(weights, key=lambda k: (ORDER_RANK[k[0]], k[1] or 0))
        raise_keys = [k for k in keys if k[1] is not None]
        options = []
        for k in keys:
            col = (RAISE_COLOURS[min(raise_keys.index(k), 2)] if k in raise_keys
                   else ACT_COLOURS.get(k[0], "#888888"))
            options.append((k, key_label(k), 100.0 * weights[k] / total, col))
        chosen = bucket(move, s) if move is not None else None
        if chosen is not None and chosen not in weights and chosen[1] is not None and raise_keys:
            chosen = min(raise_keys, key=lambda r: abs(r[1] - chosen[1]))
        node = {"seat": seat, "pos": s.position, "hero": hero, "street": s.street,
                "board": list(s.board), "pot": s.pot, "options": options, "chosen": chosen,
                "bot": bucket(adv, s) if adv is not None else None, "auto": auto,
                "verdict": None, "range": sum(COMBOS[h] for h in self.ranges.get(seat, ORDER))
                / 13.26, "grid": grid if preflop else None, "hole": list(s.hole),
                "spot": f"{s.position} \u00b7 " + (["unopened", "vs open", "vs 3-bet", "vs 4-bet",
                                                     "vs 5-bet"][min(raises_before(s), 4)]
                                                    if preflop else s.street)}
        # For the details window: every hand's action mix, and who it's up against.
        node["cells"] = grid
        node["range_hands"] = list(self.ranges.get(seat, ORDER))
        node["opps"] = [(p.position, list(self.ranges.get(p.seat, ORDER)))
                        for p in s.players if p.seat != seat and not p.folded]
        if move is not None:
            self.narrow(seat, per_hand, chosen)
        else:
            self.last_hero = per_hand
        return node

    def analyse_mix(self, s, move, adv, auto, exact_mix):
        """A decision whose whole strategy is known exactly (the SB vs BB game's solve):
        {hand: {move: freq}} instead of sampling the bot."""
        seat, hero = s.my_seat, s.my_seat == 0
        hands = self.ranges.get(seat, ORDER)
        weights, grid, per_hand = {}, {}, {}
        for h in hands:
            cell = {}
            for mv, f in exact_mix.get(h, {}).items():
                if f <= 0:
                    continue
                k = bucket(mv, s)
                cell[k] = cell.get(k, 0.0) + f
            if not cell:
                continue
            grid[h] = cell
            per_hand[h] = max(cell, key=cell.get)
            for k, f in cell.items():
                weights[k] = weights.get(k, 0.0) + COMBOS[h] * f
        total = sum(weights.values()) or 1
        keys = sorted(weights, key=lambda k: (ORDER_RANK[k[0]], k[1] or 0))
        raise_keys = [k for k in keys if k[1] is not None]
        options = []
        for k in keys:
            col = (RAISE_COLOURS[min(raise_keys.index(k), 2)] if k in raise_keys
                   else ACT_COLOURS.get(k[0], "#888888"))
            label = "Limp" if (k[0] == "Call" and not raises_before(s)) else key_label(k)
            options.append((k, label, 100.0 * weights[k] / total, col))
        chosen = bucket(move, s) if move is not None else None
        node = {"seat": seat, "pos": s.position, "hero": hero, "street": s.street,
                "board": list(s.board), "pot": s.pot, "options": options, "chosen": chosen,
                "bot": bucket(adv, s) if adv is not None else None, "auto": auto,
                "verdict": None, "range": sum(COMBOS[h] for h in hands) / 13.26,
                "grid": grid, "hole": list(s.hole),
                "spot": f"{s.position} \u00b7 " + ("unopened" if raises_before(s) == 0 else
                                                    "vs open"),
                "cells": grid, "range_hands": list(hands),
                "opps": [(p.position, list(self.ranges.get(p.seat, ORDER)))
                         for p in s.players if p.seat != seat and not p.folded]}
        if move is not None:
            # narrow by the action family, like the sampled tree
            fam = family(chosen) if chosen else None
            keep = [h for h in hands if h in grid and any(family(k) == fam for k in grid[h])]
            if keep:
                self.ranges[seat] = keep
        else:
            self.last_hero = per_hand
        return node

    def narrow(self, seat, per_hand, chosen):
        if chosen is None:
            return
        fam = family(chosen)
        old = self.ranges.get(seat, ORDER)
        keep = [h for h in old if h not in per_hand or family(per_hand[h]) == fam]
        if any(h in per_hand for h in keep):
            self.ranges[seat] = keep

    def resolve_hero(self, s, move):
        """Your decision is in: narrow your range by it."""
        if self.last_hero is not None:
            self.narrow(0, self.last_hero, bucket(move, s))
            self.last_hero = None


# The trainer's bots (the coach, the hand tree and your opponents) play the sound version:
# solver-like big blind defence. The contest bot keeps a wider defence that measured better
# against the test bots (see bots/contender8.py, blind_curve).
# Fitted to your sims and charts (the 20 bb Monker sim in my_ranges/, the 25 bb charts in
# study_ranges/): jams capped by how wide the opener is (less so when shorter), a fixed-size
# small 3-bet, bluffs from hands that would fold; the SB limps, traps and jams first in;
# the BB defends wide (high cards and connected hands, suited aces call, offsuit aces and
# middle pairs jam) and raises or jams against a limp. Postflop, out of position to the
# preflop raiser (or the last street's bettor) it checks its range (90%+): leads only on
# low connected flops that hit the caller's range (donk_check, donk_texture). Against the
# flop c-bet it check-raises small (3x) and large (about pot) with sets / two pair, some
# draws and a few top pairs (xr), and from 15 bb deep never folds any pair, gutshot or
# backdoor flush draw (cont_rule). As the preflop raiser heads-up it c-bets big (3/4 pot)
# for value with top pair or better, small (1/3) with draws, overcards and backdoors, and
# checks back most medium pairs (cbet_plan).
STUDY = ("contender8:blind_curve=1;open_mc=1;jam_cap=1.2;call_pref_all=1;call_pref=1.4;"
         "small3_value=0.08;small3_margin=0.06;small3_lists=2;behind_pen=0.025;"
         "short_suited_pen=0.1;jam_width_k=2.0;jam_width_depth=5;small3_bluff_ip=0.4;"
         "behind_bw=0.0;force_slack=0.03;ace_call=1;small3_value_abs=0.02;small3_bluff_oop=1.0;"
         "curve_lo=0.55;curve_slope=3.4;curve_hc=0.06;curve_conn=0.04;curve_suited=0.12;"
         "curve_sb=0.8;sb_curve_lo=0.55;sb_curve_slope=1.2;ace_off_pref=0.3;call_pref_bb=2.5;"
         "bb_pair_jam=1;ante_widen=0.12;limp_game=1;limp_lo=0.8;limp_slope=3.0;"
         "limp_pref_suited=4.0;iso_value=0.04;bb_iso=0.2;caller_exp_bb=0.4;limped_real=0.8;"
         "limped_call_pref=2.0;small_bet_margin=1;stretch_down=1;deep_jam_bb=24;deep_jam_k=0.4;"
         "donk_check=1;donk_texture=1;xr=1;cont_rule=1;cbet_plan=1")
STUDY_TABLE = None  # your study charts (study_ranges/) + imported sims, loaded at start
STUDY_FOLDERS = ("my_ranges", "study_ranges")   # read in this order (later ones win)
PLAYERS_FOR_SOLVES = 8      # which solved tables to use (set from --players)
USE_SOLVED = False          # --solved: use solver/mccfr8.py results (experimental)
SOLVE_MIN_WEIGHT = 0.004    # leave out solved spots reached less than this (relative)


def load_flop_sims():
    global FLOP_SIMS
    import flop_sims
    FLOP_SIMS = flop_sims.load_all()
    return FLOP_SIMS


def load_study_table():
    """The trainer's ranges: your own sims (my_ranges, via bots/range_table8.py) plus the
    charts in study_ranges/, which win where both have a spot. The contest bot never sees
    study_ranges."""
    global STUDY_TABLE
    from bots.contender8 import TABLE8
    from solver.import_ranges8 import load_folder
    table = {}
    # Solved 8-max / 6-max preflop ranges (solver/mccfr8.py), for spots that were played
    # often enough in the solve to have converged; your own sims and charts override them.
    import json
    solved = Path(__file__).resolve().parent / "solver" / "mccfr_out"
    for f in sorted(solved.glob("*max_*bb.json")) if (solved.is_dir() and USE_SOLVED) else []:
        if "_s" in f.stem.split("bb")[-1]:
            continue  # a single run; only merged results are used
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        if int(d.get("players", 0)) != PLAYERS_FOR_SOLVES:
            continue
        for spot, acts in d["table"].items():
            if d.get("weight", {}).get(spot, 0.0) >= SOLVE_MIN_WEIGHT:
                table.setdefault(spot, {})[float(d["depth"])] = acts
    for spot, depths in TABLE8.items():
        table.setdefault(spot, {}).update(depths)
    # Your sims in my_ranges/ (e.g. converted by solver/monker_import8.py), read directly:
    # the trainer plays them even if you haven't imported them for the contest bot.
    here = Path(__file__).resolve().parent
    drop_unreached(here / "my_ranges")
    for spot, depths in cached_folders([here / f for f in STUDY_FOLDERS]).items():
        table.setdefault(spot, {}).update(depths)
    STUDY_TABLE = table


def drop_unreached(folder):
    """One-time cleanup after an update: delete the range files named in
    <folder>/_unreached.txt (decisions your solve almost never reaches, whose strategy is
    noise: solver/monker_import8.py no longer writes them), then mark the list as done."""
    lst = folder / "_unreached.txt"
    if not lst.is_file():
        return 0
    done = folder / "_unreached.done.txt"
    if done.is_file():          # cleaned once already: a sim imported since stays as it is
        lst.unlink()
        return 0
    n = 0
    since = lst.stat().st_mtime
    for fn in lst.read_text(encoding="utf-8").split():
        f = folder / Path(fn).name
        # (a file newer than the list came from an import of yours: never touched)
        if f.is_file() and "@" in f.name and "__" in f.name and f.stat().st_mtime <= since:
            f.unlink()
            n += 1
    if n:      # (nothing to delete: already clean, the list stays for later updates)
        lst.replace(folder / "_unreached.done.txt")
        print(f"Removed {n} unreached spots from {folder.name}/ (rare lines your sims never "
              "really solved; the bots' own rules play those).")
    return n


def cached_folders(folders):
    """my_ranges/ and study_ranges/ read through a cache file: re-reading ~20,000 small
    range files takes minutes on some PCs, so they're read again only when a file in
    them has been added, removed or changed. Later folders win where both have a spot."""
    import contextlib
    import io
    import os
    import pickle
    from solver.import_ranges8 import load_folder
    sig = []
    for folder in folders:
        if folder.is_dir():
            with os.scandir(folder) as it:
                files = [(e.name, e.stat().st_size, int(e.stat().st_mtime)) for e in it
                         if e.is_file()]
            sig.append((str(folder), len(files), hash(tuple(sorted(files)))))
    cache = Path(__file__).resolve().parent / ".ranges_cache.pickle"
    try:
        with open(cache, "rb") as f:
            saved_sig, table = pickle.load(f)
        if saved_sig == sig:
            return table
    except (OSError, ValueError, EOFError, pickle.UnpicklingError):
        pass
    print("Reading your sims and charts (first start after a change: this can take a minute)...")
    table = {}
    for folder in folders:
        if folder.is_dir():
            with contextlib.redirect_stdout(io.StringIO()):
                for spot, depths in load_folder(folder).items():
                    table.setdefault(spot, {}).update(depths)
    try:
        with open(cache, "wb") as f:
            pickle.dump((sig, table), f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass
    return table


def make_study(spec, seed):
    """A trainer bot: the spec's bot, playing your study charts where you have them."""
    bot = match8.make(spec, seed)
    if STUDY_TABLE and hasattr(bot, "table") and spec.startswith("contender8"):
        bot.table = STUDY_TABLE
    return FTL.with_open_policy(bot)   # (2x opens at a final table: final_table.py)

# ---------- EV of each option: Monte Carlo rollouts ----------

class Replay:
    """A seat in a rollout: replays the hand so far, then (for you) the option being
    tested, then plays the bot's strategy."""

    def __init__(self, bot, moves):
        self.bot, self.moves = bot, list(moves)

    def new_hand(self, n, b):
        self.bot.new_hand(n, b)

    def act(self, s):
        if self.moves:
            return self.moves.pop(0)
        return self.bot.act(s)


def option_move(key, s):
    word, size = key
    if word == "Fold":
        return ("fold", 0) if s.to_call else ("check", 0)
    if word in ("Check", "Call"):
        return ("call", 0) if s.to_call else ("check", 0)
    if word == "All-in":
        return ("raise", s.max_raise_to)
    return ("raise", int(round(size * BB)))


class EVWorker:
    """Works out, in the background, what each option is worth at your decision: for each
    sample it deals the opponents hands from their current ranges and a random board, then
    plays every option out with the bots (the same sample for every option, so the
    comparison is fair). Results are bb relative to folding now."""

    def __init__(self, app):
        self.app = app
        self.jobs = queue.Queue()
        self.gen = 0
        threading.Thread(target=self.run, daemon=True).start()

    def submit(self, job):
        self.gen += 1
        job["gen"] = self.gen
        self.jobs.put(job)

    def cancel(self):
        self.gen += 1

    def run(self):
        bots = [make_study(STUDY, 50000 + k) for k in range(8)]
        while True:
            job = self.jobs.get()
            while not self.jobs.empty():   # only the latest decision matters
                job = self.jobs.get()
            if job["gen"] != self.gen:
                continue
            try:
                self.solve(job, bots)
            except Exception:
                pass

    def solve(self, job, bots):
        s, keys, ranges = job["state"], job["keys"], job["ranges"]
        button, stacks = job["start"]
        n = len(stacks)
        per_seat = {i: [] for i in range(n)}
        for st, seat, a, amt in s.history:
            per_seat[seat].append((a, amt) if a == "raise" else (a, 0))
        moves = {k: option_move(k, s) for k in keys}
        dead = [card(c) for c in s.hole + s.board]
        rng = random.Random(job["gen"])
        live = [p.seat for p in s.players if p.seat != 0 and not p.folded]
        totals = {k: 0.0 for k in keys}
        rounds, t0, last_post = 0, time.time(), 0.0
        ante, ante_mode = job.get("ante", (match8.ANTE, "each"))
        game = Game8([None] * n, stack=0, big_blind=BB, ante=ante, ante_mode=ante_mode,
                     time_limit_s=1e9)
        while rounds < 600 and time.time() - t0 < 6.0:
            if job["gen"] != self.gen:
                return
            # One sample: hands for the live opponents from their ranges, a random board.
            used = set(dead)
            holes = {}
            for seat in live:
                hand = self.sample_hand(ranges.get(seat), used, rng)
                if hand is None:
                    break
                holes[seat] = hand
                used.update(hand)
            else:
                rest = [c for c in range(52) if c not in used]
                rng.shuffle(rest)
                deck = []
                for i in range(n):
                    if i == 0:
                        deck += [card(c) for c in s.hole]
                    elif i in holes:
                        deck += holes[i]
                    else:
                        deck += [rest.pop(), rest.pop()]
                deck += [card(c) for c in s.board] + rest
                for k in keys:
                    game.bots = [Replay(bots[i], per_seat[i] + ([moves[k]] if i == 0 else []))
                                 for i in range(n)]
                    r = game.play_hand(list(deck), button=button, hand_no=rounds,
                                       stacks=list(stacks))
                    totals[k] += r[0]
                rounds += 1
            if time.time() - last_post > 0.5 or rounds in (40, 600):
                last_post = time.time()
                self.post(job, totals, rounds)
        self.post(job, totals, rounds, done=True)

    @staticmethod
    def sample_hand(types, used, rng):
        types = types or ORDER
        for _ in range(60):
            h = rng.choices(types, weights=[COMBOS[t] for t in types])[0]
            r1, r2 = h[0], h[1]
            if len(h) == 2:
                s1, s2 = rng.sample("shdc", 2)
            elif h[2] == "s":
                s1 = s2 = rng.choice("shdc")
            else:
                s1, s2 = rng.sample("shdc", 2)
            a, b = card(r1 + s1), card(r2 + s2)
            if a not in used and b not in used:
                return [a, b]
        return None

    def post(self, job, totals, rounds, done=False):
        if not rounds or job["gen"] != self.gen:
            return
        fold_key = next((k for k in totals if k[0] == "Fold"), None)
        base = totals[fold_key] / rounds if fold_key else 0.0
        evs = {k: (v / rounds - base) / BB for k, v in totals.items()}
        self.app.events.put(("ev", job["node"], evs, rounds, done))


# ---------- the game thread ----------

class HumanSeat:
    def __init__(self, app):
        self.app = app
        self.advisor = make_study(STUDY, 4242)
        self.shown = False
        self.last_result = None

    def new_hand(self, hand_no, is_button):
        self.advisor.new_hand(hand_no, is_button)
        self.app.analyst.new_hand(hand_no)
        # At a final table you see every hand from the deal (the game loop shows the table),
        # unless clear folds are skipped: then from your first real decision.
        self.shown = self.app.mode == "Final table" and not self.app.ft_skip

    def act(self, s):
        app = self.app
        if app.settings_version != app.hand_version:
            raise Redeal  # the set-up changed during this hand
        app.board_seen_len = max(getattr(app, "board_seen_len", 0), len(s.board))
        adv = self.advisor.act(s)
        sim = app.flop_sim(s)
        if sim is not None:
            adv = sim.pick(s, BB) or adv   # your Pio solve's play (or the nearest flop's)
        icm = None
        if app.mode == "Final table" and app.ft is not None:
            icm = FTL.coach_spot(s, app.ft)   # what the ICM Coach explains at this decision
        if app.mode == "Spots" and app.director and hasattr(app.director, "flop_move"):
            adv = app.director.flop_move(s, sample=False) or adv  # the solve's play
        auto = None
        if app.mode == "Spots" and s.street == "preflop" and app.director:
            auto = app.director.hero_preflop(s, adv)
        elif app.skip(s, self.shown):
            auto = adv
        if auto is not None:  # played for you (spot set-up, or not a drilled spot)
            app.analyst.record(s, auto, adv, auto=True)
            if self.shown:
                app.events.put(("nodes", app.analyst.flush()))
            return auto
        if (((app.mode == "Play" and app.skip_trash) or
             (app.mode == "Final table" and app.ft_skip and not app.replaying))
                and s.street == "preflop" and s.to_call and not self.shown):
            grid = app.analyst.grid_for(s)
            h = hand_type_of(s.hole)
            if grid.get(h) == "Fold" and not close_spot(grid, h, "Medium"):
                if app.mode != "Final table":
                    raise Redeal  # an obvious fold: don't deal you this hand at all
                # A final table can't skip hands: fold it for you and play on quickly.
                self.shown = True
                app.events.put(("reveal", s, app.hand_depth))
                app.analyst.record(s, ("fold", 0), adv, auto=True)
                app.fast_forward = app.autofolded = True
                app.events.put(("autofold", h))
                return ("fold", 0)
        if (not self.shown and app.mode in ("Drill", "Spots") and s.street == "preflop" and
                not close_spot(app.analyst.grid_for(s), hand_type_of(s.hole), app.difficulty)):
            # Not a close decision at this difficulty: deal another (Spots) or let this
            # one play itself and wait for the next drill spot (Drill).
            if app.mode == "Spots":
                raise Redeal
            app.analyst.record(s, adv, adv, auto=True)
            return adv
        if not self.shown:
            self.shown = True
            app.events.put(("reveal", s, app.hand_depth))
        app.analyst.record(s, None, adv)
        app.events.put(("nodes", app.analyst.flush()))
        ranges = {seat: list(r) for seat, r in app.analyst.ranges.items()}
        if icm is not None:
            app.events.put(("icm_spot", icm))
        app.events.put(("your_turn", s, adv, ranges, app.hand_start))
        move = app.replies.get()
        if move == "quit":
            raise SystemExit
        if move == "redeal":  # you changed the set-up: this hand is dropped, a new one dealt
            raise Redeal
        if icm is not None and app.ft is not None and not app.replaying:
            graded = FTL.grade(icm, move, s)
            if graded is not None:   # you jammed, folded, or called the jam: an ICM grade
                right, g = graded
                rec = app.ft.icm
                rec["spots"] += 1
                rec["right"] += right
                if not right:
                    rec["lost"] += abs(g)
                app.events.put(("icm_graded", icm, right, g))
        app.analyst.resolve_hero(s, move)
        return move

    def hand_over(self, result):
        if self.app.mode != "Spots":  # set-up spots would teach the models odd habits
            self.advisor.hand_over(result)
        self.last_result = result


SPEEDS = (("Slow", 1.0), ("Normal", 0.55), ("Fast", 0.2), ("Turbo", 0.07))  # s per bot action
FOLD_BEAT = 0.4     # a bot's fold takes this share of the beat (folds are dead time)
QUICK_REST = 0.18   # Final table, you folded: the rest of the hand plays at this pace


def you_next(s, move):
    """True when you (seat 0) are the next to act after this move (everyone between has
    folded or is all-in)."""
    n = len(s.players)
    if s.my_seat == 0:
        return False
    k = (s.my_seat + 1) % n
    while k != s.my_seat:
        if k == 0:
            return not s.players[0].folded and not s.players[0].all_in
        p = s.players[k]
        if not p.folded and not p.all_in:
            return False
        k = (k + 1) % n
    return False


def I_WSOP_BB(level):
    """The big blind in chips at this final-table level (WSOP Circuit structure)."""
    import icm_study as I
    return I.WSOP_LEVELS[min(max(level, 1), len(I.WSOP_LEVELS)) - 1][1]


def runout_equities(holes, board, start):
    """{cards on the board: {seat: share of the pot}} for an all-in run-out, from the
    `start` cards on (each street of the run-out, as on TV). Exact when one or two cards
    are to come, sampled before the flop."""
    import itertools
    from cards import card as c_, evaluate as ev_
    used = {c for h in holes.values() for c in h} | set(board)
    deck = [r + x for r in "23456789TJQKA" for x in "cdhs" if r + x not in used]
    rng = random.Random(len(deck) * 31 + len(holes))
    out = {}
    for k in sorted({start, 3, 4, 5}):
        if k < start or k > len(board):
            continue
        need = 5 - k
        runs = list(itertools.combinations(deck, need)) if need <= 2 else \
            [tuple(rng.sample(deck, need)) for _ in range(1500)]
        wins = {seat: 0.0 for seat in holes}
        for extra in runs:
            full = [c_(x) for x in list(board[:k]) + list(extra)]
            sc = {seat: ev_([c_(x) for x in h] + full) for seat, h in holes.items()}
            top = max(sc.values())
            best = [seat for seat, v in sc.items() if v == top]
            for seat in best:
                wins[seat] += 1.0 / len(best)
        out[k] = {seat: w / max(1, len(runs)) for seat, w in wins.items()}
    return out


def think_pause(speed):
    """After you act, the next player takes a moment too (time to think)."""
    return 0.05 + 0.85 * speed
FOLD_WAIT = 2.0   # Fast fold on: after you fold, skip the rest of the hand after this long


class Paced:
    def __init__(self, bot, app, human):
        self.bot, self.app, self.human = bot, app, human

    def act(self, s):
        app = self.app
        if app.settings_version != app.hand_version:
            raise Redeal  # the set-up changed during this hand
        if app.fold_wait:  # you folded: watch the rest of the hand, or skip to the next
            app.fold_deadline = time.time() + FOLD_WAIT if app.fast_fold else None
            app.events.put(("fold_choice",))
            while app.fold_wait:
                if app.fold_deadline is not None:
                    if app.view is not None or app.icm_view:  # looking away: don't skip ahead
                        app.fold_deadline += 0.05
                    elif time.time() > app.fold_deadline:
                        app.fast_forward, app.fold_wait = True, False
                        break
                time.sleep(0.05)
            if app.settings_version != app.hand_version:
                raise Redeal
        if app.after_you:
            app.after_you = False
            if app.mode in ("Play", "Spots", "Final table") and not app.fast_forward and \
                    self.human.shown:
                time.sleep(think_pause(app.speed))
        app.board_seen_len = max(getattr(app, "board_seen_len", 0), len(s.board))
        sim_move = None
        if app.mode == "Spots" and app.director and s.street == "flop" and \
                hasattr(app.director, "flop_move") and s.my_seat == app.director.villain:
            sim_move = app.director.flop_move(s, sample=True)
        if sim_move is None and app.mode in ("Play", "Final table") and s.street == "flop":
            sim = app.flop_sim(s)
            if sim is not None:
                sim_move = sim.pick(s, BB, random)
        if sim_move is not None:
            move = sim_move
        elif app.mode == "Spots" and s.street == "preflop" and app.director:
            move = app.director.other_preflop(s, self.bot)
        else:
            move = self.bot.act(s)
        app.analyst.record(s, move)
        if self.human.shown:
            app.events.put(("nodes", app.analyst.flush()))
            app.events.put(("action", s, move))
            if app.mode in ("Play", "Spots", "Final table") and not app.fast_forward:
                # A beat to see the action; shorter when it's your turn next.
                if app.quick_rest:
                    time.sleep(min(QUICK_REST, app.speed))
                else:
                    beat = app.speed * (FOLD_BEAT if move[0] == "fold" else 1.0)
                    time.sleep(beat * (0.3 if you_next(s, move) else 1.0))
        return move

    def new_hand(self, hand_no, is_button):
        self.bot.new_hand(hand_no, is_button)

    def hand_over(self, result):
        if self.app.mode != "Spots":
            self.bot.hand_over(result)


def game_loop(app, args):
    rng = random.Random(args.seed)
    app.analyst = Analyst()
    human = HumanSeat(app)
    bots = [human] + [Paced(make_study(args.bots, rng.randrange(10 ** 6)), app, human)
                      for _ in range(args.players - 1)]
    n = args.players
    start = int(args.depth * BB)
    game = Game8(bots, stack=start, big_blind=BB, ante=match8.ANTE, time_limit_s=1e9)
    hand_no = 0
    play_button = 0
    failures = 0
    ft_id, ft_pending, ft_bots, ft_game, seats = None, None, {}, None, None
    ft_last, ft_replay, used_ft = None, False, 0
    last = None        # the last hand you played, for Replay
    try:
        while True:
            mode = app.mode
            replay = app.replay_request and (ft_last if mode == "Final table" else last) is not None
            app.replay_request = False
            deck = new_deck(rng)
            app.director = None
            if mode == "Spots" and app.spot["scen"] == "Flop sim":
                sim = app.pick_sim(rng)
                if sim is None or sim.opener not in position_names(n) or \
                        sim.other not in position_names(n):
                    time.sleep(0.2)
                    continue
                import flop_sims
                hero = app.spot.get("side") or sim.other
                if hero not in (sim.opener, sim.other):
                    hero = sim.other
                villain = sim.other if hero == sim.opener else sim.opener
                button = next(b for b in range(n) if seat_positions(n, b)[0] == hero)
                pos = seat_positions(n, button)
                vseat = next(s_ for s_, p in pos.items() if p == villain)
                deck = sim.make_deck(rng, n, 0, hero, vseat)
                if deck is None:
                    time.sleep(0.2)
                    continue
                app.director = flop_sims.SimDirector(sim, hero, vseat, BB, rng)
                depth = sim.depth
                hand_stacks = [int(depth * BB)] * n
            elif mode == "Spots":
                scen, hero, villain, depth = app.spot_cfg()
                if spot_problem(n, scen, hero, villain):
                    time.sleep(0.2)
                    continue
                button = next(b for b in range(n) if seat_positions(n, b)[0] == hero)
                pos = seat_positions(n, button)
                vseat = next((s for s, p in pos.items() if p == villain), None)
                app.director = Director(scen, vseat)
                hand_stacks = [int(depth * BB)] * n
            elif mode == "Final table":
                ft = app.ft
                if ft is None or ft.over:
                    time.sleep(0.1)
                    continue
                if ft_id != ft.id:  # a new session: new players
                    ft_id, ft_pending = ft.id, None
                    ft_bots = {x: Paced(FTL.ICMBot(make_study(args.bots, rng.randrange(10 ** 6)),
                                                   ft, rng), app, human)
                               for x in range(1, FTL.SEATS)}
                ft_replay = bool(replay and ft_last is not None and ft_last[0] == ft.id)
                if ft_replay:   # the last hand again (it doesn't count in the session)
                    _, seats, button, hand_stacks, deck, used_ft = ft_last
                else:
                    if ft_pending is None:
                        ft_pending = ft.start_hand()
                    seats, button, hand_stacks = ft_pending
                hand_stacks = list(hand_stacks)
                app.ft_seats = list(seats)
                ft_game = Game8([human] + [ft_bots[x] for x in seats[1:]], stack=start,
                                big_blind=BB, ante=BB, ante_mode="bb", time_limit_s=1e9)
                depth = round(hand_stacks[0] / BB, 1)
                replay = False
            elif mode == "Drill":
                button = rng.randrange(n)
                depth = rng.choice(DRILL_DEPTHS[app.drill_depths])
                hand_stacks = [int(depth * BB)] * n
            else:
                button = play_button
                play_button = (play_button + 1) % n
                depth = app.play_depth
                hand_stacks = [int(depth * BB)] * n   # same stacks every hand
            if replay:  # the same cards, seats, stacks and bots' random choices
                deck, button, hand_stacks, used_no, depth, app.director = last
                hand_stacks = list(hand_stacks)
            elif mode == "Final table" and ft_replay:
                used_no, replay = used_ft, True
            else:
                used_no = hand_no
            app.replaying = replay
            FTL.POLICY.update(on=mode == "Final table", icm=app.ft_icm, table=app.ft)
            app.n = len(hand_stacks)
            app.hand_key = (hand_no, random.random())
            app.hand_sim = app.analyst.hand_sim = None
            app.analyst.director = app.director
            app.hand_depth = depth
            app.hand_start = (button, list(hand_stacks))
            app.fast_forward = app.hold = app.fold_wait = app.after_you = False
            app.quick_rest = False
            app.board_seen_len = 0     # board cards dealt during the betting
            app.hand_version = app.settings_version
            try:
                g_ = ft_game if mode == "Final table" else game
                if mode == "Final table":   # every hand from the deal: seats, button, your cards
                    k_ = len(hand_stacks)
                    order_ = [button, (button + 1) % 2] if k_ == 2 else \
                        [(button + 3 + i) % k_ for i in range(k_)]
                    pos_ = dict(zip(order_, position_names(k_)))
                    app.events.put(("ft_deal", used_no, button, list(hand_stacks),
                                    [name(deck[0]), name(deck[1])], pos_, depth))
                    time.sleep(0.4 * app.speed + 0.2)
                result = g_.play_hand(list(deck), button=button, hand_no=used_no,
                                      stacks=hand_stacks)
            except Redeal:
                hand_no += 1
                failures += 1
                if mode == "Play":
                    app.skipped_hands += 1
                if failures == 5000 and mode != "Play":
                    app.events.put(("notice", "The bot almost never plays its range into this "
                                    "spot at this depth: try another depth or position."))
                continue
            if not replay:
                hand_no += 1
                if mode == "Final table":
                    ft_last = (app.ft.id, list(seats), button, list(hand_stacks), list(deck),
                               used_no)
                else:
                    last = (list(deck), button, list(hand_stacks), used_no, depth, app.director)
            failures = 0
            ft_info = None
            if mode == "Final table" and not replay:
                ft_info = app.ft.finish_hand(seats, hand_stacks, result)
                ft_pending = None
            if not human.shown:
                if ft_info is not None:  # e.g. a walk: show the new stacks, deal the next
                    app.events.put(("ft_quiet", ft_info, list(seats), list(result)))
                    time.sleep(0.6)
                continue  # no decision for you in this hand (e.g. a walk): deal the next
            holes = [[name(deck[2 * i]), name(deck[2 * i + 1])] for i in range(len(hand_stacks))]
            app.events.put(("hand_over", human.last_result, result, replay, holes))
            if ft_info is not None:
                app.events.put(("ft_hand", ft_info, list(seats)))
            if mode == "Drill":
                end = time.time() + 0.9  # a moment to read the result, then the next spot
                while time.time() < end and not app.hold:
                    time.sleep(0.05)
                if not app.hold:
                    continue
            if (app.fast_forward or app.fold_wait or app.quick_rest) and app.fast_fold:
                # You folded: deal the next hand by itself unless you ask to review this one.
                hold_for = 0.4 if app.autofolded else 1.3
                if mode == "Final table":
                    # Time to watch a showdown's run-out deal, see who won, and take in the
                    # new stacks and prize equities (longer when someone busted).
                    r_ = human.last_result or {}
                    hold_for = 1.0
                    if len(r_.get("shown") or {}) >= 2:
                        # The run-out deals in (the cards nobody saw yet), then a look.
                        seen = app.board_seen_len
                        new = max(0, len(r_.get("board") or []) - seen)
                        deal = (0.45 + (0.28 if seen < 3 and new >= 3 else 0.0)
                                + 0.6 * max(0, new - (3 if seen < 3 else 0)))
                        hold_for = (deal if new else 0.0) + 2.5
                    if ft_info is not None and ft_info["busts"]:
                        hold_for += 1.0
                end = time.time() + hold_for
                while time.time() < end and not app.hold:
                    time.sleep(0.05)
                if not app.hold:
                    continue
            reply = app.replies.get()
            if reply == "quit":
                return
            if reply == "replay":
                app.replay_request = True
    except SystemExit:
        return



# ---------- the preflop chart window ----------

RANKS = "AKQJT98765432"


class ChartWindow:
    """A separate window with the 13x13 preflop chart for one decision of the hand: every
    hand coloured by what the bot does with it (split when it mixes), your hand outlined.
    Buttons along the top switch between the hand's preflop decisions."""

    CELL_W, CELL_H = 46, 34

    def __init__(self, app):
        self.app = app
        self.node = None
        self.top = tk.Toplevel(app.root)
        self.top.title("Preflop chart")
        self.top.configure(bg=C["bg"])
        self.top.resizable(False, False)
        self.w = 13 * self.CELL_W + 40
        self.h = 13 * self.CELL_H + 190
        self.cv = tk.Canvas(self.top, width=self.w, height=self.h, bg=C["bg"],
                            highlightthickness=0)
        self.cv.pack()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.alive = True

    def close(self):
        self.alive = False
        self.top.destroy()

    def show(self, node=None):
        nodes = [n for n in self.app.tree if n.get("grid")]
        if node is None or node not in nodes:
            if self.node in nodes and node is None:
                node = self.node
            else:
                hero = [n for n in nodes if n["hero"]]
                node = (hero or nodes or [None])[-1]
        self.node = node
        self.draw(nodes)
        self.top.lift()

    def draw(self, nodes):
        cv, app = self.cv, self.app
        cv.delete("all")
        cv.create_text(20, 22, text="Preflop chart", fill=C["text"], anchor="w", font=FH(14))
        cv.create_text(20 + text_w("Preflop chart", FH(14)) + 10, 23,
                       text="what the bot does with every hand", fill=C["dim"], anchor="w",
                       font=F(9))
        # One button per preflop decision of this hand.
        x = 20
        for n in nodes:
            who = "You" if n["hero"] else n["pos"]
            what = n["spot"].split(" \u00b7 ")[-1]
            label = f"{who} \u00b7 {what}"
            w = text_w(label, FH(9)) + 22
            on = n is self.node
            app_button(self, x, 42, w, 26, label, C["accent"] if on else C["card2"],
                       lambda m=n: self.show(m), fg="white" if on else C["dim"])
            x += w + 6
        node = self.node
        if node is None:
            cv.create_text(self.w / 2, self.h / 2, text="No preflop decisions in this hand yet.",
                           fill=C["dim"], font=F(11))
            return
        # Legend.
        y = 84
        colours = {o[0]: o[3] for o in node["options"]}
        x = 20
        for k, label, pct, col in node["options"]:
            app.rrect_on(cv, x, y, x + 14, y + 14, 4, fill=col, outline="")
            t = f"{label}  {pct:.0f}%"
            cv.create_text(x + 20, y + 7, text=t, fill=C["text"], anchor="w", font=F(9))
            x += 30 + text_w(t, F(9))
        cv.create_text(self.w - 20, y + 7, text=f"range {node['range']:.0f}% of hands",
                       fill=C["faint"], anchor="e", font=F(9))
        # The grid.
        gx, gy = 20, 110
        grid = node["grid"]
        mine = hand_type_of(node["hole"]) if node["hero"] else None
        for r, r1 in enumerate(RANKS):
            for c, r2 in enumerate(RANKS):
                if r == c:
                    h = r1 + r2
                elif r < c:
                    h = r1 + r2 + "s"
                else:
                    h = r2 + r1 + "o"
                x1 = gx + c * self.CELL_W
                y1 = gy + r * self.CELL_H
                x2, y2 = x1 + self.CELL_W - 2, y1 + self.CELL_H - 2
                cell = grid.get(h)
                if not cell:
                    cv.create_rectangle(x1, y1, x2, y2, fill="#13161c", outline="")
                    cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=h, fill="#2f3542",
                                   font=F(8))
                    continue
                px = x1
                order = [o[0] for o in node["options"]]
                for k in sorted(cell, key=lambda k: order.index(k) if k in order else 99):
                    w = (x2 - x1) * cell[k]
                    cv.create_rectangle(px, y1, px + w, y2, fill=colours.get(k, "#666666"),
                                        outline="")
                    px += w
                cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=h, fill="white", font=FH(9))
                if h == mine:
                    cv.create_rectangle(x1 - 1, y1 - 1, x2 + 1, y2 + 1, outline="white", width=3)
        # Your hand, and how this decision was played.
        y = gy + 13 * self.CELL_H + 14
        title = f"{node['spot']}  \u00b7  pot {bb(node['pot'])} bb"
        cv.create_text(20, y + 8, text=title, fill=C["dim"], anchor="w", font=F(10))
        if node["hero"] and mine:
            cell = grid.get(mine, {})
            mix = ", ".join(f"{key_label(k)} {100 * f:.0f}%" for k, f in
                            sorted(cell.items(), key=lambda kv: -kv[1]))
            cv.create_text(20, y + 32, text=f"Your hand {mine}:  the bot plays {mix or '-'}",
                           fill=C["gold"], anchor="w", font=FH(11))
            if node["chosen"]:
                cv.create_text(20, y + 54, text=f"You played: {key_label(node['chosen'])}",
                               fill=C["text"], anchor="w", font=F(10))
        elif node["chosen"]:
            cv.create_text(20, y + 32, text=f"{node['pos']} played: {key_label(node['chosen'])}",
                           fill=C["text"], anchor="w", font=FH(11))


# ---------- equities for the decision details window ----------

_TYPE_COMBOS = {}


def type_combos(h):
    """Every two-card combo (as card ints) of hand type h, e.g. 'AKs' -> 4 combos."""
    if h not in _TYPE_COMBOS:
        r1, r2, suits = h[0], h[1], "shdc"
        if len(h) == 2:
            pairs = [(r1 + suits[i], r1 + suits[j]) for i in range(4) for j in range(i + 1, 4)]
        elif h[2] == "s":
            pairs = [(r1 + x, r2 + x) for x in suits]
        else:
            pairs = [(r1 + x, r2 + y) for x in suits for y in suits if x != y]
        _TYPE_COMBOS[h] = [(card(a), card(b)) for a, b in pairs]
    return _TYPE_COMBOS[h]


def equity_vs(mine, opps, board, samples, rng):
    """Share of the pot won by a hand from `mine` (combos) against one hand from each
    opponent combo list, over random run-outs. None if no combo fits."""
    dead = set(board)
    mine = [c for c in mine if c[0] not in dead and c[1] not in dead]
    opps = [[c for c in o if c[0] not in dead and c[1] not in dead] for o in opps]
    if not mine or any(not o for o in opps):
        return None
    deck = [c for c in range(52) if c not in dead]
    need = 5 - len(board)
    won = n = 0.0
    for _ in range(samples * 3):
        if n >= samples:
            break
        a, b = mine[rng.randrange(len(mine))]
        used = {a, b}
        hands = []
        for o in opps:
            for _ in range(25):
                x, y = o[rng.randrange(len(o))]
                if x not in used and y not in used:
                    break
            else:
                hands = None
                break
            used.update((x, y))
            hands.append((x, y))
        if hands is None:
            continue
        run = []
        while len(run) < need:
            c = deck[rng.randrange(len(deck))]
            if c not in used and c not in run:
                run.append(c)
        full = board + run
        me = evaluate([a, b] + full)
        best = max(evaluate([x, y] + full) for x, y in hands)
        if me > best:
            won += 1
        elif me == best:
            won += 1.0 / (1 + sum(1 for x, y in hands if evaluate([x, y] + full) == me))
        n += 1
    return won / n if n else None


class DetailsWindow:
    """Click a card in the hand tree: that decision in full. Each action with how often
    it's taken, its EV and the average equity of the hands that take it; the player's
    hand against the ranges still in; and the whole range as a 13x13 grid, coloured by
    action, with every hand's equity."""

    CW, CH = 44, 34
    GX = 490  # where the grid starts

    def __init__(self, app):
        self.app = app
        self.node = None
        self.top = tk.Toplevel(app.root)
        self.top.title("Decision details")
        self.top.configure(bg=C["bg"])
        self.top.resizable(False, False)
        self.w, self.h = self.GX + 13 * self.CW + 20, 13 * self.CH + 150
        self.cv = tk.Canvas(self.top, width=self.w, height=self.h, bg=C["bg"],
                            highlightthickness=0)
        self.cv.pack()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.alive = True
        self.results = queue.Queue()
        self.eq, self.my_eq, self.done = {}, None, False
        self.job = 0
        self.top.after(120, self.poll)

    def close(self):
        self.alive = False
        self.job += 1
        self.top.destroy()

    def show(self, node):
        self.node = node
        self.eq, self.my_eq, self.done = {}, None, False
        self.job += 1
        threading.Thread(target=self.work, args=(self.job, node), daemon=True).start()
        self.draw()
        self.top.lift()

    def work(self, job, node):
        """Equities for the hand and every hand in the range (in the background)."""
        rng = random.Random(7)
        board = [card(c) for c in node["board"]]
        opps = [[c for h in hands for c in type_combos(h)] for _, hands in node["opps"]]
        if not opps:
            self.results.put((job, "done", None))
            return
        many = len(opps) > 2
        if node.get("hole"):
            hole = [(card(node["hole"][0]), card(node["hole"][1]))]
            self.results.put((job, "mine", equity_vs(hole, opps, board, 2000 if many else 4000,
                                                       rng)))
        hands = sorted(node.get("range_hands") or ORDER, key=ORDER.index)
        batch = {}
        for i, h in enumerate(hands):
            if job != self.job:
                return
            batch[h] = equity_vs(type_combos(h), opps, board, 400 if many else 600, rng)
            if len(batch) >= 12 or i == len(hands) - 1:
                self.results.put((job, "range", batch))
                batch = {}
        self.results.put((job, "done", None))

    def poll(self):
        if not self.alive:
            return
        changed = False
        try:
            while True:
                job, kind, data = self.results.get_nowait()
                if job != self.job:
                    continue
                changed = True
                if kind == "mine":
                    self.my_eq = data
                elif kind == "range":
                    self.eq.update({h: e for h, e in data.items() if e is not None})
                else:
                    self.done = True
        except queue.Empty:
            pass
        if changed:
            self.draw()
        self.top.after(150, self.poll)

    def avg_eq(self, hands):
        num = den = 0.0
        for h, w in hands:
            if h in self.eq:
                num += self.eq[h] * w
                den += w
        return num / den if den else None

    def draw(self):
        cv, app, node = self.cv, self.app, self.node
        cv.delete("all")
        if node is None:
            return
        who = "You" if node["hero"] else (self.app.seat_name(node["seat"]) or
                                          f"Bot {node['seat']}")
        street = node["street"].capitalize()
        cv.create_text(20, 24, text=f"{node['pos']}  ·  {who}", fill=C["text"], anchor="w",
                       font=FH(15))
        sub = f"{street}  ·  pot {bb(node['pot'])} bb  ·  range {node['range']:.0f}% of hands"
        cv.create_text(20, 48, text=sub, fill=C["dim"], anchor="w", font=F(10))
        x = 20
        for c in node["board"]:
            app.card_on(cv, x, 64, c, 30, 40)
            x += 34
        known = node["hero"] or bool(app.final_holes)
        if known and node.get("hole"):
            hx = 300
            cv.create_text(hx - 8, 84, text="Hand", fill=C["faint"], anchor="e", font=F(9))
            for k, c in enumerate(node["hole"]):
                app.card_on(cv, hx + k * 34, 64, c, 30, 40)
        # Who it's up against.
        y = 122
        vs = "   ".join(f"{pos} {sum(COMBOS[h] for h in hands) / 13.26:.0f}%"
                       for pos, hands in node["opps"]) or "-"
        cv.create_text(20, y, text=f"Against:  {vs}", fill=C["dim"], anchor="w", font=F(9))
        # Actions table.
        y = 150
        cols = (20, 210, 285, 345, 405)
        for x_, t in zip(cols, ("Action", "How often", "Combos", "EV", "Avg equity")):
            cv.create_text(x_, y, text=t, fill=C["faint"], anchor="w", font=F(8))
        y += 14
        cells = node.get("cells") or {}
        total = sum(COMBOS[h] for h in cells) or 1
        evs = node.get("ev") or {}
        for k, label, pct, col in node["options"]:
            y += 30
            app.rrect_on(cv, 20, y - 11, 34, y + 3, 4, fill=col, outline="")
            mark = ""
            if node["chosen"] == k:
                mark = "  ← played"
            if node["hero"] and node.get("bot") == k:
                mark += "  ● bot"
            cv.create_text(42, y - 4, text=label + mark, fill=C["text"], anchor="w", font=FH(10))
            bar = 60 * pct / 100
            app.rrect_on(cv, 210, y - 9, 270, y + 1, 4, fill=C["card2"], outline="")
            if bar > 1:
                app.rrect_on(cv, 210, y - 9, 210 + bar, y + 1, 4, fill=col, outline="")
            cv.create_text(210, y + 9, text=f"{pct:.1f}%", fill=C["dim"], anchor="w", font=F(8))
            cv.create_text(285, y - 4, text=f"{pct * node['range'] * 13.26 / 100:.0f}",
                           fill=C["dim"], anchor="w", font=F(10))
            ev = evs.get(k)
            cv.create_text(345, y - 4, text="-" if ev is None else f"{ev:+.2f}",
                           fill=C["text"] if ev is not None else C["faint"], anchor="w",
                           font=F(10))
            share = [(h, COMBOS[h] * c.get(k, 0)) for h, c in cells.items() if c.get(k)]
            a = self.avg_eq(share)
            cv.create_text(405, y - 4, text="…" if a is None else f"{100 * a:.0f}%",
                           fill=C["text"], anchor="w", font=F(10))
        y += 34
        whole = self.avg_eq([(h, COMBOS[h]) for h in (node.get("range_hands") or ORDER)])
        lines = []
        if known and node.get("hole"):
            mine = hand_type_of(node["hole"])
            lines.append(("This hand " + mine,
                          "…" if self.my_eq is None else f"{100 * self.my_eq:.1f}%"))
        lines.append(("Whole range", "…" if whole is None else f"{100 * whole:.1f}%"))
        cv.create_text(20, y, text="Equity against those ranges (all-in to the river)",
                       fill=C["faint"], anchor="w", font=F(8))
        for label, v in lines:
            y += 24
            cv.create_text(20, y, text=label, fill=C["dim"], anchor="w", font=F(10))
            cv.create_text(405, y, text=v, fill=C["gold"] if label.startswith("This") else
                           C["text"], anchor="w", font=FH(11))
        if node["hero"] and evs:
            y += 30
            n = node.get("ev_n", 0)
            cv.create_text(20, y, text=f"EV in bb against folding, {n} simulated hands"
                           if n else "EV in bb against folding", fill=C["faint"],
                           anchor="w", font=F(8))
        if node["street"] == "preflop" and node.get("grid"):
            app_button(self, 20, self.h - 46, 170, 30, "Preflop chart ↗", C["card2"],
                       lambda: app.open_chart(node))
        if not self.done:
            cv.create_text(self.GX - 30, self.h - 30, text="working out equities…", fill=C["faint"],
                           anchor="e", font=F(8))
        # The range: action colours, and each hand's equity.
        gx, gy = self.GX, 20
        colours = {o[0]: o[3] for o in node["options"]}
        order = [o[0] for o in node["options"]]
        in_range = set(node.get("range_hands") or ORDER)
        mine = hand_type_of(node["hole"]) if known and node.get("hole") else None
        for r, r1 in enumerate(RANKS):
            for c, r2 in enumerate(RANKS):
                h = r1 + r2 if r == c else (r1 + r2 + "s" if r < c else r2 + r1 + "o")
                x1, y1 = gx + c * self.CW, gy + r * self.CH
                x2, y2 = x1 + self.CW - 2, y1 + self.CH - 2
                if h not in in_range:
                    cv.create_rectangle(x1, y1, x2, y2, fill="#13161c", outline="")
                    cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=h, fill="#2f3542",
                                   font=F(7))
                    continue
                cell = cells.get(h)
                if cell:
                    px = x1
                    for k in sorted(cell, key=lambda k: order.index(k) if k in order else 99):
                        w = (x2 - x1) * cell[k]
                        cv.create_rectangle(px, y1, px + w, y2, fill=colours.get(k, "#666666"),
                                            outline="")
                        px += w
                else:
                    cv.create_rectangle(x1, y1, x2, y2, fill=C["card2"], outline="")
                cv.create_text((x1 + x2) / 2, y1 + 11, text=h, fill="white", font=FH(8))
                e = self.eq.get(h)
                if e is not None:
                    cv.create_text((x1 + x2) / 2, y1 + 24, text=f"{100 * e:.0f}",
                                   fill="#e8ecf2", font=F(7))
                if h == mine:
                    cv.create_rectangle(x1 - 1, y1 - 1, x2 + 1, y2 + 1, outline="white", width=3)
        y = gy + 13 * self.CH + 10
        note = ("Colours: the bot's action with each hand.  Numbers: equity % vs the ranges "
                "still in.")
        if node["street"] != "preflop":
            note += "  Grey: in the range, action not sampled."
        cv.create_text(gx, y + 6, text=note, fill=C["faint"], anchor="w", font=F(8))


def hand_type_of(hole):
    """'Ah', 'Kd' -> 'AKo'."""
    (r1, s1), (r2, s2) = hole[0], hole[1]
    if RANKS.index(r1) > RANKS.index(r2):
        r1, s1, r2, s2 = r2, s2, r1, s1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def app_button(win, x, y, w, h, text, colour, cmd, fg="white"):
    """A rounded button on a chart window's canvas."""
    cv = win.cv
    tag = f"cb{x}_{y}"
    body = win.app.rrect_on(cv, x, y, x + w, y + h, 8, fill=colour, outline="", tags=(tag,))
    cv.create_text(x + w / 2, y + h / 2, text=text, fill=fg, font=FH(9), tags=(tag,))
    cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
    cv.tag_bind(tag, "<Enter>", lambda e: (cv.itemconfigure(body, fill=shade(colour, 0.1)),
                                           cv.configure(cursor="hand2")))
    cv.tag_bind(tag, "<Leave>", lambda e: (cv.itemconfigure(body, fill=colour),
                                           cv.configure(cursor="")))


# ---------- the window ----------

CAPTIONS = [("Elite pro decision", "#2fbf77"), ("Shitreg decision", "#e3b23c"),
            ("Dustreg decision", "#ea8a3a"), ("Fish decision", "#e0524f")]


def _match_option(chosen, keys):
    """Your move as one of the bot's options: itself, or (a raise or all-in) the bot's
    nearest aggressive option, so a 3-bet isn't 'never played' when the bot jams instead."""
    if chosen in keys:
        return chosen
    if chosen[0] in ("Raise", "Bet", "All-in"):
        aggr = [k for k in keys if k[0] in ("Raise", "Bet", "All-in")]
        if aggr:
            size = chosen[1] if chosen[1] is not None else 1e9
            return min(aggr, key=lambda k: abs((k[1] if k[1] is not None else 1e9) - size))
    return chosen


def caption_for(node):
    """Grade your decision. The bot's play is elite. Otherwise, by what it costs: with
    simulated EVs, the bb given up against the best option (0.5 / 1.5 / 3 bb steps); with a
    Pio solve, how often the solve plays it with your cards; else how often the bot plays it
    with your hand type (preflop) or its range. A move the bot doesn't make at all is
    compared with its nearest one (a small 3-bet with the bot's jam)."""
    chosen = node.get("chosen")
    if chosen is None or node.get("verdict") is None:
        return None
    if node.get("icm"):   # a final-table all-in decision: graded by ICM, in prize money
        right, g, pool = node["icm"]
        if right:
            return CAPTIONS[0]
        loss = abs(g) / max(pool, 1.0)   # (a 1M pool: under $1K, $3K, more)
        return CAPTIONS[1 if loss < 0.001 else 2 if loss < 0.003 else 3]
    if node["verdict"]:
        return CAPTIONS[0]
    keys = [o[0] for o in node["options"] if o[2] > 0 or o[0] != chosen]
    chosen = _match_option(chosen, keys)

    def by_freq(f):
        return CAPTIONS[0 if f >= 0.25 else 1 if f >= 0.08 else 2 if f >= 0.02 else 3]
    if node.get("sim_mix"):
        mix = node["sim_mix"]
        return by_freq(mix.get(chosen, 0.0))
    evs = node.get("ev") or {}
    if evs and node.get("ev_n", 0) >= 40 and chosen in evs:
        loss = max(evs.values()) - evs[chosen]
        return CAPTIONS[0 if loss <= 0.5 else 1 if loss <= 1.5 else 2 if loss <= 3.0 else 3]
    cell = None
    if node.get("grid") and node.get("hole"):
        cell = node["grid"].get(hand_type_of(node["hole"]))
    if cell:
        return by_freq(cell.get(chosen, 0.0))
    freq = {o[0]: o[2] / 100.0 for o in node["options"]}
    return by_freq(freq.get(chosen, 0.0))


class Trainer:
    def __init__(self, root, args):
        self.root, self.args, self.n = root, args, args.players
        self.events, self.replies = queue.Queue(), queue.Queue()
        self.mode = "Play"
        self.drill_spot, self.drill_depths = "Any", "All"
        self.difficulty = "Medium"
        self.play_depth = args.depth   # Play mode: everyone's stack at the start of each hand
        self.fast_fold = True     # after you fold, the next hand deals itself
        self.skip_trash = True    # Play mode: obvious folds are folded for you
        self.ft_skip = True       # Final table: clear folds are folded for you and skipped
        self.drill_postflop = True  # Drill mode: play your drilled hands on to the river
        self.fast_forward = self.hold = self.autofolded = False
        self.replay_request = self.replaying = False
        self.autonext = False     # showing "next hand coming" after your fold
        self.fold_choice = False  # showing Watch hand / Next hand after your fold
        order = position_names(self.n)
        self.spot = {"scen": "Defend → flop", "hero": "BB",
                     "villain": "CO" if "CO" in order else order[0], "depth": 40}
        self.director = None
        self.hand_depth = None
        self.speed = SPEEDS[1][1]
        self.fold_wait = False    # you folded: the hand waits for Watch / Next hand
        self.quick_rest = False   # Final table, you folded: the rest plays out quickly
        self.after_you = False    # you just acted: the next bot pauses before acting
        self.settings_version = self.hand_version = 0  # set-up changes cancel the hand
        self.details = None       # the decision details window, when open
        self.fold_deadline = None # ... until then, when Fast fold is on
        self.state = self.advice = None
        self.show_hint = False
        self.waiting_next = False
        self.notice = ""
        self.toast = ""
        self.skipped = 0
        self.skipped_hands = 0    # Play mode: obvious folds never dealt to you
        self.seats = [{} for _ in range(self.n)]
        self.board, self.pot, self.dealer, self.street = [], 0, 0, "preflop"
        self.stats = {m: {"hands": 0, "total": 0.0, "agree": [0, 0]}
                      for m in ("Play", "Drill", "Spots")}
        self.feedback = None
        self.last_state = self.final = None
        self.final_holes = None
        self.caption = self.caption_node = None
        self.caption_until = 0.0
        self._board_seen, self._board_t, self._anim_until = [], {}, 0.0   # board deal-in
        self._eq_before = {}
        # Final table mode (final_table.py): a session, hand after hand, until you bust.
        self.ft = None            # the session
        self.ft_table = 1         # which of the preset tables (icm_study.N_TABLES)
        self.ft_pool = "$1M"
        self.ft_seats = None      # this hand's table seats, by engine seat
        self.icm_spot = None      # your current / last ICM push-fold decision
        self.icm_last = None      # (right, dollars) once you've made it
        self.coach_host = None
        self.coach_bot = None     # the coach's own bot (how players answer your 2x open)
        self.icm_view = False     # the ICM study tab is showing (over the table)
        self.icm_win = None
        self.ft_icm = True        # Final table: all-in decisions by ICM (else chip EV)
        self.ft_level_hands = FTL.LEVEL_HANDS   # Final table: hands per blind level
        self.hand_key = self.hand_sim_key = None
        self.hand_sim = None      # Play / Final table: the Pio solve this flop follows
        self.stats["Final table"] = {"hands": 0, "total": 0.0, "agree": [0, 0]}

        root.title("Practice table")
        root.configure(bg=C["bg"])
        root.resizable(True, True)    # drag it bigger (or maximize): more room for the panel
        root.minsize(BASE_W, BASE_H)
        pick_font(root)
        self.cv = tk.Canvas(root, width=W, height=H, bg=C["bg"], highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self._resize_job = None
        root.bind("<Configure>", self.on_resize, add="+")
        self.chart = None       # the preflop chart window, when open
        self.show_ev = True     # simulate each option's EV at your decisions
        self.ev_worker = EVWorker(self)
        self.tree = []          # decision cards for the current hand
        self.tree_scroll = 0
        self.panel_tab = "Tree"
        self.history = []       # plain-text hand history (kept, not shown)
        self.past = []          # finished hands you can step back through (◀)
        self.view = None        # index into self.past while looking back, else None
        self._live = None       # the live table, put aside while looking back
        root.bind("<MouseWheel>", self.on_wheel)
        root.bind("<Button-4>", self.on_wheel)
        root.bind("<Button-5>", self.on_wheel)
        self._drag = None
        self.custom = None  # custom raise size in bb (the slider under the buttons)
        self._hits, self._hover = [], None
        self.cv.bind("<Button-1>", self.on_click, add="+")
        self.cv.bind("<Motion>", self.on_motion, add="+")
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        keys = (("f", self.do_fold), ("c", self.do_call), ("a", self.do_allin),
                ("<space>", self.k_next), ("r", self.review), ("z", self.replay),
                ("w", self.watch_rest), ("b", self.go_back), ("<Left>", self.go_back), ("<Right>", self.go_forward),
                ("<Escape>", self.back_to_table),
                ("1", lambda: self.k_size(0)),
                ("2", lambda: self.k_size(1)), ("3", lambda: self.k_size(2)))
        for key, fn in keys:   # (not while typing in a box, or on the ICM tab)
            root.bind(key, lambda e, f=fn: None if (self.icm_view or
                                                    isinstance(e.widget, tk.Entry)) else f())
        root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=game_loop, args=(self, args), daemon=True).start()
        self.draw()
        root.after(30, self.poll)

    # ----- used by the game thread -----
    def sim_groups(self):
        """Solved spots (depth, pot type, seats) -> their flops, in a stable order."""
        groups = {}
        for sm in FLOP_SIMS:
            groups.setdefault((sm.depth, sm.kind, sm.opener, sm.other), []).append(sm)
        keys = sorted(groups, key=lambda k: (k[0], k[1], k[2], k[3]))
        return [(k, sorted(groups[k], key=lambda sm: "".join(sm.board))) for k in keys]

    @staticmethod
    def group_label(key):
        depth, kind, opener, other = key
        return f"{depth:g}bb {opener} v {other}" + {"SRP": "", "LP": " limped"}.get(kind,
                                                                                 f" {kind}")

    def current_group(self):
        groups = self.sim_groups()
        if not groups:
            return None, []
        return groups[self.spot.get("simgroup", 0) % len(groups)]

    def current_sim(self):
        """The solve for this hand (a random one of the spot's flops, or the one you chose)."""
        key, sims = self.current_group()
        if not sims:
            return None
        hs = getattr(self, "hand_sim", None)
        if hs is not None and hs in sims:
            return hs
        return sims[self.spot.get("simflop", 0) % len(sims)]

    def pick_sim(self, rng):
        """Game thread: the solve to deal next hand."""
        key, sims = self.current_group()
        if not sims:
            self.hand_sim = None
            return None
        if self.spot.get("simpick", "Random") == "Random":
            self.hand_sim = rng.choice(sims)
        else:
            self.hand_sim = sims[self.spot.get("simflop", 0) % len(sims)]
        return self.hand_sim

    def step_flop(self, d):
        key, sims = self.current_group()
        if sims:
            self.spot["simflop"] = (self.spot.get("simflop", 0) + d) % len(sims)
            self.spot["simpick"] = "Choose"
            self.restart_hand()
            self.draw()

    def pick_group(self, i):
        self.spot.update(simgroup=i, simflop=0, simpick="Random")
        self.restart_hand()
        self.draw()

    def draw_sim_setup(self, x, y, x2):
        """Spots -> Flop sim: pick a solved spot, random or chosen flop, and your seat."""
        cv = self.cv
        if not FLOP_SIMS:
            cv.create_text(x, y + 4, anchor="nw", width=x2 - x, fill=C["dim"], font=F(9),
                           text="No flop solves yet. Solve flops in PioSOLVER (the ranges are in "
                                "pio_ranges/), then run:\n  py -3.13 solver/pio_export.py "
                                "\"D:\\PioSOLVER\\saves\"\nand restart the trainer.")
            return y + 80
        groups = self.sim_groups()
        labels = [self.group_label(k) for k, _ in groups]
        key, sims = self.current_group()
        y = self.chip_grid(x, y, x2, f"Solved spot ({len(groups)})", labels, self.group_label(key),
                           lambda v: self.pick_group(labels.index(v)), 2)
        pick = self.spot.get("simpick", "Random")
        y = self.chip_grid(x, y, x2, f"Flop ({len(sims)} solved)", ["Random", "Choose"], pick,
                           lambda v: self.set_spot("simpick", v), 2)
        if pick == "Choose":
            board = "".join(sims[self.spot.get("simflop", 0) % len(sims)].board)
            self.button(x, y, 40, 28, "\u25c0", C["card2"], lambda: self.step_flop(-1), size=10)
            cv.create_text((x + x2) / 2, y + 14, text=board, fill=C["text"], font=FH(12))
            self.button(x2 - 40, y, 40, 28, "\u25b6", C["card2"], lambda: self.step_flop(1),
                        size=10)
            y += 36
        cur = sims[0]
        side = self.spot.get("side")
        if side not in (cur.opener, cur.other):
            side = cur.other
        y = self.chip_grid(x, y, x2, "You play", [cur.opener, cur.other], side,
                           lambda v: self.set_spot("side", v), 2)
        cv.create_text(x, y + 2, anchor="nw", width=x2 - x, fill=C["faint"], font=F(8),
                       text=f"Preflop is played for you; the flop and both hands come from the "
                            f"solve ({cur.depth:g} bb). The opponent plays the solve on the flop; "
                            f"from the turn on, the bots play.")
        return y + 48

    def spot_cfg(self):
        sp = self.spot
        return sp["scen"], sp["hero"], sp["villain"], sp["depth"]

    def skip(self, s, shown_already):
        """Drill mode: auto-play decisions that aren't the chosen kind of spot."""
        if self.mode != "Drill":
            return False
        if s.street != "preflop":
            # Postflop on: a hand you were drilled on goes on to the river with you playing.
            return not (self.drill_postflop and shown_already)
        if shown_already or self.drill_spot == "Any":
            return False
        want = {"Vs open": 1, "Vs 3-bet": 2, "Vs 4-bet": 3}[self.drill_spot]
        return raises_before(s) != want

    # ----- drawing helpers -----
    def rrect(self, x1, y1, x2, y2, r, **kw):
        return self.rrect_on(self.cv, x1, y1, x2, y2, r, **kw)

    @staticmethod
    def rrect_on(cv, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def on_resize(self, e):
        if e.widget is not self.root:
            return
        w, h = max(BASE_W, e.width), max(BASE_H, e.height)
        if (w, h) == (W, H):
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(80, lambda: self.apply_size(w, h))

    def apply_size(self, w, h):
        """A bigger window: the table keeps its size, centred in a wider table area; the
        rest goes to the panel (the hand tree, the final table's standings) and the ICM tab."""
        global W, H, TABLE_W
        self._resize_job = None
        W, H = w, h
        TABLE_W = BASE_TABLE_W + (w - BASE_W) // 2
        self.cv.configure(width=w, height=h)
        if self.icm_win is not None:
            self.icm_win.resize(0, TOP + 1, W, H - TOP - 1)
        self.draw()

    def open_icm(self):
        """The ICM study tab (ICM, ICM Trainer, Mega Satellites) in this window; the table
        waits underneath until you pick Play / Drill / Spots / Final table again."""
        import icm_study
        if self.icm_view:
            return
        win = icm_study.embedded(self, 0, TOP + 1, W, H - TOP - 1)
        self.icm_view = True
        win.show()
        self.draw()

    def close_icm(self):
        if self.icm_view:
            self.icm_view = False
            self.icm_win.hide()

    def flop_sim(self, s):
        """Play / Final table: the Pio solve a heads-up flop follows (that flop, or the
        nearest solved flop for the same spot), or None. Decided once per hand."""
        if self.mode not in ("Play", "Final table") or s.street != "flop" or not FLOP_SIMS:
            return None
        if self.hand_sim_key != self.hand_key:
            self.hand_sim_key, self.hand_sim = self.hand_key, None
            live = [p for p in s.players if not p.folded]
            line = flop_sims.preflop_line(s.history)
            if len(live) == 2 and line and {line[1], line[2]} == {p.seat for p in live}:
                kind, a, b = line
                pos = {p.seat: p.position for p in s.players}
                pot = s.pot - sum(p.bet for p in s.players)
                eff = min(p.stack + p.bet for p in live)
                try:
                    self.hand_sim = flop_sims.find(FLOP_SIMS, kind, pos[a], pos[b],
                                                   list(s.board), pot, eff)
                except (OSError, ValueError, KeyError):
                    self.hand_sim = None
            self.analyst.hand_sim = self.hand_sim
        return self.hand_sim

    # ----- final table sessions -----
    def ft_pays(self):
        import icm_study
        if self.ft_pool in icm_study.POOLS:
            return icm_study.pool_payouts(icm_study.POOLS[self.ft_pool])
        win = getattr(self, "icm_win", None)
        return list(win.custom) if win is not None else icm_study.pool_payouts(1_000_000)

    def ft_new(self, restart=True):
        if self.ft is not None:
            FTL.save_session(self.ft, self.ft.leaks)
        self.ft = FTL.FinalTable(self.ft_table, self.ft_pays(), BB)
        self.ft.level_hands = self.ft_level_hands
        self.icm_spot = self.icm_last = None
        self.stats["Final table"] = {"hands": 0, "total": 0.0, "agree": [0, 0]}
        if restart:
            self.restart_hand()
        self.draw()

    def ft_pick(self, k):
        self.ft_table = (k - 1) % FTL.I.N_TABLES + 1
        self.ft_new()

    def ft_random(self):
        k = random.randint(1, FTL.I.N_TABLES)
        while k == self.ft_table:
            k = random.randint(1, FTL.I.N_TABLES)
        self.ft_pick(k)

    def ft_end(self):
        if self.ft is not None and not self.ft.over:
            self.ft.end()
            FTL.save_session(self.ft, self.ft.leaks)
            self.restart_hand()
        self.draw()

    def set_ft_pool(self, v):
        self.ft_pool = v
        if self.ft is not None and not self.ft.over:
            self.ft.pays9 = sorted(self.ft_pays(), reverse=True)  # re-priced from now on
        self.draw()

    def set_ft_level_hands(self, v):
        self.ft_level_hands = 0 if v == "Never" else int(v.split()[0])
        if self.ft is not None:
            self.ft.level_hands = self.ft_level_hands
        self.draw()

    def ft_play_from_icm(self, table_no, pay_mode):
        """From the ICM tab's push/fold drill: this table and prize pool, full hands."""
        self.ft_table = (table_no - 1) % FTL.I.N_TABLES + 1
        self.ft_pool = pay_mode
        self.set_mode("Final table")
        self.ft_new()

    def set_ft_icm(self, v):
        self.ft_icm = v == "ICM"
        self.draw()

    def ft_label(self):
        if self.ft_pool != "Custom $":
            return self.ft_pool
        return f"${sum(self.ft_pays()) / 1000:,.0f}K custom"

    def seat_name(self, seat):
        """At a final table, the player's name (you: 'You'); elsewhere None."""
        if self.mode != "Final table" or self.ft is None or not self.ft_seats or \
                seat >= len(self.ft_seats) or len(self.ft_seats) != len(self.seats):
            return None
        return self.ft.name(self.ft_seats[seat])

    def seat_colour(self, seat):
        if self.mode == "Final table" and self.ft_seats and seat < len(self.ft_seats):
            return self.ft_seats[seat]
        return seat

    def ft_equity(self, seat):
        """Prize equity (dollars) of the player in this engine seat, or None (during a
        run-out: what it was before the hand)."""
        if self.mode != "Final table" or self.ft is None or not self.ft_seats:
            return None
        if seat >= len(self.ft_seats) or len(self.ft_seats) != len(self.seats):
            return None
        x = self.ft_seats[seat]
        if self.anim_on() and x in self._eq_before:
            return self._eq_before[x]
        return self.ft.equities().get(x)

    def anim_on(self):
        """The board is still dealing in (results wait for it)."""
        return time.time() < self._anim_until

    def runout_left(self):
        return max(0.0, self._anim_until - time.time())

    def open_analysis(self, spot):
        """For the coach: what a 2x open does here. How each player behind answers it (the
        same bots and final-table rules they play by), what it risks and wins in prize
        money, and how the hand plays on if it's called."""
        import icm_study as I
        s = spot["state"]
        if self.coach_bot is None:
            self.coach_bot = make_study(STUDY, 4243)
        bot = self.coach_bot
        sim = self.sim_sizes(s)      # your sim's smallest open, else 2x
        to = sim[0] if sim else max(s.min_raise_to, min(s.max_raise_to,
                                                        int(round(FTL.OPEN_X * BB))))
        me_p = next(p for p in s.players if p.seat == s.my_seat)
        names = position_names(len(s.players))
        by_pos = {p.position: p for p in s.players}
        order = [by_pos[nm] for nm in names]
        me = order.index(me_p)
        added = to - me_p.bet
        players = [replace(p, bet=to, stack=p.stack - added) if p.seat == me_p.seat else p
                   for p in s.players]
        hist = list(s.history) + [("preflop", me_p.seat, "raise", to)]
        resp = []
        dead = set(s.hole)
        for k in range(me + 1, len(order)):
            r = order[k]
            rp = next(p for p in players if p.seat == r.seat)
            st_r = replace(s, players=[replace(p) for p in players], history=list(hist),
                           pot=s.pot + added, to_call=to - rp.bet,
                           min_raise_to=min(rp.stack + rp.bet, to + max(BB, to - BB)),
                           max_raise_to=rp.stack + rp.bet, my_stack=rp.stack, my_bet=rp.bet,
                           my_seat=rp.seat, position=rp.position, opp_bet=to,
                           opp_stack=max(p.stack for p in players if p.seat != rp.seat),
                           is_button=rp.seat == s.button_seat)
            freq = {"fold": 0.0, "call": 0.0, "raise": 0.0, "jam": 0.0}
            for h in ORDER:
                combo = combo_for(h, dead)
                if not combo:
                    continue
                bot.plan3 = None
                try:
                    mv = bot.act(replace(st_r, hole=combo))
                except Exception:
                    continue
                if mv[0] == "fold":
                    key = "fold"
                elif mv[0] in ("call", "check"):
                    key = "call"
                else:
                    key = "jam" if mv[1] >= st_r.max_raise_to - 1 else "raise"
                freq[key] += COMBOS[h]
            tot = sum(freq.values()) or 1
            freq = {a: v / tot for a, v in freq.items()}
            resp.append({"pos": r.position, "stack": spot["stacks"][k], "k": k, **freq})
            hist.append(("preflop", r.seat, "fold", 0))   # (the next player faces it alone)
            players = [replace(p, folded=True) if p.seat == r.seat else p for p in players]
        st, pays = spot["stacks"], spot["pays"]
        val = I.icm_value(pays)
        sp = I.Spot(st, val, me)
        to_bb = to / BB
        p_all_fold = 1.0
        for r in resp:
            p_all_fold *= r["fold"]
        # Open and fold to a re-raise: the 2 bb are gone (to the big blind, roughly).
        after = [x - p for x, p in zip(st, sp.post)]
        after[me] -= to_bb
        after[-1] += sum(sp.post) + to_bb
        lose_open = I.icm(after, pays)[me] - sp.v_fold[me]
        steal = sp.v_steal[me] - sp.v_fold[me]
        caller = max(resp, key=lambda r: r["call"]) if resp else None
        adv = self.advice
        out = {"to": to_bb, "resp": resp, "p_all_fold": p_all_fold, "steal": steal,
               "open_fold": lose_open, "pot": sum(sp.post), "hand": spot["hand"],
               "sims": describe(adv, s) if adv else None}
        if caller is not None and caller["call"] > 0:
            k, n = caller["k"], len(st)
            blind_k = 1.0 if k == n - 1 else 0.5 if k == n - 2 else 0.0
            pot = sum(sp.post) + to_bb + (to_bb - blind_k)       # after the call
            behind = max(0.0, min(st[me] - to_bb,
                                  st[k] - to_bb - (I.ANTE if k == n - 1 else 0.0)))
            ip = caller["pos"] in ("SB", "BB") and names[me] != "SB"
            out.update(caller=caller["pos"], caller_call=caller["call"], spr=behind / pot,
                       flop_pot=pot, behind=behind, ip=ip)
        return out

    def _answers(self, s, players, hist, pot, to, last_raise, seats, weights=None):
        """How each player in `seats` (in turn; the ones before them folded) answers a raise
        to `to`: [(seat, position, {fold, call, raise, jam})], from the coach's bot (your
        sims with the final table's rules). weights: seat -> {hand: weight} (their range)."""
        if self.coach_bot is None:
            self.coach_bot = make_study(STUDY, 4243)
        bot, dead, out = self.coach_bot, set(s.hole), []
        players, hist = list(players), list(hist)
        for seat in seats:
            rp = next(p for p in players if p.seat == seat)
            others = [p.stack for p in players if p.seat != seat and not p.folded]
            st_r = replace(s, players=[replace(p) for p in players], history=list(hist), pot=pot,
                           to_call=max(0, to - rp.bet),
                           min_raise_to=min(rp.stack + rp.bet, to + max(BB, last_raise)),
                           max_raise_to=rp.stack + rp.bet, my_stack=rp.stack, my_bet=rp.bet,
                           my_seat=seat, position=rp.position, opp_bet=to,
                           opp_stack=max(others) if others else 0,
                           is_button=seat == s.button_seat)
            freq = {"fold": 0.0, "call": 0.0, "raise": 0.0, "jam": 0.0}
            per_hand = {}
            w = (weights or {}).get(seat)
            for h in ORDER:
                wt = COMBOS[h] * (w.get(h, 0.0) if w is not None else 1.0)
                combo = combo_for(h, dead) if wt > 0 else None
                if not combo:
                    continue
                bot.plan3 = None
                try:
                    mv = bot.act(replace(st_r, hole=combo))
                except Exception:
                    continue
                if mv[0] == "fold":
                    key = "fold"
                elif mv[0] in ("call", "check"):
                    key = "call"
                else:
                    key = "jam" if mv[1] >= st_r.max_raise_to - 1 else "raise"
                freq[key] += wt
                per_hand[h] = key
            tot = sum(freq.values()) or 1.0
            out.append((seat, rp.position, {a: v / tot for a, v in freq.items()}, per_hand))
            hist.append(("preflop", seat, "fold", 0))
            players = [replace(p, folded=True) if p.seat == seat else p for p in players]
        return out

    def postflop_analysis(self, spot):
        """For the coach after the flop: what you have, your equity against each opponent's
        range (as the hand tree has narrowed it), the SPR, and your sims' / solve's play."""
        from cards import category
        s = spot["state"]
        board = [card(c) for c in s.board]
        hole = [card(c) for c in s.hole]
        rng = random.Random(7)
        made = describe_hand(s.hole, s.board)
        opps = []
        live = [p for p in s.players if p.seat != s.my_seat and not p.folded]
        for p in live:
            types = self.analyst.ranges.get(p.seat, ORDER)
            combos = [c for t in types for c in type_combos(t)]
            eq = equity_vs([tuple(hole)], [combos], board, 500, rng)
            opps.append({"pos": p.position, "eq": eq, "range": sum(COMBOS[t] for t in types)
                         / 13.26, "stack": (p.stack + p.bet) / BB})
        allin = None
        if len(live) > 1:
            allin = equity_vs([tuple(hole)], [[c for t in self.analyst.ranges.get(p.seat, ORDER)
                                               for c in type_combos(t)] for p in live],
                              board, 500, rng)
        behind = min(s.my_stack, max(p.stack for p in live)) if live else s.my_stack
        pot = s.pot
        adv = self.advice
        sim = self.hand_sim if s.street == "flop" else None
        return {"made": made, "opps": opps, "multi": allin, "spr": behind / pot if pot else 0,
                "pot": pot / BB, "behind": behind / BB, "street": s.street,
                "to_call": min(s.to_call, s.my_stack) / BB,
                "bet": ((adv[1] - s.my_bet) / BB if adv and adv[0] == "raise" else None),
                "bet_allin": bool(adv and adv[0] == "raise" and adv[1] >= s.max_raise_to - 1),
                "play": describe(adv, s) if adv else "?",
                "solve": (f"your Pio solve{sim_label(sim, s)}" if sim is not None else None),
                "hand": spot["hand"], "cat": category(evaluate(hole + board))}

    def vs3bet_analysis(self, spot):
        """For the coach: you opened and got 3-bet. The 3-bettor's range (how the bots 3-bet
        your open) and your equity against it; folding, flatting, a small 4-bet, a jam."""
        import icm_study as I
        s = spot["state"]
        acts = [(i, seat, a, amt) for i, (st_, seat, a, amt) in
                enumerate(h for h in s.history if h[0] == "preflop") if a != "fold"]
        if (len(acts) != 2 or acts[0][2] != "raise" or acts[1][2] != "raise" or
                acts[0][1] != s.my_seat or not s.to_call):
            return None
        names = position_names(len(s.players))
        by_pos = {p.position: p for p in s.players}
        order = [by_pos[nm] for nm in names]
        me = next(i for i, p in enumerate(order) if p.seat == s.my_seat)
        k = next(i for i, p in enumerate(order) if p.seat == acts[1][1])
        tb = order[k]
        if tb.all_in or tb.stack == 0:
            return None          # a jam: the call / fold coach covers it
        open_to, to3 = acts[0][3], acts[1][3]
        st, pays = spot["stacks"], spot["pays"]
        hnames, idx = I.exact()[0], I.exact()[1]
        h = idx[spot["hand"]]
        # Their 3-bet range: how the bot in their seat answers your open, hand by hand.
        posts_then = FTL._posts(s, order, upto=acts[1][0])
        tb_before = posts_then[k] * BB
        players0 = []
        for i, p in enumerate(order):
            if p.seat == tb.seat:
                players0.append(replace(p, bet=int(round(tb_before)),
                                        stack=p.stack + p.bet - int(round(tb_before)),
                                        folded=False))
            elif i == me:
                players0.append(replace(p))
            else:
                players0.append(replace(p, folded=(i < k)))
        hist0 = [h_ for h_ in s.history if h_[0] == "preflop"][:acts[1][0]]
        pot0 = s.pot - (tb.bet - int(round(tb_before)))
        ans = self._answers(s, players0, hist0, pot0, open_to, open_to - BB, [tb.seat])
        per = ans[0][3] if ans else {}
        range3 = {x: 1.0 if per.get(x) == "raise" else 0.0 for x in hnames}
        if sum(range3.values()) == 0:
            from bots.preflop8 import TOP
            range3 = {x: 1.0 if TOP[x] <= 0.08 else 0.0 for x in hnames}
        r3_list = [range3[x] for x in hnames]
        eq = I.range_eq(h, I._sparse(r3_list))
        share = I._share_w(r3_list)
        # Prize money: folding now, and a jam (they call with part of their 3-bet range).
        posts = FTL._posts(s, order)
        val = I.icm_value(pays)
        sp = I.Spot(st, val, me, posts=posts, responders=[k], fold_to=k)
        me_p = order[me]
        jam_players = [replace(p, bet=p.stack + p.bet, stack=0) if p.seat == me_p.seat else p
                       for p in s.players]
        jam_to = me_p.stack + me_p.bet
        jam_hist = list(s.history) + [("preflop", me_p.seat, "raise", jam_to)]
        a_jam = self._answers(s, jam_players, jam_hist, s.pot + me_p.stack, jam_to,
                              jam_to - to3, [tb.seat], {tb.seat: range3})
        f_jam, per_jam = (a_jam[0][2], a_jam[0][3]) if a_jam else ({"fold": 1.0}, {})
        p_call = 1.0 - f_jam.get("fold", 1.0)
        call_w = [1.0 if range3[x] and per_jam.get(x) in ("call", "jam", "raise") else 0.0
                  for x in hnames]
        eq_call = (I.eq_icm(h, I._sparse(call_w), sp.chop_share(k, me))
                   if sum(call_w) else 0.0)
        jam_ev = (1 - p_call) * sp.v_steal[me] + p_call * (
            eq_call * sp.win[k][me] + (1 - eq_call) * sp.lose[k][me])
        # A small 4-bet: how they answer it.
        to4 = min(me_p.stack + me_p.bet, int(round(2.2 * to3)))
        small = None
        eff_chips = min(me_p.stack + me_p.bet, tb.stack + tb.bet)
        if to4 < me_p.stack + me_p.bet and to4 < FTL.COMMIT_SHARE * eff_chips:
            # (a 4-bet of half the stack or more is an all-in: no "small" 4-bet then)
            p4 = [replace(p, bet=to4, stack=p.stack - (to4 - p.bet)) if p.seat == me_p.seat else p
                  for p in s.players]
            a4 = self._answers(s, p4, list(s.history) + [("preflop", me_p.seat, "raise", to4)],
                               s.pot + to4 - me_p.bet, to4, to4 - to3, [tb.seat],
                               {tb.seat: range3})
            small = a4[0][2] if a4 else None
        to3_bb, open_bb = to3 / BB, open_to / BB
        dead = sum(sp.post) - posts[me] - posts[k]
        pot_flat = dead + 2 * to3_bb
        behind = max(0.0, min(st[me], st[k]) - to3_bb)
        post_order = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
        ip = post_order.index(order[me].position) > post_order.index(tb.position)
        adv = self.advice
        return {"who": tb.position, "who_stack": st[k], "open_to": open_bb, "to3": to3_bb,
                "share": share, "eq": eq, "fold": sp.v_fold[me], "jam_gain": jam_ev - sp.v_fold[me], "p_call": p_call,
                "eq_call": eq_call, "jam_to": jam_to / BB, "small": small, "to4": to4 / BB,
                "pot_flat": pot_flat, "behind": behind, "spr": behind / pot_flat if pot_flat else 0,
                "ip": ip, "sims": describe(adv, s) if adv else "?", "hand": spot["hand"],
                "rp": I.risk_premiums(st, val)[me][k]}

    def facing_analysis(self, spot):
        """For the coach: facing one open preflop. The opener's range and your equity against
        it, your sims' play, a 3-bet (how everyone answers it, prize money, SPR) and a call."""
        import icm_study as I
        s = spot["state"]
        acts = [(seat, a, amt) for st_, seat, a, amt in s.history
                if st_ == "preflop" and a != "fold"]
        if len(acts) != 1 or acts[0][1] != "raise" or not s.min_raise_to:
            return None
        names = position_names(len(s.players))
        by_pos = {p.position: p for p in s.players}
        order = [by_pos[nm] for nm in names]
        n = len(order)
        me = next(k for k, p in enumerate(order) if p.seat == s.my_seat)
        j = next(k for k, p in enumerate(order) if p.seat == acts[0][0])
        opener = order[j]
        if opener.all_in:
            return None
        st, pays = spot["stacks"], spot["pays"]
        open_to = acts[0][2] / BB
        eff_op = min(st[j], max(st[k] for k in range(n) if k != j))
        prior_list = FTL.open_range(opener.position, eff_op)
        hnames = I.exact()[0]
        prior = dict(zip(hnames, prior_list))
        h = I.exact()[1][spot["hand"]]
        eq = I.range_eq(h, I._sparse(prior_list))
        share = I._share_w(prior_list)
        sizes = self.sizes(s)
        to3 = sizes[0] if sizes else min(s.max_raise_to, int(3 * acts[0][2]))
        me_p = order[me]
        added = to3 - me_p.bet
        players = [replace(p, bet=to3, stack=p.stack - added) if p.seat == me_p.seat else p
                   for p in s.players]
        hist = list(s.history) + [("preflop", me_p.seat, "raise", to3)]
        seats = [order[k].seat for k in range(me + 1, n) if not order[k].folded] + [opener.seat]
        answers = self._answers(s, players, hist, s.pot + added, to3, to3 - acts[0][2], seats,
                                {opener.seat: prior})
        p_all_fold = 1.0
        for _, _, f, _ in answers:
            p_all_fold *= f["fold"]
        posts = FTL._posts(s, order)
        val = I.icm_value(pays)
        sp = I.Spot(st, val, me, posts=posts, fold_to=j)
        to3_bb = to3 / BB
        after = [x - p for x, p in zip(st, sp.post)]
        after[me] -= to3_bb - posts[me]
        after[j] += sum(sp.post) + to3_bb - posts[me]
        three_fold = I.icm(after, pays)[me] - sp.v_fold[me]
        steal = sp.v_steal[me] - sp.v_fold[me]
        dead = sum(sp.post) - posts[me] - posts[j]           # blinds, antes left in the pot
        pot3 = dead + 2 * to3_bb
        behind3 = min(st[me], st[j]) - to3_bb
        post_order = ["SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
        ip = post_order.index(order[me].position) > post_order.index(opener.position) \
            if order[me].position in post_order and opener.position in post_order else False
        pot_call = dead + 2 * open_to
        behind_call = min(st[me], st[j]) - open_to
        adv = self.advice
        sims = describe(adv, s) if adv else "?"
        return {"opener": opener.position, "open_to": open_to, "open_share": share, "eq": eq,
                "sims": sims, "to3": to3_bb,
                "answers": [(p, st[[x.seat for x in order].index(seat)], f)
                            for seat, p, f, _ in answers],
                "p_all_fold": p_all_fold, "steal": steal, "three_fold": three_fold,
                "pot": sum(sp.post), "pot3": pot3, "spr3": behind3 / pot3 if pot3 else 0,
                "behind3": behind3, "ip": ip, "pot_call": pot_call,
                "spr_call": behind_call / pot_call if pot_call else 0,
                "behind_call": behind_call, "hand": spot["hand"],
                "squeezers": len(seats) - 1}

    # ----- the leak tracker -----
    def ft_record_leaks(self):
        """End of a final-table hand: every decision of yours goes in the session's leak
        log (an ICM-graded all-in decision by its prize money, anything else by the EV it
        gave up, in dollars at your chips' value), the bad ones as blunders."""
        import icm_study as I
        ft, log = self.ft, self.ft.leaks
        button, stacks = self.hand_start
        if not stacks:
            return
        n = len(stacks)
        stack_bb = stacks[0] / BB
        eq0 = I.icm(stacks, ft.pays(n))[0]
        per_bb = eq0 / max(stack_bb, 1.0)        # what a big blind of yours is worth
        for node in self.tree:
            if not node.get("hero") or node.get("auto") or node.get("verdict") is None or \
                    node.get("logged"):
                continue
            node["logged"] = True
            hole = "".join(node.get("hole") or [])
            board = " " + "".join(node.get("board") or []) if node.get("board") else ""
            where = f"Hand {ft.hands} \u00b7 {node['pos']} {stack_bb:.0f} bb \u00b7 {hole}{board}"
            if node.get("icm_kind"):
                kind = node["icm_kind"]
                log.spot(kind)
                right, g, pool = node["icm"]
                if right:
                    continue
                act = node.get("icm_act") or "go"
                did = "folded" if act == "fold" else ("called" if kind == "call" else "jammed")
                best = "folding" if act != "fold" else ("calling" if kind == "call" else
                                                         "jamming")
                cat = f"{FTL.ICM_KINDS[kind]}: too {'loose' if act != 'fold' else 'tight'}"
                log.add(cat, kind, abs(g), f"{where} \u00b7 {FTL.ICM_KINDS[kind]}: you {did}; "
                        f"{best} was {money_k(abs(g))} better", ft.hands)
                continue
            street = node.get("street", "preflop")
            kind = f"{street.title()} decisions"
            log.spot(kind)
            cap = caption_for(node)
            if not cap or cap[0] not in ("Dustreg decision", "Fish decision"):
                continue
            chosen = node.get("chosen")
            evs = node.get("ev") or {}
            if evs and node.get("ev_n", 0) >= 40 and chosen in evs:
                best = max(evs, key=evs.get)
                loss_bb, approx = evs[best] - evs[chosen], ""
            else:
                best = node.get("bot") or chosen
                loss_bb, approx = (1.0 if cap[0].startswith("Dustreg") else 2.5), "about "
            if chosen is None or best is None:
                continue
            cat = f"{street.title()}: {chosen[0]} instead of {best[0]}"
            log.add(cat, kind, loss_bb * per_bb,
                    f"{where} \u00b7 {street}: you {key_label(chosen).lower()}; "
                    f"{key_label(best).lower()} was {approx}{loss_bb:.1f} bb better "
                    f"({approx}{money_k(loss_bb * per_bb)})", ft.hands)

    def open_leaks(self):
        """The leak tracker: this session's biggest blunders and leaks, and every session."""
        if self.ft is None:
            return
        win = getattr(self, "leak_win", None)
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        top = tk.Toplevel(self.root)
        top.title("Leak tracker")
        top.configure(bg=C["bg"])
        self.leak_win = top
        cv = tk.Canvas(top, width=700, height=640, bg=C["bg"], highlightthickness=0)
        bar = tk.Scrollbar(top, command=cv.yview)
        cv.configure(yscrollcommand=bar.set)
        cv.pack(side="left", fill="both", expand=True)
        top.bind("<MouseWheel>", lambda e: cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        y = 22

        def head(t, col=None):
            nonlocal y
            y += 10
            cv.create_text(20, y, text=t, fill=col or C["gold"], anchor="w", font=FH(12))
            y += 24

        def line(t, col=None, bold=False, x=28):
            nonlocal y
            item = cv.create_text(x, y, text=t, fill=col or C["text"], anchor="nw",
                                  font=FH(10) if bold else F(10), width=650 - x)
            b = cv.bbox(item)
            y = (b[3] if b else y + 16) + 6

        ft, log = self.ft, self.ft.leaks
        cv.create_text(20, y, text=f"Leak tracker  \u00b7  final table #{ft.table_no}",
                       fill=C["text"], anchor="w", font=FH(15))
        y += 28
        spots = sum(log.spots.values())
        line(f"This session: {ft.hands} hands, {spots} decisions, {len(log.entries)} blunders, "
             f"{money_k(log.lost())} given up.", col=C["dim"])
        head("Your 3 biggest blunders")
        if not log.entries:
            line("None yet.", col=C["dim"])
        for k, e in enumerate(log.top(3), 1):
            line(f"{k}.  {money_k(e['cost'])}  \u00b7  {e['cat']}", col=C["bad"], bold=True)
            line(e["text"], x=48)
        head("Leaks this session (how often, of the spots you played)")
        types = log.by_type()
        if not types:
            line("No leaks yet.", col=C["dim"])
        for cat, (times, n_spots, cost) in types.items():
            pct = f"{100 * times / n_spots:.0f}% of {n_spots}" if n_spots else f"{times}"
            line(f"{cat}:  {times}x ({pct} spots)  \u00b7  {money_k(cost)}")
        hist = FTL.load_history()
        spots_all, leaks_all = FTL.all_time(hist, None if log.saved else log)
        head("All sessions: your leaks")
        if not leaks_all:
            line("Nothing recorded yet.", col=C["dim"])
        for cat, (times, cost) in list(leaks_all.items())[:8]:
            kind_spots = sum(v for kk, v in spots_all.items()
                             if cat.startswith(FTL.ICM_KINDS.get(kk, kk.split()[0])))
            pct = f" ({100 * times / kind_spots:.0f}% of {kind_spots})" if kind_spots else ""
            line(f"{cat}:  {times}x{pct}  \u00b7  {money_k(cost)}")
        head("Past sessions")
        if not hist:
            line("Sessions are saved when they end (bust, win, End session, New session).",
                 col=C["dim"])
        for h in reversed(hist[-12:]):
            fin = h.get("finish")
            res = (f"{ordinal(fin[1])} {money_k(fin[2])}" if fin and fin[1] else
                   f"ended at {money_k(fin[2])}" if fin else "left for a new session")
            worst = next(iter(h.get("by_type", {})), "no leaks")
            line(f"{h['date']}  \u00b7  table #{h['table']}  \u00b7  {h['hands']} hands  "
                 f"\u00b7  {res}  \u00b7  {h['blunders']} blunders ({money_k(h['lost'])})  "
                 f"\u00b7  biggest leak: {worst}", col=C["dim"])
        view = min(y + 20, max(400, top.winfo_screenheight() - 120))
        cv.configure(height=view, scrollregion=(0, 0, 700, y + 20))
        if view < y + 20:
            bar.pack(side="right", fill="y")

    def open_icm_coach(self):
        if self.icm_spot is None:
            return
        import icm_study
        FTL.ensure_solved(self.icm_spot)
        if self.icm_spot.get("street") != "preflop" and "post" not in self.icm_spot:
            self.icm_spot["post"] = self.postflop_analysis(self.icm_spot)
        need_open = (self.icm_spot.get("first_in") and "open2x" not in self.icm_spot and
                     self.icm_spot.get("eff", 99) >= 8)   # (no 2x opens under 8 bb)
        need_face = self.icm_spot.get("street") == "preflop" and "facing" not in self.icm_spot \
            and not self.icm_spot.get("first_in")
        if need_face and "vs3bet" not in self.icm_spot:
            self.icm_spot["vs3bet"] = self.vs3bet_analysis(self.icm_spot)
        if need_open or need_face:
            self.root.configure(cursor="watch")
            self.root.update()
            try:
                if need_open:
                    self.icm_spot["open2x"] = self.open_analysis(self.icm_spot)
                if need_face:
                    self.icm_spot["facing"] = self.facing_analysis(self.icm_spot)
            finally:
                self.root.configure(cursor="")
        if self.coach_host is not None and self.coach_host.coach is not None:
            try:
                self.coach_host.coach.destroy()
            except tk.TclError:
                pass
        self.coach_host = icm_study.CoachHost(sys.modules[__name__], self.root, self.icm_spot)
        self.coach_host.open_coach()

    def open_details(self, node):
        if self.details is None or not self.details.alive or \
                not self.details.top.winfo_exists():
            self.details = DetailsWindow(self)
        self.details.show(node)

    def open_chart(self, node=None):
        if self.chart is None or not self.chart.alive:
            self.chart = ChartWindow(self)
        self.chart.show(node)

    def refresh_chart(self, new_hand=False):
        if self.chart is not None and self.chart.alive:
            if new_hand:
                self.chart.node = None
            self.chart.show()

    def button(self, x, y, w, h, text, color, cmd, sub=None, enabled=True, size=12,
               radius=8, fg="white", outline=""):
        tag = f"btn{len(self._tags)}"
        self._tags.append(tag)
        fill = color if enabled else C["card"]
        body = self.rrect(x, y, x + w, y + h, radius, fill=fill, outline=outline, tags=(tag,))
        col = fg if enabled else C["faint"]
        if sub:
            self.cv.create_text(x + w / 2, y + h / 2 - 8, text=text, fill=col, tags=(tag,),
                                font=FH(size))
            self.cv.create_text(x + w / 2, y + h / 2 + 12, text=sub, tags=(tag,),
                                fill=shade(col, -0.22) if enabled else col, font=F(8))
        else:
            self.cv.create_text(x + w / 2, y + h / 2, text=text, fill=col, tags=(tag,),
                                font=FH(size))
        if enabled:   # (one click handler for the canvas: see on_click; binding every
            # button on every redraw was most of the redraw time)
            self._hits.append((x, y, x + w, y + h, cmd, body, fill))

    def chip(self, x, y, w, text, on, cmd, enabled=True, h=28):
        """A small toggle pill (for settings)."""
        if on:
            self.button(x, y, w, h, text, C["accent"], cmd, size=10, radius=7)
        else:
            self.button(x, y, w, h, text, C["card2"], cmd, size=10, radius=7, fg=C["dim"],
                        enabled=enabled)

    def card(self, x, y, c, w=46, h=64, dim=False):
        self.card_on(self.cv, x, y, c, w, h, dim)

    def card_on(self, cv, x, y, c, w=46, h=64, dim=False):
        rr = max(3, min(7, int(w * 0.14)))
        if w > 6:   # a soft shadow under every card
            self.rrect_on(cv, x + 2, y + 3, x + w + 2, y + h + 3, rr, fill="#05070a", outline="")
        if c is None:  # face down: a violet back with a diamond lattice
            self.rrect_on(cv, x, y, x + w, y + h, rr, fill="#e9e6ff", outline="")
            self.rrect_on(cv, x + 2, y + 2, x + w - 2, y + h - 2, max(2, rr - 1), fill="#4a3aa6",
                          outline="")
            if w >= 14:
                step = max(5, int(w / 4))
                for gx in range(int(x + 5), int(x + w - 4), step):
                    for gy in range(int(y + 5), int(y + h - 4), step):
                        cv.create_polygon(gx, gy - 2, gx + 2, gy, gx, gy + 2, gx - 2, gy,
                                          fill="#7d6cf0", outline="")
            return
        # TV style: a white face, a big rank over a big suit, four-colour suits.
        sym, col = SUIT_TV[c[1]]
        face, edge = ("#9a9ea6", "#7d828b") if dim else ("#f7f8fa", "#c9ced6")
        if dim:
            col = shade(col, 0.35)
        self.rrect_on(cv, x, y, x + w, y + h, rr, fill=face, outline=edge)
        if w > 10:   # a faint sheen at the bottom
            self.rrect_on(cv, x + 2, y + h * 0.62, x + w - 2, y + h - 2, max(2, rr - 2),
                          fill="#8f939a" if dim else "#eceef2", outline="")
        if w < 12:
            return
        rank = "10" if c[0] == "T" else c[0]
        big = w >= 30
        cv.create_text(x + w * 0.5, y + h * (0.31 if big else 0.32), text=rank, fill=col,
                       font=FH(int(h * (0.38 if rank != "10" else 0.33))))
        cv.create_text(x + w * 0.5, y + h * 0.71, text=sym, fill=col, font=F(int(h * 0.36)))

    def table_geom(self):
        y_top = TOP + STRIP
        cy = y_top + (H - y_top - BOTTOM) / 2 + 4
        return TABLE_W / 2, cy, 330, 168  # centre, half width, half height (stadium)

    def seat_xy(self, seat, inward=0.0):
        """A point on the stadium-shaped rail: you at the bottom, then clockwise."""
        cx, cy, a, b = self.table_geom()
        straight = 2 * (a - b)
        per = 2 * straight + 2 * math.pi * b
        d = (seat / self.n) * per
        segs = [("line", straight / 2), ("arc_l", math.pi * b), ("top", straight),
                ("arc_r", math.pi * b), ("line2", straight / 2)]
        for kind_, length in segs:
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

    # ----- the whole screen -----
    def on_click(self, e):
        for x1, y1, x2, y2, cmd, _, _ in reversed(self._hits):
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                cmd()
                return

    def on_motion(self, e):
        hit = next((h for h in reversed(self._hits)
                    if h[0] <= e.x <= h[2] and h[1] <= e.y <= h[3]), None)
        if hit is self._hover:
            return
        try:
            if self._hover is not None and self._hover[5] is not None:
                self.cv.itemconfigure(self._hover[5], fill=self._hover[6])
            if hit is not None and hit[5] is not None:
                self.cv.itemconfigure(hit[5], fill=shade(hit[6], 0.1))
        except tk.TclError:
            pass
        self.cv.configure(cursor="hand2" if hit is not None else "")
        self._hover = hit

    def draw(self):
        self._hits, self._hover = [], None
        self.cv.delete("all")
        self._tags = []
        self.draw_table()
        self.draw_back()
        self.draw_panel()
        self.draw_strip()
        self.draw_actions()
        self.draw_top()

    def draw_back(self):
        """The ◀ button on the table: look back at earlier hands."""
        y = TOP + STRIP + 10
        if self.view is not None:
            w = text_w("Looking back  \u00b7  the table is paused", F(10)) + 28
            self.rrect(16, y, 16 + w, y + 30, 15, fill=C["card2"], outline="")
            self.cv.create_text(16 + w / 2, y + 15, text="Looking back  \u00b7  the table is paused",
                                fill=C["text"], font=F(10))
            return
        older = len(self.past) - (1 if self.finished() else 0)
        if older > 0:
            self.button(16, y, 168, 32, "\u25c0  Previous hand", C["card2"], self.go_back,
                        size=10, radius=16)

    def draw_top(self):
        cv = self.cv
        cv.create_rectangle(0, 0, W, TOP, fill=C["panel"], outline="")
        cv.create_line(0, TOP, W, TOP, fill=C["line"])
        # Logo: two overlapping card shapes.
        self.rrect(20, 15, 36, 37, 4, fill=C["accent"], outline="")
        self.rrect(27, 12, 43, 34, 4, fill=shade(C["accent"], 0.35), outline="")
        cv.create_text(54, TOP / 2, text="Practice", fill=C["text"], anchor="w", font=FH(15))
        cv.create_text(58 + text_w("Practice", FH(15)), TOP / 2, text="table", fill=C["dim"],
                       anchor="w", font=F(14))
        x = 230
        self.rrect(x, 11, x + 436, 41, 9, fill=C["card"], outline="")
        bx = x + 4
        for m, w in (("Play", 80), ("Drill", 80), ("Spots", 80), ("Final table", 104)):
            on = m == self.mode and not self.icm_view
            self.button(bx, 15, w, 22, m, C["accent"] if on else C["card"],
                        lambda v=m: self.set_mode(v), size=10, radius=6,
                        fg="white" if on else C["dim"])
            bx += w + 2
        # ICM study (and Mega Satellites) opens in its own window.
        self.button(bx, 15, 80, 22, "ICM", C["gold"] if self.icm_view else C["card"],
                    self.open_icm, size=10, radius=6,
                    fg="#14171c" if self.icm_view else C["gold"])
        if self.mode in ("Drill", "Spots"):
            x = 676
            cv.create_text(x, TOP / 2, text="Difficulty", fill=C["dim"], anchor="w", font=F(9))
            for i, level in enumerate(("Medium", "Expert")):
                self.chip(x + 66 + i * 80, 12, 74, level, self.difficulty == level,
                          lambda v=level: self.set_attr("difficulty", v))
        if self.mode in ("Play", "Final table"):
            x = 680
            for i, (label, v) in enumerate(SPEEDS):
                self.chip(x + i * 55, 12, 51, label, self.speed == v,
                          lambda v=v: self.set_speed(v))
        x = W - 146 - 2 * 116
        for label, attr in (("Fast fold", "fast_fold"), ("Playable only", "skip_trash")):
            if attr == "skip_trash" and self.mode == "Drill":
                label, attr = "Postflop", "drill_postflop"
            elif attr == "skip_trash" and self.mode == "Final table":
                label, attr = "Skip folds", "ft_skip"
            elif attr == "skip_trash" and self.mode != "Play":
                x += 116
                continue
            on_ = getattr(self, attr)
            self.button(x, 11, 108, 30, f"{label} {'on' if on_ else 'off'}",
                        C["card2"] if on_ else C["card"], lambda a=attr: self.toggle(a),
                        size=9, radius=8, fg=C["text"] if on_ else C["faint"])
            x += 116
        on = self.show_hint
        self.button(W - 146, 11, 128, 30, "Hint on" if on else "Hint off",
                    C["accent"] if on else C["card2"], self.toggle_hint, size=10, radius=8,
                    fg="white" if on else C["dim"])

    def timeline(self):
        """The hand so far, as (kind, label, detail) items for the strip."""
        s = self.last_state
        if s is None:
            return []
        hist = self.final["history"] if self.final else s.history
        board = self.final["board"] if self.final else s.board
        names = (self.final["positions"] if self.final else
                 {p.seat: p.position for p in s.players})
        items, street, raised = [], "preflop", True  # preflop starts with the blinds "bet"
        for st, seat, a, amt in hist:
            if st != street:
                street, raised = st, False
                cards = {"flop": board[:3], "turn": board[3:4], "river": board[4:5]}[st]
                items.append(("street", st.upper(), cards))
            word = {"fold": "Fold", "check": "Check", "call": "Call"}.get(a)
            if a == "raise":
                word = f"{'Raise' if raised else 'Bet'} {bb(amt)}"
                raised = True
            items.append(("act", "You" if seat == 0 else names[seat], word))
        cur = s.street
        if not self.final and cur != "preflop" and cur != street:
            cards = {"flop": board[:3], "turn": board[3:4], "river": board[4:5]}[cur]
            items.append(("street", cur.upper(), cards))
        if self.final:
            board_left = {0: "FLOP", 3: "TURN", 4: "RIVER"}
            shown = len([i for i in items if i[0] == "street"])
            if self.final.get("shown") and len(board) == 5 and shown < 3:
                for k, st in ((0, "flop"), (3, "turn"), (4, "river")):
                    label = board_left[k]
                    if not any(i[0] == "street" and i[1] == label for i in items):
                        cards = board[:3] if k == 0 else board[k:k + 1]
                        items.append(("street", label, cards))
        elif self.state is not None:
            items.append(("you", "You", "Your move"))
        return items

    def draw_strip(self):
        cv = self.cv
        y0 = TOP
        cv.create_rectangle(0, y0 + 1, TABLE_W, y0 + STRIP, fill=C["bg"], outline="")
        cv.create_line(0, y0 + STRIP, TABLE_W, y0 + STRIP, fill=C["line"])
        items = self.timeline()
        widths = []
        for k, label, detail in items:
            if k == "street":
                widths.append(20 + text_w(label, FH(9)) + 24 * len(detail))
            else:
                widths.append(34 + text_w(label, FH(9)) + text_w(detail, FH(9)))
        # Show the most recent items that fit.
        x_max = TABLE_W - 16
        total, start = 0, len(items)
        while start > 0 and total + widths[start - 1] + 6 <= x_max - 16:
            start -= 1
            total += widths[start] + 6
        x, y1, y2 = 16, y0 + 10, y0 + STRIP - 10
        if start > 0:
            cv.create_text(x, (y1 + y2) / 2, text="‹", fill=C["faint"], font=FH(14))
            x += 14
        for (k, label, detail), w in zip(items[start:], widths[start:]):
            if k == "street":
                self.rrect(x, y1, x + w, y2, 7, fill=C["card"], outline="")
                cv.create_text(x + 10, (y1 + y2) / 2, text=label, fill=C["dim"], anchor="w",
                               font=FH(9))
                cx = x + 14 + text_w(label, FH(9))
                for c in detail:
                    self.card(cx, y1 + 3, c, 21, y2 - y1 - 6)
                    cx += 24
            else:
                you_turn = k == "you"
                self.rrect(x, y1, x + w, y2, 7, fill=C["card"] if not you_turn else C["bg"],
                           outline=C["accent"] if you_turn else "", width=2)
                lw = 16 + text_w(label, FH(9))
                cv.create_text(x + 10, (y1 + y2) / 2, text=label, anchor="w",
                               fill=C["accent"] if label == "You" else C["dim"], font=FH(9))
                self.rrect(x + lw, y1 + 4, x + w - 4, y2 - 4, 5,
                           fill=C["card2"] if not you_turn else C["bg"], outline="")
                cv.create_text(x + lw + (w - 4 - lw) / 2, (y1 + y2) / 2, text=detail,
                               fill=C["accent"] if you_turn else C["text"], font=FH(9))
            x += w + 6

    def spot_title(self):
        if self.mode == "Final table" and self.ft is not None:
            ft = self.ft
            left = ft.left_in_level()
            return (f"Final table #{ft.table_no} \u00b7 {ft.blinds()}"
                    + (f" (up in {left})" if left else "") +
                    f" \u00b7 {len(ft.alive())} left \u00b7 "
                    f"{self.ft_label()} \u00b7 big blind ante")
        if self.mode == "Spots" and self.hand_depth:
            sp = self.spot
            sim = self.current_sim() if sp["scen"] == "Flop sim" else None
            if sim is not None:
                pot = {"3BP": "3-bet pot", "4BP": "4-bet pot", "LP": "limped pot"}.get(
                    sim.kind, "single-raised pot")
                return f"{sim.opener} vs. {sim.other} \u00b7 {pot} \u00b7 Pio flop sim \u00b7 {sim.depth:g}bb"
            if sp["scen"] == "First in":
                return f"{sp['hero']} first in · {self.hand_depth:g}bb"
            if sp["scen"] in ("Facing open", "Defend → flop"):
                pair = f"{sp['villain']} vs. {sp['hero']}"
            else:
                pair = f"{sp['hero']} vs. {sp['villain']}"
            return f"{pair} · {SHORT[sp['scen']]} · {self.hand_depth:g}bb"
        if self.mode == "Drill" and self.hand_depth:
            return f"Drill · {self.drill_spot} · {self.hand_depth:g}bb"
        if self.hand_depth:
            return f"{self.hand_depth:g}bb deep · blinds 0.5 / 1 · ante 0.125"
        return "Blinds 0.5 / 1 · ante 0.125"

    def draw_table(self):
        cv = self.cv
        cx, cy, a, b = self.table_geom()
        cv.create_rectangle(0, TOP, TABLE_W, H - BOTTOM, fill=C["bg"], outline="")
        # The table: a shadow, a leather rail with a gold trim, and green felt that lightens
        # towards the middle, with a dashed betting line.
        self.stadium(cx, cy + 10, a + 34, b + 30, fill="#06080b", outline="")
        self.stadium(cx, cy, a + 26, b + 26, fill="#2a201b", outline="#3b2d25", width=2)
        self.stadium(cx, cy, a + 17, b + 17, fill="", outline="#47372c", width=1)
        self.stadium(cx, cy, a + 4, b + 4, fill="#0a281f", outline="#a5803c", width=2)
        for i, col in enumerate(FELT):
            self.stadium(cx, cy, a - i * 9, b - i * 7, fill=col, outline="")
        self.stadium(cx, cy, a - 70, b - 62, fill="", outline="#2e7a5f", width=1, dash=(4, 5))
        if not (self.toast and not self.notice and not self.anim_on()):   # (a message takes
            # the title's place)
            cv.create_text(cx, TOP + STRIP + 26, text=self.spot_title(), fill=C["dim"],
                           font=F(10))
        # The board, broadcast style: the cards in a dark panel, the pot in a bar below.
        px0, px1, py0 = cx - 146, cx + 146, cy - 40
        self.rrect(px0 - 2, py0 + 3, px1 + 2, cy + 63, 10, fill="#04070c", outline="")
        self.rrect(px0, py0, px1, cy + 58, 10, fill="#0b1a2e", outline="#1f3b5f", width=1)
        now = time.time()
        self.note_board(now)
        for i in range(5):
            x = cx - 138 + i * 56
            self.rrect(x, cy - 34, x + 52, cy + 34, 6, fill="#0f2238", outline="#1d3a5c",
                       width=1)
            if i < len(self.board):
                self.draw_board_card(i, x, cy - 34, now, w=52, h=68)
        bar_y = cy + 38
        cv.create_rectangle(px0 + 1, bar_y, px1 - 1, cy + 57, fill="#123a63", outline="")
        cv.create_rectangle(px0 + 1, bar_y, cx - 30, cy + 57, fill="#0e2d4d", outline="")
        cv.create_text(px0 + 12, bar_y + 10, text="POT", fill="#9fd3ff", anchor="w",
                       font=FH(10))
        if self.pot or getattr(self, "pot_final", 0):
            cv.create_text(px1 - 12, bar_y + 10, text=self.pot_text(), fill="white",
                           anchor="e", font=FH(11))
        if self.caption and (time.time() < self.caption_until or self.waiting_next or
                             self.autonext):
            text, col = self.caption
            w = text_w(text, FH(14)) + 40
            self.rrect(cx - w / 2, cy + 68, cx + w / 2, cy + 98, 15, fill=col, outline="")
            cv.create_text(cx, cy + 83, text=text, fill="white", font=FH(14))
        if self.notice:
            cv.create_text(cx, cy + 90, text=self.notice, fill=C["bad"], font=F(10, True),
                           width=520)
        elif self.toast and not self.anim_on():
            w = min(text_w(self.toast, F(10)) + 28, TABLE_W - 360)
            ty = TOP + STRIP + 13                        # in the line above the table
            self.rrect(cx - w / 2, ty, cx + w / 2, ty + 26, 13, fill=C["card2"], outline="")
            cv.create_text(cx, ty + 13, text=self.toast, fill=C["text"], font=F(10),
                           width=w - 20)
        for seat in range(min(self.n, len(self.seats))):
            self.draw_seat(seat)
        if self.mode == "Final table" and self.view is None:
            if self.icm_spot is not None:
                self.button(TABLE_W - 166, TOP + STRIP + 10, 150, 32, "ICM Coach", C["gold"],
                            self.open_icm_coach, size=10, radius=16, fg="#14171c")

    def runout_now(self):
        """During (and after) an all-in run-out: each shown hand's share of the pot with the
        board cards dealt so far, or None."""
        ro = getattr(self, "_runout", None)
        if not ro or self.view is not None:
            return None
        start, by_k = ro
        now = time.time()
        k = start + sum(1 for i in range(start, len(self.board))
                        if self._board_t.get(i) is None or now - self._board_t[i] >= 0.38)
        dealt = [x for x in by_k if x <= k]
        return by_k[max(dealt)] if dealt else None

    def pot_text(self):
        """'2.5 bb', or at a final table the chips as on TV: '400,000  ·  2.5 bb'."""
        pot = self.pot or getattr(self, "pot_final", 0)
        if self.mode == "Final table" and self.ft is not None:
            big = I_WSOP_BB(self.ft.level)
            return f"{pot / BB * big:,.0f}  \u00b7  {bb(pot)} bb"
        return f"{bb(pot)} bb"

    def draw_ft_over(self):
        """The session's result in the action bar: the table (both hands, the board, the
        swings) stays in view above it."""
        cv = self.cv
        x0, y0, x1, y1 = 16, H - BOTTOM + 8, TABLE_W - 16, H - 10
        self.rrect(x0, y0, x1, y1, 16, fill=C["panel"], outline=C["gold"], width=2)
        ft = self.ft
        if ft is None:
            title, col, sub = "Pick a final table to start a session", C["text"], ""
        else:
            kind, place, prize = ft.over
            if kind == "win":
                title, col = f"You won the final table!  {money_k(prize)}", C["good"]
            elif kind == "bust":
                title, col = f"You finished {ordinal(place)}:  {money_k(prize)}", C["gold"]
            else:
                title, col = f"Session ended: your stack was worth {money_k(prize)}", C["text"]
            r_ = ft.icm
            sub = (f"{ft.hands} hands  \u00b7  ICM push/fold decisions {r_['right']} / "
                   f"{r_['spots']}  \u00b7  mistakes cost {money_k(r_['lost'])}")
        cv.create_text(x0 + 22, y0 + 34, text=title, fill=col, font=FH(15), anchor="w")
        cv.create_text(x0 + 22, y0 + 66, text=sub, fill=C["dim"], font=F(9), anchor="w",
                       width=x1 - x0 - 470)
        bx, by = x1 - 450, y0 + (y1 - y0 - 50) / 2
        self.button(bx, by, 140, 50, "Play it again", C["accent"], self.ft_new, size=11)
        self.button(bx + 150, by, 140, 50, "Next table \u25b6", C["card2"],
                    lambda: self.ft_pick(self.ft_table + 1), size=11)
        self.button(bx + 300, by, 140, 50, "Random table", C["card2"], self.ft_random, size=11)

    # ----- moving pieces: the board deals in, chips -----
    def note_board(self, now):
        """New board cards get a start time: the flop one by one, the turn and river a beat
        apart (an all-in run-out deals out the same way)."""
        b = list(self.board)
        if self.view is not None:
            self._board_seen, self._board_t = b, {}
            return
        seen = self._board_seen
        if len(b) < len(seen) or b[:len(seen)] != seen:
            seen, self._board_t = [], {}
        if len(b) > len(seen):
            t = max(now, self._anim_until - 0.3)
            for i in range(len(seen), len(b)):
                self._board_t[i] = t
                t += 0.14 if i < 2 else 0.6
            self._anim_until = max(self._board_t.values()) + 0.42
        self._board_seen = b

    def draw_board_card(self, i, x, y, now, w=48, h=66, dur=0.38):
        start = self._board_t.get(i)
        p = 1.0 if start is None else (now - start) / dur
        if p >= 1:
            self.card(x, y, self.board[i], w, h)
            return
        if p <= 0:
            return
        e = 1 - (1 - p) ** 3                       # ease out
        cx, cy, _, _ = self.table_geom()
        sx, sy = cx + 150, cy - 150                 # from the dealer's spot
        px, py = sx + (x - sx) * e, sy + (y - sy) * e - 18 * math.sin(math.pi * e)
        squash = abs(1 - 2 * e)                     # the flip: narrow, then turn face up
        cw = max(2, w * squash)
        self.card(px + (w - cw) / 2, py, self.board[i] if e >= 0.5 else None, cw, h)

    def avatar(self, name, size, dim=False):
        """The player's avatar (avatars/<name>_<size>[_dim].png) as a Tk image, or None
        when there's no picture for the name (or the image can't be read)."""
        cache = self.__dict__.setdefault("_avatars", {})
        key = (name, size, dim)
        if key not in cache:
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            path = AVATARS / f"{slug}_{size}{'_dim' if dim else ''}.png"
            try:
                cache[key] = tk.PhotoImage(file=str(path)) if path.is_file() else None
            except tk.TclError:
                cache[key] = None
        return cache[key]

    def chip_stack(self, x, y, n, col, small=False):
        rx, ry, dy = (7, 3, 2) if small else (10, 4, 3)
        for k in range(n):
            yy = y - k * dy
            self.cv.create_oval(x - rx, yy - ry, x + rx, yy + ry, fill=col,
                                outline=shade(col, -0.35))
        top = y - (n - 1) * dy
        self.cv.create_oval(x - rx + 3, top - ry + 1, x + rx - 3, top + ry - 1, fill="",
                            outline="white", dash=(2, 2))

    def stadium(self, cx, cy, a, b, **kw):
        """A rectangle with fully rounded ends (a = half width, b = half height)."""
        pts = []
        for k in range(19):  # right end
            t = -math.pi / 2 + math.pi * k / 18
            pts += [cx + (a - b) + b * math.cos(t), cy + b * math.sin(t)]
        for k in range(19):  # left end
            t = math.pi / 2 + math.pi * k / 18
            pts += [cx - (a - b) + b * math.cos(t), cy + b * math.sin(t)]
        return self.cv.create_polygon(pts, **kw)

    def draw_seat(self, seat):
        d = self.seats[seat]
        if not d:
            return
        cv = self.cv
        x, y = self.seat_xy(seat)
        me = seat == 0
        folded = d.get("folded")
        cards = d.get("cards")
        r = 32 if me else 28
        turn = bool(d.get("turn"))
        name = self.seat_name(seat)
        colour = SEAT_COLOURS[self.seat_colour(seat) % len(SEAT_COLOURS)]
        # Cards beside the seat (to the right; on the left half for seats on the right end).
        cw, ch = (40, 58) if me else (24, 34)
        right_side = x <= TABLE_W / 2 + 40
        cxs = x + r + 6 if right_side else x - r - 6 - 2 * cw - 3
        if (not folded and d.get("in_hand", True)) or (d.get("reveal") and cards):
            for k in range(2):
                c = cards[k] if cards else None
                self.card(cxs + k * (cw + 3), y - ch / 2 - (6 if me else 4), c, cw, ch,
                          dim=bool(folded and d.get("reveal")))  # folded hands muted
        eqs = self.runout_now()
        if eqs is not None and seat in eqs and cards and not folded:
            e = eqs[seat]
            bx, by = cxs + cw + 1.5, y - ch / 2 - (6 if me else 4) - 12
            lead = e >= max(eqs.values()) - 1e-9
            self.rrect(bx - 26, by - 10, bx + 26, by + 10, 5,
                       fill="#16c1f3" if lead else "#0e2d4d",
                       outline="#7fe0ff" if lead else "#1f3b5f", width=1)
            cv.create_text(bx, by, text=f"{100 * e:.0f}%",
                           fill="#04111d" if lead else "white", font=FH(11))
        ring = C["act"] if turn else (C["accent"] if me else shade(colour, -0.15))
        if folded:
            ring = "#262b33"
        if turn:   # the player to act glows
            cv.create_oval(x - r - 8, y - r - 8, x + r + 8, y + r + 8, fill="",
                           outline=shade(C["act"], -0.55), width=4)
            cv.create_oval(x - r - 4, y - r - 4, x + r + 4, y + r + 4, fill="",
                           outline=shade(C["act"], -0.25), width=2)
        cv.create_oval(x - r - 2, y - r + 3, x + r + 2, y + r + 6, fill=C["shadow"], outline="")
        cv.create_oval(x - r, y - r, x + r, y + r,
                       fill=shade(colour, -0.62) if not folded else "#121418",
                       outline=ring, width=3 if (turn or me) else 2)
        cv.create_oval(x - r + 5, y - r + 5, x + r - 5, y + r - 5, fill="",
                       outline=shade(colour, -0.45) if not folded else "#181b20", width=1)
        col = C["faint"] if folded else "white"
        pic = self.avatar(name, 60 if me else 52, bool(folded)) if name else None
        if pic is not None:      # the player's avatar fills the seat (position: name plate)
            cv.create_image(x, y, image=pic)
        else:
            cv.create_text(x, y - 6, text=initials(name) if name else d.get("position", ""),
                           fill=col, font=FH(13 if me else 12))
            cv.create_text(x, y + 13, text=d.get("position", "") if name else "",
                           fill=C["faint"] if folded else shade(colour, 0.35), font=F(8))
        # The name plate: who, and their stack.
        pw, py = 116, y + r - 4
        self.rrect(x - pw / 2, py, x + pw / 2, py + 34, 9, fill="#0d1117",
                   outline=C["act"] if turn else (C["accent"] if me else "#2a303b"), width=1)
        label = name or ("You" if me else f"Bot {seat}")
        if pic is not None and d.get("position"):
            label = f"{label}  \u00b7  {d['position']}"
        cv.create_text(x, py + 10, text=label, fill=C["dim"] if not folded else C["faint"],
                       font=F(8, bold=me))
        stack = d.get("stack_pre", d.get("stack", 0)) if self.anim_on() else d.get("stack", 0)
        cv.create_text(x, py + 24, text=f"{bb(stack)} bb",
                       fill=C["faint"] if folded else (C["gold"] if me else C["text"]),
                       font=FH(11 if me else 10))
        eq = self.ft_equity(seat)
        if eq is not None:
            cv.create_text(x, py + 44, text=money_k(eq), fill=C["gold"] if me else C["dim"],
                           font=FH(10) if me else F(9))
        if d.get("action") and not folded and not (self.anim_on() and
                                                   d["action"].startswith("Out")):
            word = d["action"].split()[0]
            col = {"Check": C["call"], "Call": C["call"]}.get(word, C["r2"])
            if word == "All-in":
                col = C["allin"]
            ty = y - r - 14
            w = 12 + 7 * len(d["action"])
            self.rrect(x - w / 2, ty - 10, x + w / 2, ty + 10, 6, fill=col, outline="")
            cv.create_text(x, ty, text=d["action"], fill="white", font=FH(9))
        if d.get("bet"):
            bx, by = self.seat_xy(seat, 0.36)
            amt = d["bet"] / BB
            chip_col = ("#4aa3ff" if amt <= 1 else "#e04b4b" if amt <= 5 else
                        "#2fb57b" if amt <= 20 else "#24262b")
            self.chip_stack(bx, by + 4, min(5, 1 + int(math.log2(amt + 1))), chip_col)
            text = f"{bb(d['bet'])} bb"
            tw = text_w(text, FH(10)) + 14
            self.rrect(bx + 14, by - 9, bx + 14 + tw, by + 9, 9, fill="#082019",
                       outline="#2c6b55", width=1)
            cv.create_text(bx + 21, by, text=text, fill="white", anchor="w", font=FH(10))
        if seat == self.dealer:
            dx, dy = self.seat_xy(seat, 0.2)
            dx += -r - 4 if x >= TABLE_W / 2 else r + 4
            cv.create_oval(dx - 10, dy - 8, dx + 12, dy + 13, fill="#05070a", outline="")
            cv.create_oval(dx - 11, dy - 11, dx + 11, dy + 11, fill="#f4f1e8", outline="#b9b2a0",
                           width=2)
            cv.create_text(dx, dy, text="D", fill="#111111", font=FH(9))
        if d.get("result") and not self.anim_on():
            good = d["result"].startswith("+")
            ty = y - r - 36 if not me else y - r - 36
            self.rrect(x - 46, ty - 11, x + 46, ty + 11, 8, fill=C["good"] if good else C["bad"],
                       outline="")
            cv.create_text(x, ty, text=d["result"], fill="white", font=FH(10))

    def sim_sizes(self, s):
        """The raise sizes your sim uses in this spot at this depth (raise-to, in chips,
        smallest first; the bots raise to these), or [] (then the usual sizes)."""
        if not STUDY_TABLE:
            return []
        from bots.lines import raise_amount
        from bots.lines8 import current_spot
        from bots.ranges import lookup
        eff = min(s.my_stack + s.my_bet,
                  max((p.stack + p.bet for p in s.players if p.seat != s.my_seat
                       and not p.folded), default=0)) / BB
        blend = lookup(current_spot(s), eff, STUDY_TABLE)
        if not blend:
            return []
        _, acts = max(blend, key=lambda b: b[0])   # (the nearest sim's sizes only)
        names = {a for a, rng in acts.items() if a.startswith("raise") and sum(rng.values()) > 0}
        out = sorted({raise_amount(s, a, (2.0, "x")) for a in names})
        out = [x for x in out if s.min_raise_to <= x < s.max_raise_to]
        if raises_before(s) == 0:   # (the old trees' 29% raise: a 2 bb min-raise, not a size)
            out = [x for x in out if x > 2.05 * BB]
        return out[:3]

    def sizes(self, s):
        """Up to three bet / raise sizes (raise-to, in chips) for the buttons."""
        if not s or not s.min_raise_to:
            return []
        if (self.mode == "Spots" and s.street == "flop" and self.spot["scen"] == "Flop sim"
                and self.current_sim() is not None):
            # The solve's own sizes at this decision (all-in has its own button).
            sim = self.current_sim()
            node = sim.node_for(s, BB)
            if node is not None:
                out = []
                for code in sim.nodes[node]["actions"]:
                    if code.startswith("b"):
                        mv = sim.move(s, code, BB)
                        if mv[0] == "raise" and mv[1] < s.max_raise_to and mv[1] not in out:
                            out.append(mv[1])
                if out:
                    return sorted(out)[:3]
        if s.street == "preflop" and self.mode == "Final table":
            game = FTL.game_sizes(s)       # a game with traps: its own raise size(s)
            if game is not None:
                return game
        if s.street == "preflop":
            sim = self.sim_sizes(s)
            if sim:      # your sim's own sizes here (a two-size tree: both)
                return sim
            # One size per spot, the bot's own (anything else: the custom slider).
            n = raises_before(s)
            eff = (s.my_stack + s.my_bet) / BB
            sb = s.position == "SB"
            limpers = sum(1 for st_, _, a, _ in s.history if st_ == "preflop" and a == "call")
            if n == 0 and limpers:   # raising over a limp: a real iso size
                cands = [FTL.iso_to_bb(eff, limpers) * BB]
            elif n == 0:
                size = (3.5 if sb else 2.3) if eff >= 40 else (3.0 if sb else 2.1) if eff > 25 \
                    else (2.2 if sb else 2.0)
                cands = [size * BB]
            elif n == 1:
                raiser = next(seat for st_, seat, a, _ in s.history if a == "raise")
                rpos = next(p.position for p in s.players if p.seat == raiser)
                blind = s.position in ("SB", "BB")
                in_pos = not blind or (s.position == "BB" and rpos == "SB")
                callers = sum(1 for st_, seat, a, _ in s.history if a == "call")
                if eff <= 25:
                    mult = 2.65 if in_pos else 3.2
                else:
                    mult = 3.0 if in_pos else 3.8
                cands = [(mult + callers) * s.opp_bet]
            elif n == 2 and eff > 50:
                cands = [2.3 * s.opp_bet]
            else:
                cands = []
        elif s.opp_bet == 0:
            cands = [f * s.pot for f in (0.33, 0.66, 1.0)]
        else:
            cands = [2.5 * s.opp_bet, 3.5 * s.opp_bet]
        out = []
        for c in cands:
            c = int(max(s.min_raise_to, min(s.max_raise_to, c)))
            if c < s.max_raise_to and c not in out:
                out.append(c)
        return out[:3]

    def draw_actions(self):
        cv = self.cv
        y0 = H - BOTTOM
        cv.create_rectangle(0, y0, TABLE_W, H, fill=C["bg"], outline="")
        s = self.state
        self._sizes = []
        x1, x2, y, h = 24, TABLE_W - 24, y0 + 10, 60
        if self.view is not None:
            back = len(self.past) - (1 if self._live_finished() else 0) - self.view
            cv.create_text(TABLE_W / 2, y - 2, anchor="s", fill=C["dim"], font=F(10),
                           text=f"{back} hand{'s' if back > 1 else ''} ago  \u00b7  "
                                f"{self.past[self.view]['label']}")
            self.button(TABLE_W / 2 - 330, y, 200, h, "\u25c0  PREVIOUS", C["card2"],
                        self.go_back, sub="B / \u2190", size=12, enabled=self.view > 0)
            self.button(TABLE_W / 2 - 110, y, 200, h, "NEWER  \u25b6", C["card2"],
                        self.go_forward, sub="\u2192", size=12)
            self.button(TABLE_W / 2 + 110, y, 240, h, "BACK TO TABLE", C["accent"],
                        self.back_to_table, sub="Esc", size=13)
            return
        if self.fold_choice:
            msg = "You folded"
            if self.fold_deadline is not None:
                msg += f"  \u00b7  next hand in {max(0, self.fold_deadline - time.time()):.0f}s"
            cv.create_text(TABLE_W / 2 + 15, y - 2, text=msg, fill=C["dim"], anchor="s",
                           font=F(10))
            self.button(TABLE_W / 2 - 230, y, 220, h, "WATCH HAND", C["card2"], self.watch_rest,
                        sub="W  \u00b7  play it out", size=12)
            self.button(TABLE_W / 2, y, 260, h, "NEXT HAND", C["accent"], self.skip_rest,
                        sub="Space", size=13)
            return
        if self.mode == "Final table" and (self.ft is None or self.ft.over):
            self.draw_ft_over()   # the session is over: a banner here, the last hand stays up
            return
        ft_mode = False
        if self.autonext:
            cv.create_text(TABLE_W / 2 - 170, y + h / 2,
                           text="Next hand coming\u2026", fill=C["dim"], anchor="e", font=F(12))
            self.button(TABLE_W / 2 - 150, y, 220, h, "REVIEW", C["card2"], self.review,
                        sub="R  \u00b7  keep this hand up", size=12)
            if not ft_mode:
                self.button(TABLE_W / 2 + 80, y, 200, h, "REPLAY HAND", C["card2"], self.replay,
                            sub="Z  \u00b7  same cards again", size=12)
            return
        if self.waiting_next:
            if not ft_mode:
                self.button(TABLE_W / 2 - 230, y, 220, h, "REPLAY HAND", C["card2"],
                            self.replay, sub="Z  \u00b7  same cards again", size=12)
            self.button(TABLE_W / 2, y, 260, h, "NEXT HAND", C["accent"], self.next_hand,
                        sub="Space", size=13)
            return
        if not s:
            msg = ("Dealing your spot\u2026" if self.mode not in ("Play", "Final table")
                   else "Waiting for the other players\u2026")
            cv.create_text(TABLE_W / 2, y + h / 2, text=msg, fill=C["dim"], font=F(12))
            return
        self._sizes = self.sizes(s)
        buttons = []
        if s.to_call:
            buttons.append(("FOLD", C["fold"], self.do_fold, "F"))
        buttons.append((f"CALL {bb(min(s.to_call, s.my_stack))}" if s.to_call else "CHECK",
                        C["call"], self.do_call, "C"))
        for i, amt in enumerate(self._sizes):
            word = "RAISE" if s.opp_bet else "BET"
            note = ""
            if s.street == "preflop" and raises_before(s):
                note = f"{amt / s.opp_bet:.2f}".rstrip("0").rstrip(".") + "x"
            elif s.street != "preflop" and not s.opp_bet and s.pot:
                note = f"{round(amt / s.pot * 100)}% pot"
            buttons.append((f"{word} {bb(amt)}", (C["r1"], C["r2"], C["r3"])[i],
                            lambda a=amt: self.do_raise(a), f"{i + 1}   {note}".strip()))
        if s.min_raise_to:
            buttons.append((f"ALL-IN {bb(s.max_raise_to)}", C["allin"], self.do_allin, "A"))
        gap = 8
        w = (x2 - x1 - gap * (len(buttons) - 1)) / len(buttons)
        for i, (text, col, cmd, sub) in enumerate(buttons):
            self.button(x1 + i * (w + gap), y, w, h, text, col, cmd, sub=sub, size=13, radius=6)
        y2 = y0 + h + 22
        if s.min_raise_to:
            self.draw_slider(s, x1, y2 + 12, 270)
            word = "Raise to" if s.opp_bet else "Bet"
            self.button(x1 + 290, y2 - 2, 118, 28, f"{word} {self.custom:g}", C["card2"],
                        self.do_custom, size=10, radius=6)
        if self.show_hint and self.advice:
            hx = x1 + 420
            self.rrect(hx, y2 - 2, x2, y2 + 26, 6, fill=C["card"], outline="")
            cv.create_text(hx + 12, y2 + 12, text="Bot's play", fill=C["dim"], anchor="w",
                           font=F(9))
            cv.create_text(hx + 80, y2 + 12, text=describe(self.advice, s), fill=C["gold"],
                           anchor="w", font=FH(12))

    def draw_slider(self, s, x, y, length):
        lo, hi = s.min_raise_to / BB, s.max_raise_to / BB
        if self.custom is None or not lo <= self.custom <= hi:
            self.custom = round(lo, 2)
        frac = (self.custom - lo) / (hi - lo) if hi > lo else 0
        cv = self.cv
        self.rrect(x, y - 3, x + length, y + 3, 3, fill=C["card2"], outline="", tags=("slider",))
        self.rrect(x, y - 3, x + max(6, length * frac), y + 3, 3, fill=C["accent"], outline="",
                   tags=("slider",))
        kx = x + length * frac
        cv.create_oval(kx - 9, y - 9, kx + 9, y + 9, fill="white", outline=C["accent"], width=3,
                       tags=("slider",))
        cv.create_rectangle(x - 10, y - 14, x + length + 10, y + 14, fill="", outline="",
                            tags=("slider",))

        def start(e):
            self._drag = (x, length, lo, hi)
            self.on_drag(e)

        cv.tag_bind("slider", "<Button-1>", start)

    def on_drag(self, e):
        if not self._drag:
            return
        x, length, lo, hi = self._drag
        f = min(1.0, max(0.0, (e.x - x) / length))
        v = lo + f * (hi - lo)
        v = round(v * 4) / 4 if hi - lo > 2 else round(v, 2)  # quarter big blinds
        self.custom = min(hi, max(lo, v))
        self.draw()

    def draw_panel(self):
        cv = self.cv
        x0 = TABLE_W
        cv.create_rectangle(x0, TOP + 1, W, H, fill=C["panel"], outline="")
        cv.create_line(x0, TOP, x0, H, fill=C["line"])
        x, y, x2 = x0 + 18, TOP + 14, W - 18
        st = self.stats[self.mode]
        tabs = ["Tree", {"Play": "Coach", "Final table": "Final table"}.get(self.mode, "Setup")]
        tab = self.panel_tab if self.panel_tab == "Tree" else tabs[1]
        if tab == "Tree":
            self.draw_tree(x, y + 68, x2, H - 14)
        self.draw_tabs(x, y, x2, tabs, tab)
        y += 44
        if tab == "Tree":
            a, n = st["agree"]
            pct = f"{100 * a / n:.0f}%" if n else "\u2013"
            cv.create_text(x, y + 6, text=f"Same play as the bot  {pct}", fill=C["dim"],
                           anchor="w", font=F(9))
            if self.mode == "Play" and self.skip_trash and self.skipped_hands:
                cv.create_text(x + 168, y + 6, text=f"{self.skipped_hands} skipped",
                               fill=C["faint"], anchor="w", font=F(8))
            if self.mode == "Final table" and self.ft is not None:
                eq = self.ft.equities().get(0, 0.0)
                cv.create_text(x2, y + 6, text=f"Your seat {money_k(eq)}", fill=C["gold"],
                               anchor="e", font=FH(9))
            elif self.mode == "Play":
                cv.create_text(x2, y + 6, text=f"Result {st['total'] / BB:+.1f} bb",
                               fill=C["good"] if st["total"] >= 0 else C["bad"], anchor="e",
                               font=FH(9))
            else:
                cv.create_text(x2, y + 6, text=f"{n} decisions", fill=C["faint"], anchor="e",
                               font=F(9))
            return
        if self.mode == "Final table":
            return self.draw_ft_setup(x, y, x2)
        if self.mode == "Play":
            y = self.chip_grid(x, y, x2, "Starting stack (bb), every hand",
                               ["15", "20", "25", "30", "40", "60", "100"], f"{self.play_depth:g}",
                               lambda v: self.set_attr("play_depth", float(v)), 7)
            y += 4
            per100 = st["total"] / BB / st["hands"] * 100 if st["hands"] else 0.0
            tiles = [("Hands", f"{st['hands']}", C["text"]),
                     ("Result", f"{st['total'] / BB:+.1f} bb",
                      C["good"] if st["total"] >= 0 else C["bad"]),
                     ("bb / 100", f"{per100:+.1f}", C["good"] if st["total"] >= 0 else C["bad"])]
            y = self.tiles(x, y, x2, tiles)
        elif self.mode == "Drill":
            y = self.section(x, y, "DRILL SETUP")
            y = self.chip_grid(x, y, x2, "Spot", ["Any", "Vs open", "Vs 3-bet", "Vs 4-bet"],
                               self.drill_spot, lambda v: self.set_attr("drill_spot", v), 4)
            y = self.chip_grid(x, y, x2, "Stack depth (bb)", list(DRILL_DEPTHS), self.drill_depths,
                               lambda v: self.set_attr("drill_depths", v), 4)
        else:
            y = self.section(x, y, "SPOT SETUP")
            sp = self.spot
            y = self.chip_grid(x, y, x2, "Situation", SCENARIOS, sp["scen"],
                               lambda v: self.set_spot("scen", v), 2)
            if sp["scen"] == "Flop sim":
                y = self.draw_sim_setup(x, y, x2)
                return self.draw_agreement(x, y, x2, st)
            order = position_names(self.n)
            y = self.chip_grid(x, y, x2, "Your position", order, sp["hero"],
                               lambda v: self.set_spot("hero", v), 4)
            if sp["scen"] != "First in":
                label = ("Opener (villain)" if sp["scen"] in ("Facing open", "Defend → flop")
                         else "Villain (acts after you)")
                y = self.chip_grid(x, y, x2, label, order, sp["villain"],
                                   lambda v: self.set_spot("villain", v), 4)
            y = self.chip_grid(x, y, x2, "Stack depth (bb)", [str(d) for d in SPOT_DEPTHS],
                               str(sp["depth"]), lambda v: self.set_spot("depth", int(v)), 5)
            problem = spot_problem(self.n, sp["scen"], sp["hero"], sp["villain"])
            if problem:
                cv.create_text(x, y + 2, text=problem, fill=C["bad"], anchor="nw",
                               font=FH(9), width=x2 - x)
                y += 20
        self.draw_agreement(x, y, x2, st)

    def draw_ft_setup(self, x, y, x2):
        cv = self.cv
        y = self.section(x, y, "FINAL TABLE  \u00b7  8 left, 9th already paid")
        cv.create_text(x, y + 14, text="Table", fill=C["dim"], anchor="w", font=F(9))
        self.button(x + 46, y, 30, 28, "\u25c0", C["card2"], lambda: self.ft_pick(self.ft_table - 1),
                    size=10)
        cv.create_text(x + 122, y + 14, text=f"#{self.ft_table} of {FTL.I.N_TABLES}", fill=C["text"],
                       font=FH(11))
        self.button(x + 168, y, 30, 28, "\u25b6", C["card2"], lambda: self.ft_pick(self.ft_table + 1),
                    size=10)
        self.button(x + 208, y, x2 - x - 208, 28, "Random table", C["card2"], self.ft_random,
                    size=10)
        y += 40
        y = self.chip_grid(x, y, x2, "Prize pool (WSOP Circuit payouts)",
                           ["$500K", "$1M", "$1.5M", "Custom $"], self.ft_pool, self.set_ft_pool, 4)
        if self.ft_pool == "Custom $":
            cv.create_text(x, y + 2, text="Custom payouts: type them in the ICM tab (Custom $)",
                           fill=C["faint"], anchor="nw", font=F(8), width=x2 - x)
            y += 18
        y = self.chip_grid(x, y, x2, "Blinds up every (WSOP Circuit Main Event levels)",
                           ["15 hands", "20 hands", "30 hands", "Never"],
                           f"{self.ft_level_hands} hands" if self.ft_level_hands else "Never",
                           self.set_ft_level_hands, 4)
        y = self.chip_grid(x, y, x2, "All-in decisions (jam, re-jam, call an all-in)",
                           ["ICM", "Chip EV (your sims)"],
                           "ICM" if self.ft_icm else "Chip EV (your sims)", self.set_ft_icm, 2)
        third = (x2 - x - 16) / 3
        self.button(x, y, third, 34, "New session", C["accent"], self.ft_new, size=10)
        self.button(x + third + 8, y, third, 34, "End session", C["card2"], self.ft_end,
                    size=10, enabled=self.ft is not None and not self.ft.over, fg=C["text"])
        self.button(x + 2 * (third + 8), y, third, 34, "Leaks", C["gold"], self.open_leaks,
                    size=10, fg="#14171c")
        y += 46
        ft = self.ft
        if ft is None:
            return
        eq = ft.equities()
        r_ = ft.icm
        y = self.tiles(x, y, x2, [("Hand", f"{ft.hands}", C["text"]),
                                  ("Blinds", ft.blinds().split(" ")[0], C["text"]),
                                  ("Left", f"{len(ft.alive())}", C["text"])])
        cv.create_text(x, y - 2, text=f"Your seat {money_k(eq.get(0, 0.0))}", fill=C["gold"],
                       anchor="w", font=FH(11))
        cv.create_text(x2, y + 14, text=f"ICM {r_['right']} / {r_['spots']} right  \u00b7  "
                       f"mistakes {money_k(r_['lost'])}",   # (own line: big seats overlap)
                       fill=C["bad"] if r_["lost"] else C["dim"], anchor="e", font=F(9))
        y += 26
        # The payouts, in plain sight: who has been paid, and what the next one out gets.
        cv.create_text(x, y + 4, text="Payouts", fill=C["dim"], anchor="w", font=F(9))
        y += 16
        left = len(ft.alive())
        who_at = {pl: k for k, (pl, _) in ft.out.items()}
        half = (x2 - x) / 2
        for p in range(1, FTL.SEATS + 1):
            col_x = x + 4 + (half if p > 4 else 0)
            row_y = y + 8 + ((p - 1) % 4) * 17
            prize = ft.pays9[p - 1]
            if p in who_at and p != 1:
                k = who_at[p]
                text, col = f"{ordinal(p)}  {money_k(prize)}  {ft.name(k)}", C["faint"]
            elif p == left and not ft.over:
                text, col = f"{ordinal(p)}  {money_k(prize)}  \u2190 next out", C["gold"]
            else:
                text, col = f"{ordinal(p)}  {money_k(prize)}", C["text"]
            cv.create_text(col_x, row_y, text=text, fill=col, anchor="w", font=F(9))
        y += 4 * 17 + 12
        cv.create_text(x, y + 4, text="Standings", fill=C["dim"], anchor="w", font=F(9))
        y += 18
        rows = sorted(range(FTL.SEATS), key=lambda k: (-(ft.stacks[k]),
                                                      ft.out.get(k, (0, 0))[0]))
        for k in rows:
            who = ft.name(k)
            if k in ft.out:
                pl, prize = ft.out[k]
                text, right_ = f"{ordinal(pl)}  \u00b7  {who}", money_k(prize)
                col = C["faint"]
            else:
                text = f"{who}  \u00b7  {ft.stacks[k] / BB:.1f} bb"
                right_, col = money_k(eq.get(k, 0.0)), C["gold"] if k == 0 else C["text"]
            cv.create_text(x + 4, y + 8, text=text, fill=col, anchor="w", font=F(9))
            cv.create_text(x2 - 4, y + 8, text=right_, fill=col, anchor="e", font=F(9))
            y += 19
        if self.icm_spot is not None and self.icm_last is not None:
            right, g = self.icm_last
            go = "Jam" if self.icm_spot["kind"] == "jam" else "Call"
            cv.create_text(x, y + 10, text=f"Last ICM spot: {self.icm_spot['hand']}  \u00b7  "
                           f"{(go if g > 0 else 'Fold')} was best ({money_k(abs(g))})  \u00b7  "
                           f"{'right' if right else 'wrong'}",
                           fill=C["good"] if right else C["bad"], anchor="w", font=F(9))

    def draw_agreement(self, x, y, x2, st):
        cv = self.cv
        # Agreement with the bot.
        a, n = st["agree"]
        pct = f"{100 * a / n:.0f}%" if n else "–"
        y += 6
        self.rrect(x, y, x2, y + 50, 12, fill=C["card"], outline="")
        cv.create_text(x + 14, y + 16, text="Same play as the bot", fill=C["dim"], anchor="w",
                       font=F(9))
        cv.create_text(x + 14, y + 35, text=f"{n} decisions", fill=C["faint"], anchor="w",
                       font=F(8))
        cv.create_text(x2 - 14, y + 25, text=pct, fill=C["text"], anchor="e",
                       font=FH(18))
        if n:
            bar_x = x + 28 + text_w("Same play as the bot", F(9))
            bar_w = x2 - 80 - bar_x
            self.rrect(bar_x, y + 21, bar_x + bar_w, y + 29, 4, fill=C["line"], outline="")
            if a:
                self.rrect(bar_x, y + 21, bar_x + max(8, bar_w * a / n), y + 29, 4,
                           fill=C["good"], outline="")
        y += 62
        # Last decision.
        f = self.feedback
        self.rrect(x, y, x2, y + 92, 12, fill=C["card"], outline="")
        if f:
            text, col = self.caption or ((("Same as the bot", C["good"]) if f["same"] else
                                          ("The bot plays it differently", C["bad"])))
            self.rrect(x + 12, y + 12, x + 24 + text_w(text, FH(8)), y + 32, 10, fill=col,
                       outline="")
            cv.create_text(x + 18, y + 22, anchor="w", fill="white", font=FH(8), text=text)
            cv.create_text(x2 - 12, y + 22, text=f["spot"], fill=C["dim"], anchor="e",
                           font=F(9))
            cv.create_text(x + 14, y + 52, text="You", fill=C["dim"], anchor="w", font=F(9))
            cv.create_text(x + 54, y + 52, text=f["you"], fill=C["text"], anchor="w",
                           font=FH(12))
            cv.create_text(x + 14, y + 75, text="Bot", fill=C["dim"], anchor="w", font=F(9))
            cv.create_text(x + 54, y + 75, text=f["bot"], fill=C["gold"], anchor="w",
                           font=FH(12))
        else:
            cv.create_text(x + 14, y + 46, anchor="w", fill=C["dim"], font=F(10),
                           text="Make a decision to see how the bot\nplays the same spot.")

    def draw_tabs(self, x, y, x2, tabs, tab):
        cv = self.cv
        cv.create_rectangle(x - 18, TOP + 1, x2 + 18, y + 64, fill=C["panel"], outline="")
        cw = 74  # the chart button
        self.rrect(x, y, x2 - cw - 8, y + 32, 10, fill=C["card"], outline="")
        tw = (x2 - cw - 8 - x - 8) / 2
        for i, t in enumerate(tabs):
            on = t == tab
            self.button(x + 4 + i * tw, y + 4, tw, 24, "Hand tree" if t == "Tree" else t,
                        C["accent"] if on else C["card"], lambda v=t: self.set_tab(v),
                        size=10, radius=7, fg="white" if on else C["dim"])
        self.button(x2 - cw, y, cw, 32, "\u25a6 Chart", C["card2"], lambda: self.open_chart(),
                    size=10, radius=10)

    # ----- the decision tree (right-hand panel) -----
    def draw_tree(self, x, y, x2, y_end):
        cv = self.cv
        items = []  # (kind, data, height)
        depth = f"{self.hand_depth:g} bb" if self.hand_depth else "blinds 0.5 / 1"
        items.append(("root", f"Start  ·  {depth}  ·  {self.n}-handed", 30))
        street = "preflop"
        for node in self.tree:
            if node["street"] != street:
                street = node["street"]
                items.append(("street", node, 34))
            items.append(("node", node, 124 if node["hero"] else 104))
        gap = 22
        total = sum(h for _, _, h in items) + gap * (len(items) - 1)
        avail = y_end - y
        top = y - max(0, total - avail) + self.tree_scroll
        top = min(y, max(y - max(0, total - avail), top))
        self.tree_scroll = top - (y - max(0, total - avail))
        cv.create_rectangle(x - 4, y - 2, x2 + 4, y_end + 2, fill=C["panel"], outline="")
        cx = (x + x2) / 2
        anchor = None  # where the connector to the next item starts
        anchor_col = C["faint"]
        cy = top
        for kind_, data, h in items:
            visible = cy + h >= y - 40 and cy <= y_end + 40
            if anchor is not None and visible:
                ax, ay = anchor
                cv.create_line(ax, ay, ax, ay + gap * 0.45, cx, cy - gap * 0.45, cx, cy,
                               smooth=True, fill=anchor_col, width=2)
            if kind_ == "root":
                w = text_w(data, F(9)) + 28
                if visible:
                    self.rrect(cx - w / 2, cy, cx + w / 2, cy + h, 15, fill=C["card2"], outline="")
                    cv.create_text(cx, cy + h / 2, text=data, fill=C["dim"], font=F(9))
                anchor, anchor_col = (cx, cy + h), C["faint"]
            elif kind_ == "street":
                if visible:
                    self.draw_street_pill(cx, cy, h, data)
                anchor = (cx, cy + h)
            else:
                end = self.draw_node(x, cy, x2, h, data) if visible else None
                if visible:
                    self._hits.append((x - 2, cy - 2, x2 + 2, cy + h + 5,
                                       lambda d=data: self.open_details(d), None, None))
                    if not data["hero"]:
                        cv.create_text(x2 - 12, cy + 33, text="details \u2197", fill=C["faint"],
                                       anchor="e", font=F(7))
                    else:
                        pot = f"Pot {bb(data['pot'])} bb"
                        cv.create_text(x2 - 22 - text_w(pot, F(9)), cy + 19, text="details \u2197",
                                       fill=C["faint"], anchor="e", font=F(7))
                if end:
                    anchor, anchor_col = end
                else:
                    anchor, anchor_col = (cx, cy + h), C["faint"]
            cy += h + gap
        # Cover what scrolled past the edges.
        cv.create_rectangle(x - 4, y_end + 2, x2 + 4, H, fill=C["panel"], outline="")
        if not self.tree:
            cv.create_text(cx, y + 90, text="Decisions show up here as the hand is played.",
                           fill=C["faint"], font=F(9))

    def draw_street_pill(self, cx, cy, h, node):
        cards = node["board"][{"flop": 0, "turn": 3, "river": 4}[node["street"]]:]
        label = node["street"].upper()
        w = text_w(label, FH(9)) + 30 + 23 * len(cards)
        self.rrect(cx - w / 2, cy, cx + w / 2, cy + h, 10, fill=C["card"], outline=C["line"])
        self.cv.create_text(cx - w / 2 + 12, cy + h / 2, text=label, fill=C["dim"], anchor="w",
                            font=FH(9))
        x = cx - w / 2 + 20 + text_w(label, FH(9))
        for c in cards:
            self.card(x, cy + 5, c, 20, h - 10)
            x += 23

    def draw_node(self, x, y, x2, h, node):
        """One decision card. Returns (x, y) where the line to the next card starts and
        its colour."""
        cv = self.cv
        hero = node["hero"]
        self.rrect(x, y + 3, x2, y + h + 3, 12, fill=C["shadow"], outline="")
        self.rrect(x, y, x2, y + h, 12, fill=C["card"],
                   outline=C["accent"] if hero else C["line"], width=2 if hero else 1)
        # Header: position badge, name, verdict, pot.
        badge = node["pos"]
        bw = text_w(badge, FH(9)) + 14
        self.rrect(x + 10, y + 9, x + 10 + bw, y + 29, 7,
                   fill=C["accent"] if hero else C["card2"], outline="")
        cv.create_text(x + 10 + bw / 2, y + 19, text=badge, fill="white", font=FH(9))
        name = "You" if hero else (self.seat_name(node["seat"]) or f"Bot {node['seat']}")
        if hero and node["auto"]:
            name += "  (dealt)"
        cv.create_text(x + 18 + bw, y + 19, text=name, fill=C["text"] if hero else C["dim"],
                       anchor="w", font=FH(10))
        if not hero and self.final_holes and node["seat"] < len(self.final_holes):
            hx = x + 26 + bw + text_w(name, FH(10))
            for k, c in enumerate(self.final_holes[node["seat"]]):
                self.card(hx + k * 19, y + 8, c, 17, 23)
        cv.create_text(x2 - 12, y + 19, text=f"Pot {bb(node['pot'])} bb", fill=C["faint"],
                       anchor="e", font=F(9))
        # Range bar.
        bx1, bx2, by = x + 12, x2 - 12, y + 38
        cv.create_text(bx1, by - 1, text=f"Range  {node['range']:.0f}% of hands", anchor="w",
                       fill=C["faint"], font=F(8))
        by += 9
        opts = node["options"]
        self.rrect(bx1, by, bx2, by + 12, 6, fill=C["card2"], outline="")
        px = bx1
        for i, (k, label, pct, col) in enumerate(opts):
            w = (bx2 - bx1) * pct / 100
            if w < 1:
                continue
            if i == 0 or px == bx1:
                self.rrect(px, by, px + w, by + 12, 6, fill=col, outline="")
                if w > 12:
                    cv.create_rectangle(px + 6, by, px + w, by + 12, fill=col, outline="")
            elif px + w >= bx2 - 1:
                self.rrect(px, by, px + w, by + 12, 6, fill=col, outline="")
                cv.create_rectangle(px, by, px + min(6, w), by + 12, fill=col, outline="")
            else:
                cv.create_rectangle(px, by, px + w, by + 12, fill=col, outline="")
            if w > 30:
                cv.create_text(px + w / 2, by + 6, text=f"{pct:.0f}%", fill="white",
                               font=FH(7))
            px += w
        # Option tiles.
        ty = by + 20
        n = max(1, len(opts))
        tw = (bx2 - bx1 - 6 * (n - 1)) / n
        end = None
        chosen, botk = node["chosen"], node["bot"]
        for i, (k, label, pct, col) in enumerate(opts):
            tx = bx1 + i * (tw + 6)
            on = k == chosen
            fill = col if on else shade(col, -0.62)
            outline = "white" if on and hero else ""
            self.rrect(tx, ty, tx + tw, ty + 36, 8, fill=fill, outline=outline,
                       width=2 if outline else 1)
            lab = label if text_w(label, FH(9)) <= tw - 8 else label.split()[-1]
            cv.create_text(tx + tw / 2, ty + 12, text=lab, font=FH(9),
                           fill="white" if on else shade(col, 0.35))
            evs = node.get("ev") or {}
            show_ev = hero and k in evs and (node["verdict"] is not None or self.show_hint)
            sub = f"{pct:.0f}%" + (f"  \u00b7  {evs[k]:+.2f}" if show_ev else "")
            cv.create_text(tx + tw / 2, ty + 26, text=sub, font=F(8),
                           fill="white" if on else C["dim"])
            if hero and botk == k:
                cv.create_oval(tx + tw - 10, ty - 4, tx + tw + 2, ty + 8, fill=C["gold"],
                               outline=C["card"], width=2)
            if on:
                end = ((tx + tw / 2, ty + 36), col)
        if hero and node.get("ev") and (node["verdict"] is not None or self.show_hint):
            best = max(node["ev"].values())
            state = "" if node.get("ev_done") else "  (simulating\u2026)"
            cv.create_text(x2 - 12, y + 37, anchor="e", fill=C["faint"], font=F(7),
                           text=f"EV (bb vs fold) \u00b7 {node.get('ev_n', 0)} samples{state}")
        if hero:
            vy = y + h - 12
            if node["verdict"] is None and not node["auto"]:
                cv.create_text(x + 12, vy, text="Your move — the gold dot is the bot's play",
                               fill=C["accent"], anchor="w", font=F(8))
            elif node["auto"]:
                cv.create_text(x + 12, vy, text="Played for you to set up the spot",
                               fill=C["faint"], anchor="w", font=F(8))
            else:
                text, col = caption_for(node) or ("", C["dim"])
                if not node["verdict"]:
                    text += f"  \u00b7  bot: {key_label(botk) if botk else '?'}"
                cv.create_text(x + 12, vy, text=text, fill=col, anchor="w", font=FH(9))
        return end

    def on_wheel(self, e):
        if self.panel_tab != "Tree" or e.x < TABLE_W:
            return
        step = 40 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else -40
        self.tree_scroll += step
        self.draw()

    def section(self, x, y, title):
        self.cv.create_text(x, y + 4, text=title, fill=C["faint"], anchor="w",
                            font=FH(8))
        return y + 16

    def tiles(self, x, y, x2, tiles):
        w = (x2 - x - 16) / 3
        for i, (k, v, col) in enumerate(tiles):
            bx = x + i * (w + 8)
            self.rrect(bx, y, bx + w, y + 58, 12, fill=C["card"], outline="")
            self.cv.create_text(bx + w / 2, y + 18, text=k, fill=C["dim"], font=F(9))
            self.cv.create_text(bx + w / 2, y + 39, text=v, fill=col, font=FH(13))
        return y + 70

    def chip_grid(self, x, y, x2, label, options, current, on_pick, cols):
        self.cv.create_text(x, y + 6, text=label, fill=C["dim"], anchor="w", font=F(9))
        y += 16
        w = (x2 - x - (cols - 1) * 6) / cols
        for i, o in enumerate(options):
            r, c = divmod(i, cols)
            self.chip(x + c * (w + 6), y + r * 32, w, o, o == current, lambda v=o: on_pick(v))
        rows = (len(options) + cols - 1) // cols
        return y + rows * 32 + 4

    # ----- events from the game thread -----
    def poll(self):
        changed = False
        try:
            while self.view is None:  # looking back at an old hand: the table waits
                self.handle(self.events.get_nowait())
                changed = True
        except queue.Empty:
            pass
        tick = self.fold_choice and self.fold_deadline is not None and \
            int(time.time() * 4) != getattr(self, "_tick", None)
        if tick:
            self._tick = int(time.time() * 4)
        animating = time.time() < self._anim_until + 0.05
        if animating:
            self._anim_dirty = True
        elif getattr(self, "_anim_dirty", False):   # one last frame: the results appear
            self._anim_dirty = False
            changed = True
        if changed or tick or animating or \
                (self.caption and 0 < self.caption_until - time.time() < 0.06):
            self.draw()
        self.root.after(30, self.poll)

    def write(self, text):
        self.history.append(text)
        del self.history[:-400]

    def from_state(self, s):
        if s.street != self.street:
            self.street = s.street
            self.write(f"  {s.street.upper()}  {' '.join(s.board)}")
            for d in self.seats:
                d.pop("action", None)
        for p in s.players:
            self.seats[p.seat].update(stack=p.stack, bet=p.bet, folded=p.folded,
                                      position=p.position)
        self.board, self.pot = list(s.board), s.pot
        self.last_state = s
        for d in self.seats:
            d.pop("turn", None)

    def apply_move(self, s, move):
        d = self.seats[s.my_seat]
        text = describe(move, s)
        d["action"] = text
        if text == "Fold":
            d["folded"] = True
            return
        if text == "Check":
            return
        if text.startswith("Call"):
            add = min(s.to_call, s.my_stack)
        else:
            to = s.max_raise_to if text == "All-in" else max(s.min_raise_to,
                                                             min(move[1], s.max_raise_to))
            add = to - s.my_bet
        d["bet"] = d.get("bet", 0) + add
        d["stack"] = d.get("stack", 0) - add
        self.pot += add

    def labels_from_history(self, s):
        """Action labels for the current street, rebuilt from the hand's history."""
        names = {p.seat: p.position for p in s.players}
        for st, seat, a, amt in s.history:
            if st != s.street:
                continue
            word = {"fold": "Fold", "check": "Check", "call": "Call"}.get(a)
            p = next(p for p in s.players if p.seat == seat)
            if a == "raise":
                word = "All-in" if p.all_in else f"Raise to {bb(amt)}"
            self.seats[seat]["action"] = word
        # Preflop line in the history box.
        line = []
        for st, seat, a, amt in s.history:
            if st == "preflop" and a != "fold":
                who = "You" if seat == 0 else names[seat]
                line.append(f"{who} {a}" + (f" {bb(amt)}" if a == "raise" else ""))
        return ", ".join(line)

    def handle(self, ev):
        k = ev[0]
        if k == "new_hand":
            _, hand_no, button, stacks = ev
            self.reset_hand(button, stacks)
            self.write(f"\n— Hand {hand_no + 1} —")
        elif k == "ft_deal":   # a final-table hand, dealt: shown before anyone acts
            _, hand_no, button, stacks, hole, pos, depth = ev
            self.reset_hand(button, stacks)
            self.hand_depth = depth
            self.street = "preflop"
            n = len(stacks)
            sb, bb_ = (button, (button + 1) % 2) if n == 2 else ((button + 1) % n,
                                                                 (button + 2) % n)
            for seat, d in enumerate(self.seats):
                d["position"] = pos.get(seat, "")
                d["cards"] = list(hole) if seat == 0 else None
            for seat, amt in ((sb, BB // 2), (bb_, BB + BB)):   # blinds (+ the big blind ante)
                d = self.seats[seat]
                put = min(amt, d["stack"])
                d["stack"] -= put
                d["bet"] = min(put, BB if seat == bb_ else BB // 2)
                self.pot += put
            self.write(f"\n- {pos.get(0, '')}, {depth:g} bb -  {hole[0]} {hole[1]}")
        elif k == "reveal":  # the first moment you see this hand
            _, s, depth = ev
            self.reset_hand(s.button_seat, [p.stack for p in s.players])
            self.hand_depth = depth
            self.street = s.street
            self.from_state(s)
            line = self.labels_from_history(s)
            where = f"{s.position}, {depth:g} bb" if depth else s.position
            self.write(f"\n- {where} -  {s.hole[0]} {s.hole[1]}")
            if line:
                self.write("   " + line)
            if s.street != "preflop":
                self.write(f"  {s.street.upper()}  {' '.join(s.board)}")
        elif k == "action":
            _, s, move = ev
            self.from_state(s)
            self.apply_move(s, move)
            d = self.seats[s.my_seat]
            self.write(f"{d.get('position', ''):<5} Bot {s.my_seat}  {d['action']}")
        elif k == "your_turn":
            _, s, adv, ranges, start = ev
            if self.settings_version != self.hand_version:
                self.replies.put("redeal")  # a hand dealt before you changed the set-up
                return
            node = next((n for n in reversed(self.tree) if n["hero"] and n["verdict"] is None
                         and not n["auto"]), None)
            if node is not None and self.show_ev:
                node["ev"], node["ev_n"] = {}, 0
                keys = [o[0] for o in node["options"]]
                self.ev_worker.submit({"state": s, "keys": keys, "ranges": ranges,
                                       "start": start, "node": id(node),
                                       "ante": ((BB, "bb") if self.mode == "Final table"
                                                else (match8.ANTE, "each"))})
            self.from_state(s)
            self.seats[0]["cards"] = list(s.hole)
            self.seats[0]["turn"] = True
            self.state, self.advice = s, adv
            self.notice = ""
        elif k == "hand_over":
            _, r, result, was_replay, holes = ev
            self.state = None
            self.fold_choice = False
            self.final = r
            self.final_holes = holes
            for seat in range(1, min(len(holes), len(self.seats))):
                d = self.seats[seat]
                d["cards"], d["reveal"] = holes[seat], True
            if r and r.get("board") and not r["shown"]:
                # You folded and the rest played on: the streets it reached (the result
                # carries all five cards, dealt or not; only the played streets are real).
                last = max((["preflop", "flop", "turn", "river"].index(st_)
                            for st_, *_ in r.get("history", ())), default=0)
                dealt = [0, 3, 4, 5][last]
                if dealt > len(self.board):
                    self.board = list(r["board"][:dealt])
            if r and r["shown"]:
                before = len(self.board)
                self._runout = None
                if len(r["shown"]) >= 2 and len(r["board"]) > before and self.view is None:
                    try:     # the win % beside each hand, street by street, as on TV
                        self._runout = (before, runout_equities(r["shown"], list(r["board"]),
                                                                before))
                    except Exception:
                        self._runout = None
                self.board = list(r["board"])
                self.write("  Board  " + " ".join(r["board"]))
                for seat, cards in r["shown"].items():
                    self.seats[seat]["cards"] = cards
                    who = "You" if seat == 0 else f"Bot {seat}"
                    self.write(f"  {who} shows {cards[0]} {cards[1]}")
            if not was_replay and self.view is None:
                if self.mode == "Final table" and self.ft is not None:
                    try:
                        self.ft_record_leaks()
                    except Exception:
                        pass
            for seat, res in enumerate(result):
                d = self.seats[seat]
                d["stack_pre"] = d.get("stack", 0)   # shown until the run-out has dealt
                d["bet"] = 0
                if res > 0 or (seat == 0 and res < 0):
                    d["result"] = f"{'+' if res > 0 else '-'}{bb(abs(res))} bb"
                if r:
                    d["stack"] = r["stacks"][seat] + res
            self.pot_final, self.pot = self.pot, 0   # (the bar keeps it till the next hand)
            st = self.stats[self.mode]
            if not was_replay:  # replays don't count in your results
                st["total"] += result[0]
                st["hands"] += 1
            word = "won" if result[0] > 0 else "lost"
            self.write(f"  You {word} {bb(abs(result[0]))} bb" if result[0] else "  No chips won or lost")
            self.waiting_next = self.mode != "Drill"
            if (self.fast_forward or self.fold_wait or self.quick_rest) and self.fast_fold and \
                    self.mode != "Drill":
                self.waiting_next, self.autonext = False, True
            if self.mode == "Drill":
                self.autonext = True  # the next drill spot comes by itself; R / Z hold it
            self.past.append(self.snapshot(result[0], was_replay))
            del self.past[:-50]
        elif k == "nodes":
            fresh = not self.tree
            self.tree.extend(ev[1])
            self.tree_scroll = 0
            if any(n.get("grid") for n in ev[1]):
                self.refresh_chart(new_hand=fresh)
        elif k == "autofold":
            self.autofolded = True
            self.skipped += 1
            self.toast = f"Auto-folded {ev[1]}: an obvious fold"
        elif k == "ev":
            _, node_id, evs, rounds, done = ev
            for node in self.tree:
                if id(node) == node_id:
                    node["ev"], node["ev_n"], node["ev_done"] = evs, rounds, done
                    if node is self.caption_node:
                        self.caption = caption_for(node)
        elif k == "notice":
            self.notice = ev[1]
        elif k == "icm_spot":
            self.icm_spot, self.icm_last = ev[1], None
        elif k == "icm_graded":
            _, spot, right, g = ev
            self.icm_last = (right, g)
            if spot["kind"] == "mix":
                self.toast = FTL.mix_verdict(spot, right, g)
            else:
                go = "Jam" if spot["kind"] == "jam" else "Call"
                best = go if g > 0 else "Fold"
                self.toast = (f"ICM: {best} {spot['hand']}  \u00b7  " +
                              (f"right ({money_k(abs(g))} better)" if right else
                               f"wrong, that cost {money_k(abs(g))}"))
            # At a final table the ICM verdict is the grade for this decision: a fold the
            # coach calls best is never a "fish" fold, whatever the chip-EV sims prefer.
            node = self.caption_node or next(
                (n for n in reversed(self.tree) if n["hero"] and n["verdict"] is not None), None)
            if node is not None:
                node["icm"] = (right, g, sum(spot["pays"]))
                node["icm_kind"] = ("call" if spot["kind"] == "call" else
                                    "trap" if spot["kind"] == "mix" else
                                    "rejam" if spot.get("opener") is not None else "jam_first")
                node["icm_act"] = spot.get("answer")
                if node is self.caption_node:
                    self.caption = caption_for(node)
                    self.caption_until = time.time() + 2.5
        elif k in ("ft_hand", "ft_quiet"):
            info, seats = ev[1], ev[2]
            self._eq_before = dict(info["before"])
            busts = []
            if len(seats) == len(self.seats):
                for e, x in enumerate(seats):
                    d = self.seats[e]
                    if k == "ft_quiet":
                        d["stack"] = self.ft.stacks[x] if self.ft else d.get("stack", 0)
                    delta = info["after"].get(x, 0.0) - info["before"].get(x, 0.0)
                    if abs(delta) >= 0.5:
                        d["result"] = f"{'+' if delta > 0 else '-'}{money_k(abs(delta))}"
                    else:
                        d.pop("result", None)
            if self.ft is not None and self.ft.over:
                FTL.save_session(self.ft, self.ft.leaks)
            for x, place, prize in info["busts"]:
                who = self.ft.name(x) if self.ft else f"Seat {x + 1}"
                busts.append(f"{who} out in {ordinal(place)}: {money_k(prize)}")
                if x in seats and len(seats) == len(self.seats):
                    self.seats[seats.index(x)]["action"] = f"Out {ordinal(place)}"
            if busts:
                self.toast = "  \u00b7  ".join(busts)
            elif k == "ft_quiet":
                won = max(range(len(ev[3])), key=lambda i: ev[3][i])
                self.toast = ("You win the blinds" if won == 0 else
                              f"{self.seats[won].get('position', '')} takes the blinds")
        elif k == "fold_choice":
            self.fold_choice = True

    def reset_hand(self, button, stacks):
        self.ev_worker.cancel()
        self._runout = None
        self.pot_final = 0
        self.final_holes = None
        self.last_state, self.final = None, None
        self.autonext = self.autofolded = False
        self.toast = "Replay" if self.replaying else ""
        self.tree, self.tree_scroll = [], 0
        self.dealer, self.board, self.pot, self.street = button, [], 0, "preflop"
        self.seats = [{"stack": st, "in_hand": True} for st in stacks]
        self.icm_spot = self.icm_last = None   # the coach button is for this hand's spot
        self.state = None
        self.waiting_next = False

    # ----- your actions -----
    def send(self, move):
        s, adv = self.state, self.advice
        if not s:
            return
        self.state = None
        you, bot = describe(move, s), describe(adv, s)
        same = kind(move, s) == kind(adv, s)
        if same and kind(move, s) == "Raise":
            def to(m):
                return max(s.min_raise_to, min(m[1], s.max_raise_to))
            same = abs(to(move) - to(adv)) <= 0.25 * to(adv)
        st = self.stats[self.mode]
        if not self.replaying:
            st["agree"][1] += 1
            st["agree"][0] += same
        if s.street == "preflop":
            spot = ["Unopened", "Vs open", "Vs 3-bet", "Vs 4-bet"][min(raises_before(s), 3)]
        else:
            spot = s.street.capitalize()
        self.feedback = {"you": you, "bot": bot, "same": same, "spot": f"{spot} · {s.position}"}
        for node in reversed(self.tree):
            if node["hero"] and node["verdict"] is None and not node["auto"]:
                if node.get("sim_mix"):
                    # The solve mixes: an action it takes at least a quarter of the time with
                    # your exact cards counts as right too.
                    mix, ch = node["sim_mix"], bucket(move, s)
                    near = min(mix, key=lambda k: (k[0] != ch[0], abs((k[1] or 0) - (ch[1] or 0))))
                    same = mix.get(near, 0) >= 0.25 or near == max(mix, key=mix.get)
                    self.feedback["same"] = same
                node["chosen"], node["verdict"] = bucket(move, s), same
                if node["chosen"] not in [o[0] for o in node["options"]]:
                    node["options"].append((node["chosen"], key_label(node["chosen"]), 0.0,
                                            ACT_COLOURS.get(node["chosen"][0], RAISE_COLOURS[0])))
                break
        self.refresh_chart()
        last = next((n for n in reversed(self.tree) if n["hero"] and n["verdict"] is not None
                     and not n["auto"]), None)
        self.caption_node = last
        self.caption = caption_for(last) if last else None
        self.caption_until = time.time() + 2.5
        if you == "Fold":
            if self.mode == "Drill":
                self.fast_forward = True
            elif self.mode == "Final table":
                # The rest of the hand plays out quickly (Fast fold), or at bot speed.
                self.quick_rest = self.fast_fold
            else:
                self.fold_wait = True  # the next bot to act waits for Watch / Next hand
        else:
            self.after_you = True
        self.apply_move(s, move)
        self.seats[0].pop("turn", None)
        self.write(f"{s.position:<5} YOU  {you}" + ("" if same else f"   (bot: {bot})"))
        self.draw()
        self.replies.put(move)

    def do_fold(self):
        if self.state:
            self.send(("fold", 0) if self.state.to_call else ("check", 0))

    def do_call(self):
        if self.state:
            self.send(("call", 0) if self.state.to_call else ("check", 0))

    def do_raise(self, amt):
        if self.state and self.state.min_raise_to:
            self.send(("raise", amt))

    def do_allin(self):
        if self.state and self.state.min_raise_to:
            self.send(("raise", self.state.max_raise_to))

    def do_custom(self):
        s = self.state
        if s and s.min_raise_to:
            amt = int(round((self.custom or 0) * BB))
            self.send(("raise", max(s.min_raise_to, min(s.max_raise_to, amt))))

    def next_hand(self):
        if not self.waiting_next:
            return
        if self.anim_on():   # the run-out is still dealing: see it, then the next hand
            if not getattr(self, "_next_queued", False):
                self._next_queued = True
                self.root.after(int(1000 * self.runout_left()) + 350, self._queued_next)
            return
        for d in self.seats:
            d.pop("result", None)
        self.waiting_next = False
        self.replies.put("next")
        self.draw()

    def _queued_next(self):
        self._next_queued = False
        self.next_hand()

    def k_next(self):
        if self.fold_choice:
            self.skip_rest()
        else:
            self.next_hand()

    def watch_rest(self):
        """You folded: let the rest of the hand play out at bot speed."""
        if self.fold_choice:
            self.fold_choice = False
            self.fast_forward = False
            self.fold_wait = False
            self.draw()

    def skip_rest(self):
        """You folded: skip the rest of the hand and deal the next one."""
        if self.fold_choice:
            self.fold_choice = False
            self.fast_forward = True
            self.fold_wait = False
            self.draw()

    def replay(self):
        """Play the last hand again: same cards, seats and stacks."""
        if self.autonext:
            self.hold = True
            self.autonext = False
            self.waiting_next = True
        if not self.waiting_next:
            return
        for d in self.seats:
            d.pop("result", None)
        self.waiting_next = False
        self.toast = "Replaying the hand"
        self.replies.put("replay")
        self.draw()

    def review(self):
        """Hold the hand you folded so you can look at the tree; Next hand when done."""
        if self.autonext:
            self.hold = True
            self.autonext = False
            self.waiting_next = True
            self.panel_tab = "Tree"
            self.draw()

    # ----- looking back at earlier hands -----
    VIEW_FIELDS = ("seats", "board", "pot", "dealer", "street", "tree", "tree_scroll", "final",
                   "final_holes", "hand_depth", "feedback", "caption", "caption_node",
                   "caption_until", "state", "advice", "waiting_next", "autonext", "fold_choice", "toast",
                   "notice")

    def snapshot(self, won, was_replay):
        snap = {f: getattr(self, f) for f in self.VIEW_FIELDS}
        snap["seats"] = [dict(d) for d in self.seats]   # next_hand() clears results
        snap["tree"] = list(self.tree)
        snap.update(state=None, advice=None, waiting_next=False, autonext=False, fold_choice=False,
                    caption=None, caption_until=0.0, notice="")
        hole = self.seats[0].get("cards") or []
        snap["label"] = (" ".join(hole) + "  " if hole else "") + (
            f"{'+' if won > 0 else ''}{won / BB:.1f} bb" if won else "0 bb")
        if was_replay:
            snap["label"] += "  (replay)"
        return snap

    def finished(self):
        """True when the table shows a hand that has already ended (it is past[-1])."""
        return bool(self.past) and (self.waiting_next or self.autonext) and self.state is None

    def go_back(self):
        """Step back one hand (B or the left arrow). The table waits until you come back."""
        if self.view is None:
            first = len(self.past) - (2 if self.finished() else 1)
            if first < 0:
                return
            if self.autonext:  # keep the hand that just ended up when you come back
                self.hold, self.autonext, self.waiting_next = True, False, True
            self._live = {f: getattr(self, f) for f in self.VIEW_FIELDS}
            self.view = first
        elif self.view > 0:
            self.view -= 1
        else:
            return
        self.load_view()

    def go_forward(self):
        if self.view is None:
            return
        last = len(self.past) - (2 if self._live_finished() else 1)
        if self.view < last:
            self.view += 1
            self.load_view()
        else:
            self.back_to_table()

    def _live_finished(self):
        live = self._live or {}
        return bool(self.past) and (live.get("waiting_next") or live.get("autonext")) and \
            live.get("state") is None

    def load_view(self):
        for f, v in self.past[self.view].items():
            if f in self.VIEW_FIELDS:
                setattr(self, f, v)
        self.panel_tab = "Tree"
        self.refresh_chart()
        self.draw()

    def back_to_table(self):
        if self.view is None:
            return
        for f, v in self._live.items():
            setattr(self, f, v)
        self.view, self._live = None, None
        self.refresh_chart()
        self.draw()

    def k_size(self, i):
        if self.state and i < len(self._sizes):
            self.do_raise(self._sizes[i])

    # ----- settings -----
    def set_mode(self, m):
        self.close_icm()
        changed = m != self.mode
        if changed:
            self.feedback = None
            self.panel_tab = "Tree" if m == "Play" else "Setup"
        self.mode = m
        if m == "Final table" and self.ft is None:
            self.ft_new(restart=False)
        if changed:
            self.restart_hand()
        self.draw()

    def restart_hand(self):
        """New settings take effect now: drop the hand in progress and deal a fresh one."""
        self.settings_version += 1  # the game thread drops a hand dealt under older settings
        if self.view is not None:
            self.back_to_table()
        if self.state is not None:       # waiting for your move
            self.state = None
            self.seats[0].pop("turn", None)
            self.replies.put("redeal")
        elif self.fold_choice:           # you folded: skip the rest
            self.skip_rest()
        elif self.waiting_next:          # the hand is over: deal the next one
            self.next_hand()

    def set_tab(self, t):
        self.panel_tab = "Tree" if t == "Tree" else "Setup"
        self.draw()

    def set_attr(self, name, v):
        setattr(self, name, v)
        self.draw()

    def set_spot(self, key, v):
        changed = self.spot.get(key) != v
        self.spot[key] = v
        self.notice = ""
        if changed and self.mode == "Spots":
            self.restart_hand()
        self.draw()

    def set_speed(self, v):
        self.speed = v
        self.draw()

    def toggle(self, attr):
        setattr(self, attr, not getattr(self, attr))
        self.draw()

    def toggle_hint(self):
        self.show_hint = not self.show_hint
        self.draw()

    def close(self):
        if self.ft is not None:
            FTL.save_session(self.ft, self.ft.leaks)
        self.replies.put("quit")
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=8, help="2-8 players including you")
    ap.add_argument("--depth", type=float, default=25, help="starting stack in Play mode (bb)")
    ap.add_argument("--bots", default=STUDY, help="which bot fills the other seats")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--solved", action="store_true",
                    help="use the experimental 8-max / 6-max solver results (solver/mccfr8.py)")
    args = ap.parse_args()
    if not 2 <= args.players <= 8:
        sys.exit("--players must be 2 to 8")
    global PLAYERS_FOR_SOLVES, USE_SOLVED
    PLAYERS_FOR_SOLVES = args.players
    USE_SOLVED = args.solved
    load_study_table()
    load_flop_sims()
    root = tk.Tk()
    Trainer(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
