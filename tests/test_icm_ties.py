"""Split pots: the tie table, and a chop valued as a chop (not half a win) under ICM."""
import icm_study as I


def test_tie_table():
    names, idx, E, W = I.exact()
    T = I.ties()
    assert len(T) == 169
    for a in range(169):
        for b in range(169):
            assert T[a][b] == T[b][a]
            assert 0.0 <= T[a][b] <= 1.0
            if a != b:
                assert abs(E[a][b] + E[b][a] - 1) < 1e-9
    assert 0.85 < T[idx["AKs"]][idx["AKo"]] < 0.95          # AK vs AK: nearly always a chop
    assert T[idx["AA"]][idx["KK"]] < 0.01
    assert abs(E[idx["AA"]][idx["KK"]] - 0.8195) < 0.002     # (the exact figure)
    assert abs(E[idx["KK"]][idx["AKs"]] - 0.6595) < 0.002


def test_chop_share():
    pays = I.pool_payouts(1_000_000)
    st = [1.06, 23.63, 5.74]
    icm = I.Spot(st, I.icm_value(pays[:3]), 1)
    chips = I.Spot(st, lambda s, who: {i: s[i] for i in who}, 1)
    for i in icm.behind:
        assert abs(chips.chop_share(i, 1) - 0.5) < 1e-9
        assert abs(chips.chop_share(i, i) - 0.5) < 1e-9
        assert icm.chop_share(i, i) > 0.5        # surviving is worth more than half
    # a chop gives both their live chips back and splits the dead ante (the big blind's)
    c = I.Spot._showdown(st, [0.0, 0.5, 1.0], [0.0, 0.0, 1.0], [1.06, 23.13, 3.74], 1, 2, None)
    assert abs(sum(c) - sum(st)) < 1e-9
    assert abs(c[2] - (5.74 - 0.5)) < 1e-9 and abs(c[1] - (23.63 + 0.5)) < 1e-9


def test_call_gain_uses_chops():
    """call_from's gain = wins * win + losses * lose + chops * chop - fold, exactly."""
    pays = I.pool_payouts(1_000_000)[:3]
    res = I.solve_jam([1.06, 23.63, 5.74], I.icm_value(pays), 1, iters=30)
    cf = I.call_from(res, 2)
    sp, rj = res["spot"], I._sparse(res["jam_range"])
    names, idx, _, _ = I.exact()
    for h in ("62o", "AKo", "TT"):
        eq, tie = I.range_eq(idx[h], rj), I.range_tie(idx[h], rj)
        v = ((eq - tie / 2) * sp.lose[2][2] + (1 - eq - tie / 2) * sp.win[2][2]
             + tie * sp.chop[2][2] - sp.v_steal[2])
        assert abs(v - cf["gain"][h]) < 1e-6
