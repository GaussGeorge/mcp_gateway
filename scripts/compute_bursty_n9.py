#!/usr/bin/env python3
"""Compute N=9 bursty statistics after adding run6-7, and generate paper-ready output."""
import os, csv, statistics, math, json

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.path.join(ROOT, "results", "exp_bursty_C20_B30")

def read_all_data(gw):
    """Read all data points from steps_summary.csv in run1-run9."""
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

def extract_cascade_from_logs(gw):
    """Extract cascade_steps from stdout.log files when CSV cascade is 0."""
    cascades = []
    gw_dir = os.path.join(BASE, gw)
    import re
    for i in range(1, 10):
        logfile = os.path.join(gw_dir, f"run{i}", "stdout.log")
        if not os.path.exists(logfile):
            continue
        with open(logfile, encoding="utf-8", errors="replace") as f:
            content = f.read()
        # Look for cascade_steps pattern in summary
        m = re.search(r'cascade_steps["\s:=]+(\d+)', content)
        if m:
            cascades.append({"run": i, "cascade": int(m.group(1))})
    return cascades

def t_test_2sample(a, b):
    """Two-sample pooled t-test, returns (t, df)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return float('nan'), 0
    m1, m2 = statistics.mean(a), statistics.mean(b)
    s1, s2 = statistics.stdev(a), statistics.stdev(b)
    sp = math.sqrt(((n1-1)*s1**2 + (n2-1)*s2**2) / (n1+n2-2))
    if sp == 0:
        return float('inf'), n1+n2-2
    t = (m1 - m2) / (sp * math.sqrt(1/n1 + 1/n2))
    return t, n1+n2-2

# Read data
ng_data = read_all_data("ng")
pg_data = read_all_data("plangate_real")
N = len(ng_data)

print("=" * 70)
print(f"BURSTY REAL-LLM STATISTICS (N={N})")
print("=" * 70)

# Print all data points
for gw_name, data in [("NG", ng_data), ("PlanGate", pg_data)]:
    print(f"\n--- {gw_name} ({len(data)} points) ---")
    for i, d in enumerate(data):
        print(f"  [{i+1}] run{d['run']}: success={d['success']}, partial={d['partial']}, "
              f"rej={d['all_rejected']}, cascade={d['cascade_steps']}")

# Compute metrics
def compute_metrics(data, label):
    n = len(data)
    partials = [d["partial"] for d in data]
    successes = [d["success"] for d in data]
    rejecteds = [d["all_rejected"] for d in data]
    cascades = [d["cascade_steps"] for d in data]
    abds = [100*d["partial"]/(d["success"]+d["partial"]) if (d["success"]+d["partial"])>0 else 0 for d in data]
    srs = [100*d["success"]/200 for d in data]
    
    print(f"\n=== {label} (N={n}) ===")
    print(f"  PARTIAL:  {statistics.mean(partials):.1f} ± {statistics.stdev(partials):.1f}  (rounded: {round(statistics.mean(partials))}±{round(statistics.stdev(partials))})")
    print(f"  SUCCESS:  {statistics.mean(successes):.1f} ± {statistics.stdev(successes):.1f}")
    print(f"  Rej0:     {statistics.mean(rejecteds):.1f} ± {statistics.stdev(rejecteds):.1f}  (rounded: {round(statistics.mean(rejecteds))}±{round(statistics.stdev(rejecteds))})")
    print(f"  ABD:      {statistics.mean(abds):.1f} ± {statistics.stdev(abds):.1f}%")
    print(f"  SR:       {statistics.mean(srs):.1f} ± {statistics.stdev(srs):.1f}%")
    if any(c > 0 for c in cascades):
        print(f"  Cascade:  {statistics.mean(cascades):.1f} ± {statistics.stdev(cascades):.1f}  (rounded: {round(statistics.mean(cascades))}±{round(statistics.stdev(cascades))})")
    
    return {
        "partials": partials, "successes": successes, "rejecteds": rejecteds,
        "cascades": cascades, "abds": abds, "srs": srs
    }

ng_metrics = compute_metrics(ng_data, "NG")
pg_metrics = compute_metrics(pg_data, "PlanGate")

# T-tests
print("\n" + "=" * 70)
print("T-TESTS")
print("=" * 70)

for metric_name, ng_vals, pg_vals in [
    ("PARTIAL", ng_metrics["partials"], pg_metrics["partials"]),
    ("Rej0", ng_metrics["rejecteds"], pg_metrics["rejecteds"]),
    ("ABD", ng_metrics["abds"], pg_metrics["abds"]),
    ("SR", ng_metrics["srs"], pg_metrics["srs"]),
]:
    t, df = t_test_2sample(ng_vals, pg_vals)
    ng_m = statistics.mean(ng_vals)
    pg_m = statistics.mean(pg_vals)
    pct = (ng_m - pg_m) / ng_m * 100 if ng_m != 0 else 0
    print(f"  {metric_name}: t({df})={t:.2f}, NG={ng_m:.1f}, PG={pg_m:.1f}, reduction={pct:.1f}%")

# Cascade t-test (use log data if CSV cascade is 0)
ng_log_cascade = extract_cascade_from_logs("ng")
pg_log_cascade = extract_cascade_from_logs("plangate_real")
if ng_log_cascade and pg_log_cascade:
    ng_c = [c["cascade"] for c in ng_log_cascade]
    pg_c = [c["cascade"] for c in pg_log_cascade]
    t_c, df_c = t_test_2sample(ng_c, pg_c)
    print(f"  Cascade (from logs, N_ng={len(ng_c)}, N_pg={len(pg_c)}): t({df_c})={t_c:.2f}, "
          f"NG={statistics.mean(ng_c):.1f}±{statistics.stdev(ng_c):.1f}, "
          f"PG={statistics.mean(pg_c):.1f}±{statistics.stdev(pg_c):.1f}")

# Paper table format
print("\n" + "=" * 70)
print("PAPER TABLE VALUES (for Table 12)")
print("=" * 70)

for gw_name, metrics in [("NG", ng_metrics), ("PlanGate", pg_metrics)]:
    p_m = round(statistics.mean(metrics["partials"]))
    p_s = round(statistics.stdev(metrics["partials"]))
    r_m = round(statistics.mean(metrics["rejecteds"]))
    r_s = round(statistics.stdev(metrics["rejecteds"]))
    abd_m = statistics.mean(metrics["abds"])
    abd_s = statistics.stdev(metrics["abds"])
    sr_m = statistics.mean(metrics["srs"])
    sr_s = statistics.stdev(metrics["srs"])
    print(f"  {gw_name}: PARTIAL=${p_m}\\pm{p_s}$  Rej0=${r_m}\\pm{r_s}$  "
          f"ABD=${abd_m:.1f}\\pm{abd_s:.1f}$  SR=${sr_m:.1f}\\pm{sr_s:.1f}$")

# PARTIAL reduction percentage (from exact means, matching displayed integers)
ng_p_exact = statistics.mean(ng_metrics["partials"])
pg_p_exact = statistics.mean(pg_metrics["partials"])
pct_exact = (ng_p_exact - pg_p_exact) / ng_p_exact * 100
ng_p_round = round(ng_p_exact)
pg_p_round = round(pg_p_exact)
pct_round = (ng_p_round - pg_p_round) / ng_p_round * 100

print(f"\n  PARTIAL reduction (exact): ({ng_p_exact:.2f}-{pg_p_exact:.2f})/{ng_p_exact:.2f} = {pct_exact:.1f}%")
print(f"  PARTIAL reduction (rounded): ({ng_p_round}-{pg_p_round})/{ng_p_round} = {pct_round:.1f}%")

# Comparison with old N=7 values
print("\n" + "=" * 70)
print("COMPARISON: N=7 (old) vs N=9 (new)")
print("=" * 70)
old_n7 = {
    "ng_partial": "91±11", "pg_partial": "75±12",
    "ng_rej": "89±11", "pg_rej": "109±12",
    "ng_abd": "82.3±3.9", "pg_abd": "82.7±5.3",
    "ng_sr": "9.8±2.4", "pg_sr": "7.9±2.4",
    "ng_cascade": "163±25", "pg_cascade": "144±24",
    "partial_t": "t(12)=2.59", "partial_pct": "17.6%",
}
print(f"  Old N=7: NG PARTIAL={old_n7['ng_partial']}, PG PARTIAL={old_n7['pg_partial']}, {old_n7['partial_t']}, {old_n7['partial_pct']}")
t_partial, df_partial = t_test_2sample(ng_metrics["partials"], pg_metrics["partials"])
print(f"  New N={N}: NG PARTIAL={round(ng_p_exact)}±{round(statistics.stdev(ng_metrics['partials']))}, "
      f"PG PARTIAL={round(pg_p_exact)}±{round(statistics.stdev(pg_metrics['partials']))}, "
      f"t({df_partial})={t_partial:.2f}, {pct_round:.1f}%")
