#!/usr/bin/env python3
"""
_compute_tput_latency_stats.py — Aggregate results from Exp13_TputLatency.

Reads:
  results/exp_tput_latency/tput_latency_summary.csv
  (or per-run CSVs in results/exp_tput_latency/conc*/*/runN.csv)

Outputs:
  results/exp_tput_latency/tput_latency_agg.csv  — mean±std per (gateway, concurrency)
  prints a paper-ready table to stdout

Usage:
  python scripts/_compute_tput_latency_stats.py
  python scripts/_compute_tput_latency_stats.py --input results/exp_tput_latency/tput_latency_summary.csv
  python scripts/_compute_tput_latency_stats.py --show-crossings
"""

import argparse
import csv
import os
import statistics
from collections import defaultdict
from typing import Dict, List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_tput_latency")
DEFAULT_SUMMARY_CSV = os.path.join(RESULTS_DIR, "tput_latency_summary.csv")

GW_ORDER = ["ng", "srl", "sbac", "plangate_full"]
GW_LABELS = {
    "ng":            "No-Gate (NG)",
    "srl":           "SRL",
    "sbac":          "SBAC",
    "plangate_full": "PlanGate",
}

# ==============================
#  Data loading
# ==============================

def load_summary(csv_path: str) -> List[dict]:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if "error" in r and r.get("error", ""):
                continue  # skip failed trials
            rows.append(r)
    return rows


# ==============================
#  Aggregation helpers
# ==============================

def avg(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def sd(vals: List[float]) -> float:
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def ci95(vals: List[float]) -> float:
    """95% CI half-width (≈ ±1.96 × SE), for N ≤ 10 use t(N-1)=2.776 at N=5."""
    N = len(vals)
    if N < 2:
        return 0.0
    t_map = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
             7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}
    t = t_map.get(N, 1.96)
    se = statistics.stdev(vals) / (N ** 0.5)
    return t * se


def aggregate(rows: List[dict]) -> Dict[Tuple[str, int], dict]:
    groups: Dict[Tuple[str, int], List[dict]] = defaultdict(list)
    for r in rows:
        try:
            key = (r["gateway"], int(r["concurrency"]))
        except (KeyError, ValueError):
            continue
        groups[key].append(r)

    result: Dict[Tuple[str, int], dict] = {}
    for (gw, conc), rs in groups.items():
        def col(name): return [float(r.get(name, 0) or 0) for r in rs]

        gps  = col("effective_goodput_s")
        p50  = [v / 1000 for v in col("e2e_p50_ms")]    # → seconds
        p95  = [v / 1000 for v in col("e2e_p95_ms")]
        p99  = [v / 1000 for v in col("e2e_p99_ms")]
        succ = col("success")
        part = col("partial")
        casc = col("cascade_failed")
        rej0 = col("rejected_s0")
        jfi  = col("jfi_steps")

        total    = [s + p + c + r0 for s, p, c, r0 in zip(succ, part, casc, rej0)]
        admitted = [s + p + c for s, p, c in zip(succ, part, casc)]
        doomed   = [p + c for p, c in zip(part, casc)]
        # admitted-session denominator (excludes step-0 rejections)
        abd      = [100 * do / ad if ad > 0 else 0.0 for do, ad in zip(doomed, admitted)]
        casc_pct = [100 * c / ad if ad > 0 else 0.0 for c, ad in zip(casc, admitted)]
        # all-session denominator (kept for reference only)
        casc_all_pct = [100 * c / t if t > 0 else 0.0 for c, t in zip(casc, total)]

        result[(gw, conc)] = {
            "n":                 len(rs),
            "gps_mean":          avg(gps),   "gps_sd":  sd(gps),  "gps_ci95": ci95(gps),
            "p50_mean":          avg(p50),   "p50_sd":  sd(p50),
            "p95_mean":          avg(p95),   "p95_sd":  sd(p95),  "p95_ci95": ci95(p95),
            "p99_mean":          avg(p99),   "p99_sd":  sd(p99),
            "succ_mean":         avg(succ),
            "abd_pct_mean":      avg(abd),   "abd_pct_sd": sd(abd),
            "casc_pct_mean":     avg(casc_pct),
            "casc_all_pct_mean": avg(casc_all_pct),
            "rej0_mean":         avg(rej0),
            "jfi_mean":          avg(jfi),
        }
    return result


# ==============================
#  Output / Reporting
# ==============================

def write_agg_csv(agg: Dict[Tuple[str, int], dict], out_path: str):
    fields = [
        "gateway", "concurrency", "n",
        "gps_mean", "gps_sd", "gps_ci95",
        "p50_mean", "p50_sd",
        "p95_mean", "p95_sd", "p95_ci95",
        "p99_mean", "p99_sd",
        "succ_mean",
        "abd_pct_mean", "abd_pct_sd",
        "casc_pct_mean",
        "casc_all_pct_mean",
        "rej0_mean",
        "jfi_mean",
    ]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for (gw, conc) in sorted(agg.keys(), key=lambda k: (GW_ORDER.index(k[0]) if k[0] in GW_ORDER else 99, k[1])):
            row = {"gateway": gw, "concurrency": conc, **agg[(gw, conc)]}
            writer.writerow(row)
    print(f"Aggregated CSV written to: {out_path}")


def print_main_table(agg: Dict[Tuple[str, int], dict]):
    conc_levels = sorted(set(k[1] for k in agg.keys()))
    print(f"\n{'='*95}")
    print(f"  THROUGHPUT–LATENCY SWEEP  |  Metrics: GP/s and P95 (seconds)")
    print(f"{'='*95}")
    print(f"  {'Gateway':20s}", end="")
    for c in conc_levels:
        print(f" | conc={c:>2}", end="")
    print()
    print(f"  {'-'*90}")

    for metric, label in [("gps_mean", "  GP/s  "), ("p95_mean", "  P95/s ")]:
        for gw in GW_ORDER:
            print(f"  {GW_LABELS.get(gw, gw):20s}[{label}]", end="")
            for conc in conc_levels:
                d = agg.get((gw, conc))
                if d:
                    v = d[metric]
                    se = d["gps_ci95"] if metric == "gps_mean" else d["p95_ci95"]
                    print(f"  {v:.2f}±{se:.2f}", end="")
                else:
                    print(f"  {'N/A':>10}", end="")
            print()
        print(f"  {'-'*90}")


def print_abd_cascade_table(agg: Dict[Tuple[str, int], dict]):
    conc_levels = sorted(set(k[1] for k in agg.keys()))
    print(f"\n  Admitted-Session Failure Rate (ABD+Cascade / admitted):")
    print(f"  {'Gateway':20s} {'Conc':>5} {'Fail%/adm':>10} {'Casc%/adm':>10} {'Rej0':>8}")
    print(f"  {'-'*65}")
    for gw in GW_ORDER:
        for conc in conc_levels:
            d = agg.get((gw, conc))
            if not d:
                continue
            print(f"  {GW_LABELS.get(gw, gw):20s} {conc:>5} "
                  f"{d['abd_pct_mean']:>7.1f}%  {d['casc_pct_mean']:>7.1f}%  "
                  f"{d['rej0_mean']:>7.1f}")
        print()


def find_crossings(agg: Dict[Tuple[str, int], dict]):
    """
    Find concurrency levels where PlanGate's effective goodput matches or
    exceeds another gateway — i.e., the 'iso-goodput' crossing point.
    At equal (or better) goodput, PlanGate should have lower P95.
    """
    conc_levels = sorted(set(k[1] for k in agg.keys()))
    PG = "plangate_full"

    print(f"\n  ISO-GOODPUT ANALYSIS (PlanGate vs others):")
    print(f"  At concurrency C where PlanGate GP/s >= baseline GP/s,")
    print(f"  compare P95 latency.")
    print(f"  {'Gateway':15s} {'C_cross':>8} {'PG GP/s':>9} {'Base GP/s':>10} {'PG P95':>8} {'Base P95':>9}")
    print(f"  {'-'*65}")

    for gw in [g for g in GW_ORDER if g != PG]:
        for conc in conc_levels:
            pg_d  = agg.get((PG, conc))
            bas_d = agg.get((gw, conc))
            if not pg_d or not bas_d:
                continue
            if pg_d["gps_mean"] >= bas_d["gps_mean"] * 0.95:   # within 5% = "comparable"
                print(f"  {GW_LABELS.get(gw, gw):15s} {conc:>8} "
                      f"{pg_d['gps_mean']:>9.2f} {bas_d['gps_mean']:>10.2f} "
                      f"{pg_d['p95_mean']:>8.2f} {bas_d['p95_mean']:>9.2f}")


# ==============================
#  Entry point
# ==============================

def main():
    parser = argparse.ArgumentParser(
        description="Aggregate Exp13_TputLatency results and print paper-ready table."
    )
    parser.add_argument("--input", default=DEFAULT_SUMMARY_CSV,
                        help=f"Summary CSV (default: {DEFAULT_SUMMARY_CSV})")
    parser.add_argument("--show-crossings", action="store_true",
                        help="Print iso-goodput crossing analysis")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: summary CSV not found: {args.input}")
        print("Run scripts/run_tput_latency_sweep.py first.")
        raise SystemExit(1)

    rows = load_summary(args.input)
    if not rows:
        print("No valid rows in summary CSV.")
        raise SystemExit(1)

    print(f"Loaded {len(rows)} trial rows from {args.input}")
    agg = aggregate(rows)

    out_csv = os.path.join(RESULTS_DIR, "tput_latency_agg.csv")
    write_agg_csv(agg, out_csv)
    print_main_table(agg)
    print_abd_cascade_table(agg)

    if args.show_crossings:
        find_crossings(agg)


if __name__ == "__main__":
    main()
