#!/usr/bin/env python3
"""
analyze_real_llm.py — 真实 LLM 实验聚合分析 + 学术图表
======================================================
读取 results/exp_real3_{glm,deepseek}/summary_all.csv，
计算 mean±std 并生成:
  Fig 1: Success Rate comparison (GLM + DeepSeek, grouped bar with error bars)
  Fig 2: E2E Latency P50/P95 (grouped bar)
  Fig 3: Cascade waste + Effective Goodput (dual-axis)
  Fig 4: Token efficiency per successful task

用法:
  python scripts/analyze_real_llm.py
"""

import csv
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ── 路径 ──
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLM_DIR = os.path.join(BASE, "results", "exp_real3_glm")
DS_DIR = os.path.join(BASE, "results", "exp_real3_deepseek")
OUT_PNG = os.path.join(BASE, "results", "paper_figures", "PNG")
OUT_PDF = os.path.join(BASE, "results", "paper_figures", "PDF")

# ── 样式 ──
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
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
})

GATEWAY_ORDER = ["ng", "srl", "mcpdp-real", "mcpdp-real-no-sessioncap"]
LABELS = {
    "ng": "No-Gov",
    "srl": "SRL",
    "mcpdp-real": "PlanGate",
    "mcpdp-real-no-sessioncap": "PlanGate\n(w/o SC)",
}
COLORS = {
    "ng": "#e74c3c",
    "srl": "#3498db",
    "mcpdp-real": "#2ecc71",
    "mcpdp-real-no-sessioncap": "#1abc9c",
}
HATCHES = {"ng": "//", "srl": "..", "mcpdp-real": "", "mcpdp-real-no-sessioncap": "xx"}


def load_summary(csv_path):
    """Load summary_all.csv and group rows by gateway."""
    data = defaultdict(list)
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"]
            data[gw].append({
                "success": int(row["success"]),
                "partial": int(row["partial"]),
                "all_rejected": int(row["all_rejected"]),
                "agents": int(row["agents"]),
                "cascade_wasted_steps": int(row["cascade_wasted_steps"]),
                "agent_llm_tokens": int(row["agent_llm_tokens"]),
                "backend_llm_tokens": int(row["backend_llm_tokens"]),
                "raw_goodput": float(row["raw_goodput"]),
                "effective_goodput": float(row["effective_goodput"]),
                "eff_gp_per_s": float(row["eff_gp_per_s"]),
                "e2e_p50_ms": float(row["e2e_p50_ms"]),
                "e2e_p95_ms": float(row["e2e_p95_ms"]),
                "elapsed_s": float(row["elapsed_s"]),
            })
    return data


def compute_stats(data, field):
    """Return mean, std for a field across runs."""
    vals = [r[field] for r in data]
    return np.mean(vals), np.std(vals)


def _save(fig, name):
    os.makedirs(OUT_PNG, exist_ok=True)
    os.makedirs(OUT_PDF, exist_ok=True)
    fig.savefig(os.path.join(OUT_PNG, f"{name}.png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(OUT_PDF, f"{name}.pdf"), bbox_inches="tight")
    print(f"  [saved] {name}.png / .pdf")
    plt.close(fig)


def plot_success_rate(glm, ds):
    """Fig 1: Success rate comparison (GLM + DeepSeek) with error bars."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    providers = [("GLM-4-Flash (C=10)", glm, axes[0]),
                 ("DeepSeek-V3 (C=3)", ds, axes[1])]

    for title, data, ax in providers:
        gws = [g for g in GATEWAY_ORDER if g in data]
        x = np.arange(len(gws))
        width = 0.5

        means, stds = [], []
        for g in gws:
            m, s = compute_stats(data[g], "success")
            means.append(m / data[g][0]["agents"] * 100)
            stds.append(s / data[g][0]["agents"] * 100)

        bars = ax.bar(x, means, width, yerr=stds, capsize=5,
                      color=[COLORS[g] for g in gws],
                      edgecolor="black", linewidth=0.6)

        for i, (m, s) in enumerate(zip(means, stds)):
            ax.text(i, m + s + 1.5, f"{m:.1f}±{s:.1f}%",
                    ha="center", va="bottom", fontsize=9, fontweight="bold")

        ax.set_xticks(x)
        ax.set_xticklabels([LABELS[g] for g in gws])
        ax.set_title(title, fontweight="bold")
        ax.set_ylim(0, 110)
        ax.set_ylabel("Success Rate (%)")

    fig.suptitle("Task Success Rate — Real LLM Experiments (3 Runs)", fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    _save(fig, "fig_real_success_rate")


def plot_e2e_latency(glm, ds):
    """Fig 2: E2E latency P50/P95 grouped bar."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    providers = [("GLM-4-Flash", glm, axes[0]),
                 ("DeepSeek-V3", ds, axes[1])]

    for title, data, ax in providers:
        gws = [g for g in GATEWAY_ORDER if g in data]
        x = np.arange(len(gws))
        w = 0.35

        p50_m, p50_s, p95_m, p95_s = [], [], [], []
        for g in gws:
            m50, s50 = compute_stats(data[g], "e2e_p50_ms")
            m95, s95 = compute_stats(data[g], "e2e_p95_ms")
            p50_m.append(m50 / 1000)
            p50_s.append(s50 / 1000)
            p95_m.append(m95 / 1000)
            p95_s.append(s95 / 1000)

        ax.bar(x - w/2, p50_m, w, yerr=p50_s, capsize=4,
               color="#3498db", alpha=0.8, label="P50")
        ax.bar(x + w/2, p95_m, w, yerr=p95_s, capsize=4,
               color="#e74c3c", alpha=0.8, label="P95")

        ax.set_xticks(x)
        ax.set_xticklabels([LABELS[g] for g in gws])
        ax.set_ylabel("Latency (seconds)")
        ax.set_title(title, fontweight="bold")
        ax.legend()

    fig.suptitle("End-to-End Latency — Real LLM (3 Runs)", fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    _save(fig, "fig_real_e2e_latency")


def plot_cascade_goodput(glm, ds):
    """Fig 3: Cascade waste + Effective GP/s (dual axis)."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    providers = [("GLM-4-Flash", glm, axes[0]),
                 ("DeepSeek-V3", ds, axes[1])]

    for title, data, ax in providers:
        gws = [g for g in GATEWAY_ORDER if g in data]
        x = np.arange(len(gws))
        w = 0.35

        cw_m, cw_s, gp_m, gp_s = [], [], [], []
        for g in gws:
            m1, s1 = compute_stats(data[g], "cascade_wasted_steps")
            m2, s2 = compute_stats(data[g], "eff_gp_per_s")
            cw_m.append(m1); cw_s.append(s1)
            gp_m.append(m2); gp_s.append(s2)

        ax.bar(x, cw_m, w, yerr=cw_s, capsize=4,
               color=[COLORS[g] for g in gws], edgecolor="black", linewidth=0.5)
        for i, (m, s) in enumerate(zip(cw_m, cw_s)):
            ax.text(i, m + s + 0.5, f"{m:.1f}", ha="center", fontsize=9)
        ax.set_ylabel("Cascade Wasted Steps")
        ax.set_xticks(x)
        ax.set_xticklabels([LABELS[g] for g in gws])
        ax.set_title(title, fontweight="bold")

        ax2 = ax.twinx()
        ax2.plot(x, gp_m, "ko-", linewidth=2, markersize=6, label="Eff GP/s")
        for i, (m, s) in enumerate(zip(gp_m, gp_s)):
            ax2.annotate(f"{m:.2f}", (i, m), textcoords="offset points",
                         xytext=(0, 8), ha="center", fontsize=8, fontweight="bold")
        ax2.set_ylabel("Effective GP/s")
        ax2.legend(loc="upper right")

    fig.suptitle("Cascade Waste & Effective Goodput — Real LLM (3 Runs)", fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    _save(fig, "fig_real_cascade_goodput")


def plot_token_efficiency(glm, ds):
    """Fig 4: Token cost per successful task."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    providers = [("GLM-4-Flash", glm, axes[0]),
                 ("DeepSeek-V3", ds, axes[1])]

    for title, data, ax in providers:
        gws = [g for g in GATEWAY_ORDER if g in data]
        x = np.arange(len(gws))
        w = 0.5

        eff_m, eff_s = [], []
        for g in gws:
            vals = []
            for r in data[g]:
                total_tokens = r["agent_llm_tokens"] + r["backend_llm_tokens"]
                success = max(r["success"], 1)
                vals.append(total_tokens / success)
            eff_m.append(np.mean(vals))
            eff_s.append(np.std(vals))

        ax.bar(x, eff_m, w, yerr=eff_s, capsize=5,
               color=[COLORS[g] for g in gws], edgecolor="black", linewidth=0.5)
        for i, (m, s) in enumerate(zip(eff_m, eff_s)):
            ax.text(i, m + s + 100, f"{m:,.0f}", ha="center", fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels([LABELS[g] for g in gws])
        ax.set_ylabel("Tokens per Successful Task")
        ax.set_title(title, fontweight="bold")

    fig.suptitle("Token Efficiency — Real LLM (3 Runs)", fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    _save(fig, "fig_real_token_efficiency")


def print_summary_table(name, data):
    """Print formatted summary table."""
    gws = [g for g in GATEWAY_ORDER if g in data]
    print(f"\n{'='*80}")
    print(f"  {name} — Mean ± Std (N=3)")
    print(f"{'='*80}")
    header = f"{'Gateway':<30} {'Success%':>10} {'P50(s)':>10} {'P95(s)':>10} {'Cascade':>10} {'GP/s':>8} {'Tok/Task':>10}"
    print(header)
    print("-" * 80)
    for g in gws:
        sm, ss = compute_stats(data[g], "success")
        agents = data[g][0]["agents"]
        sr_m, sr_s = sm / agents * 100, ss / agents * 100

        p50m, p50s = compute_stats(data[g], "e2e_p50_ms")
        p95m, p95s = compute_stats(data[g], "e2e_p95_ms")
        cwm, cws = compute_stats(data[g], "cascade_wasted_steps")
        gpm, gps = compute_stats(data[g], "eff_gp_per_s")

        tok_vals = [(r["agent_llm_tokens"] + r["backend_llm_tokens"]) / max(r["success"], 1) for r in data[g]]
        tm, ts = np.mean(tok_vals), np.std(tok_vals)

        print(f"{LABELS.get(g,g):<30} {sr_m:>5.1f}±{sr_s:<4.1f} {p50m/1000:>5.1f}±{p50s/1000:<4.1f} "
              f"{p95m/1000:>5.1f}±{p95s/1000:<4.1f} {cwm:>5.1f}±{cws:<4.1f} {gpm:>4.2f}±{gps:<4.2f} "
              f"{tm:>7,.0f}±{ts:<5,.0f}")
    print()


def main():
    # Load data
    glm_path = os.path.join(GLM_DIR, "summary_all.csv")
    ds_path = os.path.join(DS_DIR, "summary_all.csv")

    if not os.path.exists(glm_path):
        print(f"[WARN] GLM data not found: {glm_path}")
        glm = {}
    else:
        glm = load_summary(glm_path)

    if not os.path.exists(ds_path):
        print(f"[WARN] DeepSeek data not found: {ds_path}")
        ds = {}
    else:
        ds = load_summary(ds_path)

    if not glm and not ds:
        print("[ERROR] No data found. Run experiments first.")
        sys.exit(1)

    # Print tables
    if glm:
        print_summary_table("GLM-4-Flash (C=10, 200 RPM)", glm)
    if ds:
        print_summary_table("DeepSeek-V3 (C=3, 60 RPM)", ds)

    # Generate figures
    if glm and ds:
        plot_success_rate(glm, ds)
        plot_e2e_latency(glm, ds)
        plot_cascade_goodput(glm, ds)
        plot_token_efficiency(glm, ds)
        print(f"\n  All figures saved to: {OUT_PNG} and {OUT_PDF}")
    elif glm:
        print("  [INFO] Only GLM data available, skipping dual-panel plots")
    elif ds:
        print("  [INFO] Only DeepSeek data available, skipping dual-panel plots")


if __name__ == "__main__":
    main()
