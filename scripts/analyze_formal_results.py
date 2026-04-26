#!/usr/bin/env python3
"""Analyze formal Week 5 experiment results with permutation tests."""
import csv
import os
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

def load_summary(concurrency):
    path = os.path.join(ROOT, "results", f"exp_week5_C{concurrency}", "week5_summary.csv")
    data = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gw = row["gateway"]
            if gw not in data:
                data[gw] = {"sr": [], "abd": [], "p95": [], "gps": []}
            data[gw]["sr"].append(float(row["success_rate"]))
            data[gw]["abd"].append(float(row["abd_total"]))
            data[gw]["p95"].append(float(row["p95_ms"]))
            data[gw]["gps"].append(float(row["eff_gps"]))
    return data

def permutation_test(a, b, n_perm=50000):
    a, b = np.array(a), np.array(b)
    observed = np.mean(a) - np.mean(b)
    combined = np.concatenate([a, b])
    n = len(a)
    rng = np.random.default_rng(42)
    count = 0
    for _ in range(n_perm):
        perm = rng.permutation(combined)
        if abs(np.mean(perm[:n]) - np.mean(perm[n:])) >= abs(observed):
            count += 1
    return observed, count / n_perm

def print_table(conc, data):
    print(f"\n{'='*70}")
    print(f"  C={conc} Formal 5-Repeat Results")
    print(f"{'='*70}")
    print(f"  {'Gateway':<18} {'SuccRate%':>10} {'ABD%':>10} {'GP/s':>8} {'P50ms':>8} {'P95ms':>8}")
    print(f"  {'─'*18} {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*8}")
    for gw in ["ng", "rajomon", "pp", "plangate_real"]:
        d = data[gw]
        sr_m, sr_s = np.mean(d["sr"]), np.std(d["sr"])
        abd_m, abd_s = np.mean(d["abd"]), np.std(d["abd"])
        gps_m = np.mean(d["gps"])
        p95_m = np.mean(d["p95"])
        print(f"  {gw:<18} {sr_m:>6.1f}±{sr_s:.1f} {abd_m:>6.1f}±{abd_s:.1f} {gps_m:>8.2f} {'':>8} {p95_m:>8.0f}")

def main():
    c10 = load_summary(10)
    c40 = load_summary(40)

    print_table(10, c10)
    print_table(40, c40)

    print(f"\n{'='*70}")
    print("  Permutation Tests (two-sided, n=50000)")
    print(f"{'='*70}")

    # PG vs each baseline at C=10
    print("\n  --- C=10: PlanGate vs Baselines ---")
    for gw in ["ng", "rajomon", "pp"]:
        obs, p = permutation_test(c10["plangate_real"]["sr"], c10[gw]["sr"])
        marker = "*" if p < 0.10 else ""
        print(f"  SuccRate PG-{gw:<8}: {obs:+.1f}pp  p={p:.3f} {marker}")
    obs, p = permutation_test(c10["ng"]["abd"], c10["plangate_real"]["abd"])
    marker = "*" if p < 0.10 else ""
    print(f"  ABD NG-PG       : {obs:+.1f}pp  p={p:.3f} {marker}")
    obs, p = permutation_test(c10["ng"]["p95"], c10["plangate_real"]["p95"])
    print(f"  P95 NG-PG       : {obs:+.0f}ms  p={p:.3f}")

    # PG vs each baseline at C=40
    print("\n  --- C=40: PlanGate vs Baselines ---")
    for gw in ["ng", "rajomon", "pp"]:
        obs, p = permutation_test(c40["plangate_real"]["sr"], c40[gw]["sr"])
        print(f"  SuccRate PG-{gw:<8}: {obs:+.1f}pp  p={p:.3f}")
    obs, p = permutation_test(c40["ng"]["abd"], c40["plangate_real"]["abd"])
    print(f"  ABD NG-PG       : {obs:+.1f}pp  p={p:.3f}")

    # Degradation analysis
    print(f"\n  --- C=10 → C=40 Degradation ---")
    for gw in ["ng", "rajomon", "pp", "plangate_real"]:
        m10 = np.mean(c10[gw]["sr"])
        m40 = np.mean(c40[gw]["sr"])
        print(f"  {gw:<18}: {m10:.1f}% → {m40:.1f}% (Δ={m40-m10:+.1f}pp)")

    # P95 comparison
    print(f"\n  --- P95 Comparison ---")
    for conc, data in [(10, c10), (40, c40)]:
        vals = {gw: np.mean(data[gw]["p95"]) for gw in data}
        best = min(vals, key=vals.get)
        print(f"  C={conc}: " + ", ".join(f"{gw}={v:.0f}ms" for gw, v in vals.items()) + f"  [BEST: {best}]")

    # GP/s comparison
    print(f"\n  --- GP/s Comparison ---")
    for conc, data in [(10, c10), (40, c40)]:
        vals = {gw: np.mean(data[gw]["gps"]) for gw in data}
        best = max(vals, key=vals.get)
        print(f"  C={conc}: " + ", ".join(f"{gw}={v:.2f}" for gw, v in vals.items()) + f"  [BEST: {best}]")

    print(f"\n  === Summary ===")
    pg10 = np.mean(c10["plangate_real"]["sr"])
    ng10 = np.mean(c10["ng"]["sr"])
    pg40 = np.mean(c40["plangate_real"]["sr"])
    ng40 = np.mean(c40["ng"]["sr"])
    print(f"  C=10: PlanGate {pg10:.1f}% vs NG {ng10:.1f}% (Δ={pg10-ng10:+.1f}pp)")
    print(f"  C=40: PlanGate {pg40:.1f}% vs NG {ng40:.1f}% (Δ={pg40-ng40:+.1f}pp)")
    print(f"  PlanGate degradation: {pg10:.1f}% → {pg40:.1f}% ({pg40-pg10:+.1f}pp)")
    print(f"  NG degradation:       {ng10:.1f}% → {ng40:.1f}% ({ng40-ng10:+.1f}pp)")

if __name__ == "__main__":
    main()
