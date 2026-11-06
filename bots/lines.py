"""Names for heads-up preflop spots ("lines") and sized actions, shared by the range
importer and the bots.

A line is the preflop action so far as a short code: c = call/limp, r = raise, a = all-in.
The small blind (button) acts first, so "" is the SB's first decision, "r" is the BB facing
an open raise, "rr" the SB facing a 3-bet, "ra" the SB facing an all-in 3-bet, and so on.
Friendly aliases exist for the common ones.

Actions: fold, call (check counts as call), allin, raise, or a sized raise:
raise_2.5bb (to 2.5 big blinds), raise_3x (3x the bet faced), raise_75pct (75% of pot).
"""

import re

ALIASES = {
    "sb_first": "",
    "bb_vs_limp": "c",
    "bb_vs_raise": "r",
    "bb_vs_allin": "a",
    "sb_vs_3bet": "rr",
    "sb_vs_3bet_allin": "ra",
    "bb_vs_4bet": "rrr",
    "bb_vs_4bet_allin": "rra",
    "sb_vs_5bet": "rrrr",
    "sb_vs_5bet_allin": "rrra",
    "sb_limp_vs_raise": "cr",
    "sb_limp_vs_allin": "ca",
    "bb_vs_limp_3bet": "crr",
    "bb_vs_limp_reshove": "cra",
}
NAMES = {code: name for name, code in ALIASES.items()}
LINE = re.compile(r"^[cr]*[ra]?$")
ACTION = re.compile(r"^(fold|call|check|allin|raise)(?:_(\d+(?:\.\d+)?)(bb|x|pct))?$")


def parse_line(name: str):
    """'bb_vs_raise' or 'line_rr' -> line code, or None if not a valid spot name."""
    name = name.lower()
    if name in ALIASES:
        return ALIASES[name]
    if name.startswith("line_") or name == "line":
        code = name[5:]
        if LINE.match(code) and "a" not in code[:-1]:
            return code
    return None


def parse_action(name: str):
    """'raise_2.5bb' -> 'raise_2.5bb', 'check' -> 'call'; None if not an action."""
    m = ACTION.match(name.lower())
    if not m:
        return None
    kind = "call" if m.group(1) == "check" else m.group(1)
    if m.group(2) and kind != "raise":
        return None
    return kind if not m.group(2) else f"raise_{m.group(2)}{m.group(3)}"


def describe(code: str) -> str:
    return NAMES.get(code, f"line_{code}")


def current_line(state):
    """Line code for a bot's preflop decision. Only the last raise can be an all-in (the
    hand would not continue past one), and it is one when the opponent has nothing left."""
    code = "".join("r" if a == "raise" else "c" for st, _, a, _ in state.history
                   if st == "preflop" and a in ("raise", "call"))
    if code.endswith("r") and state.opp_stack == 0:
        code = code[:-1] + "a"
    return code


def raise_amount(state, action: str, default):
    """Total raise-to amount for a sized action key (or the default size if unsized)."""
    if action == "allin":
        return state.max_raise_to
    m = ACTION.match(action)
    if m and m.group(2):
        size, unit = float(m.group(2)), m.group(3)
    else:
        size, unit = default
    if unit == "bb":
        target = size * state.big_blind
    elif unit == "x":
        target = size * max(state.opp_bet, state.big_blind)
    else:  # pct of the pot after calling
        target = state.opp_bet + size / 100 * (state.pot + state.to_call)
    return int(min(max(target, state.min_raise_to), state.max_raise_to))
