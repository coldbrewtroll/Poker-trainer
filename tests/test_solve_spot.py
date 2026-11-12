"""Solve a spot: a spot and flop solved before (or the same flop with the suits swapped)
opens from the saved solve instead of solving again."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "web" / "py")]

import solve_spot as S  # noqa: E402

SPOT, DEPTH = "SRP_BTN_vs_BB", 40.0


def _library_board():
    boards = S.library(DEPTH, SPOT)
    assert boards, "the 40 bb BTN vs BB library is missing"
    b = boards[0]
    return [b[i:i + 2] for i in (0, 2, 4)]


def test_same_flop():
    assert S.same_flop(["Kh", "7d", "2c"], ["Ks", "7h", "2d"]) is not None
    assert S.same_flop(["Kh", "7h", "2c"], ["Ks", "7h", "2d"]) is None      # two-tone vs rainbow
    assert S.same_flop(["Kh", "7d", "2c"], ["Kh", "7d", "3c"]) is None


def test_library_solve_found_and_renamed():
    board = _library_board()
    hit = S.find_saved(DEPTH, SPOT, board, S.DEFAULT_SIZES)
    assert hit is not None
    f, p, d = hit
    assert all(k == v for k, v in p.items())
    assert S.saved_accuracy(d) == S.LIBRARY_ACCURACY
    # the same flop, suits swapped (spades <-> clubs, hearts <-> diamonds)
    sw = {"s": "c", "c": "s", "h": "d", "d": "h"}
    other = [c[0] + sw[c[1]] for c in board]
    hit2 = S.find_saved(DEPTH, SPOT, other, S.DEFAULT_SIZES)
    assert hit2 is not None and hit2[0] == f
    d2 = S.renamed(hit2[2], hit2[1])
    assert sorted(d2["board"]) == sorted(other)
    t1, t2 = S.Tree(d), S.Tree(d2)
    assert t1.summary()[0] == t2.summary()[0]           # the same strategy
    assert sorted(t1.summary()[1]) == sorted(t2.summary()[1])
    # other sizes: not the same solve
    assert S.find_saved(DEPTH, SPOT, board, dict(S.DEFAULT_SIZES, ip_flop="50")) is None
