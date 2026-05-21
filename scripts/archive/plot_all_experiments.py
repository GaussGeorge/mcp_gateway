#!/usr/bin/env python3
"""
plot_all_experiments.py — 全部 9 组实验论文级可视化
===================================================
从 results/exp*_summary.csv 自动读取数据，生成 12 张学术级图表:

  Fig 1. 核心性能对比 — 成功率/级联失败/有效吞吐 (Exp1)
  Fig 2. 级联浪费率对比雷达图 (Exp1)
  Fig 3. 重量工具占比敏感性 (Exp2)
  Fig 4. 混合模式 P&S/ReAct 比例 (Exp3)
  Fig 5. 消融实验 (Exp4)
  Fig 6. P&S 并发扩展性 (Exp5)
  Fig 7. ReAct 并发扩展性 (Exp6)
  Fig 8. 客户端拒绝 price_ttl 扫参 (Exp7)
  Fig 9. 折扣函数消融 (Exp8)
  Fig10. 高并发压力测试 (Exp9)
  Fig11. 尾延迟对比 P50 vs P95 vs P99 (Exp1)
  Fig12. Jain 公平性指数对比 (Exp1)

用法:
  python scripts/plot_all_experiments.py
"""

import csv
import os
import sys
import warnings
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)

# ═══════════════════ 全局样式配置 ═══════════════════
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
    "lines.linewidth": 1.8,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
})

# ═══════════════════ 配色方案 (统一学术风格) ═══════════════════
COLORS = {
    "ng":             "#e67e22",   # 暖橙
    "srl":            "#3498db",   # 蓝
    "rajomon":        "#9b59b6",   # 紫
    "dagor":          "#e74c3c",   # 红
    "sbac":           "#95a5a6",   # 灰
    "plangate_full":  "#2ecc71",   # 绿
    # Exp4 消融变体
    "wo_budgetlock":  "#f39c12",   # 黄橙
    "wo_sessioncap":  "#1abc9c",   # 青
    # Exp8 折扣函数
    "plangate_quadratic":    "#2ecc71",
    "plangate_linear":       "#3498db",
    "plangate_exponential":  "#e74c3c",
    "plangate_logarithmic":  "#f39c12",
}

LABELS = {
    "ng":             "No-Gov (NG)",
    "srl":            "SRL",
    "rajomon":        "Rajomon",
    "dagor":          "DAGOR",
    "sbac":           "SBAC",
    "plangate_full":  "PlanGate",
    "wo_budgetlock":  "w/o BudgetLock",
    "wo_sessioncap":  "w/o SessionCap",
    "plangate_quadratic":    "Quadratic (K²)",
    "plangate_linear":       "Linear (K)",
    "plangate_exponential":  "Exponential (eᴷ)",
    "plangate_logarithmic":  "Logarithmic (ln K)",
}

HATCHES = {
    "ng": "",  "srl": "//",  "rajomon": "\\\\",
    "dagor": "xx",  "sbac": "..",  "plangate_full": "",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
OUT_PNG = os.path.join(RESULTS_DIR, "paper_figures", "PNG")
OUT_PDF = os.path.join(RESULTS_DIR, "paper_figures", "PDF")


# ═══════════════════ 数据加载 ═══════════════════
def load_summary(exp_name: str) -> list:
    """加载实验汇总 CSV"""
    path = os.path.join(RESULTS_DIR, exp_name.lower(),
                        f"{exp_name.lower()}_summary.csv")
    if not os.path.exists(path):
        print(f"  [WARN] 未找到: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _int(v, default=0):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def aggregate(rows, group_key="gateway", metrics=None):
    """按 group_key 聚合，返回 {group: {metric: (mean, std)}}"""
    if metrics is None:
        metrics = ["success", "cascade_failed", "effective_goodput_s",
                    "p50_ms", "p95_ms", "p99_ms", "jfi_steps", "jfi_latency"]
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        g = r[group_key]
        for m in metrics:
            v = _float(r.get(m))
            groups[g][m].append(v)
    result = {}
    for g, data in groups.items():
        result[g] = {}
        for m, vals in data.items():
            arr = np.array(vals)
            result[g][m] = (np.mean(arr), np.std(arr))
    return result


def aggregate_sweep(rows, sweep_key, metrics=None):
    """按 (gateway, sweep_val) 聚合扫参实验"""
    if metrics is None:
        metrics = ["success", "cascade_failed", "effective_goodput_s",
                    "p50_ms", "p95_ms", "p99_ms"]
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        key = (r["gateway"], r.get("sweep_val", ""))
        for m in metrics:
            groups[key][m].append(_float(r.get(m)))
    result = {}
    for key, data in groups.items():
        result[key] = {m: (np.mean(vals), np.std(vals))
                       for m, vals in data.items()}
    return result


def _save(fig, name):
    """保存 PNG + PDF"""
    os.makedirs(OUT_PNG, exist_ok=True)
    os.makedirs(OUT_PDF, exist_ok=True)
    fig.savefig(os.path.join(OUT_PNG, f"{name}.png"),
                bbox_inches="tight", dpi=300)
    fig.savefig(os.path.join(OUT_PDF, f"{name}.pdf"),
                bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ {name}")


# ═══════════════════ Fig 1: 核心性能对比 (Exp1) ═══════════════════
def plot_fig1_core(data):
    """三合一: 成功率 + 级联失败 + 有效吞吐"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate(data)
    gws = [g for g in gw_order if g in agg]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))

    # (a) 成功率
    ax = axes[0]
    means = [agg[g]["success"][0] for g in gws]
    stds = [agg[g]["success"][1] for g in gws]
    bars = ax.bar(range(len(gws)), means, yerr=stds,
                  color=[COLORS.get(g, "#999") for g in gws],
                  edgecolor="white", linewidth=0.5, capsize=3,
                  zorder=3)
    ax.set_xticks(range(len(gws)))
    ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=25, ha="right")
    ax.set_ylabel("Successful Sessions (out of 500)")
    ax.set_title("(a) Success Rate")
    # 标注数值
    for bar, m in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2,
                f"{m:.0f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    # (b) 级联失败
    ax = axes[1]
    means = [agg[g]["cascade_failed"][0] for g in gws]
    stds = [agg[g]["cascade_failed"][1] for g in gws]
    bars = ax.bar(range(len(gws)), means, yerr=stds,
                  color=[COLORS.get(g, "#999") for g in gws],
                  edgecolor="white", linewidth=0.5, capsize=3,
                  zorder=3)
    ax.set_xticks(range(len(gws)))
    ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=25, ha="right")
    ax.set_ylabel("Cascade Failures")
    ax.set_title("(b) Cascade Waste")
    for bar, m in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                f"{m:.0f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    # (c) 有效吞吐
    ax = axes[2]
    means = [agg[g]["effective_goodput_s"][0] for g in gws]
    stds = [agg[g]["effective_goodput_s"][1] for g in gws]
    bars = ax.bar(range(len(gws)), means, yerr=stds,
                  color=[COLORS.get(g, "#999") for g in gws],
                  edgecolor="white", linewidth=0.5, capsize=3,
                  zorder=3)
    ax.set_xticks(range(len(gws)))
    ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=25, ha="right")
    ax.set_ylabel("Effective Goodput/s")
    ax.set_title("(c) Effective Throughput")
    for bar, m in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f"{m:.1f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    fig.suptitle("Fig. 1  Core Performance Comparison (Exp1: 500 sessions, 200 concurrency)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig01_core_performance")


# ═══════════════════ Fig 2: 级联浪费率雷达图 ═══════════════════
def plot_fig2_radar(data):
    """多维雷达图对比各网关"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate(data, metrics=["success", "cascade_failed",
                                     "effective_goodput_s", "p95_ms",
                                     "jfi_steps", "jfi_latency"])
    gws = [g for g in gw_order if g in agg]

    categories = ["Success Rate", "Low Cascade\nWaste", "Eff. Throughput",
                  "Low P95 Latency", "JFI Steps", "JFI Latency"]
    N = len(categories)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))

    # Normalize each dimension to [0, 1]
    raw = {}
    for g in gws:
        vals = [
            agg[g]["success"][0] / 500.0,  # success rate
            1.0 - min(agg[g]["cascade_failed"][0] / 130.0, 1.0),  # inverse cascade
            min(agg[g]["effective_goodput_s"][0] / 70.0, 1.0),  # throughput
            1.0 - min(agg[g]["p95_ms"][0] / 2000.0, 1.0),  # inverse latency
            agg[g]["jfi_steps"][0],
            agg[g]["jfi_latency"][0],
        ]
        raw[g] = vals

    for g in gws:
        vals = raw[g] + raw[g][:1]
        ax.plot(angles, vals, linewidth=2, label=LABELS.get(g, g),
                color=COLORS.get(g, "#999"))
        ax.fill(angles, vals, alpha=0.08, color=COLORS.get(g, "#999"))

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=8)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=9)
    ax.set_title("Fig. 2  Multi-Dimensional Gateway Comparison",
                 fontsize=13, fontweight="bold", pad=20)
    _save(fig, "fig02_radar_comparison")


# ═══════════════════ Fig 3: 重量工具占比 (Exp2) ═══════════════════
def plot_fig3_heavy_ratio(data):
    """成功率 vs heavy_ratio 多网关折线"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate_sweep(data, "heavy_ratio")

    sweep_vals = sorted(set(k[1] for k in agg.keys()))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    for g in gw_order:
        means = [agg.get((g, sv), {}).get("success", (0, 0))[0] for sv in sweep_vals]
        stds = [agg.get((g, sv), {}).get("success", (0, 0))[1] for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax1.errorbar([float(v) for v in sweep_vals], means, yerr=stds,
                         label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                         marker="o", markersize=5, capsize=3)
    ax1.set_xlabel("Heavy Tool Ratio")
    ax1.set_ylabel("Successful Sessions (out of 200)")
    ax1.set_title("(a) Success Rate vs Heavy Ratio")
    ax1.legend(fontsize=8)

    for g in gw_order:
        means = [agg.get((g, sv), {}).get("cascade_failed", (0, 0))[0] for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax2.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="s", markersize=5)
    ax2.set_xlabel("Heavy Tool Ratio")
    ax2.set_ylabel("Cascade Failures")
    ax2.set_title("(b) Cascade Waste vs Heavy Ratio")
    ax2.legend(fontsize=8)

    fig.suptitle("Fig. 3  Heavy Tool Ratio Sensitivity (Exp2)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig03_heavy_ratio")


# ═══════════════════ Fig 4: 混合模式 (Exp3) ═══════════════════
def plot_fig4_mixed_mode(data):
    """P&S vs ReAct 混合比例"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate_sweep(data, "ps_ratio",
                          metrics=["success", "cascade_failed", "effective_goodput_s"])

    sweep_vals = sorted(set(k[1] for k in agg.keys()))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    for g in gw_order:
        means = [agg.get((g, sv), {}).get("success", (0, 0))[0] for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax1.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="o", markersize=5)
    ax1.set_xlabel("Plan-and-Solve Ratio")
    ax1.set_ylabel("Successful Sessions (out of 200)")
    ax1.set_title("(a) Success Rate vs P&S Ratio")
    ax1.legend(fontsize=8)

    for g in gw_order:
        means = [agg.get((g, sv), {}).get("effective_goodput_s", (0, 0))[0]
                 for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax2.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="s", markersize=5)
    ax2.set_xlabel("Plan-and-Solve Ratio")
    ax2.set_ylabel("Effective Goodput/s")
    ax2.set_title("(b) Throughput vs P&S Ratio")
    ax2.legend(fontsize=8)

    fig.suptitle("Fig. 4  Mixed-Mode Routing (Exp3: P&S + ReAct)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig04_mixed_mode")


# ═══════════════════ Fig 5: 消融实验 (Exp4) ═══════════════════
def plot_fig5_ablation(data):
    """Full vs w/o-BudgetLock vs w/o-SessionCap vs Rajomon"""
    gw_order = ["plangate_full", "wo_budgetlock", "wo_sessioncap", "rajomon"]
    agg = aggregate(data, metrics=["success", "cascade_failed",
                                     "effective_goodput_s"])
    gws = [g for g in gw_order if g in agg]

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    metrics_info = [
        ("success", "Successful Sessions", "(a) Success"),
        ("cascade_failed", "Cascade Failures", "(b) Cascade Waste"),
        ("effective_goodput_s", "Eff. Goodput/s", "(c) Throughput"),
    ]
    for ax, (metric, ylabel, title) in zip(axes, metrics_info):
        means = [agg[g][metric][0] for g in gws]
        stds = [agg[g][metric][1] for g in gws]
        bars = ax.bar(range(len(gws)), means, yerr=stds,
                      color=[COLORS.get(g, "#999") for g in gws],
                      edgecolor="white", capsize=3, zorder=3)
        ax.set_xticks(range(len(gws)))
        ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=20, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        for bar, m in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                    f"{m:.1f}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    fig.suptitle("Fig. 5  Ablation Study (Exp4: 500 sessions, 200 concurrency)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig05_ablation")


# ═══════════════════ Fig 6: P&S 并发扩展 (Exp5) ═══════════════════
def plot_fig6_scale_ps(data):
    """并发扩展: 成功率 + 吞吐"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate_sweep(data, "concurrency",
                          metrics=["success", "effective_goodput_s", "cascade_failed"])
    sweep_vals = sorted(set(k[1] for k in agg.keys()), key=lambda x: float(x))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    for g in gw_order:
        means = [agg.get((g, sv), {}).get("success", (0, 0))[0] for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax1.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="o", markersize=5)
    ax1.set_xlabel("Concurrency Level")
    ax1.set_ylabel("Successful Sessions")
    ax1.set_title("(a) Success Rate Scalability (P&S)")
    ax1.legend(fontsize=8)

    for g in gw_order:
        means = [agg.get((g, sv), {}).get("effective_goodput_s", (0, 0))[0]
                 for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax2.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="s", markersize=5)
    ax2.set_xlabel("Concurrency Level")
    ax2.set_ylabel("Effective Goodput/s")
    ax2.set_title("(b) Throughput Scalability (P&S)")
    ax2.legend(fontsize=8)

    fig.suptitle("Fig. 6  Concurrency Scalability — Plan-and-Solve (Exp5)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig06_scale_ps")


# ═══════════════════ Fig 7: ReAct 并发扩展 (Exp6) ═══════════════════
def plot_fig7_scale_react(data):
    """纯 ReAct 并发扩展"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate_sweep(data, "concurrency",
                          metrics=["success", "effective_goodput_s"])
    sweep_vals = sorted(set(k[1] for k in agg.keys()), key=lambda x: float(x))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    for g in gw_order:
        means = [agg.get((g, sv), {}).get("success", (0, 0))[0] for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax1.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="o", markersize=5)
    ax1.set_xlabel("Concurrency Level")
    ax1.set_ylabel("Successful Sessions")
    ax1.set_title("(a) Success Rate (Pure ReAct)")
    ax1.legend(fontsize=8)

    for g in gw_order:
        means = [agg.get((g, sv), {}).get("effective_goodput_s", (0, 0))[0]
                 for sv in sweep_vals]
        if any(m > 0 for m in means):
            ax2.plot([float(v) for v in sweep_vals], means,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                     marker="s", markersize=5)
    ax2.set_xlabel("Concurrency Level")
    ax2.set_ylabel("Effective Goodput/s")
    ax2.set_title("(b) Throughput (Pure ReAct)")
    ax2.legend(fontsize=8)

    fig.suptitle("Fig. 7  Concurrency Scalability — Pure ReAct (Exp6)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig07_scale_react")


# ═══════════════════ Fig 8: 客户端拒绝 (Exp7) ═══════════════════
def plot_fig8_client_reject(data):
    """price_ttl 参数扫描"""
    agg = aggregate_sweep(data, "price_ttl",
                          metrics=["success", "effective_goodput_s"])
    sweep_vals = sorted(set(k[1] for k in agg.keys()), key=lambda x: float(x))
    gws = sorted(set(k[0] for k in agg.keys()))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    for g in gws:
        means = [agg.get((g, sv), {}).get("success", (0, 0))[0] for sv in sweep_vals]
        stds = [agg.get((g, sv), {}).get("success", (0, 0))[1] for sv in sweep_vals]
        ax1.errorbar([float(v) for v in sweep_vals], means, yerr=stds,
                     label=LABELS.get(g, g), color=COLORS.get(g, "#2ecc71"),
                     marker="o", capsize=3)
    ax1.set_xlabel("Price TTL (seconds)")
    ax1.set_ylabel("Successful Sessions")
    ax1.set_title("(a) Success Rate vs Price TTL")
    ax1.legend(fontsize=9)

    for g in gws:
        means = [agg.get((g, sv), {}).get("effective_goodput_s", (0, 0))[0]
                 for sv in sweep_vals]
        ax2.plot([float(v) for v in sweep_vals], means,
                 label=LABELS.get(g, g), color=COLORS.get(g, "#2ecc71"),
                 marker="s")
    ax2.set_xlabel("Price TTL (seconds)")
    ax2.set_ylabel("Effective Goodput/s")
    ax2.set_title("(b) Throughput vs Price TTL")
    ax2.legend(fontsize=9)

    fig.suptitle("Fig. 8  Client-Side Hard Reject — Price TTL Sweep (Exp7)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig08_client_reject")


# ═══════════════════ Fig 9: 折扣函数消融 (Exp8) ═══════════════════
def plot_fig9_discount_ablation(data):
    """Linear vs Quadratic vs Exponential vs Logarithmic"""
    gw_order = ["plangate_quadratic", "plangate_linear",
                "plangate_exponential", "plangate_logarithmic"]
    agg = aggregate(data, metrics=["success", "cascade_failed",
                                     "effective_goodput_s", "jfi_steps"])
    gws = [g for g in gw_order if g in agg]

    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    metrics_info = [
        ("success", "Successful Sessions", "(a) Success"),
        ("cascade_failed", "Cascade Failures", "(b) Cascade Waste"),
        ("effective_goodput_s", "Eff. Goodput/s", "(c) Throughput"),
        ("jfi_steps", "Jain Fairness Index", "(d) JFI (Steps)"),
    ]
    for ax, (metric, ylabel, title) in zip(axes, metrics_info):
        means = [agg[g][metric][0] for g in gws]
        stds = [agg[g][metric][1] for g in gws]
        bars = ax.bar(range(len(gws)), means, yerr=stds,
                      color=[COLORS.get(g, "#999") for g in gws],
                      edgecolor="white", capsize=3, zorder=3)
        ax.set_xticks(range(len(gws)))
        ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=25, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        for bar, m in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                    f"{m:.1f}", ha="center", va="bottom", fontsize=7, fontweight="bold")

    fig.suptitle("Fig. 9  Discount Function Ablation (Exp8: Pure ReAct, 500 sessions)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig09_discount_ablation")


# ═══════════════════ Fig 10: 高并发压力 (Exp9) ═══════════════════
def plot_fig10_scale_stress(data):
    """200-1000 并发压力测试"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate_sweep(data, "concurrency",
                          metrics=["success", "cascade_failed",
                                   "effective_goodput_s"])
    sweep_vals = sorted(set(k[1] for k in agg.keys()), key=lambda x: float(x))

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    # (a) 成功率
    for g in gw_order:
        means = [agg.get((g, sv), {}).get("success", (0, 0))[0] for sv in sweep_vals]
        if any(m > 0 for m in means):
            axes[0].plot([int(float(v)) for v in sweep_vals], means,
                         label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                         marker="o", markersize=5)
    axes[0].set_xlabel("Concurrency Level")
    axes[0].set_ylabel("Successful Sessions")
    axes[0].set_title("(a) Success under Stress")
    axes[0].legend(fontsize=7, ncol=2)

    # (b) 级联失败
    for g in gw_order:
        means = [agg.get((g, sv), {}).get("cascade_failed", (0, 0))[0] for sv in sweep_vals]
        if any(m > 0 for m in means):
            axes[1].plot([int(float(v)) for v in sweep_vals], means,
                         label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                         marker="s", markersize=5)
    axes[1].set_xlabel("Concurrency Level")
    axes[1].set_ylabel("Cascade Failures")
    axes[1].set_title("(b) Cascade Waste under Stress")
    axes[1].legend(fontsize=7, ncol=2)

    # (c) 吞吐
    for g in gw_order:
        means = [agg.get((g, sv), {}).get("effective_goodput_s", (0, 0))[0]
                 for sv in sweep_vals]
        if any(m > 0 for m in means):
            axes[2].plot([int(float(v)) for v in sweep_vals], means,
                         label=LABELS.get(g, g), color=COLORS.get(g, "#999"),
                         marker="^", markersize=5)
    axes[2].set_xlabel("Concurrency Level")
    axes[2].set_ylabel("Effective Goodput/s")
    axes[2].set_title("(c) Throughput under Stress")
    axes[2].legend(fontsize=7, ncol=2)

    fig.suptitle("Fig. 10  High-Concurrency Stress Test (Exp9: 200–1000 concurrent)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "fig10_scale_stress")


# ═══════════════════ Fig 11: 尾延迟对比 (Exp1) ═══════════════════
def plot_fig11_latency(data):
    """P50 / P95 / P99 分组柱状图"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate(data, metrics=["p50_ms", "p95_ms", "p99_ms"])
    gws = [g for g in gw_order if g in agg]

    x = np.arange(len(gws))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))
    for i, (metric, label, color) in enumerate([
        ("p50_ms", "P50", "#3498db"),
        ("p95_ms", "P95", "#e74c3c"),
        ("p99_ms", "P99", "#2c3e50"),
    ]):
        means = [agg[g][metric][0] for g in gws]
        stds = [agg[g][metric][1] for g in gws]
        ax.bar(x + i * width, means, width, yerr=stds,
               label=label, color=color, alpha=0.85, capsize=2, zorder=3)

    ax.set_xticks(x + width)
    ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=20, ha="right")
    ax.set_ylabel("Step Latency (ms)")
    ax.set_title("Fig. 11  Tail Latency Comparison (Exp1: P50 / P95 / P99)",
                 fontsize=13, fontweight="bold")
    ax.legend()
    ax.set_yscale("log")
    fig.tight_layout()
    _save(fig, "fig11_tail_latency")


# ═══════════════════ Fig 12: Jain 公平性 (Exp1) ═══════════════════
def plot_fig12_fairness(data):
    """JFI Steps vs JFI Latency 对比"""
    gw_order = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
    agg = aggregate(data, metrics=["jfi_steps", "jfi_latency"])
    gws = [g for g in gw_order if g in agg]

    x = np.arange(len(gws))
    width = 0.35

    fig, ax = plt.subplots(figsize=(9, 5))
    means_s = [agg[g]["jfi_steps"][0] for g in gws]
    stds_s = [agg[g]["jfi_steps"][1] for g in gws]
    means_l = [agg[g]["jfi_latency"][0] for g in gws]
    stds_l = [agg[g]["jfi_latency"][1] for g in gws]

    ax.bar(x - width/2, means_s, width, yerr=stds_s,
           label="JFI (Steps)", color="#3498db", capsize=3, zorder=3)
    ax.bar(x + width/2, means_l, width, yerr=stds_l,
           label="JFI (Latency)", color="#2ecc71", capsize=3, zorder=3)

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS.get(g, g) for g in gws], rotation=20, ha="right")
    ax.set_ylabel("Jain's Fairness Index")
    ax.set_ylim(0.5, 1.05)
    ax.axhline(y=1.0, color="#e74c3c", linestyle="--", alpha=0.5, label="Perfect Fairness")
    ax.set_title("Fig. 12  Jain's Fairness Index Comparison (Exp1)",
                 fontsize=13, fontweight="bold")
    ax.legend()
    fig.tight_layout()
    _save(fig, "fig12_jain_fairness")


# ═══════════════════ 主函数 ═══════════════════
def main():
    print("=" * 60)
    print("  PlanGate — 论文级图表生成 (12 张)")
    print("=" * 60)

    exp1 = load_summary("exp1_core")
    exp2 = load_summary("exp2_heavyratio")
    exp3 = load_summary("exp3_mixedmode")
    exp4 = load_summary("exp4_ablation")
    exp5 = load_summary("exp5_scaleconc")
    exp6 = load_summary("exp6_scaleconcreact")
    exp7 = load_summary("exp7_clientreject")
    exp8 = load_summary("exp8_discountablation")
    exp9 = load_summary("exp9_scalestress")

    if exp1: plot_fig1_core(exp1)
    if exp1: plot_fig2_radar(exp1)
    if exp2: plot_fig3_heavy_ratio(exp2)
    if exp3: plot_fig4_mixed_mode(exp3)
    if exp4: plot_fig5_ablation(exp4)
    if exp5: plot_fig6_scale_ps(exp5)
    if exp6: plot_fig7_scale_react(exp6)
    if exp7: plot_fig8_client_reject(exp7)
    if exp8: plot_fig9_discount_ablation(exp8)
    if exp9: plot_fig10_scale_stress(exp9)
    if exp1: plot_fig11_latency(exp1)
    if exp1: plot_fig12_fairness(exp1)

    print("\n" + "=" * 60)
    print(f"  完成! 图表输出: {OUT_PNG}")
    print(f"                {OUT_PDF}")
    print("=" * 60)


if __name__ == "__main__":
    main()
