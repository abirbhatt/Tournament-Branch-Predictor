#!/usr/bin/env python3
"""
run.py -- compile your RTL, run it on traces, and check it against the golden model.

Examples
  python3 run.py --pred bimodal --trace sort                 # one trace
  python3 run.py --pred gshare --gsh-bits 12 --trace all     # every trace
  python3 run.py --pred tournament --trace interp --limit 2000   # quick debug
  python3 run.py --pred tournament --spec-hist 1 --lat 8 --depth 32 --trace all

On a mismatch it prints the first diverging prediction with the golden
model's internal state (indices, counters, GHR) so you know where to look.

Simulators: Icarus Verilog (default, free) or QuestaSim (--sim questa).
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "model"))
import golden  # noqa: E402

TRACES = ["sort", "bsearch", "matmul", "interp", "hash", "strsearch", "cfg_large"]
STAT_RE = re.compile(r"(\w+)=([\d.]+)")


def defines(cfg):
    d = {"BP_" + cfg["pred"].upper(): None}
    for k in ("bim_bits", "gsh_bits", "cho_bits", "spec_hist"):
        d[k.upper()] = cfg[k]
    return d


def cfg_tag(cfg):
    return (f"{cfg['pred']}_b{cfg['bim_bits']}_g{cfg['gsh_bits']}_c{cfg['cho_bits']}"
            f"_s{cfg['spec_hist']}_{cfg['sim']}")


def compile_rtl(cfg, rtl_dir, build):
    tag = cfg_tag(cfg)
    srcs = sorted(glob.glob(os.path.join(rtl_dir, "*.v")))
    tb = os.path.join(ROOT, "tb", "tb_bp.v")
    os.makedirs(build, exist_ok=True)
    defs = defines(cfg)
    if cfg["sim"] == "iverilog":
        out = os.path.join(build, f"sim_{tag}.vvp")
        cmd = ["iverilog", "-g2005", "-Wall", "-o", out]
        cmd += [f"-D{k}" if v is None else f"-D{k}={v}" for k, v in defs.items()]
        cmd += [tb] + srcs
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("COMPILE FAILED:\n" + r.stdout + r.stderr)
            sys.exit(1)
        warn = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                         if "timescale" not in l and l.strip())
        return out, warn
    else:  # questa
        lib = os.path.join(build, f"work_{tag}")
        if not os.path.isdir(lib):
            subprocess.run(["vlib", lib], check=True, capture_output=True)
        cmd = ["vlog", "-work", lib]
        cmd += [f"+define+{k}" if v is None else f"+define+{k}={v}" for k, v in defs.items()]
        cmd += [tb] + srcs
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("COMPILE FAILED:\n" + r.stdout + r.stderr)
            sys.exit(1)
        return lib, ""


def run_sim(cfg, exe, trace_path, n, lat, depth, log, vcd=None):
    plus = [f"+TRACE={trace_path}", f"+N={n}", f"+LAT={lat}", f"+DEPTH={depth}", f"+LOG={log}"]
    if vcd:
        plus.append(f"+VCD={vcd}")
    if cfg["sim"] == "iverilog":
        cmd = ["vvp", "-n", exe] + plus
    else:
        cmd = ["vsim", "-c", "-lib", exe, "tb_bp", "-do", "run -all; quit -f"] + plus
    r = subprocess.run(cmd, capture_output=True, text=True)
    stats = None
    for line in r.stdout.splitlines():
        if "ERROR" in line:
            print(line)
        if line.strip().lstrip("# ").startswith("branches="):
            stats = {k: float(v) for k, v in STAT_RE.findall(line)}
    if stats is None:
        print("Simulation did not finish. Output:\n" + r.stdout[-3000:] + r.stderr[-2000:])
    return stats


def compare_logs(rtl_log, gold_lines, trace, ctx=4):
    with open(rtl_log) as f:
        rtl = [l.strip() for l in f if l.strip()]
    for i, g in enumerate(gold_lines):
        gk = g.split("|")[0].strip()
        rk = rtl[i] if i < len(rtl) else "<missing>"
        if gk != rk:
            print(f"\n  MISMATCH at predict event #{i}:")
            print(f"    expected: {gk}")
            print(f"    got     : {rk}")
            print(f"    golden state at that predict: {g.split('|', 1)[1].strip()}")
            lo = max(0, i - ctx)
            print("    previous predicts (golden):")
            for j in range(lo, i):
                print(f"      {gold_lines[j]}")
            return False
    if len(rtl) > len(gold_lines):
        print(f"\n  MISMATCH: RTL made {len(rtl)} predicts, golden made {len(gold_lines)}")
        return False
    return True


def run_one(cfg, exe, trace_name, lat, depth, limit, build, quiet=False, vcd=False):
    tp = os.path.join(ROOT, "traces", f"{trace_name}.hex")
    trace = golden.read_trace(tp, limit)
    n = len(trace)
    log = os.path.join(build, f"rtl_{cfg_tag(cfg)}_{trace_name}_l{lat}_d{depth}.log")
    vcd_path = os.path.join(build, f"wave_{trace_name}.vcd") if vcd else None
    rs = run_sim(cfg, exe, tp, n, lat, depth, log, vcd_path)
    if vcd_path and not quiet:
        print(f"  waveform: {os.path.relpath(vcd_path, ROOT)}  (open with gtkwave)")
    pred = golden.make(cfg["pred"], **{k: cfg[k] for k in ("bim_bits", "gsh_bits", "cho_bits", "spec_hist")})
    glines = []
    gs = golden.simulate(pred, trace, lat, depth, glines)
    ok = rs is not None and compare_logs(log, glines, trace)
    if ok:
        for k in ("branches", "correct", "predicts", "flushes", "cycles"):
            if int(rs[k]) != gs[k]:
                print(f"  STATS MISMATCH {k}: rtl={int(rs[k])} golden={gs[k]}")
                ok = False
    try:
        os.remove(log)
    except OSError:
        pass
    res = dict(trace=trace_name, ok=ok, accuracy=gs["accuracy"], mispredicts=gs["mispredicts"],
               branches=gs["branches"], storage_bits=gs["storage_bits"])
    if not quiet:
        if ok:
            print(f"  [PASS] {trace_name:10s} accuracy={gs['accuracy']:7.3f}%  "
                  f"mispredicts={gs['mispredicts']:6d}/{gs['branches']}")
        else:
            print(f"  [FAIL] {trace_name:10s} (RTL does not match the golden model)")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", default="tournament", choices=["bimodal", "gshare", "tournament"])
    ap.add_argument("--trace", default="all", help="trace name or 'all'")
    ap.add_argument("--bim-bits", type=int, default=10)
    ap.add_argument("--gsh-bits", type=int, default=10)
    ap.add_argument("--cho-bits", type=int, default=10)
    ap.add_argument("--spec-hist", type=int, default=0)
    ap.add_argument("--lat", type=int, default=1, help="resolve latency in cycles")
    ap.add_argument("--depth", type=int, default=1, help="max in-flight branches")
    ap.add_argument("--limit", type=int, default=None, help="only first N branches of each trace")
    ap.add_argument("--sim", default="iverilog", choices=["iverilog", "questa"])
    ap.add_argument("--rtl-dir", default=os.path.join(ROOT, "rtl"))
    ap.add_argument("--vcd", action="store_true", help="dump waveforms to build/wave_<trace>.vcd (use --limit!)")
    a = ap.parse_args()
    if a.vcd and not a.limit:
        print("note: --vcd without --limit makes a huge file; adding --limit 500")
        a.limit = 500

    if a.sim == "iverilog" and not shutil.which("iverilog"):
        sys.exit("iverilog not found. Install Icarus Verilog (brew install icarus-verilog / apt install iverilog) or use --sim questa")

    cfg = dict(pred=a.pred, bim_bits=a.bim_bits, gsh_bits=a.gsh_bits, cho_bits=a.cho_bits,
               spec_hist=a.spec_hist, sim=a.sim)
    build = os.path.join(ROOT, "build")
    exe, warn = compile_rtl(cfg, a.rtl_dir, build)
    if warn:
        print("Compiler warnings (worth reading):\n" + warn)
    names = TRACES if a.trace == "all" else a.trace.split(",")
    print(f"{cfg_tag(cfg)}  lat={a.lat} depth={a.depth}")
    res = [run_one(cfg, exe, t, a.lat, a.depth, a.limit, build, vcd=a.vcd) for t in names]
    allok = all(r["ok"] for r in res)
    if len(res) > 1:
        avg = sum(r["accuracy"] for r in res) / len(res)
        if allok:
            print(f"  mean accuracy = {avg:.3f}%   storage = {res[0]['storage_bits']} bits")
    print("ALL PASS" if allok else "FAILED")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
