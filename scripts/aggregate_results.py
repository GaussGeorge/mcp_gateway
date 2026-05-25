#!/usr/bin/env python3
"""Aggregate experiment CSV summaries and print compact mean/std tables.

This script is intentionally lightweight and diagnostic-oriented. It now
includes the post-P3/P4 mechanism ablation summary so we can quickly check
that new control-path toggles still produce valid mock regression outputs.
"""

import csv
import os
import statistics
from collections import defaultdict


BASE = os.path.join(os.path.dirname(__file__), "..", "results")
METRICS = [
    "effective_goodput_s",
    "cascade_failed",
    "success",
    "rejected_s0",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "e2e_p50_ms",
    "e2e_p95_ms",
    "e2e_p99_ms",
]


def agg(path, groupby_cols):
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    groups = defaultdict(list)
    for row in rows:
        key = tuple(row[col] for col in groupby_cols)
        groups[key].append(row)
    results = []
    for key, grouped_rows in sorted(groups.items()):
        item = dict(zip(groupby_cols, key))
        for metric in METRICS:
            values = [float(row[metric]) for row in grouped_rows if row.get(metric)]
            item[f"{metric}_mean"] = round(statistics.mean(values), 2) if values else 0.0
            item[f"{metric}_std"] = round(statistics.stdev(values), 2) if len(values) >= 2 else 0.0
        results.append(item)
    return results


def fmt(mean_key, std_key, item):
    mean = item[mean_key]
    std = item[std_key]
    if std > 0:
        return f"{mean:.1f}(±{std:.1f})"
    return f"{mean:.1f}"


def print_section(title, path, groupby_cols, row_fmt):
    if not os.path.exists(path):
        return
    print()
    print(title)
    for line in row_fmt(agg(path, groupby_cols)):
        print(line)


print("=== Exp1_Core ===")
print(f"{'Gateway':16s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'Reject':>14s}  {'P50':>14s}  {'P95':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}  {'E2E_P99':>16s}")
for d in agg(os.path.join(BASE, "exp1_core", "exp1_core_summary.csv"), ["gateway"]):
    print(f"{d['gateway']:16s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('rejected_s0_mean','rejected_s0_std',d):>14s}  {fmt('p50_ms_mean','p50_ms_std',d):>14s}  {fmt('p95_ms_mean','p95_ms_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}  {fmt('e2e_p99_ms_mean','e2e_p99_ms_std',d):>16s}")

print()
print("=== Exp3_MixedMode (ps_ratio sweep) ===")
print(f"{'Gateway':16s}  {'ps':>4s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'E2E_P50':>16s}")
for d in agg(os.path.join(BASE, "exp3_mixedmode", "exp3_mixedmode_summary.csv"), ["gateway", "sweep_val"]):
    print(f"{d['gateway']:16s}  {d['sweep_val']:>4s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}")

print()
print("=== Exp4_Ablation ===")
print(f"{'Gateway':16s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'E2E_P95':>16s}")
for d in agg(os.path.join(BASE, "exp4_ablation", "exp4_ablation_summary.csv"), ["gateway"]):
    print(f"{d['gateway']:16s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}")

print()
print("=== Exp7_ClientReject (price_ttl sweep) ===")
print(f"{'TTL':>5s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'Reject':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}  {'E2E_P99':>16s}")
for d in agg(os.path.join(BASE, "exp7_clientreject", "exp7_clientreject_summary.csv"), ["sweep_val"]):
    print(f"{d['sweep_val']:>5s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('rejected_s0_mean','rejected_s0_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}  {fmt('e2e_p99_ms_mean','e2e_p99_ms_std',d):>16s}")

print()
print("=== Exp5_ScaleConc (P&S, key gateways E2E) ===")
print(f"{'Gateway':16s}  {'conc':>4s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}")
for d in agg(os.path.join(BASE, "exp5_scaleconc", "exp5_scaleconc_summary.csv"), ["gateway", "sweep_val"]):
    print(f"{d['gateway']:16s}  {d['sweep_val']:>4s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}")

exp6_csv = os.path.join(BASE, "exp6_scaleconcreact", "exp6_scaleconcreact_summary.csv")
if os.path.exists(exp6_csv):
    print()
    print("=== Exp6_ScaleConcReact (ReAct, key gateways E2E) ===")
    print(f"{'Gateway':16s}  {'conc':>4s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}")
    for d in agg(exp6_csv, ["gateway", "sweep_val"]):
        print(f"{d['gateway']:16s}  {d['sweep_val']:>4s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}")

exp11_csv = os.path.join(BASE, "exp11_newmechanismablation", "exp11_newmechanismablation_summary.csv")
if os.path.exists(exp11_csv):
    print()
    print("=== Exp11_NewMechanismAblation (diagnostic/regression) ===")
    print(f"{'Gateway':16s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'E2E_P95':>16s}")
    for d in agg(exp11_csv, ["gateway"]):
        print(f"{d['gateway']:16s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}")
