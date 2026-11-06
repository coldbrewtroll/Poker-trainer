"""ICM study maths: exact ICM, the satellite race form, and push/fold under ICM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import icm_study as I  # noqa: E402


def test_icm_basics():
    assert [round(v, 6) for v in I.icm([10, 10, 10], [50, 30, 20])] == [33.333333] * 3
    a, b = I.icm([30, 10], [65, 35])
    assert abs(a - 57.5) < 1e-9 and abs(b - 42.5) < 1e-9
    eq = I.icm([50, 30, 20], [50, 30, 20])
    assert abs(sum(eq) - 100) < 1e-9 and eq[0] < 50  # the chip leader's $ share < chip share


def test_satellite_matches_exact_icm_without_field():
    st = [30, 20, 10, 5, 25, 8]
    exact = I.icm(st, [1, 1, 1, 0.5, 0, 0])
    race = I.satellite(st, 3, leftover=0.5)
    assert max(abs(a - b) for a, b in zip(exact, race)) < 0.003


def test_icm_tightens_jams_and_calls():
    pays = I.icm_value([30, 20, 14, 10, 8, 7, 6, 5])
    st = [30, 25, 22, 20, 18, 15, 12, 10]
    icm = I.solve_jam(st, pays, 5)
    chips = I.solve_jam(st, I.chips_value, 5)
    for seat, (_, need) in icm["calls"].items():
        assert need > chips["calls"][seat][1]  # the risk premium
    # Into shorter stacks (who call tighter) you jam wider; into covering stacks, tighter.
    assert icm["share"] > chips["share"]
    st = [10, 12, 15, 18, 20, 22, 25, 30]
    assert I.solve_jam(st, pays, 2)["share"] < I.solve_jam(st, I.chips_value, 2)["share"]


def test_landmark_is_chip_ev_below_target_and_capped_above():
    st = [30, 25, 22, 20, 18, 15, 12, 10]
    assert I.landmark([30, 50], 40) == [0.75, 1.0]
    far = I.solve_jam(st, I.landmark_value(200), 5)
    chips = I.solve_jam(st, I.chips_value, 5)
    assert abs(far["share"] - chips["share"]) < 1e-9
    rp = I.risk_premiums(st, I.landmark_value(35))
    assert rp[0][7] > 10 and abs(rp[7][0]) < 1e-9  # the big stack near the target pays a premium


def test_prize_pools_sum_to_the_penny():
    for pool in list(I.POOLS.values()) + [123_457, 2_000_000]:
        pays = I.pool_payouts(pool)
        assert len(pays) == 9 and abs(sum(pays) - pool) < 1e-6
        assert pays == sorted(pays, reverse=True)


def test_preset_tables_fixed_and_playable():
    tables = [I.preset_table(k) for k in range(1, I.N_TABLES + 1)]
    assert tables[36] == I.preset_table(37)
    assert len({tuple(t) for t in tables}) == I.N_TABLES
    for st in tables:
        assert len(st) == 9 and min(st) >= 5 and max(st) <= 100
        assert len(I.table_spots(st)) >= 6


def test_call_from_matches_solve_call():
    st = [30, 25, 22, 20, 18, 15, 12, 10]
    v = I.icm_value(I.pool_payouts(1_000_000)[:8])
    a = I.solve_call(st, v, 2, 6)
    b = I.call_from(I.solve_jam(st, v, 2), 6)
    assert abs(a["share"] - b["share"]) < 1e-12
    assert max(abs(a["gain"][h] - b["gain"][h]) for h in a["gain"]) < 1e-9
    assert [I.ordinal(k) for k in (1, 2, 3, 4, 9, 11, 21)] == \
        ["1st", "2nd", "3rd", "4th", "9th", "11th", "21st"]


def test_short_sb_folds_trash_to_an_open():
    """7 left, the SB with 4 bb facing a 14 bb HJ's open: the opener (who holds a real
    range and is priced in) answers first, so Q4o is a fold, and it's a fold under ICM by
    more than in chips."""
    from bots.preflop8 import TOP
    names = I.exact()[0]
    st = [13.5, 8, 14, 102, 27.5, 4, 15]
    posts = [0, 0, 2, 0, 0, 0.5, 1]
    prior = {2: [1.0 if TOP[h] <= 0.23 else 0.0 for h in names]}
    kw = dict(posts=posts, responders=[2, 6], fold_to=2, priors=prior)
    icm = I.solve_jam(st, I.icm_value(I.pool_payouts(1_000_000)[:7]), 5, **kw)
    chips = I.solve_jam(st, I.chips_value, 5, **kw)
    assert icm["gain"]["Q4o"] < 0 and icm["gain"]["AA"] > 0
    assert icm["share"] < chips["share"] < 0.6
