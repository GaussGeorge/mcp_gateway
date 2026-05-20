#!/usr/bin/env python3
"""
plot_rajomon_sensitivity.py — Rajomon price_step 敏感性曲线图
============================================================
用 rajomon_sensitivity.csv 绘制 ABD vs price_step, 加 PlanGate 参考线。
承担 baseline fairness defense 角色, 正文图而非附录图。

用法:
  python scripts/plot_rajomon_sensitivity.py
"""

import csv
import os
import statistics
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
CSV_PATH = os.path.join(ROOT_DIR, "results", "exp_rajomon_sensitivity", "rajomon_sensitivity.csv")
OUTPUT_DIR = os.path.join(ROOT_DIR, "results", "paper_figures")

# PlanGate Week 4 formal results
PLANGATE_ABD = 18.9
NG_ABD = 65.5


def load_data():
    data = defaultdict(list)
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ps = int(row["price_step"])
            data[ps].append({
                "abd_total": float(row["abd_total"]),
                "abd_ps": float(row["abd_ps"]),
                "abd_react": float(row["abd_react"]),
                "success_rate": float(row["success_rate"]),
                "goodput": float(row["goodput"]),
            })
    return dict(data)


def main():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib/numpy not available, skipping plot generation")
        return

    data = load_data()
    os.makedirs(os.path.join(OUTPUT_DIR, "PNG"), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "PDF"), exist_ok=True)

    price_steps = sorted(data.keys())
    abd_means = [statistics.mean([r["abd_total"] for r in data[ps]]) for ps in price_steps]
    abd_stds = [statistics.stdev([r["abd_total"] for r in data[ps]]) for ps in price_steps]
    abd_ps_means = [statistics.mean([r["abd_ps"] for r in data[ps]]) for ps in price_steps]
    abd_react_means = [statistics.mean([r["abd_react"] for r in data[ps]]) for ps in price_steps]
    gp_means = [statistics.mean([r["goodput"] for r in data[ps]]) for ps in price_steps]

    # === Figure 1: ABD vs price_step (main figure) ===
    fig, ax1 = plt.subplots(1, 1, figsize=(6, 4))

    # ABD lines
    ax1.errorbar(price_steps, abd_means, yerr=abd_stds, marker='o', color='#d32f2f',
                 linewidth=2, markersize=7, capsize=4, label='ABD_total', zorder=3)
    ax1.plot(price_steps, abd_ps_means, marker='s', color='#1565c0',
             linewidth=1.5, markersize=6, linestyle='--', label='ABD_P&S', zorder=3)
    ax1.plot(price_steps, abd_react_means, marker='^', color='#2e7d32',
             linewidth=1.5, markersize=6, linestyle='--', label='ABD_ReAct', zorder=3)

    # Reference lines
    ax1.axhline(y=PLANGATE_ABD, color='#7b1fa2', linewidth=1.5, linestyle=':',
                label=f'PlanGate ABD={PLANGATE_ABD}%', zorder=2)
    ax1.axhline(y=NG_ABD, color='#757575', linewidth=1, linestyle='-.',
                label=f'No Gov. ABD={NG_ABD}%', zorder=2)

    # Annotations
    best_idx = abd_means.index(min(abd_means))
    ax1.annotate(f'Best-case\nABD={abd_means[best_idx]:.1f}%\n(still +{abd_means[best_idx]-PLANGATE_ABD:.1f}pp vs PlanGate)',
                 xy=(price_steps[best_idx], abd_means[best_idx]),
                 xytext=(price_steps[best_idx]+15, abd_means[best_idx]-12),
                 arrowprops=dict(arrowstyle='->', color='#d32f2f', lw=1.2),
                 fontsize=8, color='#d32f2f',
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='#fff3e0', edgecolor='#d32f2f', alpha=0.9))

    # Shaded region: "structural mismatch zone"
    ax1.fill_between([0, 110], PLANGATE_ABD, PLANGATE_ABD, alpha=0)  # dummy
    ax1.axhspan(50, 100, alpha=0.06, color='#d32f2f', zorder=0)
    ax1.text(80, 92, 'Structural\nmismatch zone', fontsize=8, color='#b71c1c', alpha=0.7,
             ha='center', va='top', style='italic')

    ax1.set_xlabel('Rajomon price_step', fontsize=11)
    ax1.set_ylabel('ABD (%)', fontsize=11)
    ax1.set_title('Rajomon Sensitivity: Per-Request Pricing\nvs. Session Commitment', fontsize=12, fontweight='bold')
    ax1.set_xlim(-2, 108)
    ax1.set_ylim(0, 105)
    ax1.legend(loc='center right', fontsize=8, framealpha=0.9)
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(price_steps)

    fig.savefig(os.path.join(OUTPUT_DIR, "PNG", "rajomon_sensitivity.png"), dpi=300, bbox_inches='tight')
    fig.savefig(os.path.join(OUTPUT_DIR, "PDF", "rajomon_sensitivity.pdf"), bbox_inches='tight')
    # Also save to paper/figures for direct LaTeX inclusion
    paper_fig_dir = os.path.join(ROOT_DIR, "paper", "figures")
    os.makedirs(paper_fig_dir, exist_ok=True)
    fig.savefig(os.path.join(paper_fig_dir, "rajomon_sensitivity.pdf"), bbox_inches='tight')
    plt.close()

    print(f"  Rajomon sensitivity figure saved:")
    print(f"    PNG: {os.path.join(OUTPUT_DIR, 'PNG', 'rajomon_sensitivity.png')}")
    print(f"    PDF: {os.path.join(OUTPUT_DIR, 'PDF', 'rajomon_sensitivity.pdf')}")
    print(f"    Paper: {os.path.join(paper_fig_dir, 'rajomon_sensitivity.pdf')}")

    # === Figure 2: ABD + GP/s dual-axis (supplementary) ===
    fig2, ax_abd = plt.subplots(1, 1, figsize=(6, 4))
    ax_gp = ax_abd.twinx()

    l1 = ax_abd.errorbar(price_steps, abd_means, yerr=abd_stds, marker='o', color='#d32f2f',
                         linewidth=2, markersize=7, capsize=4, label='ABD_total (%)', zorder=3)
    l2, = ax_gp.plot(price_steps, gp_means, marker='D', color='#1565c0',
                      linewidth=2, markersize=6, label='GP/s', zorder=3)

    ax_abd.axhline(y=PLANGATE_ABD, color='#7b1fa2', linewidth=1.5, linestyle=':',
                   label=f'PlanGate ABD={PLANGATE_ABD}%')

    ax_abd.set_xlabel('Rajomon price_step', fontsize=11)
    ax_abd.set_ylabel('ABD (%)', fontsize=11, color='#d32f2f')
    ax_gp.set_ylabel('Effective Goodput/s', fontsize=11, color='#1565c0')
    ax_abd.set_xlim(-2, 108)
    ax_abd.set_ylim(0, 105)
    ax_gp.set_ylim(0, 35)
    ax_abd.set_xticks(price_steps)

    lines = [l1, l2]
    labels = [l.get_label() for l in lines]
    ax_abd.legend(lines, labels, loc='center right', fontsize=8)
    ax_abd.grid(True, alpha=0.3)
    ax_abd.set_title('Rajomon: ABD and Goodput vs. price_step', fontsize=12, fontweight='bold')

    fig2.savefig(os.path.join(OUTPUT_DIR, "PNG", "rajomon_sensitivity_dual.png"), dpi=300, bbox_inches='tight')
    fig2.savefig(os.path.join(OUTPUT_DIR, "PDF", "rajomon_sensitivity_dual.pdf"), bbox_inches='tight')
    plt.close()

    print(f"  Dual-axis figure saved:")
    print(f"    PNG: {os.path.join(OUTPUT_DIR, 'PNG', 'rajomon_sensitivity_dual.png')}")


if __name__ == "__main__":
    main()
