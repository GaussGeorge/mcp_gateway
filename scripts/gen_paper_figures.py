#!/usr/bin/env python3
"""
Generate ALL publication-quality figures for PlanGate paper.
Reads from experiment summary CSVs and outputs to paper/figures/.
"""
import os, sys
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as mticker

# ═══════════════════════════════════════════
# Global style
# ═══════════════════════════════════════════
plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif'],
    'font.size': 10,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 8.5,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.05,
})

COLORS = {
    'NG': '#d62728',      # red
    'SRL': '#ff7f0e',     # orange
    'SBAC': '#1f77b4',    # blue
    'PlanGate': '#2ca02c', # green
    'PG w/o SC': '#9467bd', # purple
}

GW_ORDER = ['NG', 'SRL', 'SBAC', 'PlanGate']
GW_MAP = {
    'ng': 'NG', 'srl': 'SRL', 'sbac': 'SBAC',
    'plangate_full': 'PlanGate',
    'mcpdp-real': 'PlanGate',
    'mcpdp-real-no-sessioncap': 'PG w/o SC',
}

OUT_DIR = os.path.join('paper', 'figures')
os.makedirs(OUT_DIR, exist_ok=True)
BACKUP_DIR = os.path.join('results', 'paper_figures')
for sub in ['PNG', 'PDF']:
    os.makedirs(os.path.join(BACKUP_DIR, sub), exist_ok=True)


def save_fig(fig, name):
    for ext in ['pdf', 'png']:
        path = os.path.join(OUT_DIR, f'{name}.{ext}')
        fig.savefig(path)
    fig.savefig(os.path.join(BACKUP_DIR, 'PDF', f'{name}.pdf'))
    fig.savefig(os.path.join(BACKUP_DIR, 'PNG', f'{name}.png'))
    plt.close(fig)
    print(f"  ✓ {name}")


def load_summary(exp_name):
    path = os.path.join('results', exp_name, f'{exp_name}_summary.csv')
    return pd.read_csv(path)


# ═══════════════════════════════════════════
# Fig: Exp1 Cascade Comparison (bar chart)
# ═══════════════════════════════════════════
def fig_exp1_cascade():
    df = load_summary('exp1_core')
    stats = []
    for gw_raw, gw_name in [('ng','NG'), ('srl','SRL'), ('sbac','SBAC'), ('plangate_full','PlanGate')]:
        sub = df[df['gateway'] == gw_raw]
        stats.append({
            'gateway': gw_name,
            'cascade_mean': sub['cascade_failed'].mean(),
            'cascade_std': sub['cascade_failed'].std(),
        })
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    x = np.arange(len(stats))
    colors = [COLORS[s['gateway']] for s in stats]
    bars = ax.bar(x, [s['cascade_mean'] for s in stats],
                  yerr=[s['cascade_std'] for s in stats],
                  color=colors, edgecolor='black', linewidth=0.8,
                  capsize=4, width=0.6, zorder=3)
    for bar, s in zip(bars, stats):
        val = s['cascade_mean']
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + s['cascade_std'] + 3,
                f'{val:.1f}', ha='center', va='bottom', fontsize=9, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([s['gateway'] for s in stats])
    ax.set_ylabel('Cascade Failures (mean ± std)')
    ax.set_ylim(0, 160)
    ax.grid(axis='y', alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    save_fig(fig, 'mock_cascade_comparison')


# ═══════════════════════════════════════════
# Fig: Exp9 Scalability Stress Test (line chart)
# ═══════════════════════════════════════════
def fig_exp9_scalability():
    df = load_summary('exp9_scalestress')
    conc_levels = sorted(df['sweep_val'].unique())
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3.2))
    
    # Panel (a): Cascade failures
    for gw_raw, gw_name in [('ng','NG'), ('srl','SRL'), ('sbac','SBAC'), ('plangate_full','PlanGate')]:
        means, stds = [], []
        for c in conc_levels:
            sub = df[(df['gateway'] == gw_raw) & (df['sweep_val'] == c)]
            means.append(sub['cascade_failed'].mean())
            stds.append(sub['cascade_failed'].std())
        ax1.errorbar(conc_levels, means, yerr=stds, marker='o', markersize=5,
                     label=gw_name, color=COLORS[gw_name], linewidth=1.8, capsize=3)
    ax1.set_xlabel('Max Concurrency')
    ax1.set_ylabel('Cascade Failures')
    ax1.set_title('(a) Cascade Failures vs. Concurrency')
    ax1.legend(loc='upper left', framealpha=0.9)
    ax1.grid(alpha=0.3)
    ax1.set_xticks(conc_levels)
    
    # Panel (b): Effective goodput
    for gw_raw, gw_name in [('ng','NG'), ('srl','SRL'), ('sbac','SBAC'), ('plangate_full','PlanGate')]:
        means = []
        for c in conc_levels:
            sub = df[(df['gateway'] == gw_raw) & (df['sweep_val'] == c)]
            means.append(sub['effective_goodput_s'].mean())
        ax2.plot(conc_levels, means, marker='s', markersize=5,
                 label=gw_name, color=COLORS[gw_name], linewidth=1.8)
    ax2.set_xlabel('Max Concurrency')
    ax2.set_ylabel('Effective Goodput (GP/s)')
    ax2.set_title('(b) Effective Goodput vs. Concurrency')
    ax2.legend(loc='best', framealpha=0.9)
    ax2.grid(alpha=0.3)
    ax2.set_xticks(conc_levels)
    
    fig.tight_layout()
    save_fig(fig, 'exp9_scalability')


# ═══════════════════════════════════════════
# Fig: Exp10 Adversarial Robustness (grouped bar)
# ═══════════════════════════════════════════
def fig_exp10_adversarial():
    df = load_summary('exp10_adversarial')
    gateways = [('ng','NG'), ('srl','SRL'), ('sbac','SBAC'), ('plangate_full','PlanGate')]
    metrics = ['success', 'cascade_failed', 'effective_goodput_s']
    metric_labels = ['Task Success', 'Cascade Failures', 'Eff. Goodput (GP/s)']
    
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.0))
    x = np.arange(len(gateways))
    width = 0.55
    
    for i, (metric, label) in enumerate(zip(metrics, metric_labels)):
        ax = axes[i]
        means, stds, colors = [], [], []
        for gw_raw, gw_name in gateways:
            sub = df[df['gateway'] == gw_raw]
            means.append(sub[metric].mean())
            stds.append(sub[metric].std())
            colors.append(COLORS[gw_name])
        bars = ax.bar(x, means, yerr=stds, width=width, color=colors,
                      edgecolor='black', linewidth=0.6, capsize=3, zorder=3)
        for bar, m in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + stds[means.index(m)] + 1,
                    f'{m:.1f}', ha='center', va='bottom', fontsize=8, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels([g[1] for g in gateways], fontsize=8)
        ax.set_ylabel(label)
        ax.set_title(f'({"abc"[i]}) {label}')
        ax.grid(axis='y', alpha=0.3, zorder=0)
        ax.set_axisbelow(True)
    
    fig.tight_layout()
    save_fig(fig, 'exp10_adversarial')


# ═══════════════════════════════════════════
# Fig: Exp8 Discount Function Ablation (grouped bar)
# ═══════════════════════════════════════════
def fig_exp8_discount():
    df = load_summary('exp8_discountablation')
    funcs = ['quadratic', 'linear', 'exponential', 'logarithmic']
    func_labels = ['Quadratic ($K^2$)', 'Linear ($K$)', 'Exponential ($e^K$)', 'Logarithmic (ln)']
    func_colors = ['#2ca02c', '#1f77b4', '#ff7f0e', '#d62728']
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 3.0))
    x = np.arange(len(funcs))
    width = 0.55
    
    # (a) Cascade failures
    for i, func in enumerate(funcs):
        sub = df[df['gateway'] == func]
        mean_v = sub['cascade_failed'].mean()
        std_v = sub['cascade_failed'].std()
        bar = ax1.bar(x[i], mean_v, yerr=std_v, width=width, color=func_colors[i],
                      edgecolor='black', linewidth=0.6, capsize=4, zorder=3)
        ax1.text(x[i], mean_v + std_v + 1, f'{mean_v:.1f}', ha='center', va='bottom',
                 fontsize=9, fontweight='bold')
    ax1.set_xticks(x)
    ax1.set_xticklabels(func_labels, fontsize=8)
    ax1.set_ylabel('Cascade Failures')
    ax1.set_title('(a) Cascade Failures')
    ax1.grid(axis='y', alpha=0.3, zorder=0)
    ax1.set_axisbelow(True)
    
    # (b) Effective goodput
    for i, func in enumerate(funcs):
        sub = df[df['gateway'] == func]
        mean_v = sub['effective_goodput_s'].mean()
        std_v = sub['effective_goodput_s'].std()
        bar = ax2.bar(x[i], mean_v, yerr=std_v, width=width, color=func_colors[i],
                      edgecolor='black', linewidth=0.6, capsize=4, zorder=3)
        ax2.text(x[i], mean_v + std_v + 0.5, f'{mean_v:.1f}', ha='center', va='bottom',
                 fontsize=9, fontweight='bold')
    ax2.set_xticks(x)
    ax2.set_xticklabels(func_labels, fontsize=8)
    ax2.set_ylabel('Eff. Goodput (GP/s)')
    ax2.set_title('(b) Effective Goodput')
    ax2.grid(axis='y', alpha=0.3, zorder=0)
    ax2.set_axisbelow(True)
    
    fig.tight_layout()
    save_fig(fig, 'exp8_discount_ablation')


# ═══════════════════════════════════════════
# Fig: Cross-LLM Comparison (4-panel)
# ═══════════════════════════════════════════
def fig_cross_llm():
    providers = {
        'GLM-4-Flash\n(200 RPM, C=10)': 'results/exp_real3_glm/summary_all.csv',
        'DeepSeek-V3\n(60 RPM, C=3)': 'results/exp_real3_deepseek/summary_all.csv',
    }
    gw_order_real = ['ng', 'srl', 'mcpdp-real', 'mcpdp-real-no-sessioncap']
    gw_labels_real = ['NG', 'SRL', 'PlanGate', 'PG w/o SC']
    gw_colors_real = [COLORS['NG'], COLORS['SRL'], COLORS['PlanGate'], COLORS['PG w/o SC']]
    
    fig, axes = plt.subplots(2, 2, figsize=(8, 6))
    
    for p_idx, (prov_label, csv_path) in enumerate(providers.items()):
        df = pd.read_csv(csv_path)
        
        for gw_idx, gw in enumerate(gw_order_real):
            sub = df[df['gateway'] == gw]
            if len(sub) == 0: continue
            
            total = sub['agents'].iloc[0]
            succ_pct = sub['success'].mean() / total * 100
            succ_std = sub['success'].std() / total * 100
            cascade = sub['cascade_wasted_steps'].mean()
            cascade_std = sub['cascade_wasted_steps'].std()
            p95 = sub['e2e_p95_ms'].mean() / 1000  # to seconds
            p95_std = sub['e2e_p95_ms'].std() / 1000
            token_eff = (sub['agent_llm_tokens'] / sub['success']).mean()
            token_std = (sub['agent_llm_tokens'] / sub['success']).std()
            
            # (row, 0): Success rate
            ax = axes[p_idx, 0]
            ax.bar(gw_idx, succ_pct, yerr=succ_std, width=0.6,
                   color=gw_colors_real[gw_idx], edgecolor='black', linewidth=0.5,
                   capsize=3, zorder=3)
            ax.text(gw_idx, succ_pct + succ_std + 1, f'{succ_pct:.1f}%',
                    ha='center', va='bottom', fontsize=7.5, fontweight='bold')
            
            # (row, 1): Cascade waste
            ax = axes[p_idx, 1]
            ax.bar(gw_idx, cascade, yerr=cascade_std, width=0.6,
                   color=gw_colors_real[gw_idx], edgecolor='black', linewidth=0.5,
                   capsize=3, zorder=3)
            ax.text(gw_idx, cascade + cascade_std + 0.5, f'{cascade:.1f}',
                    ha='center', va='bottom', fontsize=7.5)
    
    for p_idx, prov_label in enumerate(providers.keys()):
        for col in range(2):
            ax = axes[p_idx, col]
            ax.set_xticks(range(len(gw_labels_real)))
            ax.set_xticklabels(gw_labels_real, fontsize=8)
            ax.grid(axis='y', alpha=0.3, zorder=0)
            ax.set_axisbelow(True)
        
        axes[p_idx, 0].set_ylabel(f'{prov_label}\nSuccess Rate (%)')
        axes[p_idx, 1].set_ylabel('Cascade Wasted Steps')
    
    axes[0, 0].set_title('(a) Task Success Rate')
    axes[0, 1].set_title('(b) Cascade Waste')
    
    fig.tight_layout()
    save_fig(fig, 'cross_llm_comparison')


# ═══════════════════════════════════════════
# Fig: Token Efficiency (both providers)
# ═══════════════════════════════════════════
def fig_token_efficiency():
    providers = {
        'GLM-4-Flash': 'results/exp_real3_glm/summary_all.csv',
        'DeepSeek-V3': 'results/exp_real3_deepseek/summary_all.csv',
    }
    gw_order = ['ng', 'srl', 'mcpdp-real', 'mcpdp-real-no-sessioncap']
    gw_labels = ['NG', 'SRL', 'PlanGate', 'PG w/o SC']
    gw_colors = [COLORS['NG'], COLORS['SRL'], COLORS['PlanGate'], COLORS['PG w/o SC']]
    
    fig, axes = plt.subplots(1, 2, figsize=(7, 3.0))
    
    for p_idx, (prov, csv_path) in enumerate(providers.items()):
        ax = axes[p_idx]
        df = pd.read_csv(csv_path)
        x = np.arange(len(gw_order))
        
        for gw_idx, gw in enumerate(gw_order):
            sub = df[df['gateway'] == gw]
            if len(sub) == 0: continue
            token_per_success = sub['agent_llm_tokens'] / sub['success']
            mean_v = token_per_success.mean()
            std_v = token_per_success.std()
            ax.bar(gw_idx, mean_v, yerr=std_v, width=0.55, color=gw_colors[gw_idx],
                   edgecolor='black', linewidth=0.5, capsize=3, zorder=3)
            ax.text(gw_idx, mean_v + std_v + 100, f'{mean_v:,.0f}',
                    ha='center', va='bottom', fontsize=7.5, fontweight='bold')
        
        ax.set_xticks(x)
        ax.set_xticklabels(gw_labels, fontsize=8)
        ax.set_ylabel('Tokens / Successful Task')
        ax.set_title(prov)
        ax.grid(axis='y', alpha=0.3, zorder=0)
        ax.set_axisbelow(True)
    
    fig.suptitle('Token Efficiency per Successful Task (lower is better)', fontsize=11, y=1.02)
    fig.tight_layout()
    save_fig(fig, 'chart4_token_efficiency')


# ═══════════════════════════════════════════
# Fig: Fairness - Step Distribution (boxplot)
# Using mock Exp1 data for 4-gateway comparison
# ═══════════════════════════════════════════
def fig_fairness_boxplot():
    """Boxplot of steps completed per session for all 4 gateways from Exp1."""
    exp1_dir = os.path.join('results', 'exp1_core')
    gw_map_exp1 = {
        'ng': 'NG', 'srl': 'SRL', 'sbac': 'SBAC', 'plangate_full': 'PlanGate'
    }
    
    all_steps = {}
    for gw in ['ng', 'srl', 'sbac', 'plangate_full']:
        steps = []
        for run in range(1, 6):
            # Use session-level CSV (has n_steps column)
            sess_csv = os.path.join(exp1_dir, f'{gw}_run{run}_sessions.csv')
            if os.path.exists(sess_csv):
                sdf = pd.read_csv(sess_csv)
                if 'n_steps' in sdf.columns:
                    steps.extend(sdf['n_steps'].tolist())
        if steps:
            all_steps[gw_map_exp1[gw]] = steps
    
    if not all_steps:
        print("  ⚠ No session-level data for fairness boxplot, skipping")
        return
    
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    data = [all_steps.get(gw, []) for gw in GW_ORDER if gw in all_steps]
    labels = [gw for gw in GW_ORDER if gw in all_steps]
    colors_list = [COLORS[gw] for gw in labels]
    
    bp = ax.boxplot(data, labels=labels, patch_artist=True, widths=0.5,
                    medianprops={'color': 'black', 'linewidth': 1.5},
                    whiskerprops={'linewidth': 1.0},
                    capprops={'linewidth': 1.0})
    for patch, col in zip(bp['boxes'], colors_list):
        patch.set_facecolor(col)
        patch.set_alpha(0.7)
    
    ax.set_ylabel('Steps Completed per Session')
    ax.grid(axis='y', alpha=0.3)
    fig.tight_layout()
    save_fig(fig, 'chart6_fairness')


# ═══════════════════════════════════════════
# Fig: Exp4 Ablation (grouped bar - 3 variants)
# ═══════════════════════════════════════════
def fig_exp4_ablation():
    df = load_summary('exp4_ablation')
    variants = [
        ('plangate_full', 'PlanGate Full', '#2ca02c'),
        ('wo_budgetlock', 'w/o BudgetLock', '#d62728'),
        ('wo_sessioncap', 'w/o SessionCap', '#1f77b4'),
    ]
    metrics = [
        ('success', 'Task Success'),
        ('cascade_failed', 'Cascade Failures'),
        ('effective_goodput_s', 'Eff. Goodput (GP/s)'),
    ]
    
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.0))
    x = np.arange(len(variants))
    width = 0.5
    
    for i, (metric, label) in enumerate(metrics):
        ax = axes[i]
        for j, (gw, gw_label, color) in enumerate(variants):
            sub = df[df['gateway'] == gw]
            mean_v = sub[metric].mean()
            std_v = sub[metric].std()
            bar = ax.bar(x[j], mean_v, yerr=std_v, width=width, color=color,
                         edgecolor='black', linewidth=0.6, capsize=4, zorder=3)
            ax.text(x[j], mean_v + std_v + 1, f'{mean_v:.1f}', ha='center',
                    va='bottom', fontsize=9, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels([v[1] for v in variants], fontsize=7.5, rotation=10)
        ax.set_ylabel(label)
        ax.set_title(f'({"abc"[i]}) {label}')
        ax.grid(axis='y', alpha=0.3, zorder=0)
        ax.set_axisbelow(True)
    
    fig.tight_layout()
    save_fig(fig, 'exp4_ablation')


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
if __name__ == '__main__':
    print("Generating paper figures...")
    print(f"  Output: {OUT_DIR}/")
    
    try:
        fig_exp1_cascade()
    except Exception as e:
        print(f"  ✗ Exp1 cascade: {e}")
    
    try:
        fig_exp9_scalability()
    except Exception as e:
        print(f"  ✗ Exp9 scalability: {e}")
    
    try:
        fig_exp10_adversarial()
    except Exception as e:
        print(f"  ✗ Exp10 adversarial: {e}")
    
    try:
        fig_exp8_discount()
    except Exception as e:
        print(f"  ✗ Exp8 discount: {e}")
    
    try:
        fig_cross_llm()
    except Exception as e:
        print(f"  ✗ Cross-LLM: {e}")
    
    try:
        fig_token_efficiency()
    except Exception as e:
        print(f"  ✗ Token efficiency: {e}")
    
    try:
        fig_fairness_boxplot()
    except Exception as e:
        print(f"  ✗ Fairness boxplot: {e}")
    
    try:
        fig_exp4_ablation()
    except Exception as e:
        print(f"  ✗ Exp4 ablation: {e}")
    
    print("\nDone!")
