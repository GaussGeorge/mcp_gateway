"""Compute GP/s from 5 runs by reading raw run CSVs."""
import csv, os, statistics

RESULTS_BASE = r"results\exp1_core"
PAPER_GPS = {"ng": 16.2, "srl": 28.3, "sbac": 46.4, "plangate_full": 51.9}

print("=== GP/s (5-run mean) vs Paper ===")
for gw in ["ng", "srl", "sbac", "plangate_full"]:
    gps_arr = []
    for run in range(1, 6):
        rf = os.path.join(RESULTS_BASE, f"{gw}_run{run}.csv")
        if not os.path.isfile(rf):
            continue
        rows = list(csv.DictReader(open(rf)))
        # last effective_goodput per session = total EG for that session
        sessions = {}
        times_all = []
        for x in rows:
            sid = x["session_id"]
            eg = float(x.get("effective_goodput") or 0)
            sessions[sid] = eg
            t = x.get("timestamp")
            if t:
                times_all.append(float(t))
        total_eg = sum(sessions.values())
        if times_all:
            duration = max(times_all) - min(times_all)
            if duration > 0:
                gps_arr.append(total_eg / duration)
    if gps_arr:
        mg = statistics.mean(gps_arr)
        paper_g = PAPER_GPS.get(gw, "?")
        diff = abs(mg - paper_g) / paper_g * 100 if isinstance(paper_g, float) else 0
        print(f"{gw}: GP/s={mg:.2f}  paper={paper_g}  diff={diff:.1f}%")
