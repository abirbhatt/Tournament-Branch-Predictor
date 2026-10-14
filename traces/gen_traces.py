#!/usr/bin/env python3
"""
Synthetic workload trace generator for the branch predictor lab.

Each workload is a small program written in Python. Every conditional
branch in the "program" goes through br(pc, cond), which records
(pc, taken) and returns cond so the program keeps running normally.
Each branch site has a fixed, word-aligned PC, just like a real binary.

Output format (one branch per line, $readmemh-compatible, 9 hex digits):
    {3'b000, taken, pc[31:0]}   e.g.  1004000a8  -> taken=1, pc=0x004000a8

Usage:
    python3 traces/gen_traces.py            # writes traces/*.hex
    python3 traces/gen_traces.py --small    # ~10x shorter traces for quick debug
"""
import argparse
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))


class Tracer:
    def __init__(self, limit):
        self.recs = []
        self.limit = limit

    def br(self, pc, cond):
        cond = bool(cond)
        self.recs.append((pc, 1 if cond else 0))
        return cond

    def full(self):
        return len(self.recs) >= self.limit


def site(base, k):
    """PC of branch site k inside a code block starting at base."""
    return base + 4 * k


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------

def w_sort(t, rng):
    """Insertion sort on random arrays: data-dependent compares + loop exits."""
    B = 0x00400100
    while not t.full():
        n = rng.randint(8, 48)
        a = [rng.randint(0, 999) for _ in range(n)]
        i = 1
        while t.br(site(B, 0), i < n):
            key = a[i]
            j = i - 1
            while t.br(site(B, 1), j >= 0) and t.br(site(B, 2), a[j] > key):
                a[j + 1] = a[j]
                j -= 1
            a[j + 1] = key
            i += 1


def w_bsearch(t, rng):
    """Binary search over a sorted table: hard, data-dependent direction branches."""
    B = 0x00410200
    tbl = sorted(rng.sample(range(100000), 4096))
    while not t.full():
        key = rng.choice(tbl) if rng.random() < 0.7 else rng.randint(0, 99999)
        lo, hi = 0, len(tbl) - 1
        found = False
        while t.br(site(B, 0), lo <= hi):
            mid = (lo + hi) // 2
            if t.br(site(B, 1), tbl[mid] == key):
                found = True
                break
            if t.br(site(B, 2), tbl[mid] < key):
                lo = mid + 1
            else:
                hi = mid - 1
        t.br(site(B, 3), found)


def w_matmul(t, rng):
    """Blocked matrix multiply: nested loops with fixed trip counts (very predictable)."""
    B = 0x00420040
    while not t.full():
        n = rng.choice([4, 8, 12])
        i = 0
        while t.br(site(B, 0), i < n):
            j = 0
            while t.br(site(B, 1), j < n):
                k = 0
                while t.br(site(B, 2), k < n):
                    k += 1
                t.br(site(B, 3), (i == j))  # diagonal special case
                j += 1
            i += 1


def w_interp(t, rng):
    """Tiny bytecode interpreter: dispatch as an if-chain, so outcomes correlate
    strongly with recent history (the opcode sequence of the program loop)."""
    B = 0x00430300
    # A fixed "program": a loop body of opcodes that repeats with a counter.
    prog = [0, 1, 2, 1, 3, 0, 2, 4, 1, 5]
    while not t.full():
        iters = rng.randint(20, 60)
        pc_v = 0
        cnt = 0
        while t.br(site(B, 0), cnt < iters):
            op = prog[pc_v]
            if t.br(site(B, 1), op == 0):
                pass
            elif t.br(site(B, 2), op == 1):
                pass
            elif t.br(site(B, 3), op == 2):
                pass
            elif t.br(site(B, 4), op == 3):
                pass
            elif t.br(site(B, 5), op == 4):
                pass
            else:
                pass
            pc_v += 1
            if t.br(site(B, 6), pc_v == len(prog)):
                pc_v = 0
                cnt += 1


def w_hash(t, rng):
    """Chained hash table lookups: chain-length loop + key compare + rare resize check."""
    B = 0x00440400
    nb = 256
    buckets = [[] for _ in range(nb)]
    keys = []
    while not t.full():
        if t.br(site(B, 0), rng.random() < 0.3 or not keys):
            k = rng.randint(0, 1 << 20)
            buckets[k % nb].append(k)
            keys.append(k)
            t.br(site(B, 1), len(keys) % 1024 == 0)  # "resize" check
        else:
            k = rng.choice(keys) if rng.random() < 0.8 else rng.randint(0, 1 << 20)
            chain = buckets[k % nb]
            idx = 0
            while t.br(site(B, 2), idx < len(chain)):
                if t.br(site(B, 3), chain[idx] == k):
                    break
                idx += 1


def w_strsearch(t, rng):
    """Naive substring search over text from a small alphabet."""
    B = 0x00450500
    alpha = "abcd"
    while not t.full():
        text = "".join(rng.choice(alpha) for _ in range(200))
        pat = "".join(rng.choice(alpha) for _ in range(rng.randint(2, 5)))
        i = 0
        while t.br(site(B, 0), i <= len(text) - len(pat)):
            j = 0
            while t.br(site(B, 1), j < len(pat)) and t.br(site(B, 2), text[i + j] == pat[j]):
                j += 1
            t.br(site(B, 3), j == len(pat))
            i += 1


def w_cfg_large(t, rng):
    """Large synthetic control-flow graph: 512 static branches with mixed
    behaviors (biased, loop-like, globally correlated, random). Stresses table
    capacity and aliasing the way big server/browser code does."""
    B = 0x00500000
    NS = 512
    kinds = []
    for s in range(NS):
        r = rng.random()
        if r < 0.40:
            kinds.append(("bias", rng.choice([0.03, 0.97, 0.9, 0.1])))
        elif r < 0.60:
            kinds.append(("loop", rng.randint(3, 12)))
        elif r < 0.96:
            kinds.append(("corr", rng.randint(2, 4)))  # outcome = XOR of recent outcomes
        else:
            kinds.append(("rand", 0.5))
    loopc = [0] * NS
    hist = []
    s = 0
    while not t.full():
        kind, p = kinds[s]
        if kind == "bias":
            c = rng.random() < p
        elif kind == "loop":
            loopc[s] += 1
            c = loopc[s] % p != 0
        elif kind == "corr":
            c = (hist[-p] if len(hist) >= p else 0) ^ (hist[-1] if hist else 0)
            if rng.random() < 0.02:
                c = not c
        else:
            c = rng.random() < 0.5
        c = t.br(site(B, s), c)
        hist.append(1 if c else 0)
        if len(hist) > 16:
            hist.pop(0)
        # walk the "CFG": mostly fall through to a nearby block, sometimes jump far
        if c:
            s = (s + 1 + (s % 7)) % NS
        else:
            s = (s + 1) % NS if rng.random() < 0.9 else rng.randrange(NS)


WORKLOADS = {
    "sort": w_sort,
    "bsearch": w_bsearch,
    "matmul": w_matmul,
    "interp": w_interp,
    "hash": w_hash,
    "strsearch": w_strsearch,
    "cfg_large": w_cfg_large,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true", help="short traces for quick debug")
    ap.add_argument("--n", type=int, default=None, help="branches per trace")
    args = ap.parse_args()
    n = args.n or (10000 if args.small else 100000)
    for name, fn in WORKLOADS.items():
        t = Tracer(n)
        fn(t, random.Random(sum(map(ord, name))))  # fixed seed: traces are reproducible
        recs = t.recs[:n]
        path = os.path.join(HERE, f"{name}.hex")
        with open(path, "w") as f:
            for pc, tk in recs:
                f.write(f"{tk:01x}{pc:08x}\n")
        taken = sum(tk for _, tk in recs)
        sites = len({pc for pc, _ in recs})
        print(f"{name:10s} {len(recs):7d} branches  {sites:5d} static sites  {100*taken/len(recs):5.1f}% taken")


if __name__ == "__main__":
    main()
