#!/usr/bin/env python3
"""Aggregate experiment CSV summaries and print formatted tables with mean (±std)."""
import csv, statistics, os
from collections import defaultdict

BASE = os.path.join(os.path.dirname(__file__), '..', 'results')

def agg(path, groupby_cols):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    groups = defaultdict(list)
    for r in rows:
        key = tuple(r[c] for c in groupby_cols)
        groups[key].append(r)
    results = []
    for key, rs in sorted(groups.items()):
        d = dict(zip(groupby_cols, key))
        for m in ['effective_goodput_s','cascade_failed','success','rejected_s0',
                   'p50_ms','p95_ms','p99_ms','e2e_p50_ms','e2e_p95_ms','e2e_p99_ms']:
            vals = [float(r[m]) for r in rs if r.get(m)]
            d[m+'_mean'] = round(statistics.mean(vals),2) if vals else 0
            d[m+'_std'] = round(statistics.stdev(vals),2) if len(vals) >= 2 else 0
        results.append(d)
    return results

def fmt(mean_key, std_key, d, width=8):
    """Format as 'mean (±std)' string."""
    m = d[mean_key]
    s = d[std_key]
    if s > 0:
        return f"{m:.1f}(±{s:.1f})"
    return f"{m:.1f}"

print('=== Exp1_Core ===')
print(f"{'Gateway':16s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'Reject':>14s}  {'P50':>14s}  {'P95':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}  {'E2E_P99':>16s}")
for d in agg(os.path.join(BASE, 'exp1_core', 'exp1_core_summary.csv'), ['gateway']):
    print(f"{d['gateway']:16s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('rejected_s0_mean','rejected_s0_std',d):>14s}  {fmt('p50_ms_mean','p50_ms_std',d):>14s}  {fmt('p95_ms_mean','p95_ms_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}  {fmt('e2e_p99_ms_mean','e2e_p99_ms_std',d):>16s}")

print()
print('=== Exp3_MixedMode (ps_ratio sweep) ===')
print(f"{'Gateway':16s}  {'ps':>4s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'E2E_P50':>16s}")
for d in agg(os.path.join(BASE, 'exp3_mixedmode', 'exp3_mixedmode_summary.csv'), ['gateway','sweep_val']):
    print(f"{d['gateway']:16s}  {d['sweep_val']:>4s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}")

print()
print('=== Exp7_ClientReject (price_ttl sweep) ===')
print(f"{'TTL':>5s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'Success':>14s}  {'Reject':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}  {'E2E_P99':>16s}")
for d in agg(os.path.join(BASE, 'exp7_clientreject', 'exp7_clientreject_summary.csv'), ['sweep_val']):
    print(f"{d['sweep_val']:>5s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('success_mean','success_std',d):>14s}  {fmt('rejected_s0_mean','rejected_s0_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}  {fmt('e2e_p99_ms_mean','e2e_p99_ms_std',d):>16s}")

print()
print('=== Exp5_ScaleConc (key gateways E2E) ===')
print(f"{'Gateway':16s}  {'conc':>4s}  {'EffGP/s':>16s}  {'Cascade':>14s}  {'E2E_P50':>16s}  {'E2E_P95':>16s}")
for d in agg(os.path.join(BASE, 'exp5_scaleconc', 'exp5_scaleconc_summary.csv'), ['gateway','sweep_val']):
    print(f"{d['gateway']:16s}  {d['sweep_val']:>4s}  {fmt('effective_goodput_s_mean','effective_goodput_s_std',d):>16s}  {fmt('cascade_failed_mean','cascade_failed_std',d):>14s}  {fmt('e2e_p50_ms_mean','e2e_p50_ms_std',d):>16s}  {fmt('e2e_p95_ms_mean','e2e_p95_ms_std',d):>16s}")
