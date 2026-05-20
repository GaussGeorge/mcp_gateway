"""Verify paper tables against CSV data. Read-only, no modifications."""
import csv, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read_csv(relpath):
    with open(os.path.join(BASE, relpath), 'r') as f:
        return list(csv.DictReader(f))

def avg(vals): return sum(vals)/len(vals) if vals else 0
def sd(vals):
    if len(vals)<2: return 0
    m=avg(vals); return (sum((v-m)**2 for v in vals)/(len(vals)-1))**0.5

def check(label, paper_val, csv_val, tol=0.15):
    match = abs(paper_val - csv_val) <= tol
    return f"  {label:22s}: paper={paper_val:>8.1f}  csv={csv_val:>8.1f}  {'OK' if match else '*** MISMATCH ***'}"

# ============================================================================
print("="*100)
print("TABLE 1: tab:exp1 - Exp1 Core Performance (500 sessions, 5 runs)")
print("="*100)
data = read_csv('results/exp1_core/exp1_core_summary.csv')

paper_exp1 = {
    'ng':            {'Succ':22.6, 'Rej(s0)':361.8, 'Casc':115.6, 'GP/s':17.3, 'P50':1009, 'P95':1975},
    'srl':           {'Succ':39.4, 'Rej(s0)':354.0, 'Casc':106.6, 'GP/s':29.0, 'P50':1007, 'P95':1900},
    'sbac':          {'Succ':55.2, 'Rej(s0)':411.2, 'Casc':33.6,  'GP/s':48.0, 'P50':287,  'P95':1378},
    'plangate_full': {'Succ':73.0, 'Rej(s0)':427.0, 'Casc':0.0,   'GP/s':55.6, 'P50':3.8,  'P95':819},
}

for gw, paper in paper_exp1.items():
    rows = [r for r in data if r['gateway']==gw]
    has_jfi = rows and 'jfi_steps' in rows[0]
    csv_vals = {
        'Succ':    avg([float(r['success']) for r in rows]),
        'Rej(s0)': avg([float(r['rejected_s0']) for r in rows]),
        'Casc':    avg([float(r['cascade_failed']) for r in rows]),
        'GP/s':    avg([float(r['effective_goodput_s']) for r in rows]),
        'P50':     avg([float(r['p50_ms']) for r in rows]),
        'P95':     avg([float(r['p95_ms']) for r in rows]),
    }
    if has_jfi:
        csv_vals['JFI'] = avg([float(r['jfi_steps']) for r in rows])
    label = {'ng':'NG','srl':'SRL','sbac':'SBAC','plangate_full':'PlanGate'}[gw]
    print(f"\n{label} (n_runs={len(rows)}):")
    for k in paper:
        if k == 'JFI' and not has_jfi:
            print(f"  {'JFI':20s}: paper={paper[k]:>8.3f}  csv=  (N/A - not in regenerated summary)")
            continue
        tol = 0.005 if k=='JFI' else 5.0 if k in ['P50','P95'] else 2.0
        print(check(k, paper[k], csv_vals[k], tol))

# ============================================================================
print("\n" + "="*100)
print("TABLE 2: tab:ablation - Exp4 Ablation (500 sessions, 5 runs)")
print("="*100)
data = read_csv('results/exp4_ablation/exp4_ablation_summary.csv')

paper_abl = {
    'plangate_full':  {'Succ':81.0, 'Casc':1.4,  'GP/s':59.3, 'P50':5.2, 'P95':1009},
    'wo_budgetlock':  {'Succ':19.0, 'Casc':11.0, 'GP/s':12.1, 'P50':3.5, 'P95':160},
    'wo_sessioncap':  {'Succ':81.6, 'Casc':1.0,  'GP/s':59.1, 'P50':4.9, 'P95':1076},
}

for gw, paper in paper_abl.items():
    rows = [r for r in data if r['gateway']==gw]
    has_jfi = rows and 'jfi_steps' in rows[0]
    csv_vals = {
        'Succ': avg([float(r['success']) for r in rows]),
        'Casc': avg([float(r['cascade_failed']) for r in rows]),
        'GP/s': avg([float(r['effective_goodput_s']) for r in rows]),
        'P50':  avg([float(r['p50_ms']) for r in rows]),
        'P95':  avg([float(r['p95_ms']) for r in rows]),
    }
    if has_jfi:
        csv_vals['JFI'] = avg([float(r['jfi_steps']) for r in rows])
    print(f"\n{gw} (n_runs={len(rows)}):")
    for k in paper:
        if k == 'JFI' and not has_jfi:
            print(f"  {'JFI':20s}: paper={paper[k]:>8.3f}  csv=  (N/A)")
            continue
        tol = 0.005 if k=='JFI' else 5.0 if k in ['P50','P95'] else 2.0
        print(check(k, paper[k], csv_vals[k], tol))

# ============================================================================
print("\n" + "="*100)
print("TABLE 3: tab:discount - Exp8 Discount Function (500 sessions, 5 runs)")
print("="*100)
data = read_csv('results/exp8_discountablation/exp8_discountablation_summary.csv')

paper_disc = {
    'plangate_quadratic':   {'Casc':15.8, 'GP/s':23.3, 'P95':843, 'JFI':0.763},
    'plangate_linear':      {'Casc':21.0, 'GP/s':23.3, 'P95':842, 'JFI':0.776},
    'plangate_exponential': {'Casc':11.8, 'GP/s':23.7, 'P95':828, 'JFI':0.772},
    'plangate_logarithmic': {'Casc':31.2, 'GP/s':21.0, 'P95':831, 'JFI':0.735},
}

for gw, paper in paper_disc.items():
    rows = [r for r in data if r['gateway']==gw]
    csv_vals = {
        'Casc': avg([float(r['cascade_failed']) for r in rows]),
        'GP/s': avg([float(r['effective_goodput_s']) for r in rows]),
        'P95':  avg([float(r['p95_ms']) for r in rows]),
        'JFI':  avg([float(r['jfi_steps']) for r in rows]),
    }
    print(f"\n{gw}:")
    for k in paper:
        tol = 0.005 if k=='JFI' else 1.0 if k in ['P95'] else 0.15
        print(check(k, paper[k], csv_vals[k], tol))

# ============================================================================
print("\n" + "="*100)
print("TABLE 4: tab:reallm - Real-LLM GLM-4-Flash (exp_week5 C=10 and C=40)")
print("="*100)
data_c10 = read_csv('results/exp_week5_C10/week5_summary.csv')
data_c40 = read_csv('results/exp_week5_C40/week5_summary.csv')

for label, gw in [('NG','ng'), ('Rajomon','rajomon'), ('PP','pp'), ('PlanGate','plangate_real')]:
    rows10 = [r for r in data_c10 if r['gateway']==gw]
    rows40 = [r for r in data_c40 if r['gateway']==gw]
    sr10  = avg([float(r['success_rate']) for r in rows10])
    sr40  = avg([float(r['success_rate']) for r in rows40])
    p95_10 = avg([float(r['p95_ms'])/1000 for r in rows10])
    p95_40 = avg([float(r['p95_ms'])/1000 for r in rows40])
    gps10 = avg([float(r['eff_gps']) for r in rows10])
    gps40 = avg([float(r['eff_gps']) for r in rows40])
    print(f"  {label}(n={len(rows10)}/{len(rows40)}): "
          f"C=10 Succ%={sr10:.1f}, P95={p95_10:.1f}s, GP/s={gps10:.2f} | "
          f"C=40 Succ%={sr40:.1f}, P95={p95_40:.1f}s, GP/s={gps40:.2f}")

print("\nPaper claims (v7 corrected):")
print("  C=10: NG 94.0%, Raj 94.0%, PP 92.1%, PlanGate 88.4%")
print("  C=10 P95: NG 80.5s, Raj 73.1s, PP 75.7s, PlanGate 87.4s")
print("  C=40: NG 96.3%, Raj 96.4%, PP 95.7%, PlanGate 96.2%")
print("  C=40 P95: NG 56.3s, Raj 59.4s, PP 56.1s, PlanGate 55.5s")

# ============================================================================
print("\n" + "="*100)
print("TABLE 5: tab:bursty_reallm - Bursty Real-LLM")
print("="*100)
data = read_csv('results/exp_bursty_C20_B30/bursty_summary.csv')

# Count rows per gateway
for gw in set(r['gateway'] for r in data):
    rows = [r for r in data if r['gateway']==gw]
    partial = [float(r['partial']) for r in rows if 'partial' in r]
    rej0 = [float(r.get('all_rejected',0)) for r in rows]
    succ_pct = [float(r['success_rate']) for r in rows]
    cascade = [float(r['cascade_steps']) for r in rows]
    abd = [float(r['abd_total']) for r in rows]
    print(f"{gw}(n={len(rows)}): PARTIAL={avg(partial):.0f}+-{sd(partial):.0f}, Rej0_actual=check_col, ABD={avg(abd):.1f}%, Succ%={avg(succ_pct):.1f}, Cascade={avg(cascade):.0f}+-{sd(cascade):.0f}")

print("\nPaper claims (N=7):")
print("NG:       PARTIAL=94+-14, Rej0=86+-15, ABD=82.5%, Succ%=10.0, Cascade=174+-38")
print("PlanGate: PARTIAL=76+-12, Rej0=108+-12, ABD=82.4%, Succ%=8.1, Cascade=143+-25")

# ============================================================================
print("\n" + "="*100)
print("TABLE 6: tab:scalestress - Exp9 High-Concurrency (200-1000)")
print("="*100)
data9 = read_csv('results/exp9_scalestress/exp9_scalestress_summary.csv')

print("\nPlanGate Cascade Failures:")
for conc in [200,400,600,800,1000]:
    rows = [r for r in data9 if r['gateway']=='plangate_full' and int(r['sweep_val'])==conc]
    casc = avg([float(r['cascade_failed']) for r in rows])
    gps = avg([float(r['effective_goodput_s']) for r in rows])
    print(f"  C={conc}: Casc={casc:.1f}, GP/s={gps:.1f}")

print("\nNG Cascade Failures:")
for conc in [200,400,600,800,1000]:
    rows = [r for r in data9 if r['gateway']=='ng' and int(r['sweep_val'])==conc]
    casc = avg([float(r['cascade_failed']) for r in rows])
    print(f"  C={conc}: Casc={casc:.1f}")

print("\nSBAC Cascade Failures:")
for conc in [200,400,600,800,1000]:
    rows = [r for r in data9 if r['gateway']=='sbac' and int(r['sweep_val'])==conc]
    casc = avg([float(r['cascade_failed']) for r in rows])
    print(f"  C={conc}: Casc={casc:.1f}")

print("\nPaper claims:")
print("PG Casc:   0.0, 0.8, 0.4, 0.6, 0.4")
print("PG GP/s:   60.5, 56.7, 60.8, 59.7, 52.0")
print("NG Casc:   120.0, 123.2, 124.2, 121.4, 126.2")
print("SBAC Casc: 34.8, 39.2, 38.0, 38.0, 35.6")

# ============================================================================
print("\n" + "="*100)
print("TABLE 7: tab:selfhosted - Self-Hosted vLLM (C=20, N=3)")
print("  NOTE: C=10 CSV deprecated — those runs used Brain=qwen (env override), not GLM-4-Flash.")
print("="*100)
data_sh = read_csv('results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv')

for gw in ['ng','plangate_real']:
    rows = [r for r in data_sh if r['gateway']==gw]
    succ_pct  = [float(r['success_rate']) for r in rows]
    cascade   = [float(r['cascade_steps']) for r in rows]
    rej0      = [float(r['all_rejected']) for r in rows]
    p95       = [float(r['p95_ms'])/1000 for r in rows]
    label = 'PlanGate' if gw=='plangate_real' else 'NG'
    print(f"{label}(n={len(rows)}): Succ%={avg(succ_pct):.1f}+-{sd(succ_pct):.1f}, Cascade={avg(cascade):.0f}+-{sd(cascade):.0f}, Rej0={avg(rej0):.1f}+-{sd(rej0):.1f}, P95={avg(p95):.0f}+-{sd(p95):.0f}s")

print("\nPaper claims:")
print("NG:       Succ%=12.3+-2.9, Cascade=98+-21, Rej0=40.7, P95=101s")
print("PlanGate: Succ%=8.7+-3.2,  Cascade=85+-9,  Rej0=48.3, P95=117s")

# ============================================================================
print("\n" + "="*100)
print("TABLE 9: tab:adversarial - Exp10 Adversarial (500 sessions)")
print("="*100)
data10 = read_csv('results/exp10_adversarial/exp10_adversarial_summary.csv')

paper_adv = {
    'ng':            {'Succ':28.8, 'Casc':119.4, 'GP/s':18.8, 'JFI':0.762},
    'srl':           {'Succ':38.0, 'Casc':113.2, 'GP/s':27.3, 'JFI':0.706},
    'sbac':          {'Succ':52.0, 'Casc':39.4,  'GP/s':41.8, 'JFI':0.748},
    'plangate_full': {'Succ':72.6, 'Casc':1.0,   'GP/s':58.0, 'JFI':0.656},
}

for gw, paper in paper_adv.items():
    rows = [r for r in data10 if r['gateway']==gw]
    csv_vals = {
        'Succ': avg([float(r['success']) for r in rows]),
        'Casc': avg([float(r['cascade_failed']) for r in rows]),
        'GP/s': avg([float(r['effective_goodput_s']) for r in rows]),
        'JFI':  avg([float(r['jfi_steps']) for r in rows]),
    }
    label = {'ng':'NG','srl':'SRL','sbac':'SBAC','plangate_full':'PlanGate'}[gw]
    print(f"\n{label}:")
    for k in paper:
        tol = 0.005 if k=='JFI' else 0.15
        print(check(k, paper[k], csv_vals[k], tol))

# ============================================================================
print("\n" + "="*100)
print("TABLE 10: tab:commitment-quality - Week4 Formal Experiment")
print("="*100)
dataw4 = read_csv('results/exp_week4_formal/week2_smoke_summary.csv')

paper_cq = {
    'ng':            {'Succ%':11.5, 'ABD_total':65.5, 'ABD_PS':67.2, 'ABD_React':65.3, 'GP/s':24.4},
    'rajomon':       {'Succ%':11.3, 'ABD_total':65.4, 'ABD_PS':64.7, 'ABD_React':66.3, 'GP/s':25.5},
    'rajomon_sb':    {'Succ%':11.6, 'ABD_total':64.7, 'ABD_PS':68.7, 'ABD_React':61.4, 'GP/s':24.1},
    'sbac':          {'Succ%':13.4, 'ABD_total':56.0, 'ABD_PS':58.4, 'ABD_React':54.1, 'GP/s':32.3},
    'pp':            {'Succ%':11.3, 'ABD_total':62.9, 'ABD_PS':60.4, 'ABD_React':65.1, 'GP/s':28.2},
    'pg_nores':      {'Succ%':12.5, 'ABD_total':27.8, 'ABD_PS':81.6, 'ABD_React':15.6, 'GP/s':48.6},
    'plangate_full': {'Succ%':17.3, 'ABD_total':18.9, 'ABD_PS':0.0,  'ABD_React':21.7, 'GP/s':50.4},
}

for gw, paper in paper_cq.items():
    rows = [r for r in dataw4 if r['gateway']==gw]
    if not rows:
        print(f"\n{gw}: NO DATA IN CSV")
        continue
    csv_vals = {
        'Succ%':     avg([float(r['success_rate']) for r in rows]),
        'ABD_total': avg([float(r['abd_total']) for r in rows]),
        'ABD_PS':    avg([float(r['abd_ps']) for r in rows]),
        'ABD_React': avg([float(r['abd_react']) for r in rows]),
        'GP/s':      avg([float(r['goodput']) for r in rows]),
    }
    print(f"\n{gw}(n={len(rows)}):")
    for k in paper:
        tol = 0.5 if 'ABD' in k else 0.2
        print(check(k, paper[k], csv_vals[k], tol))

# ============================================================================
# Check bursty N=7 data by reading individual run dirs
print("\n" + "="*100)
print("BURSTY REALLM - Checking per-run directories for N=7")
print("="*100)

for gw_dir, gw_label in [('ng','NG'), ('plangate_real','PlanGate')]:
    base_dir = os.path.join(BASE, 'results', 'exp_bursty_C20_B30', gw_dir)
    if os.path.isdir(base_dir):
        run_dirs = sorted([d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])
        print(f"\n{gw_label}: found {len(run_dirs)} run directories: {run_dirs}")
        # Try to find summary files in each run dir
        for rd in run_dirs[:2]:
            rdp = os.path.join(base_dir, rd)
            files = os.listdir(rdp)
            print(f"  {rd}: {[f for f in files if f.endswith('.csv')][:5]}")
    else:
        print(f"\n{gw_label}: directory {base_dir} not found")

print("\n\nDONE - Verification complete")
