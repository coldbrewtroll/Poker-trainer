"""Plays your own imported solver ranges preflop (bots/range_table.py, made by
solver/import_ranges.py), mixing actions at the solved frequencies.

Between two solved stack depths it blends them; near (within 20% of) the shallowest or
deepest one it uses that one; elsewhere, and for spots you haven't imported, it falls back
to the parent bot. Unsized raises use DEFAULT_SIZES (set them to what your sims used).
"""

from bots import shortstack
from bots.lines import ALIASES, current_line, raise_amount
from bots.shortstack import hand_type
from cards import card

try:
    from bots.range_table import TABLE as _RAW
except ImportError:  # no ranges imported yet
    _RAW = {}

# Older tables used friendly names as keys; accept both.
TABLE = {ALIASES.get(k, k): v for k, v in _RAW.items()}

# Unsized "raise" per number of raises already made: open, 3-bet, 4-bet, 5-bet.
DEFAULT_SIZES = {0: (2.5, "bb"), 1: (3.0, "x"), 2: (2.2, "x"), 3: (2.0, "x")}
NEAR = 0.2      # use the nearest solved depth if within 20% of it
MAX_GAP = 2.0   # only blend two depths if the deeper is at most twice the shallower


def lookup(line, eff, table=None):
    """[(weight, {action: {hand: freq}})] for this spot and effective stack, or None."""
    depths = (TABLE if table is None else table).get(line)
    if not depths:
        return None
    solved = sorted(d for d in depths if d > 0)
    if solved:
        lo = max((d for d in solved if d <= eff), default=None)
        hi = min((d for d in solved if d >= eff), default=None)
        if lo is not None and hi is not None:
            if lo == hi:
                return [(1.0, depths[lo])]
            if hi / lo <= MAX_GAP:
                w = (eff - lo) / (hi - lo)
                return [(1 - w, depths[lo]), (w, depths[hi])]
        nearest = min(solved, key=lambda d: abs(d - eff))
        if abs(nearest - eff) <= NEAR * nearest:
            return [(1.0, depths[nearest])]
    if 0 in depths:
        return [(1.0, depths[0])]
    return None


def mix(blend, hand):
    """Action frequencies for one hand from a (possibly blended) lookup."""
    out = {}
    for w, acts in blend:
        for action, rng in acts.items():
            f = rng.get(hand, 0.0)
            if f:
                out[action] = out.get(action, 0.0) + w * f
    return out


class Bot(shortstack.Bot):
    table = None  # tests can set a table on an instance

    def act(self, s):
        if s.street == "preflop":
            table = TABLE if self.table is None else self.table
            if table:
                eff = min(s.my_stack + s.my_bet, s.opp_stack + s.opp_bet) / s.big_blind
                line = current_line(s)
                blend = lookup(line, eff, table)
                if blend:  # your own sims take priority over built-in strategy
                    return self.play_mix(s, line, mix(blend, hand_type(*map(card, s.hole))))
        return super().act(s)

    def play_mix(self, s, line, freqs):
        r = self.rng.random()
        for action, f in freqs.items():
            r -= f
            if r < 0:
                return self.do(s, line, action)
        return ("fold", 0) if s.to_call else ("check", 0)

    def do(self, s, line, action):
        if action == "fold":
            return ("fold", 0) if s.to_call else ("check", 0)
        if action == "call" or not s.min_raise_to:
            return ("call", 0) if s.to_call else ("check", 0)
        default = DEFAULT_SIZES.get(line.count("r"), (2.0, "x"))
        size, unit = default
        return "raise", raise_amount(s, action, (size, unit))
