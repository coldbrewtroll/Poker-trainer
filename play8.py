"""Play against the bots yourself, in this window.

    python play8.py                       # you + 7 contender8 bots, 25 bb each
    python play8.py --players 6 --depth 40
    python play8.py --bots starter8       # easier opponents

Online tournament antes (12.5% of the big blind each). Chips carry over from hand to hand;
anyone who goes broke (you too) gets a fresh starting stack, and your running total is
shown after every hand. Type your move when it says "Your move":

    f        fold
    c        check, or call
    r 6      raise to 6 big blinds in total
    a        all-in
    q        quit
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import new_deck  # noqa: E402
from engine8 import Game8  # noqa: E402
import match8  # noqa: E402

BB = match8.BIG_BLIND


def bb(chips):
    return f"{chips / BB:g}"


class Human:
    """A seat played from the keyboard."""

    def __init__(self):
        self.shown_upto = 0  # how much of the history has been printed this hand

    def new_hand(self, hand_no, is_button):
        self.shown_upto = 0

    def act(self, s):
        names = {p.seat: p.position for p in s.players}
        new = s.history[self.shown_upto:]
        self.shown_upto = len(s.history)
        street = None
        for st, seat, a, amt in new:
            if st != street:
                street = st
            who = "You" if seat == s.my_seat else names[seat]
            print(f"   {who:<5} {a}" + (f" to {bb(amt)} bb" if a == "raise" else ""))
        print()
        board = " ".join(s.board) if s.board else "(none yet)"
        print(f"  {s.street.upper()}   board: {board}   pot: {bb(s.pot)} bb")
        others = "  ".join(f"{p.position} {bb(p.stack)}" + (" (bet " + bb(p.bet) + ")" if p.bet else "")
                           for p in s.players if p.seat != s.my_seat and not p.folded)
        print(f"  still in: {others}")
        print(f"  YOU ({s.position}): {' '.join(s.hole)}   stack {bb(s.my_stack)} bb"
              + (f", already bet {bb(s.my_bet)} bb" if s.my_bet else ""))
        options = ["f = fold", "c = " + (f"call {bb(s.to_call)} bb" if s.to_call else "check")]
        if s.min_raise_to:
            options.append(f"r N = raise to N bb ({bb(s.min_raise_to)} to {bb(s.max_raise_to)})")
            options.append("a = all-in")
        print("  " + "   ".join(options))
        while True:
            try:
                text = input("  Your move: ").strip().lower()
            except EOFError:
                raise SystemExit
            if text in ("q", "quit", "exit"):
                raise SystemExit
            if text in ("f", "fold"):
                return ("fold", 0) if s.to_call else ("check", 0)
            if text in ("c", "call", "check", "k"):
                return ("call", 0) if s.to_call else ("check", 0)
            if text in ("a", "all", "allin", "all-in") and s.min_raise_to:
                return "raise", s.max_raise_to
            if text.startswith("r") and s.min_raise_to:
                try:
                    to = round(float(text[1:].strip()) * BB)
                except ValueError:
                    print("  Type r and a number, like: r 6")
                    continue
                if to < s.min_raise_to or to > s.max_raise_to:
                    print(f"  The raise must be from {bb(s.min_raise_to)} to "
                          f"{bb(s.max_raise_to)} bb.")
                    continue
                return "raise", to
            print("  Type f, c, r and a number, a, or q.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=8, help="2-8 players including you")
    ap.add_argument("--depth", type=float, default=25, help="starting stack in big blinds")
    ap.add_argument("--bots", default="contender8", help="which bot fills the other seats")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if not 2 <= args.players <= 8:
        sys.exit("--players must be 2 to 8")
    rng = random.Random(args.seed)
    human = Human()
    bots = [human] + [match8.make(args.bots, rng.randrange(10**6))
                      for _ in range(args.players - 1)]
    start = int(args.depth * BB)
    game = Game8(bots, stack=start, big_blind=BB, ante=match8.ANTE, time_limit_s=1e9)
    stacks = [start] * args.players
    total = 0.0
    hand_no = 0
    print(__doc__.split("Type your move")[0].strip().splitlines()[0])
    print(f"{args.players} players, {args.depth:g} bb each, you are in seat 1. "
          "Type q to quit.\n")
    try:
        while True:
            button = hand_no % args.players
            deck = new_deck(rng)
            print("=" * 70)
            print(f"Hand {hand_no + 1}")
            result = game.play_hand(deck, button=button, hand_no=hand_no, stacks=stacks)
            # What happened after your last move, and the showdown.
            last = game.last_hand if hasattr(game, "last_hand") else None
            hist = human_view_rest(human, game)
            for line in hist:
                print(line)
            won = result[0]
            total += won
            print(f"\n  You {'won' if won > 0 else 'lost' if won < 0 else 'broke even'}"
                  f" {bb(abs(won))} bb this hand.   Running total: {total / BB:+g} bb "
                  f"over {hand_no + 1} hands.")
            stacks = [st + r for st, r in zip(stacks, result)]
            for i, st in enumerate(stacks):
                if st < BB:
                    stacks[i] = start
                    if i == 0:
                        print("  You went broke: here's a fresh stack.")
            hand_no += 1
            del last
    except SystemExit:
        pass
    print(f"\nYou played {hand_no} hands: {total / BB:+g} bb "
          f"({total / BB / max(hand_no, 1) * 100:+.1f} bb per 100 hands).")


def human_view_rest(human, game):
    """Lines for the end of the hand: remaining actions, board, showdown."""
    r = human.last_result
    if r is None:
        return []
    names = r["positions"]
    lines = []
    for st, seat, a, amt in r["history"][human.shown_upto:]:
        who = "You" if seat == r["my_seat"] else names[seat]
        lines.append(f"   {who:<5} {a}" + (f" to {bb(amt)} bb" if a == "raise" else ""))
    if r["board"]:
        lines.append(f"  Board: {' '.join(r['board'])}")
    for seat, cards in r["shown"].items():
        who = "You" if seat == r["my_seat"] else names[seat]
        lines.append(f"  {who:<5} shows {' '.join(cards)}")
    return lines


def _hand_over(self, result):
    self.last_result = result


Human.hand_over = _hand_over
Human.last_result = None

if __name__ == "__main__":
    main()
