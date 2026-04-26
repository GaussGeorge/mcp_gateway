#!/usr/bin/env python3
"""
plot_mock_cascade.py — 生成 Exp1 级联失败对比柱状图
====================================================
从 exp1_core_summary.csv 读取数据, 生成 6 个网关的平均级联失败数柱状图。
输出: paper/figures/mock_cascade_comparison.pdf + .png
"""
import csv
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ═══════════════════════════════════════════════════════════════
# 全局样式 (与 plot_paper_charts.py 统一)
# ═══════════════════════════════════════════════════════════════
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 13,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "axes.linewidth": 1.0,
    "lines.linewidth": 1.5,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
})

# 配色方案
COLORS = {
    "ng":            "#f39c12",  # 橙色
    "srl":           "#3498db",  # 蓝色
    "rajomon":       "#bdc3c7",  # 浅灰 (参考基线)
    "dagor":         "#95a5a6",  # 中灰 (参考基线)
    "sbac":          "#9b59b6",  # 紫色
    "plangate_full": "#2ecc71",  # 绿色
}

LABELS = {
    "ng":            "No-Gov (NG)",
    "srl":           "SRL",
    "rajomon":       "Rajomon†",
    "dagor":         "DAGOR†",
    "sbac":          "SBAC",
    "plangate_full": "PlanGate",
}

GATEWAY_ORDER = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "results", "exp1_core", "exp1_core_summary.csv")
OUT_DIR = os.path.join(BASE_DIR, "paper", "figures")


def load_cascade_data():
    """从 CSV 读取每个网关的级联失败数"""
    cascade_by_gw = defaultdict(list)
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"].strip()
            cascade = int(row["cascade_failed"])
            cascade_by_gw[gw].append(cascade)
    # 计算平均值
    avg = {}
    for gw in GATEWAY_ORDER:
        vals = cascade_by_gw.get(gw, [0])
        avg[gw] = np.mean(vals)
    return avg


def main():
    data = load_cascade_data()

    fig, ax = plt.subplots(figsize=(6.5, 4.0))

    x = np.arange(len(GATEWAY_ORDER))
    bars_vals = [data[gw] for gw in GATEWAY_ORDER]
    bar_colors = [COLORS[gw] for gw in GATEWAY_ORDER]

    bars = ax.bar(x, bars_vals, width=0.55, color=bar_colors,
                  edgecolor="#2c3e50", linewidth=0.8, zorder=3)

    # 柱顶标注
    for i, (bar, val) in enumerate(zip(bars, bars_vals)):
        label = f"{val:.1f}" if val > 0 else "0"
        fontweight = "bold" if val == 0 else "normal"
        color = "#27ae60" if val == 0 else "#2c3e50"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                label, ha="center", va="bottom",
                fontsize=11, fontweight=fontweight, color=color, zorder=4)

    # PlanGate 特殊标注
    pg_idx = GATEWAY_ORDER.index("plangate_full")
    ax.annotate("Zero cascade\nfailures",
                xy=(pg_idx, 3), xytext=(pg_idx + 0.1, 35),
                fontsize=9, fontweight="bold", color="#27ae60",
                ha="center",
                arrowprops=dict(arrowstyle="->", color="#27ae60", lw=1.5),
                zorder=5)

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[gw] for gw in GATEWAY_ORDER], fontsize=9.5)
    ax.set_ylabel("Avg. Cascade Failures (per 500 sessions)")
    ax.set_ylim(0, max(bars_vals) * 1.25)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.grid(axis="x", visible=False)

    # 参考基线注脚
    ax.text(0.98, 0.96, "† Reference baselines (per-request systems)",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=7.5, color="#7f8c8d", style="italic")

    fig.tight_layout()

    os.makedirs(OUT_DIR, exist_ok=True)
    png_path = os.path.join(OUT_DIR, "mock_cascade_comparison.png")
    pdf_path = os.path.join(OUT_DIR, "mock_cascade_comparison.pdf")
    fig.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    print(f"[OK] {png_path}")
    print(f"[OK] {pdf_path}")
    plt.close(fig)


if __name__ == "__main__":
    main()
