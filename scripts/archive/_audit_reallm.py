"""Audit all three 'dangerous' real-LLM experiments against paper claims."""
import csv, os, statistics

BASE = r"d:\mcp版本备份\mcp-governance-main - A"

def read_csv(relpath):
    with open(os.path.join(BASE, relpath)) as f:
        return list(csv.DictReader(f))

def mean(vals): return sum(vals)/len(vals) if vals else float("nan")
def std(vals): return statistics.stdev(vals) if len(vals)>1 else 0

# ============================================================
print("=" * 80)
print("EXP 1: exp_week5_C10 — Real-LLM C=10 (paper: 'boundary condition')")
print("=" * 80)
rows = read_csv("artifact_cache/exp_week5_C10/week5_summary.csv")
for gw in ["ng","rajomon","pp","plangate_real"]:
    r = [x for x in rows if x["gateway"]==gw]
    sr = [float(x["success_rate"]) for x in r]
    abd = [float(x["abd_total"]) for x in r]
    p95 = [float(x["p95_ms"])/1000 for x in r]
    print(f"  {gw:16s} n={len(r)} Succ%={mean(sr):.1f}±{std(sr):.1f}  "
          f"ABD={mean(abd):.1f}±{std(abd):.1f}%  P95={mean(p95):.0f}±{std(p95):.0f}s")

print()
print("PAPER CLAIMS (v7 §5.2):")
print("  PlanGate: 88.4±2.4% vs NG 92-94%, ABD 10.1±2.5% vs 5-7%")
print("  => Paper correctly reports PlanGate IS WORSE at C=10 (boundary condition)")

# ============================================================
print()
print("=" * 80)
print("EXP 2: exp_bursty_C20_B30 — Bursty Real-LLM (paper: '~19% doomed reduction')")
print("=" * 80)
rows = read_csv("results/exp_bursty_C20_B30/bursty_summary.csv")
for gw in ["ng","plangate_real"]:
    r = [x for x in rows if x["gateway"]==gw]
    partial = [float(x["partial"]) for x in r]
    casc_steps = [float(x["cascade_steps"]) for x in r]
    rej0 = [float(x["all_rejected"]) for x in r]
    abd = [float(x["abd_total"]) for x in r]
    sr = [float(x["success_rate"]) for x in r]
    print(f"  {gw:16s} n={len(r)}  PARTIAL={mean(partial):.0f}±{std(partial):.0f}  "
          f"Casc_steps={mean(casc_steps):.0f}±{std(casc_steps):.0f}  "
          f"Rej0={mean(rej0):.0f}±{std(rej0):.0f}  ABD={mean(abd):.1f}%  Succ%={mean(sr):.1f}")

ng_partial = [float(x["partial"]) for x in rows if x["gateway"]=="ng"]
pg_partial = [float(x["partial"]) for x in rows if x["gateway"]=="plangate_real"]
ng_casc  = [float(x["cascade_steps"]) for x in rows if x["gateway"]=="ng"]
pg_casc  = [float(x["cascade_steps"]) for x in rows if x["gateway"]=="plangate_real"]
print(f"\n  Reduction in doomed: ({mean(ng_partial):.1f}-{mean(pg_partial):.1f})/{mean(ng_partial):.1f} = "
      f"{(mean(ng_partial)-mean(pg_partial))/mean(ng_partial)*100:.1f}%")
print(f"  Reduction in cascade: ({mean(ng_casc):.1f}-{mean(pg_casc):.1f})/{mean(ng_casc):.1f} = "
      f"{(mean(ng_casc)-mean(pg_casc))/mean(ng_casc)*100:.1f}%")

# t-test for doomed sessions
import math
n1, n2 = len(ng_partial), len(pg_partial)
m1, m2 = mean(ng_partial), mean(pg_partial)
s1, s2 = std(ng_partial), std(pg_partial)
se = math.sqrt(s1**2/n1 + s2**2/n2)
t_stat = (m1 - m2) / se if se > 0 else 0
print(f"  t-stat (partial, one-tailed): t={t_stat:.2f}, n1={n1}, n2={n2}")

print()
print("PAPER CLAIMS:")
print("  NG: PARTIAL=91±11, PG: PARTIAL=75±12 → ~19% reduction (p<0.02)")
print("  NG: Cascade=163±25, PG: Cascade=144±24 → ~18% reduction (p<0.05)")

# ============================================================
print()
print("=" * 80)
print("EXP 3: exp_selfhosted_vllm_C20_W8 — Self-Hosted vLLM C=20 W=8")
print("=" * 80)
rows = read_csv("results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv")
for gw in ["ng","plangate_real"]:
    r = [x for x in rows if x["gateway"]==gw]
    sr = [float(x["success_rate"]) for x in r]
    abd = [float(x["abd_total"]) for x in r]
    casc = [float(x["cascade_steps"]) for x in r]
    rej0 = [float(x["all_rejected"]) for x in r]
    p95 = [float(x["p95_ms"])/1000 for x in r]
    partial = [float(x["partial"]) for x in r]
    admitted = [float(x["success"])+float(x["partial"]) for x in r]
    print(f"  {gw:16s} n={len(r)}  Succ%={mean(sr):.1f}±{std(sr):.1f}  "
          f"Partial(ABD)={mean(partial):.1f}±{std(partial):.1f}  "
          f"ABD%={mean(abd):.1f}%  Casc_steps={mean(casc):.0f}±{std(casc):.0f}  "
          f"Rej0={mean(rej0):.1f}  P95={mean(p95):.0f}s")
    print(f"     Admitted(S+P)={mean(admitted):.1f}  cascade/admitted={mean(casc)/mean(admitted)*100:.1f}% wasted steps per admitted")

ng_sh = [x for x in rows if x["gateway"]=="ng"]
pg_sh = [x for x in rows if x["gateway"]=="plangate_real"]
print(f"\n  --- Key comparison ---")
print(f"  Succ%: NG={mean([float(x['success_rate']) for x in ng_sh]):.1f}  PG={mean([float(x['success_rate']) for x in pg_sh]):.1f}  "
      f"(PG LOWER by {mean([float(x['success_rate']) for x in ng_sh])-mean([float(x['success_rate']) for x in pg_sh]):.1f}pp)")
print(f"  Casc_steps: NG={mean([float(x['cascade_steps']) for x in ng_sh]):.1f}  PG={mean([float(x['cascade_steps']) for x in pg_sh]):.1f}  "
      f"(PG FEWER by {(mean([float(x['cascade_steps']) for x in ng_sh])-mean([float(x['cascade_steps']) for x in pg_sh])):.1f} steps, "
      f"{(mean([float(x['cascade_steps']) for x in ng_sh])-mean([float(x['cascade_steps']) for x in pg_sh]))/mean([float(x['cascade_steps']) for x in ng_sh])*100:.1f}% less)")
print(f"  ABD%: NG={mean([float(x['abd_total']) for x in ng_sh]):.1f}%  PG={mean([float(x['abd_total']) for x in pg_sh]):.1f}%")
print(f"  P95: NG={mean([float(x['p95_ms']) for x in ng_sh])/1000:.0f}s  PG={mean([float(x['p95_ms']) for x in pg_sh])/1000:.0f}s  (PG HIGHER)")
print(f"\n  => PlanGate: LOWER success, HIGHER ABD%), HIGHER P95, but ~13% fewer cascade steps")
print(f"  => Interpretation: PG converts SOME admitted sessions to step-0 rejections (more Rej0),")
print(f"     but session-cap under-estimates actual capacity, leading to over-rejection")
