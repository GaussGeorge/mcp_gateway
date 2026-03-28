#!/usr/bin/env python3
"""可视化 Exp1_Core 和 Exp4_Ablation 实验结果"""
import csv
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

# 中文字体
rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
OUT_DIR = os.path.join(RESULTS_DIR, "figures")
os.makedirs(OUT_DIR, exist_ok=True)


def load_summary(path):
    results = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"]
            results.setdefault(gw, []).append(row)
    return results


def stats(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key)]
    if not vals:
        return 0, 0
    return np.mean(vals), np.std(vals)


# ===== 颜色方案 =====
COLORS = {
    "ng": "#888888",
    "srl": "#1f77b4",
    "rajomon": "#ff7f0e",
    "dagor": "#17becf",
    "sbac": "#bcbd22",
    "plangate_full": "#2ca02c",
    "plangate_no_lock": "#d62728",
}
LABELS = {
    "ng": "No-Gov",
    "srl": "SRL",
    "rajomon": "Rajomon",
    "dagor": "DAGOR-MCP",
    "sbac": "SBAC-MCP",
    "plangate_full": "PlanGate-Full",
    "plangate_no_lock": "PlanGate-NoLock",
}
EXP1_GATEWAYS = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full", "plangate_no_lock"]


def plot_exp1_session_breakdown(data):
    """Exp1: 会话状态分布 (堆叠柱状图)"""
    gateways = EXP1_GATEWAYS
    labels = [LABELS[g] for g in gateways]

    success_m = [stats(data[g], "success")[0] for g in gateways]
    rej_m = [stats(data[g], "rejected_s0")[0] for g in gateways]
    cascade_m = [stats(data[g], "cascade_failed")[0] for g in gateways]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(gateways))
    w = 0.5

    bars1 = ax.bar(x, success_m, w, label="SUCCESS", color="#2ca02c", alpha=0.85)
    bars2 = ax.bar(x, rej_m, w, bottom=success_m, label="REJECTED@S0", color="#1f77b4", alpha=0.85)
    bottoms = [s + r for s, r in zip(success_m, rej_m)]
    bars3 = ax.bar(x, cascade_m, w, bottom=bottoms, label="CASCADE_FAIL", color="#d62728", alpha=0.85)

    ax.set_xlabel("Gateway", fontsize=13)
    ax.set_ylabel("Sessions (avg of 5 runs)", fontsize=13)
    ax.set_title("Exp1_Core: Session Outcome Distribution (500 sessions, concurrency=200)", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11, loc="upper right")
    ax.set_ylim(0, 340)
    ax.grid(axis="y", alpha=0.3)

    # 标注 cascade 数字
    for i, (g, c) in enumerate(zip(gateways, cascade_m)):
        if c > 0:
            ax.text(i, bottoms[i] + c + 3, f"cascade={c:.0f}", ha="center", fontsize=9, color="#d62728", fontweight="bold")

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "exp1_session_breakdown.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_exp1_goodput(data):
    """Exp1: Raw vs Effective Goodput/s"""
    gateways = EXP1_GATEWAYS
    labels = [LABELS[g] for g in gateways]

    raw_m = [stats(data[g], "raw_goodput_s")[0] for g in gateways]
    raw_s = [stats(data[g], "raw_goodput_s")[1] for g in gateways]
    eff_m = [stats(data[g], "effective_goodput_s")[0] for g in gateways]
    eff_s = [stats(data[g], "effective_goodput_s")[1] for g in gateways]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(gateways))
    w = 0.35

    bars1 = ax.bar(x - w/2, raw_m, w, yerr=raw_s, label="Raw Goodput/s", color="#ff9800", alpha=0.85, capsize=4)
    bars2 = ax.bar(x + w/2, eff_m, w, yerr=eff_s, label="Effective Goodput/s", color="#4caf50", alpha=0.85, capsize=4)

    ax.set_xlabel("Gateway", fontsize=13)
    ax.set_ylabel("Goodput/s", fontsize=13)
    ax.set_title("Exp1_Core: Raw vs Effective Goodput/s", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    # 标注浪费百分比
    for i, (r, e) in enumerate(zip(raw_m, eff_m)):
        if r > 0 and abs(r - e) > 1:
            waste = (1 - e / r) * 100
            ax.annotate(f"waste {waste:.0f}%", xy=(i, max(r, e) + 3),
                        ha="center", fontsize=9, color="#d62728", fontweight="bold")

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "exp1_goodput.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_exp1_latency(data):
    """Exp1: P50/P95/P99 延迟对比"""
    gateways = EXP1_GATEWAYS
    labels = [LABELS[g] for g in gateways]

    p50 = [stats(data[g], "p50_ms")[0] for g in gateways]
    p95 = [stats(data[g], "p95_ms")[0] for g in gateways]
    p99 = [stats(data[g], "p99_ms")[0] for g in gateways]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(gateways))
    w = 0.25

    ax.bar(x - w, p50, w, label="P50", color="#42a5f5", alpha=0.85)
    ax.bar(x, p95, w, label="P95", color="#ffa726", alpha=0.85)
    ax.bar(x + w, p99, w, label="P99", color="#ef5350", alpha=0.85)

    ax.set_xlabel("Gateway", fontsize=13)
    ax.set_ylabel("Latency (ms)", fontsize=13)
    ax.set_title("Exp1_Core: Step Latency Percentiles", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "exp1_latency.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_exp4_ablation(data4):
    """Exp4: 消融实验 — Full vs NoLock 多维对比"""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5))

    gw_list = ["plangate_full", "plangate_no_lock"]
    labels = [LABELS[g] for g in gw_list]
    colors = [COLORS[g] for g in gw_list]

    # --- Panel 1: Effective Goodput/s ---
    ax = axes[0]
    eff_m = [stats(data4[g], "effective_goodput_s")[0] for g in gw_list]
    eff_s = [stats(data4[g], "effective_goodput_s")[1] for g in gw_list]
    bars = ax.bar(labels, eff_m, yerr=eff_s, color=colors, alpha=0.85, capsize=5, width=0.5)
    ax.set_ylabel("Effective Goodput/s", fontsize=12)
    ax.set_title("Effective Goodput/s", fontsize=13, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    # 标注差距
    gap = (1 - eff_m[1] / eff_m[0]) * 100 if eff_m[0] > 0 else 0
    ax.text(0.5, max(eff_m) * 1.08, f"↓{gap:.1f}%", ha="center", fontsize=12, color="#d62728", fontweight="bold",
            transform=ax.get_xaxis_transform())

    # --- Panel 2: CASCADE_FAIL ---
    ax = axes[1]
    cas_m = [stats(data4[g], "cascade_failed")[0] for g in gw_list]
    cas_s = [stats(data4[g], "cascade_failed")[1] for g in gw_list]
    bars = ax.bar(labels, cas_m, yerr=cas_s, color=colors, alpha=0.85, capsize=5, width=0.5)
    ax.set_ylabel("CASCADE_FAIL (avg)", fontsize=12)
    ax.set_title("Cascade Failures", fontsize=13, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    for i, (m, s) in enumerate(zip(cas_m, cas_s)):
        ax.text(i, m + s + 1, f"{m:.1f}", ha="center", fontsize=11, fontweight="bold")

    # --- Panel 3: 会话结果分布 ---
    ax = axes[2]
    for i, g in enumerate(gw_list):
        s_m = stats(data4[g], "success")[0]
        r_m = stats(data4[g], "rejected_s0")[0]
        c_m = stats(data4[g], "cascade_failed")[0]
        total = s_m + r_m + c_m
        if total > 0:
            ax.barh(i, s_m / total * 100, color="#2ca02c", alpha=0.85, height=0.4)
            ax.barh(i, r_m / total * 100, left=s_m / total * 100, color="#1f77b4", alpha=0.85, height=0.4)
            ax.barh(i, c_m / total * 100, left=(s_m + r_m) / total * 100, color="#d62728", alpha=0.85, height=0.4)
    ax.set_yticks(range(len(gw_list)))
    ax.set_yticklabels(labels, fontsize=11)
    ax.set_xlabel("Percentage (%)", fontsize=12)
    ax.set_title("Session Outcome %", fontsize=13, fontweight="bold")
    ax.set_xlim(0, 105)
    ax.legend(["SUCCESS", "REJECTED@S0", "CASCADE_FAIL"], fontsize=9, loc="lower right")
    ax.grid(axis="x", alpha=0.3)

    fig.suptitle("Exp4_Ablation: PlanGate-Full vs PlanGate-NoLock", fontsize=15, fontweight="bold", y=1.02)
    plt.tight_layout()
    path = os.path.join(OUT_DIR, "exp4_ablation.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_exp1_radar(data):
    """Exp1: 雷达图 — 多维度综合能力"""
    gateways = EXP1_GATEWAYS

    # 5 维度: EffGP/s, Success率, 零Cascade, 低P95, 低P50
    metrics = []
    for g in gateways:
        eff = stats(data[g], "effective_goodput_s")[0]
        succ = stats(data[g], "success")[0]
        cascade = stats(data[g], "cascade_failed")[0]
        p95 = stats(data[g], "p95_ms")[0]
        p50 = stats(data[g], "p50_ms")[0]
        metrics.append([eff, succ, cascade, p95, p50])

    # 归一化到 [0,1]，越大越好
    metrics = np.array(metrics)
    norm = np.zeros_like(metrics)
    # EffGP/s — 越大越好
    norm[:, 0] = metrics[:, 0] / max(metrics[:, 0].max(), 1)
    # Success 率 — 越大越好
    norm[:, 1] = metrics[:, 1] / 300.0
    # Cascade — 越少越好 (反转)
    max_cas = max(metrics[:, 2].max(), 1)
    norm[:, 2] = 1 - metrics[:, 2] / max_cas
    # P95 — 越低越好 (反转)
    max_p95 = max(metrics[:, 3].max(), 1)
    norm[:, 3] = 1 - metrics[:, 3] / max_p95
    # P50 — 越低越好 (反转)
    max_p50 = max(metrics[:, 4].max(), 1)
    norm[:, 4] = 1 - metrics[:, 4] / max_p50

    categories = ["Eff Goodput/s", "Success Rate", "Zero Cascade", "Low P95", "Low P50"]
    N = len(categories)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    for i, g in enumerate(gateways):
        values = norm[i].tolist()
        values += values[:1]
        ax.plot(angles, values, "o-", linewidth=2, label=LABELS[g], color=COLORS[g], alpha=0.8)
        ax.fill(angles, values, alpha=0.08, color=COLORS[g])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=11)
    ax.set_ylim(0, 1.1)
    ax.set_title("Exp1_Core: Multi-Dimensional Radar", fontsize=14, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=10)

    plt.tight_layout()
    path = os.path.join(OUT_DIR, "exp1_radar.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def main():
    exp1_path = os.path.join(RESULTS_DIR, "exp1_core", "exp1_core_summary.csv")
    exp4_path = os.path.join(RESULTS_DIR, "exp4_ablation", "exp4_ablation_summary.csv")

    print("加载数据...")
    data1 = load_summary(exp1_path)
    data4 = load_summary(exp4_path)

    print("生成图表...")
    plot_exp1_session_breakdown(data1)
    plot_exp1_goodput(data1)
    plot_exp1_latency(data1)
    plot_exp1_radar(data1)
    plot_exp4_ablation(data4)
    print(f"\n所有图表已保存至: {OUT_DIR}")


if __name__ == "__main__":
    main()
