#!/usr/bin/env python3
"""Quick analysis of experiment results for all key experiments."""
import csv, os
from collections import defaultdict

BASE = os.path.join(os.path.dirname(__file__), '..', 'results')

def analyze(exp_name, summary_file, group_key='sweep_val'):
    path = os.path.join(BASE, exp_name, summary_file)
    if not os.path.exists(path):
        print(f"  MISSING: {path}")
        return
    data = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for row in csv.DictReader(f):
            gw = row['gateway']
            sv = row.get(group_key, 'default')
            if row.get('error'):
                continue
            try:
                data[(gw, sv)]['success'].append(float(row['success']))
                data[(gw, sv)]['rejected_s0'].append(float(row['rejected_s0']))
                data[(gw, sv)]['cascade_failed'].append(float(row['cascade_failed']))
                data[(gw, sv)]['effective_goodput_s'].append(float(row['effective_goodput_s']))
            except (ValueError, KeyError):
                continue

    # Collect unique sweep vals
    sweep_vals = sorted(set(sv for (_, sv) in data.keys()))
    gateways = ['ng', 'srl', 'rajomon', 'dagor', 'sbac', 'plangate_full']

    for sv in sweep_vals:
        if len(sweep_vals) > 1:
            print(f"\n  --- {group_key}={sv} ---")
        for gw in gateways:
            rows = data.get((gw, sv))
            if not rows or not rows['success']:
                continue
            n = len(rows['success'])
            avg = lambda k: sum(rows[k]) / n
            print(f"    {gw:20s}  succ={avg('success'):6.1f}  rej_s0={avg('rejected_s0'):6.1f}  cascade={avg('cascade_failed'):6.1f}  effGP/s={avg('effective_goodput_s'):6.1f}")

print("=" * 80)
print("Exp1_Core (high load: 500 sessions, conc=200)")
print("=" * 80)
analyze('exp1_core', 'exp1_core_summary.csv', 'sweep_val')

print("\n" + "=" * 80)
print("Exp3_MixedMode (ps_ratio sweep, conc=20)")
print("=" * 80)
analyze('exp3_mixedmode', 'exp3_mixedmode_summary.csv', 'sweep_val')

print("\n" + "=" * 80)
print("Exp5_ScaleConc P&S mode (concurrency sweep)")
print("=" * 80)
analyze('exp5_scaleconc', 'exp5_scaleconc_summary.csv', 'sweep_val')

print("\n" + "=" * 80)
print("Exp6_ScaleConcReact ReAct mode (concurrency sweep)")
print("=" * 80)
analyze('exp6_scaleconcreact', 'exp6_scaleconcreact_summary.csv', 'sweep_val')

print("\n" + "=" * 80)
print("Exp4_Ablation (PlanGate variants)")
print("=" * 80)
analyze('exp4_ablation', 'exp4_ablation_summary.csv', 'sweep_val')
