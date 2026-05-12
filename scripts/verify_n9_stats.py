"""Verify N=9 bursty experiment statistics against paper claims."""
import os, csv, math, statistics

BASE = os.path.join(os.path.dirname(__file__), '..', 'results', 'exp_bursty_C20_B30')

def load_gateway(gw_name):
    rows = []
    gw_dir = os.path.join(BASE, gw_name)
    for i in range(1, 8):
        run_dir = os.path.join(gw_dir, f'run{i}')
        csv_path = os.path.join(run_dir, 'steps_summary.csv')
        if not os.path.exists(csv_path):
            continue
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Handle two CSV formats (run1-5 vs run6-7)
                cascade_col = 'cascade_wasted_steps' if 'cascade_wasted_steps' in row else 'cascade_steps'
                agent_tok = 'agent_llm_tokens' if 'agent_llm_tokens' in row else 'agent_tokens'
                backend_tok = 'backend_llm_tokens' if 'backend_llm_tokens' in row else 'backend_tokens'
                p50_col = 'e2e_p50_ms' if 'e2e_p50_ms' in row else 'p50_ms'
                p95_col = 'e2e_p95_ms' if 'e2e_p95_ms' in row else 'p95_ms'
                rows.append({
                    'run': i,
                    'success': int(row['success']),
                    'partial': int(row['partial']),
                    'all_rejected': int(row['all_rejected']),
                    'cascade': int(row[cascade_col]),
                    'agent_tokens': int(row[agent_tok]),
                    'backend_tokens': int(row[backend_tok]),
                    'e2e_p50': float(row[p50_col]),
                    'e2e_p95': float(row[p95_col]),
                })
    return rows

def stats(arr):
    m = statistics.mean(arr)
    s = statistics.stdev(arr) if len(arr) > 1 else 0
    return m, s

def ttest_ind(a, b):
    na, nb = len(a), len(b)
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = statistics.variance(a) if na > 1 else 0
    vb = statistics.variance(b) if nb > 1 else 0
    sp = math.sqrt(((na-1)*va + (nb-1)*vb) / (na+nb-2))
    se = sp * math.sqrt(1/na + 1/nb)
    t_val = (ma - mb) / se if se > 0 else float('inf')
    df = na + nb - 2
    return t_val, df

ng = load_gateway('ng')
pg = load_gateway('plangate_real')

print(f"NG data points: {len(ng)}")
print(f"PG data points: {len(pg)}")
print()

# Print raw data
for label, data in [("NG", ng), ("PG", pg)]:
    print(f"--- {label} raw data ---")
    for d in data:
        print(f"  run{d['run']}: success={d['success']}, partial={d['partial']}, "
              f"rej0={d['all_rejected']}, cascade={d['cascade']}")
    print()

# Compute statistics
ng_partial = [d['partial'] for d in ng]
pg_partial = [d['partial'] for d in pg]
ng_cascade = [d['cascade'] for d in ng]
pg_cascade = [d['cascade'] for d in pg]
ng_rej0 = [d['all_rejected'] for d in ng]
pg_rej0 = [d['all_rejected'] for d in pg]
ng_succ = [d['success'] for d in ng]
pg_succ = [d['success'] for d in pg]

print("=" * 60)
print("STATISTICS COMPARISON (Computed vs Paper)")
print("=" * 60)

metrics = [
    ("PARTIAL", ng_partial, pg_partial, "95+-12", "75+-10", "-21.1%", "t(16)=3.81"),
    ("Cascade", ng_cascade, pg_cascade, "174+-33", "143+-22", "-17.9%", "t(16)=2.39"),
    ("Rej0", ng_rej0, pg_rej0, "84+-13", "108+-10", None, "t(16)=4.29"),
    ("Success", ng_succ, pg_succ, None, None, None, None),
]

for name, ng_arr, pg_arr, paper_ng, paper_pg, paper_red, paper_t in metrics:
    ng_m, ng_s = stats(ng_arr)
    pg_m, pg_s = stats(pg_arr)
    t_val, df = ttest_ind(ng_arr, pg_arr)
    reduction = (ng_m - pg_m) / ng_m * 100 if ng_m != 0 else 0
    print(f"\n{name}:")
    print(f"  Computed NG: {ng_m:.1f} +- {ng_s:.1f}  (rounded: {round(ng_m)} +- {round(ng_s)})")
    print(f"  Computed PG: {pg_m:.1f} +- {pg_s:.1f}  (rounded: {round(pg_m)} +- {round(pg_s)})")
    print(f"  Computed reduction: {reduction:.1f}%")
    print(f"  Computed t({df}) = {t_val:.2f}")
    if paper_ng:
        print(f"  Paper NG: {paper_ng}")
    if paper_pg:
        print(f"  Paper PG: {paper_pg}")
    if paper_red:
        print(f"  Paper reduction: {paper_red}")
    if paper_t:
        print(f"  Paper t: {paper_t}")

# ABD% = partial / (partial + success) * 100
ng_abd = [d['partial'] / (d['partial'] + d['success']) * 100 if (d['partial'] + d['success']) > 0 else 0 for d in ng]
pg_abd = [d['partial'] / (d['partial'] + d['success']) * 100 if (d['partial'] + d['success']) > 0 else 0 for d in pg]
ng_abd_m, ng_abd_s = stats(ng_abd)
pg_abd_m, pg_abd_s = stats(pg_abd)
t_abd, df_abd = ttest_ind(ng_abd, pg_abd)
print(f"\nABD%:")
print(f"  Computed NG: {ng_abd_m:.1f} +- {ng_abd_s:.1f}")
print(f"  Computed PG: {pg_abd_m:.1f} +- {pg_abd_s:.1f}")
print(f"  Computed t({df_abd}) = {t_abd:.2f}")
print(f"  Paper NG: 82.1+-3.5")
print(f"  Paper PG: 81.6+-5.2")

# Success% = success / 200 * 100
ng_sp = [d['success'] / 200 * 100 for d in ng]
pg_sp = [d['success'] / 200 * 100 for d in pg]
ng_sp_m, ng_sp_s = stats(ng_sp)
pg_sp_m, pg_sp_s = stats(pg_sp)
t_sp, df_sp = ttest_ind(ng_sp, pg_sp)
print(f"\nSuccess%:")
print(f"  Computed NG: {ng_sp_m:.1f} +- {ng_sp_s:.1f}")
print(f"  Computed PG: {pg_sp_m:.1f} +- {pg_sp_s:.1f}")
print(f"  Computed t({df_sp}) = {t_sp:.2f}")
print(f"  Paper NG: 10.3+-2.4")
print(f"  Paper PG: 8.4+-2.5")

# Token efficiency
ng_tokens = [d['agent_tokens'] + d['backend_tokens'] for d in ng]
pg_tokens = [d['agent_tokens'] + d['backend_tokens'] for d in pg]
ng_tok_m, ng_tok_s = stats(ng_tokens)
pg_tok_m, pg_tok_s = stats(pg_tokens)
print(f"\nTotal Tokens (agent+backend):")
print(f"  NG: {ng_tok_m:.0f} +- {ng_tok_s:.0f}")
print(f"  PG: {pg_tok_m:.0f} +- {pg_tok_s:.0f}")

# P50/P95 latency
ng_p50 = [d['e2e_p50'] for d in ng]
pg_p50 = [d['e2e_p50'] for d in pg]
ng_p95 = [d['e2e_p95'] for d in ng]
pg_p95 = [d['e2e_p95'] for d in pg]
print(f"\nLatency:")
print(f"  NG P50: {statistics.mean(ng_p50):.0f} ms")
print(f"  PG P50: {statistics.mean(pg_p50):.0f} ms")
print(f"  NG P95: {statistics.mean(ng_p95):.0f} ms")
print(f"  PG P95: {statistics.mean(pg_p95):.0f} ms")

print("\n\n=== DONE ===")
