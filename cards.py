"""Cards and a fast 7-card hand evaluator (no lookup tables, ~10-20 microseconds per hand).

Cards are ints 0..51: rank * 4 + suit, rank 0 = deuce ... 12 = ace, suit 0..3 = c d h s.
evaluate() returns an int; higher is a better hand. category(score) gives 0..8:
high card, pair, two pair, trips, straight, flush, full house, quads, straight flush.
"""

RANKS = "23456789TJQKA"
SUITS = "cdhs"
CATEGORIES = ("high card", "pair", "two pair", "trips", "straight", "flush", "full house",
              "quads", "straight flush")


def card(s: str) -> int:
    return RANKS.index(s[0]) * 4 + SUITS.index(s[1])


def name(c: int) -> str:
    return RANKS[c >> 2] + SUITS[c & 3]


def straight_high(mask: int) -> int:
    """Rank of the top card of the best straight in a 13-bit rank mask, or -1."""
    m = (mask << 1) | (mask >> 12 & 1)  # bit 0 = ace played low
    for top in range(13, 3, -1):
        if (m >> (top - 4)) & 0b11111 == 0b11111:
            return top - 1
    return -1


def _top(mask: int, n: int) -> int:
    """The n highest ranks in mask, packed 4 bits each, highest first."""
    out = 0
    for r in range(12, -1, -1):
        if mask >> r & 1:
            out = out << 4 | r
            n -= 1
            if n == 0:
                break
    return out


# Lookup tables over 13-bit rank masks, built once at import (~10 ms).
_STRAIGHT = [straight_high(m) for m in range(1 << 13)]
_BITS = [bin(m).count("1") for m in range(1 << 13)]
_TOP1 = [_top(m, 1) for m in range(1 << 13)]
_TOP2 = [_top(m, 2) for m in range(1 << 13)]
_TOP3 = [_top(m, 3) for m in range(1 << 13)]
_TOP5 = [_top(m, 5) for m in range(1 << 13)]


def evaluate(cards) -> int:
    counts = [0] * 13
    suits = [0, 0, 0, 0]
    mask = 0
    for c in cards:
        r = c >> 2
        counts[r] += 1
        suits[c & 3] |= 1 << r
        mask |= 1 << r

    flush = 0
    for sm in suits:
        if _BITS[sm] >= 5:
            sh = _STRAIGHT[sm]
            if sh >= 0:
                return 8 << 20 | sh
            flush = sm
            break

    if _BITS[mask] == len(cards):  # no pairs at all: the most common case
        if flush:
            return 5 << 20 | _TOP5[flush]
        sh = _STRAIGHT[mask]
        return 4 << 20 | sh if sh >= 0 else _TOP5[mask]

    quads = trips = trips2 = -1
    pairs = []
    for r in range(12, -1, -1):
        n = counts[r]
        if n == 4:
            quads = r
        elif n == 3:
            if trips < 0:
                trips = r
            elif trips2 < 0:
                trips2 = r
        elif n == 2:
            pairs.append(r)

    if quads >= 0:
        return 7 << 20 | quads << 4 | _TOP1[mask & ~(1 << quads)]
    if trips >= 0 and (trips2 >= 0 or pairs):
        return 6 << 20 | trips << 4 | max(trips2, pairs[0] if pairs else -1)
    if flush:
        return 5 << 20 | _TOP5[flush]
    sh = _STRAIGHT[mask]
    if sh >= 0:
        return 4 << 20 | sh
    if trips >= 0:
        return 3 << 20 | trips << 8 | _TOP2[mask & ~(1 << trips)]
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        return 2 << 20 | p1 << 8 | p2 << 4 | _TOP1[mask & ~(1 << p1) & ~(1 << p2)]
    p = pairs[0]
    return 1 << 20 | p << 12 | _TOP3[mask & ~(1 << p)]


def category(score: int) -> int:
    return score >> 20
