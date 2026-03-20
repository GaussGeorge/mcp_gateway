"""
plot_figures.py — Phase 4: 生成论文6张核心图表 (PDF矢量图)
============================================================
用法: python scripts/plot_figures.py
输出: figures/ 目录下的 6 个 PDF 文件

图表自包含规范:
  1. 完整图题 (Title)
  2. 坐标轴含单位
  3. 清晰图例 (Legend)
  4. yerr 误差棒 (3 次均值 ± 标准差)
  5. 简易 t-test 显著性标星 (* p<0.05, ** p<0.01, *** p<0.001)
"""

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from scipy import stats as sp_stats

# ==========  paths & weights  ==========
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
FIGURES_DIR = Path(__file__).resolve().parent.parent / "figures"
PDF_DIR = FIGURES_DIR / "PDF"
PNG_DIR = FIGURES_DIR / "PNG"
TOOL_WEIGHTS = {"mock_heavy": 5, "calculate": 1, "web_fetch": 1}

# ==========  colour palette  ==========
C_GW = {"NG": "#D64541", "SRL": "#2E86C1", "DP": "#28B463",
         "DP-NoRegime": "#E67E22"}
C_TOOL = {"calculate": "#5DADE2", "web_fetch": "#F4D03F",
           "mock_heavy": "#E74C3C"}
C_STATUS = {"success": "#4CAF50", "rejected": "#FF9800", "error": "#F44336"}

# ==========  matplotlib global style  ==========
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 13,
    "legend.fontsize": 10,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.15,
    "axes.grid": True,
    "grid.alpha": 0.25,
})


# ====================================================================
#  Data helpers
# ====================================================================
def _load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _run_metrics(rows):
    """Return a metrics dict from one CSV run."""
    total = len(rows)
    success = sum(1 for r in rows if r["status"] == "success")
    rejected = sum(1 for r in rows if r["status"] == "rejected")
    error = total - success - rejected

    ts = [float(r["timestamp"]) for r in rows]
    duration = max(ts) - min(ts) if len(ts) > 1 else 60.0

    lats = sorted(float(r["latency_ms"]) for r in rows if r["status"] == "success")
    p50 = lats[len(lats) // 2] if lats else 0.0
    p95 = lats[int(len(lats) * 0.95)] if lats else 0.0
    p999 = lats[int(len(lats) * 0.999)] if len(lats) > 1 else p95

    goodput = sum(TOOL_WEIGHTS.get(r.get("tool_name", ""), 1)
                  for r in rows if r["status"] == "success")

    # per-tool goodput (absolute, not rate)
    tg = defaultdict(float)
    for r in rows:
        if r["status"] == "success":
            tg[r.get("tool_name", "unknown")] += TOOL_WEIGHTS.get(
                r.get("tool_name", ""), 1)

    # per-budget stats
    bs = defaultdict(lambda: {"success": 0, "rejected": 0, "error": 0, "total": 0})
    for r in rows:
        b = r.get("budget", "?")
        bs[b]["total"] += 1
        bs[b][r["status"]] += 1

    return dict(
        total=total, success=success, rejected=rejected, error=error,
        duration=duration,
        success_pct=100 * success / total if total else 0,
        rejected_pct=100 * rejected / total if total else 0,
        error_pct=100 * error / total if total else 0,
        throughput=success / duration if duration else 0,
        goodput=goodput,
        goodput_rate=goodput / duration if duration else 0,
        p50=p50, p95=p95, p999=p999,
        tool_goodput={k: v / duration for k, v in tg.items()},
        budget_stats=dict(bs),
    )


def _load_groups(exp_dir):
    """Return {(prefix, gateway): [metrics, ...], ...}."""
    groups = defaultdict(list)
    for f in sorted(exp_dir.glob("*.csv")):
        parts = f.stem.split("_")
        run_idx = next((i for i, p in enumerate(parts) if p.startswith("run")), None)
        if run_idx is None:
            continue
        gw = parts[run_idx - 1]
        prefix = "_".join(parts[:run_idx - 1])
        groups[(prefix, gw)].append(_run_metrics(_load_csv(f)))
    return dict(groups)


# ====================================================================
#  Stat helpers
# ====================================================================
def _ms(vals):
    """mean, std (ddof=1)."""
    a = np.asarray(vals, dtype=float)
    return float(np.mean(a)), float(np.std(a, ddof=1)) if len(a) > 1 else 0.0


def _ttest(a, b):
    """Welch's t-test → p-value."""
    if len(a) < 2 or len(b) < 2:
        return 1.0
    return float(sp_stats.ttest_ind(a, b, equal_var=False).pvalue)


def _star(p):
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def _sig_label(p):
    """Return descriptive significance label with p-value."""
    if p < 0.001:
        return f"p < 0.001"
    if p < 0.01:
        return f"p = {p:.3f}"
    if p < 0.05:
        return f"p = {p:.2f}"
    return f"n.s. (p = {p:.2f})"


def _bracket(ax, x1, x2, y, dy, text, fs=10):
    """Significance bracket."""
    ax.plot([x1, x1, x2, x2], [y, y + dy, y + dy, y],
            lw=1.0, color="k", clip_on=False)
    ax.text((x1 + x2) / 2, y + dy, text,
            ha="center", va="bottom", fontsize=fs)


# ====================================================================
#  Figure 1 — Core Metrics Summary Table  (Exp1)
# ====================================================================
def fig1():
    print("  Fig 1: Core Metrics Summary Table …")
    data = _load_groups(RESULTS_DIR / "exp1_step_pulse")

    gws = ["ng", "srl", "dp"]
    gw_lbl = ["NG", "SRL", "DP"]
    mk = ["throughput", "p95", "goodput_rate", "rejected_pct", "error_pct", "p999"]
    rl = ["Throughput (req/s)", "P95 Latency (ms)", "Goodput (w-req/s)",
          "Rejection Rate (%)", "Error Rate (%)", "P999 Latency (ms)"]

    vals = {}
    for gw in gws:
        runs = data.get(("exp1", gw), [])
        vals[gw] = {k: [m[k] for m in runs] for k in mk}

    cell_txt, cell_clr = [], []
    for k, label in zip(mk, rl):
        row_t, row_c = [], []
        for gw, gl in zip(gws, gw_lbl):
            m, s = _ms(vals[gw][k])
            star = ""
            if gw == "dp":
                p_ng = _ttest(vals["dp"][k], vals["ng"][k])
                p_srl = _ttest(vals["dp"][k], vals["srl"][k])
                star = _star(min(p_ng, p_srl))
                if star:
                    star = " " + star

            if k in ("p95", "p999"):
                row_t.append(f"{m:.0f} ± {s:.0f}{star}")
            elif k in ("rejected_pct", "error_pct"):
                row_t.append(f"{m:.1f} ± {s:.1f}{star}")
            else:
                row_t.append(f"{m:.1f} ± {s:.1f}{star}")

            row_c.append("#D5F5E3" if gw == "dp" else "#FDFEFE")
        cell_txt.append(row_t)
        cell_clr.append(row_c)

    fig, ax = plt.subplots(figsize=(9.5, 4.5))
    ax.axis("off")

    nrows = len(rl)
    tbl = ax.table(
        cellText=cell_txt, rowLabels=rl, colLabels=gw_lbl,
        cellColours=cell_clr,
        rowColours=["#EAF2F8"] * nrows,
        colColours=[C_GW[l] + "55" for l in gw_lbl],
        loc="center", cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1.2, 1.7)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#CCCCCC")

    ax.set_title(
        "Table 1: Core Performance Metrics — Step-Pulse Workload (Exp1)\n"
        "* p < 0.05  ** p < 0.01  *** p < 0.001  (DP vs. baselines, Welch t-test)",
        fontsize=11, pad=12)
    fig.savefig(PDF_DIR / "fig1_core_metrics_table.pdf")
    fig.savefig(PNG_DIR / "fig1_core_metrics_table.png")
    plt.close(fig)
    print("    → fig1_core_metrics_table.pdf / .png")


# ====================================================================
#  Figure 2 — Heavy-Ratio Sensitivity  (Exp2)
# ====================================================================
def fig2():
    print("  Fig 2: Heavy Ratio Sensitivity …")
    data = _load_groups(RESULTS_DIR / "exp2_heavy_ratio")

    hrs = [10, 30, 50]
    prefixes = [f"exp2_hr{h}" for h in hrs]
    gws = ["ng", "srl", "dp"]
    gw_lbl = ["NG", "SRL", "DP"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.5))

    for gw, gl in zip(gws, gw_lbl):
        t_m, t_s, g_m, g_s = [], [], [], []
        for pf in prefixes:
            runs = data.get((pf, gw), [])
            tm, ts = _ms([r["throughput"] for r in runs])
            gm, gs = _ms([r["goodput_rate"] for r in runs])
            t_m.append(tm); t_s.append(ts)
            g_m.append(gm); g_s.append(gs)

        c = C_GW[gl]
        ax1.errorbar(hrs, t_m, yerr=t_s, marker="o", capsize=4,
                     label=gl, color=c, lw=2)
        ax2.errorbar(hrs, g_m, yerr=g_s, marker="s", capsize=4,
                     label=gl, color=c, lw=2)

    # significance annotations on Goodput panel
    for i, pf in enumerate(prefixes):
        dp_v = [r["goodput_rate"] for r in data.get((pf, "dp"), [])]
        ng_v = [r["goodput_rate"] for r in data.get((pf, "ng"), [])]
        srl_v = [r["goodput_rate"] for r in data.get((pf, "srl"), [])]
        p = min(_ttest(dp_v, ng_v), _ttest(dp_v, srl_v))
        s = _star(p)
        if s:
            mx, sx = _ms(dp_v)
            ax2.annotate(_sig_label(p), (hrs[i], mx + sx + 0.3),
                         ha="center", fontsize=9, fontweight="bold")

    for ax, title, ylabel in [
        (ax1, "(a) Throughput", "Throughput (req/s)"),
        (ax2, "(b) Goodput", "Goodput (weighted req/s)"),
    ]:
        ax.set_xlabel("Heavy Ratio (%)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_xticks(hrs)
        ax.legend()

    fig.suptitle(
        "Figure 2: Impact of Heavy-Request Ratio on Gateway Performance (Exp2)",
        fontsize=12, y=1.04)
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig2_heavy_ratio_sensitivity.pdf")
    fig.savefig(PNG_DIR / "fig2_heavy_ratio_sensitivity.png")
    plt.close(fig)
    print("    → fig2_heavy_ratio_sensitivity.pdf / .png")


# ====================================================================
#  Figure 3 — Budget-Group Pass-Rate Bar Chart  (Exp3)
# ====================================================================
def fig3():
    print("  Fig 3: Budget Group Pass Rate …")
    exp_dir = RESULTS_DIR / "exp3_budget_fairness"
    gws = ["ng", "srl", "dp"]
    gw_lbl = ["NG", "SRL", "DP"]
    budgets = ["10", "100"]
    bud_lbl = ["Low Budget (10)", "High Budget (100)"]

    # per-run pass rate by budget
    rates = {gw: {b: [] for b in budgets} for gw in gws}
    for gw in gws:
        for ri in range(1, 4):
            fp = exp_dir / f"exp3_{gw}_run{ri}.csv"
            if not fp.exists():
                continue
            rows = _load_csv(fp)
            for b in budgets:
                br = [r for r in rows if r.get("budget", "") == b]
                if br:
                    rates[gw][b].append(
                        100 * sum(1 for r in br if r["status"] == "success") / len(br))

    fig, ax = plt.subplots(figsize=(7, 5))
    x = np.arange(len(budgets))
    w = 0.22

    bar_info = {}
    for i, (gw, gl) in enumerate(zip(gws, gw_lbl)):
        ms = [_ms(rates[gw][b]) for b in budgets]
        means = [m for m, _ in ms]
        stds = [s for _, s in ms]
        bars = ax.bar(x + (i - 1) * w, means, w, yerr=stds, capsize=4,
                      label=gl, color=C_GW[gl], edgecolor="white", lw=0.5)
        bar_info[gw] = (means, stds, bars)

    # bracket: DP Low vs DP High
    dp_lo, dp_hi = rates["dp"]["10"], rates["dp"]["100"]
    p = _ttest(dp_hi, dp_lo)
    s = _star(p)
    if s:
        xlo = x[0] + w   # DP bar for budget=10
        xhi = x[1] + w   # DP bar for budget=100
        ytop = bar_info["dp"][0][1] + bar_info["dp"][1][1] + 4
        _bracket(ax, xlo, xhi, ytop, 2, _sig_label(p))

    # bracket: DP-High vs NG-High
    p2 = _ttest(rates["dp"]["100"], rates["ng"]["100"])
    s2 = _star(p2)
    if s2:
        xng = x[1] - w
        xdp = x[1] + w
        y2 = max(bar_info["ng"][0][1], bar_info["dp"][0][1]) + \
             max(bar_info["ng"][1][1], bar_info["dp"][1][1]) + 10
        _bracket(ax, xng, xdp, y2, 2, _sig_label(p2))

    ax.set_xlabel("Budget Group")
    ax.set_ylabel("Success Rate (%)")
    ax.set_title(
        "Figure 3: Budget Fairness — Success Rate by Budget Group (Exp3)")
    ax.set_xticks(x)
    ax.set_xticklabels(bud_lbl)
    ax.set_ylim(0, 115)
    ax.legend()
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig3_budget_fairness.pdf")
    fig.savefig(PNG_DIR / "fig3_budget_fairness.png")
    plt.close(fig)
    print("    → fig3_budget_fairness.pdf / .png")


# ====================================================================
#  Figure 4 — Goodput Breakdown Stacked Bar  (Exp3)
# ====================================================================
def fig4():
    print("  Fig 4: Goodput Breakdown …")
    data = _load_groups(RESULTS_DIR / "exp3_budget_fairness")

    gws = ["ng", "srl", "dp"]
    gw_lbl = ["NG", "SRL", "DP"]
    tools = ["calculate", "web_fetch", "mock_heavy"]
    tool_lbl = ["Calculate", "Web Fetch", "Mock Heavy (×5)"]

    fig, ax = plt.subplots(figsize=(6, 5))
    x = np.arange(len(gws))
    w = 0.5

    # per-tool goodput rate (mean across runs)
    t_means = {t: [] for t in tools}
    for gw in gws:
        runs = data.get(("exp3", gw), [])
        for t in tools:
            vals = [m["tool_goodput"].get(t, 0) for m in runs]
            t_means[t].append(_ms(vals)[0])

    bottoms = np.zeros(len(gws))
    for t, tl in zip(tools, tool_lbl):
        h = np.array(t_means[t])
        bars = ax.bar(x, h, w, bottom=bottoms, label=tl,
                      color=C_TOOL[t], edgecolor="white", lw=0.5)
        # 在堆叠块内标注占比
        for j, (bi, hi) in enumerate(zip(bottoms, h)):
            total_gw = sum(t_means[tt][j] for tt in tools)
            if total_gw > 0 and hi / total_gw > 0.08:  # 只标注>8%的块
                pct = 100 * hi / total_gw
                ax.text(j, bi + hi / 2, f"{pct:.0f}%",
                        ha="center", va="center", fontsize=8,
                        color="white", fontweight="bold")
        bottoms += h

    # total goodput error bar
    tot_m, tot_s = [], []
    all_gp = {}
    for gw in gws:
        runs = data.get(("exp3", gw), [])
        v = [m["goodput_rate"] for m in runs]
        all_gp[gw] = v
        m, s = _ms(v)
        tot_m.append(m); tot_s.append(s)
    ax.errorbar(x, tot_m, yerr=tot_s, fmt="none", capsize=5, color="k", lw=1.5)

    for i, (m, s) in enumerate(zip(tot_m, tot_s)):
        ax.text(i, m + s + 0.3, f"{m:.1f}", ha="center", va="bottom", fontsize=10)

    # significance DP vs NG
    p = _ttest(all_gp["dp"], all_gp["ng"])
    s = _star(p)
    if s:
        ymax = max(tot_m[0] + tot_s[0], tot_m[2] + tot_s[2]) + 3
        _bracket(ax, 0, 2, ymax, 0.4, _sig_label(p))

    ax.set_xlabel("Gateway Strategy")
    ax.set_ylabel("Goodput (weighted req/s)")
    ax.set_title(
        "Figure 4: Goodput Contribution by Tool Type (Exp3)")
    ax.set_xticks(x)
    ax.set_xticklabels(gw_lbl)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig4_goodput_breakdown.pdf")
    fig.savefig(PNG_DIR / "fig4_goodput_breakdown.png")
    plt.close(fig)
    print("    → fig4_goodput_breakdown.pdf / .png")


# ====================================================================
#  Figure 5 — Step-Surge Stacked Area  (Exp1)
# ====================================================================
def fig5():
    print("  Fig 5: Step Surge Area Chart …")
    exp_dir = RESULTS_DIR / "exp1_step_pulse"
    gws = ["ng", "srl", "dp"]
    gw_lbl = ["NG", "SRL", "DP"]

    BIN = 2  # seconds

    fig, axes = plt.subplots(1, 3, figsize=(14, 4), sharey=True)

    for ax, gw, gl in zip(axes, gws, gw_lbl):
        # average across 3 runs
        all_s, all_r, all_e = [], [], []
        for ri in range(1, 4):
            fp = exp_dir / f"exp1_{gw}_run{ri}.csv"
            rows = _load_csv(fp)
            ts = [float(r["timestamp"]) for r in rows]
            t0 = min(ts)
            rel = [t - t0 for t in ts]
            mx = max(rel)
            n_bins = int(mx / BIN) + 1
            sc = np.zeros(n_bins); rc = np.zeros(n_bins); ec = np.zeros(n_bins)
            for r, rt in zip(rows, rel):
                bi = min(int(rt / BIN), n_bins - 1)
                if r["status"] == "success":
                    sc[bi] += 1
                elif r["status"] == "rejected":
                    rc[bi] += 1
                else:
                    ec[bi] += 1
            all_s.append(sc / BIN)
            all_r.append(rc / BIN)
            all_e.append(ec / BIN)

        # pad to same length
        max_len = max(len(a) for a in all_s)
        def _pad(lst):
            return [np.pad(a, (0, max_len - len(a))) for a in lst]
        all_s, all_r, all_e = _pad(all_s), _pad(all_r), _pad(all_e)

        ms = np.mean(all_s, axis=0)
        mr = np.mean(all_r, axis=0)
        me = np.mean(all_e, axis=0)
        centers = np.arange(max_len) * BIN + BIN / 2

        ax.stackplot(centers, ms, mr, me,
                     labels=["Success", "Rejected", "Error"],
                     colors=[C_STATUS["success"], C_STATUS["rejected"],
                             C_STATUS["error"]],
                     alpha=0.85)
        idx = gws.index(gw)

        # 计算子标题核心指标
        tot = ms + mr + me
        tot_sum = np.sum(tot)
        s_pct = 100 * np.sum(ms) / tot_sum if tot_sum > 0 else 0
        e_pct = 100 * np.sum(me) / tot_sum if tot_sum > 0 else 0
        r_pct = 100 * np.sum(mr) / tot_sum if tot_sum > 0 else 0
        ax.set_title(f"({chr(97 + idx)}) {gl}\n"
                     f"Succ={s_pct:.0f}%  Rej={r_pct:.0f}%  Err={e_pct:.0f}%",
                     fontsize=10)

        # 添加 Step 脉冲触发时间垂直虚线
        for t_line in [10, 25, 35, 45]:
            ax.axvline(t_line, color="gray", ls="--", lw=0.8, alpha=0.6)

        ax.set_xlabel("Time (s)")
        ax.set_xlim(0, 65)
        if idx == 0:
            ax.set_ylabel("Requests / sec")

    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3,
              bbox_to_anchor=(0.5, 1.03), frameon=False)
    fig.suptitle(
        "Figure 5: Request-Status Distribution Over Time — Step-Pulse (Exp1, mean of 3 runs)",
        fontsize=12, y=1.10)
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig5_step_surge_area.pdf")
    fig.savefig(PNG_DIR / "fig5_step_surge_area.png")
    plt.close(fig)
    print("    → fig5_step_surge_area.pdf / .png")


# ====================================================================
#  Figure 6 — Ablation 2×2 Grid  (Exp4)
#  (a) Error Rate  (b) SLA Compliance  (c) Convergence Time  (d) Goodput
#  指标在 20-80s 核心过载窗口内计算 (突发+震荡期)
# ====================================================================
_OVERLOAD_T0 = 20.0   # 过载窗口起始 (秒)
_OVERLOAD_T1 = 80.0   # 过载窗口结束 (秒)

def _run_metrics_windowed(rows, t_start, t_end):
    """在指定时间窗口内计算指标 (秒, 相对于实验起始)。"""
    ts = [float(r["timestamp"]) for r in rows]
    t0 = min(ts)
    filtered = [r for r, t in zip(rows, ts)
                if t_start <= (t - t0) < t_end]
    if not filtered:
        return None
    total = len(filtered)
    success = sum(1 for r in filtered if r["status"] == "success")
    rejected = sum(1 for r in filtered if r["status"] == "rejected")
    error = total - success - rejected
    lats = sorted(float(r["latency_ms"]) for r in filtered
                  if r["status"] == "success")
    goodput = sum(TOOL_WEIGHTS.get(r.get("tool_name", ""), 1)
                  for r in filtered if r["status"] == "success")
    dur = t_end - t_start
    sla_ok = sum(1 for r in filtered if float(r["latency_ms"]) < 2000)
    return dict(
        total=total, success=success, rejected=rejected, error=error,
        success_pct=100 * success / total if total else 0,
        rejected_pct=100 * rejected / total if total else 0,
        error_pct=100 * error / total if total else 0,
        goodput_rate=goodput / dur if dur else 0,
        sla_pct=100 * sla_ok / total if total else 0,
    )

def fig6():
    print("  Fig 6: Ablation Experiment (4-panel, overload window 20-80s) …")
    exp_dir = RESULTS_DIR / "exp4_ablation"

    gws = ["dp", "dp-noregime", "srl"]
    gw_lbl = ["DP-Full", "DP-NoRegime", "SRL"]
    colors = [C_GW["DP"], C_GW["DP-NoRegime"], C_GW["SRL"]]

    # 自动检测 run 数量
    n_runs = len(list(exp_dir.glob("exp4_dp_run*.csv")))
    if n_runs == 0:
        print("    [SKIP] no data")
        return

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    ax1, ax2, ax3, ax4 = axes.flat
    x = np.arange(len(gws))
    w = 0.5

    # 收集窗口内指标
    windowed = {}   # gw -> [metrics_dict, ...]
    for gw in gws:
        mlist = []
        for ri in range(1, n_runs + 1):
            fp = exp_dir / f"exp4_{gw}_run{ri}.csv"
            if not fp.exists():
                continue
            rows = _load_csv(fp)
            m = _run_metrics_windowed(rows, _OVERLOAD_T0, _OVERLOAD_T1)
            if m:
                mlist.append(m)
        windowed[gw] = mlist

    def _draw_bar(ax, key, ylabel, title, fmt="{:.1f}%", suffix="%",
                  ylim_factor=1.4, bracket_dy=1):
        all_v = {}
        means, stds = [], []
        for gw in gws:
            v = [m[key] for m in windowed[gw]]
            all_v[gw] = v
            m_, s_ = _ms(v)
            means.append(m_); stds.append(s_)
        ax.bar(x, means, w, yerr=stds, capsize=5,
               color=colors, edgecolor="white", lw=0.5)
        for i, (m_, s_) in enumerate(zip(means, stds)):
            txt = fmt.format(m_) + (f"\n(±{s_:.1f})" if s_ > 0.01 else "")
            ax.text(i, m_ + s_ + 0.3, txt,
                    ha="center", va="bottom", fontsize=9)
        # DP vs DP-NoRegime
        p_ab = _ttest(all_v["dp"], all_v["dp-noregime"])
        y_ab = max(means[0] + stds[0], means[1] + stds[1]) + 2.5
        _bracket(ax, 0, 1, y_ab, bracket_dy, _sig_label(p_ab))
        # DP vs SRL
        p_ac = _ttest(all_v["dp"], all_v["srl"])
        y_ac = max(means[0] + stds[0], means[2] + stds[2]) + 5
        _bracket(ax, 0, 2, y_ac, bracket_dy, _sig_label(p_ac))

        ax.set_xlabel("Gateway Strategy")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_xticks(x); ax.set_xticklabels(gw_lbl, fontsize=9)
        mx = max(means) if means else 1
        ax.set_ylim(0, max(mx * ylim_factor, mx + 15))

    # (a) Error Rate — 核心优势指标，放最前面
    _draw_bar(ax1, "error_pct", "Error Rate (%)",
              "(a) Error Rate (20-80s)", fmt="{:.1f}")

    # (b) SLA Compliance
    _draw_bar(ax2, "sla_pct", "SLA Compliance (%)",
              "(b) SLA Compliance (Latency < 2s, 20-80s)", fmt="{:.1f}")

    # (c) Convergence Time
    all_conv = {}
    conv_means, conv_stds = [], []
    BIN_CT = 1.0
    BURST_START = 20.0
    BURST_END = 45.0
    ERR_THRESH = 5.0

    for gw in gws:
        conv_vals = []
        for ri in range(1, n_runs + 1):
            fp = exp_dir / f"exp4_{gw}_run{ri}.csv"
            if not fp.exists():
                continue
            rows = _load_csv(fp)
            ts = [float(r["timestamp"]) for r in rows]
            t0 = min(ts)
            conv_time = BURST_END - BURST_START  # default: never recovers
            n_bins = int((BURST_END - BURST_START) / BIN_CT)
            for bi in range(n_bins):
                bstart = BURST_START + bi * BIN_CT
                bend = bstart + BIN_CT
                bin_rows = [r["status"] for r in rows
                            if bstart <= (float(r["timestamp"]) - t0) < bend]
                if not bin_rows:
                    continue
                err_pct = 100 * sum(1 for s in bin_rows if s == "error") / len(bin_rows)
                if err_pct < ERR_THRESH:
                    conv_time = bi * BIN_CT + BIN_CT
                    break
            conv_vals.append(conv_time)
        all_conv[gw] = conv_vals
        m, s = _ms(conv_vals)
        conv_means.append(m); conv_stds.append(s)

    ax3.bar(x, conv_means, w, yerr=conv_stds, capsize=5,
            color=colors, edgecolor="white", lw=0.5)
    for i, (m, s) in enumerate(zip(conv_means, conv_stds)):
        ax3.text(i, m + s + 0.3, f"{m:.1f}s\n(±{s:.1f})",
                 ha="center", va="bottom", fontsize=9)
    p_conv = _ttest(all_conv["dp"], all_conv["dp-noregime"])
    y_cv = max(conv_means[0] + conv_stds[0], conv_means[1] + conv_stds[1]) + 1
    _bracket(ax3, 0, 1, y_cv, 0.3, _sig_label(p_conv))
    p_conv2 = _ttest(all_conv["dp"], all_conv["srl"])
    y_cv2 = max(conv_means[0] + conv_stds[0], conv_means[2] + conv_stds[2]) + 3
    _bracket(ax3, 0, 2, y_cv2, 0.3, _sig_label(p_conv2))
    ax3.set_xlabel("Gateway Strategy")
    ax3.set_ylabel("Convergence Time (s)")
    ax3.set_title("(c) Burst Convergence (Error < 5%)")
    ax3.set_xticks(x); ax3.set_xticklabels(gw_lbl, fontsize=9)
    ax3.set_ylim(0, max(conv_means) * 1.4 if max(conv_means) > 0 else 30)

    # (d) Goodput — 放最后（弱化权重）
    _draw_bar(ax4, "goodput_rate", "Goodput (weighted req/s)",
              "(d) Goodput (20-80s)", fmt="{:.1f}", suffix="")

    fig.suptitle(
        f"Figure 6: Ablation — Overload Window [20-80s] (Exp4, N={n_runs})",
        fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig6_ablation.pdf")
    fig.savefig(PNG_DIR / "fig6_ablation.png")
    plt.close(fig)
    print("    → fig6_ablation.pdf / .png")


# ====================================================================
#  Figure 7 — Composite Workload Timeline (Exp4)
# ====================================================================
# Composite 阶段边界 (秒)
_COMPOSITE_BOUNDARIES = [0, 20, 45, 80, 100, 120]
_COMPOSITE_LABELS = ["Steady\nQPS=30", "Burst\nQPS=120",
                     "Sine\n30~90", "Idle\nQPS=10", "Square\n100/10"]

def fig7():
    print("  Fig 7: Composite Workload Timeline …")
    exp_dir = RESULTS_DIR / "exp4_ablation"
    gws = ["dp", "dp-noregime", "srl"]
    gw_lbl = ["DP-Full", "DP-NoRegime", "SRL"]

    # 自动检测 run 数量
    n_runs = len(list(exp_dir.glob("exp4_dp_run*.csv")))
    if n_runs == 0:
        print("    [SKIP] no data")
        return

    BIN = 2  # seconds

    # 阶段背景色 (淡色)
    _PHASE_COLORS = ["#E8F8F5", "#FDEDEC", "#FEF9E7", "#EBF5FB", "#F5EEF8"]

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5), sharey=True)

    for ax, gw, gl in zip(axes, gws, gw_lbl):
        all_s, all_r, all_e = [], [], []
        for ri in range(1, n_runs + 1):
            fp = exp_dir / f"exp4_{gw}_run{ri}.csv"
            if not fp.exists():
                continue
            rows = _load_csv(fp)
            ts = [float(r["timestamp"]) for r in rows]
            t0 = min(ts)
            rel = [t - t0 for t in ts]
            mx = max(rel)
            n_bins = int(mx / BIN) + 1
            sc = np.zeros(n_bins); rc = np.zeros(n_bins); ec = np.zeros(n_bins)
            for r, rt in zip(rows, rel):
                bi = min(int(rt / BIN), n_bins - 1)
                if r["status"] == "success":
                    sc[bi] += 1
                elif r["status"] == "rejected":
                    rc[bi] += 1
                else:
                    ec[bi] += 1
            all_s.append(sc / BIN)
            all_r.append(rc / BIN)
            all_e.append(ec / BIN)

        if not all_s:
            continue

        max_len = max(len(a) for a in all_s)
        def _pad(lst):
            return [np.pad(a, (0, max_len - len(a))) for a in lst]
        all_s, all_r, all_e = _pad(all_s), _pad(all_r), _pad(all_e)

        ms = np.mean(all_s, axis=0)
        mr = np.mean(all_r, axis=0)
        me = np.mean(all_e, axis=0)
        centers = np.arange(max_len) * BIN + BIN / 2

        # 阶段背景色带
        for pi in range(len(_COMPOSITE_LABELS)):
            ax.axvspan(_COMPOSITE_BOUNDARIES[pi], _COMPOSITE_BOUNDARIES[pi + 1],
                       alpha=0.3, color=_PHASE_COLORS[pi], zorder=0)

        ax.stackplot(centers, ms, mr, me,
                     labels=["Success", "Rejected", "Error"],
                     colors=[C_STATUS["success"], C_STATUS["rejected"],
                             C_STATUS["error"]],
                     alpha=0.85)
        idx = gws.index(gw)

        # 计算全局指标
        tot = ms + mr + me
        tot_sum = np.sum(tot)
        s_pct = 100 * np.sum(ms) / tot_sum if tot_sum > 0 else 0
        r_pct = 100 * np.sum(mr) / tot_sum if tot_sum > 0 else 0
        e_pct = 100 * np.sum(me) / tot_sum if tot_sum > 0 else 0
        ax.set_title(f"({chr(97 + idx)}) {gl}\n"
                     f"Succ={s_pct:.0f}%  Rej={r_pct:.0f}%  Err={e_pct:.0f}%",
                     fontsize=10, fontweight="bold")

        # 阶段分界线
        for tb in _COMPOSITE_BOUNDARIES[1:-1]:
            ax.axvline(tb, color="gray", ls="--", lw=0.8, alpha=0.6)

        # 所有子图都标注阶段名
        for i in range(len(_COMPOSITE_LABELS)):
            mid = (_COMPOSITE_BOUNDARIES[i] + _COMPOSITE_BOUNDARIES[i+1]) / 2
            ax.text(mid, -0.12, _COMPOSITE_LABELS[i],
                    ha="center", va="top", fontsize=7,
                    transform=ax.get_xaxis_transform(),
                    color="#555555")

        ax.set_xlabel("Time (s)")
        ax.set_xlim(0, 125)
        if idx == 0:
            ax.set_ylabel("Requests / sec")

    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3,
              bbox_to_anchor=(0.5, 1.03), frameon=False)
    fig.suptitle(
        f"Figure 7: Composite Workload Timeline — Ablation (Exp4, mean of {n_runs} runs)",
        fontsize=12, y=1.10)
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig7_composite_timeline.pdf")
    fig.savefig(PNG_DIR / "fig7_composite_timeline.png")
    plt.close(fig)
    print("    → fig7_composite_timeline.pdf / .png")


# ====================================================================
#  Figure 8 — Regime Switching Timeline (Exp4, DP-Full only)
# ====================================================================
_REGIME_ORDER = {"steady": 0, "bursty": 1, "periodic": 2}
_REGIME_COLORS = {"steady": "#2E86C1", "bursty": "#E74C3C", "periodic": "#F39C12"}

def fig8():
    print("  Fig 8: Regime Switching Timeline …")
    exp_dir = RESULTS_DIR / "exp4_ablation"
    BIN = 1.0  # seconds

    # 自动检测 run 数量
    n_runs = len(list(exp_dir.glob("exp4_dp_run*.csv")))
    if n_runs == 0:
        print("    [SKIP] no data")
        return

    # 阶段背景色 (淡色) — 与 fig7 一致
    _PHASE_COLORS = ["#E8F8F5", "#FDEDEC", "#FEF9E7", "#EBF5FB", "#F5EEF8"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True,
                                    gridspec_kw={"height_ratios": [1, 1.2]})

    # --- Panel (a): Regime state for DP-Full ---
    # Collect regime data across runs (use majority vote per bin)
    all_regimes = []
    for ri in range(1, n_runs + 1):
        fp = exp_dir / f"exp4_dp_run{ri}.csv"
        if not fp.exists():
            continue
        rows = _load_csv(fp)
        ts = [float(r["timestamp"]) for r in rows]
        t0 = min(ts)
        mx = max(ts) - t0
        n_bins = int(mx / BIN) + 1
        bins = [[] for _ in range(n_bins)]
        for r in rows:
            rt = float(r["timestamp"]) - t0
            bi = min(int(rt / BIN), n_bins - 1)
            regime = r.get("regime", "steady") or "steady"
            bins[bi].append(regime)
        # majority regime per bin
        regime_ts = []
        for b in bins:
            if not b:
                regime_ts.append("steady")
            else:
                counts = {}
                for rg in b:
                    counts[rg] = counts.get(rg, 0) + 1
                regime_ts.append(max(counts, key=counts.get))
        all_regimes.append(regime_ts)

    if all_regimes:
        max_len = max(len(a) for a in all_regimes)
        # pad to same length
        for i in range(len(all_regimes)):
            while len(all_regimes[i]) < max_len:
                all_regimes[i].append("steady")

        # majority vote across runs
        final_regime = []
        for bi in range(max_len):
            counts = {}
            for run_r in all_regimes:
                rg = run_r[bi]
                counts[rg] = counts.get(rg, 0) + 1
            final_regime.append(max(counts, key=counts.get))

        centers = np.arange(max_len) * BIN + BIN / 2
        regime_y = np.array([_REGIME_ORDER.get(r, 0) for r in final_regime])

        # 阶段背景色带 — 与 fig7 对齐
        for pi in range(len(_COMPOSITE_LABELS)):
            ax1.axvspan(_COMPOSITE_BOUNDARIES[pi], _COMPOSITE_BOUNDARIES[pi + 1],
                        alpha=0.25, color=_PHASE_COLORS[pi], zorder=0)

        # Color background bands by regime
        for i in range(len(final_regime)):
            color = _REGIME_COLORS.get(final_regime[i], "#999")
            ax1.axvspan(i * BIN, (i + 1) * BIN, alpha=0.3, color=color, lw=0)

        # Step line
        ax1.step(centers, regime_y, where="mid", color="black", lw=1.5)
        ax1.set_yticks([0, 1, 2])
        ax1.set_yticklabels(["Steady", "Bursty", "Periodic"])
        ax1.set_ylabel("Active Regime")
        ax1.set_title("(a) DP-Full Regime Switching")

        # Phase boundaries
        for tb in _COMPOSITE_BOUNDARIES[1:-1]:
            ax1.axvline(tb, color="gray", ls="--", lw=0.8, alpha=0.6)
        # Phase labels — 与 fig7 对齐，所有子图都标注
        for i in range(len(_COMPOSITE_LABELS)):
            mid = (_COMPOSITE_BOUNDARIES[i] + _COMPOSITE_BOUNDARIES[i + 1]) / 2
            ax1.text(mid, 2.3, _COMPOSITE_LABELS[i].replace("\n", " "),
                     ha="center", va="bottom", fontsize=7, color="#555")
        ax1.set_ylim(-0.3, 2.8)

        # Legend patches
        from matplotlib.patches import Patch
        patches = [Patch(facecolor=_REGIME_COLORS[k], alpha=0.5, label=k.capitalize())
                   for k in ["steady", "bursty", "periodic"]]
        ax1.legend(handles=patches, loc="upper right", fontsize=8, ncol=3)

    # --- Panel (b): Rolling error rate comparison with 95% CI ---
    # 阶段背景色带
    for pi in range(len(_COMPOSITE_LABELS)):
        ax2.axvspan(_COMPOSITE_BOUNDARIES[pi], _COMPOSITE_BOUNDARIES[pi + 1],
                    alpha=0.20, color=_PHASE_COLORS[pi], zorder=0)

    for gw, gl, color, ls in [("dp", "DP-Full", C_GW["DP"], "-"),
                               ("dp-noregime", "DP-NoRegime", C_GW["DP-NoRegime"], "--")]:
        all_err_ts = []
        n_gw_runs = len(list(exp_dir.glob(f"exp4_{gw}_run*.csv")))
        for ri in range(1, n_gw_runs + 1):
            fp = exp_dir / f"exp4_{gw}_run{ri}.csv"
            if not fp.exists():
                continue
            rows = _load_csv(fp)
            ts = [float(r["timestamp"]) for r in rows]
            t0 = min(ts)
            mx = max(ts) - t0
            n_bins = int(mx / BIN) + 1
            err_cnt = np.zeros(n_bins)
            tot_cnt = np.zeros(n_bins)
            for r in rows:
                rt = float(r["timestamp"]) - t0
                bi = min(int(rt / BIN), n_bins - 1)
                tot_cnt[bi] += 1
                if r["status"] == "error":
                    err_cnt[bi] += 1
            err_pct = np.where(tot_cnt > 0, 100 * err_cnt / tot_cnt, 0)
            all_err_ts.append(err_pct)

        if not all_err_ts:
            continue
        max_len = max(len(a) for a in all_err_ts)
        all_err_ts = [np.pad(a, (0, max_len - len(a))) for a in all_err_ts]
        arr = np.array(all_err_ts)
        mean_err = np.mean(arr, axis=0)
        centers = np.arange(max_len) * BIN + BIN / 2

        # 95% CI shading
        if arr.shape[0] >= 2:
            se = np.std(arr, axis=0, ddof=1) / np.sqrt(arr.shape[0])
            ci95 = 1.96 * se
            ax2.fill_between(centers, mean_err - ci95, mean_err + ci95,
                             alpha=0.18, color=color, lw=0)

        ax2.plot(centers, mean_err, color=color, ls=ls, lw=1.8, label=gl)

    # Phase boundaries on error chart
    for tb in _COMPOSITE_BOUNDARIES[1:-1]:
        ax2.axvline(tb, color="gray", ls="--", lw=0.8, alpha=0.6)
    ax2.set_xlabel("Time (s)")
    ax2.set_ylabel("Error Rate (%)")
    ax2.set_title("(b) Rolling Error Rate: DP-Full vs DP-NoRegime (95% CI)")
    ax2.legend(loc="upper right", fontsize=9)
    ax2.set_xlim(0, 125)
    ax2.set_ylim(0, None)

    fig.suptitle(
        f"Figure 8: Regime Switching & Error Rate — Ablation (Exp4, mean of {n_runs} runs)",
        fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig8_regime_timeline.pdf")
    fig.savefig(PNG_DIR / "fig8_regime_timeline.png")
    plt.close(fig)
    print("    → fig8_regime_timeline.pdf / .png")


# ====================================================================
#  main
# ====================================================================
def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  Phase 4: Generating 8 publication-quality figures (PDF + PNG)")
    print("=" * 60)

    fig1()
    fig2()
    fig3()
    fig4()
    fig5()
    fig6()
    fig7()
    fig8()

    n_pdf = len(list(PDF_DIR.glob("*.pdf")))
    n_png = len(list(PNG_DIR.glob("*.png")))
    print(f"\n{'=' * 60}")
    print(f"  ALL DONE — {n_pdf} PDFs in {PDF_DIR}")
    print(f"             {n_png} PNGs in {PNG_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
