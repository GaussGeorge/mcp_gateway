#!/usr/bin/env python3
"""
Generate concurrency sweep figures and bootstrap confidence intervals
for PlanGate paper.
"""

import os, sys, re, glob
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from scipy import stats

# ── Output dirs ──
FIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'paper', 'figures')
RES_DIR = os.path.join(os.path.dirname(__file__), '..', 'results', 'paper_figures')
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(os.path.join(RES_DIR, 'PNG'), exist_ok=True)
os.makedirs(os.path.join(RES_DIR, 'PDF'), exist_ok=True)

# ── Style ──
plt.rcParams.update({
    'font.size': 11, 'font.family': 'serif',
    'axes.linewidth': 1.2, 'figure.dpi': 300,
    'savefig.bbox': 'tight', 'savefig.pad_inches': 0.05,
})
COLORS = {
    'NG': '#e74c3c',      # red
    'SRL': '#f39c12',     # orange
    'PlanGate': '#27ae60', # green
    'PG w/o SC': '#2980b9', # blue
}
MARKERS = {'NG': 'o', 'SRL': 's', 'PlanGate': 'D', 'PG w/o SC': '^'}
GATEWAY_MAP = {
    'ng': 'NG', 'srl': 'SRL',
    'mcpdp-real': 'PlanGate', 'mcpdp-real-no-sessioncap': 'PG w/o SC'
}


# ── 1. Load concurrency sweep data ──
def load_conc_sweep(sweep_dir):
    """Parse summary CSVs from concurrency sweep directory."""
    rows = []
    for f in sorted(glob.glob(os.path.join(sweep_dir, '*_summary.csv'))):
        df = pd.read_csv(f)
        for _, r in df.iterrows():
            gw_raw = r['gateway']
            # Parse gateway name and concurrency level from gateway field like ng_c3
            m = re.match(r'^(.+)_c(\d+)$', gw_raw)
            if m:
                gw_name = GATEWAY_MAP.get(m.group(1), m.group(1))
                conc = int(m.group(2))
                rows.append({
                    'gateway': gw_name, 'conc': conc,
                    'success': r['success'], 'partial': r['partial'],
                    'all_rejected': r['all_rejected'],
                    'cascade_waste': r['cascade_wasted_steps'],
                    'eff_gp_per_s': r['eff_gp_per_s'],
                    'e2e_p50_ms': r['e2e_p50_ms'],
                    'e2e_p95_ms': r['e2e_p95_ms'],
                    'elapsed_s': r['elapsed_s'],
                })
    return pd.DataFrame(rows)


def plot_conc_sweep(df):
    """Generate concurrency sensitivity sweep figure (2 subplots)."""
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))

    conc_levels = sorted(df['conc'].unique())

    # (a) Success rate
    ax = axes[0]
    for gw in ['NG', 'SRL', 'PlanGate', 'PG w/o SC']:
        sub = df[df['gateway'] == gw].sort_values('conc')
        ax.plot(sub['conc'], sub['success'] / 50 * 100,
                marker=MARKERS[gw], color=COLORS[gw], label=gw,
                linewidth=2, markersize=7)
    ax.set_xlabel('Concurrency Level')
    ax.set_ylabel('Success Rate (%)')
    ax.set_title('(a) Task Success Rate')
    ax.set_xticks(conc_levels)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # (b) Cascade waste
    ax = axes[1]
    for gw in ['NG', 'SRL', 'PlanGate', 'PG w/o SC']:
        sub = df[df['gateway'] == gw].sort_values('conc')
        ax.plot(sub['conc'], sub['cascade_waste'],
                marker=MARKERS[gw], color=COLORS[gw], label=gw,
                linewidth=2, markersize=7)
    ax.set_xlabel('Concurrency Level')
    ax.set_ylabel('Cascade Wasted Steps')
    ax.set_title('(b) Cascade Waste')
    ax.set_xticks(conc_levels)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # (c) Effective GP/s
    ax = axes[2]
    for gw in ['NG', 'SRL', 'PlanGate', 'PG w/o SC']:
        sub = df[df['gateway'] == gw].sort_values('conc')
        ax.plot(sub['conc'], sub['eff_gp_per_s'],
                marker=MARKERS[gw], color=COLORS[gw], label=gw,
                linewidth=2, markersize=7)
    ax.set_xlabel('Concurrency Level')
    ax.set_ylabel('Effective GP/s')
    ax.set_title('(c) Effective Goodput')
    ax.set_xticks(conc_levels)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    for ext, d in [('pdf', FIG_DIR), ('pdf', os.path.join(RES_DIR, 'PDF')),
                   ('png', os.path.join(RES_DIR, 'PNG'))]:
        fig.savefig(os.path.join(d, f'conc_sweep_deepseek.{ext}'), dpi=300)
    plt.close()
    print(f"✓ Concurrency sweep figure saved")


# ── 2. Bootstrap CI for N=3 real results ──
def load_real_summary(summary_path):
    """Load summary_all.csv for real experiments."""
    df = pd.read_csv(summary_path)
    rows = []
    for _, r in df.iterrows():
        gw_raw = r['gateway'].strip()
        gw = GATEWAY_MAP.get(gw_raw, gw_raw)
        rows.append({
            'gateway': gw,
            'success_rate': r['success'] / r['agents'] * 100,
            'cascade_waste': r['cascade_wasted_steps'],
            'eff_gp_per_s': r['eff_gp_per_s'],
            'e2e_p95_s': r['e2e_p95_ms'] / 1000,
            'agent_tokens': r['agent_llm_tokens'],
            'success_count': r['success'],
        })
    return pd.DataFrame(rows)


def bootstrap_ci(data, n_bootstrap=10000, ci=95):
    """Compute bootstrap confidence interval."""
    rng = np.random.default_rng(42)
    boot_means = np.array([
        np.mean(rng.choice(data, size=len(data), replace=True))
        for _ in range(n_bootstrap)
    ])
    alpha = (100 - ci) / 2
    lo = np.percentile(boot_means, alpha)
    hi = np.percentile(boot_means, 100 - alpha)
    return np.mean(data), lo, hi


def compute_bootstrap_stats(df, provider_name):
    """Compute bootstrap CIs for each gateway's metrics."""
    print(f"\n{'='*60}")
    print(f"Bootstrap 95% CI — {provider_name} (N=3 runs)")
    print(f"{'='*60}")

    results = []
    for gw in df['gateway'].unique():
        sub = df[df['gateway'] == gw]
        n = len(sub)

        sr = sub['success_rate'].values
        cw = sub['cascade_waste'].values
        gps = sub['eff_gp_per_s'].values

        sr_mean, sr_lo, sr_hi = bootstrap_ci(sr)
        cw_mean, cw_lo, cw_hi = bootstrap_ci(cw)
        gps_mean, gps_lo, gps_hi = bootstrap_ci(gps)

        results.append({
            'Gateway': gw, 'N': n,
            'Success%': f'{sr_mean:.1f}', 'SR_CI': f'[{sr_lo:.1f}, {sr_hi:.1f}]',
            'CascWaste': f'{cw_mean:.1f}', 'CW_CI': f'[{cw_lo:.1f}, {cw_hi:.1f}]',
            'GP/s': f'{gps_mean:.2f}', 'GP_CI': f'[{gps_lo:.2f}, {gps_hi:.2f}]',
        })

        print(f"\n{gw} (N={n}):")
        print(f"  Success Rate: {sr_mean:.1f}% 95%CI [{sr_lo:.1f}, {sr_hi:.1f}]")
        print(f"  Cascade Waste: {cw_mean:.1f} 95%CI [{cw_lo:.1f}, {cw_hi:.1f}]")
        print(f"  Eff GP/s: {gps_mean:.3f} 95%CI [{gps_lo:.3f}, {gps_hi:.3f}]")

    return pd.DataFrame(results)


def wilcoxon_or_permutation_test(df, metric='success_rate'):
    """
    For N=3 per group, Wilcoxon signed-rank is underpowered.
    We use permutation test (exact) for paired comparison.
    """
    print(f"\n{'='*60}")
    print(f"Permutation Tests (PlanGate vs baselines, metric={metric})")
    print(f"{'='*60}")

    gateways = df['gateway'].unique()
    plangate_key = 'PlanGate' if 'PlanGate' in gateways else None
    if plangate_key is None:
        print("No PlanGate found, skipping.")
        return

    pg_vals = df[df['gateway'] == plangate_key][metric].values
    results = []

    for gw in gateways:
        if gw == plangate_key:
            continue
        other_vals = df[df['gateway'] == gw][metric].values
        n = min(len(pg_vals), len(other_vals))
        if n < 2:
            continue

        # Paired difference
        diff = pg_vals[:n] - other_vals[:n]
        observed_diff = np.mean(diff)

        # Exact permutation test: enumerate all 2^n sign flips
        count_extreme = 0
        for mask in range(2**n):
            flipped = np.array([d if (mask >> i) & 1 == 0 else -d
                                for i, d in enumerate(diff)])
            if np.mean(flipped) >= observed_diff:
                count_extreme += 1
        p_val = count_extreme / (2**n)

        sig = '***' if p_val < 0.001 else '**' if p_val < 0.01 else '*' if p_val < 0.05 else 'n.s.'
        results.append({
            'Comparison': f'PlanGate vs {gw}',
            'Mean Diff': f'{observed_diff:.2f}',
            'p-value': f'{p_val:.4f}',
            'Significance': sig
        })
        print(f"  PlanGate vs {gw}: diff={observed_diff:.2f}, p={p_val:.4f} {sig}")

    return pd.DataFrame(results) if results else None


def main():
    base = os.path.join(os.path.dirname(__file__), '..')

    # ── Concurrency Sweep ──
    sweep_dir = os.path.join(base, 'results', 'exp_conc_sweep_deepseek')
    if os.path.isdir(sweep_dir):
        df_sweep = load_conc_sweep(sweep_dir)
        print(f"Loaded {len(df_sweep)} concurrency sweep records")
        print(df_sweep.to_string(index=False))
        plot_conc_sweep(df_sweep)

        # Save sweep summary CSV
        sweep_csv = os.path.join(sweep_dir, 'conc_sweep_summary.csv')
        df_sweep.to_csv(sweep_csv, index=False)
        print(f"✓ Sweep summary saved: {sweep_csv}")
    else:
        print(f"⚠ Sweep dir not found: {sweep_dir}")

    # ── Bootstrap CI for DeepSeek N=3 ──
    ds_summary = os.path.join(base, 'results', 'exp_real3_deepseek', 'summary_all.csv')
    if os.path.isfile(ds_summary):
        df_ds = load_real_summary(ds_summary)
        stats_ds = compute_bootstrap_stats(df_ds, "DeepSeek-V3")
        wilcoxon_or_permutation_test(df_ds, 'success_rate')

    # ── Bootstrap CI for GLM N=3 ──
    glm_summary = os.path.join(base, 'results', 'exp_real3_glm', 'summary_all.csv')
    if os.path.isfile(glm_summary):
        df_glm = load_real_summary(glm_summary)
        stats_glm = compute_bootstrap_stats(df_glm, "GLM-4-Flash")
        wilcoxon_or_permutation_test(df_glm, 'success_rate')

    # ── LaTeX table for concurrency sweep ──
    if os.path.isdir(sweep_dir):
        print(f"\n{'='*60}")
        print("LaTeX Table: Concurrency Sweep (DeepSeek-V3)")
        print(f"{'='*60}")
        print(r"\begin{table}[t]")
        print(r"\caption{Concurrency sensitivity sweep on DeepSeek-V3 (60 RPM, 50 agents per run).}")
        print(r"\label{tab:concsweep}")
        print(r"\small")
        print(r"\begin{tabular}{@{}llrrrrr@{}}")
        print(r"\toprule")
        print(r"\textbf{C} & \textbf{Gateway} & \textbf{Succ.\%} & \textbf{Casc.} & \textbf{GP/s} & \textbf{P50 (s)} & \textbf{P95 (s)} \\")
        print(r"\midrule")
        df_s = df_sweep.sort_values(['conc', 'gateway'])
        for conc in sorted(df_s['conc'].unique()):
            sub = df_s[df_s['conc'] == conc]
            for i, (_, r) in enumerate(sub.iterrows()):
                c_str = str(conc) if i == 0 else ''
                print(f"{c_str} & {r['gateway']} & {r['success']/50*100:.0f} & {r['cascade_waste']:.0f} & {r['eff_gp_per_s']:.2f} & {r['e2e_p50_ms']/1000:.1f} & {r['e2e_p95_ms']/1000:.1f} \\\\")
            if conc < max(df_s['conc'].unique()):
                print(r"\midrule")
        print(r"\bottomrule")
        print(r"\end{tabular}")
        print(r"\end{table}")

    print("\n✓ All done.")


if __name__ == '__main__':
    main()
