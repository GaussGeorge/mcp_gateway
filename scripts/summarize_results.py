"""
summarize_results.py — 实验结果汇总与质量检查
==============================================
用法: python scripts/summarize_results.py
"""

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
import statistics

RESULTS_DIR = Path(__file__).parent.parent / "results"
TOOL_WEIGHTS = {"mock_heavy": 5, "calculate": 1, "web_fetch": 1}


def load_csv(filepath):
    """加载 CSV 并返回行列表。"""
    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def compute_metrics(rows):
    """从一组 CSV 行计算关键指标。"""
    total = len(rows)
    success = sum(1 for r in rows if r["status"] == "success")
    rejected = sum(1 for r in rows if r["status"] == "rejected")
    error = sum(1 for r in rows if r["status"] == "error")

    latencies = [float(r["latency_ms"]) for r in rows if r["status"] == "success"]
    p50 = p95 = 0.0
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]

    # Goodput: 加权成功请求
    goodput = 0
    for r in rows:
        if r["status"] == "success":
            w = TOOL_WEIGHTS.get(r.get("tool_name", ""), 1)
            goodput += w

    # 按工具分组
    tool_stats = defaultdict(lambda: {"success": 0, "rejected": 0, "error": 0, "total": 0})
    for r in rows:
        tn = r.get("tool_name", "unknown")
        tool_stats[tn]["total"] += 1
        tool_stats[tn][r["status"]] += 1

    # 按预算分组
    budget_stats = defaultdict(lambda: {"success": 0, "rejected": 0, "error": 0, "total": 0})
    for r in rows:
        b = r.get("budget", "?")
        budget_stats[b]["total"] += 1
        budget_stats[b][r["status"]] += 1

    return {
        "total": total,
        "success": success,
        "rejected": rejected,
        "error": error,
        "success_pct": 100 * success / total if total else 0,
        "rejected_pct": 100 * rejected / total if total else 0,
        "error_pct": 100 * error / total if total else 0,
        "p50": p50,
        "p95": p95,
        "goodput": goodput,
        "tool_stats": dict(tool_stats),
        "budget_stats": dict(budget_stats),
    }


def print_separator(title=""):
    print(f"\n{'=' * 70}")
    if title:
        print(f"  {title}")
        print(f"{'=' * 70}")


def summarize_experiment(exp_dir, exp_name):
    """汇总一个实验目录。"""
    if not exp_dir.exists():
        return

    print_separator(exp_name)

    # 按 (experiment_prefix, gateway) 分组
    files = sorted(exp_dir.glob("*.csv"))
    groups = defaultdict(list)

    for f in files:
        # Parse filename like "exp1_ng_run1.csv" or "exp2_hr10_ng_run1.csv"
        parts = f.stem.split("_")
        # Find gateway name (last before runN)
        run_idx = None
        for i, p in enumerate(parts):
            if p.startswith("run"):
                run_idx = i
                break
        if run_idx is None:
            continue
        gateway = parts[run_idx - 1]
        prefix = "_".join(parts[:run_idx - 1])
        groups[(prefix, gateway)].append(f)

    for (prefix, gateway), run_files in sorted(groups.items()):
        all_metrics = []
        for rf in sorted(run_files):
            rows = load_csv(rf)
            m = compute_metrics(rows)
            all_metrics.append(m)

        # 计算均值 ± 标准差
        n = len(all_metrics)
        success_pcts = [m["success_pct"] for m in all_metrics]
        rejected_pcts = [m["rejected_pct"] for m in all_metrics]
        error_pcts = [m["error_pct"] for m in all_metrics]
        p50s = [m["p50"] for m in all_metrics]
        goodputs = [m["goodput"] for m in all_metrics]

        mean_s = statistics.mean(success_pcts)
        std_s = statistics.stdev(success_pcts) if n > 1 else 0
        mean_r = statistics.mean(rejected_pcts)
        std_r = statistics.stdev(rejected_pcts) if n > 1 else 0
        mean_e = statistics.mean(error_pcts)
        mean_p50 = statistics.mean(p50s)
        mean_gp = statistics.mean(goodputs)
        std_gp = statistics.stdev(goodputs) if n > 1 else 0

        label = f"{prefix} | {gateway.upper()}"
        print(f"\n  {label} ({n} runs)")
        print(f"    Success:  {mean_s:5.1f}% ± {std_s:.1f}%")
        print(f"    Rejected: {mean_r:5.1f}% ± {std_r:.1f}%")
        print(f"    Error:    {mean_e:5.1f}%")
        print(f"    P50:      {mean_p50:.0f} ms")
        print(f"    Goodput:  {mean_gp:.0f} ± {std_gp:.0f}")

        # 工具级拒绝统计 (合并所有 runs)
        tool_reject = defaultdict(lambda: {"rej": 0, "total": 0})
        for m in all_metrics:
            for tn, ts in m["tool_stats"].items():
                tool_reject[tn]["rej"] += ts["rejected"]
                tool_reject[tn]["total"] += ts["total"]
        reject_detail = []
        for tn in sorted(tool_reject):
            r = tool_reject[tn]
            pct = 100 * r["rej"] / r["total"] if r["total"] else 0
            if r["rej"] > 0:
                reject_detail.append(f"{tn}:{r['rej']}/{r['total']}({pct:.0f}%)")
        if reject_detail:
            print(f"    Rejections: {', '.join(reject_detail)}")

        # 预算级统计 (仅 Exp3)
        if "exp3" in prefix:
            for m in all_metrics[:1]:  # 只打印第一个 run 的预算分组
                for bud, bs in sorted(m["budget_stats"].items()):
                    if bs["total"] > 0:
                        pct = 100 * bs["success"] / bs["total"]
                        rej = 100 * bs["rejected"] / bs["total"]
                        print(f"    Budget={bud}: success={pct:.0f}% reject={rej:.0f}% (n={bs['total']})")


def main():
    print_separator("MCP Phase 3 实验结果汇总")

    experiments = [
        ("exp1_step_pulse", "Exp1: 核心负载与恢复能力 (Step 脉冲)"),
        ("exp2_heavy_ratio", "Exp2: Heavy Ratio 敏感性 (Poisson)"),
        ("exp3_budget_fairness", "Exp3: 预算公平性 (Poisson)"),
        ("exp4_ablation", "Exp4: 消融实验 (Poisson)"),
    ]

    for dirname, title in experiments:
        exp_dir = RESULTS_DIR / dirname
        summarize_experiment(exp_dir, title)

    print_separator("ALL DONE")


if __name__ == "__main__":
    main()
