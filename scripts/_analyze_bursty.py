#!/usr/bin/env python3
"""Quick analysis of bursty 3-repeat results."""
import statistics
from math import sqrt

# NG runs
ng = [
    {'s': 27, 'p': 95, 'r': 78},
    {'s': 16, 'p': 103, 'r': 81},
    {'s': 21, 'p': 95, 'r': 84},
]

# PlanGate runs
pg = [
    {'s': 20, 'p': 70, 'r': 110},
    {'s': 19, 'p': 67, 'r': 114},
    {'s': 20, 'p': 75, 'r': 105},
]

def calc_metrics(runs):
    srs = [100*r['s']/200 for r in runs]
    abds = [100*r['p']/(r['s']+r['p']) for r in runs]
    partials = [r['p'] for r in runs]
    return srs, abds, partials

def welch_t(a, b):
    n1, n2 = len(a), len(b)
    m1, m2 = statistics.mean(a), statistics.mean(b)
    s1, s2 = statistics.stdev(a), statistics.stdev(b)
    se = sqrt(s1**2/n1 + s2**2/n2)
    t = (m1 - m2) / se if se > 0 else 0
    return t, m1 - m2

ng_sr, ng_abd, ng_p = calc_metrics(ng)
pg_sr, pg_abd, pg_p = calc_metrics(pg)

print("=" * 60)
print("  Bursty Real-LLM 3-Repeat Analysis")
print("=" * 60)

print(f"\n  NG:       SR = {statistics.mean(ng_sr):.1f} ± {statistics.stdev(ng_sr):.1f}%")
print(f"            ABD = {statistics.mean(ng_abd):.1f} ± {statistics.stdev(ng_abd):.1f}%")
print(f"            PARTIAL = {statistics.mean(ng_p):.0f} ± {statistics.stdev(ng_p):.0f}")

print(f"\n  PlanGate: SR = {statistics.mean(pg_sr):.1f} ± {statistics.stdev(pg_sr):.1f}%")
print(f"            ABD = {statistics.mean(pg_abd):.1f} ± {statistics.stdev(pg_abd):.1f}%")
print(f"            PARTIAL = {statistics.mean(pg_p):.0f} ± {statistics.stdev(pg_p):.0f}")

t_abd, d_abd = welch_t(ng_abd, pg_abd)
t_sr, d_sr = welch_t(ng_sr, pg_sr)
t_p, d_p = welch_t(ng_p, pg_p)

print(f"\n  --- Hypothesis Tests (Welch t-test, df≈4) ---")
print(f"  ABD:     NG - PG = {d_abd:+.1f} pp,  t = {t_abd:.3f}  (p<0.05 if |t|>2.776)")
print(f"  SR:      NG - PG = {d_sr:+.1f} pp,  t = {t_sr:.3f}")
print(f"  PARTIAL: NG - PG = {d_p:+.0f},     t = {t_p:.3f}")

print(f"\n  --- Cascade Waste ---")
print(f"  NG PARTIAL mean:  {statistics.mean(ng_p):.0f}")
print(f"  PG PARTIAL mean:  {statistics.mean(pg_p):.0f}")
print(f"  Reduction:        {d_p:.0f} agents ({d_p/statistics.mean(ng_p)*100:.1f}%)")

# Token efficiency
ng_tok = [983771, 984900, None]  # run3 tokens not yet available
pg_tok = [None, None, None]
# Approximate from summary
print(f"\n  --- Per-Admitted-Agent Success Rate ---")
for name, runs in [("NG", ng), ("PG", pg)]:
    rates = [r['s']/(r['s']+r['p'])*100 for r in runs]
    print(f"  {name}: {statistics.mean(rates):.1f} ± {statistics.stdev(rates):.1f}%")

print("=" * 60)
