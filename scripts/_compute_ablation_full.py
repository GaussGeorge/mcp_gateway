"""Compute 5-run averages for exp4_ablation from sessions and run CSVs."""
import csv, os, statistics

RESULTS_BASE = r"results\exp4_ablation"
PAPER = {
    "plangate_full":  {"succ": 77.6, "gps": 57.2, "p50": 3.1, "p95": 817},
    "wo_budgetlock":  {"succ": 18.4, "gps": 12.3},
    "wo_sessioncap":  {"succ": 82.6, "gps": 57.0},
}
LABELS = {
    "plangate_full": "Full PlanGate",
    "wo_budgetlock": "w/o BudgetLock",
    "wo_sessioncap": "w/o SessionCap",
}

print(f"{'Variant':<20} {'N':>2}  {'Succ(paper)':>20} {'GP/s(paper)':>16} {'P50(paper)':>14} {'P95(paper)':>14}")
print("-" * 90)

for gw in ["plangate_full", "wo_budgetlock", "wo_sessioncap"]:
    succ_a, gps_a, p50_a, p95_a = [], [], [], []

    for r in range(1, 6):
        # Session counts
        sf = os.path.join(RESULTS_BASE, f"{gw}_run{r}_sessions.csv")
        if os.path.isfile(sf):
            rows = list(csv.DictReader(open(sf)))
            succ_a.append(sum(1 for x in rows if x["state"] == "SUCCESS"))

        # Step-level latency + GP/s
        rf = os.path.join(RESULTS_BASE, f"{gw}_run{r}.csv")
        if os.path.isfile(rf):
            rows2 = list(csv.DictReader(open(rf)))
            lats = sorted([float(x["latency_ms"]) for x in rows2 if x.get("latency_ms")])
            n = len(lats)
            if n > 0:
                p50_a.append(lats[int(n * 0.50)])
                p95_a.append(lats[int(n * 0.95)])
            # GP/s
            sessions = {}
            times_all = []
            for x in rows2:
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
                    gps_a.append(total_eg / duration)

    pv = PAPER.get(gw, {})
    ms = statistics.mean(succ_a) if succ_a else float("nan")
    mg = statistics.mean(gps_a) if gps_a else float("nan")
    mp50 = statistics.mean(p50_a) if p50_a else float("nan")
    mp95 = statistics.mean(p95_a) if p95_a else float("nan")

    label = LABELS[gw]
    print(f"{label:<20} {len(succ_a):>2}  "
          f"Succ={ms:>5.1f}(paper={pv.get('succ','?')})  "
          f"GP/s={mg:>5.1f}(paper={pv.get('gps','?')})  "
          f"P50={mp50:>6.1f}(paper={pv.get('p50','?')})  "
          f"P95={mp95:>6.1f}(paper={pv.get('p95','?')})")
