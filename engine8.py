"""No-Limit Hold'em engine for 2-8 players (8-max), for testing bots.

A bot is any object with act(state) -> (action, amount):
    ("fold", 0)  ("check", 0)  ("call", 0)  ("raise", raise_to)
raise_to is the bot's TOTAL bet on this street after raising (not the increment).
Optional hooks: new_hand(hand_no, is_button) and hand_over(result).

Seats are numbered clockwise. The button is a seat; the next two post the small and big
blind (heads-up, the button posts the small blind). Preflop the player after the big blind
acts first, postflop the first player still in after the button. Uneven all-ins create
side pots; ties split. Optional antes (every player by default, as in online tournaments).
Illegal actions are corrected and counted, like engine.py.

Positions with 8 players: UTG, UTG1, LJ, HJ, CO, BTN, SB, BB (fewer players drop the
earliest ones; heads-up: BTN (also the small blind), BB).
"""

import time
from dataclasses import dataclass, field

from cards import evaluate, name
from engine import STREETS, Stats

POSITIONS_8 = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]


def position_names(n):
    """Position names in seat order starting from the first to act preflop."""
    if n == 2:
        return ["BTN", "BB"]
    return POSITIONS_8[8 - n:]


@dataclass
class Player:
    seat: int
    pid: int          # stable id of the bot behind this seat (for opponent models)
    position: str
    stack: int        # chips behind
    bet: int          # this street
    folded: bool
    all_in: bool


@dataclass
class State:
    """Everything a bot may see when it's its turn."""
    hole: list
    board: list
    street: str
    pot: int              # all chips committed this hand, including this street's bets
    to_call: int
    min_raise_to: int     # 0 when raising isn't possible
    max_raise_to: int
    my_stack: int
    my_bet: int
    big_blind: int
    my_seat: int
    position: str
    button_seat: int
    players: list         # Player for every seat (including us)
    history: list = field(default_factory=list)  # (street, seat, action, amount)
    ante_total: int = 0   # antes in the pot this hand (dead money)
    # Heads-up style shortcuts so simple bots work unchanged:
    opp_bet: int = 0      # the bet to match (highest bet this street)
    opp_stack: int = 0    # largest stack among opponents still in
    is_button: bool = False

    @property
    def opponents(self):
        """Players still in the hand other than us."""
        return [p for p in self.players if p.seat != self.my_seat and not p.folded]


class Game8:
    def __init__(self, bots, stack=200, big_blind=2, time_limit_s=0.001, strict_time=False,
                 ante=0, ante_mode="each"):
        """ante: chips of dead money. ante_mode "each": every player posts it (the online
        tournament format, usually 1/8 of the big blind); "bb": the big blind posts it all
        (big blind ante, common live)."""
        assert 2 <= len(bots) <= 8
        assert ante_mode in ("bb", "each")
        self.bots = list(bots)
        self.stack = stack
        self.bb = big_blind
        self.sb = big_blind // 2
        self.ante = ante
        self.ante_mode = ante_mode
        self.time_limit = time_limit_s
        self.strict_time = strict_time
        self.stats = {}

    def stats_for(self, bot) -> Stats:
        return self.stats.setdefault(id(bot), Stats())

    def play_hand(self, deck, button=0, hand_no=0, pids=None, stacks=None):
        """One hand. Seat i gets deck[2i], deck[2i+1]; the board is the next five cards.
        pids: stable id per seat (default: the seat number). stacks: starting chips per
        seat (default: everyone gets `stack`). Returns the chip result per seat."""
        n = len(self.bots)
        pids = pids or list(range(n))
        holes = [[deck[2 * i], deck[2 * i + 1]] for i in range(n)]
        board_cards = deck[2 * n:2 * n + 5]
        stacks = list(stacks) if stacks else [self.stack] * n
        start = list(stacks)
        committed = [0] * n
        folded = [False] * n
        history = []
        # Seat order of positions: first to act preflop ... big blind.
        if n == 2:
            sb_seat, bb_seat = button, (button + 1) % 2
            order = [button, bb_seat]
        else:
            sb_seat, bb_seat = (button + 1) % n, (button + 2) % n
            order = [(button + 3 + k) % n for k in range(n)]
        names = position_names(n)
        position = {seat: names[k] for k, seat in enumerate(order)}
        for seat in range(n):
            hook = getattr(self.bots[seat], "new_hand", None)
            if hook:
                hook(hand_no, seat == button)

        # Antes: dead money straight into the pot (not part of anyone's bet).
        ante_total = 0
        antes = [0] * n
        if self.ante:
            payers = [bb_seat] if self.ante_mode == "bb" else range(n)
            for seat in payers:
                amt = min(self.ante, stacks[seat])
                stacks[seat] -= amt
                committed[seat] += amt
                antes[seat] = amt
                ante_total += amt
        self._ante_total = ante_total

        for street_i, street in enumerate(STREETS):
            board = board_cards[:(0, 3, 4, 5)[street_i]]
            bets = [0] * n
            if street == "preflop":
                for seat, amt in ((sb_seat, self.sb), (bb_seat, self.bb)):
                    amt = min(amt, stacks[seat])
                    stacks[seat] -= amt
                    bets[seat] = amt
                first = order[0]
            else:
                first = next(((button + 1 + k) % n for k in range(n)
                              if not folded[(button + 1 + k) % n]), button)
            live = [s for s in range(n) if not folded[s]]
            can_act = [s for s in live if stacks[s] > 0]
            if len(can_act) >= 2 or (street == "preflop" and can_act):
                self._betting(street, board, holes, stacks, bets, committed, folded, first,
                              button, position, pids, history)
            for s in range(n):
                committed[s] += bets[s]
            if sum(1 for s in range(n) if not folded[s]) == 1:
                break

        result = self._settle(holes, board_cards, committed, folded)
        shown = {s: [name(c) for c in holes[s]] for s in range(n)
                 if not folded[s] and sum(not f for f in folded) > 1}
        for seat in range(n):
            hook = getattr(self.bots[seat], "hand_over", None)
            if hook:
                hook({"result": result[seat], "my_seat": seat, "button_seat": button,
                      "board": [name(c) for c in board_cards], "shown": shown,
                      "history": list(history), "pids": list(pids),
                      "positions": dict(position), "stack": start[seat],
                      "stacks": start, "antes": antes})
        assert abs(sum(result)) < 1e-6
        return result

    @staticmethod
    def _settle(holes, board_cards, committed, folded):
        """Main pot and side pots by all-in level; each goes to the best eligible hand."""
        n = len(holes)
        won = [0.0] * n
        scores = {s: evaluate(holes[s] + board_cards) for s in range(n) if not folded[s]}
        levels = sorted({c for c in committed if c > 0})
        prev = 0
        for level in levels:
            layer = sum(min(c, level) - min(c, prev) for c in committed)
            eligible = [s for s in scores if committed[s] >= level]
            if not eligible:  # only folded money at this level: goes to the live players
                eligible = list(scores)
            best = max(scores[s] for s in eligible)
            winners = [s for s in eligible if scores[s] == best]
            for s in winners:
                won[s] += layer / len(winners)
            prev = level
        return [won[s] - committed[s] for s in range(n)]

    def _view(self, seat, street, board, holes, stacks, bets, committed, folded, button,
              position, pids, history):
        n = len(stacks)
        cur = max(bets)
        to_call = max(0, cur - bets[seat])
        max_to = bets[seat] + stacks[seat]
        others_can = any(stacks[s] > 0 for s in range(n) if s != seat and not folded[s])
        can_raise = others_can and max_to > cur
        min_to = min(cur + self._last_raise, max_to) if can_raise else 0
        players = [Player(s, pids[s], position[s], stacks[s], bets[s], folded[s],
                          stacks[s] == 0 and not folded[s]) for s in range(n)]
        opp_stacks = [stacks[s] for s in range(n) if s != seat and not folded[s]]
        return State(
            hole=[name(c) for c in holes[seat]], board=[name(c) for c in board],
            street=street, pot=sum(committed) + sum(bets), to_call=to_call,
            min_raise_to=min_to, max_raise_to=max_to if can_raise else 0,
            my_stack=stacks[seat], my_bet=bets[seat], big_blind=self.bb, my_seat=seat,
            position=position[seat], button_seat=button, players=players,
            history=list(history), ante_total=self._ante_total, opp_bet=cur,
            opp_stack=max(opp_stacks, default=0), is_button=seat == button)

    def _betting(self, street, board, holes, stacks, bets, committed, folded, first, button,
                 position, pids, history):
        n = len(stacks)
        self._last_raise = self.bb
        acted = [False] * n
        p = first
        while True:
            live = [s for s in range(n) if not folded[s]]
            if len(live) == 1:
                return
            cur = max(bets)
            pending = [s for s in live if stacks[s] > 0 and (not acted[s] or bets[s] < cur)]
            if not pending:
                return
            if folded[p] or stacks[p] == 0 or (acted[p] and bets[p] == cur):
                p = (p + 1) % n
                continue
            state = self._view(p, street, board, holes, stacks, bets, committed, folded,
                               button, position, pids, history)
            action, amount = self._ask(p, state)
            st = self.stats_for(self.bots[p])
            to_call = state.to_call
            if action == "raise" and not state.min_raise_to:
                action = "call"
                st.corrected += 1
            if action == "check" and to_call > 0:
                action = "fold"
                st.corrected += 1
            if action == "call" and to_call == 0:
                action = "check"
            if action == "fold" and to_call == 0:
                action = "check"
            if action == "fold":
                folded[p] = True
                history.append((street, p, "fold", 0))
            elif action == "check":
                history.append((street, p, "check", 0))
            elif action == "call":
                amt = min(to_call, stacks[p])
                stacks[p] -= amt
                bets[p] += amt
                history.append((street, p, "call", bets[p]))
            else:
                try:
                    raise_to = int(amount)
                except (TypeError, ValueError):
                    raise_to = state.min_raise_to
                    st.corrected += 1
                if raise_to < state.min_raise_to or raise_to > state.max_raise_to:
                    st.corrected += 1
                    raise_to = min(max(raise_to, state.min_raise_to), state.max_raise_to)
                increment = raise_to - cur
                if increment >= self._last_raise:  # a full raise reopens the action
                    self._last_raise = increment
                    acted = [False] * n
                stacks[p] -= raise_to - bets[p]
                bets[p] = raise_to
                history.append((street, p, "raise", raise_to))
            acted[p] = True
            p = (p + 1) % n

    def _ask(self, seat, state):
        st = self.stats_for(self.bots[seat])
        t0 = time.perf_counter()
        try:
            action, amount = self.bots[seat].act(state)
        except Exception:
            action, amount = "fold", 0
            st.errors += 1
        dt = time.perf_counter() - t0
        st.decisions += 1
        st.total_s += dt
        st.max_s = max(st.max_s, dt)
        if dt > self.time_limit:
            st.slow += 1
            if self.strict_time:
                return "fold", 0
        if action not in ("fold", "check", "call", "raise"):
            st.corrected += 1
            return "fold", 0
        return action, amount
