#!/usr/bin/env python3
"""Compute bursty N=7 stats from all existing run data."""
import os, csv, statistics, math

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.path.join(ROOT, "results", "exp_bursty_C20_B30")

def read_all_data(gw):
    data = []
    gw_dir = os.path.join(BASE, gw)
    for i in range(1, 10):
        summary_file = os.path.join(gw_dir, f"run{i}", "steps_summary.csv")
        if not os.path.exists(summary_file):
            continue
        with open(summary_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                data.append({
                    "run": i,
                    "success": int(row.get("success", 0)),
                    "partial": int(row.get("partial", 0)),
                    "all_rejected": int(row.get("all_rejected", row.get("rejected", 0))),
                    "cascade_steps": int(row.get("cascade_steps", row.get("cascade", 0))),
                })
    return data

def t_test_2sample(a, b):
    n1, n2 = len(a), len(b)
    m1, m2 = statistics.mean(a), statistics.mean(b)
    s1, s2 = statistics.stdev(a), statistics.stdev(b)
    sp = math.sqrt(((n1-1)*s1**2 + (n2-1)*s2**2) / (n1+n2-2))
    t = (m1 - m2) / (sp * math.sqrt(1/n1 + 1/n2))
    return t, n1+n2-2

for gw in ["ng", "plangate_real"]:
    data = read_all_data(gw)
    print(f"\n=== {gw} (N={len(data)}) ===")
    for i, d in enumerate(data):
        print(f"  point {i+1} (run{d['run']}): success={d['success']}, partial={d['partial']}, rej={d['all_rejected']}, cascade={d['cascade_steps']}")
    
    partials = [d["partial"] for d in data]
    successes = [d["success"] for d in data]
    rejecteds = [d["all_rejected"] for d in data]
    cascades = [d["cascade_steps"] for d in data]
    
    print(f"  PARTIAL: {statistics.mean(partials):.1f} +/- {statistics.stdev(partials):.1f}")
    print(f"  SUCCESS: {statistics.mean(successes):.1f} +/- {statistics.stdev(successes):.1f}")
    print(f"  Rej0: {statistics.mean(rejecteds):.1f} +/- {statistics.stdev(rejecteds):.1f}")
    if any(c > 0 for c in cascades):
        print(f"  Cascade: {statistics.mean(cascades):.1f} +/- {statistics.stdev(cascades):.1f}")
    
    abds = [100*d["partial"]/(d["success"]+d["partial"]) if (d["success"]+d["partial"])>0 else 0 for d in data]
    print(f"  ABD: {statistics.mean(abds):.1f} +/- {statistics.stdev(abds):.1f}%")
    srs = [100*d["success"]/200 for d in data]
    print(f"  SR: {statistics.mean(srs):.1f} +/- {statistics.stdev(srs):.1f}%")

# T-tests
print("\n=== T-tests ===")
ng_data = read_all_data("ng")
pg_data = read_all_data("plangate_real")

for metric_name, ng_vals, pg_vals in [
    ("PARTIAL", [d["partial"] for d in ng_data], [d["partial"] for d in pg_data]),
    ("Rej0", [d["all_rejected"] for d in ng_data], [d["all_rejected"] for d in pg_data]),
    ("Cascade", [d["cascade_steps"] for d in ng_data], [d["cascade_steps"] for d in pg_data]),
]:
    t, df = t_test_2sample(ng_vals, pg_vals)
    diff = statistics.mean(ng_vals) - statistics.mean(pg_vals)
    pct = diff / statistics.mean(ng_vals) * 100 if statistics.mean(ng_vals) != 0 else 0
    print(f"{metric_name}: t({df})={t:.2f}, NG={statistics.mean(ng_vals):.0f}+/-{statistics.stdev(ng_vals):.0f}, PG={statistics.mean(pg_vals):.0f}+/-{statistics.stdev(pg_vals):.0f}, reduction={pct:.1f}%")

# Paper comparison
print("\n=== Paper values vs Computed ===")
print("Paper: NG PARTIAL 91+/-11, PG PARTIAL 75+/-12, t(12)=2.59")
print("Paper: NG Rej0 89+/-11, PG Rej0 109+/-12, t(12)=3.28")
print("Paper: NG ABD 82.3+/-3.9, PG ABD 82.7+/-5.3")
print("Paper: NG SR 9.8+/-2.4, PG SR 7.9+/-2.4")
