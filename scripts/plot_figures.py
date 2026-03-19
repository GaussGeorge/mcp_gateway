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
C_STATUS = {"success": "#28B463", "rejected": "#8E44AD", "error": "#E74C3C"}

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
        throughput=success / duration if duration else 0,
        goodput=goodput,
        goodput_rate=goodput / duration if duration else 0,
        p50=p50, p95=p95,
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
    mk = ["throughput", "p95", "goodput_rate"]
    rl = ["Throughput (req/s)", "P95 Latency (ms)", "Goodput (w-req/s)"]

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

            if k == "p95":
                row_t.append(f"{m:.0f} ± {s:.0f}{star}")
            else:
                row_t.append(f"{m:.1f} ± {s:.1f}{star}")

            row_c.append("#D5F5E3" if gw == "dp" else "#FDFEFE")
        cell_txt.append(row_t)
        cell_clr.append(row_c)

    fig, ax = plt.subplots(figsize=(7.5, 2.8))
    ax.axis("off")

    tbl = ax.table(
        cellText=cell_txt, rowLabels=rl, colLabels=gw_lbl,
        cellColours=cell_clr,
        rowColours=["#EAF2F8"] * 3,
        colColours=[C_GW[l] + "55" for l in gw_lbl],
        loc="center", cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(11)
    tbl.scale(1.2, 1.8)
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
            ax2.annotate(s, (hrs[i], mx + sx + 0.3),
                         ha="center", fontsize=13, fontweight="bold")

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
        fontsize=12, y=1.02)
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
        _bracket(ax, xlo, xhi, ytop, 2, s)

    # bracket: DP-High vs NG-High
    p2 = _ttest(rates["dp"]["100"], rates["ng"]["100"])
    s2 = _star(p2)
    if s2:
        xng = x[1] - w
        xdp = x[1] + w
        y2 = max(bar_info["ng"][0][1], bar_info["dp"][0][1]) + \
             max(bar_info["ng"][1][1], bar_info["dp"][1][1]) + 10
        _bracket(ax, xng, xdp, y2, 2, s2)

    ax.set_xlabel("Budget Group")
    ax.set_ylabel("Success Rate (%)")
    ax.set_title(
        "Figure 3: Budget Fairness — Success Rate by Budget Group (Exp3)\n"
        "* p < 0.05  ** p < 0.01  *** p < 0.001")
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
        ax.bar(x, h, w, bottom=bottoms, label=tl,
               color=C_TOOL[t], edgecolor="white", lw=0.5)
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
        _bracket(ax, 0, 2, ymax, 0.4, s)

    ax.set_xlabel("Gateway Strategy")
    ax.set_ylabel("Goodput (weighted req/s)")
    ax.set_title(
        "Figure 4: Goodput Contribution by Tool Type (Exp3)\n"
        "* p < 0.05  ** p < 0.01  *** p < 0.001")
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
        ax.set_title(f"({chr(97 + idx)}) {gl}")
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
#  Figure 6 — Ablation Bar Chart  (Exp4)
# ====================================================================
def fig6():
    print("  Fig 6: Ablation Experiment …")
    data = _load_groups(RESULTS_DIR / "exp4_ablation")

    gws = ["dp", "dp-noregime", "srl"]
    gw_lbl = ["DP-Full", "DP-NoRegime", "SRL"]
    colors = [C_GW["DP"], C_GW["DP-NoRegime"], C_GW["SRL"]]

    fig, ax = plt.subplots(figsize=(6, 5))
    x = np.arange(len(gws))
    w = 0.5

    all_v = {}
    means, stds = [], []
    for gw in gws:
        runs = data.get(("exp4", gw), [])
        v = [m["goodput_rate"] for m in runs]
        all_v[gw] = v
        m, s = _ms(v)
        means.append(m); stds.append(s)

    ax.bar(x, means, w, yerr=stds, capsize=5,
           color=colors, edgecolor="white", lw=0.5)

    for i, (m, s) in enumerate(zip(means, stds)):
        ax.text(i, m + s + 0.3, f"{m:.1f}", ha="center", va="bottom", fontsize=10)

    # bracket DP-Full vs SRL
    p1 = _ttest(all_v["dp"], all_v["srl"])
    s1 = _star(p1)
    if s1:
        y1 = max(means[0] + stds[0], means[2] + stds[2]) + 3
        _bracket(ax, 0, 2, y1, 0.4, s1)

    # bracket DP-Full vs DP-NoRegime
    p2 = _ttest(all_v["dp"], all_v["dp-noregime"])
    s2 = _star(p2)
    if s2:
        y2 = max(means[0] + stds[0], means[1] + stds[1]) + 2
        _bracket(ax, 0, 1, y2, 0.4, s2)
    else:
        # not significant → show n.s.
        y2 = max(means[0] + stds[0], means[1] + stds[1]) + 2
        _bracket(ax, 0, 1, y2, 0.4, f"n.s.\n(p={p2:.2f})")

    ax.set_xlabel("Gateway Strategy")
    ax.set_ylabel("Goodput (weighted req/s)")
    ax.set_title(
        "Figure 6: Ablation — Adaptive Regime Impact (Exp4)\n"
        "* p < 0.05  ** p < 0.01  *** p < 0.001")
    ax.set_xticks(x)
    ax.set_xticklabels(gw_lbl)
    ax.set_ylim(0, max(means) * 1.35)
    fig.tight_layout()
    fig.savefig(PDF_DIR / "fig6_ablation.pdf")
    fig.savefig(PNG_DIR / "fig6_ablation.png")
    plt.close(fig)
    print("    → fig6_ablation.pdf / .png")


# ====================================================================
#  main
# ====================================================================
def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  Phase 4: Generating 6 publication-quality figures (PDF + PNG)")
    print("=" * 60)

    fig1()
    fig2()
    fig3()
    fig4()
    fig5()
    fig6()

    n_pdf = len(list(PDF_DIR.glob("*.pdf")))
    n_png = len(list(PNG_DIR.glob("*.png")))
    print(f"\n{'=' * 60}")
    print(f"  ALL DONE — {n_pdf} PDFs in {PDF_DIR}")
    print(f"             {n_png} PNGs in {PNG_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
