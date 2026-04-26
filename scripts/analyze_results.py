#!/usr/bin/env python3
"""Analyze Exp3 and Exp6 results from new experiment run."""
import csv
import sys
import os
from collections import defaultdict
import statistics

BASE = "/mnt/d/mcp-governance-main/results"

def parse_summary(csv_path):
    results = defaultdict(lambda: defaultdict(list))
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"]
            sv = row["sweep_val"]
            try:
                results[(gw, sv)]["eff_gps"].append(float(row["effective_goodput_s"]))
                results[(gw, sv)]["success"].append(int(row["success"]))
                results[(gw, sv)]["cascade"].append(int(row["cascade_failed"]))
                results[(gw, sv)]["reject"].append(int(row["rejected_s0"]))
                results[(gw, sv)]["raw_gps"].append(float(row["raw_goodput_s"]))
                results[(gw, sv)]["p50"].append(float(row["p50_ms"]))
                results[(gw, sv)]["p95"].append(float(row["p95_ms"]))
            except (ValueError, KeyError):
                pass  # skip rows with empty/invalid data
    return results

def mean_std(vals):
    m = statistics.mean(vals)
    s = statistics.stdev(vals) if len(vals) > 1 else 0
    return m, s

def print_exp3():
    csv_path = os.path.join(BASE, "exp3_mixedmode", "exp3_mixedmode_summary.csv")
    results = parse_summary(csv_path)
    
    gateways = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    ps_ratios = ["0.0", "0.3", "0.5", "0.7", "1.0"]
    
    print("=" * 90)
    print("Exp3 MixedMode Summary (mean±std over 5 runs, sessions=200, conc=20)")
    print("=" * 90)
    
    for ps in ps_ratios:
        print(f"\n--- ps_ratio = {ps} ---")
        print(f"{'Gateway':<18} {'EffGP/s':>12} {'Success':>10} {'Cascade':>10} {'Reject':>10} {'P50(ms)':>10}")
        print("-" * 70)
        for gw in gateways:
            k = (gw, ps)
            if k in results:
                eff_m, eff_s = mean_std(results[k]["eff_gps"])
                suc_m = statistics.mean(results[k]["success"])
                cas_m = statistics.mean(results[k]["cascade"])
                rej_m = statistics.mean(results[k]["reject"])
                p50_m = statistics.mean(results[k]["p50"])
                print(f"{gw:<18} {eff_m:>8.1f}±{eff_s:<4.1f} {suc_m:>8.1f} {cas_m:>9.1f} {rej_m:>9.1f} {p50_m:>10.1f}")

def print_exp6():
    csv_path = os.path.join(BASE, "exp6_scaleconcreact", "exp6_scaleconcreact_summary.csv")
    results = parse_summary(csv_path)
    
    gateways = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    concurrencies = ["10", "20", "40", "60"]
    
    print("\n" + "=" * 90)
    print("Exp6 ScaleConcReact Summary (mean±std over 5 runs, sessions=200, ps_ratio=0.0)")
    print("=" * 90)
    
    for conc in concurrencies:
        print(f"\n--- concurrency = {conc} ---")
        print(f"{'Gateway':<18} {'EffGP/s':>12} {'RawGP/s':>12} {'Success':>10} {'Cascade':>10} {'Reject':>10}")
        print("-" * 72)
        for gw in gateways:
            k = (gw, conc)
            if k in results:
                eff_m, eff_s = mean_std(results[k]["eff_gps"])
                raw_m, _ = mean_std(results[k]["raw_gps"])
                suc_m = statistics.mean(results[k]["success"])
                cas_m = statistics.mean(results[k]["cascade"])
                rej_m = statistics.mean(results[k]["reject"])
                print(f"{gw:<18} {eff_m:>8.1f}±{eff_s:<4.1f} {raw_m:>8.1f}     {suc_m:>8.1f} {cas_m:>9.1f} {rej_m:>9.1f}")

if __name__ == "__main__":
    print_exp3()
    print_exp6()
