"""A representative set of flops for solving: every board shape the trainer's matcher needs
(pairing and suit pattern by rank position), and within each shape rank combinations picked
by weighted k-medoids (each real flop counts once). python flopset.py N > flops.json"""
import itertools
import json
import random
import sys
from collections import defaultdict

RANKS = "23456789TJQKA"
SUITS = "shdc"


def shape(cards):
    """(pairing, suit pattern) of a flop, cards sorted high to low by rank."""
    cs = sorted(cards, key=lambda c: -c[0])
    ranks = [c[0] for c in cs]
    suits = [c[1] for c in cs]
    if ranks[0] == ranks[2]:
        pairing = "trips"
    elif ranks[0] == ranks[1]:
        pairing = "pair-high"
    elif ranks[1] == ranks[2]:
        pairing = "pair-low"
    else:
        pairing = "none"
    first = {}
    pattern = tuple(first.setdefault(s, len(first)) for s in suits)
    if pairing != "none":   # (the paired cards' order doesn't matter)
        pattern = "two-tone" if len(set(suits)) == 2 else "rainbow"
    return pairing, pattern, tuple(ranks)


def main(n_total):
    groups = defaultdict(lambda: defaultdict(int))      # shape -> ranks -> count
    deck = [(r, s) for r in range(13) for s in range(4)]
    for combo in itertools.combinations(deck, 3):
        p, pat, ranks = shape(combo)
        groups[(p, pat)][ranks] += 1
    total = sum(sum(g.values()) for g in groups.values())
    out = []
    rng = random.Random(1)
    for key, g in sorted(groups.items(), key=lambda kv: -sum(kv[1].values())):
        share = sum(g.values()) / total
        k = max(1, round(n_total * share))
        pts = list(g)
        w = [g[p] for p in pts]

        def dist(a, b):
            return 3 * abs(a[0] - b[0]) + 2 * abs(a[1] - b[1]) + abs(a[2] - b[2])
        # weighted k-medoids
        meds = [max(pts, key=lambda p: g[p])]
        while len(meds) < min(k, len(pts)):
            meds.append(max(pts, key=lambda p: g[p] * min(dist(p, m) for m in meds)))
        for _ in range(20):
            clusters = defaultdict(list)
            for p in pts:
                clusters[min(range(len(meds)), key=lambda i: dist(p, meds[i]))].append(p)
            new = []
            for i in range(len(meds)):
                cl = clusters.get(i) or [meds[i]]
                new.append(min(cl, key=lambda c: sum(g[q] * dist(c, q) for q in cl)))
            if new == meds:
                break
            meds = new
        clusters = defaultdict(list)
        for p in pts:
            clusters[min(range(len(meds)), key=lambda i: dist(p, meds[i]))].append(p)
        for i, m in enumerate(meds):
            weight = sum(g[q] for q in clusters[i]) / total
            # real cards for the shape's suit pattern
            pairing, pat = key
            if pairing == "none":
                suits = [SUITS[x] for x in pat]
            elif pairing == "trips":
                suits = ["s", "h", "d"]
            elif pat == "rainbow":
                suits = ["s", "h", "d"]
            else:   # two-tone paired: the odd card shares a suit with one of the pair
                suits = ["s", "h", "s"] if pairing == "pair-high" else ["s", "s", "h"]
            board = "".join(RANKS[r] + s for r, s in zip(m, suits))
            out.append({"board": board, "shape": f"{pairing}/{pat}", "weight": round(weight, 6)})
    out.sort(key=lambda x: -x["weight"])
    json.dump(out, sys.stdout, indent=0)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 80)
