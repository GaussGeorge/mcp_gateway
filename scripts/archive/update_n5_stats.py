#!/usr/bin/env python3
"""
Update paper statistics for N=5 real-LLM experiments.
Regenerates summary_all.csv, computes bootstrap CIs and permutation tests,
and outputs LaTeX-ready table rows for paper update.

Usage:
    python3 scripts/update_n5_stats.py
"""

import os, glob, re, sys
import numpy as np
import pandas as pd

GATEWAY_MAP = {
    'ng': 'NG', 'srl': 'SRL',
    'mcpdp-real': 'PlanGate', 'mcpdp-real-no-sessioncap': 'PG w/o SC'
}
GATEWAY_ORDER = ['NG', 'SRL', 'PlanGate', 'PG w/o SC']

BASE = os.path.join(os.path.dirname(__file__), '..')


def rebuild_summary(result_dir):
    """Rebuild summary_all.csv from all *_summary.csv files."""
    summary_path = os.path.join(result_dir, 'summary_all.csv')
    parts = []
    for f in sorted(glob.glob(os.path.join(result_dir, '*_summary.csv'))):
        if os.path.basename(f) == 'summary_all.csv':
            continue
        parts.append(pd.read_csv(f))
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    df.to_csv(summary_path, index=False)
    return df


def load_summary(result_dir):
    """Load and parse summary data."""
    df = rebuild_summary(result_dir)
    if df is None:
        return None
    rows = []
    for _, r in df.iterrows():
        gw_raw = r['gateway'].strip()
        gw = GATEWAY_MAP.get(gw_raw, gw_raw)
        rows.append({
            'gateway': gw,
            'success_rate': r['success'] / r['agents'] * 100,
            'success_count': r['success'],
            'cascade_waste': r['cascade_wasted_steps'],
            'eff_gp_per_s': r['eff_gp_per_s'],
            'e2e_p50_s': r['e2e_p50_ms'] / 1000,
            'e2e_p95_s': r['e2e_p95_ms'] / 1000,
            'agent_tokens': r['agent_llm_tokens'],
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
    return np.mean(data), np.percentile(boot_means, alpha), np.percentile(boot_means, 100 - alpha)


def permutation_test(a, b, n_perm=None):
    """Exact or approximate permutation test for difference of means."""
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    diff = a - b
    observed = np.mean(diff)

    if n <= 12:  # exact
        count = 0
        for mask in range(2**n):
            flipped = np.array([d if (mask >> i) & 1 == 0 else -d for i, d in enumerate(diff)])
            if np.mean(flipped) >= observed:
                count += 1
        return observed, count / (2**n)
    else:  # approximate
        rng = np.random.default_rng(42)
        n_perm = n_perm or 100000
        count = 0
        for _ in range(n_perm):
            signs = rng.choice([-1, 1], size=n)
            if np.mean(diff * signs) >= observed:
                count += 1
        return observed, count / n_perm


def analyze_provider(df, provider_name):
    """Full analysis for one provider."""
    print(f"\n{'='*70}")
    print(f"  {provider_name} — N per gateway")
    print(f"{'='*70}")

    for gw in GATEWAY_ORDER:
        sub = df[df['gateway'] == gw]
        if len(sub) > 0:
            sr = sub['success_rate']
            cw = sub['cascade_waste']
            gps = sub['eff_gp_per_s']
            p95 = sub['e2e_p95_s']
            toks = sub['agent_tokens'] / sub['success_count']

            print(f"\n{gw} (N={len(sub)}):")
            print(f"  Success Rate: {sr.mean():.1f}±{sr.std():.1f}%")
            print(f"  Cascade Waste: {cw.mean():.1f}±{cw.std():.1f}")
            print(f"  Eff GP/s: {gps.mean():.2f}±{gps.std():.2f}")
            print(f"  P95 (s): {p95.mean():.1f}±{p95.std():.1f}")
            print(f"  Tokens/Success: {toks.mean():.0f}±{toks.std():.0f}")

    # Bootstrap CIs
    print(f"\n{'='*70}")
    print(f"  Bootstrap 95% CI — {provider_name}")
    print(f"{'='*70}")
    for gw in GATEWAY_ORDER:
        sub = df[df['gateway'] == gw]
        if len(sub) < 2:
            continue
        sr_mean, sr_lo, sr_hi = bootstrap_ci(sub['success_rate'].values)
        cw_mean, cw_lo, cw_hi = bootstrap_ci(sub['cascade_waste'].values)
        print(f"  {gw}: SR={sr_mean:.1f}% [{sr_lo:.1f}, {sr_hi:.1f}]  "
              f"CW={cw_mean:.1f} [{cw_lo:.1f}, {cw_hi:.1f}]")

    # Permutation tests
    print(f"\n{'='*70}")
    print(f"  Permutation Tests — {provider_name}")
    print(f"{'='*70}")
    pg = df[df['gateway'] == 'PlanGate']
    if len(pg) >= 2:
        for gw in ['NG', 'SRL', 'PG w/o SC']:
            other = df[df['gateway'] == gw]
            if len(other) < 2:
                continue
            diff, p = permutation_test(pg['success_rate'].values, other['success_rate'].values)
            sig = '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else 'n.s.'
            print(f"  PlanGate vs {gw}: diff={diff:+.2f}pp, p={p:.4f} {sig}")

    # LaTeX table rows
    print(f"\n{'='*70}")
    print(f"  LaTeX Table Rows — {provider_name}")
    print(f"{'='*70}")
    for gw in GATEWAY_ORDER:
        sub = df[df['gateway'] == gw]
        if len(sub) == 0:
            continue
        sr = sub['success_rate']
        cw = sub['cascade_waste']
        gps = sub['eff_gp_per_s']
        p95 = sub['e2e_p95_s']
        n = len(sub)
        print(f"  & {gw:12s} & ${sr.mean():.1f}\\pm{sr.std():.1f}$ "
              f"& ${cw.mean():.1f}\\pm{cw.std():.1f}$ "
              f"& ${gps.mean():.2f}\\pm{gps.std():.2f}$ "
              f"& ${p95.mean():.1f}\\pm{p95.std():.1f}$ \\\\")

    # Token efficiency table
    print(f"\n  Token Efficiency LaTeX — {provider_name}")
    for gw in GATEWAY_ORDER:
        sub = df[df['gateway'] == gw]
        if len(sub) == 0:
            continue
        tps = sub['agent_tokens'] / sub['success_count']
        print(f"  & {gw:12s} & ${tps.mean():,.0f} \\pm {tps.std():,.0f}$ \\\\")

    # Bootstrap CI table rows
    print(f"\n  Bootstrap CI LaTeX — {provider_name}")
    for gw in GATEWAY_ORDER:
        sub = df[df['gateway'] == gw]
        if len(sub) < 2:
            continue
        mean, lo, hi = bootstrap_ci(sub['success_rate'].values)
        print(f"  & {gw:12s} & {mean:.1f}\\% & [{lo:.1f}, {hi:.1f}] \\\\")

    return df


def main():
    providers = [
        ('DeepSeek-V3', os.path.join(BASE, 'results', 'exp_real3_deepseek')),
        ('GLM-4-Flash', os.path.join(BASE, 'results', 'exp_real3_glm')),
    ]

    for name, path in providers:
        if not os.path.isdir(path):
            print(f"⚠ {name} directory not found: {path}")
            continue
        df = load_summary(path)
        if df is None:
            print(f"⚠ No data for {name}")
            continue

        print(f"\n{'#'*70}")
        print(f"  Provider: {name}")
        print(f"  Data directory: {path}")
        print(f"  Total runs: {len(df)}")
        for gw in GATEWAY_ORDER:
            n = len(df[df['gateway'] == gw])
            print(f"    {gw}: N={n}")
        print(f"{'#'*70}")

        analyze_provider(df, name)

    print(f"\n{'='*70}")
    print("✓ All statistics computed. Update paper tables manually.")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
