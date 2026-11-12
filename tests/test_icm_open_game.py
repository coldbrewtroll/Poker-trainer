"""The short first-in game (icm_study.solve_open): the opener answers each player's re-jam
separately, and the values at its answers are the dollars once facing that bet."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "web" / "py")]
try:
    import tkinter  # noqa: F401
except ImportError:
    import tkstub
    tkstub.install()

import icm_study as I  # noqa: E402
import final_table as FT  # noqa: E402

# a 6.3 bb opener (seat 2) with a 27.8 bb player and a 5.4 bb big blind behind
ST = [35.1, 22.4, 6.3, 27.8, 5.4]
PAYS = I.pool_payouts(1_000_000)[:5]


def _game():
    return I.solve_open(list(ST), I.icm_value(PAYS), 2)


def test_answer_per_jammer():
    g = _game()
    for i in g["resp"]:
        nd = g["nodes"][f"open_vs_rejam:{i}"]
        assert nd["mix"] and nd["ev"]
    # the big blind is tiny: the opener calls its re-jam with every hand it raises
    calls = g["ranges"]["open_call"][4]
    opens = g["ranges"]["open"]
    # (combo-weighted; the first round's warm start leaves a trace of a fold or two)
    assert I._share_w(calls) >= 0.99 * I._share_w(opens)
    # (and against the deep player it folds some)
    deep = g["ranges"]["open_call"][3]
    assert sum(deep) < sum(opens) - 1e-6


def test_values_once_facing_the_jam():
    g = _game()
    v = I.icm_value(PAYS)
    st = list(ST)
    # folding to the deep player's re-jam: the opener loses its 2 bb raise, the jammer
    # takes the blinds, the ante and the raise
    after = list(st)
    after[2] -= I.OPEN_TO
    after[4] -= 1.0 + I.ANTE
    after[3] += I.OPEN_TO + 0.5 + 1.0 + I.ANTE
    after[3] -= 0.5                                  # (the small blind is the jammer's seat)
    fold_value = v(after, list(range(5)))[2]
    for h, ev in g["nodes"]["open_vs_rejam:3"]["ev"].items():
        assert abs(ev["fold"] - fold_value) < 1e-6 * sum(PAYS), h
    # AA calls, and calling is worth far more than folding once facing it
    ev = g["nodes"]["open_vs_rejam:3"]["ev"]["AA"]
    assert ev["call"] - ev["fold"] > 0.01 * sum(PAYS)


def test_coach_text_uses_the_jammers_answer():
    sp = {"game": "open", "stacks": list(ST), "opener": 2, "hero": 3, "me": 3, "kind": "mix",
          "node": "vs_open:3", "pays": PAYS, "jammer": None, "kw": {}}
    text = " ".join(" ".join(lines) for _, lines in FT.game_info(sp))
    assert "against a re-jam it" in text
