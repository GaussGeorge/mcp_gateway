#!/usr/bin/env python3
"""
plot_architecture.py — 生成 PlanGate 三层架构图
================================================
输出: paper/figures/architecture.pdf + .png
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# ═══════════════════════════════════════════════════════════════
# 全局样式
# ═══════════════════════════════════════════════════════════════
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9,
    "figure.dpi": 300,
    "savefig.dpi": 300,
})

# 配色
C_AGENT_BG   = "#EBF5FB"   # 淡蓝
C_AGENT_BD   = "#2980B9"   # 蓝色边框
C_GW_BG      = "#E8F8F5"   # 淡绿
C_GW_BD      = "#27AE60"   # 绿色边框
C_BACKEND_BG = "#FEF9E7"   # 淡黄
C_BACKEND_BD = "#F39C12"   # 橙色边框
C_COMP_BG    = "#FFFFFF"   # 白色组件背景
C_ARROW      = "#2C3E50"   # 深色箭头
C_REJECT     = "#E74C3C"   # 红色拒绝箭头
C_DARK       = "#2C3E50"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "paper", "figures")


def draw_rounded_box(ax, xy, width, height, label, facecolor, edgecolor,
                     fontsize=9, fontweight="bold", linewidth=1.5, zorder=2):
    """绘制圆角矩形 + 居中文本"""
    box = FancyBboxPatch(
        xy, width, height,
        boxstyle="round,pad=0.02",
        facecolor=facecolor, edgecolor=edgecolor,
        linewidth=linewidth, zorder=zorder,
    )
    ax.add_patch(box)
    cx = xy[0] + width / 2
    cy = xy[1] + height / 2
    ax.text(cx, cy, label, ha="center", va="center",
            fontsize=fontsize, fontweight=fontweight, color=C_DARK, zorder=zorder + 1)


def draw_arrow(ax, xy_start, xy_end, label="", color=C_ARROW, style="->",
               connectionstyle="arc3,rad=0", fontsize=7.5, label_offset=(0, 0.02)):
    """绘制带标签的箭头"""
    arrow = FancyArrowPatch(
        xy_start, xy_end,
        arrowstyle=style, color=color,
        linewidth=1.5, mutation_scale=12,
        connectionstyle=connectionstyle,
        zorder=5
    )
    ax.add_patch(arrow)
    if label:
        mid_x = (xy_start[0] + xy_end[0]) / 2 + label_offset[0]
        mid_y = (xy_start[1] + xy_end[1]) / 2 + label_offset[1]
        ax.text(mid_x, mid_y, label, ha="center", va="center",
                fontsize=fontsize, color=color, style="italic", zorder=6)


def main():
    fig, ax = plt.subplots(1, 1, figsize=(7.0, 5.5))
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.set_aspect("equal")
    ax.axis("off")

    # ═══════════════════════════════════════════════════════════
    # Layer 1: Agent Clients (顶层)
    # ═══════════════════════════════════════════════════════════
    # 大背景框
    agent_box = FancyBboxPatch(
        (0.05, 0.82), 0.90, 0.18,
        boxstyle="round,pad=0.015",
        facecolor=C_AGENT_BG, edgecolor=C_AGENT_BD,
        linewidth=2.0, zorder=1
    )
    ax.add_patch(agent_box)
    ax.text(0.50, 0.97, "Agent Clients", ha="center", va="center",
            fontsize=11, fontweight="bold", color=C_AGENT_BD, zorder=3)

    # ReAct Agent
    draw_rounded_box(ax, (0.10, 0.84), 0.25, 0.10,
                     "ReAct Agent\n(step-by-step)", C_COMP_BG, C_AGENT_BD, fontsize=8)
    # Plan-and-Solve Agent
    draw_rounded_box(ax, (0.40, 0.84), 0.25, 0.10,
                     "Plan-and-Solve Agent\n(DAG upfront)", C_COMP_BG, C_AGENT_BD, fontsize=8)
    # More agents (...)
    draw_rounded_box(ax, (0.70, 0.84), 0.20, 0.10,
                     "Agent N\n...", C_COMP_BG, C_AGENT_BD, fontsize=8)

    # ═══════════════════════════════════════════════════════════
    # Layer 2: PlanGate Gateway (中间层 — 核心)
    # ═══════════════════════════════════════════════════════════
    gw_box = FancyBboxPatch(
        (0.05, 0.35), 0.90, 0.40,
        boxstyle="round,pad=0.015",
        facecolor=C_GW_BG, edgecolor=C_GW_BD,
        linewidth=2.5, zorder=1
    )
    ax.add_patch(gw_box)
    ax.text(0.50, 0.72, "PlanGate Gateway  (Go reverse proxy)",
            ha="center", va="center",
            fontsize=11, fontweight="bold", color=C_GW_BD, zorder=3)

    # 内部组件
    comp_w, comp_h = 0.24, 0.10

    # 动态价格表
    draw_rounded_box(ax, (0.08, 0.58), comp_w, comp_h,
                     "Dynamic Price\nTable", C_COMP_BG, C_GW_BD, fontsize=8)
    # 预算锁定管理器
    draw_rounded_box(ax, (0.38, 0.58), comp_w, comp_h,
                     "Budget Reservation\nManager", C_COMP_BG, C_GW_BD, fontsize=8)
    # 会话跟踪器
    draw_rounded_box(ax, (0.68, 0.58), comp_w, comp_h,
                     "Session Tracker\n(Sunk-Cost)", C_COMP_BG, C_GW_BD, fontsize=8)

    # 治理引擎 - 中央决策
    draw_rounded_box(ax, (0.20, 0.42), 0.60, 0.10,
                     "Admission Decision Engine    P_eff = P_base × I(t) / (1 + K²α)",
                     C_COMP_BG, C_GW_BD, fontsize=7.5, linewidth=2.0)

    # 强度跟踪器
    draw_rounded_box(ax, (0.08, 0.37), 0.24, 0.07,
                     "Intensity Tracker\n(EMA / Hysteresis)", "#F0F0F0", "#7F8C8D",
                     fontsize=7, fontweight="normal")

    # ═══════════════════════════════════════════════════════════
    # Layer 3: MCP Backend (底层)
    # ═══════════════════════════════════════════════════════════
    be_box = FancyBboxPatch(
        (0.05, 0.02), 0.90, 0.25,
        boxstyle="round,pad=0.015",
        facecolor=C_BACKEND_BG, edgecolor=C_BACKEND_BD,
        linewidth=2.0, zorder=1
    )
    ax.add_patch(be_box)
    ax.text(0.50, 0.24, "MCP Backend Server  (Python, JSON-RPC 2.0)",
            ha="center", va="center",
            fontsize=11, fontweight="bold", color=C_BACKEND_BD, zorder=3)

    # Tools
    tool_names = ["calculator", "web_search", "llm_reasoner", "text_fmt", "weather"]
    tool_w = 0.14
    start_x = 0.10
    for i, name in enumerate(tool_names):
        x = start_x + i * (tool_w + 0.04)
        draw_rounded_box(ax, (x, 0.06), tool_w, 0.10,
                         name, C_COMP_BG, C_BACKEND_BD,
                         fontsize=6.5, fontweight="normal")

    # ═══════════════════════════════════════════════════════════
    # 箭头: Agent → Gateway
    # ═══════════════════════════════════════════════════════════
    # ReAct Agent → Gateway
    draw_arrow(ax, (0.22, 0.84), (0.35, 0.75),
               "X-Session-ID\ntools/call", C_AGENT_BD,
               fontsize=6.5, label_offset=(-0.10, 0.0))
    # P&S Agent → Gateway
    draw_arrow(ax, (0.52, 0.84), (0.50, 0.75),
               "X-Plan-DAG\nX-Total-Budget", C_AGENT_BD,
               fontsize=6.5, label_offset=(0.12, 0.0))

    # ═══════════════════════════════════════════════════════════
    # 箭头: Gateway → Backend (forward)
    # ═══════════════════════════════════════════════════════════
    draw_arrow(ax, (0.40, 0.42), (0.40, 0.27),
               "forward", C_GW_BD,
               fontsize=7.5, label_offset=(-0.09, 0.0))

    # 箭头: Gateway → Agent (reject)
    draw_arrow(ax, (0.85, 0.52), (0.92, 0.83),
               "reject\n(-32001)", C_REJECT,
               fontsize=7, label_offset=(0.04, 0.0),
               connectionstyle="arc3,rad=-0.2")

    # ═══════════════════════════════════════════════════════════
    # 箭头: Backend → Gateway (response + signals)
    # ═══════════════════════════════════════════════════════════
    draw_arrow(ax, (0.60, 0.27), (0.60, 0.42),
               "response + signals\n(429, latency, RateLimit)", C_BACKEND_BD,
               fontsize=6.5, label_offset=(0.15, 0.0))

    # ═══════════════════════════════════════════════════════════
    # 保存
    # ═══════════════════════════════════════════════════════════
    os.makedirs(OUT_DIR, exist_ok=True)
    png_path = os.path.join(OUT_DIR, "architecture.png")
    pdf_path = os.path.join(OUT_DIR, "architecture.pdf")
    fig.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    print(f"[OK] {png_path}")
    print(f"[OK] {pdf_path}")
    plt.close(fig)


if __name__ == "__main__":
    main()
