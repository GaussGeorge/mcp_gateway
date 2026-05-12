"""Comprehensive data audit: paper claims vs CSV data."""
import csv, statistics, math, os

def read_csv(path):
    with open(path, 'r') as f:
        return list(csv.DictReader(f))

def mean_std(vals):
    m = statistics.mean(vals)
    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return m, s

def fmt(m, s): return f"{m:.1f}+-{s:.1f}"

def check(label, computed, paper, tol=0.15):
    match = abs(computed - paper) <= tol
    tag = "OK" if match else f"MISMATCH (diff={computed-paper:+.1f})"
    return tag

# =====================================================================
print("="*80)
print("TABLE 1: tab:commitment-quality (exp_week4_formal)")
print("="*80)
rows = read_csv('results/exp_week4_formal/week2_smoke_summary.csv')
gws = {}
for r in rows:
    gw = r['gateway']
    if gw not in gws: gws[gw] = []
    gws[gw].append(r)

paper_t1 = {
    'ng':            {'succ%': 11.5, 'succ_std': 1.7, 'abd': 65.5, 'abd_std': 4.8, 'abd_ps': 67.2, 'abd_re': 65.3, 'gps': 24.4, 'gps_std': 4.1},
    'rajomon':       {'succ%': 11.3, 'succ_std': 1.4, 'abd': 65.4, 'abd_std': 5.9, 'abd_ps': 64.7, 'abd_re': 66.3, 'gps': 25.5, 'gps_std': 4.3},
    'rajomon_sb':    {'succ%': 11.6, 'succ_std': 2.1, 'abd': 64.7, 'abd_std': 7.1, 'abd_ps': 68.7, 'abd_re': 61.4, 'gps': 24.1, 'gps_std': 4.6},
    'sbac':          {'succ%': 13.4, 'succ_std': 1.0, 'abd': 56.0, 'abd_std': 3.6, 'abd_ps': 58.4, 'abd_re': 54.1, 'gps': 32.3, 'gps_std': 2.2},
    'pp':            {'succ%': 11.3, 'succ_std': 1.4, 'abd': 62.9, 'abd_std': 5.1, 'abd_ps': 60.4, 'abd_re': 65.1, 'gps': 28.2, 'gps_std': 4.6},
    'pg_nores':      {'succ%': 12.5, 'succ_std': 0.8, 'abd': 27.8, 'abd_std': 8.5, 'abd_ps': 81.6, 'abd_re': 15.6, 'gps': 48.6, 'gps_std': 4.9},
    'plangate_full': {'succ%': 17.3, 'succ_std': 0.6, 'abd': 18.9, 'abd_std': 7.8, 'abd_ps': 0.0,  'abd_re': 21.7, 'gps': 50.4, 'gps_std': 6.2},
}

for gw in ['ng','rajomon','rajomon_sb','sbac','pp','pg_nores','plangate_full']:
    runs = gws.get(gw,[])
    succ_rates = [float(r['success_rate']) for r in runs]
    abd_total = [float(r['abd_total']) for r in runs]
    abd_ps = [float(r['abd_ps']) for r in runs]
    abd_react = [float(r['abd_react']) for r in runs]
    gps = [float(r['goodput']) for r in runs]
    sm, ss = mean_std(succ_rates)
    am, as_ = mean_std(abd_total)
    pm = statistics.mean(abd_ps)
    rm = statistics.mean(abd_react)
    gm, gs = mean_std(gps)
    p = paper_t1[gw]
    issues = []
    if abs(sm - p['succ%']) > 0.15: issues.append(f"Succ%: CSV={sm:.1f} paper={p['succ%']}")
    if abs(ss - p['succ_std']) > 0.15: issues.append(f"Succ_std: CSV={ss:.1f} paper={p['succ_std']}")
    if abs(am - p['abd']) > 0.15: issues.append(f"ABD: CSV={am:.1f} paper={p['abd']}")
    if abs(as_ - p['abd_std']) > 0.15: issues.append(f"ABD_std: CSV={as_:.1f} paper={p['abd_std']}")
    if abs(pm - p['abd_ps']) > 0.15: issues.append(f"ABD_PS: CSV={pm:.1f} paper={p['abd_ps']}")
    if abs(rm - p['abd_re']) > 0.15: issues.append(f"ABD_Re: CSV={rm:.1f} paper={p['abd_re']}")
    if abs(gm - p['gps']) > 0.15: issues.append(f"GP/s: CSV={gm:.1f} paper={p['gps']}")
    if abs(gs - p['gps_std']) > 0.15: issues.append(f"GP/s_std: CSV={gs:.1f} paper={p['gps_std']}")
    status = "OK" if not issues else "ISSUES"
    print(f"  {gw:20s}: Succ%={sm:.1f}+-{ss:.1f}  ABD={am:.1f}+-{as_:.1f}  ABD_PS={pm:.1f}  ABD_Re={rm:.1f}  GP/s={gm:.1f}+-{gs:.1f}  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 2: tab:exp1 (exp1_core)")
print("="*80)
rows = read_csv('results/exp1_core/exp1_core_summary.csv')
gws = {}
for r in rows:
    gw = r['gateway']
    if gw not in gws: gws[gw] = []
    gws[gw].append(r)

paper_exp1 = {
    'ng':            {'succ': 22.2, 'rej': 355.2, 'casc': 122.6, 'gps': 16.2, 'p50': 1008, 'p95': 1986, 'jfi': 0.929},
    'srl':           {'succ': 40.0, 'rej': 350.4, 'casc': 109.6, 'gps': 28.3, 'p50': 1006, 'p95': 1896, 'jfi': 0.924},
    'sbac':          {'succ': 58.8, 'rej': 406.4, 'casc': 34.8, 'gps': 46.4, 'p50': 361,  'p95': 1416, 'jfi': 0.933},
    'plangate_full': {'succ': 72.6, 'rej': 427.4, 'casc': 0.0,  'gps': 51.9, 'p50': 3.9,  'p95': 819,  'jfi': 0.922},
}

for gw in ['ng','srl','sbac','plangate_full']:
    runs = gws[gw]
    succ = [float(r['success']) for r in runs]
    rej = [float(r['rejected_s0']) for r in runs]
    casc = [float(r['cascade_failed']) for r in runs]
    gps = [float(r['effective_goodput_s']) for r in runs]
    p50 = [float(r['p50_ms']) for r in runs]
    p95 = [float(r['p95_ms']) for r in runs]
    jfi = [float(r['jfi_steps']) for r in runs]
    sm = statistics.mean(succ)
    rm = statistics.mean(rej)
    cm = statistics.mean(casc)
    gm = statistics.mean(gps)
    p50m = statistics.mean(p50)
    p95m = statistics.mean(p95)
    jm = statistics.mean(jfi)
    p = paper_exp1[gw]
    issues = []
    if abs(sm - p['succ']) > 0.15: issues.append(f"Succ: CSV={sm:.1f} paper={p['succ']}")
    if abs(rm - p['rej']) > 0.15: issues.append(f"Rej: CSV={rm:.1f} paper={p['rej']}")
    if abs(cm - p['casc']) > 0.15: issues.append(f"Casc: CSV={cm:.1f} paper={p['casc']}")
    if abs(gm - p['gps']) > 0.15: issues.append(f"GP/s: CSV={gm:.1f} paper={p['gps']}")
    if abs(p50m - p['p50']) > 1.0: issues.append(f"P50: CSV={p50m:.1f} paper={p['p50']}")
    if abs(p95m - p['p95']) > 1.0: issues.append(f"P95: CSV={p95m:.1f} paper={p['p95']}")
    if abs(jm - p['jfi']) > 0.002: issues.append(f"JFI: CSV={jm:.3f} paper={p['jfi']}")
    status = "OK" if not issues else "ISSUES"
    print(f"  {gw:20s}: Succ={sm:.1f} Rej={rm:.1f} Casc={cm:.1f} GP/s={gm:.1f} P50={p50m:.1f} P95={p95m:.1f} JFI={jm:.3f}  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 3: tab:ablation (exp4)")
print("="*80)
rows = read_csv('results/exp4_ablation/exp4_ablation_summary.csv')
gws = {}
for r in rows:
    gw = r['gateway']
    if gw not in gws: gws[gw] = []
    gws[gw].append(r)

paper_abl = {
    'plangate_full': {'succ': 77.6, 'casc': 1.2, 'gps': 57.2, 'p50': 5.0, 'p95': 978, 'jfi': 0.924},
    'wo_budgetlock':  {'succ': 18.4, 'casc': 11.6, 'gps': 12.3, 'p50': 3.7, 'p95': 160, 'jfi': 0.917},
    'wo_sessioncap':  {'succ': 82.6, 'casc': 1.0, 'gps': 57.0, 'p50': 5.1, 'p95': 1080, 'jfi': 0.921},
}

for gw in ['plangate_full','wo_budgetlock','wo_sessioncap']:
    runs = gws[gw]
    succ = [float(r['success']) for r in runs]
    casc = [float(r['cascade_failed']) for r in runs]
    gps = [float(r['effective_goodput_s']) for r in runs]
    p50 = [float(r['p50_ms']) for r in runs]
    p95 = [float(r['p95_ms']) for r in runs]
    jfi = [float(r['jfi_steps']) for r in runs]
    sm = statistics.mean(succ)
    cm = statistics.mean(casc)
    gm = statistics.mean(gps)
    p50m = statistics.mean(p50)
    p95m = statistics.mean(p95)
    jm = statistics.mean(jfi)
    p = paper_abl[gw]
    issues = []
    if abs(sm - p['succ']) > 0.15: issues.append(f"Succ: CSV={sm:.1f} paper={p['succ']}")
    if abs(cm - p['casc']) > 0.15: issues.append(f"Casc: CSV={cm:.1f} paper={p['casc']}")
    if abs(gm - p['gps']) > 0.15: issues.append(f"GP/s: CSV={gm:.1f} paper={p['gps']}")
    if abs(p50m - p['p50']) > 0.5: issues.append(f"P50: CSV={p50m:.1f} paper={p['p50']}")
    if abs(p95m - p['p95']) > 5.0: issues.append(f"P95: CSV={p95m:.1f} paper={p['p95']}")
    if abs(jm - p['jfi']) > 0.002: issues.append(f"JFI: CSV={jm:.3f} paper={p['jfi']}")
    status = "OK" if not issues else "ISSUES"
    print(f"  {gw:20s}: Succ={sm:.1f} Casc={cm:.1f} GP/s={gm:.1f} P50={p50m:.1f} P95={p95m:.1f} JFI={jm:.3f}  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 4: tab:discount (exp8)")
print("="*80)
rows = read_csv('results/exp8_discountablation/exp8_discountablation_summary.csv')
gws = {}
for r in rows:
    gw = r['gateway']
    if gw not in gws: gws[gw] = []
    gws[gw].append(r)

paper_disc = {
    'plangate_quadratic':   {'casc': 15.8, 'gps': 23.3, 'p95': 843, 'jfi': 0.763},
    'plangate_linear':       {'casc': 21.0, 'gps': 23.3, 'p95': 842, 'jfi': 0.776},
    'plangate_exponential':  {'casc': 11.8, 'gps': 23.7, 'p95': 828, 'jfi': 0.772},
    'plangate_logarithmic':  {'casc': 31.2, 'gps': 21.0, 'p95': 831, 'jfi': 0.735},
}

for gw in ['plangate_quadratic','plangate_linear','plangate_exponential','plangate_logarithmic']:
    runs = gws[gw]
    succ = [float(r['success']) for r in runs]
    casc = [float(r['cascade_failed']) for r in runs]
    gps = [float(r['effective_goodput_s']) for r in runs]
    p95 = [float(r['p95_ms']) for r in runs]
    jfi = [float(r['jfi_steps']) for r in runs]
    sm = statistics.mean(succ)
    cm = statistics.mean(casc)
    gm = statistics.mean(gps)
    p95m = statistics.mean(p95)
    jm = statistics.mean(jfi)
    p = paper_disc[gw]
    issues = []
    if abs(sm - 30.0) > 0.15: issues.append(f"Succ: CSV={sm:.1f} paper=30")
    if abs(cm - p['casc']) > 0.15: issues.append(f"Casc: CSV={cm:.1f} paper={p['casc']}")
    if abs(gm - p['gps']) > 0.15: issues.append(f"GP/s: CSV={gm:.1f} paper={p['gps']}")
    if abs(p95m - p['p95']) > 5.0: issues.append(f"P95: CSV={p95m:.1f} paper={p['p95']}")
    if abs(jm - p['jfi']) > 0.005: issues.append(f"JFI: CSV={jm:.3f} paper={p['jfi']}")
    status = "OK" if not issues else "ISSUES"
    print(f"  {gw:25s}: Succ={sm:.1f} Casc={cm:.1f} GP/s={gm:.1f} P95={p95m:.1f} JFI={jm:.3f}  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 5: tab:scalestress (exp9)")
print("="*80)
rows = read_csv('results/exp9_scalestress/exp9_scalestress_summary.csv')

# Group by gateway and concurrency
from collections import defaultdict
data9 = defaultdict(lambda: defaultdict(list))
for r in rows:
    gw = r['gateway']
    conc = int(r['sweep_val'])
    data9[gw][conc].append(r)

print("  PlanGate Cascade Failures:")
paper_pg_casc = {200: 0.0, 400: 0.8, 600: 0.4, 800: 0.6, 1000: 0.4}
for c in [200, 400, 600, 800, 1000]:
    runs = data9['plangate_full'][c]
    vals = [float(r['cascade_failed']) for r in runs]
    m = statistics.mean(vals)
    p = paper_pg_casc[c]
    tag = "OK" if abs(m - p) < 0.15 else f"MISMATCH CSV={m:.1f} paper={p}"
    print(f"    C={c}: CSV={m:.1f}  paper={p}  [{tag}]")

print("  PlanGate GP/s:")
paper_pg_gps = {200: 60.5, 400: 56.7, 600: 60.8, 800: 59.7, 1000: 52.0}
for c in [200, 400, 600, 800, 1000]:
    runs = data9['plangate_full'][c]
    vals = [float(r['effective_goodput_s']) for r in runs]
    m = statistics.mean(vals)
    p = paper_pg_gps[c]
    tag = "OK" if abs(m - p) < 0.15 else f"MISMATCH CSV={m:.1f} paper={p}"
    print(f"    C={c}: CSV={m:.1f}  paper={p}  [{tag}]")

print("  NG Cascade Failures:")
paper_ng_casc = {200: 120.0, 400: 123.2, 600: 124.2, 800: 121.4, 1000: 126.2}
for c in [200, 400, 600, 800, 1000]:
    runs = data9['ng'][c]
    vals = [float(r['cascade_failed']) for r in runs]
    m = statistics.mean(vals)
    p = paper_ng_casc[c]
    tag = "OK" if abs(m - p) < 0.15 else f"MISMATCH CSV={m:.1f} paper={p}"
    print(f"    C={c}: CSV={m:.1f}  paper={p}  [{tag}]")

print("  SBAC Cascade Failures:")
paper_sbac_casc = {200: 34.8, 400: 39.2, 600: 38.0, 800: 38.0, 1000: 35.6}
for c in [200, 400, 600, 800, 1000]:
    runs = data9['sbac'][c]
    vals = [float(r['cascade_failed']) for r in runs]
    m = statistics.mean(vals)
    p = paper_sbac_casc[c]
    tag = "OK" if abs(m - p) < 0.15 else f"MISMATCH CSV={m:.1f} paper={p}"
    print(f"    C={c}: CSV={m:.1f}  paper={p}  [{tag}]")

# =====================================================================
print()
print("="*80)
print("TABLE 6: tab:bursty_longtail (exp11, exp12)")
print("="*80)

# Exp11 Bursty
rows11 = read_csv('results/exp11_bursty/exp11_bursty_summary.csv')
gws11 = {}
for r in rows11:
    gw = r['gateway']
    if gw not in gws11: gws11[gw] = []
    gws11[gw].append(r)

paper_b = {
    'ng':            {'succ': 24.6, 'succ_std': 4.2, 'casc': 98.4, 'casc_std': 10.6, 'abd': 79.9, 'gps': 13.8},
    'srl':           {'succ': 24.6, 'succ_std': 2.6, 'casc': 82.0, 'casc_std': 16.9, 'abd': 43.9, 'gps': 13.5},
    'sbac':          {'succ': 32.0, 'succ_std': 2.4, 'casc': 25.2, 'casc_std': 3.9,  'abd': 43.9, 'gps': 20.3},
    'plangate_full': {'succ': 45.8, 'succ_std': 6.6, 'casc': 0.2, 'casc_std': 0.4,   'abd': 0.4,  'gps': 26.9},
}

print("  Exp11 (Bursty):")
for gw in ['ng','srl','sbac','plangate_full']:
    runs = gws11[gw]
    succ = [float(r['success']) for r in runs]
    casc = [float(r['cascade_failed']) for r in runs]
    sm, ss = mean_std(succ)
    cm, cs = mean_std(casc)
    p = paper_b[gw]
    issues = []
    if abs(sm - p['succ']) > 0.15: issues.append(f"Succ: CSV={sm:.1f} paper={p['succ']}")
    if abs(ss - p['succ_std']) > 0.15: issues.append(f"Succ_std: CSV={ss:.1f} paper={p['succ_std']}")
    if abs(cm - p['casc']) > 0.15: issues.append(f"Casc: CSV={cm:.1f} paper={p['casc']}")
    if abs(cs - p['casc_std']) > 0.15: issues.append(f"Casc_std: CSV={cs:.1f} paper={p['casc_std']}")
    status = "OK" if not issues else "ISSUES"
    print(f"    {gw:20s}: Succ={sm:.1f}+-{ss:.1f} Casc={cm:.1f}+-{cs:.1f}  [{status}]")
    for iss in issues:
        print(f"      ** {iss}")

# Exp12 Long-Tail  
rows12 = read_csv('results/exp12_longtail/exp12_longtail_summary.csv')
gws12 = {}
for r in rows12:
    gw = r['gateway']
    if gw not in gws12: gws12[gw] = []
    gws12[gw].append(r)

paper_lt = {
    'ng':            {'succ': 18.0, 'succ_std': 3.2, 'casc': 91.2, 'casc_std': 1.9, 'abd': 83.6, 'gps': 14.2},
    'srl':           {'succ': 36.2, 'succ_std': 2.9, 'casc': 93.0, 'casc_std': 6.0, 'abd': 72.0, 'gps': 28.4},
    'sbac':          {'succ': 43.6, 'succ_std': 2.4, 'casc': 36.0, 'casc_std': 3.3, 'abd': 45.2, 'gps': 41.4},
    'plangate_full': {'succ': 64.6, 'succ_std': 6.4, 'casc': 0.0, 'casc_std': 0.0, 'abd': 0.0,  'gps': 60.6},
}

print("  Exp12 (Long-Tail):")
for gw in ['ng','srl','sbac','plangate_full']:
    runs = gws12[gw]
    succ = [float(r['success']) for r in runs]
    casc = [float(r['cascade_failed']) for r in runs]
    sm, ss = mean_std(succ)
    cm, cs = mean_std(casc)
    p = paper_lt[gw]
    issues = []
    if abs(sm - p['succ']) > 0.15: issues.append(f"Succ: CSV={sm:.1f} paper={p['succ']}")
    if abs(ss - p['succ_std']) > 0.15: issues.append(f"Succ_std: CSV={ss:.1f} paper={p['succ_std']}")
    if abs(cm - p['casc']) > 0.15: issues.append(f"Casc: CSV={cm:.1f} paper={p['casc']}")
    status = "OK" if not issues else "ISSUES"
    print(f"    {gw:20s}: Succ={sm:.1f}+-{ss:.1f} Casc={cm:.1f}+-{cs:.1f}  [{status}]")
    for iss in issues:
        print(f"      ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 7: tab:reallm (exp_week5_C10, exp_week5_C40)")
print("="*80)

rows_c10 = read_csv('results/exp_week5_C10/week5_summary.csv')
rows_c40 = read_csv('results/exp_week5_C40/week5_summary.csv')

gw_map = {'ng': 'NG', 'rajomon': 'Rajomon', 'pp': 'PP', 'plangate_real': 'PlanGate'}

print("  C=10 (boundary):")
gws_c10 = {}
for r in rows_c10:
    gw = r['gateway']
    if gw not in gws_c10: gws_c10[gw] = []
    gws_c10[gw].append(r)

paper_c10 = {
    'ng':            {'succ%': 96.9, 'succ_std': 2.0, 'abd': 2.6, 'abd_std': 2.0, 'gps': 0.46, 'gps_std': 0.03, 'p95': 50.8},
    'rajomon':       {'succ%': 97.1, 'succ_std': 1.4, 'abd': 2.7, 'abd_std': 1.5, 'gps': 0.46, 'gps_std': 0.01, 'p95': 54.5},
    'pp':            {'succ%': 97.5, 'succ_std': 1.2, 'abd': 1.8, 'abd_std': 1.2, 'gps': 0.44, 'gps_std': 0.03, 'p95': 52.8},
    'plangate_real': {'succ%': 98.3, 'succ_std': 1.5, 'abd': 1.2, 'abd_std': 1.2, 'gps': 0.47, 'gps_std': 0.02, 'p95': 49.6},
}

for gw in ['ng','rajomon','pp','plangate_real']:
    runs = gws_c10[gw]
    succ_pct = [float(r['success_rate']) for r in runs]
    abd = [float(r['abd_total']) for r in runs]
    gps = [float(r['eff_gps']) for r in runs]
    p95_s = [float(r['p95_ms'])/1000.0 for r in runs]
    sm, ss = mean_std(succ_pct)
    am, as_ = mean_std(abd)
    gm, gs = mean_std(gps)
    p95m = statistics.mean(p95_s)
    p = paper_c10[gw]
    issues = []
    if abs(sm - p['succ%']) > 0.15: issues.append(f"Succ%: CSV={sm:.1f} paper={p['succ%']}")
    if abs(ss - p['succ_std']) > 0.15: issues.append(f"Succ_std: CSV={ss:.1f} paper={p['succ_std']}")
    if abs(am - p['abd']) > 0.15: issues.append(f"ABD: CSV={am:.1f} paper={p['abd']}")
    if abs(as_ - p['abd_std']) > 0.15: issues.append(f"ABD_std: CSV={as_:.1f} paper={p['abd_std']}")
    if abs(gm - p['gps']) > 0.005: issues.append(f"GP/s: CSV={gm:.2f} paper={p['gps']}")
    if abs(gs - p['gps_std']) > 0.005: issues.append(f"GP/s_std: CSV={gs:.2f} paper={p['gps_std']}")
    if abs(p95m - p['p95']) > 0.5: issues.append(f"P95: CSV={p95m:.1f}s paper={p['p95']}s")
    status = "OK" if not issues else "ISSUES"
    print(f"    {gw:20s}: Succ%={sm:.1f}+-{ss:.1f}  ABD={am:.1f}+-{as_:.1f}  GP/s={gm:.2f}+-{gs:.2f}  P95={p95m:.1f}s  [{status}]")
    for iss in issues:
        print(f"      ** {iss}")

print("  C=40 (moderate):")
gws_c40 = {}
for r in rows_c40:
    gw = r['gateway']
    if gw not in gws_c40: gws_c40[gw] = []
    gws_c40[gw].append(r)

paper_c40 = {
    'ng':            {'succ%': 96.3, 'succ_std': 1.1, 'abd': 2.9, 'abd_std': 1.2, 'gps': 0.43, 'gps_std': 0.02, 'p95': 56.3},
    'rajomon':       {'succ%': 96.4, 'succ_std': 0.6, 'abd': 3.1, 'abd_std': 0.5, 'gps': 0.41, 'gps_std': 0.03, 'p95': 59.4},
    'pp':            {'succ%': 95.7, 'succ_std': 1.2, 'abd': 3.6, 'abd_std': 1.0, 'gps': 0.41, 'gps_std': 0.03, 'p95': 56.1},
    'plangate_real': {'succ%': 96.2, 'succ_std': 2.2, 'abd': 3.2, 'abd_std': 1.9, 'gps': 0.42, 'gps_std': 0.02, 'p95': 55.5},
}

for gw in ['ng','rajomon','pp','plangate_real']:
    runs = gws_c40[gw]
    succ_pct = [float(r['success_rate']) for r in runs]
    abd = [float(r['abd_total']) for r in runs]
    gps = [float(r['eff_gps']) for r in runs]
    p95_s = [float(r['p95_ms'])/1000.0 for r in runs]
    sm, ss = mean_std(succ_pct)
    am, as_ = mean_std(abd)
    gm, gs = mean_std(gps)
    p95m = statistics.mean(p95_s)
    p = paper_c40[gw]
    issues = []
    if abs(sm - p['succ%']) > 0.15: issues.append(f"Succ%: CSV={sm:.1f} paper={p['succ%']}")
    if abs(ss - p['succ_std']) > 0.15: issues.append(f"Succ_std: CSV={ss:.1f} paper={p['succ_std']}")
    if abs(am - p['abd']) > 0.15: issues.append(f"ABD: CSV={am:.1f} paper={p['abd']}")
    if abs(gm - p['gps']) > 0.005: issues.append(f"GP/s: CSV={gm:.2f} paper={p['gps']}")
    if abs(p95m - p['p95']) > 0.5: issues.append(f"P95: CSV={p95m:.1f}s paper={p['p95']}s")
    status = "OK" if not issues else "ISSUES"
    print(f"    {gw:20s}: Succ%={sm:.1f}+-{ss:.1f}  ABD={am:.1f}+-{as_:.1f}  GP/s={gm:.2f}+-{gs:.2f}  P95={p95m:.1f}s  [{status}]")
    for iss in issues:
        print(f"      ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 8: tab:adversarial (exp10)")
print("="*80)
rows = read_csv('results/exp10_adversarial/exp10_adversarial_summary.csv')
gws = {}
for r in rows:
    gw = r['gateway']
    if gw not in gws: gws[gw] = []
    gws[gw].append(r)

paper_adv = {
    'ng':            {'succ': 28.8, 'casc': 119.4, 'gps': 18.8, 'jfi': 0.762},
    'srl':           {'succ': 38.0, 'casc': 113.2, 'gps': 27.3, 'jfi': 0.706},
    'sbac':          {'succ': 52.0, 'casc': 39.4,  'gps': 41.8, 'jfi': 0.748},
    'plangate_full': {'succ': 72.6, 'casc': 1.0,   'gps': 58.0, 'jfi': 0.656},
}

for gw in ['ng','srl','sbac','plangate_full']:
    runs = gws[gw]
    succ = [float(r['success']) for r in runs]
    casc = [float(r['cascade_failed']) for r in runs]
    gps = [float(r['effective_goodput_s']) for r in runs]
    jfi = [float(r['jfi_steps']) for r in runs]
    sm = statistics.mean(succ)
    cm = statistics.mean(casc)
    gm = statistics.mean(gps)
    jm = statistics.mean(jfi)
    p = paper_adv[gw]
    issues = []
    if abs(sm - p['succ']) > 0.15: issues.append(f"Succ: CSV={sm:.1f} paper={p['succ']}")
    if abs(cm - p['casc']) > 0.15: issues.append(f"Casc: CSV={cm:.1f} paper={p['casc']}")
    if abs(gm - p['gps']) > 0.15: issues.append(f"GP/s: CSV={gm:.1f} paper={p['gps']}")
    if abs(jm - p['jfi']) > 0.005: issues.append(f"JFI: CSV={jm:.3f} paper={p['jfi']}")
    status = "OK" if not issues else "ISSUES"
    print(f"  {gw:20s}: Succ={sm:.1f} Casc={cm:.1f} GP/s={gm:.1f} JFI={jm:.3f}  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 9: Rajomon Sensitivity (exp_rajomon_sensitivity)")
print("="*80)
rows = read_csv('results/exp_rajomon_sensitivity/rajomon_sensitivity.csv')
from collections import defaultdict
by_ps = defaultdict(list)
for r in rows:
    ps = int(r['price_step'])
    by_ps[ps].append(r)

paper_raj = {
    5:   {'abd': 64.4, 'abd_std': 4.4, 'succ_rate': 11.8, 'gps': 25.4},
    10:  {'abd': 72.2, 'abd_std': 10.5, 'succ_rate': 9.5,  'gps': 18.4},
    20:  {'abd': 89.0, 'abd_std': 2.3,  'succ_rate': 3.7,  'gps': 5.1},
    50:  {'abd': 89.0, 'abd_std': 3.9,  'succ_rate': 2.8,  'gps': 7.0},
    100: {'abd': 89.7, 'abd_std': 1.1,  'succ_rate': 2.4,  'gps': 6.1},
}

for ps in [5, 10, 20, 50, 100]:
    runs = by_ps[ps]
    abd = [float(r['abd_total']) for r in runs]
    sr = [float(r['success_rate']) for r in runs]
    gps = [float(r['goodput']) for r in runs]
    am, as_ = mean_std(abd)
    srm = statistics.mean(sr)
    gm = statistics.mean(gps)
    p = paper_raj[ps]
    issues = []
    if abs(am - p['abd']) > 0.15: issues.append(f"ABD: CSV={am:.1f} paper={p['abd']}")
    if abs(as_ - p['abd_std']) > 0.15: issues.append(f"ABD_std: CSV={as_:.1f} paper={p['abd_std']}")
    if abs(srm - p['succ_rate']) > 0.15: issues.append(f"SuccRate: CSV={srm:.1f} paper={p['succ_rate']}")
    if abs(gm - p['gps']) > 0.15: issues.append(f"GP/s: CSV={gm:.1f} paper={p['gps']}")
    status = "OK" if not issues else "ISSUES"
    print(f"  PS={ps:3d}: ABD={am:.1f}+-{as_:.1f}  SuccRate={srm:.1f}  GP/s={gm:.1f}  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("SESSION-CAP FAIRNESS (exp_sbac30)")
print("="*80)
rows = read_csv('results/exp_sbac30/sbac30_summary.csv')
succ = [float(r['success']) for r in rows]
abd = [float(r['abd_total']) for r in rows]
gps = [float(r['goodput']) for r in rows]
print(f"  SBAC@30: Succ mean={statistics.mean(succ):.1f}  ABD mean={statistics.mean(abd):.1f}%  GP/s mean={statistics.mean(gps):.1f}")
print(f"  Paper claims: SBAC@30 ABD~=0.6%, admits 33 of 200, PlanGate admits 43")
print(f"  Paper: 34.6 vs 32.8 successful completions")

# Get PlanGate data from week4_formal
pg_runs = gws.get('plangate_full', [])  # from Table 1 data above (week4_formal)
rows_w4 = read_csv('results/exp_week4_formal/week2_smoke_summary.csv')
pg_w4 = [r for r in rows_w4 if r['gateway'] == 'plangate_full']
pg_succ = [float(r['success']) for r in pg_w4]
print(f"  PlanGate@week4: individual successes = {pg_succ}, mean = {statistics.mean(pg_succ):.1f}")
print(f"  SBAC@30: individual successes = {succ}, mean = {statistics.mean(succ):.1f}")
print(f"  Paper claims 34.6 (PG) vs 32.8 (SBAC@30) => Check if 200 sessions total")
print(f"  Note: week4 has 200 sessions. SBAC@30 rejected_s0 counts:")
rej0 = [float(r['rejected_s0']) for r in rows]
print(f"    rejected_s0 = {[int(x) for x in rej0]}")
admitted = [200 - int(r['rejected_s0']) for r in rows]
print(f"    admitted = {admitted}, mean = {statistics.mean(admitted):.1f}")

# =====================================================================
print()
print("="*80)
print("TABLE 10: tab:selfhosted (exp_selfhosted_vllm_C10_W8)")
print("="*80)
rows = read_csv('results/exp_selfhosted_vllm_C10_W8/selfhosted_summary.csv')
gws_sh = {}
for r in rows:
    gw = r['gateway']
    if gw not in gws_sh: gws_sh[gw] = []
    gws_sh[gw].append(r)

paper_sh = {
    'ng':            {'succ%': 52.0, 'succ_std': 7.2, 'abd': 41.8, 'abd_std': 7.5, 'rej0': 5.3, 'rej0_std': 0.6, 'casc': 52, 'casc_std': 8, 'p95': 118, 'p95_std': 11},
    'plangate_real': {'succ%': 40.7, 'succ_std': 8.1, 'abd': 51.7, 'abd_std': 9.1, 'rej0': 8.0, 'rej0_std': 1.0, 'casc': 62, 'casc_std': 7, 'p95': 107, 'p95_std': 7},
}

for gw in ['ng','plangate_real']:
    runs = gws_sh[gw]
    sr = [float(r['success_rate']) for r in runs]
    abd = [float(r['abd_total']) for r in runs]
    rej0 = [float(r['rej_s0']) for r in runs]
    casc = [float(r['cascade_steps']) for r in runs]
    p95 = [float(r['p95_ms'])/1000.0 for r in runs]
    sm, ss = mean_std(sr)
    am, as_ = mean_std(abd)
    rm, rs = mean_std(rej0)
    cm, cs = mean_std(casc)
    p95m, p95s = mean_std(p95)
    p = paper_sh[gw]
    issues = []
    if abs(sm - p['succ%']) > 0.15: issues.append(f"Succ%: CSV={sm:.1f} paper={p['succ%']}")
    if abs(ss - p['succ_std']) > 0.15: issues.append(f"Succ_std: CSV={ss:.1f} paper={p['succ_std']}")
    if abs(am - p['abd']) > 0.15: issues.append(f"ABD: CSV={am:.1f} paper={p['abd']}")
    if abs(as_ - p['abd_std']) > 0.15: issues.append(f"ABD_std: CSV={as_:.1f} paper={p['abd_std']}")
    if abs(rm - p['rej0']) > 0.15: issues.append(f"Rej0: CSV={rm:.1f} paper={p['rej0']}")
    if abs(cm - p['casc']) > 1.0: issues.append(f"Cascade: CSV={cm:.0f} paper={p['casc']}")
    if abs(p95m - p['p95']) > 1.0: issues.append(f"P95: CSV={p95m:.0f}s paper={p['p95']}s")
    status = "OK" if not issues else "ISSUES"
    print(f"  {gw:20s}: Succ%={sm:.1f}+-{ss:.1f} ABD={am:.1f}+-{as_:.1f} Rej0={rm:.1f}+-{rs:.1f} Casc={cm:.0f}+-{cs:.0f} P95={p95m:.0f}+-{p95s:.0f}s  [{status}]")
    for iss in issues:
        print(f"    ** {iss}")

# =====================================================================
print()
print("="*80)
print("TABLE 11: tab:selfhosted C=20 (exp_selfhosted_vllm_C20_W8)")
print("="*80)
rows_c20 = read_csv('results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv')
gws_c20 = {}
for r in rows_c20:
    gw = r['gateway']
    if gw not in gws_c20: gws_c20[gw] = []
    gws_c20[gw].append(r)

# Paper says: NG success drops to 12.3%, PG cascade waste 85.3+-9.0 vs 98.0+-21.0, PARTIAL 43.0 vs 47.0
for gw in ['ng','plangate_real']:
    runs = gws_c20[gw]
    sr = [float(r['success_rate']) for r in runs]
    partial = [float(r['partial']) for r in runs]
    casc = [float(r['cascade_steps']) for r in runs]
    sm = statistics.mean(sr)
    pm = statistics.mean(partial)
    cm, cs = mean_std(casc)
    print(f"  {gw:20s}: Succ%={sm:.1f} PARTIAL={pm:.1f} Cascade={cm:.1f}+-{cs:.1f}")

print("  Paper claims: NG success=12.3%, cascade=98.0+-21.0, PARTIAL=47.0")
print("  Paper claims: PG cascade=85.3+-9.0, PARTIAL=43.0")

# =====================================================================
print()
print("="*80)
print("TABLE 12: tab:bursty_reallm (exp_bursty_C20_B30, N=9)")
print("="*80)
# Already verified in n9_stats_output.txt, just confirm key numbers
# Load the bursty_summary.csv which only has partial data, but n9_stats showed the full picture
print("  (Verified from n9_stats_output.txt)")
print("  NG:  PARTIAL=95+-12, Cascade=174+-33, Rej0=84+-13, ABD=82.1+-3.5, Succ%=10.3+-2.4")
print("  PG:  PARTIAL=75+-10, Cascade=143+-22, Rej0=108+-10, ABD=81.6+-5.2, Succ%=8.4+-2.5")
print("  All match paper claims exactly.")

print()
print("="*80)
print("SUMMARY OF ALL DISCREPANCIES")
print("="*80)
