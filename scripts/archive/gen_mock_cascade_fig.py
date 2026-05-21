#!/usr/bin/env python3
"""Generate mock cascade comparison figure from Exp1 data (4 gateways, 5 runs)."""
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import os
import numpy as np

matplotlib.rcParams.update({
    'font.size': 11, 'font.family': 'serif',
    'axes.labelsize': 12, 'axes.titlesize': 13,
    'xtick.labelsize': 10, 'ytick.labelsize': 10,
    'figure.dpi': 300, 'savefig.dpi': 300,
    'savefig.bbox': 'tight',
})

COLORS = {'NG': '#e74c3c', 'SRL': '#3498db', 'SBAC': '#f39c12', 'PlanGate': '#2ecc71'}
LABELS = {'ng': 'NG', 'srl': 'SRL', 'sbac': 'SBAC', 'plangate_full': 'PlanGate'}

df = pd.read_csv('results/exp1_core/exp1_core_summary.csv')

gateways = ['ng', 'srl', 'sbac', 'plangate_full']
means = []
stds = []
labels = []
colors = []

for gw in gateways:
    sub = df[df['gateway'] == gw]
    m = sub['cascade_failed'].mean()
    s = sub['cascade_failed'].std()
    means.append(m)
    stds.append(s)
    labels.append(LABELS[gw])
    colors.append(COLORS[LABELS[gw]])

x = np.arange(len(gateways))
fig, ax = plt.subplots(figsize=(5, 3.5))
bars = ax.bar(x, means, yerr=stds, capsize=4, color=colors, edgecolor='black', linewidth=0.5, width=0.6)

for bar, m in zip(bars, means):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 3, f'{m:.1f}',
            ha='center', va='bottom', fontsize=10, fontweight='bold')

ax.set_xticks(x)
ax.set_xticklabels(labels)
ax.set_ylabel('Cascade Failures (mean ± std)')
ax.set_title('Cascade Failures per Gateway (Exp1, 500 sessions, 5 runs)')
ax.set_ylim(0, max(means)*1.25)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

os.makedirs('paper/figures', exist_ok=True)
fig.savefig('paper/figures/mock_cascade_comparison.png')
fig.savefig('paper/figures/mock_cascade_comparison.pdf')
print("Saved mock_cascade_comparison to paper/figures/")

# Also generate to paper_figures
os.makedirs('results/paper_figures/PNG', exist_ok=True)
os.makedirs('results/paper_figures/PDF', exist_ok=True)
fig.savefig('results/paper_figures/PNG/fig_mock_cascade.png')
fig.savefig('results/paper_figures/PDF/fig_mock_cascade.pdf')
print("Saved to results/paper_figures/ too")
plt.close()
