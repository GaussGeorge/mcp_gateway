import csv, os, statistics

BASE = r"artifact_cache\exp1_core"
PAPER_P = {
    "ng":           {"p50": 1008, "p95": 1986},
    "srl":          {"p50": 1006, "p95": 1896},
    "sbac":         {"p50":  361, "p95": 1416},
    "plangate_full":{"p50":  3.9, "p95":  819},
}
for gw in ["ng", "srl", "sbac", "plangate_full"]:
    p50_runs, p95_runs = [], []
    for r in range(1, 6):
        f = os.path.join(BASE, f"{gw}_run{r}_sessions.csv")
        if not os.path.isfile(f):
            continue
        rows = list(csv.DictReader(open(f)))
        lats = sorted([float(x["total_latency_ms"]) for x in rows if x.get("total_latency_ms")])
        n = len(lats)
        p50_runs.append(lats[int(n * 0.50)])
        p95_runs.append(lats[int(n * 0.95)])
    if p50_runs:
        mp50 = statistics.mean(p50_runs)
        mp95 = statistics.mean(p95_runs)
        pv = PAPER_P.get(gw, {})
        diff50 = abs(mp50 - pv["p50"]) / pv["p50"] * 100
        diff95 = abs(mp95 - pv["p95"]) / pv["p95"] * 100
        print(f"{gw}: P50={mp50:.1f} (paper={pv['p50']}, diff={diff50:.1f}%)  "
              f"P95={mp95:.1f} (paper={pv['p95']}, diff={diff95:.1f}%)")
