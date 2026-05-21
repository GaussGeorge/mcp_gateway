"""Compute 5-run averages for exp1_core from step-level CSVs (results/) and sessions CSVs (artifact_cache/).
Paper uses:
  - Succ / Casc / Rej0  <- session-level counts
  - P50 / P95           <- step-level latency_ms percentiles  (from *_run{n}.csv)
  - GP/s                <- effective_goodput_s from summary (or recomputed)
"""
import csv, os, statistics

RESULTS_BASE = r"results\exp1_core"
ARTIFACT_BASE = r"artifact_cache\exp1_core"

PAPER = {
    "ng":           {"succ": 22.2, "casc": 122.6, "rej0": 355.2, "gps": 16.2, "p50": 1008, "p95": 1986},
    "srl":          {"succ": 40.0, "casc": 109.6, "rej0": 350.4, "gps": 28.3, "p50": 1006, "p95": 1896},
    "sbac":         {"succ": 58.8, "casc":  34.8, "rej0": 406.4, "gps": 46.4, "p50":  361, "p95": 1416},
    "plangate_full":{"succ": 72.6, "casc":   0.0, "rej0": 427.4, "gps": 51.9, "p50":  3.9, "p95":  819},
}
LABELS = {"ng": "NG", "srl": "SRL", "sbac": "SBAC", "plangate_full": "PlanGate"}

print("=" * 90)
print(f"{'GW':<16} {'Succ(paper)':>14} {'Casc(paper)':>14} {'P50(paper)':>14} {'P95(paper)':>14}")
print("=" * 90)

for gw in ["ng", "srl", "sbac", "plangate_full"]:
    succ_a, casc_a, rej0_a, gps_a, p50_a, p95_a = [], [], [], [], [], []

    for r in range(1, 6):
        # --- session counts from artifact_cache (has 5 session CSVs) ---
        sf = os.path.join(ARTIFACT_BASE, f"{gw}_run{r}_sessions.csv")
        if os.path.isfile(sf):
            rows = list(csv.DictReader(open(sf)))
            succ_a.append(sum(1 for x in rows if x["state"] == "SUCCESS"))
            casc_a.append(sum(1 for x in rows if "CASCADE" in x["state"]))
            rej0_a.append(sum(1 for x in rows if x["state"] == "REJECTED_AT_STEP_0"))

        # --- step-level latency from results/ (has 5 run CSVs) ---
        rf = os.path.join(RESULTS_BASE, f"{gw}_run{r}.csv")
        if os.path.isfile(rf):
            rows2 = list(csv.DictReader(open(rf)))
            lats = sorted([float(x["latency_ms"]) for x in rows2 if x.get("latency_ms")])
            n = len(lats)
            if n > 0:
                p50_a.append(lats[int(n * 0.50)])
                p95_a.append(lats[int(n * 0.95)])

    pv = PAPER.get(gw, {})
    ms  = statistics.mean(succ_a) if succ_a else float("nan")
    mc  = statistics.mean(casc_a) if casc_a else float("nan")
    mp50 = statistics.mean(p50_a) if p50_a else float("nan")
    mp95 = statistics.mean(p95_a) if p95_a else float("nan")

    label = LABELS[gw]
    print(f"{label:<16} "
          f"Succ={ms:>5.1f}(p={pv['succ']})  "
          f"Casc={mc:>6.1f}(p={pv['casc']})  "
          f"P50={mp50:>7.1f}(p={pv['p50']})  "
          f"P95={mp95:>7.1f}(p={pv['p95']})")
    ns = len(succ_a)
    np50 = len(p50_a)
    print(f"  runs for session={ns}, runs for latency={np50}")
