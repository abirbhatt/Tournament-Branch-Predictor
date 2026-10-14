#!/usr/bin/env python3
"""
Golden reference model for the branch predictor lab.

This is the lab spec in executable form. Your RTL must produce the exact
same prediction on every predict event (including re-predictions after a
pipeline flush). run.py compares the two streams line by line.

It also models the testbench's pipeline exactly:
  * Each cycle, the oldest in-flight branch resolves (update) once it has
    been in flight for LAT cycles.
  * If that branch mispredicted, every younger in-flight branch is squashed
    and fetch rewinds to the branch right after it (no predict that cycle).
  * Otherwise, a new branch is predicted if fewer than DEPTH branches were
    in flight at the start of the cycle.
  * Predict reads state BEFORE the clock edge; the update writes AT the edge.
"""
import argparse
import sys


def read_trace(path, limit=None):
    recs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            v = int(line, 16)
            recs.append(((v >> 0) & 0xFFFFFFFF, (v >> 32) & 1))
            if limit and len(recs) >= limit:
                break
    return recs


def sat(c, taken):
    return min(c + 1, 3) if taken else max(c - 1, 0)


def idx(pc, n):
    return (pc >> 2) & ((1 << n) - 1)


class Bimodal:
    def __init__(self, bim_bits, **_):
        self.B = bim_bits
        self.bim = [1] * (1 << bim_bits)

    def storage_bits(self):
        return 2 * len(self.bim)

    def predict(self, pc):
        return self.bim[idx(pc, self.B)] >> 1, None

    def update(self, pc, taken, mispred, meta):
        i = idx(pc, self.B)
        self.bim[i] = sat(self.bim[i], taken)

    def debug(self, pc):
        i = idx(pc, self.B)
        return f"bim_idx={i} bim_ctr={self.bim[i]}"


class Gshare:
    def __init__(self, gsh_bits, **_):
        self.G = gsh_bits
        self.mask = (1 << gsh_bits) - 1
        self.gsh = [1] * (1 << gsh_bits)
        self.ghr = 0  # non-speculative: updated at resolve time

    def storage_bits(self):
        return 2 * len(self.gsh) + self.G

    def predict(self, pc):
        gi = idx(pc, self.G) ^ self.ghr
        return self.gsh[gi] >> 1, gi

    def update(self, pc, taken, mispred, meta):
        gi = meta  # index used at predict time
        self.gsh[gi] = sat(self.gsh[gi], taken)
        self.ghr = ((self.ghr << 1) | taken) & self.mask

    def debug(self, pc):
        gi = idx(pc, self.G) ^ self.ghr
        return f"ghr={self.ghr:x} gsh_idx={gi} gsh_ctr={self.gsh[gi]}"


class Tournament:
    def __init__(self, bim_bits, gsh_bits, cho_bits, spec_hist=0, **_):
        self.B, self.G, self.C = bim_bits, gsh_bits, cho_bits
        self.mask = (1 << gsh_bits) - 1
        self.bim = [1] * (1 << bim_bits)
        self.gsh = [1] * (1 << gsh_bits)
        self.cho = [1] * (1 << cho_bits)
        self.ghr = 0
        self.spec = spec_hist

    def storage_bits(self):
        return 2 * (len(self.bim) + len(self.gsh) + len(self.cho)) + self.G

    def predict(self, pc):
        ghr = self.ghr
        pb = self.bim[idx(pc, self.B)] >> 1
        pg = self.gsh[idx(pc, self.G) ^ ghr] >> 1
        use_g = self.cho[idx(pc, self.C)] >> 1
        p = pg if use_g else pb
        return p, (ghr, pb, pg, p)

    def debug(self, pc):
        bi, ci = idx(pc, self.B), idx(pc, self.C)
        gi = idx(pc, self.G) ^ self.ghr
        return (f"ghr={self.ghr:x} bim_idx={bi} bim_ctr={self.bim[bi]} gsh_idx={gi} "
                f"gsh_ctr={self.gsh[gi]} cho_idx={ci} cho_ctr={self.cho[ci]}")

    def commit_predict(self, meta):
        """Called at the clock edge after a predict (speculative history only)."""
        if self.spec:
            ghr, _, _, p = meta
            self.ghr = ((self.ghr << 1) | p) & self.mask

    def update(self, pc, taken, mispred, meta):
        ghr, pb, pg, p = meta
        i = idx(pc, self.B)
        self.bim[i] = sat(self.bim[i], taken)
        gi = idx(pc, self.G) ^ ghr
        self.gsh[gi] = sat(self.gsh[gi], taken)
        if pb != pg:
            ci = idx(pc, self.C)
            self.cho[ci] = sat(self.cho[ci], pg == taken)
        if self.spec:
            if mispred:
                self.ghr = ((ghr << 1) | taken) & self.mask  # repair from checkpoint
        else:
            self.ghr = ((self.ghr << 1) | taken) & self.mask


def make(kind, **kw):
    return {"bimodal": Bimodal, "gshare": Gshare, "tournament": Tournament}[kind](**kw)


def simulate(pred, trace, lat=1, depth=1, log=None):
    """Cycle-level pipeline model matching tb/tb_bp.v. Returns stats dict."""
    n = len(trace)
    q = []  # in-flight: (trace_idx, pred, meta, predict_cycle)
    nxt = 0
    cyc = 0
    correct = 0
    resolved = 0
    npred = 0
    flushes = 0
    commit = getattr(pred, "commit_predict", None)
    while nxt < n or q:
        occ = len(q)
        upd = None
        if q and q[0][3] + lat <= cyc:
            upd = q.pop(0)
        flush = False
        if upd is not None:
            ti, p, meta, _ = upd
            pc, taken = trace[ti]
            flush = (p != taken)
        do_pred = (not flush) and nxt < n and occ < depth
        # --- combinational predict (reads pre-edge state) ---
        if do_pred:
            pc, _ = trace[nxt]
            p, pmeta = pred.predict(pc)
            if log is not None:
                dbg = pred.debug(pc) if hasattr(pred, "debug") else ""
                log.append(f"P {nxt} {p} | cyc={cyc} pc={pc:08x} {dbg}")
            npred += 1
        # --- clock edge: apply update, then speculative history shift ---
        if upd is not None:
            ti, up, umeta, _ = upd
            pc, taken = trace[ti]
            pred.update(pc, taken, up != taken, umeta)
            resolved += 1
            correct += (up == taken)
            if flush:
                flushes += 1
                q.clear()
                nxt = ti + 1
        if do_pred:
            if commit:
                commit(pmeta)
            q.append((nxt, p, pmeta, cyc))
            nxt += 1
        cyc += 1
    return dict(branches=resolved, correct=correct, mispredicts=resolved - correct,
                predicts=npred, flushes=flushes, cycles=cyc,
                accuracy=100.0 * correct / max(resolved, 1),
                storage_bits=pred.storage_bits())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--pred", default="tournament", choices=["bimodal", "gshare", "tournament"])
    ap.add_argument("--bim-bits", type=int, default=10)
    ap.add_argument("--gsh-bits", type=int, default=10)
    ap.add_argument("--cho-bits", type=int, default=10)
    ap.add_argument("--spec-hist", type=int, default=0)
    ap.add_argument("--lat", type=int, default=1)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--log", default=None, help="write predict log here")
    a = ap.parse_args()
    trace = read_trace(a.trace, a.limit)
    pred = make(a.pred, bim_bits=a.bim_bits, gsh_bits=a.gsh_bits, cho_bits=a.cho_bits,
                spec_hist=a.spec_hist)
    log = [] if a.log else None
    st = simulate(pred, trace, a.lat, a.depth, log)
    if a.log:
        with open(a.log, "w") as f:
            f.write("\n".join(log) + "\n")
    print(" ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in st.items()))


if __name__ == "__main__":
    sys.exit(main())
