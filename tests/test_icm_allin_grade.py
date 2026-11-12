"""Jam / call grades: a coin flip (within FT.ALLIN_CLOSE of the pool, or a hand the solve
plays both ways) counts either answer right; anything else only the better one."""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "web" / "py")]
try:
    import tkinter  # noqa: F401
except ImportError:
    import tkstub
    tkstub.install()

import icm_study as I  # noqa: E402
import final_table as FT  # noqa: E402

ST = [24.0, 12.0, 23.0, 11.0, 19.0, 9.5, 15.5]
PAYS = I.pool_payouts(1_000_000)[:7]
S = SimpleNamespace(max_raise_to=10_000)
JAM, FOLD = ("raise", 10_000), ("fold", 0)


def _spot(hand, kind="jam", hero=2, jammer=None):
    return {"kind": kind, "stacks": list(ST), "pays": PAYS, "hero": hero, "me": hero,
            "jammer": jammer, "hand": hand, "kw": {}}


def _grades(hand, **kw):
    go = JAM if kw.get("kind", "jam") == "jam" else ("call", 0)
    return FT.grade(_spot(hand, **kw), go, S), FT.grade(_spot(hand, **kw), FOLD, S)


def test_clear_hands_one_answer():
    (g_ok, g), (f_ok, _) = _grades("AA")
    assert g_ok and not f_ok and g > 0
    (g_ok, g), (f_ok, _) = _grades("72o")
    assert f_ok and not g_ok and g < 0


def test_coin_flip_both_right():
    pool = sum(PAYS)
    res = FT.solve(list(ST), PAYS, 2)
    close = min(res["gain"], key=lambda h: abs(res["gain"][h]))
    assert abs(res["gain"][close]) <= FT.ALLIN_CLOSE * pool     # (some hand is that close)
    (g_ok, _), (f_ok, _) = _grades(close)
    assert g_ok and f_ok
    sp = FT.ensure_solved(_spot(close))
    sp["answer"] = "go" if res["gain"][close] <= 0 else "fold"
    assert "coin flip" in FT.allin_verdict(sp, True, res["gain"][close])


def test_calls_graded_with_the_callers_range():
    res = I.call_from(FT.solve(list(ST), PAYS, 2), 5)
    assert res["call_range"] is not None and len(res["call_range"]) == 169
    (c_ok, g), (f_ok, _) = _grades("AA", kind="call", hero=5, jammer=2)
    assert c_ok and not f_ok and g > 0
