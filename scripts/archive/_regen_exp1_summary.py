"""Regenerate exp1_core_summary.csv with all 5 runs' data."""
import csv, os, statistics

RESULTS_BASE = r"results\exp1_core"
OUT = os.path.join(RESULTS_BASE, "exp1_core_summary.csv")

GATEWAYS = ["ng", "srl", "sbac", "plangate_full"]

fieldnames = [
    "gateway", "run_idx", "success", "rejected_s0", "cascade_failed",
    "effective_goodput_s", "p50_ms", "p95_ms",
]

rows_out = []
for gw in GATEWAYS:
    for run in range(1, 6):
        sf = os.path.join(RESULTS_BASE, f"{gw}_run{run}_sessions.csv")
        rf = os.path.join(RESULTS_BASE, f"{gw}_run{run}.csv")
        if not os.path.isfile(sf) or not os.path.isfile(rf):
            continue

        # session counts
        sess = list(csv.DictReader(open(sf)))
        succ = sum(1 for x in sess if x["state"] == "SUCCESS")
        rej0 = sum(1 for x in sess if x["state"] == "REJECTED_AT_STEP_0")
        casc = sum(1 for x in sess if "CASCADE" in x["state"])

        # step-level latency
        steps = list(csv.DictReader(open(rf)))
        lats = sorted([float(x["latency_ms"]) for x in steps if x.get("latency_ms")])
        n = len(lats)
        p50 = lats[int(n * 0.50)] if n > 0 else 0
        p95 = lats[int(n * 0.95)] if n > 0 else 0

        # GP/s
        sessions_eg = {}
        times_all = []
        for x in steps:
            sid = x["session_id"]
            eg = float(x.get("effective_goodput") or 0)
            sessions_eg[sid] = eg
            t = x.get("timestamp")
            if t:
                times_all.append(float(t))
        total_eg = sum(sessions_eg.values())
        duration = max(times_all) - min(times_all) if len(times_all) > 1 else 1
        gps = total_eg / duration if duration > 0 else 0

        rows_out.append({
            "gateway": gw, "run_idx": run,
            "success": succ, "rejected_s0": rej0, "cascade_failed": casc,
            "effective_goodput_s": round(gps, 2),
            "p50_ms": round(p50, 1), "p95_ms": round(p95, 1),
        })

# Write
with open(OUT, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows_out)

print(f"Written {len(rows_out)} rows to {OUT}")
print()

# Print summary means per gateway
for gw in GATEWAYS:
    gw_rows = [r for r in rows_out if r["gateway"] == gw]
    if not gw_rows: continue
    ms = statistics.mean(r["success"] for r in gw_rows)
    mc = statistics.mean(r["cascade_failed"] for r in gw_rows)
    mr = statistics.mean(r["rejected_s0"] for r in gw_rows)
    mg = statistics.mean(r["effective_goodput_s"] for r in gw_rows)
    mp50 = statistics.mean(r["p50_ms"] for r in gw_rows)
    mp95 = statistics.mean(r["p95_ms"] for r in gw_rows)
    print(f"{gw}: Succ={ms:.1f} Casc={mc:.1f} Rej0={mr:.1f} GP/s={mg:.2f} P50={mp50:.1f} P95={mp95:.1f}")
