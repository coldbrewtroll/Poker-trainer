"""Plays one bot (the hero) against a field of opponents at an 8-max table.

    python match8.py contender8 "starter8*7" --hands 3000
    python match8.py contender8 "baselines.Maniac*2,baselines.CallingStation*2,starter8*3"

Each deal is replayed once with the hero in every seat (the opponents keep their order),
so the hero gets every position and every set of cards: luck and position mostly cancel.
--hands counts deals; each is played as many times as there are seats.

Online tournament defaults: every player antes 1/8 of a big blind (12.5%), no rake. Chips
are counted with an 8-chip big blind (small blind 4, ante 1) so that ante is exact.
--depth sets everyone's stack in big blinds; --stacks 10-100 instead gives every seat a
random stack each deal (uneven, like a tournament). Fewer opponents = a shorter table
(e.g. 5 opponents for 6-handed, 1 for heads-up).
"""

import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import new_deck  # noqa: E402
from engine8 import Game8  # noqa: E402


def parse_field(spec):
    """'a*2,b' -> ['a', 'a', 'b']"""
    out = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        name, _, times = part.partition("*")
        out += [name] * int(times or 1)
    return out


def make(spec, seed):
    from tournament import make as make_bot
    return make_bot(spec, seed)


BIG_BLIND = 8   # chips; small blind 4, ante 1 = 12.5% of the big blind
ANTE = 1


def run8(hero, field, deals, seed=0, stack=100 * BIG_BLIND, big_blind=BIG_BLIND, ante=ANTE,
         ante_mode="each", stack_range=None):
    """Returns (hero's chip result per deal summed over all seatings, hero Stats).
    ante: chips each player posts (ante_mode "each", the online tournament format) or
    that the big blind posts (ante_mode "bb"); 0 = no ante.
    stack_range: (lo, hi) in big blinds to give every seat a random stack each deal
    (tournament-style uneven stacks); the same stacks are used for every seating."""
    n = len(field) + 1
    bots = [hero] + list(field)
    game = Game8(bots, stack=stack, big_blind=big_blind, ante=ante, ante_mode=ante_mode)
    rng = random.Random(seed)
    per_deal = []
    hand_no = 0
    for d in range(deals):
        deck = new_deck(rng)
        button = d % n
        stacks = None
        if stack_range:
            lo, hi = stack_range
            stacks = [int(rng.uniform(lo, hi) * big_blind) for _ in range(n)]
        total = 0.0
        for hero_seat in range(n):
            seating = list(field)
            seating.insert(hero_seat, hero)
            pids = [bots.index(b) for b in seating]
            game.bots = seating
            r = game.play_hand(deck, button=button, hand_no=hand_no, pids=pids, stacks=stacks)
            hand_no += 1
            total += r[hero_seat]
        per_deal.append(total)
    return per_deal, game.stats_for(hero)


def summarise(per_deal, n_seats, big_blind=BIG_BLIND):
    n = len(per_deal)
    mean = sum(per_deal) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in per_deal) / max(n - 1, 1))
    scale = 100 / n_seats / big_blind  # per hand, in big blinds, per 100 hands
    return mean * scale, 1.96 * sd / math.sqrt(n) * scale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hero")
    ap.add_argument("field", help="comma-separated bot specs, name*count allowed")
    ap.add_argument("--hands", type=int, default=2000, help="deals (each played per seat)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--depth", type=float, default=100, help="everyone's stack in big blinds")
    ap.add_argument("--stacks", help="random stack per seat each deal, in bb, e.g. 10-100")
    ap.add_argument("--ante", type=float, default=0.125,
                    help="ante per player in big blinds (default 0.125; 0 = none)")
    args = ap.parse_args()

    names = parse_field(args.field)
    if not 1 <= len(names) <= 7:
        sys.exit("the field needs 1-7 opponents")
    hero = make(args.hero, args.seed + 1)
    field = [make(nm, args.seed + 10 + k) for k, nm in enumerate(names)]
    rng_spec = tuple(float(x) for x in args.stacks.split("-")) if args.stacks else None
    per_deal, st = run8(hero, field, args.hands, args.seed, int(args.depth * BIG_BLIND),
                        ante=int(round(args.ante * BIG_BLIND)), stack_range=rng_spec)
    bb100, ci = summarise(per_deal, len(names) + 1)
    depth = f"{args.stacks} bb stacks" if args.stacks else f"{args.depth:g} bb"
    print(f"{args.hero} vs {args.field} ({len(names) + 1}-handed, {depth}, ante "
          f"{args.ante:g} bb each, {args.hands} deals x {len(names) + 1} seats)")
    print(f"  {args.hero} wins {bb100:+.1f} bb/100  (95% range {bb100 - ci:+.1f} to "
          f"{bb100 + ci:+.1f})")
    avg = st.total_s / max(st.decisions, 1) * 1e6
    print(f"  {args.hero}: {st.decisions} decisions, avg {avg:.0f} us, max "
          f"{st.max_s * 1e6:.0f} us, over 1 ms: {st.slow}, corrected: {st.corrected}, "
          f"errors: {st.errors}")


if __name__ == "__main__":
    main()
