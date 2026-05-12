"""
Data consistency check: v4 paper vs experimental data.
Run from repo root: python scripts/_consistency_check.py
"""
import csv, statistics, os, glob

def avg(lst): return sum(lst)/len(lst) if lst else float('nan')
def stdev(lst): return statistics.stdev(lst) if len(lst)>1 else 0.0

# ========================
# 1. exp1_core summary (Mock Exp1: 500 sessions, 200 concurrency)
# Paper claims: NG cascade=122.6, SRL cascade=109.6, SBAC GP/s=46.4, PlanGate GP/s=51.9, PlanGate cascade=0
# ========================
print("=" * 70)
print("1. exp1_core MOCK EXPERIMENT (500 sessions, 200 concurrency)")
print("   Paper: NG cascade=122.6, SRL cascade=109.6, SBAC GP/s=46.4, PlanGate GP/s=51.9, PlanGate cascade=0")
print("-" * 70)
data = {}
with open('results/exp1_core/exp1_core_summary.csv') as f:
    for row in csv.DictReader(f):
        gw = row['gateway']
        if gw not in data:
            data[gw] = []
        data[gw].append(row)

for gw, rows in data.items():
    succ = avg([int(r['success']) for r in rows])
    casc = avg([int(r['cascade_failed']) for r in rows])
    gp_s = avg([float(r['effective_goodput_s']) for r in rows])
    abd_list = [int(r['cascade_failed'])/(int(r['success'])+int(r['cascade_failed']))*100
                if int(r['success'])+int(r['cascade_failed']) > 0 else 0 for r in rows]
    abd = avg(abd_list)
    print(f"  {gw:20s}: succ={succ:.1f}, cascade={casc:.1f}, GP/s={gp_s:.1f}, ABD={abd:.1f}%")

# ========================
# 2. exp_week4_formal (Table 1 data: 200 sessions x 5 runs, 7 gateways)
# Paper Table 1: NG ABD=65.5%±4.8, Rajomon ABD=65.4%, SBAC ABD=56.0%, PlanGate ABD=18.9%, ABD_PS=0.0%, ABD_ReAct=21.7%
# ========================
print()
print("=" * 70)
print("2. exp_week4_formal (Table 1: commitment quality)")
print("   Paper: NG ABD=65.5%±4.8, Rajomon ABD=65.4%, SBAC ABD=56.0%±3.6,")
print("          PlanGate ABD=18.9%±7.8, ABD_PS=0.0%, ABD_ReAct=21.7%, GP/s=50.4")
print("   NG Succ%=11.5±1.7, Rajomon GP/s=25.5, SBAC GP/s=32.3, PlanGate Succ%=17.3±0.6")
print("-" * 70)

base = 'results/exp_week4_formal'
gw_map = {
    'ng': 'NG',
    'rajomon': 'Rajomon',
    'rajomon_sb': 'Rajomon+SB',
    'sbac': 'SBAC',
    'pp': 'Prog.Priority',
    'pg_nores': 'PG-noRes',
    'plangate_full': 'PlanGate',
}
for gw_dir_name, gw_label in gw_map.items():
    gw_path = os.path.join(base, gw_dir_name)
    if not os.path.exists(gw_path):
        print(f"  {gw_label:15s}: DIR NOT FOUND")
        continue
    all_succ = []; all_casc = []; all_rej = []
    all_abd = []; all_abd_ps = []; all_abd_react = []
    all_gp = []; run_total = 0
    for run in range(1, 6):
        sess_file = os.path.join(gw_path, f'run{run}', 'steps_sessions.csv')
        if not os.path.exists(sess_file):
            continue
        ps_succ = ps_casc = react_succ = react_casc = 0
        total_succ = total_casc = total_rej = 0
        gp = 0.0
        with open(sess_file) as f:
            for row in csv.DictReader(f):
                mode = row['mode']
                state = row['state']
                gp_val = float(row.get('effective_goodput', 0))
                if state == 'SUCCESS':
                    total_succ += 1; gp += gp_val
                    if mode == 'plan_and_solve':
                        ps_succ += 1
                    else:
                        react_succ += 1
                elif state == 'PARTIAL':
                    total_casc += 1
                    if mode == 'plan_and_solve':
                        ps_casc += 1
                    else:
                        react_casc += 1
                elif 'REJECT' in state:
                    total_rej += 1
        total_sessions = total_succ + total_casc + total_rej
        all_succ.append(total_succ)
        all_casc.append(total_casc)
        all_rej.append(total_rej)
        abd_t = total_casc/(total_succ+total_casc)*100 if total_succ+total_casc > 0 else 0
        abd_ps_v = ps_casc/(ps_succ+ps_casc)*100 if ps_succ+ps_casc > 0 else 0
        abd_r_v = react_casc/(react_succ+react_casc)*100 if react_succ+react_casc > 0 else 0
        all_abd.append(abd_t)
        all_abd_ps.append(abd_ps_v)
        all_abd_react.append(abd_r_v)
        all_gp.append(gp)
        run_total = total_sessions

    if all_succ:
        total_sessions_avg = avg(all_succ) + avg(all_casc) + avg(all_rej)
        succ_pct = avg(all_succ)/total_sessions_avg*100 if total_sessions_avg > 0 else 0
        # Goodput per second: need experiment duration. Approximate from steps.
        # Just report total GP and note it can't compute per-second without wall time
        print(f"  {gw_label:15s}: Succ%={succ_pct:.1f}({avg(all_succ):.1f}/{total_sessions_avg:.0f}), "
              f"ABD={avg(all_abd):.1f}%+/-{stdev(all_abd):.1f}, "
              f"ABD_PS={avg(all_abd_ps):.1f}%, ABD_R={avg(all_abd_react):.1f}%")
    else:
        print(f"  {gw_label:15s}: no run data found")

# ========================
# 3. exp_rajomon_sensitivity (price_step scan)
# Paper: best ABD=64.4% at price_step=5; NG ABD=65.5%; price_step>=20 -> ABD>89%
# ========================
print()
print("=" * 70)
print("3. exp_rajomon_sensitivity")
print("   Paper: price_step=5 ABD=64.4%, NG ABD=65.5%, price_step>=20 ABD>89%")
print("-" * 70)
ps_data = {}
with open('results/exp_rajomon_sensitivity/rajomon_sensitivity.csv') as f:
    for row in csv.DictReader(f):
        ps = int(row['price_step'])
        if ps not in ps_data:
            ps_data[ps] = []
        ps_data[ps].append(float(row['abd_total']))
for ps in sorted(ps_data.keys()):
    vals = ps_data[ps]
    print(f"  price_step={ps:3d}: ABD avg={avg(vals):.1f}% (min={min(vals):.1f}, max={max(vals):.1f})")

# ========================
# 4. exp8_discountablation (discount function ablation)
# Paper: K^2 cascade=15.8, exp=11.8, linear=21.0, log=31.2
# ========================
print()
print("=" * 70)
print("4. exp8_discountablation (discount function ablation)")
print("   Paper: K^2(quadratic) cascade=15.8, exponential=11.8, linear=21.0, log=31.2")
print("-" * 70)
disc_data = {}
with open('results/exp8_discountablation/exp8_discountablation_summary.csv') as f:
    for row in csv.DictReader(f):
        gw = row['gateway']
        if gw not in disc_data:
            disc_data[gw] = []
        disc_data[gw].append(int(row['cascade_failed']))
for gw, vals in disc_data.items():
    print(f"  {gw:30s}: cascade avg={avg(vals):.1f} (runs: {vals})")

# ========================
# 5. exp10_adversarial (adversarial robustness)
# Paper: PlanGate 72.6 tasks, NG 28.8, PlanGate cascade=1.0
# ========================
print()
print("=" * 70)
print("5. exp10_adversarial")
print("   Paper: PlanGate succ=72.6, NG succ=28.8, cascade NG=119.4, PlanGate cascade=1.0, PlanGate GP/s=58.0")
print("-" * 70)
adv_data = {}
with open('results/exp10_adversarial/exp10_adversarial_summary.csv') as f:
    for row in csv.DictReader(f):
        gw = row['gateway']
        if gw not in adv_data:
            adv_data[gw] = []
        adv_data[gw].append(row)
for gw, rows in adv_data.items():
    succ = avg([int(r['success']) for r in rows])
    casc = avg([int(r['cascade_failed']) for r in rows])
    gp_s = avg([float(r['effective_goodput_s']) for r in rows])
    print(f"  {gw:20s}: succ={succ:.1f}, cascade={casc:.1f}, GP/s={gp_s:.1f}")

# ========================
# 6. exp11_bursty (bursty mock)
# Paper: PlanGate cascade=0, mock bursty ABD=0.4%
# ========================
print()
print("=" * 70)
print("6. exp11_bursty (mock bursty robustness setting)")
print("   Paper: mentions ABD=0.4% in mock bursty setting")
print("-" * 70)
bursty_data = {}
with open('results/exp11_bursty/exp11_bursty_summary.csv') as f:
    for row in csv.DictReader(f):
        gw = row['gateway']
        if gw not in bursty_data:
            bursty_data[gw] = []
        bursty_data[gw].append(row)
for gw, rows in bursty_data.items():
    succ = avg([int(r['success']) for r in rows])
    casc = avg([int(r['cascade_failed']) for r in rows])
    admitted = avg([int(r['success'])+int(r['cascade_failed']) for r in rows])
    abd = avg([int(r['cascade_failed'])/(int(r['success'])+int(r['cascade_failed']))*100
               if int(r['success'])+int(r['cascade_failed']) > 0 else 0 for r in rows])
    print(f"  {gw:20s}: succ={succ:.1f}, cascade={casc:.1f}, ABD={abd:.2f}%")

# ========================
# 7. Bursty real-LLM experiments (exp_week5_real_llm or similar)
# Paper: PARTIAL -21.1% (p<0.002), cascade -17.9% (p<0.05), from 174±33 to 143±22
# ========================
print()
print("=" * 70)
print("7. Bursty real-LLM experiments")
print("   Paper: PARTIAL reduction -21.1% p<0.002, cascade -17.9% p<0.05 (174+/-33 -> 143+/-22)")
print("          Token: 6120 vs 6788 NG, waste 28.8% -> 21.0%")
print("-" * 70)

# Look for real LLM bursty data
for d in ['exp_week5_real_llm', 'exp_real3_deepseek', 'exp_real3_glm', 'exp_week5_pilot']:
    path = f'results/{d}'
    if os.path.exists(path):
        files = os.listdir(path)
        print(f"  {d}: {files[:5]}...")

# ========================
# 8. Steady real-LLM P95 tail latency (C=10 and C=40)
# Paper: C=10 PlanGate P95=49.6s, baselines 50.8-54.5s; C=40 PlanGate 55.5s, baselines 56.1-59.4s
# ========================
print()
print("=" * 70)
print("8. Steady real-LLM P95 latency")
print("   Paper: C=10 PlanGate P95=49.6s, baselines 50.8-54.5s")
print("          C=40 PlanGate P95=55.5s, baselines 56.1-59.4s")
print("          C=10 success 95.2-96.1%, C=40 success 95.7-96.4%")
print("-" * 70)
for d in ['exp_week5_C10', 'exp_week5_C40']:
    path = f'results/{d}'
    if os.path.exists(path):
        print(f"  {d}:")
        summary = os.path.join(path, 'week5_summary.csv')
        if os.path.exists(summary):
            with open(summary) as f:
                rows = list(csv.DictReader(f))
            gw_data = {}
            for row in rows:
                gw = row.get('gateway', row.get('system', ''))
                if gw not in gw_data:
                    gw_data[gw] = []
                gw_data[gw].append(row)
            for gw, gw_rows in gw_data.items():
                # Print available fields
                if gw_rows:
                    print(f"    Fields: {list(gw_rows[0].keys())}")
                    break
            break
        else:
            print(f"    Files: {os.listdir(path)[:8]}")

# ========================
# 9. Self-hosted vLLM C=20 (exp_selfhosted_vllm_C20_W8)
# Paper: NG 12.3±2.9%, PlanGate 8.7±3.2%, cascade 85±9 vs 98±21, PARTIAL 43 vs 47
# ========================
print()
print("=" * 70)
print("9. Self-hosted vLLM C=20 (high contention)")
print("   Paper: NG success=12.3%+/-2.9, PlanGate=8.7%+/-3.2, cascade 85+/-9 vs 98+/-21")
print("-" * 70)
vllm_path = 'results/exp_selfhosted_vllm_C20_W8'
if os.path.exists(vllm_path):
    print(f"  Files: {os.listdir(vllm_path)[:8]}")
else:
    print(f"  DIR NOT FOUND: {vllm_path}")
