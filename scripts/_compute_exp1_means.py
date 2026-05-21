"""Compute 5-run averages from artifact_cache/exp1_core sessions CSV files."""
import csv, os, statistics

BASE = r"artifact_cache\exp1_core"
GATEWAYS = ["ng", "srl", "sbac", "plangate_full"]
PAPER = {
    "ng":           {"succ": 22.2, "casc": 122.6, "rej0": 355.2, "gps": 16.2, "p50": 1008, "p95": 1986},
    "srl":          {"succ": 40.0, "casc": 109.6, "rej0": 350.4, "gps": 28.3, "p50": 1006, "p95": 1896},
    "sbac":         {"succ": 58.8, "casc":  34.8, "rej0": 406.4, "gps": 46.4, "p50":  361, "p95": 1416},
    "plangate_full":{"succ": 72.6, "casc":   0.0, "rej0": 427.4, "gps": 51.9, "p50":    3.9,"p95":  819},
}

print(f"{'GW':<16} {'N':>2}  {'Succ':>8} {'Casc':>8} {'Rej0':>8}  {'P50':>8} {'P95':>8}")
print("-"*70)
for gw in GATEWAYS:
    succ_a, casc_a, rej0_a, p50_a, p95_a = [], [], [], [], []
    for r in range(1, 6):
        f = os.path.join(BASE, f"{gw}_run{r}_sessions.csv")
        if not os.path.isfile(f):
            continue
        rows = list(csv.DictReader(open(f)))
        succ = [x for x in rows if x["state"] == "SUCCESS"]
        casc = [x for x in rows if "CASCADE" in x["state"]]
        rej0 = [x for x in rows if x["state"] == "REJECTED_AT_STEP_0"]
        lats = [float(x["total_latency_ms"]) for x in succ if x["total_latency_ms"]]
        succ_a.append(len(succ))
        casc_a.append(len(casc))
        rej0_a.append(len(rej0))
        if lats:
            lats.sort()
            p50_a.append(lats[int(len(lats)*0.50)])
            p95_a.append(lats[int(len(lats)*0.95)])

    n = len(succ_a)
    if n == 0:
        print(f"{gw:<16}  NO DATA")
        continue

    ms = statistics.mean(succ_a)
    mc = statistics.mean(casc_a)
    mr0 = statistics.mean(rej0_a)
    mp50 = statistics.mean(p50_a) if p50_a else float("nan")
    mp95 = statistics.mean(p95_a) if p95_a else float("nan")

    pv = PAPER.get(gw, {})
    print(f"{gw:<16}  {n:>2}  Succ={ms:>6.1f}(paper={pv.get('succ','?')})  "
          f"Casc={mc:>6.1f}(paper={pv.get('casc','?')})  "
          f"Rej0={mr0:>6.1f}(paper={pv.get('rej0','?')})")
    print(f"{'':16}      P50={mp50:>7.1f}(paper={pv.get('p50','?')})  "
          f"P95={mp95:>7.1f}(paper={pv.get('p95','?')})")
