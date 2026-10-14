#!/usr/bin/env python3
"""
sweep.py -- design-space exploration on YOUR RTL (every run is checked
bit-exact against the golden model, so every number you report is backed
by a passing RTL simulation).

Experiments
  capacity : accuracy vs. storage budget for bimodal / gshare / tournament
             (serial pipeline, LAT=1 DEPTH=1)              -> Part 4
  latency  : tournament accuracy vs. branch-resolve latency with
             non-speculative vs. speculative global history  -> Part 5

Usage
  python3 sweep.py capacity
  python3 sweep.py latency --size 10
  python3 sweep.py all
  python3 sweep.py capacity --limit 20000    # faster, for a first look

Outputs go to results/: CSVs, PNG plots (if matplotlib is installed) and
summary_<experiment>.md with tables you can paste into a README.
"""
import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import run  # noqa: E402

RES = os.path.join(ROOT, "results")
BUILD = os.path.join(ROOT, "build")


def mkcfg(pred, b=10, g=10, c=10, s=0, sim="iverilog"):
    return dict(pred=pred, bim_bits=b, gsh_bits=g, cho_bits=c, spec_hist=s, sim=sim)


def _job(args):
    cfg, exe, trace, lat, depth, limit = args
    return run.run_one(cfg, exe, trace, lat, depth, limit, BUILD, quiet=True)


def execute(points, limit, jobs, rtl_dir):
    """points: list of (label_dict, cfg, lat, depth). Returns rows."""
    exes = {}
    for _, cfg, _, _ in points:
        tag = run.cfg_tag(cfg)
        if tag not in exes:
            exes[tag], _ = run.compile_rtl(cfg, rtl_dir, BUILD)
    work, keys = [], []
    for label, cfg, lat, depth in points:
        for t in run.TRACES:
            work.append((cfg, exes[run.cfg_tag(cfg)], t, lat, depth, limit))
            keys.append(label)
    print(f"running {len(work)} RTL simulations on {jobs} workers ...")
    rows, fails = [], 0
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for label, r in zip(keys, ex.map(_job, work)):
            if not r["ok"]:
                fails += 1
                print(f"  FAIL: {label} {r['trace']}  (run it with run.py to see the mismatch)")
            rows.append({**label, **r})
    if fails:
        print(f"\n{fails} runs FAILED the golden check -- fix the RTL before using these numbers.")
    return rows, fails


def mean_by(rows, keys):
    groups = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        groups.setdefault(k, []).append(r)
    out = []
    for k, rs in groups.items():
        d = dict(zip(keys, k))
        d["mean_accuracy"] = sum(r["accuracy"] for r in rs) / len(rs)
        d["storage_bits"] = rs[0]["storage_bits"]
        d["per_trace"] = {r["trace"]: r["accuracy"] for r in rs}
        out.append(d)
    return out


def write_csv(path, rows):
    cols = [k for k in rows[0].keys() if k != "ok"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def plot(path, series, xlabel, ylabel, title, logx=False, xticks=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not installed; skipping plot)")
        return
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
    for name, xs, ys in series:
        ax.plot(xs, ys, marker="o", label=name)
    if logx:
        ax.set_xscale("log", base=2)
    if xticks:
        ax.set_xticks(xticks[0])
        ax.set_xticklabels(xticks[1])
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {os.path.relpath(path, ROOT)}")


def capacity(a):
    sizes = [6, 8, 10, 12, 14]
    pts = []
    for n in sizes:
        # bimodal / gshare get one more index bit so budgets interleave on the plot
        pts.append(({"pred": "bimodal", "size": n + 1}, mkcfg("bimodal", b=n + 1, sim=a.sim), 1, 1))
        pts.append(({"pred": "gshare", "size": n + 1}, mkcfg("gshare", g=n + 1, sim=a.sim), 1, 1))
        pts.append(({"pred": "tournament", "size": n}, mkcfg("tournament", b=n, g=n, c=n, sim=a.sim), 1, 1))
    rows, fails = execute(pts, a.limit, a.jobs, a.rtl_dir)
    write_csv(os.path.join(RES, "capacity.csv"), rows)
    agg = mean_by(rows, ["pred", "size"])
    series = []
    for p in ["bimodal", "gshare", "tournament"]:
        pa = sorted([r for r in agg if r["pred"] == p], key=lambda r: r["storage_bits"])
        series.append((p, [r["storage_bits"] / 8 / 1024 for r in pa], [r["mean_accuracy"] for r in pa]))
    plot(os.path.join(RES, "capacity.png"), series, "storage budget (KiB)",
         "mean prediction accuracy (%)", "Accuracy vs. storage (7 workloads)", logx=True)
    md = ["## Capacity sweep (serial pipeline)", "",
          "| predictor | index bits | storage | mean acc | " + " | ".join(run.TRACES) + " |",
          "|---|---|---|---|" + "---|" * len(run.TRACES)]
    for r in sorted(agg, key=lambda r: (r["pred"], r["size"])):
        md.append(f"| {r['pred']} | {r['size']} | {r['storage_bits']/8/1024:.2f} KiB | "
                  f"{r['mean_accuracy']:.2f}% | " +
                  " | ".join(f"{r['per_trace'][t]:.1f}" for t in run.TRACES) + " |")
    return md, fails


def latency(a):
    lats = [1, 2, 4, 8, 16]
    n = a.size
    pts = []
    for s in [0, 1]:
        for L in lats:
            pts.append(({"spec_hist": s, "lat": L},
                        mkcfg("tournament", b=n, g=n, c=n, s=s, sim=a.sim), L, a.depth))
    rows, fails = execute(pts, a.limit, a.jobs, a.rtl_dir)
    write_csv(os.path.join(RES, "latency.csv"), rows)
    agg = mean_by(rows, ["spec_hist", "lat"])
    series = []
    for s, name in [(0, "non-speculative GHR"), (1, "speculative GHR + repair")]:
        pa = sorted([r for r in agg if r["spec_hist"] == s], key=lambda r: r["lat"])
        series.append((name, [r["lat"] for r in pa], [r["mean_accuracy"] for r in pa]))
    plot(os.path.join(RES, "latency.png"), series, "branch resolve latency (cycles)",
         "mean prediction accuracy (%)", f"Tournament ({n}-bit tables), depth {a.depth}",
         logx=True, xticks=(lats, [str(x) for x in lats]))
    md = [f"## Resolve-latency sweep (tournament, {n}-bit tables, depth {a.depth})", "",
          "| GHR | latency | mean acc | " + " | ".join(run.TRACES) + " |",
          "|---|---|---|" + "---|" * len(run.TRACES)]
    for r in sorted(agg, key=lambda r: (r["spec_hist"], r["lat"])):
        md.append(f"| {'spec' if r['spec_hist'] else 'non-spec'} | {r['lat']} | {r['mean_accuracy']:.2f}% | " +
                  " | ".join(f"{r['per_trace'][t]:.1f}" for t in run.TRACES) + " |")
    return md, fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiment", choices=["capacity", "latency", "all"])
    ap.add_argument("--size", type=int, default=10, help="tournament index bits for latency sweep")
    ap.add_argument("--depth", type=int, default=32, help="max in-flight branches for latency sweep")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--sim", default="iverilog", choices=["iverilog", "questa"])
    ap.add_argument("--rtl-dir", default=os.path.join(ROOT, "rtl"))
    a = ap.parse_args()
    os.makedirs(RES, exist_ok=True)
    md, fails = ["# Branch predictor results", "",
                 "_Every number below comes from an RTL simulation that matched the golden model bit-exactly._", ""], 0
    if a.experiment in ("capacity", "all"):
        m, f = capacity(a); md += m + [""]; fails += f
    if a.experiment in ("latency", "all"):
        m, f = latency(a); md += m + [""]; fails += f
    with open(os.path.join(RES, f"summary_{a.experiment}.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
