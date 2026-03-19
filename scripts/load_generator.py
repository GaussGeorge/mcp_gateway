"""
load_generator.py — MCP 服务治理实验 · 全异步发压机
========================================================
Phase 2.1 实现：
  - 支持 Poisson / Step 两种波形
  - 固定随机种子保证可复现
  - 混合轻量 + 重量请求（可调比例）
  - 每个请求携带 budget（令牌预算）
  - 落盘 CSV：timestamp, status, latency_ms, budget, tool_name

用法:
  # Poisson 稳态，目标 QPS=30，heavy 占比 20%，预算 100，时长 60s
  python scripts/load_generator.py --target http://127.0.0.1:9003 \\
      --waveform poisson --qps 30 --heavy-ratio 0.2 \\
      --budget 100 --duration 60 --output results/dp_poisson.csv

  # Step 脉冲：基准 QPS=10 → 第 10s 突增到 80 → 第 30s 回落 → 结束
  python scripts/load_generator.py --target http://127.0.0.1:9003 \\
      --waveform step --duration 50 --output results/dp_step.csv \\
      --step-stages "0:10:10,10:80:20,30:10:20"

  # 双预算组（公平性实验 Exp3）
  python scripts/load_generator.py --target http://127.0.0.1:9003 \\
      --waveform poisson --qps 40 --heavy-ratio 0.3 \\
      --budget-groups "10:50,100:50" --duration 60

CPU Affinity: Core 0-1 (发压机独占)
"""

import asyncio
import aiohttp
import argparse
import csv
import json
import os
import platform
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

# ══════════════════════════════════════════════════
# 1. 固定随机种子 — 统计严谨性
# ══════════════════════════════════════════════════
SEED = 20260401
random.seed(SEED)
np.random.seed(SEED)

# ══════════════════════════════════════════════════
# 2. CPU 亲和性 — 绑核 Core 0-1
# ══════════════════════════════════════════════════
def set_cpu_affinity(cores: List[int]):
    if platform.system() != "Windows":
        return
    try:
        import psutil
        psutil.Process(os.getpid()).cpu_affinity(cores)
        print(f"[发压机] CPU 亲和性: cores={cores}")
    except ImportError:
        print("[发压机] psutil 未安装，跳过绑核")
    except Exception as e:
        print(f"[发压机] 绑核失败: {e}")


# ══════════════════════════════════════════════════
# 3. 请求模板
# ══════════════════════════════════════════════════
LIGHT_TOOLS = [
    {
        "name": "calculate",
        "arguments": {"operation": "multiply", "a": 17, "b": 23},
    },
    {
        "name": "web_fetch",
        "arguments": {"url": "https://example.com/doc", "max_length": 500, "simulate_rtt_ms": 150},
    },
]

HEAVY_TOOL = {
    "name": "mock_heavy",
    "arguments": {"cpu_burn_ms": 800, "memory_mb": 0},
}


def pick_request(heavy_ratio: float) -> Tuple[str, dict]:
    """按比例选择轻量/重量工具。"""
    if random.random() < heavy_ratio:
        return HEAVY_TOOL["name"], HEAVY_TOOL["arguments"]
    else:
        t = random.choice(LIGHT_TOOLS)
        return t["name"], t["arguments"]


# ══════════════════════════════════════════════════
# 4. 预算组
# ══════════════════════════════════════════════════
@dataclass
class BudgetGroup:
    budget: int
    weight: int  # 百分比权重

    def __repr__(self):
        return f"Budget({self.budget}, w={self.weight}%)"


def parse_budget_groups(spec: str) -> List[BudgetGroup]:
    """解析预算组: '10:50,100:50' → [BudgetGroup(10, 50), BudgetGroup(100, 50)]"""
    groups = []
    for part in spec.split(","):
        budget, weight = part.strip().split(":")
        groups.append(BudgetGroup(int(budget), int(weight)))
    return groups


def pick_budget(groups: List[BudgetGroup]) -> int:
    """按权重随机选择预算值。"""
    weights = [g.weight for g in groups]
    total = sum(weights)
    r = random.randint(1, total)
    cumsum = 0
    for g in groups:
        cumsum += g.weight
        if r <= cumsum:
            return g.budget
    return groups[-1].budget


# ══════════════════════════════════════════════════
# 5. Step 波形解析
# ══════════════════════════════════════════════════
@dataclass
class StepStage:
    start_sec: float   # 该阶段开始时间 (s)
    qps: float         # 该阶段目标 QPS
    duration: float    # 该阶段持续时间 (s)


def parse_step_stages(spec: str) -> List[StepStage]:
    """解析 Step 阶段: '0:10:10,10:80:20,30:10:20'"""
    stages = []
    for part in spec.split(","):
        start, qps, dur = part.strip().split(":")
        stages.append(StepStage(float(start), float(qps), float(dur)))
    return stages


def get_step_qps(stages: List[StepStage], elapsed: float) -> float:
    """根据当前时间获取 Step 波形的 QPS。"""
    for stage in reversed(stages):
        if elapsed >= stage.start_sec:
            return stage.qps
    return stages[0].qps if stages else 10.0


# ══════════════════════════════════════════════════
# 5b. Composite 复合波形 (过山车流量)
# ══════════════════════════════════════════════════
@dataclass
class CompositePhase:
    start: float
    end: float
    ptype: str        # "poisson" | "sine" | "square"
    name: str
    base_qps: float = 30
    min_qps: float = 30
    max_qps: float = 90
    period: float = 5.0
    high_qps: float = 100
    low_qps: float = 10


# 默认5阶段复合流量配置 (120秒 "过山车")
DEFAULT_COMPOSITE_PHASES = [
    CompositePhase(start=0,   end=20,  ptype="poisson", name="steady",      base_qps=30),
    CompositePhase(start=20,  end=45,  ptype="poisson", name="burst",       base_qps=120),
    CompositePhase(start=45,  end=80,  ptype="sine",    name="periodic",    min_qps=30, max_qps=90, period=5.0),
    CompositePhase(start=80,  end=100, ptype="poisson", name="idle",        base_qps=10),
    CompositePhase(start=100, end=120, ptype="square",  name="micro_burst", high_qps=100, low_qps=10, period=4.0),
]


def get_composite_qps(phases: List[CompositePhase], elapsed: float) -> float:
    """根据当前时间返回复合波形的目标 QPS。"""
    for phase in phases:
        if phase.start <= elapsed < phase.end:
            if phase.ptype == "poisson":
                return phase.base_qps
            elif phase.ptype == "sine":
                offset = elapsed - phase.start
                mid = (phase.max_qps + phase.min_qps) / 2
                amp = (phase.max_qps - phase.min_qps) / 2
                return mid + amp * np.sin(offset * 2 * np.pi / phase.period)
            elif phase.ptype == "square":
                offset = elapsed - phase.start
                cycle = int(offset) % int(phase.period)
                return phase.high_qps if cycle < phase.period / 2 else phase.low_qps
    return 0


# ══════════════════════════════════════════════════
# 6. 核心：异步请求发送
# ══════════════════════════════════════════════════
@dataclass
class RequestResult:
    timestamp: float
    status: str        # "success" | "rejected" | "error"
    latency_ms: float
    budget: int
    tool_name: str


async def send_single_request(
    session: aiohttp.ClientSession,
    url: str,
    tool_name: str,
    arguments: dict,
    budget: int,
    req_id: int,
) -> RequestResult:
    """发送单个 JSON-RPC 请求并记录结果。"""
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
            "_meta": {"tokens": budget, "name": "load-generator"},
        },
    }

    ts = time.time()
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            body = await resp.json()
            latency = (time.time() - ts) * 1000

            if "error" in body and body["error"] is not None:
                code = body["error"].get("code", 0)
                # -32001 Overloaded, -32002 RateLimited, -32003 TokenInsufficient
                if code in (-32001, -32002, -32003):
                    status = "rejected"
                else:
                    status = "error"
            else:
                status = "success"

            return RequestResult(ts, status, latency, budget, tool_name)

    except asyncio.TimeoutError:
        latency = (time.time() - ts) * 1000
        return RequestResult(ts, "error", latency, budget, tool_name)
    except Exception:
        latency = (time.time() - ts) * 1000
        return RequestResult(ts, "error", latency, budget, tool_name)


# ══════════════════════════════════════════════════
# 7. 波形调度器
# ══════════════════════════════════════════════════
async def generate_poisson(
    session: aiohttp.ClientSession,
    url: str,
    qps: float,
    duration: float,
    heavy_ratio: float,
    budget_groups: List[BudgetGroup],
    results: List[RequestResult],
    semaphore: asyncio.Semaphore,
):
    """Poisson 到达: 请求间隔服从 Exponential(1/qps)。"""
    start_time = time.time()
    req_id = 0
    tasks = []

    while (time.time() - start_time) < duration:
        # Poisson 到达间隔
        interval = np.random.exponential(1.0 / qps)
        await asyncio.sleep(interval)

        req_id += 1
        tool_name, arguments = pick_request(heavy_ratio)
        budget = pick_budget(budget_groups)

        async def fire(tn=tool_name, args=arguments, b=budget, rid=req_id):
            async with semaphore:
                result = await send_single_request(session, url, tn, args, b, rid)
                results.append(result)

        tasks.append(asyncio.create_task(fire()))

    # 等待所有未完成的请求
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def generate_step(
    session: aiohttp.ClientSession,
    url: str,
    stages: List[StepStage],
    duration: float,
    heavy_ratio: float,
    budget_groups: List[BudgetGroup],
    results: List[RequestResult],
    semaphore: asyncio.Semaphore,
):
    """Step 脉冲: 按阶段切换 QPS，每阶段内使用 Poisson 到达。"""
    start_time = time.time()
    req_id = 0
    tasks = []

    while (time.time() - start_time) < duration:
        elapsed = time.time() - start_time
        current_qps = get_step_qps(stages, elapsed)

        if current_qps <= 0:
            await asyncio.sleep(0.1)
            continue

        interval = np.random.exponential(1.0 / current_qps)
        await asyncio.sleep(interval)

        req_id += 1
        tool_name, arguments = pick_request(heavy_ratio)
        budget = pick_budget(budget_groups)

        async def fire(tn=tool_name, args=arguments, b=budget, rid=req_id):
            async with semaphore:
                result = await send_single_request(session, url, tn, args, b, rid)
                results.append(result)

        tasks.append(asyncio.create_task(fire()))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def generate_composite(
    session: aiohttp.ClientSession,
    url: str,
    phases: List[CompositePhase],
    heavy_ratio: float,
    budget_groups: List[BudgetGroup],
    results: List[RequestResult],
    semaphore: asyncio.Semaphore,
):
    """Composite 复合流量: 5阶段过山车波形 (steady→burst→sine→idle→square)。"""
    duration = max(p.end for p in phases)
    start_time = time.time()
    req_id = 0
    tasks = []

    while (time.time() - start_time) < duration:
        elapsed = time.time() - start_time
        current_qps = get_composite_qps(phases, elapsed)

        if current_qps <= 0:
            await asyncio.sleep(0.01)
            continue

        interval = np.random.exponential(1.0 / current_qps)
        await asyncio.sleep(interval)

        req_id += 1
        tool_name, arguments = pick_request(heavy_ratio)
        budget = pick_budget(budget_groups)

        async def fire(tn=tool_name, args=arguments, b=budget, rid=req_id):
            async with semaphore:
                result = await send_single_request(session, url, tn, args, b, rid)
                results.append(result)

        tasks.append(asyncio.create_task(fire()))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ══════════════════════════════════════════════════
# 8. CSV 落盘
# ══════════════════════════════════════════════════
def save_csv(results: List[RequestResult], output_path: str):
    """保存结果到 CSV 文件。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "status", "latency_ms", "budget", "tool_name"])
        for r in results:
            writer.writerow([
                f"{r.timestamp:.6f}",
                r.status,
                f"{r.latency_ms:.2f}",
                r.budget,
                r.tool_name,
            ])


def print_summary(results: List[RequestResult], elapsed: float):
    """打印运行摘要。"""
    total = len(results)
    if total == 0:
        print("[发压机] 无请求结果。")
        return

    success = sum(1 for r in results if r.status == "success")
    rejected = sum(1 for r in results if r.status == "rejected")
    errors = sum(1 for r in results if r.status == "error")
    latencies = [r.latency_ms for r in results if r.status == "success"]

    print(f"\n{'=' * 55}")
    print(f"  发压机运行摘要")
    print(f"{'=' * 55}")
    print(f"  总请求数:    {total}")
    print(f"  成功:        {success} ({100*success/total:.1f}%)")
    print(f"  被拒绝:      {rejected} ({100*rejected/total:.1f}%)")
    print(f"  错误:        {errors} ({100*errors/total:.1f}%)")
    print(f"  实际 QPS:    {total/elapsed:.1f} req/s")
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies)//2]
        p95 = latencies[int(len(latencies)*0.95)]
        p99 = latencies[int(len(latencies)*0.99)]
        print(f"  延迟 P50:    {p50:.1f} ms")
        print(f"  延迟 P95:    {p95:.1f} ms")
        print(f"  延迟 P99:    {p99:.1f} ms")

    # 按工具分类统计
    tool_stats = {}
    for r in results:
        t = tool_stats.setdefault(r.tool_name, {"total": 0, "success": 0, "rejected": 0})
        t["total"] += 1
        if r.status == "success":
            t["success"] += 1
        elif r.status == "rejected":
            t["rejected"] += 1
    print(f"\n  按工具统计:")
    for tn, st in sorted(tool_stats.items()):
        rate = 100 * st["success"] / st["total"] if st["total"] > 0 else 0
        print(f"    {tn:15s}  total={st['total']:4d}  pass={st['success']:4d} ({rate:.0f}%)  reject={st['rejected']:4d}")

    # 按预算分组统计
    budget_stats = {}
    for r in results:
        b = budget_stats.setdefault(r.budget, {"total": 0, "success": 0, "rejected": 0})
        b["total"] += 1
        if r.status == "success":
            b["success"] += 1
        elif r.status == "rejected":
            b["rejected"] += 1
    if len(budget_stats) > 1:
        print(f"\n  按预算统计:")
        for bv, st in sorted(budget_stats.items()):
            rate = 100 * st["success"] / st["total"] if st["total"] > 0 else 0
            print(f"    budget={bv:5d}  total={st['total']:4d}  pass={st['success']:4d} ({rate:.0f}%)  reject={st['rejected']:4d}")

    print(f"{'=' * 55}")


# ══════════════════════════════════════════════════
# 9. 主入口
# ══════════════════════════════════════════════════
async def run(args):
    """异步主逻辑。"""
    # 解析预算组
    if args.budget_groups:
        budget_groups = parse_budget_groups(args.budget_groups)
    else:
        budget_groups = [BudgetGroup(args.budget, 100)]

    print(f"[发压机] 目标: {args.target}")
    print(f"[发压机] 波形: {args.waveform}")
    print(f"[发压机] 时长: {args.duration}s")
    print(f"[发压机] 重载比: {args.heavy_ratio*100:.0f}%")
    print(f"[发压机] 预算组: {budget_groups}")
    print(f"[发压机] 最大并发: {args.concurrency}")
    if args.waveform == "poisson":
        print(f"[发压机] 目标 QPS: {args.qps}")

    results: List[RequestResult] = []
    semaphore = asyncio.Semaphore(args.concurrency)

    connector = aiohttp.TCPConnector(limit=args.concurrency, limit_per_host=args.concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:
        start = time.time()

        if args.waveform == "poisson":
            await generate_poisson(
                session, args.target, args.qps, args.duration,
                args.heavy_ratio, budget_groups, results, semaphore,
            )
        elif args.waveform == "step":
            stages = parse_step_stages(args.step_stages)
            print(f"[发压机] Step 阶段: {[(s.start_sec, s.qps, s.duration) for s in stages]}")
            total_dur = max(s.start_sec + s.duration for s in stages) if stages else args.duration
            await generate_step(
                session, args.target, stages, total_dur,
                args.heavy_ratio, budget_groups, results, semaphore,
            )
        elif args.waveform == "composite":
            phases = DEFAULT_COMPOSITE_PHASES
            print(f"[发压机] Composite 阶段: {[(p.name, p.start, p.end, p.ptype) for p in phases]}")
            await generate_composite(
                session, args.target, phases,
                args.heavy_ratio, budget_groups, results, semaphore,
            )

        elapsed = time.time() - start

    # 按时间戳排序
    results.sort(key=lambda r: r.timestamp)

    # 输出
    print_summary(results, elapsed)

    if args.output:
        save_csv(results, args.output)
        print(f"\n[发压机] 结果已保存: {args.output} ({len(results)} 条记录)")


def main():
    parser = argparse.ArgumentParser(
        description="MCP 服务治理实验 · 全异步发压机",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", required=True,
                        help="目标网关 URL, 如 http://127.0.0.1:9003")
    parser.add_argument("--waveform", choices=["poisson", "step", "composite"], default="poisson",
                        help="负载波形: poisson (稳态) | step (脉冲) | composite (120s复合过山车)")
    parser.add_argument("--qps", type=float, default=30,
                        help="Poisson 模式目标 QPS (default: 30)")
    parser.add_argument("--duration", type=float, default=60,
                        help="测试持续时间/秒 (default: 60)")
    parser.add_argument("--heavy-ratio", type=float, default=0.2,
                        help="重量请求占比 0.0-1.0 (default: 0.2)")
    parser.add_argument("--budget", type=int, default=100,
                        help="统一预算值 (default: 100)")
    parser.add_argument("--budget-groups", type=str, default=None,
                        help="预算组, 如 '10:50,100:50' (覆盖 --budget)")
    parser.add_argument("--step-stages", type=str, default="0:10:10,10:80:20,30:10:20",
                        help="Step 阶段: 'start:qps:dur,...' (default: 0:10:10,10:80:20,30:10:20)")
    parser.add_argument("--concurrency", type=int, default=100,
                        help="最大并发请求数 (default: 100)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出 CSV 路径")
    parser.add_argument("--cpu-affinity", type=str, default="0,1",
                        help="CPU 核心绑定, 如 '0,1' (default: 0,1)")
    parser.add_argument("--heavy-burn-ms", type=int, default=800,
                        help="mock_heavy CPU 烧录时间/ms (default: 800)")
    args = parser.parse_args()

    # 设置 CPU 亲和性
    if args.cpu_affinity:
        cores = [int(c.strip()) for c in args.cpu_affinity.split(",")]
        set_cpu_affinity(cores)

    # 更新 heavy tool 参数
    HEAVY_TOOL["arguments"]["cpu_burn_ms"] = args.heavy_burn_ms

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
