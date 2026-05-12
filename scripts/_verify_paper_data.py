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
    status = "OK" if match else "MISMATCH"
    return f"  {label:20s}: paper={paper_val:>8.1f}  csv={csv_val:>8.1f}  {'OK' if match else '*** MISMATCH ***'}"

# ============================================================================
print("="*100)
print("TABLE 1: tab:exp1 - Exp1 Core Performance (500 sessions, 5 runs)")
print("="*100)
data = read_csv('results/exp1_core/exp1_core_summary.csv')

paper_exp1 = {
    'ng':            {'Succ':22.2, 'Rej':355.2, 'Casc':122.6, 'GP/s':16.2, 'P50':1008, 'P95':1986, 'JFI':0.929},
    'srl':           {'Succ':40.0, 'Rej':350.4, 'Casc':109.6, 'GP/s':28.3, 'P50':1006, 'P95':1896, 'JFI':0.924},
    'sbac':          {'Succ':58.8, 'Rej':406.4, 'Casc':34.8,  'GP/s':46.4, 'P50':361,  'P95':1416, 'JFI':0.933},
    'plangate_full': {'Succ':72.6, 'Rej':427.4, 'Casc':0.0,   'GP/s':51.9, 'P50':3.9,  'P95':819,  'JFI':0.922},
}

for gw, paper in paper_exp1.items():
    rows = [r for r in data if r['gateway']==gw]
    csv_vals = {
        'Succ': avg([float(r['success']) for r in rows]),
        'Rej':  avg([float(r['rejected_s0']) for r in rows]),
        'Casc': avg([float(r['cascade_failed']) for r in rows]),
        'GP/s': avg([float(r['effective_goodput_s']) for r in rows]),
        'P50':  avg([float(r['p50_ms']) for r in rows]),
        'P95':  avg([float(r['p95_ms']) for r in rows]),
        'JFI':  avg([float(r['jfi_steps']) for r in rows]),
    }
    label = {'ng':'NG','srl':'SRL','sbac':'SBAC','plangate_full':'PlanGate'}[gw]
    print(f"\n{label}:")
    for k in paper:
        tol = 0.005 if k=='JFI' else 1.0 if k in ['P50','P95'] else 0.15
        print(check(k, paper[k], csv_vals[k], tol))

# ============================================================================
print("\n" + "="*100)
print("TABLE 2: tab:ablation - Exp4 Ablation (500 sessions, 5 runs)")
print("="*100)
data = read_csv('results/exp4_ablation/exp4_ablation_summary.csv')

paper_abl = {
    'plangate_full':  {'Succ':77.6, 'Casc':1.2,  'GP/s':57.2, 'P50':5.0, 'P95':978,  'JFI':0.924},
    'wo_budgetlock':  {'Succ':18.4, 'Casc':11.6, 'GP/s':12.3, 'P50':3.7, 'P95':160,  'JFI':0.917},
    'wo_sessioncap':  {'Succ':82.6, 'Casc':1.0,  'GP/s':57.0, 'P50':5.1, 'P95':1080, 'JFI':0.921},
}

for gw, paper in paper_abl.items():
    rows = [r for r in data if r['gateway']==gw]
    csv_vals = {
        'Succ': avg([float(r['success']) for r in rows]),
        'Casc': avg([float(r['cascade_failed']) for r in rows]),
        'GP/s': avg([float(r['effective_goodput_s']) for r in rows]),
        'P50':  avg([float(r['p50_ms']) for r in rows]),
        'P95':  avg([float(r['p95_ms']) for r in rows]),
        'JFI':  avg([float(r['jfi_steps']) for r in rows]),
    }
    print(f"\n{gw}:")
    for k in paper:
        tol = 0.005 if k=='JFI' else 1.0 if k in ['P50','P95'] else 0.15
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
print("TABLE 4: tab:reallm - Real-LLM GLM-4-Flash")
print("="*100)

# Read summary_all.csv from exp_real3_glm
data = read_csv('results/exp_real3_glm/summary_all.csv')

# C=10: the 0412 runs are C=10, 0413 runs are C=40 based on the dates
# Actually need to figure out which runs are C=10 vs C=40
# The file has mcpdp-real-no-sessioncap (PlanGate w/o session cap), mcpdp-real (PlanGate), ng, srl
# Paper says 4 gateways: NG, Rajomon, PP, PlanGate
# But the CSV has ng, srl, mcpdp-real, mcpdp-real-no-sessioncap
# The dates: 0412 = first batch (C=10?), 0413 = second batch (C=40?)
# Let's check - there are 3 runs for 0412 per gw and 2 runs for 0413 per gw (for ng, mcpdp-real-no-sessioncap, mcpdp-real)
# SRL has 3 for 0412 and 2 for 0413

# Actually looking more carefully:
# ng: 0412 runs 1-3 (3 runs), 0413 runs 1-2 (2 runs) = 5 total
# srl: 0412 runs 1-3 (3 runs), 0413 runs 1-2 (2 runs) = 5 total  
# mcpdp-real: 0412 runs 1-3 (3 runs), 0413 runs 1-2 (2 runs) = 5 total
# mcpdp-real-no-sessioncap: 0412 runs 1-3 (3 runs), 0413 runs 1-2 (2 runs) = 5 total

# The paper says C=10 and C=40 concurrency levels
# Looking at 0412 data (first batch) vs 0413 data
# Actually the agents column says 50 for all. The concurrency is not in the CSV.
# Need to check based on dates - 0412 = C=10, 0413 = C=40

# Let me separate by date timestamp
for gw_label, gw_csv in [('NG','ng'), ('Rajomon/SRL','srl'), ('PP/PG-noRes','mcpdp-real-no-sessioncap'), ('PlanGate','mcpdp-real')]:
    rows_c10 = [r for r in data if r['gateway']==gw_csv and '20260412' in r.get('csv','') if 'csv' in r]
    rows_c40 = [r for r in data if r['gateway']==gw_csv and '20260413' in r.get('csv','') if 'csv' in r]
    
    # Actually the CSV might not have a 'csv' column - let me check the structure
    # Looking at the data read earlier, the columns are:
    # gateway,agents,success,partial,all_rejected,cascade_wasted_steps,agent_llm_tokens,backend_llm_tokens,raw_goodput,effective_goodput,eff_gp_per_s,e2e_p50_ms,e2e_p95_ms,elapsed_s
    # There's no csv column or date column. Rows are in order though.
    pass

# The data from exp_real3_glm has these rows for each gateway:
# ng: 5 rows, srl: 5 rows, mcpdp-real: 5 rows, mcpdp-real-no-sessioncap: 5 rows
# The paper says C=10 (5 runs) and C=40 (5 runs) but there are only 5 runs per gateway total
# Wait - looking at dates, there are 5 ng rows (3 from 0412 + 2 from 0413)
# Let me check: the paper table has NG, Rajomon, PP, PlanGate at C=10 and C=40

# The CSV gateways are ng, srl, mcpdp-real, mcpdp-real-no-sessioncap
# These likely map to: NG, SRL (Rajomon proxy?), PlanGate, PlanGate-no-sessioncap(PP?)
# But paper has Rajomon and PP which don't match. This is a different experiment setup.

# Let me just compute means for what we have and report
print("\nNote: exp_real3_glm CSV has gateways: ng, srl, mcpdp-real, mcpdp-real-no-sessioncap")
print("Paper table lists: NG, Rajomon, PP, PlanGate - mapping: ng=NG, srl~Rajomon?, mcpdp-real~PlanGate")
print("There are only 5 rows per gateway (combined), cannot separate C=10 vs C=40 from this CSV alone")
print("Existing data appears to combine both regimes, or may represent just one regime")
print()

for gw in ['ng','srl','mcpdp-real-no-sessioncap','mcpdp-real']:
    rows = [r for r in data if r['gateway']==gw]
    succ_pct = [float(r['success'])/float(r['agents'])*100 for r in rows]
    p95 = [float(r['e2e_p95_ms'])/1000 for r in rows]
    gps = [float(r['eff_gp_per_s']) for r in rows]
    partial = [float(r['partial']) for r in rows]
    print(f"{gw}(n={len(rows)}): Succ%={avg(succ_pct):.1f}+-{sd(succ_pct):.1f}, P95={avg(p95):.1f}s, GP/s={avg(gps):.2f}, PARTIAL={avg(partial):.1f}")

# Paper C=10 claims:
print("\nPaper C=10: NG 96.9%, Raj 97.1%, PP 97.5%, PG 98.3%")
print("Paper C=10 P95: NG 50.8s, Raj 54.5s, PP 52.8s, PG 49.6s")
print("Paper C=40: NG 96.3%, Raj 96.4%, PP 95.7%, PG 96.2%")
print("Paper C=40 P95: NG 56.3s, Raj 59.4s, PP 56.1s, PG 55.5s")

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

print("\nPaper claims:")
print("NG:       PARTIAL=91+-11, Rej0=89+-11, ABD=82.3%, Succ%=9.8, Cascade=163+-25")
print("PlanGate: PARTIAL=75+-12, Rej0=109+-12, ABD=82.7%, Succ%=7.9, Cascade=144+-24")
print("Note: bursty_summary.csv only has 4 rows total (runs 4,5 for ng and plangate_real)")
print("Need to check the per-run directories for complete N=7 data")

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
print("TABLE 7: tab:bursty_longtail - Exp11 and Exp12")
print("="*100)
data11 = read_csv('results/exp11_bursty/exp11_bursty_summary.csv')
data12 = read_csv('results/exp12_longtail/exp12_longtail_summary.csv')

print("\nExp11 (Bursty):")
for gw in ['ng','srl','sbac','plangate_full']:
    rows = [r for r in data11 if r['gateway']==gw]
    succ = avg([float(r['success']) for r in rows])
    succ_sd = sd([float(r['success']) for r in rows])
    casc = avg([float(r['cascade_failed']) for r in rows])
    casc_sd = sd([float(r['cascade_failed']) for r in rows])
    gps = avg([float(r['effective_goodput_s']) for r in rows])
    # ABD = cascade / (success + cascade) * 100
    abd_vals = [float(r['cascade_failed'])/(float(r['success'])+float(r['cascade_failed']))*100 
                if (float(r['success'])+float(r['cascade_failed']))>0 else 0 for r in rows]
    abd = avg(abd_vals)
    label = {'ng':'NG','srl':'SRL','sbac':'SBAC','plangate_full':'PlanGate'}[gw]
    print(f"  {label}: Succ={succ:.1f}+-{succ_sd:.1f}, Casc={casc:.1f}+-{casc_sd:.1f}, ABD={abd:.1f}%, GP/s={gps:.1f}")

print("\nExp12 (Long-Tail):")
for gw in ['ng','srl','sbac','plangate_full']:
    rows = [r for r in data12 if r['gateway']==gw]
    succ = avg([float(r['success']) for r in rows])
    succ_sd = sd([float(r['success']) for r in rows])
    casc = avg([float(r['cascade_failed']) for r in rows])
    casc_sd = sd([float(r['cascade_failed']) for r in rows])
    gps = avg([float(r['effective_goodput_s']) for r in rows])
    abd_vals = [float(r['cascade_failed'])/(float(r['success'])+float(r['cascade_failed']))*100 
                if (float(r['success'])+float(r['cascade_failed']))>0 else 0 for r in rows]
    abd = avg(abd_vals)
    label = {'ng':'NG','srl':'SRL','sbac':'SBAC','plangate_full':'PlanGate'}[gw]
    print(f"  {label}: Succ={succ:.1f}+-{succ_sd:.1f}, Casc={casc:.1f}+-{casc_sd:.1f}, ABD={abd:.1f}%, GP/s={gps:.1f}")

print("\nPaper Exp11 claims:")
print("  NG:       Succ=24.6+-4.2, Casc=98.4+-10.6, ABD=79.9%, GP/s=13.8")
print("  SRL:      Succ=24.6+-2.6, Casc=82.0+-16.9, ABD=76.3%, GP/s=13.5")
print("  SBAC:     Succ=32.0+-2.4, Casc=25.2+-3.9,  ABD=43.9%, GP/s=20.3")
print("  PlanGate: Succ=45.8+-6.6, Casc=0.2+-0.4,   ABD=0.4%,  GP/s=26.9")
print("Paper Exp12 claims:")
print("  NG:       Succ=18.0+-3.2, Casc=91.2+-1.9,  ABD=83.6%, GP/s=14.2")
print("  SRL:      Succ=36.2+-2.9, Casc=93.0+-6.0,  ABD=72.0%, GP/s=28.4")
print("  SBAC:     Succ=43.6+-2.4, Casc=36.0+-3.3,  ABD=45.2%, GP/s=41.4")
print("  PlanGate: Succ=64.6+-6.4, Casc=0.0+-0.0,   ABD=0.0%,  GP/s=60.6")

# ============================================================================
print("\n" + "="*100)
print("TABLE 8: tab:selfhosted - Self-Hosted vLLM")
print("="*100)
data_sh = read_csv('results/exp_selfhosted_vllm_C10_W8/selfhosted_summary.csv')

for gw in ['ng','plangate_real']:
    rows = [r for r in data_sh if r['gateway']==gw]
    succ_pct = [float(r['success'])/float(r['agents'])*100 for r in rows]
    abd = [float(r['abd_total']) for r in rows]
    rej0 = [float(r['all_rejected']) for r in rows]
    cascade = [float(r['cascade_steps']) for r in rows]
    p95 = [float(r['p95_ms'])/1000 for r in rows]
    label = 'PlanGate' if gw=='plangate_real' else 'NG'
    print(f"{label}(n={len(rows)}): Succ%={avg(succ_pct):.1f}+-{sd(succ_pct):.1f}, ABD={avg(abd):.1f}+-{sd(abd):.1f}, Rej0={avg(rej0):.1f}+-{sd(rej0):.1f}, Cascade={avg(cascade):.0f}+-{sd(cascade):.0f}, P95={avg(p95):.0f}+-{sd(p95):.0f}s")

print("\nPaper claims:")
print("NG:       Succ%=52.0+-7.2, ABD=41.8+-7.5, Rej0=5.3+-0.6, Cascade=52+-8, P95=118+-11s")
print("PlanGate: Succ%=40.7+-8.1, ABD=51.7+-9.1, Rej0=8.0+-1.0, Cascade=62+-7, P95=107+-7s")

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
