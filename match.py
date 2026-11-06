"""Plays two bots against each other and reports the result.

    python match.py starter baselines.CallingStation --hands 5000
    python match.py starter baselines.Maniac --strict-time

Bots are given as module or module.Class under bots/ (Class defaults to Bot).
Duplicate format: every deck is played twice with the seats swapped, which cancels out
most of the card luck, so the result reflects skill much sooner.
"""

import argparse
import importlib
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import Game, new_deck  # noqa: E402


def load(spec, seed):
    mod_name, _, cls = spec.partition(".")
    mod = importlib.import_module(f"bots.{mod_name}")
    klass = getattr(mod, cls or "Bot")
    try:
        return klass(seed=seed)
    except TypeError:
        return klass()


def run(a, b, hands, seed=0, stack=200, big_blind=2, strict_time=False):
    game = Game([a, b], stack=stack, big_blind=big_blind, strict_time=strict_time)
    rng = random.Random(seed)
    per_pair = []
    for i in range(hands // 2):
        deck = new_deck(rng)
        r1 = game.play_hand(deck, button=i % 2, hand_no=2 * i)
        # Same cards, seats swapped: bot A now gets what bot B had.
        game.bots = [b, a]
        # Button stays with the seat too, so B gets exactly A's cards and position.
        r2 = game.play_hand(deck, button=i % 2, hand_no=2 * i + 1)
        game.bots = [a, b]
        per_pair.append(r1[0] + r2[1])
    return per_pair, (game.stats_for(a), game.stats_for(b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot_a")
    ap.add_argument("bot_b")
    ap.add_argument("--hands", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--stack", type=int, default=200, help="starting chips (big blind = 2)")
    ap.add_argument("--strict-time", action="store_true", help="fold any move over 1 ms")
    args = ap.parse_args()

    a, b = load(args.bot_a, args.seed + 1), load(args.bot_b, args.seed + 2)
    per_pair, stats = run(a, b, args.hands, args.seed, args.stack, strict_time=args.strict_time)
    bb = 2
    n = len(per_pair)
    mean = sum(per_pair) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in per_pair) / max(n - 1, 1))
    per100 = mean / 2 / bb * 100  # two hands per pair
    ci = 1.96 * sd / math.sqrt(n) / 2 / bb * 100
    print(f"{args.bot_a} vs {args.bot_b}: {n * 2} hands (duplicate)")
    print(f"  {args.bot_a} wins {per100:+.1f} bb/100  (95% range {per100 - ci:+.1f} to "
          f"{per100 + ci:+.1f})")
    for label, st in ((args.bot_a, stats[0]), (args.bot_b, stats[1])):
        avg = st.total_s / max(st.decisions, 1) * 1e6
        print(f"  {label}: {st.decisions} decisions, avg {avg:.0f} us, max "
              f"{st.max_s * 1e6:.0f} us, over 1 ms: {st.slow}, corrected: {st.corrected}, "
              f"errors: {st.errors}")


if __name__ == "__main__":
    main()
