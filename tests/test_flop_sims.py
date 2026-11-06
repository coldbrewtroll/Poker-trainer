"""Pio flop solves: parsing Pio's output and following a solve in a hand."""
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import flop_sims  # noqa: E402
from solver import pio_export as E  # noqa: E402

RANKS, SUITS = "23456789TJQKA", "cdhs"
CARDS = [r + s for r in RANKS for s in SUITS]
HANDS = [CARDS[j] + CARDS[i] for j in range(52) for i in range(j)]  # Pio-like: higher first


def test_parse_children_and_rows():
    lines = ["child 0:", "r:0:b185", "IP_DEC", "Ks 7d 2c ", "185 0 560 ", "3 children",
             "flags: PIO_CFR ", "", "child 1:", "r:0:c", "IP_DEC", "Ks 7d 2c ", "0 0 560 ",
             "3 children", "flags: PIO_CFR "]
    kids = E.parse_children(lines)
    assert [k["id"] for k in kids] == ["r:0:b185", "r:0:c"]
    assert kids[0]["bets"] == (185.0, 0.0) and kids[0]["pot"] == 560.0
    assert E.parse_rows([" ".join(["0.5"] * 1326), "junk"]) == [[0.5] * 1326]
    m = E.NAME.match("SRP_BTN_vs_BB_30bb_Ks7d2c")
    assert m.group("spot") == "SRP_BTN_vs_BB" and m.group("depth") == "30"


@dataclass
class State:
    hole: list
    history: list = field(default_factory=list)
    street: str = "flop"
    to_call: int = 0
    min_raise_to: int = 8
    max_raise_to: int = 220
    board: list = field(default_factory=lambda: ["Ks", "7d", "2c"])


def make_sim():
    n = len(HANDS)
    bet_all = [1000 if "A" in h[::2] else 0 for h in HANDS]  # aces bet, the rest check
    nodes = {"r:0": {"player": "OOP", "actions": ["b185", "c"], "bets": [0, 0],
                     "strategy": [bet_all, [1000 - v for v in bet_all]]},
             "r:0:c": {"player": "IP", "actions": ["c", "b420"], "bets": [0, 0],
                       "strategy": [[1000] * n, [0] * n]}}
    d = {"spot": "SRP_BTN_vs_BB", "depth": 30, "board": ["Ks", "7d", "2c"], "pot": 560,
         "stack": 2795, "chips_per_bb": 100, "oop": "BB", "ip": "BTN", "hands": HANDS,
         "ranges": {"OOP": [1000] * n, "IP": [1000] * n}, "nodes": nodes}
    return flop_sims.FlopSim(d)


def test_follow_solve():
    sim = make_sim()
    assert sim.pick(State(["Ah", "5c"]), 8) == ("raise", 15)   # 185 chips at 100/bb -> 15
    assert sim.pick(State(["Qh", "5c"]), 8) == ("check", 0)
    after_check = State(["Qh", "5c"], history=[("flop", 1, "check", 0)])
    assert sim.node_for(after_check, 8) == "r:0:c"
    deck = sim.make_deck(random.Random(1), 8, 0, "BTN", 7)
    assert len(set(deck)) == 52 and len(deck) == 52


def test_spot_names_from_old_folders():
    cases = {"BU-BB SRP 40bb (Updated Sizings)": ("SRP", "BTN", "BB", 40.0),
             "BB vs BTN 3bet 25bb": ("3BP", "BTN", "BB", 25.0),
             "UTG+1 vs BB SRP 50bb": ("SRP", "UTG1", "BB", 50.0),
             "SRP_CO_vs_BB_30bb": ("SRP", "CO", "BB", 30.0),
             "3BP_BB_vs_BTN_40bb": ("3BP", "BTN", "BB", 40.0)}
    for folder, want in cases.items():
        assert E.spot_of(Path(f"D:/saves/{folder}/Ks7d2c.cfr")) == want, folder
    assert E.spot_of(Path("D:/saves/BB-SB limp 30bb/Ks7d2c.cfr")) == ("LP", "SB", "BB", 30.0)
    assert E.spot_name("3BP", "BTN", "BB") == "3BP_BB_vs_BTN"
    assert E.chip_scale(560, 2795, 30) == 100 and E.chip_scale(5.53, 37.98, 40) == 1


def test_three_bet_pot_roles():
    sim = make_sim()
    sim3 = flop_sims.FlopSim(dict(sim.d, spot="3BP_BB_vs_BTN", pot=1438))
    assert (sim3.kind, sim3.opener, sim3.other) == ("3BP", "BTN", "BB")
    assert 6 < sim3.three_bet_bb() < 8


def test_nearest_flop_maps_cards_and_sizes():
    sim = make_sim()   # Ks7d2c, pot 560 solve chips, OOP aces bet 185
    m = flop_sims.find([sim], "SRP", "BTN", "BB", ["Qh", "8s", "3c"], 44, 220)
    assert m is not None and m.name.endswith("(for Qh8s3c)")
    assert [m.card(c) for c in ("Qh", "8s", "3c")] == ["Ks", "7d", "2c"]
    assert m.card("Ac")[0] == "A" and m.card("Kc")[0] == "A"   # above the top card
    st = State(["Ad", "4h"], board=["Qh", "8s", "3c"], max_raise_to=220)
    mv = m.pick(st, 8)
    assert mv[0] == "raise" and abs(mv[1] - 185 / 560 * 44) <= 1   # a third of the pot
    # A bet of 15 real chips (a third of 44) is the solve's 185 bet.
    n = len(HANDS)
    sim.d["nodes"]["r:0:b185"] = {"player": "IP", "actions": ["f", "c"], "bets": [185, 0],
                                  "strategy": [[0] * n, [1000] * n]}
    st2 = State(["Qd", "5d"], board=["Qh", "8s", "3c"], history=[("flop", 7, "raise", 15)])
    assert m.node_for(st2, 8) == "r:0:b185"
    # Shapes must match: a monotone or paired flop doesn't stand in for a rainbow one.
    assert flop_sims.find([sim], "SRP", "BTN", "BB", ["Qh", "8h", "3h"], 44, 220) is None
    assert flop_sims.find([sim], "SRP", "BTN", "BB", ["Qh", "Qs", "3c"], 44, 220) is None
    # Too deep for the solve (stack-to-pot 20 vs 5): no stand-in.
    assert flop_sims.find([sim], "SRP", "BTN", "BB", ["Qh", "8s", "3c"], 44, 880) is None


def test_preflop_line():
    h = [("preflop", 0, "fold", 0), ("preflop", 5, "raise", 16), ("preflop", 6, "fold", 0),
         ("preflop", 7, "call", 16)]
    assert flop_sims.preflop_line(h) == ("SRP", 5, 7)
    h3 = h[:3] + [("preflop", 7, "raise", 60), ("preflop", 5, "call", 60)]
    assert flop_sims.preflop_line(h3) == ("3BP", 5, 7)
    assert flop_sims.preflop_line([("preflop", 6, "call", 8), ("preflop", 7, "check", 0)]) == \
        ("LP", 6, 7)
    assert flop_sims.preflop_line([("preflop", 6, "call", 8), ("preflop", 7, "raise", 28)]) is None


def test_limped_pots():
    assert E.spot_of(Path("D:/work/SB-BB Limped 12bb/Ks7d2c.cfr")) == ("LP", "SB", "BB", 12.0)
    assert E.spot_name("LP", "SB", "BB") == "LP_SB_vs_BB"
    assert flop_sims.roles("LP_SB_vs_BB") == ("LP", "SB", "BB")
    h = [("preflop", k, "fold", 0) for k in range(6)] + [("preflop", 6, "call", 8),
                                                        ("preflop", 7, "check", 0)]
    assert flop_sims.preflop_line(h) == ("LP", 6, 7)
    sim = make_sim()
    lp = flop_sims.FlopSim(dict(sim.d, spot="LP_SB_vs_BB", oop="SB", ip="BB"))
    assert lp.kind == "LP" and lp.name.endswith("limped pot Ks7d2c")
    d = flop_sims.SimDirector(lp, "SB", 7, 8, random.Random(1))

    @dataclass
    class S:
        history: list
        to_call: int
    assert d._preflop(S([], 4), True) == ("call", 0)      # the SB completes
    assert d._preflop(S([], 0), False) == ("check", 0)    # the BB checks


def test_open_size_from_the_solve_pot():
    sim = make_sim()     # BTN v BB, pot 5.6bb = 2 x 2.05 open + 1 ante + 0.5 SB
    assert abs(sim.open_bb() - 2.05) < 1e-9
    sim.d["pot"] = 2 * 250 + 150   # an older save with a 2.5x open
    assert abs(sim.open_bb() - 2.5) < 1e-9


def test_every_real_card_translates():
    """Real ranks with no room on the solved board still map (to the nearest free rank),
    and two cards squeezed onto one are pulled apart."""
    sim = make_sim()                       # solved Ks7d2c
    sim.board = ["Jd", "Th", "9s"]         # (a board with no gaps between its ranks)
    sim.d["board"] = sim.board
    m = flop_sims.find([sim], "SRP", "BTN", "BB", ["Kc", "7h", "2d"], 560, 2795)
    assert m is not None
    import itertools
    rest = [c for c in CARDS if c not in ("Kc", "7h", "2d")]
    for hole in itertools.combinations(rest, 2):
        out = m.cards(list(hole))
        assert out is not None and out[0] != out[1] and not set(out) & set(sim.board), hole
