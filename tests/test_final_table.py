"""Final table sessions: whole tables play to the end with places and payouts."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import final_table as FT  # noqa: E402
import icm_study as I  # noqa: E402
import match8  # noqa: E402
from engine import new_deck  # noqa: E402
from engine8 import Game8  # noqa: E402

BB = match8.BIG_BLIND


def test_whole_final_table():
    pays = I.pool_payouts(1_000_000)
    ft = FT.FinalTable(12, pays, BB)
    ft.level_hands = 10   # (quick levels: the whole table well within the hand limit)
    rng = random.Random(3)
    bots = {x: FT.ICMBot(match8.make("contender8", x), ft, rng) for x in range(8)}
    hands = 0
    while len(ft.alive()) > 1 and hands < 400:
        seats, b, st = ft.start_hand()
        assert seats[0] == 0 or 0 in ft.out   # you are always engine seat 0
        g = Game8([bots[x] for x in seats], big_blind=BB, ante=BB, ante_mode="bb",
                  time_limit_s=1e9)
        ft.finish_hand(seats, st, g.play_hand(new_deck(rng), button=b, hand_no=hands,
                                              stacks=st))
        hands += 1
    assert len(ft.alive()) == 1 and ft.over is not None
    places = sorted(p for p, _ in ft.out.values())
    assert places == list(range(1, 9))
    assert abs(sum(prize for _, prize in ft.out.values()) - sum(pays[:8])) < 1e-6


def test_push_fold_spot_reads_the_state():
    from engine8 import State, Player
    names = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
    stacks = [100, 120, 80, 96, 60, 160, 400, 400]   # chips behind (BB 8/bb)
    players = [Player(seat=i, pid=i, position=nm, stack=stacks[i], bet=0, folded=False,
                      all_in=False) for i, nm in enumerate(names)]
    players[6].bet, players[6].stack = 4, 396
    players[7].bet, players[7].stack = 8, 384     # posted 8 + 8 ante
    s = State(hole=["As", "5s"], board=[], street="preflop", pot=28, to_call=8,
              min_raise_to=16, max_raise_to=60, my_stack=60, my_bet=0, big_blind=8,
              my_seat=4, position="CO", button_seat=5, players=players, history=[
                  ("preflop", k, "fold", 0) for k in range(4)], ante_total=8)
    kind, st, me, j = FT.push_fold_spot(s)
    assert kind == "jam" and me == 4 and st[4] == 7.5 and st[7] == 50.0 and j is None


def test_open_policy_keeps_practical_jams():
    from engine8 import State, Player
    names = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]

    def state(hole, my_bb):
        pl = [Player(seat=i, pid=i, position=nm, stack=400, bet=0, folded=False, all_in=False)
              for i, nm in enumerate(names)]
        pl[4].stack = int(my_bb * 8)
        return State(hole=hole, board=[], street="preflop", pot=20, to_call=8, min_raise_to=16,
                     max_raise_to=pl[4].stack, my_stack=pl[4].stack, my_bet=0, big_blind=8,
                     my_seat=4, position="CO", button_seat=5, players=pl,
                     history=[("preflop", k, "fold", 0) for k in range(4)], ante_total=8)
    FT.POLICY["on"] = True
    try:
        jam = lambda s: ("raise", s.max_raise_to)  # noqa: E731
        s = state(["As", "Ah"], 10)
        assert FT.open_policy(s, jam(s)) == ("raise", 16)          # premium: open 2x
        s = state(["5s", "5h"], 10)
        assert FT.open_policy(s, jam(s)) == jam(s)                 # a practical jam stays
        s = state(["5s", "5h"], 40)
        assert FT.open_policy(s, jam(s)) == ("raise", 16)          # too deep to open-jam
        s = state(["Ks", "Qh"], 6)
        assert FT.open_policy(s, ("raise", 16)) == jam(s)          # under 8 bb: push / fold
    finally:
        FT.POLICY["on"] = False


def test_wsop_blind_levels():
    ft = FT.FinalTable(12, I.pool_payouts(1_000_000), BB)
    assert ft.blinds() == "80K/160K (160K ante)" and ft.level_hands == 30
    total = None
    for _ in range(30):
        ft.start_hand()
        total = sum(ft.stacks)
    assert ft.level == 1 and ft.left_in_level() == 1
    ft.start_hand()                                    # hand 31: 100K/200K
    assert ft.level == 2 and ft.blinds() == "100K/200K (200K ante)"
    assert abs(sum(ft.stacks) - total / 1.25) <= 1     # stacks 160/200 as deep in bb
    ft.level_hands = 0
    for _ in range(40):
        ft.start_hand()
    assert ft.level == 2 and ft.left_in_level() is None


def test_leak_log_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(FT, "LEAK_FILE", tmp_path / "leaks.json")
    ft = FT.FinalTable(3, I.pool_payouts(1_000_000), BB)
    log = ft.leaks
    for _ in range(10):
        log.spot("call")
    log.spot("jam_first")
    log.add("Call an all-in: too loose", "call", 4200, "Hand 3 ...", 3)
    log.add("Call an all-in: too loose", "call", 900, "Hand 7 ...", 7)
    log.add("Jam first in: too tight", "jam_first", 2500, "Hand 9 ...", 9)
    assert [e["cost"] for e in log.top(3)] == [4200, 2500, 900]
    assert log.by_type()["Call an all-in: too loose"] == (2, 10, 5100)
    ft.hands = 12
    FT.save_session(ft, log)
    FT.save_session(ft, log)                     # once only
    hist = FT.load_history()
    assert len(hist) == 1 and hist[0]["blunders"] == 3 and hist[0]["lost"] == 7600
    spots, leaks = FT.all_time(hist)
    assert leaks["Call an all-in: too loose"] == (2, 5100) and spots["call"] == 10


def test_price_guard():
    from engine8 import State, Player
    pl = [Player(seat=i, pid=i, position=p, stack=200, bet=0, folded=False, all_in=False)
          for i, p in enumerate(["UTG", "BTN", "SB", "BB"])]
    s = State(hole=["Kd", "4d"], board=[], street="preflop", pot=92, to_call=8,
              min_raise_to=0, max_raise_to=0, my_stack=200, my_bet=16, big_blind=8, my_seat=0,
              position="UTG", button_seat=1, players=pl, history=[], ante_total=8)
    FT.POLICY["on"] = True
    try:
        assert FT.price_guard(s, ("fold", 0)) == ("call", 0)     # 1 bb into 11.5 bb
        s2 = State(**{**s.__dict__, "to_call": 80, "pot": 120})
        assert FT.price_guard(s2, ("fold", 0)) == ("fold", 0)
    finally:
        FT.POLICY["on"] = False


def test_iso_raise_over_a_limp():
    from engine8 import State, Player
    names = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
    pl = [Player(seat=i, pid=i, position=nm, stack=472, bet=0, folded=nm not in ("SB", "BB"),
                 all_in=False) for i, nm in enumerate(names)]
    pl[6].bet = pl[7].bet = 8
    s = State(hole=["As", "Kd"], board=[], street="preflop", pot=24, to_call=0, min_raise_to=16,
              max_raise_to=480, my_stack=464, my_bet=8, big_blind=8, my_seat=7, position="BB",
              button_seat=5, players=pl, ante_total=8,
              history=[("preflop", k, "fold", 0) for k in range(6)] + [("preflop", 6, "call", 8)])
    assert FT.iso_size(s, ("raise", 16)) == ("raise", 28)          # 60 bb: 3.5 bb, not 2
    assert FT.iso_size(s, ("raise", 480)) == ("raise", 480)        # a jam stays a jam
    assert FT.iso_size(s, ("check", 0)) == ("check", 0)
    assert FT.iso_to_bb(20) == 3.0 and FT.iso_to_bb(80) == 4.0 and FT.iso_to_bb(40, 2) == 4.5


def test_sb_open_size():
    from engine8 import State, Player
    names = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
    pl = [Player(seat=i, pid=i, position=nm, stack=480, bet=0, folded=nm not in ("SB", "BB"),
                 all_in=False) for i, nm in enumerate(names)]
    pl[6].bet, pl[6].stack, pl[7].bet = 4, 476, 8
    s = State(hole=["As", "Kd"], board=[], street="preflop", pot=20, to_call=4, min_raise_to=16,
              max_raise_to=480, my_stack=476, my_bet=4, big_blind=8, my_seat=6, position="SB",
              button_seat=5, players=pl, ante_total=8,
              history=[("preflop", k, "fold", 0) for k in range(6)])
    assert FT.iso_size(s, ("raise", 16)) == ("raise", 28)     # 60 bb: 3.5 bb, not 2
    assert FT.iso_size(s, ("call", 0)) == ("call", 0)          # a limp stays a limp


def _facing_open(my_stack_bb, opener_stack_bb=40, hole=("Ah", "Jh")):
    """The SB facing a 2 bb BTN open, 8-max, BB ante (chips: 8 per bb)."""
    from engine8 import State, Player
    names = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
    pl = [Player(seat=i, pid=i, position=nm, stack=320, bet=0, folded=i < 5, all_in=False)
          for i, nm in enumerate(names)]
    pl[5].stack, pl[5].bet = opener_stack_bb * 8 - 16, 16
    me = int(my_stack_bb * 8)
    pl[6].stack, pl[6].bet = me - 4, 4
    pl[7].stack, pl[7].bet = 320 - 8, 8
    hist = [("preflop", k, "fold", 0) for k in range(5)] + [("preflop", 5, "raise", 16)]
    return State(hole=list(hole), board=[], street="preflop", pot=36, to_call=12,
                 min_raise_to=24, max_raise_to=me, my_stack=me - 4, my_bet=4, big_blind=8,
                 my_seat=6, position="SB", button_seat=5, players=pl, history=hist,
                 ante_total=8)


def test_commit_jam_and_legal():
    s = _facing_open(12)
    assert FT.commit_jam(s, ("raise", 54)) == ("raise", 96)   # 6.75 of 12 bb: all-in
    assert FT.commit_jam(s, ("raise", 40)) == ("raise", 40)   # 5 of 12 bb: a 3-bet
    assert FT.commit_jam(s, ("call", 0)) == ("call", 0)
    deep = _facing_open(60, opener_stack_bb=60)
    assert FT.commit_jam(deep, ("raise", 54)) == ("raise", 54)
    from dataclasses import replace
    stuck = replace(s, min_raise_to=0, max_raise_to=0)       # all-in for less to call
    assert FT.legal(stuck, ("raise", 0)) == ("call", 0)
    assert FT.legal(s, ("raise", 96)) == ("raise", 96)


def test_short_stack_facing_an_open_is_jam_or_fold(monkeypatch):
    """Under 12 bb (not the BB), facing an open: no flat when ICM says it's not a jam."""
    table = FT.FinalTable(1, I.pool_payouts(1_000_000), 8)
    monkeypatch.setitem(FT.POLICY, "on", True)
    monkeypatch.setitem(FT.POLICY, "icm", True)
    monkeypatch.setitem(FT.POLICY, "table", table)
    s = _facing_open(10, hole=("7h", "2d"))
    assert FT.icm_layer(s, ("call", 0)) == ("fold", 0)
    s = _facing_open(10, hole=("Ah", "Ad"))
    assert FT.icm_layer(s, ("call", 0)) == ("raise", s.max_raise_to)


def test_icm_veto_keeps_the_cheap_option(monkeypatch):
    """ICM says no to a jam: facing an open, the BB, or anyone with 12 bb+, calls instead;
    under 12 bb it's a fold."""
    monkeypatch.setitem(FT.POLICY, "on", True)
    monkeypatch.setitem(FT.POLICY, "icm", True)
    monkeypatch.setitem(FT.POLICY, "table", FT.FinalTable(1, I.pool_payouts(1_000_000), 8))
    from dataclasses import replace
    short = _facing_open(10, hole=("7h", "2d"))                # SB 10 bb: jam or fold
    assert FT.icm_layer(short, ("raise", short.max_raise_to)) == ("fold", 0)
    s = _facing_open(20, hole=("7h", "4h"))                    # SB 20 bb: flats instead
    assert FT.icm_layer(s, ("raise", s.max_raise_to)) == ("call", 0)
    pl = [replace(p) for p in s.players]
    pl[6].stack, pl[6].bet = 160 - 4, 4                          # SB 20 bb, still in
    pl[7].stack, pl[7].bet = 160 - 8, 8
    hist = s.history + [("preflop", 6, "fold", 0)]
    pl[6].folded = True
    bb = replace(s, players=pl, history=hist, my_seat=7, position="BB", to_call=8,
                 my_stack=152, my_bet=8, max_raise_to=160, pot=36)
    assert FT.icm_layer(bb, ("raise", 160)) == ("call", 0)


def test_allin_call_for_less():
    """A 1 bb stack facing an open and a 3-bet (all-in to call, 10:1): no folding AK."""
    from engine8 import State, Player
    names = ["CO", "BTN", "SB", "BB"]
    pl = [Player(seat=i, pid=i, position=nm, stack=200, bet=0, folded=nm == "SB",
                 all_in=False) for i, nm in enumerate(names)]
    pl[0].stack, pl[0].bet = 184, 16
    pl[1].stack, pl[1].bet = 158, 42
    pl[3].stack, pl[3].bet = 8, 8
    s = State(hole=["Ah", "Kh"], board=[], street="preflop", pot=78, to_call=34,
              min_raise_to=0, max_raise_to=0, my_stack=8, my_bet=8, big_blind=8, my_seat=3,
              position="BB", button_seat=1, players=pl, ante_total=8,
              history=[("preflop", 0, "raise", 16), ("preflop", 1, "raise", 42),
                       ("preflop", 2, "fold", 0)])
    assert FT.allin_for_less(s, ("fold", 0)) == ("call", 0)
    from dataclasses import replace
    six = replace(s, my_stack=48, to_call=60, pot=118, hole=["4h", "5h"])   # 6 bb into 15
    assert FT.allin_for_less(six, ("call", 0)) == ("fold", 0)   # 'bluff' call: no
    assert FT.allin_for_less(replace(six, hole=["Ah", "Kh"]), ("fold", 0)) == ("call", 0)


def test_strong_hands_dont_fold_postflop():
    from engine8 import State, Player
    pl = [Player(seat=0, pid=0, position="BTN", stack=400, bet=0, folded=False, all_in=False),
          Player(seat=1, pid=1, position="BB", stack=300, bet=100, folded=False, all_in=False)]
    s = State(hole=["As", "7s"], board=["Ac", "6s", "Ad", "9h"], street="turn", pot=300,
              to_call=100, min_raise_to=200, max_raise_to=400, my_stack=400, my_bet=0,
              big_blind=8, my_seat=0, position="BTN", button_seat=0, players=pl,
              history=[("turn", 1, "raise", 100)], ante_total=8)
    assert FT.price_guard(s, ("fold", 0)) == ("call", 0)        # trip aces, half pot
    from dataclasses import replace
    air = replace(s, hole=["3c", "2d"])
    assert FT.price_guard(air, ("fold", 0)) == ("fold", 0)
    cheap = replace(s, hole=["Kc", "Qd"], to_call=20, pot=380)     # 5% ... K-high: call
    assert FT.price_guard(cheap, ("fold", 0)) == ("call", 0)


def test_allin_for_less_leaves_calls_of_an_allin_alone():
    """Facing an all-in (equal stacks: the call puts you all-in too), your sims' calling
    range decides, not the all-in-for-less rule."""
    from engine8 import State, Player
    pl = [Player(seat=0, pid=0, position="BTN", stack=0, bet=80, folded=False, all_in=True),
          Player(seat=1, pid=1, position="SB", stack=76, bet=0, folded=True, all_in=False),
          Player(seat=2, pid=2, position="BB", stack=72, bet=8, folded=False, all_in=False)]
    s = State(hole=["Kh", "9d"], board=[], street="preflop", pot=100, to_call=72,
              min_raise_to=0, max_raise_to=80, my_stack=72, my_bet=8, big_blind=8, my_seat=2,
              position="BB", button_seat=0, players=pl, ante_total=8,
              history=[("preflop", 0, "raise", 80), ("preflop", 1, "fold", 0)])
    assert FT.allin_for_less(s, ("call", 0)) == ("call", 0)
    assert FT.allin_for_less(s, ("fold", 0)) == ("fold", 0)
