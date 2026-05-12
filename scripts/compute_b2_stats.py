"""Compute B2 self-hosted vLLM experiment statistics (N=3)."""
import statistics, math

# NG runs (50 agents each)
ng_success  = [23, 30, 25]
ng_partial  = [21, 15, 20]
ng_all_rej  = [6, 5, 5]
ng_cascade  = [59, 44, 54]
ng_eff_gps  = [0.21, 0.29, 0.22]
ng_p95      = [112888, 117176, 106660]
ng_tokens_a = [276543, 265587, 276064]
ng_tokens_b = [3162, 4731, 4382]

# PlanGate runs (50 agents each)
pg_success  = [24, 16, 21]
pg_partial  = [18, 25, 22]
pg_all_rej  = [8, 9, 7]
pg_cascade  = [55, 63, 69]
pg_eff_gps  = [0.23, 0.13, 0.19]
pg_p95      = [103761, 102468, 114486]
pg_tokens_a = [278641, 272977, 286694]
pg_tokens_b = [4378, 4508, 2807]

n = 3

def ms(arr):
    m = statistics.mean(arr)
    s = statistics.stdev(arr) if len(arr) > 1 else 0
    return m, s

def ttest(a, b):
    na, nb = len(a), len(b)
    ma, mb = statistics.mean(a), statistics.mean(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    sp = math.sqrt(((na-1)*va + (nb-1)*vb) / (na+nb-2))
    se = sp * math.sqrt(1/na + 1/nb)
    if se == 0:
        return float('inf')
    return (ma - mb) / se

ng_abd = [100*ng_partial[i]/(ng_success[i]+ng_partial[i]) for i in range(n)]
pg_abd = [100*pg_partial[i]/(pg_success[i]+pg_partial[i]) for i in range(n)]
ng_sr = [100*ng_success[i]/50 for i in range(n)]
pg_sr = [100*pg_success[i]/50 for i in range(n)]

ng_total_tokens = [ng_tokens_a[i]+ng_tokens_b[i] for i in range(n)]
pg_total_tokens = [pg_tokens_a[i]+pg_tokens_b[i] for i in range(n)]

print("=" * 60)
print(f"B2: SELF-HOSTED vLLM EXPERIMENT (N={n})")
print("=" * 60)

for label, ng_arr, pg_arr in [
    ("Success count", ng_success, pg_success),
    ("Partial count", ng_partial, pg_partial),
    ("All rejected", ng_all_rej, pg_all_rej),
    ("Cascade steps", ng_cascade, pg_cascade),
    ("ABD%", ng_abd, pg_abd),
    ("Success rate%", ng_sr, pg_sr),
    ("Eff GP/s", ng_eff_gps, pg_eff_gps),
    ("P95 (ms)", ng_p95, pg_p95),
    ("Total tokens", ng_total_tokens, pg_total_tokens),
]:
    nm, ns = ms(ng_arr)
    pm, ps = ms(pg_arr)
    t = ttest(ng_arr, pg_arr)
    pct = 100*(nm-pm)/nm if nm != 0 else 0
    print(f"\n{label}:")
    print(f"  NG:       {nm:.1f} +/- {ns:.1f}")
    print(f"  PlanGate: {pm:.1f} +/- {ps:.1f}")
    print(f"  Diff:     {nm-pm:+.1f} ({pct:+.1f}%)")
    print(f"  t(4)={t:.2f}")

# Cost efficiency: tokens per successful agent
ng_tps = [ng_total_tokens[i]/ng_success[i] for i in range(n)]
pg_tps = [pg_total_tokens[i]/pg_success[i] for i in range(n)]
print(f"\nTokens per success:")
print(f"  NG:       {ms(ng_tps)[0]:.0f} +/- {ms(ng_tps)[1]:.0f}")
print(f"  PlanGate: {ms(pg_tps)[0]:.0f} +/- {ms(pg_tps)[1]:.0f}")
print(f"  t(4)={ttest(ng_tps, pg_tps):.2f}")

print("\n" + "=" * 60)
print("PAPER TABLE VALUES")
print("=" * 60)
print(f"         Succ  Partial  Rej0  Cascade  ABD%       SR%        GP/s")
nm_s, ns_s = ms(ng_success)
pm_s, ps_s = ms(pg_success)
print(f"NG:      {nm_s:.1f}+/-{ns_s:.1f}  {ms(ng_partial)[0]:.1f}+/-{ms(ng_partial)[1]:.1f}  {ms(ng_all_rej)[0]:.1f}+/-{ms(ng_all_rej)[1]:.1f}  {ms(ng_cascade)[0]:.1f}+/-{ms(ng_cascade)[1]:.1f}  {ms(ng_abd)[0]:.1f}+/-{ms(ng_abd)[1]:.1f}  {ms(ng_sr)[0]:.1f}+/-{ms(ng_sr)[1]:.1f}  {ms(ng_eff_gps)[0]:.2f}")
print(f"PG:      {pm_s:.1f}+/-{ps_s:.1f}  {ms(pg_partial)[0]:.1f}+/-{ms(pg_partial)[1]:.1f}  {ms(pg_all_rej)[0]:.1f}+/-{ms(pg_all_rej)[1]:.1f}  {ms(pg_cascade)[0]:.1f}+/-{ms(pg_cascade)[1]:.1f}  {ms(pg_abd)[0]:.1f}+/-{ms(pg_abd)[1]:.1f}  {ms(pg_sr)[0]:.1f}+/-{ms(pg_sr)[1]:.1f}  {ms(pg_eff_gps)[0]:.2f}")

# df=4: t=2.776 -> p=0.05, t=3.747 -> p=0.02, t=4.604 -> p=0.01
print(f"\nReference: df=4 critical values:")
print(f"  t=2.132 -> p=0.10")
print(f"  t=2.776 -> p=0.05")
print(f"  t=3.747 -> p=0.02")
print(f"  t=4.604 -> p=0.01")
