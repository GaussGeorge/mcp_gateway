#!/usr/bin/env python3
"""
Process all mock experiment results and print formatted LaTeX table data.
Run after all experiments complete. Reads from results/exp*_summary.csv files.
"""
import pandas as pd
import numpy as np
import os
import sys

def load_summary(path):
    if not os.path.exists(path):
        return None
    return pd.read_csv(path)

def stats(series):
    return series.mean(), series.std()

def fmt(v, decimals=1):
    return f"{v:.{decimals}f}"

# ── Exp1: Core Performance ──────────────────────────────────
def process_exp1():
    df = load_summary('results/exp1_core/exp1_core_summary.csv')
    if df is None:
        print("  [SKIP] exp1_core_summary.csv not found")
        return
    print("\n" + "="*70)
    print("  Exp1: Core Performance (Tab 3)")
    print("="*70)
    print(f"  {'Gateway':<12} {'Succ':>6} {'Rej':>7} {'Casc':>7} {'Eff.GP/s':>9} {'P50':>6} {'P95':>6} {'JFI_s':>6}")
    print("  " + "-"*60)
    for gw in ['ng', 'srl', 'sbac', 'plangate_full']:
        sub = df[df['gateway'] == gw]
        label = {'ng': 'NG', 'srl': 'SRL', 'sbac': 'SBAC', 'plangate_full': 'PlanGate'}[gw]
        s_m, _ = stats(sub['success'])
        r_m, _ = stats(sub['rejected_s0'])
        c_m, _ = stats(sub['cascade_failed'])
        g_m, _ = stats(sub['effective_goodput_s'])
        p50_m, _ = stats(sub['p50_ms'])
        p95_m, _ = stats(sub['p95_ms'])
        jfi_m, _ = stats(sub['jfi_steps'])
        print(f"  {label:<12} {s_m:>6.1f} {r_m:>7.1f} {c_m:>7.1f} {g_m:>9.1f} {p50_m:>6.0f} {p95_m:>6.0f} {jfi_m:>6.3f}")
        # LaTeX row
        if gw == 'plangate_full':
            print(f"    LaTeX: \\textbf{{PlanGate}} & \\textbf{{{s_m:.1f}}} & \\textbf{{{r_m:.1f}}} & \\textbf{{{c_m:.1f}}} & \\textbf{{{g_m:.1f}}} & \\textbf{{{p50_m:.0f}}} & \\textbf{{{p95_m:.0f}}} & \\textbf{{{jfi_m:.3f}}} \\\\")
        else:
            print(f"    LaTeX: {label:<9} & {s_m:.1f}  & {r_m:.1f} & {c_m:.1f} & {g_m:.1f}  & {p50_m:.0f}  & {p95_m:.0f}  & {jfi_m:.3f} \\\\")

# ── Exp4: Ablation Study ────────────────────────────────────
def process_exp4():
    df = load_summary('results/exp4_ablation/exp4_ablation_summary.csv')
    if df is None:
        print("\n  [SKIP] exp4_ablation_summary.csv not found")
        return
    print("\n" + "="*70)
    print("  Exp4: Ablation Study (Tab 4)")
    print("="*70)
    print(f"  {'Variant':<18} {'Succ':>6} {'Casc':>6} {'Eff.GP/s':>9} {'P50':>6} {'P95':>6} {'JFI_s':>6}")
    print("  " + "-"*60)
    variants = {
        'plangate_full': 'PlanGate Full',
        'plangate_wo_budgetlock': 'wo-BudgetLock',
        'plangate_wo_sessioncap': 'wo-SessionCap',
    }
    # Check column naming
    gw_col = 'gateway'
    for gw, label in variants.items():
        sub = df[df[gw_col] == gw]
        if len(sub) == 0:
            # Try alternative naming
            for alt in [gw.replace('plangate_', 'mcpdp_'), gw]:
                sub = df[df[gw_col] == alt]
                if len(sub) > 0:
                    break
        if len(sub) == 0:
            print(f"  {label:<18}  [no data for {gw}]")
            continue
        s_m, _ = stats(sub['success'])
        c_m, _ = stats(sub['cascade_failed'])
        g_m, _ = stats(sub['effective_goodput_s'])
        p50_m, _ = stats(sub['p50_ms'])
        p95_m, _ = stats(sub['p95_ms'])
        jfi_m, _ = stats(sub['jfi_steps'])
        print(f"  {label:<18} {s_m:>6.1f} {c_m:>6.1f} {g_m:>9.1f} {p50_m:>6.0f} {p95_m:>6.0f} {jfi_m:>6.3f}")
        print(f"    LaTeX: {label:<16} & {s_m:.1f}  & {c_m:.1f} & {g_m:.1f}  & {p50_m:.1f}  & {p95_m:.0f}   & {jfi_m:.3f} \\\\")

# ── Exp8: Discount Function Ablation ────────────────────────
def process_exp8():
    df = load_summary('results/exp8_discountablation/exp8_discountablation_summary.csv')
    if df is None:
        print("\n  [SKIP] exp8_discountablation_summary.csv not found")
        return
    print("\n" + "="*70)
    print("  Exp8: Discount Function Ablation")
    print("="*70)
    # Sweep key is discount_func
    if 'sweep_val' in df.columns:
        for val in df['sweep_val'].unique():
            sub = df[df['sweep_val'] == val]
            c_m, _ = stats(sub['cascade_failed'])
            g_m, _ = stats(sub['effective_goodput_s'])
            p95_m, _ = stats(sub['p95_ms'])
            jfi_m, _ = stats(sub['jfi_steps'])
            s_m, _ = stats(sub['success'])
            print(f"  {str(val):<15} succ={s_m:.1f} casc={c_m:.1f} gp/s={g_m:.1f} p95={p95_m:.0f} jfi={jfi_m:.3f}")
    else:
        print("  [INFO] No sweep_val column; dumping all gateways")
        for gw in df['gateway'].unique():
            sub = df[df['gateway'] == gw]
            c_m, _ = stats(sub['cascade_failed'])
            g_m, _ = stats(sub['effective_goodput_s'])
            p95_m, _ = stats(sub['p95_ms'])
            jfi_m, _ = stats(sub['jfi_steps'])
            s_m, _ = stats(sub['success'])
            print(f"  {gw:<20} succ={s_m:.1f} casc={c_m:.1f} gp/s={g_m:.1f} p95={p95_m:.0f} jfi={jfi_m:.3f}")

# ── Exp9: Scalability Stress ─────────────────────────────────
def process_exp9():
    df = load_summary('results/exp9_scalestress/exp9_scalestress_summary.csv')
    if df is None:
        print("\n  [SKIP] exp9_scalestress_summary.csv not found")
        return
    print("\n" + "="*70)
    print("  Exp9: Scalability Stress Test")
    print("="*70)
    # Sweep values are concurrency levels
    conc_levels = sorted(df['sweep_val'].unique()) if 'sweep_val' in df.columns else []
    for gw in ['plangate_full', 'ng', 'sbac']:
        label = {'plangate_full': 'PlanGate', 'ng': 'NG', 'sbac': 'SBAC'}.get(gw, gw)
        print(f"\n  {label} Cascade Failures by Concurrency:")
        for conc in conc_levels:
            sub = df[(df['gateway'] == gw) & (df['sweep_val'] == conc)]
            if len(sub) == 0:
                continue
            c_m, c_s = stats(sub['cascade_failed'])
            g_m, _ = stats(sub['effective_goodput_s'])
            print(f"    conc={conc:.0f}: cascade={c_m:.1f}±{c_s:.1f}  gp/s={g_m:.1f}")

# ── Exp10: Adversarial Robustness ────────────────────────────
def process_exp10():
    df = load_summary('results/exp10_adversarial/exp10_adversarial_summary.csv')
    if df is None:
        print("\n  [SKIP] exp10_adversarial_summary.csv not found")
        return
    print("\n" + "="*70)
    print("  Exp10: Adversarial Robustness")
    print("="*70)
    for gw in df['gateway'].unique():
        sub = df[df['gateway'] == gw]
        label = {'plangate_full': 'PlanGate', 'ng': 'NG', 'srl': 'SRL', 'sbac': 'SBAC'}.get(gw, gw)
        s_m, s_s = stats(sub['success'])
        c_m, c_s = stats(sub['cascade_failed'])
        g_m, g_s = stats(sub['effective_goodput_s'])
        print(f"  {label:<12} succ={s_m:.1f}±{s_s:.1f}  casc={c_m:.1f}±{c_s:.1f}  gp/s={g_m:.1f}±{g_s:.1f}")

if __name__ == '__main__':
    print("Mock Experiment Results Summary")
    print("=" * 70)
    process_exp1()
    process_exp4()
    process_exp8()
    process_exp9()
    process_exp10()
    print("\n" + "=" * 70)
    print("Done. Copy LaTeX rows to paper/plangate_paper.tex as needed.")
