"""Names for 8-max preflop spots.

A spot is the hero's position followed by every earlier preflop action that wasn't a
fold, as <position><r|c|a> (raise, call, all-in), joined with underscores:

    CO                 CO, folded to: first in
    BTN_UTGr           BTN facing an UTG open
    BTN_UTGr_COc       BTN facing an UTG open and a CO call
    UTG_UTGr_BTNr      UTG opened, BTN 3-bet, back to UTG
    BB_SBr             big blind vs the small blind's open (blind vs blind)

Heads-up names (sb_first, bb_vs_raise, ... from bots/lines.py) mean the same spots with
only the blinds in: sb_first = SB, bb_vs_raise = BB_SBr, sb_vs_3bet = SB_SBr_BBr, etc.
"""

import re

from bots.lines import parse_line
from engine8 import POSITIONS_8

_POS = "|".join(sorted(POSITIONS_8, key=len, reverse=True))
SPOT = re.compile(rf"^({_POS})((?:_(?:{_POS})[rca])*)$")
ITEM = re.compile(rf"({_POS})([rca])")


def hu_to_spot(code: str) -> str:
    """Heads-up line code (e.g. 'rr') -> blind-vs-blind spot (e.g. 'SB_SBr_BBr')."""
    actors = ["SB" if k % 2 == 0 else "BB" for k in range(len(code))]
    hero = "SB" if len(code) % 2 == 0 else "BB"
    return "_".join([hero] + [p + c for p, c in zip(actors, code)])


def parse_spot(name: str):
    """'BTN_UTGr' or a heads-up name like 'bb_vs_raise' -> canonical spot, or None."""
    parts = name.split("_")
    canon = "_".join([parts[0].upper()] + [t[:-1].upper() + t[-1:].lower() for t in parts[1:]])
    if SPOT.match(canon):
        return canon
    code = parse_line(name)
    if code is not None:
        return hu_to_spot(code)
    return None


def current_spot(s) -> str:
    """Spot for a bot's preflop decision in engine8."""
    pos = {p.seat: p.position for p in s.players}
    heads_up = len(s.players) == 2
    if heads_up:  # the heads-up button is also the small blind
        pos = {seat: ("SB" if p == "BTN" else p) for seat, p in pos.items()}
    stacks = {p.seat: p.stack for p in s.players}
    items = []
    raises = [k for k, (st, _, a, _) in enumerate(s.history) if st == "preflop" and a == "raise"]
    last_raise = raises[-1] if raises else -1
    for k, (st, seat, a, _) in enumerate(s.history):
        if st != "preflop" or a not in ("raise", "call"):
            continue
        code = "c" if a == "call" else "r"
        # A raise is an all-in if that player has nothing left (only the latest raise can
        # be: the hand wouldn't have continued past an earlier all-in re-raise).
        if code == "r" and k == last_raise and stacks.get(seat) == 0:
            code = "a"
        items.append(pos[seat] + code)
    return "_".join([pos[s.my_seat]] + items)


def spot_items(spot: str):
    """'BTN_UTGr_COc' -> ('BTN', [('UTG', 'r'), ('CO', 'c')])"""
    hero, _, rest = spot.partition("_")
    return hero, ITEM.findall(rest)
