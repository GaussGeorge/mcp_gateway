#!/usr/bin/env python3
"""
compute_fairness.py — Jain 公平性指数计算器

计算 Jain's Fairness Index (JFI) 量化各网关对会话资源分配的公平性。

公式: JFI = (Σ xi)² / (n × Σ xi²)，其中 xi 为每个会话获得的资源份额。

指标维度:
  1. Steps fairness:  每个会话的执行步数分布（越均匀越公平）
  2. Latency fairness: 每个会话的端到端延迟分布（越均匀越公平）
  3. Goodput fairness: 每个会话的有效吞吐量分布

输入: DAG 发压机产出的 *_sessions.csv 文件
输出: 公平性指标 CSV + 终端报告

用法:
  python scripts/compute_fairness.py --input results/exp1_core/
  python scripts/compute_fairness.py --input results/exp1_core/ --output results/paper_figures/fairness.csv
"""

import argparse
import csv
import glob
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple


def jain_fairness_index(values: List[float]) -> float:
    """计算 Jain's Fairness Index: JFI = (Σ xi)² / (n × Σ xi²)
    
    JFI ∈ [1/n, 1.0]
      - 1.0 = 完全公平（所有值相等）
      - 1/n = 极端不公平（一个用户独占所有资源）
    """
    if not values or len(values) < 2:
        return 1.0
    n = len(values)
    sum_x = sum(values)
    sum_x2 = sum(x * x for x in values)
    if sum_x2 == 0:
        return 1.0
    return (sum_x * sum_x) / (n * sum_x2)


def max_min_fairness_ratio(values: List[float]) -> float:
    """计算 Max-Min 公平性比值: min(x) / max(x)
    
    值域 [0, 1]，1.0 = 完全公平
    """
    if not values or len(values) < 2:
        return 1.0
    vmin = min(values)
    vmax = max(values)
    if vmax == 0:
        return 1.0
    return vmin / vmax


def coefficient_of_variation(values: List[float]) -> float:
    """计算变异系数 (CV): std / mean
    
    越小越公平，0 = 完全公平
    """
    if not values or len(values) < 2:
        return 0.0
    import statistics
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0
    std = statistics.stdev(values)
    return std / mean


def parse_session_csvs(input_dir: str) -> Dict[str, List[dict]]:
    """解析目录下所有 *_sessions.csv 文件，按网关名称分组"""
    pattern = os.path.join(input_dir, "**", "*_sessions.csv")
    files = glob.glob(pattern, recursive=True)
    if not files:
        # 尝试非递归
        pattern = os.path.join(input_dir, "*_sessions.csv")
        files = glob.glob(pattern)

    gateway_sessions: Dict[str, List[dict]] = defaultdict(list)

    for fpath in files:
        fname = os.path.basename(fpath)
        # 从文件名中提取网关名: {gateway}_{sweep}_{runN}_sessions.csv
        # 或 {gateway}_run{N}_sessions.csv
        parts = fname.replace("_sessions.csv", "").split("_run")
        if parts:
            gw_name = parts[0]
            # Remove sweep value suffix if present
            # e.g., "ng_concurrency200" → "ng"
            for sweep_key in ["concurrency", "heavy_ratio", "ps_ratio", "price_ttl"]:
                idx = gw_name.find(f"_{sweep_key}")
                if idx >= 0:
                    gw_name = gw_name[:idx]
                    break

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row["_gateway"] = gw_name
                    row["_file"] = fpath
                    gateway_sessions[gw_name].append(row)
        except Exception as e:
            print(f"  [WARN] 跳过 {fpath}: {e}", file=sys.stderr)

    return gateway_sessions


def compute_gateway_fairness(sessions: List[dict]) -> dict:
    """对单个网关的所有会话计算公平性指标"""
    # 只看成功会话的步数分布
    all_steps = []
    success_steps = []
    success_latencies = []
    success_goodputs = []

    for s in sessions:
        state = s.get("state", "")
        n_steps = 0
        try:
            n_steps = int(s.get("n_steps", 0))
        except (ValueError, TypeError):
            pass

        all_steps.append(n_steps)

        if state == "SUCCESS":
            success_steps.append(n_steps)
            try:
                lat = float(s.get("total_latency_ms", 0))
                success_latencies.append(lat)
            except (ValueError, TypeError):
                pass
            try:
                gp = float(s.get("effective_goodput", 0))
                success_goodputs.append(gp)
            except (ValueError, TypeError):
                pass

    result = {
        "total_sessions": len(sessions),
        "success_sessions": len(success_steps),
    }

    # Steps fairness (all sessions, including rejected)
    if all_steps:
        result["jfi_steps_all"] = jain_fairness_index([float(x) for x in all_steps])
        result["cv_steps_all"] = coefficient_of_variation([float(x) for x in all_steps])

    # Steps fairness (success only)
    if success_steps:
        result["jfi_steps_success"] = jain_fairness_index([float(x) for x in success_steps])
        result["mmf_steps_success"] = max_min_fairness_ratio([float(x) for x in success_steps])

    # Latency fairness (success only)
    if success_latencies:
        result["jfi_latency"] = jain_fairness_index(success_latencies)
        result["cv_latency"] = coefficient_of_variation(success_latencies)
        result["mmf_latency"] = max_min_fairness_ratio(success_latencies)

    # Goodput fairness (success only)
    if success_goodputs:
        result["jfi_goodput"] = jain_fairness_index(success_goodputs)
        result["cv_goodput"] = coefficient_of_variation(success_goodputs)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Jain 公平性指数计算器 — 量化 PlanGate 与基线的资源分配公平性",
    )
    parser.add_argument("--input", required=True,
                        help="实验结果目录 (包含 *_sessions.csv 文件)")
    parser.add_argument("--output", default=None,
                        help="输出 CSV 路径 (默认: {input}/fairness_summary.csv)")
    args = parser.parse_args()

    if not os.path.isdir(args.input):
        print(f"错误: 目录不存在 {args.input}")
        sys.exit(1)

    gateway_sessions = parse_session_csvs(args.input)
    if not gateway_sessions:
        print(f"错误: 在 {args.input} 中未找到 *_sessions.csv 文件")
        sys.exit(1)

    print(f"\n{'='*80}")
    print(f"  Jain 公平性指数分析")
    print(f"  输入: {args.input}")
    print(f"  网关: {', '.join(sorted(gateway_sessions.keys()))}")
    print(f"{'='*80}\n")

    results = []
    for gw_name in sorted(gateway_sessions.keys()):
        sessions = gateway_sessions[gw_name]
        fairness = compute_gateway_fairness(sessions)
        fairness["gateway"] = gw_name
        results.append(fairness)

        print(f"  [{gw_name}] (n={fairness['total_sessions']}, success={fairness['success_sessions']})")
        if "jfi_steps_success" in fairness:
            print(f"    Steps  JFI={fairness['jfi_steps_success']:.4f}  "
                  f"MMF={fairness.get('mmf_steps_success', 0):.4f}")
        if "jfi_latency" in fairness:
            print(f"    Latency JFI={fairness['jfi_latency']:.4f}  "
                  f"CV={fairness.get('cv_latency', 0):.4f}  "
                  f"MMF={fairness.get('mmf_latency', 0):.4f}")
        if "jfi_goodput" in fairness:
            print(f"    Goodput JFI={fairness['jfi_goodput']:.4f}  "
                  f"CV={fairness.get('cv_goodput', 0):.4f}")
        print()

    # 保存 CSV
    output_path = args.output or os.path.join(args.input, "fairness_summary.csv")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    fieldnames = [
        "gateway", "total_sessions", "success_sessions",
        "jfi_steps_all", "cv_steps_all",
        "jfi_steps_success", "mmf_steps_success",
        "jfi_latency", "cv_latency", "mmf_latency",
        "jfi_goodput", "cv_goodput",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"  公平性摘要已保存: {output_path}")


if __name__ == "__main__":
    main()
