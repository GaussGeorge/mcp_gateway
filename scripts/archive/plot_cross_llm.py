"""
plot_cross_llm.py — Cross-LLM Provider Comparison (Paper Figure)
================================================================
生成学术论文级的跨 LLM 对比图:
  - 2×3 panel: 上行 GLM, 下行 DeepSeek
  - 或 grouped bar chart: 每个 metric 一组, 按 provider+gateway 分组

用法:
  python scripts/plot_cross_llm.py
"""

import csv
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

# ── 配置 ──
RESULT_DIRS = {
    "GLM-4-Flash\n(200 RPM, C=10)": "results/exp_real3_glm",
    "DeepSeek-V3\n(60 RPM, C=3)": "results/exp_real3_deepseek",
}

# 论文中的主对比: NG, SRL, PlanGate (no-sessioncap)
# mcpdp-real-no-sessioncap 作为 "PlanGate" 的代表
GATEWAY_ORDER = ["ng", "srl", "mcpdp-real-no-sessioncap"]
GATEWAY_LABELS = {
    "ng": "No-Gov",
    "srl": "SRL",
    "mcpdp-real-no-sessioncap": "PlanGate",
}
GATEWAY_COLORS = {
    "ng": "#e74c3c",
    "srl": "#3498db",
    "mcpdp-real-no-sessioncap": "#2ecc71",
}
GATEWAY_HATCH = {
    "ng": "//",
    "srl": "..",
    "mcpdp-real-no-sessioncap": "",
}

# ── 论文字体 ──
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 11,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "figure.dpi": 300,
})


def _detect_mode(basename: str) -> str:
    if "mcpdp-real-no-sessioncap" in basename:
        return "mcpdp-real-no-sessioncap"
    if "mcpdp-real" in basename or "mcpdp" in basename:
        return "mcpdp-real"
    return basename.split("_")[0]


def load_summaries(input_dir: str) -> dict:
    """加载最新的 summary CSV (按时间戳排序取最后)。"""
    summary_files = sorted(glob.glob(os.path.join(input_dir, "summary_*.csv")))
    if not summary_files:
        return {}
    latest = summary_files[-1]
    data = {}
    with open(latest, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            data[row["gateway"]] = row
    return data


def load_agent_csvs(input_dir: str) -> dict:
    """加载所有 *_agents.csv，按 mode 分组 (取最新)。"""
    data = {}
    for f in sorted(glob.glob(os.path.join(input_dir, "*_agents.csv"))):
        mode = _detect_mode(os.path.basename(f))
        agents = []
        with open(f, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                agents.append(row)
        data[mode] = agents
    return data


def main():
    output_dir = "results/paper_figures"
    os.makedirs(output_dir, exist_ok=True)

    # ── 加载数据 ──
    all_summaries = {}
    all_agents = {}
    for label, dir_path in RESULT_DIRS.items():
        all_summaries[label] = load_summaries(dir_path)
        all_agents[label] = load_agent_csvs(dir_path)

    providers = list(RESULT_DIRS.keys())
    gateways = GATEWAY_ORDER

    # ══════════════════════════════════════════════════════════
    # Figure 1: Cross-LLM 综合对比 (2×2 grouped bars)
    # ══════════════════════════════════════════════════════════
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle("Cross-LLM Provider Validation: PlanGate Adaptive Governance",
                 fontsize=14, fontweight="bold", y=0.98)

    n_providers = len(providers)
    n_gateways = len(gateways)
    width = 0.22
    x = np.arange(n_providers)

    # ── Panel (a): Success Rate ──
    ax = axes[0, 0]
    for j, gw in enumerate(gateways):
        vals = []
        for prov in providers:
            summary = all_summaries[prov].get(gw, {})
            total = int(summary.get("agents", 50))
            success = int(summary.get("success", 0))
            vals.append(100 * success / max(total, 1))
        bars = ax.bar(x + j * width - width, vals, width,
                      color=GATEWAY_COLORS[gw], hatch=GATEWAY_HATCH[gw],
                      edgecolor="black", linewidth=0.5,
                      label=GATEWAY_LABELS[gw])
        for i, v in enumerate(vals):
            ax.text(x[i] + j * width - width, v + 1.5, f"{v:.0f}%",
                    ha="center", fontsize=9, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(providers)
    ax.set_ylabel("Success Rate (%)")
    ax.set_title("(a) Task Success Rate")
    ax.legend(loc="upper left")
    ax.set_ylim(0, 105)
    ax.axhline(y=70, color="gray", linestyle="--", alpha=0.3, linewidth=0.8)

    # ── Panel (b): Cascade Waste Steps ──
    ax = axes[0, 1]
    for j, gw in enumerate(gateways):
        vals = []
        for prov in providers:
            summary = all_summaries[prov].get(gw, {})
            vals.append(int(summary.get("cascade_wasted_steps", 0)))
        bars = ax.bar(x + j * width - width, vals, width,
                      color=GATEWAY_COLORS[gw], hatch=GATEWAY_HATCH[gw],
                      edgecolor="black", linewidth=0.5,
                      label=GATEWAY_LABELS[gw])
        for i, v in enumerate(vals):
            ax.text(x[i] + j * width - width, v + 0.5, str(v),
                    ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(providers)
    ax.set_ylabel("Wasted Steps")
    ax.set_title("(b) Cascade Waste Steps")
    ax.legend(loc="upper right")

    # ── Panel (c): Effective GP/s ──
    ax = axes[1, 0]
    for j, gw in enumerate(gateways):
        vals = []
        for prov in providers:
            summary = all_summaries[prov].get(gw, {})
            vals.append(float(summary.get("eff_gp_per_s", 0)))
        bars = ax.bar(x + j * width - width, vals, width,
                      color=GATEWAY_COLORS[gw], hatch=GATEWAY_HATCH[gw],
                      edgecolor="black", linewidth=0.5,
                      label=GATEWAY_LABELS[gw])
        for i, v in enumerate(vals):
            ax.text(x[i] + j * width - width, v + 0.005, f"{v:.2f}",
                    ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(providers)
    ax.set_ylabel("Effective Goodput / sec")
    ax.set_title("(c) Effective Throughput (GP/s)")
    ax.legend(loc="upper left")

    # ── Panel (d): Token Efficiency (tokens per successful agent) ──
    ax = axes[1, 1]
    for j, gw in enumerate(gateways):
        vals = []
        for prov in providers:
            summary = all_summaries[prov].get(gw, {})
            total_tokens = int(summary.get("agent_llm_tokens", 0)) + int(summary.get("backend_llm_tokens", 0))
            successes = max(int(summary.get("success", 1)), 1)
            vals.append(total_tokens / successes / 1000)  # K tokens per success
        bars = ax.bar(x + j * width - width, vals, width,
                      color=GATEWAY_COLORS[gw], hatch=GATEWAY_HATCH[gw],
                      edgecolor="black", linewidth=0.5,
                      label=GATEWAY_LABELS[gw])
        for i, v in enumerate(vals):
            ax.text(x[i] + j * width - width, v + 0.2, f"{v:.1f}K",
                    ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(providers)
    ax.set_ylabel("Tokens per Success (K)")
    ax.set_title("(d) Token Efficiency")
    ax.legend(loc="upper left")

    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    out1 = os.path.join(output_dir, "cross_llm_comparison.png")
    plt.savefig(out1, dpi=300, bbox_inches="tight")
    print(f"[图表] 已保存: {out1}")

    out1_pdf = os.path.join(output_dir, "cross_llm_comparison.pdf")
    plt.savefig(out1_pdf, bbox_inches="tight")
    print(f"[图表] 已保存: {out1_pdf}")
    plt.close()

    # ══════════════════════════════════════════════════════════
    # Figure 2: Ablation — SessionCap effect per provider
    # ══════════════════════════════════════════════════════════
    ABLATION_GATEWAYS = ["ng", "mcpdp-real", "mcpdp-real-no-sessioncap"]
    ABLATION_LABELS = {
        "ng": "No-Gov",
        "mcpdp-real": "PlanGate+SC",
        "mcpdp-real-no-sessioncap": "PlanGate",
    }
    ABLATION_COLORS = {
        "ng": "#e74c3c",
        "mcpdp-real": "#27ae60",
        "mcpdp-real-no-sessioncap": "#2ecc71",
    }

    fig2, axes2 = plt.subplots(1, 2, figsize=(10, 4.5))
    fig2.suptitle("Ablation: Session Cap Impact Across LLM Providers",
                  fontsize=13, fontweight="bold", y=1.02)

    for ax_idx, prov in enumerate(providers):
        ax = axes2[ax_idx]
        ab_gateways = [g for g in ABLATION_GATEWAYS if g in all_summaries[prov]]
        x_ab = np.arange(len(ab_gateways))

        success_vals = []
        reject_vals = []
        for gw in ab_gateways:
            s = all_summaries[prov].get(gw, {})
            total = int(s.get("agents", 50))
            success = int(s.get("success", 0))
            rejected = int(s.get("all_rejected", 0))
            success_vals.append(100 * success / max(total, 1))
            reject_vals.append(100 * rejected / max(total, 1))

        bars = ax.bar(x_ab, success_vals, 0.5,
                      color=[ABLATION_COLORS.get(g, "#999") for g in ab_gateways],
                      edgecolor="black", linewidth=0.5)
        for i, (sv, rv) in enumerate(zip(success_vals, reject_vals)):
            ax.text(i, sv + 1.5, f"{sv:.0f}%", ha="center", fontsize=10, fontweight="bold")
            if rv > 0:
                ax.text(i, sv - 5, f"({rv:.0f}% rej)", ha="center", fontsize=8,
                        color="red", fontstyle="italic")

        ax.set_xticks(x_ab)
        ax.set_xticklabels([ABLATION_LABELS.get(g, g) for g in ab_gateways])
        ax.set_ylabel("Success Rate (%)")
        ax.set_title(prov.split("\n")[0])
        ax.set_ylim(0, 105)

    plt.tight_layout()
    out2 = os.path.join(output_dir, "ablation_sessioncap.png")
    plt.savefig(out2, dpi=300, bbox_inches="tight")
    print(f"[图表] 已保存: {out2}")

    out2_pdf = os.path.join(output_dir, "ablation_sessioncap.pdf")
    plt.savefig(out2_pdf, bbox_inches="tight")
    print(f"[图表] 已保存: {out2_pdf}")
    plt.close()

    # ══════════════════════════════════════════════════════════
    # Figure 3: E2E Latency Box Plot — Cross-LLM
    # ══════════════════════════════════════════════════════════
    fig3, axes3 = plt.subplots(1, 2, figsize=(10, 4.5))
    fig3.suptitle("End-to-End Latency Distribution (Successful Agents)",
                  fontsize=13, fontweight="bold", y=1.02)

    for ax_idx, prov in enumerate(providers):
        ax = axes3[ax_idx]
        latency_data = []
        latency_labels = []
        latency_colors = []
        agents_data = all_agents[prov]
        for gw in gateways:
            if gw not in agents_data:
                continue
            agents = agents_data[gw]
            e2e = [float(a["total_latency_ms"]) / 1000 for a in agents if a["state"] == "SUCCESS"]
            if e2e:
                latency_data.append(e2e)
                latency_labels.append(GATEWAY_LABELS[gw])
                latency_colors.append(GATEWAY_COLORS[gw])

        if latency_data:
            bp = ax.boxplot(latency_data, tick_labels=latency_labels, patch_artist=True)
            for patch, color in zip(bp["boxes"], latency_colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.6)

        ax.set_ylabel("E2E Latency (s)")
        ax.set_title(prov.split("\n")[0])

    plt.tight_layout()
    out3 = os.path.join(output_dir, "latency_cross_llm.png")
    plt.savefig(out3, dpi=300, bbox_inches="tight")
    print(f"[图表] 已保存: {out3}")

    out3_pdf = os.path.join(output_dir, "latency_cross_llm.pdf")
    plt.savefig(out3_pdf, bbox_inches="tight")
    print(f"[图表] 已保存: {out3_pdf}")
    plt.close()

    # ── 打印汇总表 ──
    print("\n" + "=" * 80)
    print("  Cross-LLM Result Summary (Paper Table)")
    print("=" * 80)
    header = f"{'Provider':<20} {'Gateway':<15} {'Success':>10} {'Cascade':>10} {'GP/s':>8} {'Tokens/Succ':>12}"
    print(header)
    print("-" * 80)
    for prov in providers:
        prov_short = prov.split("\n")[0]
        for gw in gateways:
            s = all_summaries[prov].get(gw, {})
            total = int(s.get("agents", 50))
            success = int(s.get("success", 0))
            cascade = int(s.get("cascade_wasted_steps", 0))
            gps = float(s.get("eff_gp_per_s", 0))
            total_tok = int(s.get("agent_llm_tokens", 0)) + int(s.get("backend_llm_tokens", 0))
            tok_per = total_tok / max(success, 1)
            print(f"{prov_short:<20} {GATEWAY_LABELS[gw]:<15} {success}/{total} ({100*success/total:.0f}%){cascade:>8} {gps:>8.2f} {tok_per:>10,.0f}")
        print()

    print("\n✓ 所有论文图表已生成")


if __name__ == "__main__":
    main()
