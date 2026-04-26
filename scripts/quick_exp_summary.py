#!/usr/bin/env python3
"""Quick summary of Exp2, 3, 5, 6, 7 data."""
import pandas as pd

# Exp2
df = pd.read_csv('results/exp2_heavyratio/exp2_heavyratio_summary.csv')
print('=== Exp2: Heavy Ratio Sweep ===')
for hr in sorted(df['sweep_val'].unique()):
    for gw in ['ng', 'sbac', 'plangate_full']:
        sub = df[(df['gateway']==gw) & (df['sweep_val']==hr)]
        if len(sub) > 0:
            print(f'  {gw} hr={hr}: succ={sub["success"].mean():.0f} casc={sub["cascade_failed"].mean():.1f} gps={sub["effective_goodput_s"].mean():.1f}')

# Exp3
df = pd.read_csv('results/exp3_mixedmode/exp3_mixedmode_summary.csv')
print('\n=== Exp3: Mixed Mode ===')
for ps in sorted(df['sweep_val'].unique()):
    for gw in ['ng', 'plangate_full']:
        sub = df[(df['gateway']==gw) & (df['sweep_val']==ps)]
        if len(sub) > 0:
            print(f'  {gw} ps={ps}: succ={sub["success"].mean():.0f} casc={sub["cascade_failed"].mean():.1f}')

# Exp5
df = pd.read_csv('results/exp5_scaleconc/exp5_scaleconc_summary.csv')
print('\n=== Exp5: Scale Concurrency ===')
for c in sorted(df['sweep_val'].unique()):
    for gw in ['ng', 'plangate_full']:
        sub = df[(df['gateway']==gw) & (df['sweep_val']==c)]
        if len(sub) > 0:
            print(f'  {gw} c={c}: succ={sub["success"].mean():.0f} casc={sub["cascade_failed"].mean():.1f} gps={sub["effective_goodput_s"].mean():.1f}')

# Exp6
df = pd.read_csv('results/exp6_scaleconcreact/exp6_scaleconcreact_summary.csv')
print('\n=== Exp6: Scale Conc ReAct ===')
for c in sorted(df['sweep_val'].unique()):
    for gw in ['ng', 'plangate_full']:
        sub = df[(df['gateway']==gw) & (df['sweep_val']==c)]
        if len(sub) > 0:
            print(f'  {gw} c={c}: succ={sub["success"].mean():.0f} casc={sub["cascade_failed"].mean():.1f} gps={sub["effective_goodput_s"].mean():.1f}')

# Exp7
df = pd.read_csv('results/exp7_clientreject/exp7_clientreject_summary.csv')
print('\n=== Exp7: Client Rejection ===')
for ttl in sorted(df['sweep_val'].unique()):
    sub = df[df['sweep_val']==ttl]
    print(f'  plangate ttl={ttl}: succ={sub["success"].mean():.0f} casc={sub["cascade_failed"].mean():.1f}')
