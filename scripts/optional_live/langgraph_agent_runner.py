"""
langgraph_agent_runner.py — LangGraph Agent 集成实验运行器
===========================================================
Exp-Real-2: 对比 LangGraph 原生 (直连后端) vs LangGraph + PlanGate (经网关)

两种模式:
  - direct:   Agent 直接调用 MCP 后端工具 (无治理)
  - governed: Agent 通过 PlanGate 网关调用工具 (外部信号治理)

每个 Agent 执行一个多步 ReAct 任务:
  1. 查询天气 → 2. 搜索相关信息 → 3. LLM 总结
  并发多个 Agent 模拟真实负载。

用法:
  python scripts/langgraph_agent_runner.py \\
      --target http://127.0.0.1:8080 \\
      --mode direct --sessions 20 --concurrency 10 \\
      --output results/langgraph_direct.csv
"""

import argparse
import asyncio
import csv
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List

import aiohttp


# ══════════════════════════════════════════════════
# Agent Task Templates
# ══════════════════════════════════════════════════
AGENT_TASKS = [
    {
        "name": "weather_report",
        "description": "查询城市天气并总结",
        "steps": [
            {"tool": "real_weather", "args": {"city": "Beijing", "format": "brief"}},
            {"tool": "deepseek_llm", "args": {"operation": "summarize", "text": "{{prev_result}}", "max_tokens": 150}},
        ],
    },
    {
        "name": "research_task",
        "description": "搜索信息并分析",
        "steps": [
            {"tool": "real_web_search", "args": {"query": "LLM tool governance 2024", "max_results": 3}},
            {"tool": "deepseek_llm", "args": {"operation": "reason", "text": "{{prev_result}}", "max_tokens": 200}},
        ],
    },
    {
        "name": "full_pipeline",
        "description": "完整流水线: 计算 → 搜索 → 天气 → 总结",
        "steps": [
            {"tool": "calculate", "args": {"operation": "multiply", "a": 42, "b": 17}},
            {"tool": "real_web_search", "args": {"query": "dynamic pricing algorithms", "max_results": 2}},
            {"tool": "real_weather", "args": {"city": "Tokyo", "format": "brief"}},
            {"tool": "deepseek_llm", "args": {"operation": "summarize", "text": "{{prev_result}}", "max_tokens": 200}},
        ],
    },
    {
        "name": "code_analysis",
        "description": "搜索技术方案并生成代码",
        "steps": [
            {"tool": "real_web_search", "args": {"query": "rate limiting token bucket implementation", "max_results": 2}},
            {"tool": "deepseek_llm", "args": {"operation": "code", "text": "Implement a token bucket rate limiter in Python", "max_tokens": 300}},
        ],
    },
]


@dataclass
class AgentResult:
    session_id: str
    task_name: str
    mode: str
    success: bool
    steps_completed: int
    total_steps: int
    total_latency_ms: float
    step_details: List[dict] = field(default_factory=list)
    error: str = ""


# ══════════════════════════════════════════════════
# MCP Tool Call via JSON-RPC
# ══════════════════════════════════════════════════
async def call_tool(
    http_session: aiohttp.ClientSession,
    target_url: str,
    session_id: str,
    tool_name: str,
    arguments: dict,
    tokens: int = 500,
) -> dict:
    """通过 MCP JSON-RPC 协议调用工具 (可经网关或直连)。"""
    payload = {
        "jsonrpc": "2.0",
        "id": f"lg-{uuid.uuid4().hex[:8]}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
            "_meta": {
                "tokens": tokens,
                "name": f"langgraph-agent-{session_id}",
                "method": tool_name,
            },
        },
    }

    headers = {
        "Content-Type": "application/json",
        "X-Session-ID": session_id,
    }

    start = time.time()
    try:
        async with http_session.post(
            target_url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            body = await resp.json()
            latency = (time.time() - start) * 1000

            if "error" in body and body["error"] is not None:
                return {
                    "success": False,
                    "error": body["error"].get("message", "unknown"),
                    "latency_ms": latency,
                    "result_text": "",
                }

            result = body.get("result", {})
            content = result.get("content", [])
            text = content[0].get("text", "") if content else ""

            return {
                "success": True,
                "error": "",
                "latency_ms": latency,
                "result_text": text[:500],  # truncate for safety
            }

    except Exception as e:
        latency = (time.time() - start) * 1000
        return {
            "success": False,
            "error": str(e),
            "latency_ms": latency,
            "result_text": "",
        }


# ══════════════════════════════════════════════════
# Agent Executor
# ══════════════════════════════════════════════════
async def run_agent(
    http_session: aiohttp.ClientSession,
    target_url: str,
    session_id: str,
    task: dict,
    mode: str,
) -> AgentResult:
    """执行单个 Agent 的多步任务。"""
    result = AgentResult(
        session_id=session_id,
        task_name=task["name"],
        mode=mode,
        success=False,
        steps_completed=0,
        total_steps=len(task["steps"]),
        total_latency_ms=0,
    )

    prev_result = ""

    for i, step in enumerate(task["steps"]):
        # 替换 {{prev_result}} 占位符
        args = dict(step["args"])
        for key, val in args.items():
            if isinstance(val, str) and "{{prev_result}}" in val:
                args[key] = val.replace("{{prev_result}}", prev_result[:300])

        step_result = await call_tool(
            http_session, target_url, session_id,
            step["tool"], args,
        )

        result.step_details.append({
            "step": i,
            "tool": step["tool"],
            "success": step_result["success"],
            "latency_ms": step_result["latency_ms"],
            "error": step_result["error"],
        })
        result.total_latency_ms += step_result["latency_ms"]

        if step_result["success"]:
            result.steps_completed += 1
            prev_result = step_result["result_text"]
        else:
            result.error = f"Step {i} ({step['tool']}): {step_result['error']}"
            return result

    result.success = True
    return result


# ══════════════════════════════════════════════════
# Batch Runner
# ══════════════════════════════════════════════════
async def run_experiment(args):
    import random
    random.seed(42)

    results: List[AgentResult] = []
    semaphore = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    connector = aiohttp.TCPConnector(
        limit=args.concurrency, limit_per_host=args.concurrency,
    )

    async def run_one(idx: int):
        session_id = f"lg-{idx:04d}-{uuid.uuid4().hex[:8]}"
        task = AGENT_TASKS[idx % len(AGENT_TASKS)]
        async with semaphore:
            result = await run_agent(http_session, args.target, session_id, task, args.mode)
            async with lock:
                results.append(result)

    start = time.time()

    async with aiohttp.ClientSession(connector=connector) as http_session:
        tasks = []
        for i in range(args.sessions):
            tasks.append(asyncio.create_task(run_one(i)))
            # Poisson 到达
            if args.arrival_rate > 0:
                interval = random.expovariate(args.arrival_rate)
                await asyncio.sleep(interval)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - start

    # 统计
    success = sum(1 for r in results if r.success)
    failed = len(results) - success

    print(f"\n{'=' * 50}")
    print(f"  LangGraph Agent 实验 — {args.mode.upper()} 模式")
    print(f"{'=' * 50}")
    print(f"  总 Agent 数:   {len(results)}")
    print(f"  成功:          {success}  ({100 * success / max(len(results), 1):.1f}%)")
    print(f"  失败:          {failed}")
    print(f"  耗时:          {elapsed:.1f}s")

    if results:
        latencies = [r.total_latency_ms for r in results if r.success]
        if latencies:
            latencies.sort()
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95)]
            mean = sum(latencies) / len(latencies)
            print(f"\n  ── Agent 端到端延迟 (ms, 仅成功) ──")
            print(f"  P50:  {p50:.0f}")
            print(f"  P95:  {p95:.0f}")
            print(f"  Mean: {mean:.0f}")

    print(f"{'=' * 50}")

    # 保存 CSV
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "session_id", "task", "mode", "success",
                "steps_completed", "total_steps",
                "total_latency_ms", "error",
            ])
            for r in results:
                writer.writerow([
                    r.session_id, r.task_name, r.mode,
                    r.success, r.steps_completed, r.total_steps,
                    f"{r.total_latency_ms:.1f}", r.error,
                ])
        print(f"  结果已保存: {args.output}")


def main():
    parser = argparse.ArgumentParser(description="LangGraph Agent 集成实验运行器")
    parser.add_argument("--target", required=True, help="目标 URL (后端或网关)")
    parser.add_argument("--mode", choices=["direct", "governed"], default="direct",
                        help="模式: direct=直连后端, governed=经 PlanGate 网关")
    parser.add_argument("--sessions", type=int, default=20, help="Agent 数量")
    parser.add_argument("--concurrency", type=int, default=10, help="最大并发")
    parser.add_argument("--arrival-rate", type=float, default=2.0, help="到达速率 (agents/s)")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出 CSV 路径")
    args = parser.parse_args()

    asyncio.run(run_experiment(args))


if __name__ == "__main__":
    main()
