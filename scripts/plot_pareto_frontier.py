#!/usr/bin/env python3
"""
plot_pareto_frontier.py — 绘制 Pareto 前沿图表

读取 tables/pareto_frontier_summary.csv（由 analyze_pareto_frontier.py 生成），
输出 3 张学术级 PDF 图（+PNG 副本）:

  1. plots/pareto_frontier/success_vs_waste.pdf
     X: useful_completion_rate（成功率），Y: waste_per_success（每成功任务浪费调用数）
     → 证明 PlanGate 在提高成功率的同时，每笔成功的浪费更少

  2. plots/pareto_frontier/rej0_vs_cascade.pdf
     X: admission_rate（准入率），Y: cascade_failed（级联失败数）
     → 证明拒绝并非 PlanGate 的唯一策略；减少级联失败是独立贡献

  3. plots/pareto_frontier/goodput_vs_latency.pdf
     X: effective_goodput（有效吞吐），Y: p95_ms（P95 延迟）
     → 经典吞吐-延迟权衡曲线，PlanGate 占据右下角

用法:
  python scripts/plot_pareto_frontier.py
  python scripts/plot_pareto_frontier.py --input tables/pareto_frontier_summary.csv
  python scripts/plot_pareto_frontier.py --no-pdf   # 仅输出 PNG
"""

import argparse
import csv
import os
import sys
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
TABLES_DIR = os.path.join(ROOT_DIR, "tables")
PLOTS_DIR = os.path.join(ROOT_DIR, "plots", "pareto_frontier")


def _float(val, default: float = float("nan")) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def load_summary(csv_path: str) -> List[dict]:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ====== 配色方案 ======
PALETTE = {
    "ng":       ("#e74c3c", "o", "NG (No Gov)"),        # 红色
    "sbac":     ("#e67e22", "s", "SBAC"),                # 橙色
    "rajomon":  ("#f39c12", "^", "Rajomon"),             # 黄橙
    "plangate": ("#2196F3", "D", "PlanGate"),            # 蓝色族
}
# PlanGate 变体配色：按 max_sessions 深浅区分
PG_CMAP_NAME = "Blues"
PARETO_EDGE_COLOR = "#1565C0"

FONT_SIZE = 11
TITLE_SIZE = 12
LEGEND_SIZE = 9
LINE_WIDTH = 1.8
MARKER_SIZE = 8
FIG_SIZE = (6, 4.5)
DPI = 300


def setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": FONT_SIZE,
        "axes.titlesize": TITLE_SIZE,
        "axes.labelsize": FONT_SIZE,
        "legend.fontsize": LEGEND_SIZE,
        "xtick.labelsize": FONT_SIZE - 1,
        "ytick.labelsize": FONT_SIZE - 1,
        "figure.dpi": DPI,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    return plt, ticker


def save_fig(plt, name: str, output_dir: str, no_pdf: bool = False):
    os.makedirs(output_dir, exist_ok=True)
    png_path = os.path.join(output_dir, f"{name}.png")
    plt.savefig(png_path, dpi=DPI, bbox_inches="tight")
    print(f"  [PNG] {png_path}")
    if not no_pdf:
        pdf_path = os.path.join(output_dir, f"{name}.pdf")
        plt.savefig(pdf_path, bbox_inches="tight")
        print(f"  [PDF] {pdf_path}")
    plt.close()


def categorize_rows(rows: List[dict]):
    """把行分为 baselines 和 PlanGate 变体两组"""
    baselines = [r for r in rows if r.get("policy") in ("ng", "sbac", "rajomon")]
    pg_variants = [r for r in rows if r.get("policy") == "plangate"]
    return baselines, pg_variants


def pg_color_by_ms(ms_val, cmap, norm):
    """根据 max_sessions 映射颜色"""
    return cmap(norm(float(ms_val) if ms_val else 30))


# ====== 图 1: success_vs_waste ======

def plot_success_vs_waste(rows: List[dict], output_dir: str, no_pdf: bool = False):
    plt, ticker = setup_matplotlib()
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as mplt

    baselines, pg_variants = categorize_rows(rows)
    fig, ax = mplt.subplots(figsize=FIG_SIZE)

    # PlanGate 变体 — 按 max_sessions 颜色映射
    ms_vals = [float(r["max_sessions"]) for r in pg_variants if r.get("max_sessions")]
    ms_vals_all = [20, 30, 40, 60, 80]
    cmap = mplt.get_cmap("Blues")
    norm = mcolors.Normalize(vmin=min(ms_vals_all) - 10, vmax=max(ms_vals_all) + 10)

    # 找出 Pareto 前沿点（简单 2D: ucr↑, wps↓ 即 low waste 好）
    pareto_pts = []
    for r in pg_variants:
        ucr = _float(r.get("useful_completion_rate"))
        wps = _float(r.get("waste_per_success"))
        dominated = False
        for other in pg_variants:
            if other is r:
                continue
            o_ucr = _float(other.get("useful_completion_rate"))
            o_wps = _float(other.get("waste_per_success"))
            if o_ucr >= ucr and o_wps <= wps and (o_ucr > ucr or o_wps < wps):
                dominated = True
                break
        if not dominated:
            pareto_pts.append(r)

    # 绘制 PlanGate 变体
    for r in pg_variants:
        x = _float(r.get("useful_completion_rate"))
        y = _float(r.get("waste_per_success"))
        ms = float(r.get("max_sessions") or 30)
        color = pg_color_by_ms(ms, cmap, norm)
        is_pareto = r in pareto_pts
        ec = PARETO_EDGE_COLOR if is_pareto else "grey"
        lw = 2.0 if is_pareto else 0.8
        ax.scatter(x, y, color=color, edgecolors=ec, linewidths=lw,
                   marker="D", s=MARKER_SIZE**2, zorder=4)
        # 标注 max_sessions 数值
        ax.annotate(f"ms={int(ms)}", (x, y),
                    textcoords="offset points", xytext=(4, 4),
                    fontsize=7, color="#333333", alpha=0.8)

    # 连接 Pareto 前沿线
    if len(pareto_pts) >= 2:
        pareto_sorted = sorted(pareto_pts, key=lambda r: _float(r.get("useful_completion_rate")))
        px = [_float(r.get("useful_completion_rate")) for r in pareto_sorted]
        py = [_float(r.get("waste_per_success")) for r in pareto_sorted]
        ax.plot(px, py, "--", color=PARETO_EDGE_COLOR, linewidth=LINE_WIDTH,
                alpha=0.7, label="Pareto front (PlanGate)", zorder=3)

    # 绘制基线
    baseline_markers = {"ng": ("o", "#e74c3c"), "sbac": ("s", "#e67e22"), "rajomon": ("^", "#f39c12")}
    for r in baselines:
        x = _float(r.get("useful_completion_rate"))
        y = _float(r.get("waste_per_success"))
        policy = r.get("policy", "")
        mk, color = baseline_markers.get(policy, ("x", "grey"))
        ax.scatter(x, y, color=color, edgecolors="black", linewidths=1.2,
                   marker=mk, s=(MARKER_SIZE + 2)**2, zorder=5)
        ax.annotate(PALETTE.get(policy, (None, None, policy))[2], (x, y),
                    textcoords="offset points", xytext=(5, -10),
                    fontsize=8, fontweight="bold", color=color)

    # 色条（max_sessions 示意）
    sm = mplt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, pad=0.02, shrink=0.8)
    cb.set_label("max_sessions", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    ax.set_xlabel("Useful Completion Rate (success / sessions)", fontsize=FONT_SIZE)
    ax.set_ylabel("Waste per Success\n(cascade_failed / success)", fontsize=FONT_SIZE)
    ax.set_title("PlanGate Tradeoff: Success Rate vs. Wasted Work", fontsize=TITLE_SIZE)
    ax.legend(fontsize=LEGEND_SIZE - 1, loc="upper right")

    save_fig(mplt, "success_vs_waste", output_dir, no_pdf)


# ====== 图 2: rej0_vs_cascade ======

def plot_rej0_vs_cascade(rows: List[dict], output_dir: str, no_pdf: bool = False):
    plt, ticker = setup_matplotlib()
    import matplotlib.pyplot as mplt

    baselines, pg_variants = categorize_rows(rows)
    fig, ax = mplt.subplots(figsize=FIG_SIZE)

    sessions = 200  # 默认；后续可从数据推断
    import matplotlib.colors as mcolors
    cmap = mplt.get_cmap("Blues")
    norm = mcolors.Normalize(vmin=10, vmax=90)

    for r in pg_variants:
        rej = _float(r.get("rejected_s0"))
        casc = _float(r.get("cascade_failed"))
        admit = sessions - rej  # admitted sessions
        ms = float(r.get("max_sessions") or 30)
        alpha = float(r.get("alpha") or 0.5)
        color = cmap(norm(ms))
        ax.scatter(admit, casc, color=color, edgecolors=PARETO_EDGE_COLOR,
                   linewidths=0.8, marker="D", s=MARKER_SIZE**2, zorder=4, alpha=0.9)
        ax.annotate(f"ms={int(ms)}", (admit, casc),
                    textcoords="offset points", xytext=(4, 3),
                    fontsize=6.5, color="#333333", alpha=0.8)

    baseline_markers = {"ng": ("o", "#e74c3c"), "sbac": ("s", "#e67e22"), "rajomon": ("^", "#f39c12")}
    for r in baselines:
        rej = _float(r.get("rejected_s0"))
        casc = _float(r.get("cascade_failed"))
        admit = sessions - rej
        policy = r.get("policy", "")
        mk, color = baseline_markers.get(policy, ("x", "grey"))
        ax.scatter(admit, casc, color=color, edgecolors="black", linewidths=1.2,
                   marker=mk, s=(MARKER_SIZE + 2)**2, zorder=5)
        ax.annotate(PALETTE.get(policy, (None, None, policy))[2], (admit, casc),
                    textcoords="offset points", xytext=(5, -10),
                    fontsize=8, fontweight="bold", color=color)

    ax.set_xlabel("Admitted Sessions (sessions - rejected@S0)", fontsize=FONT_SIZE)
    ax.set_ylabel("Cascade Failed Sessions", fontsize=FONT_SIZE)
    ax.set_title("Admission vs. Cascade Failures\n(PlanGate ≠ early reject only)", fontsize=TITLE_SIZE)

    save_fig(mplt, "rej0_vs_cascade", output_dir, no_pdf)


# ====== 图 3: goodput_vs_latency ======

def plot_goodput_vs_latency(rows: List[dict], output_dir: str, no_pdf: bool = False):
    plt, ticker = setup_matplotlib()
    import matplotlib.pyplot as mplt
    import matplotlib.colors as mcolors

    baselines, pg_variants = categorize_rows(rows)
    fig, ax = mplt.subplots(figsize=FIG_SIZE)

    cmap = mplt.get_cmap("Blues")
    norm = mcolors.Normalize(vmin=10, vmax=90)

    for r in pg_variants:
        x = _float(r.get("effective_goodput"))
        y = _float(r.get("p95_ms"))
        ms = float(r.get("max_sessions") or 30)
        color = cmap(norm(ms))
        ax.scatter(x, y, color=color, edgecolors=PARETO_EDGE_COLOR,
                   linewidths=0.8, marker="D", s=MARKER_SIZE**2, zorder=4, alpha=0.9)
        ax.annotate(f"ms={int(ms)}", (x, y),
                    textcoords="offset points", xytext=(4, 3),
                    fontsize=6.5, color="#333333", alpha=0.8)

    baseline_markers = {"ng": ("o", "#e74c3c"), "sbac": ("s", "#e67e22"), "rajomon": ("^", "#f39c12")}
    for r in baselines:
        x = _float(r.get("effective_goodput"))
        y = _float(r.get("p95_ms"))
        policy = r.get("policy", "")
        mk, color = baseline_markers.get(policy, ("x", "grey"))
        ax.scatter(x, y, color=color, edgecolors="black", linewidths=1.2,
                   marker=mk, s=(MARKER_SIZE + 2)**2, zorder=5)
        label = PALETTE.get(policy, (None, None, policy))[2]
        ax.annotate(label, (x, y),
                    textcoords="offset points", xytext=(5, -10),
                    fontsize=8, fontweight="bold", color=color)

    # 色条
    sm = mplt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, pad=0.02, shrink=0.8)
    cb.set_label("max_sessions", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    ax.set_xlabel("Effective Goodput (successful full-chain sessions)", fontsize=FONT_SIZE)
    ax.set_ylabel("P95 Latency (ms)", fontsize=FONT_SIZE)
    ax.set_title("Throughput-Latency Tradeoff\n(lower-right = better)", fontsize=TITLE_SIZE)

    save_fig(mplt, "goodput_vs_latency", output_dir, no_pdf)


# ====== 图 4: goodput_vs_abd ======

def plot_goodput_vs_abd(rows: List[dict], output_dir: str, no_pdf: bool = False):
    """
    Effective Goodput (x) vs ABD-like cascade rate (y).
    ABD-like = cascade_failed / total_sessions (proxy; NOT the formal ABD metric).
    Lower-right corner is better (high goodput, low cascade exposure).
    """
    plt, ticker = setup_matplotlib()
    import matplotlib.pyplot as mplt
    import matplotlib.colors as mcolors

    baselines, pg_variants = categorize_rows(rows)
    fig, ax = mplt.subplots(figsize=FIG_SIZE)

    cmap = mplt.get_cmap("Blues")
    norm = mcolors.Normalize(vmin=10, vmax=90)

    TOTAL_SESSIONS = 200  # pilot default; used only if abd_like not in CSV

    def _abd(r: dict) -> float:
        """Return ABD-like from pre-computed field or derive from raw counts."""
        v = _float(r.get("abd_like"))
        if not (v != v):  # not NaN
            return v
        cascade = _float(r.get("cascade_failed"))
        return cascade / TOTAL_SESSIONS

    for r in pg_variants:
        x = _float(r.get("effective_goodput"))
        y = _abd(r)
        ms = float(r.get("max_sessions") or 30)
        color = cmap(norm(ms))
        ax.scatter(x, y, color=color, edgecolors=PARETO_EDGE_COLOR,
                   linewidths=0.8, marker="D", s=MARKER_SIZE**2, zorder=4, alpha=0.9)
        ax.annotate(f"ms={int(ms)}", (x, y),
                    textcoords="offset points", xytext=(4, 3),
                    fontsize=6.5, color="#333333", alpha=0.8)

    baseline_markers = {"ng": ("o", "#e74c3c"), "sbac": ("s", "#e67e22"), "rajomon": ("^", "#f39c12")}
    for r in baselines:
        x = _float(r.get("effective_goodput"))
        y = _abd(r)
        policy = r.get("policy", "")
        mk, color = baseline_markers.get(policy, ("x", "grey"))
        ax.scatter(x, y, color=color, edgecolors="black", linewidths=1.2,
                   marker=mk, s=(MARKER_SIZE + 2)**2, zorder=5)
        blabel = PALETTE.get(policy, (None, None, policy))[2]
        ax.annotate(blabel, (x, y),
                    textcoords="offset points", xytext=(5, -10),
                    fontsize=8, fontweight="bold", color=color)

    # 色条
    sm = mplt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, pad=0.02, shrink=0.8)
    cb.set_label("max_sessions", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    ax.set_xlabel("Effective Goodput (successful full-chain sessions)", fontsize=FONT_SIZE)
    ax.set_ylabel("ABD-like cascade rate\n(cascade_failed / total_sessions)", fontsize=FONT_SIZE)
    ax.set_title("Goodput vs ABD-like Cascade Rate\n(lower-right = better; ABD-like ≠ formal ABD)", fontsize=TITLE_SIZE)
    # Annotate the ideal direction
    ax.annotate("← lower cascade\n    higher goodput →",
                xy=(0.98, 0.02), xycoords="axes fraction",
                ha="right", va="bottom", fontsize=7, color="grey",
                style="italic")

    save_fig(mplt, "goodput_vs_abd", output_dir, no_pdf)


# ====== 主函数 ======

def main():
    parser = argparse.ArgumentParser(
        description="绘制 Pareto 前沿图表（来自 pareto_frontier_summary.csv）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", type=str, default=None,
                        help="pareto_frontier_summary.csv 路径 (default: tables/pareto_frontier_summary.csv)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="图表输出目录 (default: plots/pareto_frontier/)")
    parser.add_argument("--no-pdf", action="store_true",
                        help="只输出 PNG，不生成 PDF")
    args = parser.parse_args()

    # 默认路径
    input_path = args.input or os.path.join(TABLES_DIR, "pareto_frontier_summary.csv")
    output_dir = args.output_dir or PLOTS_DIR

    if not os.path.exists(input_path):
        print(f"[ERROR] 未找到 {input_path}")
        print("  请先运行 analyze_pareto_frontier.py 生成汇总 CSV")
        sys.exit(1)

    # 检查 matplotlib 是否可用
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("[ERROR] 未安装 matplotlib，请运行: pip install matplotlib")
        sys.exit(1)

    print(f"\n  读取: {input_path}")
    rows = load_summary(input_path)
    if not rows:
        print("[ERROR] CSV 为空")
        sys.exit(1)

    print(f"  共 {len(rows)} 个变体，输出目录: {output_dir}\n")

    plot_success_vs_waste(rows, output_dir, args.no_pdf)
    plot_rej0_vs_cascade(rows, output_dir, args.no_pdf)
    plot_goodput_vs_latency(rows, output_dir, args.no_pdf)
    plot_goodput_vs_abd(rows, output_dir, args.no_pdf)

    print(f"\n  全部图表已保存至: {output_dir}")


if __name__ == "__main__":
    main()
