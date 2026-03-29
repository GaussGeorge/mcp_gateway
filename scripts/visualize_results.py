#!/usr/bin/env python3
"""可视化 Exp1~Exp5 及 Exp7 实验结果"""
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
    "wo_budgetlock": "#e377c2",
    "wo_sessioncap": "#d62728",
}
LABELS = {
    "ng": "No-Gov",
    "srl": "SRL",
    "rajomon": "Rajomon",
    "dagor": "DAGOR-MCP",
    "sbac": "SBAC-MCP",
    "plangate_full": "PlanGate-Full",
    "wo_budgetlock": "w/o BudgetLock",
    "wo_sessioncap": "w/o SessionCap",
}
EXP1_GATEWAYS = ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full"]
EXP_SWEEP_GATEWAYS = EXP1_GATEWAYS   # Exp2/3/5 共用


def load_sweep_summary(path):
    """加载扫参实验 CSV → {gateway: {sweep_val: [rows]}}"""
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"]
            sv = row.get("sweep_val", "")
            data.setdefault(gw, {}).setdefault(sv, []).append(row)
    return data


def plot_exp1_session_breakdown(data):
    """Exp1: 会话状态分布 (堆叠柱状图)"""
    gateways = EXP1_GATEWAYS
    labels = [LABELS[g] for g in gateways]

    success_m = [stats(data[g], "success")[0] for g in gateways]
    success_s = [stats(data[g], "success")[1] for g in gateways]
    rej_m = [stats(data[g], "rejected_s0")[0] for g in gateways]
    rej_s = [stats(data[g], "rejected_s0")[1] for g in gateways]
    cascade_m = [stats(data[g], "cascade_failed")[0] for g in gateways]
    cascade_s = [stats(data[g], "cascade_failed")[1] for g in gateways]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(gateways))
    w = 0.5

    bars1 = ax.bar(x, success_m, w, yerr=success_s, label="SUCCESS", color="#2ca02c", alpha=0.85, capsize=3, error_kw={"elinewidth": 1})
    bars2 = ax.bar(x, rej_m, w, yerr=rej_s, bottom=success_m, label="REJECTED@S0", color="#1f77b4", alpha=0.85, capsize=3, error_kw={"elinewidth": 1})
    bottoms = [s + r for s, r in zip(success_m, rej_m)]
    bars3 = ax.bar(x, cascade_m, w, yerr=cascade_s, bottom=bottoms, label="CASCADE_FAIL", color="#d62728", alpha=0.85, capsize=3, error_kw={"elinewidth": 1})

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

    p50_m = [stats(data[g], "p50_ms")[0] for g in gateways]
    p50_s = [stats(data[g], "p50_ms")[1] for g in gateways]
    p95_m = [stats(data[g], "p95_ms")[0] for g in gateways]
    p95_s = [stats(data[g], "p95_ms")[1] for g in gateways]
    p99_m = [stats(data[g], "p99_ms")[0] for g in gateways]
    p99_s = [stats(data[g], "p99_ms")[1] for g in gateways]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(gateways))
    w = 0.25

    ax.bar(x - w, p50_m, w, yerr=p50_s, label="P50", color="#42a5f5", alpha=0.85, capsize=3)
    ax.bar(x, p95_m, w, yerr=p95_s, label="P95", color="#ffa726", alpha=0.85, capsize=3)
    ax.bar(x + w, p99_m, w, yerr=p99_s, label="P99", color="#ef5350", alpha=0.85, capsize=3)

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
    """Exp4: 严格单变量消融实验 — 4 组对比"""
    gw_order = ["plangate_full", "wo_budgetlock", "wo_sessioncap", "rajomon"]
    gw_list = [g for g in gw_order if g in data4]
    if len(gw_list) < 2:
        print("  [WARN] Exp4 数据不足, 跳过消融图")
        return

    labels = [LABELS.get(g, g) for g in gw_list]
    colors = [COLORS.get(g, "#888888") for g in gw_list]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    x = np.arange(len(gw_list))
    w = 0.5

    # --- Panel 1: Effective Goodput/s ---
    ax = axes[0]
    eff_m = [stats(data4[g], "effective_goodput_s")[0] for g in gw_list]
    eff_s = [stats(data4[g], "effective_goodput_s")[1] for g in gw_list]
    ax.bar(x, eff_m, w, yerr=eff_s, color=colors, alpha=0.85, capsize=4)
    ax.set_ylabel("Effective Goodput/s", fontsize=12)
    ax.set_title("(a) Effective Goodput/s", fontsize=13, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10, rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.3)
    for i, (m, s) in enumerate(zip(eff_m, eff_s)):
        ax.text(i, m + s + 1, f"{m:.1f}", ha="center", fontsize=9, fontweight="bold")

    # --- Panel 2: Cascade Failures ---
    ax = axes[1]
    cas_m = [stats(data4[g], "cascade_failed")[0] for g in gw_list]
    cas_s = [stats(data4[g], "cascade_failed")[1] for g in gw_list]
    ax.bar(x, cas_m, w, yerr=cas_s, color=colors, alpha=0.85, capsize=4)
    ax.set_ylabel("Cascade Failures (avg)", fontsize=12)
    ax.set_title("(b) Cascade Failures", fontsize=13, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10, rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.3)
    for i, (m, s) in enumerate(zip(cas_m, cas_s)):
        ax.text(i, m + s + 1, f"{m:.1f}", ha="center", fontsize=9, fontweight="bold")

    # --- Panel 3: Goodput Drop % from Full ---
    ax = axes[2]
    full_eff = eff_m[0] if eff_m[0] > 0 else 1
    drops = [(1 - m / full_eff) * 100 for m in eff_m]
    bar_colors = ["#2ca02c" if d <= 0 else "#d62728" for d in drops]
    ax.bar(x, drops, w, color=bar_colors, alpha=0.85)
    ax.set_ylabel("Goodput Drop from Full (%)", fontsize=12)
    ax.set_title("(c) Individual Module Contribution", fontsize=13, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10, rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.3)
    ax.axhline(y=0, color="black", linewidth=0.5)
    for i, d in enumerate(drops):
        ypos = d + 1.5 if d >= 0 else d - 3
        ax.text(i, ypos, f"{d:.1f}%", ha="center", fontsize=9, fontweight="bold")

    fig.suptitle("Exp4: Strict Single-Variable Ablation Study",
                 fontsize=15, fontweight="bold", y=1.02)
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


# ===== Sweep 系列通用绘图 =====

def _plot_sweep_line(data_sweep, metric, xlabel, ylabel, title, fname,
                     gateways=None, sort_x=True):
    """通用扫参折线图: x=sweep_val, y=metric, 每条线一个 gateway"""
    gateways = gateways or EXP_SWEEP_GATEWAYS
    fig, ax = plt.subplots(figsize=(10, 6))

    for gw in gateways:
        if gw not in data_sweep:
            continue
        sv_dict = data_sweep[gw]
        xvals, ymeans, ystds = [], [], []
        keys = sorted(sv_dict.keys(), key=lambda v: float(v)) if sort_x else list(sv_dict.keys())
        for sv in keys:
            rows = sv_dict[sv]
            m, s = stats(rows, metric)
            xvals.append(float(sv))
            ymeans.append(m)
            ystds.append(s)
        ax.errorbar(xvals, ymeans, yerr=ystds, marker="o", linewidth=2,
                     capsize=4, label=LABELS.get(gw, gw), color=COLORS.get(gw, None), alpha=0.85)

    ax.set_xlabel(xlabel, fontsize=13)
    ax.set_ylabel(ylabel, fontsize=13)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    path = os.path.join(OUT_DIR, fname)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_exp2_heavy_ratio(data_sweep):
    """Exp2: 重载比例 → Effective Goodput/s"""
    _plot_sweep_line(data_sweep, "effective_goodput_s",
                     "Heavy-tool Ratio", "Effective Goodput/s",
                     "Exp2: Heavy Ratio vs Effective Goodput/s",
                     "exp2_heavy_ratio_effgps.png")
    _plot_sweep_line(data_sweep, "cascade_failed",
                     "Heavy-tool Ratio", "Cascade Failures (avg)",
                     "Exp2: Heavy Ratio vs Cascade Failures",
                     "exp2_heavy_ratio_cascade.png")


def plot_exp3_mixed_mode(data_sweep):
    """Exp3: PS/ReAct 混合比例 → Effective Goodput/s"""
    _plot_sweep_line(data_sweep, "effective_goodput_s",
                     "Plan-and-Solve Ratio", "Effective Goodput/s",
                     "Exp3: PS Ratio vs Effective Goodput/s",
                     "exp3_mixed_mode_effgps.png")
    _plot_sweep_line(data_sweep, "cascade_failed",
                     "Plan-and-Solve Ratio", "Cascade Failures (avg)",
                     "Exp3: PS Ratio vs Cascade Failures",
                     "exp3_mixed_mode_cascade.png")


def plot_exp5_scale_conc(data_sweep):
    """Exp5: 并发扩展 → Effective Goodput/s"""
    _plot_sweep_line(data_sweep, "effective_goodput_s",
                     "Concurrency", "Effective Goodput/s",
                     "Exp5: Concurrency vs Effective Goodput/s",
                     "exp5_scale_conc_effgps.png")
    _plot_sweep_line(data_sweep, "cascade_failed",
                     "Concurrency", "Cascade Failures (avg)",
                     "Exp5: Concurrency vs Cascade Failures",
                     "exp5_scale_conc_cascade.png")


def plot_exp7_client_reject(data_sweep):
    """Exp7: Hard Reject — price_ttl 扫参 (折线+柱状)"""
    gw = "plangate_full"
    if gw not in data_sweep:
        print("  [WARN] Exp7 无 plangate_full 数据, 跳过")
        return

    sv_dict = data_sweep[gw]
    ttl_vals, eff_means, eff_stds = [], [], []
    cas_means, cas_stds = [], []
    succ_means = []
    for sv in sorted(sv_dict.keys(), key=lambda v: float(v)):
        rows = sv_dict[sv]
        ttl_vals.append(float(sv))
        em, es = stats(rows, "effective_goodput_s")
        eff_means.append(em); eff_stds.append(es)
        cm, cs = stats(rows, "cascade_failed")
        cas_means.append(cm); cas_stds.append(cs)
        sm, _ = stats(rows, "success")
        succ_means.append(sm)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    # Panel 1: Effective Goodput/s vs TTL
    ax = axes[0]
    ax.errorbar(ttl_vals, eff_means, yerr=eff_stds, marker="o", linewidth=2,
                capsize=4, color="#2ca02c", alpha=0.85)
    ax.set_xlabel("Price Cache TTL (s)", fontsize=12)
    ax.set_ylabel("Effective Goodput/s", fontsize=12)
    ax.set_title("(a) Effective Goodput/s", fontsize=13, fontweight="bold")
    ax.grid(alpha=0.3)
    for x, m in zip(ttl_vals, eff_means):
        ax.annotate(f"{m:.1f}", (x, m), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=9, fontweight="bold")

    # Panel 2: Cascade Failures vs TTL
    ax = axes[1]
    ax.errorbar(ttl_vals, cas_means, yerr=cas_stds, marker="s", linewidth=2,
                capsize=4, color="#d62728", alpha=0.85)
    ax.set_xlabel("Price Cache TTL (s)", fontsize=12)
    ax.set_ylabel("Cascade Failures", fontsize=12)
    ax.set_title("(b) Cascade Failures", fontsize=13, fontweight="bold")
    ax.grid(alpha=0.3)

    # Panel 3: Success Sessions vs TTL
    ax = axes[2]
    succ_stds = []
    for sv in sorted(sv_dict.keys(), key=lambda v: float(v)):
        _, ss = stats(sv_dict[sv], "success")
        succ_stds.append(ss)
    ax.bar(range(len(ttl_vals)), succ_means, yerr=succ_stds, color="#1f77b4", alpha=0.85, capsize=4)
    ax.set_xticks(range(len(ttl_vals)))
    ax.set_xticklabels([f"{t:.1f}s" for t in ttl_vals], fontsize=10)
    ax.set_xlabel("Price Cache TTL (s)", fontsize=12)
    ax.set_ylabel("Successful Sessions", fontsize=12)
    ax.set_title("(c) Successful Sessions", fontsize=13, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    for i, (m, s) in enumerate(zip(succ_means, succ_stds)):
        ax.text(i, m + s + 2, f"{m:.0f}", ha="center", fontsize=9, fontweight="bold")

    fig.suptitle("Exp7: Client Hard Reject — Price TTL Sweep (PlanGate-Full)",
                 fontsize=14, fontweight="bold", y=1.02)
    plt.tight_layout()
    path = os.path.join(OUT_DIR, "exp7_client_reject.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_e2e_latency_bars(data, gateways, title, fname):
    """E2E 会话端到端延迟柱状图"""
    gw_list = [g for g in gateways if g in data]
    if not gw_list:
        print(f"  [WARN] E2E 数据不足, 跳过 {fname}")
        return

    labels = [LABELS.get(g, g) for g in gw_list]
    e2e_p50_m = [stats(data[g], "e2e_p50_ms")[0] for g in gw_list]
    e2e_p50_s = [stats(data[g], "e2e_p50_ms")[1] for g in gw_list]
    e2e_p95_m = [stats(data[g], "e2e_p95_ms")[0] for g in gw_list]
    e2e_p95_s = [stats(data[g], "e2e_p95_ms")[1] for g in gw_list]
    e2e_p99_m = [stats(data[g], "e2e_p99_ms")[0] for g in gw_list]
    e2e_p99_s = [stats(data[g], "e2e_p99_ms")[1] for g in gw_list]

    # 跳过全零数据
    if max(e2e_p50_m) == 0 and max(e2e_p95_m) == 0:
        print(f"  [WARN] E2E 数据全零, 跳过 {fname}")
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(gw_list))
    w = 0.25

    ax.bar(x - w, e2e_p50_m, w, yerr=e2e_p50_s, label="E2E P50", color="#42a5f5", alpha=0.85, capsize=3)
    ax.bar(x, e2e_p95_m, w, yerr=e2e_p95_s, label="E2E P95", color="#ffa726", alpha=0.85, capsize=3)
    ax.bar(x + w, e2e_p99_m, w, yerr=e2e_p99_s, label="E2E P99", color="#ef5350", alpha=0.85, capsize=3)

    ax.set_xlabel("Gateway", fontsize=13)
    ax.set_ylabel("Session E2E Latency (ms)", fontsize=13)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    path = os.path.join(OUT_DIR, fname)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_exp5_e2e_sweep(data_sweep):
    """Exp5: 并发扩展 — E2E 端到端延迟折线"""
    _plot_sweep_line(data_sweep, "e2e_p50_ms",
                     "Concurrency", "Session E2E P50 (ms)",
                     "Exp5: Concurrency vs Session E2E P50 Latency",
                     "exp5_scale_conc_e2e.png")


def main():
    exp1_path = os.path.join(RESULTS_DIR, "exp1_core", "exp1_core_summary.csv")
    exp4_path = os.path.join(RESULTS_DIR, "exp4_ablation", "exp4_ablation_summary.csv")
    exp2_path = os.path.join(RESULTS_DIR, "exp2_heavyratio", "exp2_heavyratio_summary.csv")
    exp3_path = os.path.join(RESULTS_DIR, "exp3_mixedmode", "exp3_mixedmode_summary.csv")
    exp5_path = os.path.join(RESULTS_DIR, "exp5_scaleconc", "exp5_scaleconc_summary.csv")
    exp7_path = os.path.join(RESULTS_DIR, "exp7_clientreject", "exp7_clientreject_summary.csv")

    print("加载数据...")

    if os.path.exists(exp1_path):
        data1 = load_summary(exp1_path)
        print("生成 Exp1 图表...")
        plot_exp1_session_breakdown(data1)
        plot_exp1_goodput(data1)
        plot_exp1_latency(data1)
        plot_exp1_radar(data1)
        plot_e2e_latency_bars(data1, EXP1_GATEWAYS,
                              "Exp1_Core: Session E2E Latency (Successful Sessions Only)",
                              "exp1_e2e_latency.png")
    else:
        print(f"  [SKIP] Exp1 数据不存在: {exp1_path}")

    if os.path.exists(exp4_path):
        data4 = load_summary(exp4_path)
        print("生成 Exp4 图表...")
        plot_exp4_ablation(data4)
    else:
        print(f"  [SKIP] Exp4 数据不存在: {exp4_path}")

    if os.path.exists(exp2_path):
        data2 = load_sweep_summary(exp2_path)
        print("生成 Exp2 图表...")
        plot_exp2_heavy_ratio(data2)
    else:
        print(f"  [SKIP] Exp2 数据不存在: {exp2_path}")

    if os.path.exists(exp3_path):
        data3 = load_sweep_summary(exp3_path)
        print("生成 Exp3 图表...")
        plot_exp3_mixed_mode(data3)
    else:
        print(f"  [SKIP] Exp3 数据不存在: {exp3_path}")

    if os.path.exists(exp5_path):
        data5 = load_sweep_summary(exp5_path)
        print("生成 Exp5 图表...")
        plot_exp5_scale_conc(data5)
        plot_exp5_e2e_sweep(data5)
    else:
        print(f"  [SKIP] Exp5 数据不存在: {exp5_path}")

    if os.path.exists(exp7_path):
        data7 = load_sweep_summary(exp7_path)
        print("生成 Exp7 图表...")
        plot_exp7_client_reject(data7)
    else:
        print(f"  [SKIP] Exp7 数据不存在: {exp7_path}")

    print(f"\n所有图表已保存至: {OUT_DIR}")


if __name__ == "__main__":
    main()
