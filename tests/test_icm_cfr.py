"""The exact game solves (icm_cfr): converged, in the trainer's format, and the deep lines
right where fictitious play was off."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "web" / "py")]
try:
    import tkinter  # noqa: F401
except ImportError:
    import tkstub
    tkstub.install()

import icm_cfr as C  # noqa: E402
import icm_study as I  # noqa: E402

pytestmark = pytest.mark.skipif(not C.available(), reason="numpy not installed")


def _v(n, pool=1_000_000):
    pays = I.pool_payouts(pool)[:n]
    return I.icm_value(list(pays)), sum(pays)


def test_sb_game_converged_and_deep_line():
    st = [37.18, 32.65, 22.04]
    v, pool = _v(3)
    g = I.solve_sb(st, v)
    assert g["exact"]
    assert max(g["exploit"].values()) < 2e-6 * pool      # (per hand)
    # facing the small blind's 4-bet jam, KQo calls: worth about 1.4% of the pool more
    # (fictitious play folded it)
    ev = g["nodes"]["bb_vs_4bet"]["ev"]["KQo"]
    assert ev["call"] - ev["fold"] > 0.01 * pool
    # the format the trainer reads
    for k in ("sb", "sb_vs_iso", "sb_vs_jam_limp", "sb_vs_jam_raise", "sb_vs_3bet",
              "bb_vs_limp", "bb_vs_rejam_iso", "bb_vs_raise", "bb_vs_4bet", "bb_vs_jam"):
        assert k in g["nodes"]
    for h, m in g["nodes"]["sb"]["mix"].items():
        assert abs(sum(m.values()) - 1) < 1e-6, h
    assert len(g["ranges"]["bb_call_jam"]) == 169


def test_open_game_converged():
    st = [11.66, 25.49, 10.1, 18.57, 5.41, 19.89, 16.4]
    v, pool = _v(7)
    g = I.solve_open(st, v, 2)
    assert g["exact"]
    assert max(g["exploit"].values()) < 2e-6 * pool
    for i in g["resp"]:
        assert g["nodes"][f"vs_open:{i}"]["mix"]
        assert g["nodes"][f"open_vs_rejam:{i}"]["ev"]
    # AA opens or jams, 72o folds
    assert g["nodes"]["open"]["mix"]["72o"].get("fold", 0) > 0.99
    assert g["nodes"]["open"]["mix"]["AA"].get("fold", 0) < 0.01


def test_answers_match_a_longer_solve():
    st = [14.12, 20.9, 20.41, 32.4]
    v, pool = _v(4)
    a = C.solve_sb(st, v)
    b = C.solve_sb(st, v, 3 * C.SB_ROUNDS)
    for node in ("sb", "bb_vs_limp", "bb_vs_raise", "bb_vs_jam"):
        for h, ev in b["nodes"][node]["ev"].items():
            if h not in a["nodes"][node]["ev"]:
                continue
            mine = a["nodes"][node]["ev"][h]
            best = max(ev, key=ev.get)
            pick = max(mine, key=mine.get)
            assert ev[best] - ev[pick] < 2e-4 * pool, (node, h)


def test_fallback_without_numpy(monkeypatch):
    monkeypatch.setattr(I, "EXACT_GAMES", False)
    g = I.solve_sb([24.56, 41.43], _v(2)[0])
    assert g is not None and not g.get("exact")
