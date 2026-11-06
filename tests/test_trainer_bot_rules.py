"""Trainer-only bot settings (STUDY in trainer8): the shallowest sim reaching down to the
push/fold charts, and deep open all-ins read as strong ranges."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match8  # noqa: E402
from engine import new_deck  # noqa: E402
from engine8 import Game8  # noqa: E402

BB = match8.BIG_BLIND
HANDS = [a + b + s for i, a in enumerate("AKQJT98765432") for b in "AKQJT98765432"[i + 1:]
         for s in "so"] + [r + r for r in "AKQJT98765432"]


class First:
    """Folds everywhere; at `pos` asks `bot` once and remembers the move."""

    def __init__(self, bot, pos, plan=None):
        self.bot, self.pos, self.plan, self.move = bot, pos, dict(plan or {}), None

    def new_hand(self, *a):
        pass

    def hand_over(self, r):
        pass

    def act(self, s):
        if s.position in self.plan:
            return self.plan.pop(s.position)(s)
        if s.position == self.pos and self.move is None:
            self.move = self.bot.act(s)
        return ("fold", 0) if s.to_call else ("check", 0)


def ask(bot, pos, depth, hole, plan=None):
    seats = [First(bot, pos, plan) for _ in range(8)]
    deck = new_deck(random.Random(3))
    from cards import card
    g = Game8(seats, stack=int(depth * BB), big_blind=BB, ante=match8.ANTE, time_limit_s=1e9)
    target = {"UTG": 0, "BB": 7}[pos]     # button 5: UTG is seat 0, BB seat 7
    deck = [c for c in deck if c not in (card(hole[0]), card(hole[1]))]
    deck[2 * target:2 * target] = [card(hole[0]), card(hole[1])]
    g.play_hand(deck, button=5, hand_no=0)
    return next(x.move for x in seats if x.move is not None)


def test_stretch_down_plays_the_shallowest_sim():
    table = {"UTG": {15.0: {"raise_2bb": {h: 1.0 for h in HANDS}}}}   # opens everything
    for flag, want in ((0, None), (1, "raise")):
        bot = match8.make(f"contender8:stretch_down={flag}", 1)
        bot.table = table
        mv = ask(bot, "UTG", 11, ["7h", "2d"])
        if flag:
            assert mv == ("raise", 2 * BB)               # the 15 bb sim, at 11 bb
        else:
            assert mv[0] == "fold"                        # built-in: no 72o at 11 bb


def test_deep_jam_calls_tighter():
    jam = {"BTN": lambda s: ("raise", s.max_raise_to)}
    hole = ["Kh", "8d"]
    wide = match8.make("contender8", 1)
    tight = match8.make("contender8:deep_jam_bb=24;deep_jam_k=0.4", 1)
    wide.table = tight.table = {}
    assert ask(wide, "BB", 60, hole, dict(jam))[0] == "call"     # vs 'any BTN open' x 1.3
    assert ask(tight, "BB", 60, hole, dict(jam))[0] == "fold"    # a 60 bb jam is strong


def test_drop_unreached(tmp_path):
    import os
    pytest = __import__("pytest")
    T = pytest.importorskip("trainer8")      # (needs tkinter)
    for n in ("BB_COa@20__call.txt", "UTG@20__raise_29pct.txt", "BB_COa@60__call.txt"):
        (tmp_path / n).write_text("AA\n")
        os.utime(tmp_path / n, (1000, 1000))         # old files (from the kit)
    (tmp_path / "_unreached.txt").write_text("BB_COa@20__call.txt\nmissing@15__call.txt\n"
                                             "BB_COa@60__call.txt\n")
    os.utime(tmp_path / "_unreached.txt", (2000, 2000))
    (tmp_path / "BB_COa@60__call.txt").write_text("KK\n")   # re-imported since: newer
    assert T.drop_unreached(tmp_path) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["BB_COa@60__call.txt",
                                                         "UTG@20__raise_29pct.txt",
                                                         "_unreached.done.txt"]
    assert T.drop_unreached(tmp_path) == 0          # once only
    (tmp_path / "_unreached.txt").write_text("UTG@20__raise_29pct.txt\n")
    assert T.drop_unreached(tmp_path) == 0          # done before: never again
    assert (tmp_path / "UTG@20__raise_29pct.txt").exists()
