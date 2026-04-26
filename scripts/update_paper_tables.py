#!/usr/bin/env python3
"""
Auto-update paper/plangate_paper.tex tables with fresh experiment data.
Reads summary CSVs and replaces table rows + associated text in the paper.

Usage:
    python3 scripts/update_paper_tables.py [--dry-run]
"""
import pandas as pd
import numpy as np
import re
import sys
import os
import argparse
import shutil

TEX_PATH = "paper/plangate_paper.tex"

def load_summary(path):
    if not os.path.exists(path):
        return None
    return pd.read_csv(path)

def stats(series):
    return series.mean(), series.std()

# ══════════════════════════════════════════════════════════════
#  Exp4: Ablation Study
# ══════════════════════════════════════════════════════════════
def build_exp4_rows(df):
    """Build new Exp4 table rows from summary data."""
    variants = {
        'plangate_full': 'PlanGate Full',
        'plangate_wo_budgetlock': 'wo-BudgetLock',
        'plangate_wo_sessioncap': 'wo-SessionCap',
    }
    rows = []
    for gw, label in variants.items():
        sub = df[df['gateway'] == gw]
        if len(sub) == 0:
            for alt in [gw.replace('plangate_', 'mcpdp_'), gw]:
                sub = df[df['gateway'] == alt]
                if len(sub) > 0:
                    break
        if len(sub) == 0:
            print(f"  [WARN] No Exp4 data for {gw}")
            continue
        s_m = sub['success'].mean()
        c_m = sub['cascade_failed'].mean()
        g_m = sub['effective_goodput_s'].mean()
        p50_m = sub['p50_ms'].mean()
        p95_m = sub['p95_ms'].mean()
        jfi_m = sub['jfi_steps'].mean()
        rows.append((label, s_m, c_m, g_m, p50_m, p95_m, jfi_m))
    return rows

def format_exp4_latex(rows):
    lines = []
    for label, s, c, g, p50, p95, jfi in rows:
        lines.append(f"{label:<16} & {s:.1f}  & {c:.1f} & {g:.1f}  & {p50:.1f}  & {p95:.0f}   & {jfi:.3f} \\\\")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
#  Exp8: Discount Function Ablation
# ══════════════════════════════════════════════════════════════
def build_exp8_rows(df):
    """Build Exp8 table rows."""
    func_map = {
        'quadratic': ('\\textbf{Quadratic} ($K^2$)', True),
        'linear': ('Linear ($K$)', False),
        'exponential': ('Exponential ($e^K$)', False),
        'logarithmic': ('Logarithmic ($\\ln(1{+}K)$)', False),
    }
    rows = []
    sweep_col = 'sweep_val' if 'sweep_val' in df.columns else 'gateway'
    for func_name, (label, is_best) in func_map.items():
        sub = df[df[sweep_col] == func_name]
        if len(sub) == 0:
            sub = df[df[sweep_col].str.contains(func_name, case=False, na=False)]
        if len(sub) == 0:
            print(f"  [WARN] No Exp8 data for {func_name}")
            continue
        c_m = sub['cascade_failed'].mean()
        g_m = sub['effective_goodput_s'].mean()
        p95_m = sub['p95_ms'].mean()
        jfi_m = sub['jfi_steps'].mean()
        s_m = sub['success'].mean()
        rows.append((label, is_best, s_m, c_m, g_m, p95_m, jfi_m))
    return rows

def format_exp8_latex(rows):
    lines = []
    for label, is_best, s, c, g, p95, jfi in rows:
        if is_best:
            lines.append(f"{label}    & \\textbf{{{c:.1f}}}  & \\textbf{{{g:.1f}}} & {p95:.0f}  & {jfi:.3f} \\\\")
        else:
            lines.append(f"{label}{'':>20} & {c:.1f}           & {g:.1f}          & {p95:.0f}  & {jfi:.3f} \\\\")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
#  Exp9: Scalability Stress
# ══════════════════════════════════════════════════════════════
def build_exp9_data(df):
    """Build Exp9 data dict: {gateway: {conc: (casc_mean, gps_mean)}}"""
    data = {}
    conc_col = 'sweep_val'
    for gw in ['plangate_full', 'ng', 'sbac']:
        gw_data = {}
        for conc in sorted(df[conc_col].unique()):
            sub = df[(df['gateway'] == gw) & (df[conc_col] == conc)]
            if len(sub) == 0:
                continue
            gw_data[int(conc)] = (sub['cascade_failed'].mean(), sub['effective_goodput_s'].mean())
        data[gw] = gw_data
    return data

def format_exp9_latex(data, conc_levels=None):
    """Format Exp9 table body for the paper."""
    if conc_levels is None:
        all_conc = set()
        for gw_data in data.values():
            all_conc.update(gw_data.keys())
        conc_levels = sorted(all_conc)

    header = "\\textbf{Concurrency} & " + " & ".join(f"\\textbf{{{c}}}" for c in conc_levels) + " \\\\"

    lines = [header, "\\midrule"]

    # PlanGate Cascade
    lines.append("\\multicolumn{" + str(len(conc_levels)+1) + "}{@{}l}{\\textit{PlanGate Cascade Failures}} \\\\")
    pg = data.get('plangate_full', {})
    vals = " & ".join(f"{pg.get(c, (0,0))[0]:.1f}" for c in conc_levels)
    lines.append(f"\\quad Mean & {vals} \\\\")

    lines.append("\\midrule")

    # PlanGate GP/s
    lines.append("\\multicolumn{" + str(len(conc_levels)+1) + "}{@{}l}{\\textit{PlanGate Eff.~GP/s}} \\\\")
    vals = " & ".join(f"{pg.get(c, (0,0))[1]:.1f}" for c in conc_levels)
    lines.append(f"\\quad Mean & {vals} \\\\")

    lines.append("\\midrule")

    # NG Cascade
    lines.append("\\multicolumn{" + str(len(conc_levels)+1) + "}{@{}l}{\\textit{NG Cascade Failures}} \\\\")
    ng = data.get('ng', {})
    vals = " & ".join(f"{ng.get(c, (0,0))[0]:.1f}" for c in conc_levels)
    lines.append(f"\\quad Mean & {vals} \\\\")

    lines.append("\\midrule")

    # SBAC Cascade
    lines.append("\\multicolumn{" + str(len(conc_levels)+1) + "}{@{}l}{\\textit{SBAC Cascade Failures}} \\\\")
    sbac = data.get('sbac', {})
    vals = " & ".join(f"{sbac.get(c, (0,0))[0]:.1f}" for c in conc_levels)
    lines.append(f"\\quad Mean & {vals} \\\\")

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
#  Exp10: Adversarial Robustness
# ══════════════════════════════════════════════════════════════
def build_exp10_rows(df):
    """Build Exp10 table rows."""
    rows = []
    for gw in ['ng', 'srl', 'sbac', 'plangate_full']:
        sub = df[df['gateway'] == gw]
        if len(sub) == 0:
            continue
        label = {'ng': 'NG', 'srl': 'SRL', 'sbac': 'SBAC', 'plangate_full': 'PlanGate'}[gw]
        s_m = sub['success'].mean()
        c_m = sub['cascade_failed'].mean()
        g_m = sub['effective_goodput_s'].mean()
        jfi_m = sub['jfi_steps'].mean()
        rows.append((label, gw == 'plangate_full', s_m, c_m, g_m, jfi_m))
    return rows

def format_exp10_latex(rows):
    lines = []
    for label, is_pg, s, c, g, jfi in rows:
        if is_pg:
            lines.append(f"\\textbf{{PlanGate}} & \\textbf{{{s:.1f}}} & \\textbf{{{c:.1f}}} & \\textbf{{{g:.1f}}} & \\textbf{{{jfi:.3f}}} \\\\")
        else:
            lines.append(f"{label:<9} & {s:.1f}  & {c:.1f}  & {g:.1f}  & {jfi:.3f} \\\\")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  Replace table content in TeX
# ══════════════════════════════════════════════════════════════
def replace_table_rows(tex, label, new_rows_text):
    """Replace table row block between \\midrule and \\bottomrule for the table with given label."""
    # Find the table with the matching label
    label_pattern = re.escape(label)
    label_match = re.search(label_pattern, tex)
    if not label_match:
        print(f"  [ERROR] Label {label} not found in paper")
        return tex

    # Find the \midrule ... \bottomrule block after the label
    after_label = tex[label_match.start():]
    midrule_match = re.search(r'\\midrule\n(.*?)\\bottomrule', after_label, re.DOTALL)
    if not midrule_match:
        print(f"  [ERROR] Could not find \\midrule...\\bottomrule for {label}")
        return tex

    old_rows = midrule_match.group(1)
    start = label_match.start() + midrule_match.start(1)
    end = label_match.start() + midrule_match.end(1)

    tex = tex[:start] + new_rows_text + "\n" + tex[end:]
    return tex


# ══════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Auto-update paper tables from experiment data")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    if not os.path.exists(TEX_PATH):
        print(f"Error: {TEX_PATH} not found. Run from project root.")
        sys.exit(1)

    with open(TEX_PATH, 'r', encoding='utf-8') as f:
        tex = f.read()

    original = tex
    changes = []

    # ── Exp4 ──
    df4 = load_summary('results/exp4_ablation/exp4_ablation_summary.csv')
    if df4 is not None:
        rows = build_exp4_rows(df4)
        if rows:
            new_text = format_exp4_latex(rows)
            tex = replace_table_rows(tex, '\\label{tab:ablation}', new_text)
            changes.append(f"Exp4: Updated {len(rows)} ablation rows")
            # Update text references
            full = [r for r in rows if r[0] == 'PlanGate Full'][0] if any(r[0] == 'PlanGate Full' for r in rows) else None
            wobl = [r for r in rows if r[0] == 'wo-BudgetLock'][0] if any(r[0] == 'wo-BudgetLock' for r in rows) else None
            if full and wobl:
                print(f"  Exp4 PlanGate Full: succ={full[1]:.1f}, casc={full[2]:.1f}, gp/s={full[3]:.1f}")
                print(f"  Exp4 wo-BudgetLock: succ={wobl[1]:.1f}, casc={wobl[2]:.1f}, gp/s={wobl[3]:.1f}")
                ratio = full[3] / wobl[3] if wobl[3] > 0 else float('inf')
                print(f"  [INFO] Degradation ratio (Full/wo-BL): {ratio:.1f}x")
    else:
        print("  [SKIP] Exp4 data not available yet")

    # ── Exp8 ──
    df8 = load_summary('results/exp8_discountablation/exp8_discountablation_summary.csv')
    if df8 is not None:
        rows = build_exp8_rows(df8)
        if rows:
            new_text = format_exp8_latex(rows)
            tex = replace_table_rows(tex, '\\label{tab:discount}', new_text)
            changes.append(f"Exp8: Updated {len(rows)} discount rows")
            for label, is_best, s, c, g, p95, jfi in rows:
                clean = re.sub(r'\\textbf\{(.+?)\}', r'\1', label)
                clean = re.sub(r'\$.*?\$', '', clean).strip()
                print(f"  Exp8 {clean}: succ={s:.1f}, casc={c:.1f}, gp/s={g:.1f}")
    else:
        print("  [SKIP] Exp8 data not available yet")

    # ── Exp9 ──
    df9 = load_summary('results/exp9_scalestress/exp9_scalestress_summary.csv')
    if df9 is not None:
        data = build_exp9_data(df9)
        if data:
            new_text = format_exp9_latex(data)
            tex = replace_table_rows(tex, '\\label{tab:scalestress}', new_text)
            changes.append(f"Exp9: Updated scalability data")
            for gw, gw_data in data.items():
                label = {'plangate_full': 'PlanGate', 'ng': 'NG', 'sbac': 'SBAC'}[gw]
                for conc, (casc, gps) in sorted(gw_data.items()):
                    print(f"  Exp9 {label} @{conc}: casc={casc:.1f}, gp/s={gps:.1f}")
    else:
        print("  [SKIP] Exp9 data not available yet")

    # ── Exp10 ──
    df10 = load_summary('results/exp10_adversarial/exp10_adversarial_summary.csv')
    if df10 is not None:
        rows = build_exp10_rows(df10)
        if rows:
            new_text = format_exp10_latex(rows)
            tex = replace_table_rows(tex, '\\label{tab:adversarial}', new_text)
            changes.append(f"Exp10: Updated {len(rows)} adversarial rows")
            for label, is_pg, s, c, g, jfi in rows:
                print(f"  Exp10 {label}: succ={s:.1f}, casc={c:.1f}, gp/s={g:.1f}, jfi={jfi:.3f}")
            # Remove TODO comments
            tex = tex.replace("% TODO: Fill in with actual Exp10 data when available\n", "")
            tex = tex.replace("% TODO: Update text with actual numbers\n", "")
    else:
        print("  [SKIP] Exp10 data not available yet")

    # ── Write ──
    if tex != original:
        if args.dry_run:
            print(f"\n[DRY RUN] Would apply {len(changes)} changes:")
            for c in changes:
                print(f"  - {c}")
        else:
            # Backup
            backup = TEX_PATH + ".bak"
            shutil.copy2(TEX_PATH, backup)
            with open(TEX_PATH, 'w', encoding='utf-8') as f:
                f.write(tex)
            print(f"\n✓ Applied {len(changes)} changes to {TEX_PATH}")
            print(f"  Backup: {backup}")
            for c in changes:
                print(f"  - {c}")
    else:
        print("\nNo changes needed (no new data available or tables already up-to-date)")


if __name__ == '__main__':
    main()
