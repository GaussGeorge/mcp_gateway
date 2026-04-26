"""
plot_exp_real3.py — Exp-Real-3 真·ReAct Agent 实验可视化
=======================================================
生成 4-panel 对比图:
  Panel 1: 任务完成率 (SUCCESS / PARTIAL / ALL_REJECTED)
  Panel 2: 级联浪费步骤 + 级联浪费 Agent 数
  Panel 3: Token 消耗 (Agent Brain + Backend LLM)
  Panel 4: E2E 延迟分布 (成功 Agent)

用法:
  python scripts/plot_exp_real3.py --input results/exp_real3_glm
  python scripts/plot_exp_real3.py --input results/exp_real3_deepseek
"""

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


# ── 样式 ──
COLORS = {
    "ng": "#e74c3c", "srl": "#3498db", "mcpdp-real": "#2ecc71",
    "mcpdp-real-no-sessioncap": "#1abc9c",
}
LABELS = {
    "ng": "No-Gov (NG)", "srl": "SRL", "mcpdp-real": "PlanGate-Real",
    "mcpdp-real-no-sessioncap": "PlanGate-Real (w/o SC)",
}
HATCH = {"ng": "//", "srl": "..", "mcpdp-real": "", "mcpdp-real-no-sessioncap": "xx"}


def _detect_mode(basename: str) -> str:
    """Detect gateway mode from filename."""
    if "mcpdp-real-no-sessioncap" in basename:
        return "mcpdp-real-no-sessioncap"
    if "mcpdp-real" in basename or "mcpdp" in basename:
        return "mcpdp-real"
    return basename.split("_")[0]


def load_agent_csvs(input_dir: str) -> dict:
    """加载所有 *_agents.csv 文件，按 gateway mode 分组。"""
    data = {}
    for f in sorted(glob.glob(os.path.join(input_dir, "*_agents.csv"))):
        basename = os.path.basename(f)
        mode = _detect_mode(basename)
        agents = []
        with open(f, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                agents.append(row)
        data[mode] = agents
    return data


def load_step_csvs(input_dir: str) -> dict:
    """加载步骤级 CSV (非 _agents, 非 _summary)。"""
    data = {}
    for f in sorted(glob.glob(os.path.join(input_dir, "*.csv"))):
        basename = os.path.basename(f)
        if "_agents" in basename or "_summary" in basename:
            continue
        mode = _detect_mode(basename)
        steps = []
        with open(f, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                steps.append(row)
        data[mode] = steps
    return data


def plot_results(input_dir: str, output_path: str):
    agent_data = load_agent_csvs(input_dir)
    step_data = load_step_csvs(input_dir)

    if not agent_data:
        print(f"[ERROR] 未找到 *_agents.csv 文件: {input_dir}")
        sys.exit(1)

    # 动态检测模式顺序
    MODE_ORDER = ["ng", "srl", "mcpdp-real", "mcpdp-real-no-sessioncap"]
    modes = [m for m in MODE_ORDER if m in agent_data]
    if not modes:
        # fallback: 使用所有检测到的模式
        modes = sorted(agent_data.keys())

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Exp-Real-3: True ReAct Agent — Gateway Comparison", fontsize=14, fontweight="bold")

    # ── Panel 1: 任务完成率 (堆叠柱状图) ──
    ax = axes[0, 0]
    x = np.arange(len(modes))
    width = 0.5
    for i, mode in enumerate(modes):
        agents = agent_data[mode]
        total = len(agents)
        success = sum(1 for a in agents if a["state"] == "SUCCESS")
        partial = sum(1 for a in agents if a["state"] == "PARTIAL")
        rejected = sum(1 for a in agents if a["state"] == "ALL_REJECTED")
        error = sum(1 for a in agents if a["state"] == "ERROR")

        s_pct = 100 * success / max(total, 1)
        p_pct = 100 * partial / max(total, 1)
        r_pct = 100 * rejected / max(total, 1)
        e_pct = 100 * error / max(total, 1)

        ax.bar(i, s_pct, width, color="#2ecc71", label="SUCCESS" if i == 0 else "")
        ax.bar(i, p_pct, width, bottom=s_pct, color="#f39c12", label="PARTIAL" if i == 0 else "")
        ax.bar(i, r_pct, width, bottom=s_pct + p_pct, color="#e74c3c", label="ALL_REJECTED" if i == 0 else "")
        ax.bar(i, e_pct, width, bottom=s_pct + p_pct + r_pct, color="#95a5a6", label="ERROR" if i == 0 else "")

        ax.text(i, s_pct / 2, f"{success}", ha="center", va="center", fontweight="bold", fontsize=10)

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS.get(m, m) for m in modes])
    ax.set_ylabel("Percentage (%)")
    ax.set_title("(a) Task Completion Rate")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_ylim(0, 105)

    # ── Panel 2: 级联浪费 ──
    ax = axes[0, 1]
    cascade_steps = []
    cascade_agents = []
    for mode in modes:
        agents = agent_data[mode]
        ws = sum(int(a["success_steps"]) for a in agents if a["state"] in ("PARTIAL", "ALL_REJECTED"))
        wa = sum(1 for a in agents if a["state"] in ("PARTIAL", "ALL_REJECTED"))
        cascade_steps.append(ws)
        cascade_agents.append(wa)

    bar_w = 0.35
    ax.bar(x - bar_w / 2, cascade_steps, bar_w, color=[COLORS.get(m, "#666") for m in modes], label="Wasted Steps")
    ax.bar(x + bar_w / 2, cascade_agents, bar_w, color=[COLORS.get(m, "#666") for m in modes], alpha=0.5, label="Wasted Agents")
    for i, (ws, wa) in enumerate(zip(cascade_steps, cascade_agents)):
        ax.text(i - bar_w / 2, ws + 0.3, str(ws), ha="center", fontsize=9)
        ax.text(i + bar_w / 2, wa + 0.3, str(wa), ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS.get(m, m) for m in modes])
    ax.set_ylabel("Count")
    ax.set_title("(b) Cascade Waste")
    ax.legend(fontsize=8)

    # ── Panel 3: Token 消耗 ──
    ax = axes[1, 0]
    agent_tokens = []
    backend_tokens = []
    for mode in modes:
        agents = agent_data[mode]
        at = sum(int(a["agent_llm_tokens"]) for a in agents)
        bt = sum(int(a["backend_llm_tokens"]) for a in agents)
        agent_tokens.append(at)
        backend_tokens.append(bt)

    ax.bar(x - bar_w / 2, agent_tokens, bar_w, color="#3498db", label="Agent Brain")
    ax.bar(x + bar_w / 2, backend_tokens, bar_w, color="#e67e22", label="Backend LLM Tool")
    for i, (at, bt) in enumerate(zip(agent_tokens, backend_tokens)):
        ax.text(i - bar_w / 2, at + 50, f"{at:,}", ha="center", fontsize=7, rotation=45)
        ax.text(i + bar_w / 2, bt + 50, f"{bt:,}", ha="center", fontsize=7, rotation=45)
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS.get(m, m) for m in modes])
    ax.set_ylabel("Tokens")
    ax.set_title("(c) LLM Token Consumption")
    ax.legend(fontsize=8)

    # ── Panel 4: E2E 延迟 (箱线图) ──
    ax = axes[1, 1]
    latency_data = []
    latency_labels = []
    for mode in modes:
        agents = agent_data[mode]
        e2e = [float(a["total_latency_ms"]) for a in agents if a["state"] == "SUCCESS"]
        if e2e:
            latency_data.append(e2e)
            latency_labels.append(LABELS.get(mode, mode))
    if latency_data:
        bp = ax.boxplot(latency_data, labels=latency_labels, patch_artist=True)
        for patch, mode in zip(bp["boxes"], modes):
            patch.set_facecolor(COLORS.get(mode, "#ccc"))
            patch.set_alpha(0.6)
    ax.set_ylabel("E2E Latency (ms)")
    ax.set_title("(d) End-to-End Latency (SUCCESS only)")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    print(f"\n  [图表] 已保存: {output_path}")
    plt.close()

    # ── 额外: 工具调用分布饼图 ──
    if step_data:
        fig2, axes2 = plt.subplots(1, len(modes), figsize=(5 * len(modes), 5))
        if len(modes) == 1:
            axes2 = [axes2]
        fig2.suptitle("Exp-Real-3: Tool Call Distribution per Gateway", fontsize=13, fontweight="bold")

        for ax, mode in zip(axes2, modes):
            steps = step_data.get(mode, [])
            tool_counts = defaultdict(int)
            for s in steps:
                tool_counts[s["tool_name"]] += 1
            if tool_counts:
                labels = list(tool_counts.keys())
                sizes = list(tool_counts.values())
                ax.pie(sizes, labels=labels, autopct="%1.0f%%", startangle=90)
            ax.set_title(LABELS.get(mode, mode))

        pie_path = output_path.replace(".png", "_tools.png")
        plt.tight_layout(rect=[0, 0, 1, 0.93])
        plt.savefig(pie_path, dpi=200, bbox_inches="tight")
        print(f"  [图表] 工具分布: {pie_path}")
        plt.close()


def main():
    parser = argparse.ArgumentParser(description="Exp-Real-3 可视化")
    parser.add_argument("--input", required=True, help="实验结果目录 (如 results/exp_real3_glm)")
    parser.add_argument("--output", default=None, help="输出图片路径 (默认: <input>/exp_real3.png)")
    args = parser.parse_args()

    output = args.output or os.path.join(args.input, "exp_real3.png")
    plot_results(args.input, output)


if __name__ == "__main__":
    main()
