#!/usr/bin/env python3
"""
analyze_latency_breakdown.py — 延迟分解分析（借鉴 OSDI'24 Parrot 方法论）

将每次请求的端到端延迟分解为三部分:
  1. Gateway Overhead (网关治理开销): X-Gateway-Latency-Us header
  2. Backend Execution (后端执行): total_latency - gateway_overhead - network
  3. Network RTT (网络往返): 近似为 total - gateway - backend

输入: DAG 发压机产出的步骤级 CSV（含 gateway_latency_us 列）
输出: 延迟分解 CSV + CDF 数据 + 分桶统计

用法:
  python scripts/analyze_latency_breakdown.py --input results/exp1_core/
  python scripts/analyze_latency_breakdown.py --input results/exp1_core/plangate_full_run1.csv
"""

import argparse
import csv
import glob
import os
import sys
import statistics
from collections import defaultdict
from typing import Dict, List


def parse_step_csvs(input_path: str) -> Dict[str, List[dict]]:
    """解析步骤级 CSV，按网关名分组"""
    if os.path.isfile(input_path):
        files = [input_path]
    else:
        # 目录模式：找所有非 session 的 CSV
        pattern = os.path.join(input_path, "**", "*.csv")
        files = [f for f in glob.glob(pattern, recursive=True)
                 if "_sessions.csv" not in f
                 and "_summary.csv" not in f
                 and "_stdout.log" not in f
                 and "fairness" not in f]
        if not files:
            pattern = os.path.join(input_path, "*.csv")
            files = [f for f in glob.glob(pattern)
                     if "_sessions.csv" not in f and "_summary.csv" not in f]

    gateway_steps: Dict[str, List[dict]] = defaultdict(list)

    for fpath in files:
        fname = os.path.basename(fpath)
        # 提取网关名
        parts = fname.replace(".csv", "").split("_run")
        gw_name = parts[0] if parts else "unknown"
        for sweep_key in ["concurrency", "heavy_ratio", "ps_ratio", "price_ttl"]:
            idx = gw_name.find(f"_{sweep_key}")
            if idx >= 0:
                gw_name = gw_name[:idx]
                break

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if "gateway_latency_us" not in (reader.fieldnames or []):
                    continue  # 跳过没有延迟分解数据的旧 CSV
                for row in reader:
                    row["_gateway"] = gw_name
                    gateway_steps[gw_name].append(row)
        except Exception as e:
            print(f"  [WARN] 跳过 {fpath}: {e}", file=sys.stderr)

    return gateway_steps


def compute_breakdown(steps: List[dict]) -> dict:
    """计算单个网关的延迟分解统计"""
    gateway_overheads_us = []  # 网关治理开销 (μs)
    backend_latencies_ms = []  # 后端执行延迟 (ms)
    total_latencies_ms = []    # 端到端总延迟 (ms)

    for step in steps:
        if step.get("status") != "success":
            continue

        try:
            total_ms = float(step.get("latency_ms", 0))
            gw_us = float(step.get("gateway_latency_us", 0))
        except (ValueError, TypeError):
            continue

        if total_ms <= 0:
            continue

        gw_ms = gw_us / 1000.0  # μs → ms
        backend_ms = max(0, total_ms - gw_ms)

        total_latencies_ms.append(total_ms)
        gateway_overheads_us.append(gw_us)
        backend_latencies_ms.append(backend_ms)

    if not total_latencies_ms:
        return {}

    def percentile(data, p):
        if not data:
            return 0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        idx = min(idx, len(sorted_data) - 1)
        return sorted_data[idx]

    result = {
        "n_steps": len(total_latencies_ms),
        # 网关开销 (μs)
        "gw_overhead_p50_us": percentile(gateway_overheads_us, 50),
        "gw_overhead_p95_us": percentile(gateway_overheads_us, 95),
        "gw_overhead_p99_us": percentile(gateway_overheads_us, 99),
        "gw_overhead_mean_us": statistics.mean(gateway_overheads_us),
        # 后端延迟 (ms)
        "backend_p50_ms": percentile(backend_latencies_ms, 50),
        "backend_p95_ms": percentile(backend_latencies_ms, 95),
        "backend_p99_ms": percentile(backend_latencies_ms, 99),
        "backend_mean_ms": statistics.mean(backend_latencies_ms),
        # 总延迟 (ms)
        "total_p50_ms": percentile(total_latencies_ms, 50),
        "total_p95_ms": percentile(total_latencies_ms, 95),
        "total_p99_ms": percentile(total_latencies_ms, 99),
        "total_mean_ms": statistics.mean(total_latencies_ms),
        # 网关开销占比
        "gw_overhead_pct_mean": (statistics.mean(gateway_overheads_us) / 1000.0)
                                / statistics.mean(total_latencies_ms) * 100
                                if statistics.mean(total_latencies_ms) > 0 else 0,
    }
    return result


def save_cdf_data(gateway_steps: Dict[str, List[dict]], output_dir: str):
    """保存延迟 CDF 数据（供绘图）"""
    cdf_path = os.path.join(output_dir, "latency_breakdown_cdf.csv")
    with open(cdf_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["gateway", "component", "latency_ms", "percentile"])

        for gw_name, steps in sorted(gateway_steps.items()):
            gw_vals = []
            backend_vals = []
            total_vals = []

            for step in steps:
                if step.get("status") != "success":
                    continue
                try:
                    total_ms = float(step.get("latency_ms", 0))
                    gw_us = float(step.get("gateway_latency_us", 0))
                except (ValueError, TypeError):
                    continue
                if total_ms <= 0:
                    continue

                gw_ms = gw_us / 1000.0
                backend_ms = max(0, total_ms - gw_ms)

                gw_vals.append(gw_ms)
                backend_vals.append(backend_ms)
                total_vals.append(total_ms)

            for component, vals in [("gateway", gw_vals), ("backend", backend_vals), ("total", total_vals)]:
                if not vals:
                    continue
                sorted_vals = sorted(vals)
                n = len(sorted_vals)
                for i, v in enumerate(sorted_vals):
                    pct = (i + 1) / n * 100
                    writer.writerow([gw_name, component, f"{v:.3f}", f"{pct:.2f}"])

    print(f"  CDF 数据已保存: {cdf_path}")


def main():
    parser = argparse.ArgumentParser(
        description="延迟分解分析 — 网关开销 vs 后端执行",
    )
    parser.add_argument("--input", required=True,
                        help="实验结果目录或单个 CSV 文件")
    parser.add_argument("--output", default=None,
                        help="输出目录 (默认: 同输入目录)")
    args = parser.parse_args()

    output_dir = args.output or (args.input if os.path.isdir(args.input) else os.path.dirname(args.input))
    os.makedirs(output_dir, exist_ok=True)

    gateway_steps = parse_step_csvs(args.input)
    if not gateway_steps:
        print(f"错误: 未找到包含 gateway_latency_us 的 CSV 文件")
        sys.exit(1)

    print(f"\n{'='*80}")
    print(f"  延迟分解分析 (Parrot-style Latency Breakdown)")
    print(f"  输入: {args.input}")
    print(f"  网关: {', '.join(sorted(gateway_steps.keys()))}")
    print(f"{'='*80}\n")

    # 汇总 CSV
    summary_path = os.path.join(output_dir, "latency_breakdown_summary.csv")
    fieldnames = [
        "gateway", "n_steps",
        "gw_overhead_p50_us", "gw_overhead_p95_us", "gw_overhead_p99_us", "gw_overhead_mean_us",
        "backend_p50_ms", "backend_p95_ms", "backend_p99_ms", "backend_mean_ms",
        "total_p50_ms", "total_p95_ms", "total_p99_ms", "total_mean_ms",
        "gw_overhead_pct_mean",
    ]

    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for gw_name in sorted(gateway_steps.keys()):
            steps = gateway_steps[gw_name]
            breakdown = compute_breakdown(steps)
            if not breakdown:
                print(f"  [{gw_name}] 无有效数据")
                continue

            breakdown["gateway"] = gw_name
            writer.writerow(breakdown)

            print(f"  [{gw_name}] (n={breakdown['n_steps']} steps)")
            print(f"    Gateway overhead: P50={breakdown['gw_overhead_p50_us']:.0f}μs  "
                  f"P95={breakdown['gw_overhead_p95_us']:.0f}μs  "
                  f"P99={breakdown['gw_overhead_p99_us']:.0f}μs")
            print(f"    Backend latency:  P50={breakdown['backend_p50_ms']:.1f}ms  "
                  f"P95={breakdown['backend_p95_ms']:.1f}ms")
            print(f"    Total latency:    P50={breakdown['total_p50_ms']:.1f}ms  "
                  f"P95={breakdown['total_p95_ms']:.1f}ms")
            print(f"    GW overhead ratio: {breakdown['gw_overhead_pct_mean']:.2f}%")
            print()

    print(f"  延迟分解摘要已保存: {summary_path}")

    # 保存 CDF 数据
    save_cdf_data(gateway_steps, output_dir)


if __name__ == "__main__":
    main()
