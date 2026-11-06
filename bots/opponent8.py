"""Per-opponent models for 8-max, keyed by each opponent's stable id (pid).

Same idea as opponent.py: every frequency starts from a typical 8-max value and moves
toward what that opponent actually does as hands accumulate."""

from bots.opponent import Frequency


class Model8:
    def __init__(self):
        self.open = Frequency(0.25)         # raises when no one has raised yet
        self.three_bet = Frequency(0.07)    # re-raises a single raise
        self.reraise = Frequency(0.20)      # re-raises when facing 2+ raises
        self.cold_call = Frequency(0.12)    # calls a raise
        self.aggression = Frequency(0.30)   # bets/raises per postflop decision
        self.folds_to_bet = Frequency(0.45)  # folds facing a postflop bet
        self.calls_allin = Frequency(0.25)  # calls a preflop all-in
        # The same split by whether they'd already put chips in voluntarily this hand
        # (raised or called: a re-jam over their open) or not (cold, or in the blinds).
        self.calls_jam_in = Frequency(0.50)
        self.calls_jam_cold = Frequency(0.12)
        self.folds_to_reraise = Frequency(0.55)  # folds when their raise gets re-raised


class Models:
    def __init__(self):
        self.by_pid = {}

    def get(self, pid) -> Model8:
        return self.by_pid.setdefault(pid, Model8())

    def learn(self, result):
        """result: engine8 hand_over dict (absolute seats)."""
        me = result["my_seat"]
        pids = result["pids"]
        start = result.get("stacks") or {}
        # An all-in raise is the stack minus the ante. Engines that don't report antes:
        # count a raise of 97% of the stack or more as all-in.
        antes = result.get("antes") or [int(x * 0.03) for x in (start or [0] * len(pids))]
        raises = 0
        facing_allin = False
        invested = set()
        raised_at = {}  # seat -> raise count right after their own raise
        for st, seat, a, amt in result["history"]:
            if st != "preflop":
                break
            if seat != me and a != "check":
                m = self.get(pids[seat])
                if facing_allin:
                    called = a in ("call", "raise")
                    m.calls_allin.add(called)
                    (m.calls_jam_in if seat in invested else m.calls_jam_cold).add(called)
                elif raises > raised_at.get(seat, raises):
                    m.folds_to_reraise.add(a == "fold")
                    m.reraise.add(a == "raise")
                elif raises == 0:
                    m.open.add(a == "raise")
                elif raises == 1:
                    m.three_bet.add(a == "raise")
                    m.cold_call.add(a == "call")
                else:
                    m.reraise.add(a == "raise")
            if a in ("raise", "call"):
                invested.add(seat)
            if a == "raise":
                raises += 1
                raised_at[seat] = raises
                if start and amt >= start[seat] - antes[seat]:
                    facing_allin = True
        last = None  # (street, seat) of the latest bet/raise on the current street
        for st, seat, a, _ in result["history"]:
            if st == "preflop":
                continue
            if last and last[0] != st:
                last = None
            if seat != me:
                m = self.get(pids[seat])
                m.aggression.add(a == "raise")
                if last and last[1] != seat:
                    m.folds_to_bet.add(a == "fold")
            if a == "raise":
                last = (st, seat)
