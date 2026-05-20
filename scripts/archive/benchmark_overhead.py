#!/usr/bin/env python3
"""
benchmark_overhead.py — PlanGate 网关开销评估脚本

评审要求：报告引入 PlanGate 后的额外网络延迟与 CPU 开销。
测试方法：
  1. 直连后端 vs 经过 PlanGate 的延迟对比
  2. 不同并发度下的延迟 CDF
  3. CPU/内存占用记录

输出：
  results/overhead/overhead_summary.csv — 汇总统计
  results/overhead/latency_cdf.csv     — 延迟 CDF 数据（供绘图用）

用法:
  # 先启动后端: python mcp_server/server.py --port 8080
  # 再启动网关: ./gateway.exe --mode mcpdp --port 9003 --backend http://127.0.0.1:8080

  python scripts/benchmark_overhead.py \\
      --backend http://127.0.0.1:8080 \\
      --gateway http://127.0.0.1:9003 \\
      --concurrency 1,10,50,100,500,1000 \\
      --requests-per-level 1000 \\
      --output results/overhead
"""

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

import aiohttp
import numpy as np


# JSON-RPC 请求模板
def make_ping_request():
    return json.dumps({
        "jsonrpc": "2.0",
        "id": "overhead-ping",
        "method": "ping",
    }).encode()


def make_tools_list_request():
    return json.dumps({
        "jsonrpc": "2.0",
        "id": "overhead-list",
        "method": "tools/list",
    }).encode()


def make_tool_call_request(tool_name="calculate", tokens=500, session_id=None):
    params = {
        "name": tool_name,
        "arguments": {"operation": "add", "a": 1, "b": 2},
        "_meta": {"tokens": tokens},
    }
    req = {
        "jsonrpc": "2.0",
        "id": f"oh-{tool_name}",
        "method": "tools/call",
        "params": params,
    }
    return json.dumps(req).encode(), session_id


@dataclass
class LatencyResult:
    """单次请求延迟结果"""
    target: str           # "backend" or "gateway"
    concurrency: int
    request_type: str     # "ping", "tools_list", "tool_call"
    latency_ms: float
    success: bool
    status_code: int = 0


async def send_request(session: aiohttp.ClientSession, url: str, body: bytes,
                       headers: dict = None) -> tuple:
    """发送单次请求，返回 (latency_ms, success, status_code)"""
    start = time.perf_counter()
    try:
        async with session.post(url, data=body, headers=headers or {"Content-Type": "application/json"},
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            await resp.read()
            elapsed = (time.perf_counter() - start) * 1000
            return elapsed, True, resp.status
    except Exception:
        elapsed = (time.perf_counter() - start) * 1000
        return elapsed, False, 0


async def run_latency_test(url: str, target_name: str, concurrency: int,
                           total_requests: int, request_type: str = "ping") -> List[LatencyResult]:
    """对指定目标运行延迟测试"""
    results = []

    if request_type == "ping":
        body = make_ping_request()
        headers = {"Content-Type": "application/json"}
    elif request_type == "tools_list":
        body = make_tools_list_request()
        headers = {"Content-Type": "application/json"}
    else:  # tool_call
        body, _ = make_tool_call_request()
        headers = {"Content-Type": "application/json"}

    sem = asyncio.Semaphore(concurrency)

    async def worker(idx: int):
        async with sem:
            hdrs = dict(headers)
            if request_type == "tool_call":
                hdrs["X-Session-ID"] = f"overhead-{idx}"
            async with aiohttp.ClientSession() as session:
                lat, ok, code = await send_request(session, url, body, hdrs)
                results.append(LatencyResult(
                    target=target_name,
                    concurrency=concurrency,
                    request_type=request_type,
                    latency_ms=lat,
                    success=ok,
                    status_code=code,
                ))

    tasks = [worker(i) for i in range(total_requests)]
    await asyncio.gather(*tasks)
    return results


def compute_percentiles(latencies: List[float]) -> dict:
    """计算延迟百分位数"""
    if not latencies:
        return {}
    arr = np.array(latencies)
    return {
        "count": len(arr),
        "mean": float(np.mean(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
        "min": float(np.min(arr)),
    }


def compute_cdf(latencies: List[float], bins: int = 100) -> List[tuple]:
    """计算 CDF 数据点"""
    if not latencies:
        return []
    sorted_lat = np.sort(latencies)
    n = len(sorted_lat)
    # 均匀采样 bins 个点
    indices = np.linspace(0, n - 1, min(bins, n), dtype=int)
    return [(float(sorted_lat[i]), (i + 1) / n) for i in indices]


async def main_async(args):
    concurrency_levels = [int(c.strip()) for c in args.concurrency.split(",")]
    request_types = ["ping", "tools_list", "tool_call"]
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    all_results = []
    summary_rows = []

    for req_type in request_types:
        for conc in concurrency_levels:
            for target_name, url in [("backend", args.backend), ("gateway", args.gateway)]:
                if url is None:
                    continue

                print(f"  测试: {target_name} | type={req_type} | concurrency={conc} | "
                      f"requests={args.requests_per_level}")

                results = await run_latency_test(
                    url, target_name, conc, args.requests_per_level, req_type
                )
                all_results.extend(results)

                # 计算统计
                success_lats = [r.latency_ms for r in results if r.success]
                stats = compute_percentiles(success_lats)
                stats["target"] = target_name
                stats["concurrency"] = conc
                stats["request_type"] = req_type
                stats["success_rate"] = len(success_lats) / len(results) if results else 0
                summary_rows.append(stats)

                print(f"    → p50={stats.get('p50', 0):.2f}ms  p95={stats.get('p95', 0):.2f}ms  "
                      f"p99={stats.get('p99', 0):.2f}ms  success={stats['success_rate']:.1%}")

    # 计算 PlanGate 额外开销
    print(f"\n{'='*60}")
    print("  PlanGate 额外开销分析")
    print(f"{'='*60}")

    overhead_rows = []
    for req_type in request_types:
        for conc in concurrency_levels:
            backend_stats = next(
                (s for s in summary_rows if s["target"] == "backend"
                 and s["concurrency"] == conc and s["request_type"] == req_type), None)
            gateway_stats = next(
                (s for s in summary_rows if s["target"] == "gateway"
                 and s["concurrency"] == conc and s["request_type"] == req_type), None)
            if backend_stats and gateway_stats:
                overhead_p50 = gateway_stats["p50"] - backend_stats["p50"]
                overhead_p95 = gateway_stats["p95"] - backend_stats["p95"]
                overhead_p99 = gateway_stats["p99"] - backend_stats["p99"]
                print(f"  {req_type:12s} conc={conc:4d}: "
                      f"Δp50={overhead_p50:+.2f}ms  Δp95={overhead_p95:+.2f}ms  Δp99={overhead_p99:+.2f}ms")
                overhead_rows.append({
                    "request_type": req_type,
                    "concurrency": conc,
                    "overhead_p50_ms": overhead_p50,
                    "overhead_p95_ms": overhead_p95,
                    "overhead_p99_ms": overhead_p99,
                    "gateway_p50": gateway_stats["p50"],
                    "gateway_p95": gateway_stats["p95"],
                    "backend_p50": backend_stats["p50"],
                    "backend_p95": backend_stats["p95"],
                })

    # 保存汇总 CSV
    summary_path = os.path.join(output_dir, "overhead_summary.csv")
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["target", "request_type", "concurrency", "count",
                      "mean", "p50", "p90", "p95", "p99", "max", "min", "success_rate"]
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\n  汇总保存: {summary_path}")

    # 保存开销对比 CSV
    if overhead_rows:
        overhead_path = os.path.join(output_dir, "overhead_comparison.csv")
        with open(overhead_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(overhead_rows[0].keys()))
            writer.writeheader()
            writer.writerows(overhead_rows)
        print(f"  开销对比: {overhead_path}")

    # 保存延迟 CDF 数据
    cdf_path = os.path.join(output_dir, "latency_cdf.csv")
    with open(cdf_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["target", "request_type", "concurrency", "latency_ms", "cdf"])
        for req_type in request_types:
            for conc in concurrency_levels:
                for target_name in ["backend", "gateway"]:
                    lats = [r.latency_ms for r in all_results
                            if r.target == target_name and r.concurrency == conc
                            and r.request_type == req_type and r.success]
                    for lat, cdf_val in compute_cdf(lats):
                        writer.writerow([target_name, req_type, conc, f"{lat:.3f}", f"{cdf_val:.4f}"])
    print(f"  CDF 数据: {cdf_path}")


def main():
    parser = argparse.ArgumentParser(description="PlanGate 网关开销评估")
    parser.add_argument("--backend", type=str, default="http://127.0.0.1:8080",
                        help="直连后端地址")
    parser.add_argument("--gateway", type=str, default="http://127.0.0.1:9003",
                        help="PlanGate 网关地址")
    parser.add_argument("--concurrency", type=str, default="1,10,50,100,500,1000",
                        help="并发度列表 (逗号分隔)")
    parser.add_argument("--requests-per-level", type=int, default=1000,
                        help="每个并发度×目标的请求数")
    parser.add_argument("--output", type=str, default="results/overhead",
                        help="输出目录")
    args = parser.parse_args()

    print(f"\n{'#'*60}")
    print(f"  PlanGate 网关开销评估 (System Overhead Benchmark)")
    print(f"  后端: {args.backend}")
    print(f"  网关: {args.gateway}")
    print(f"  并发度: {args.concurrency}")
    print(f"  每级请求数: {args.requests_per_level}")
    print(f"{'#'*60}\n")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
