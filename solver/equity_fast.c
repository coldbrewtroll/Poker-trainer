/* Preflop all-in win / tie counts for every pair of the 169 hand classes, by Monte Carlo
 * (stratified over the compatible combo pairs). Built and driven by solver/equity_ties.py.
 *
 *   equity_fast run SAMPLES SEED I0 I1 < classes.txt   classes i in [I0, I1) against j >= i
 *   equity_fast check < pairs.txt                      compare 7-card hands (evaluator test)
 *
 * Cards are rank * 4 + suit, rank 0 = deuce ... 12 = ace (the same as cards.py). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t s0, s1;
static inline uint64_t rnd(void) {          /* xorshift128+ */
    uint64_t x = s0, y = s1;
    s0 = y;
    x ^= x << 23;
    s1 = x ^ y ^ (x >> 17) ^ (y >> 26);
    return s1 + y;
}
static inline uint32_t below(uint32_t n) { return (uint32_t)(((rnd() >> 32) * (uint64_t)n) >> 32); }

static inline int top_bit(uint32_t m) { return 31 - __builtin_clz(m); }

static inline int straight_high(uint32_t m) {  /* 13-bit rank mask -> high rank + 1, or 0 */
    uint32_t w = (m << 1) | ((m >> 12) & 1);   /* bit 0: the ace as a one */
    for (int t = 13; t >= 4; t--)
        if (((w >> (t - 4)) & 0x1F) == 0x1F) return t;
    return 0;
}

static inline uint32_t kick(uint32_t m, int k) {  /* the top k ranks of mask m, packed */
    uint32_t out = 0;
    for (int i = 0; i < k; i++) {
        int r = m ? top_bit(m) : 0;
        out = (out << 4) | (uint32_t)r;
        if (m) m &= ~(1u << r);
    }
    return out;
}

static uint32_t eval7(const int *c) {
    uint32_t suit[4] = {0, 0, 0, 0};
    int cnt[13];
    memset(cnt, 0, sizeof cnt);
    uint32_t all = 0;
    for (int i = 0; i < 7; i++) {
        int r = c[i] >> 2, s = c[i] & 3;
        suit[s] |= 1u << r;
        cnt[r]++;
        all |= 1u << r;
    }
    for (int s = 0; s < 4; s++) {
        if (__builtin_popcount(suit[s]) >= 5) {
            int sh = straight_high(suit[s]);
            if (sh) return (8u << 20) | (uint32_t)sh;
            return (5u << 20) | kick(suit[s], 5);
        }
    }
    uint32_t quads = 0, trips = 0, pairs = 0;
    for (int r = 0; r < 13; r++) {
        if (cnt[r] == 4) quads |= 1u << r;
        else if (cnt[r] == 3) trips |= 1u << r;
        else if (cnt[r] == 2) pairs |= 1u << r;
    }
    if (quads) {
        int q = top_bit(quads);
        return (7u << 20) | ((uint32_t)q << 4) | kick(all & ~(1u << q), 1);
    }
    if (trips) {
        int t = top_bit(trips);
        uint32_t rest = (trips & ~(1u << t)) | pairs;
        if (rest) return (6u << 20) | ((uint32_t)t << 4) | (uint32_t)top_bit(rest);
    }
    int sh = straight_high(all);
    if (sh) return (4u << 20) | (uint32_t)sh;
    if (trips) {
        int t = top_bit(trips);
        return (3u << 20) | ((uint32_t)t << 8) | kick(all & ~(1u << t), 2);
    }
    if (pairs) {
        int p1 = top_bit(pairs);
        uint32_t rest = pairs & ~(1u << p1);
        if (rest) {
            int p2 = top_bit(rest);
            return (2u << 20) | ((uint32_t)p1 << 8) | ((uint32_t)p2 << 4) |
                   kick(all & ~(1u << p1) & ~(1u << p2), 1);
        }
        return (1u << 20) | ((uint32_t)p1 << 12) | kick(all & ~(1u << p1), 3);
    }
    return kick(all, 5);
}

#define NC 169
static int ncombo[NC], combo[NC][16][2];

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "check")) {
        int a[7], b[7];
        while (1) {
            int ok = 1;
            for (int i = 0; i < 7 && ok; i++) ok = scanf("%d", &a[i]) == 1;
            for (int i = 0; i < 7 && ok; i++) ok = scanf("%d", &b[i]) == 1;
            if (!ok) break;
            uint32_t x = eval7(a), y = eval7(b);
            printf("%d\n", x > y ? 1 : x < y ? -1 : 0);
        }
        return 0;
    }
    if (argc < 6) {
        fprintf(stderr, "usage: equity_fast run SAMPLES SEED I0 I1 < classes.txt\n");
        return 1;
    }
    long samples = atol(argv[2]);
    uint64_t seed = strtoull(argv[3], 0, 10);
    int i0 = atoi(argv[4]), i1 = atoi(argv[5]);
    for (int h = 0; h < NC; h++) {
        if (scanf("%d", &ncombo[h]) != 1) return 2;
        for (int k = 0; k < ncombo[h]; k++)
            if (scanf("%d %d", &combo[h][k][0], &combo[h][k][1]) != 2) return 2;
    }
    s0 = seed * 0x9E3779B97F4A7C15ull + 1;
    s1 = (seed + 7) * 0xBF58476D1CE4E5B9ull + 3;
    for (int i = 0; i < 20; i++) rnd();
    int pa[256][4], np;
    for (int i = i0; i < i1; i++) {
        for (int j = i; j < NC; j++) {
            np = 0;
            for (int x = 0; x < ncombo[i]; x++)
                for (int y = 0; y < ncombo[j]; y++) {
                    int a = combo[i][x][0], b = combo[i][x][1], c = combo[j][y][0], d = combo[j][y][1];
                    if (a == c || a == d || b == c || b == d) continue;
                    pa[np][0] = a; pa[np][1] = b; pa[np][2] = c; pa[np][3] = d;
                    np++;
                }
            long win = 0, tie = 0, n = 0;
            long per = (samples + np - 1) / np;
            for (int p = 0; p < np; p++) {
                int deck[48], nd = 0;
                for (int c = 0; c < 52; c++)
                    if (c != pa[p][0] && c != pa[p][1] && c != pa[p][2] && c != pa[p][3]) deck[nd++] = c;
                int h1[7], h2[7];
                h1[0] = pa[p][0]; h1[1] = pa[p][1];
                h2[0] = pa[p][2]; h2[1] = pa[p][3];
                for (long t = 0; t < per; t++) {
                    for (int k = 0; k < 5; k++) {
                        int r = k + (int)below((uint32_t)(48 - k));
                        int tmp = deck[k]; deck[k] = deck[r]; deck[r] = tmp;
                        h1[2 + k] = h2[2 + k] = deck[k];
                    }
                    uint32_t x = eval7(h1), y = eval7(h2);
                    if (x > y) win++;
                    else if (x == y) tie++;
                    n++;
                }
            }
            printf("%d %d %ld %ld %ld %d\n", i, j, win, tie, n, np);
        }
        fflush(stdout);
    }
    return 0;
}
