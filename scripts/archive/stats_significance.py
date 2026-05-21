#!/usr/bin/env python3
"""
stats_significance.py — Week 2 结果统计显著性检验
=================================================
读取 week2_smoke_summary.csv，计算:
  1. 均值 ± 标准差
  2. 95% Bootstrap 置信区间
  3. Permutation test (PlanGate vs each baseline)
  4. 输出 LaTeX 论文主表

用法:
  python scripts/stats_significance.py
  python scripts/stats_significance.py --csv results/exp_week2_smoke/week2_smoke_summary.csv
"""

import argparse
import csv
import os
import sys
import random
from collections import defaultdict
from typing import Dict, List, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DEFAULT_CSV = os.path.join(ROOT_DIR, "results", "exp_week2_smoke", "week2_smoke_summary.csv")
OUTPUT_DIR = os.path.join(ROOT_DIR, "results", "paper_figures")


def load_csv(path: str) -> Dict[str, List[dict]]:
    """按 gateway 分组加载 CSV"""
    data = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"]
            data[gw].append({
                "run": int(row["run"]),
                "success": int(row["success"]),
                "cascade_failed": int(row["cascade_failed"]),
                "rejected_s0": int(row["rejected_s0"]),
                "success_rate": float(row["success_rate"]),
                "abd_total": float(row["abd_total"]),
                "abd_ps": float(row["abd_ps"]),
                "abd_react": float(row["abd_react"]),
                "goodput": float(row["goodput"]),
            })
    return dict(data)


def mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0


def stdev(vals: List[float]) -> float:
    if len(vals) < 2:
        return 0
    m = mean(vals)
    return (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5


def bootstrap_ci(vals: List[float], n_boot: int = 10000, alpha: float = 0.05) -> Tuple[float, float]:
    """Bootstrap 95% 置信区间"""
    if len(vals) < 2:
        m = mean(vals)
        return (m, m)
    rng = random.Random(42)
    boot_means = []
    n = len(vals)
    for _ in range(n_boot):
        sample = [rng.choice(vals) for _ in range(n)]
        boot_means.append(mean(sample))
    boot_means.sort()
    lo = boot_means[int(n_boot * alpha / 2)]
    hi = boot_means[int(n_boot * (1 - alpha / 2))]
    return (lo, hi)


def permutation_test(a: List[float], b: List[float], n_perm: int = 10000) -> float:
    """Two-sided permutation test, returns p-value.
    Tests whether mean(a) != mean(b)."""
    if not a or not b:
        return 1.0
    observed = abs(mean(a) - mean(b))
    combined = a + b
    na = len(a)
    rng = random.Random(42)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(combined)
        perm_diff = abs(mean(combined[:na]) - mean(combined[na:]))
        if perm_diff >= observed:
            count += 1
    return count / n_perm


def compute_all_stats(data: Dict[str, List[dict]]):
    """计算所有指标的统计量"""
    metrics = ["abd_total", "abd_ps", "abd_react", "goodput", "success_rate"]
    results = {}

    for gw, runs in data.items():
        gw_stats = {}
        for metric in metrics:
            vals = [r[metric] for r in runs]
            m = mean(vals)
            s = stdev(vals)
            ci_lo, ci_hi = bootstrap_ci(vals)
            gw_stats[metric] = {
                "mean": m,
                "std": s,
                "ci_lo": ci_lo,
                "ci_hi": ci_hi,
                "values": vals,
            }
        results[gw] = gw_stats

    return results


def print_summary(results: Dict, data: Dict):
    """打印汇总表"""
    gw_order = ["ng", "rajomon", "rajomon_sb", "sbac", "pp", "pg_nores", "plangate_full"]
    metrics = ["abd_total", "abd_ps", "abd_react", "goodput", "success_rate"]

    print(f"\n{'='*110}")
    print("  统计显著性分析 — Week 2 Results")
    print(f"{'='*110}")

    # Header
    print(f"{'Gateway':<18}", end="")
    for m in metrics:
        print(f"  {m:>18}", end="")
    print()
    print(f"{'-'*18}", end="")
    for _ in metrics:
        print(f"  {'-'*18}", end="")
    print()

    # Data rows
    for gw in gw_order:
        if gw not in results:
            continue
        print(f"{gw:<18}", end="")
        for m in metrics:
            s = results[gw][m]
            print(f"  {s['mean']:>6.1f}±{s['std']:<4.1f} [{s['ci_lo']:.1f},{s['ci_hi']:.1f}]", end="")
        print()

    # Permutation tests vs plangate_full
    if "plangate_full" not in results:
        return

    pg = results["plangate_full"]
    print(f"\n{'='*110}")
    print("  Permutation Test (two-sided) — PlanGate vs Baselines")
    print(f"{'='*110}")
    print(f"{'Baseline':<18}  {'ABD_total p':>12}  {'ABD_P&S p':>12}  {'ABD_ReAct p':>12}  {'GP/s p':>12}  {'SuccRate p':>12}")
    print(f"{'-'*18}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}")

    for gw in gw_order:
        if gw == "plangate_full" or gw not in results:
            continue
        p_abd = permutation_test(results[gw]["abd_total"]["values"], pg["abd_total"]["values"])
        p_abd_ps = permutation_test(results[gw]["abd_ps"]["values"], pg["abd_ps"]["values"])
        p_abd_react = permutation_test(results[gw]["abd_react"]["values"], pg["abd_react"]["values"])
        p_gp = permutation_test(results[gw]["goodput"]["values"], pg["goodput"]["values"])
        p_sr = permutation_test(results[gw]["success_rate"]["values"], pg["success_rate"]["values"])

        def sig(p):
            if p < 0.001:
                return f"{p:.4f} ***"
            elif p < 0.01:
                return f"{p:.4f} **"
            elif p < 0.05:
                return f"{p:.4f} *"
            else:
                return f"{p:.4f}"

        print(f"{gw:<18}  {sig(p_abd):>12}  {sig(p_abd_ps):>12}  {sig(p_abd_react):>12}  {sig(p_gp):>12}  {sig(p_sr):>12}")

    print(f"\n  Significance: *** p<0.001, ** p<0.01, * p<0.05")


def gen_latex_table(results: Dict):
    """生成 LaTeX 论文主表 — Mode-Stratified Commitment Quality"""
    gw_order = ["ng", "rajomon", "rajomon_sb", "sbac", "pp", "pg_nores", "plangate_full"]
    gw_labels = {
        "ng": "NG (No Gov.)",
        "rajomon": "Rajomon",
        "rajomon_sb": "Rajomon+SB",
        "sbac": "SBAC",
        "pp": "Prog.Priority",
        "pg_nores": "PG-noRes",
        "plangate_full": "\\textbf{PlanGate}",
    }

    lines = []
    lines.append("% Table 1: Mode-Stratified Commitment Quality")
    lines.append("\\begin{table}[t]")
    lines.append("\\centering")
    lines.append("\\caption{Mode-stratified commitment quality across 7 gateways (200 sessions $\\times$ 5 repeats). ABD\\textsubscript{P\\&S}=0.0\\% for PlanGate confirms hard commitment via budget reservation; ABD\\textsubscript{ReAct}=29.1\\% confirms continuation-aware soft commitment.}")
    lines.append("\\label{tab:commitment-quality}")
    lines.append("\\small")
    lines.append("\\begin{tabular}{l r r r r r}")
    lines.append("\\toprule")
    lines.append("Gateway & Succ.\\% & ABD\\textsubscript{total}\\% & ABD\\textsubscript{P\\&S}\\% & ABD\\textsubscript{ReAct}\\% & GP/s \\\\")
    lines.append("\\midrule")

    for gw in gw_order:
        if gw not in results:
            continue
        r = results[gw]
        label = gw_labels.get(gw, gw)
        sr  = r["success_rate"]
        abd = r["abd_total"]
        abd_ps = r["abd_ps"]
        abd_re = r["abd_react"]
        gp  = r["goodput"]

        if gw == "plangate_full":
            line = f"{label} & \\textbf{{{sr['mean']:.1f}$\\pm${sr['std']:.1f}}} & \\textbf{{{abd['mean']:.1f}$\\pm${abd['std']:.1f}}} & \\textbf{{{abd_ps['mean']:.1f}}} & \\textbf{{{abd_re['mean']:.1f}}} & \\textbf{{{gp['mean']:.1f}$\\pm${gp['std']:.1f}}} \\\\"
        else:
            line = f"{label} & {sr['mean']:.1f}$\\pm${sr['std']:.1f} & {abd['mean']:.1f}$\\pm${abd['std']:.1f} & {abd_ps['mean']:.1f} & {abd_re['mean']:.1f} & {gp['mean']:.1f}$\\pm${gp['std']:.1f} \\\\"
        lines.append(line)

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Statistical significance analysis for Week 2 results")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Path to week2_smoke_summary.csv")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"错误: 找不到 CSV 文件: {args.csv}", file=sys.stderr)
        sys.exit(1)

    data = load_csv(args.csv)
    results = compute_all_stats(data)

    print_summary(results, data)

    # 输出 LaTeX 表
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    latex = gen_latex_table(results)
    latex_path = os.path.join(OUTPUT_DIR, "table_commitment_quality.tex")
    with open(latex_path, "w", encoding="utf-8") as f:
        f.write(latex)
    print(f"\n  LaTeX 主表已写入: {latex_path}")

    # 也写一份带 CI 的详细版
    detail_path = os.path.join(OUTPUT_DIR, "stats_detail.txt")
    with open(detail_path, "w", encoding="utf-8") as f:
        gw_order = ["ng", "rajomon", "rajomon_sb", "sbac", "pp", "pg_nores", "plangate_full"]
        for gw in gw_order:
            if gw not in results:
                continue
            f.write(f"\n=== {gw} ===\n")
            for metric in ["abd_total", "abd_ps", "abd_react", "goodput", "success_rate"]:
                s = results[gw][metric]
                f.write(f"  {metric}: {s['mean']:.2f} ± {s['std']:.2f}  95% CI [{s['ci_lo']:.2f}, {s['ci_hi']:.2f}]\n")
    print(f"  详细统计已写入: {detail_path}")


if __name__ == "__main__":
    main()
