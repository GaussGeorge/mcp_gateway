"""
dag_load_generator.py — MCP-DP DAG 会话发压机
==================================================
Step 2 实现：支持双模式、状态机管理、Raw/Effective 分离统计

核心能力：
  1. 会话状态机: PENDING → RUNNING → SUCCESS / CASCADE_FAILED / REJECTED_AT_STEP_0
  2. 双模式支持:
     - Plan-and-Solve: Header 带 X-Plan-DAG / X-Session-ID / X-Total-Budget
     - ReAct: 不带 X-Plan-DAG, 随机决定步数
  3. 串行依赖执行: 前一步返回 200 再发下一步, 失败立即终止
  4. Raw/Effective 分离统计:
     - Raw:       单步成功即累加该工具权重
     - Effective: 仅全链路成功才累加总权重
  5. 随机种子锁死: random.seed(42); np.random.seed(42)

用法:
  # P&S + ReAct 混合流量 (默认 50% / 50%)
  python scripts/dag_load_generator.py \\
      --target http://127.0.0.1:9005 \\
      --sessions 100 --concurrency 20 \\
      --budget 500 --duration 60 \\
      --output results/mcpdp_dag_test.csv

  # 纯 P&S 模式
  python scripts/dag_load_generator.py \\
      --target http://127.0.0.1:9005 \\
      --sessions 200 --ps-ratio 1.0 \\
      --budget 1000 --duration 120

  # 纯 ReAct 模式
  python scripts/dag_load_generator.py \\
      --target http://127.0.0.1:9005 \\
      --sessions 200 --ps-ratio 0.0 \\
      --budget 100 --duration 60

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
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ══════════════════════════════════════════════════
# 1. 固定随机种子 — 统计严谨性
# ══════════════════════════════════════════════════
SEED = 42
random.seed(SEED)
np.random.seed(SEED)


# ══════════════════════════════════════════════════
# 2. CPU 亲和性
# ══════════════════════════════════════════════════
def set_cpu_affinity(cores: List[int]):
    if platform.system() != "Windows":
        return
    try:
        import psutil
        psutil.Process(os.getpid()).cpu_affinity(cores)
        print(f"[DAG发压机] CPU 亲和性: cores={cores}")
    except ImportError:
        print("[DAG发压机] psutil 未安装, 跳过绑核")
    except Exception as e:
        print(f"[DAG发压机] 绑核失败: {e}")


# ══════════════════════════════════════════════════
# 3. 工具定义与权重
# ══════════════════════════════════════════════════
TOOL_CATALOG = {
    "calculate":   {"weight": 1, "arguments": {"operation": "multiply", "a": 17, "b": 23}},
    "web_fetch":   {"weight": 1, "arguments": {"url": "https://example.com/doc", "max_length": 500, "simulate_rtt_ms": 150}},
    "mock_heavy":  {"weight": 5, "arguments": {"cpu_burn_ms": 800, "memory_mb": 0}},
}

LIGHT_TOOLS = ["calculate", "web_fetch"]
HEAVY_TOOLS = ["mock_heavy"]
ALL_TOOLS = LIGHT_TOOLS + HEAVY_TOOLS

# 单步超时（秒），由 --step-timeout 覆盖
_STEP_TIMEOUT: float = 120.0


# ══════════════════════════════════════════════════
# 3.5 客户端价格表（预测性准入）
# ══════════════════════════════════════════════════
import threading as _threading

class ClientPriceTable:
    """线程安全的工具价格缓存，带 TTL 过期机制。

    每次工具调用返回后更新 (tool_name → price, timestamp)。
    查询时如果缓存已过期(>ttl_sec)，返回 None 表示未知（放行给网关决定）。
    """

    def __init__(self, ttl_sec: float = 1.0):
        self._lock = _threading.Lock()
        self._table: Dict[str, Tuple[float, float]] = {}  # tool -> (price, ts)
        self.ttl = ttl_sec
        # 统计计数器
        self.shadow_reject_count = 0   # shadow 模式下客户端"本可拒绝"的次数
        self.shadow_match_count = 0    # shadow 拒绝 与 网关真实拒绝 重合的次数
        self.shadow_total_checks = 0   # 总检查次数

    def update(self, tool_name: str, price: float):
        """更新某个工具的最新已知价格"""
        with self._lock:
            self._table[tool_name] = (price, time.time())

    def get(self, tool_name: str) -> Optional[float]:
        """获取工具价格；过期则返回 None"""
        with self._lock:
            entry = self._table.get(tool_name)
            if entry is None:
                return None
            price, ts = entry
            if time.time() - ts > self.ttl:
                return None  # 过期 → fallback 让网关判
            return price

    def estimate_session_cost(self, plan) -> Optional[float]:
        """估算整个会话的总成本。如果任一工具价格未知/过期，返回 None（放行）"""
        total = 0.0
        for step in plan.steps:
            p = self.get(step.tool_name)
            if p is None:
                return None  # 有未知价格 → 不做预测
            total += p * TOOL_CATALOG[step.tool_name]["weight"]
        return total

    def estimate_step_cost(self, tool_name: str) -> Optional[float]:
        """估算单步成本（ReAct 模式用）"""
        p = self.get(tool_name)
        if p is None:
            return None
        return p * TOOL_CATALOG[tool_name]["weight"]

    def get_stats(self) -> Dict[str, int]:
        return {
            "shadow_reject": self.shadow_reject_count,
            "shadow_match": self.shadow_match_count,
            "shadow_total_checks": self.shadow_total_checks,
        }


# 全局实例，在 run() 中根据 CLI 参数初始化
_price_table: Optional[ClientPriceTable] = None
_hard_reject_enabled: bool = False


# ══════════════════════════════════════════════════
# 4. 会话状态机
# ══════════════════════════════════════════════════
class SessionState(Enum):
    PENDING          = "PENDING"
    RUNNING          = "RUNNING"
    SUCCESS          = "SUCCESS"
    CASCADE_FAILED   = "CASCADE_FAILED"
    REJECTED_AT_STEP_0 = "REJECTED_AT_STEP_0"


class AgentMode(Enum):
    PLAN_AND_SOLVE = "plan_and_solve"
    REACT          = "react"


@dataclass
class DAGStep:
    step_id:    str
    tool_name:  str
    depends_on: List[str] = field(default_factory=list)


@dataclass
class SessionPlan:
    session_id: str
    mode:       AgentMode
    steps:      List[DAGStep]
    budget:     int
    state:      SessionState = SessionState.PENDING
    # 统计
    step_results:  List[dict] = field(default_factory=list)
    raw_goodput:   float = 0.0   # 单步成功即累加
    total_weight:  float = 0.0   # 全链路总权重
    start_time:    float = 0.0
    end_time:      float = 0.0


@dataclass
class StepResult:
    session_id:  str
    step_id:     str
    tool_name:   str
    status:      str   # "success" | "rejected" | "error"
    latency_ms:  float
    budget:      int
    tokens:      int
    mode:        str
    locked_price: str = ""
    regime:      str = ""
    timestamp:   float = 0.0


# ══════════════════════════════════════════════════
# 5. DAG 生成器
# ══════════════════════════════════════════════════
def generate_ps_session(session_id: str, budget: int, heavy_ratio: float,
                        min_steps: int = 3, max_steps: int = 7) -> SessionPlan:
    """生成 Plan-and-Solve 会话: 串行 DAG"""
    n_steps = random.randint(min_steps, max_steps)
    steps = []
    for i in range(n_steps):
        if random.random() < heavy_ratio:
            tool = random.choice(HEAVY_TOOLS)
        else:
            tool = random.choice(LIGHT_TOOLS)
        sid = f"s{i+1}"
        dep = [f"s{i}"] if i > 0 else []
        steps.append(DAGStep(step_id=sid, tool_name=tool, depends_on=dep))

    return SessionPlan(
        session_id=session_id,
        mode=AgentMode.PLAN_AND_SOLVE,
        steps=steps,
        budget=budget,
    )


def generate_react_session(session_id: str, budget: int, heavy_ratio: float,
                           min_steps: int = 1, max_steps: int = 5) -> SessionPlan:
    """生成 ReAct 会话: 逐步随机决定"""
    n_steps = random.randint(min_steps, max_steps)
    steps = []
    for i in range(n_steps):
        if random.random() < heavy_ratio:
            tool = random.choice(HEAVY_TOOLS)
        else:
            tool = random.choice(LIGHT_TOOLS)
        sid = f"r{i+1}"
        dep = [f"r{i}"] if i > 0 else []
        steps.append(DAGStep(step_id=sid, tool_name=tool, depends_on=dep))

    return SessionPlan(
        session_id=session_id,
        mode=AgentMode.REACT,
        steps=steps,
        budget=budget,
    )


# ══════════════════════════════════════════════════
# 6. HTTP 请求发送
# ══════════════════════════════════════════════════
async def send_tool_call(
    session: aiohttp.ClientSession,
    url: str,
    plan: SessionPlan,
    step_idx: int,
    req_id: int,
) -> StepResult:
    """发送单步工具调用"""
    step = plan.steps[step_idx]
    tool = TOOL_CATALOG[step.tool_name]
    tokens = plan.budget  # 每步携带全部预算

    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": step.tool_name,
            "arguments": tool["arguments"],
            "_meta": {
                "tokens": tokens,
                "name": f"dag-client-{plan.session_id}",
                "method": step.tool_name,
            },
        },
    }

    headers = {"Content-Type": "application/json"}

    # 所有模式均发送 Session-ID（ReAct 模式需要用于沉没成本会话跟踪）
    headers["X-Session-ID"] = plan.session_id

    # P&S 模式: 首步额外带完整 DAG
    if plan.mode == AgentMode.PLAN_AND_SOLVE:
        if step_idx == 0:
            dag_json = {
                "session_id": plan.session_id,
                "steps": [
                    {
                        "step_id": s.step_id,
                        "tool_name": s.tool_name,
                        "depends_on": s.depends_on,
                    }
                    for s in plan.steps
                ],
                "budget": plan.budget,
            }
            headers["X-Plan-DAG"] = json.dumps(dag_json)
            headers["X-Total-Budget"] = str(plan.budget)

    ts = time.time()
    try:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=_STEP_TIMEOUT),
        ) as resp:
            body = await resp.json()
            latency = (time.time() - ts) * 1000

            locked_price = ""
            regime = ""

            if "error" in body and body["error"] is not None:
                code = body["error"].get("code", 0)
                data = body["error"].get("data")
                if isinstance(data, dict):
                    regime = data.get("regime", "")
                    locked_price = str(data.get("locked_price", ""))

                if code in (-32001, -32002, -32003):
                    status = "rejected"
                else:
                    status = "error"
            else:
                status = "success"
                result = body.get("result")
                if isinstance(result, dict):
                    meta = result.get("_meta") or {}
                    regime = meta.get("regime", "")
                    locked_price = str(meta.get("price", ""))

            # 更新客户端价格表（无论成功/失败都尝试拿价格）
            if _price_table is not None and locked_price:
                try:
                    _price_table.update(step.tool_name, float(locked_price))
                except (ValueError, TypeError):
                    pass

            return StepResult(
                session_id=plan.session_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                status=status,
                latency_ms=latency,
                budget=plan.budget,
                tokens=tokens,
                mode=plan.mode.value,
                locked_price=locked_price,
                regime=regime,
                timestamp=ts,
            )

    except asyncio.TimeoutError:
        latency = (time.time() - ts) * 1000
        return StepResult(
            session_id=plan.session_id, step_id=step.step_id,
            tool_name=step.tool_name, status="error",
            latency_ms=latency, budget=plan.budget, tokens=tokens,
            mode=plan.mode.value, timestamp=ts,
        )
    except Exception:
        latency = (time.time() - ts) * 1000
        return StepResult(
            session_id=plan.session_id, step_id=step.step_id,
            tool_name=step.tool_name, status="error",
            latency_ms=latency, budget=plan.budget, tokens=tokens,
            mode=plan.mode.value, timestamp=ts,
        )


# ══════════════════════════════════════════════════
# 7. 会话执行器 (串行依赖)
# ══════════════════════════════════════════════════
async def execute_session(
    http_session: aiohttp.ClientSession,
    url: str,
    plan: SessionPlan,
    req_counter: List[int],
) -> SessionPlan:
    """
    执行单个会话的全部步骤（串行依赖）
    前一步 200 才发下一步, 失败立即终止
    """
    plan.state = SessionState.RUNNING
    plan.start_time = time.time()

    # 客户端价格预测（Shadow / Hard Reject）
    shadow_would_reject = False

    for i, step in enumerate(plan.steps):
        # ── 客户端预测性准入 ──
        if _price_table is not None:
            _price_table.shadow_total_checks += 1
            client_reject_this = False
            if plan.mode == AgentMode.PLAN_AND_SOLVE and i == 0:
                est = _price_table.estimate_session_cost(plan)
                if est is not None and est > plan.budget:
                    client_reject_this = True
            elif plan.mode == AgentMode.REACT:
                est = _price_table.estimate_step_cost(step.tool_name)
                if est is not None and est > plan.budget:
                    client_reject_this = True

            if client_reject_this:
                shadow_would_reject = True
                _price_table.shadow_reject_count += 1
                # Hard Reject: 本地直接拒绝，不发送网络请求
                if _hard_reject_enabled:
                    if i == 0:
                        plan.state = SessionState.REJECTED_AT_STEP_0
                    else:
                        plan.state = SessionState.CASCADE_FAILED
                    plan.end_time = time.time()
                    plan.step_results.append({
                        "step_id": step.step_id,
                        "tool_name": step.tool_name,
                        "status": "client_rejected",
                        "latency_ms": 0.0,
                        "locked_price": None,
                        "regime": None,
                        "timestamp": time.time(),
                        "shadow_would_reject": True,
                    })
                    _price_table.shadow_match_count += 1
                    return plan

        req_counter[0] += 1
        result = await send_tool_call(http_session, url, plan, i, req_counter[0])
        plan.step_results.append({
            "step_id": result.step_id,
            "tool_name": result.tool_name,
            "status": result.status,
            "latency_ms": result.latency_ms,
            "locked_price": result.locked_price,
            "regime": result.regime,
            "timestamp": result.timestamp,
            "shadow_would_reject": shadow_would_reject,
        })

        tool_weight = TOOL_CATALOG[step.tool_name]["weight"]

        if result.status == "success":
            # Raw goodput: 单步成功即累加
            plan.raw_goodput += tool_weight
        else:
            # Shadow 命中统计：客户端预测拒绝 且 网关真实拒绝
            if _price_table is not None and shadow_would_reject:
                _price_table.shadow_match_count += 1
            # 失败: 判断是 step_0 拒绝还是级联失败
            if i == 0:
                plan.state = SessionState.REJECTED_AT_STEP_0
            else:
                plan.state = SessionState.CASCADE_FAILED
            plan.end_time = time.time()
            return plan

    # 全部步骤成功
    plan.state = SessionState.SUCCESS
    plan.end_time = time.time()

    # Effective goodput: 仅全链路成功时累加总权重
    for step in plan.steps:
        plan.total_weight += TOOL_CATALOG[step.tool_name]["weight"]

    return plan


# ══════════════════════════════════════════════════
# 8. 统计汇总
# ══════════════════════════════════════════════════
@dataclass
class AggregatedStats:
    total_sessions:     int = 0
    success_sessions:   int = 0
    rejected_at_step0:  int = 0
    cascade_failed:     int = 0
    pending:            int = 0
    # 分模式统计
    ps_total:           int = 0
    ps_success:         int = 0
    ps_rejected:        int = 0
    ps_cascade:         int = 0
    react_total:        int = 0
    react_success:      int = 0
    react_rejected:     int = 0
    react_cascade:      int = 0
    # 吞吐量指标
    raw_goodput_total:      float = 0.0
    effective_goodput_total: float = 0.0
    # 延迟
    all_latencies:      List[float] = field(default_factory=list)
    session_e2e_latencies: List[float] = field(default_factory=list)
    total_steps:        int = 0
    total_step_success: int = 0
    elapsed_seconds:    float = 0.0
    # 客户端价格预测统计
    client_shadow_reject:  int = 0   # 客户端预测应拒绝的次数
    client_shadow_match:   int = 0   # 预测拒绝与网关真实拒绝重合次数
    client_shadow_checks:  int = 0   # 总检查次数


def compute_stats(plans: List[SessionPlan], elapsed: float) -> AggregatedStats:
    stats = AggregatedStats(elapsed_seconds=elapsed)
    stats.total_sessions = len(plans)

    for plan in plans:
        is_ps = plan.mode == AgentMode.PLAN_AND_SOLVE

        if is_ps:
            stats.ps_total += 1
        else:
            stats.react_total += 1

        if plan.state == SessionState.SUCCESS:
            stats.success_sessions += 1
            if is_ps:
                stats.ps_success += 1
            else:
                stats.react_success += 1
        elif plan.state == SessionState.REJECTED_AT_STEP_0:
            stats.rejected_at_step0 += 1
            if is_ps:
                stats.ps_rejected += 1
            else:
                stats.react_rejected += 1
        elif plan.state == SessionState.CASCADE_FAILED:
            stats.cascade_failed += 1
            if is_ps:
                stats.ps_cascade += 1
            else:
                stats.react_cascade += 1
        else:
            stats.pending += 1

        stats.raw_goodput_total += plan.raw_goodput
        stats.effective_goodput_total += plan.total_weight

        # 全链路端到端延迟（仅成功会话）
        if plan.state == SessionState.SUCCESS and plan.end_time > plan.start_time:
            e2e_ms = (plan.end_time - plan.start_time) * 1000.0
            stats.session_e2e_latencies.append(e2e_ms)

        for sr in plan.step_results:
            stats.total_steps += 1
            stats.all_latencies.append(sr["latency_ms"])
            if sr["status"] == "success":
                stats.total_step_success += 1

    # 客户端价格预测统计
    if _price_table is not None:
        pt_stats = _price_table.get_stats()
        stats.client_shadow_reject = pt_stats["shadow_reject"]
        stats.client_shadow_match = pt_stats["shadow_match"]
        stats.client_shadow_checks = pt_stats["shadow_total_checks"]

    return stats


def print_stats(stats: AggregatedStats):
    print(f"\n{'='*60}")
    print(f"  DAG 会话发压机 — 统计摘要")
    print(f"{'='*60}")
    print(f"  总会话数:        {stats.total_sessions}")
    print(f"  ├─ SUCCESS:      {stats.success_sessions}  ({_pct(stats.success_sessions, stats.total_sessions)})")
    print(f"  ├─ REJECTED@S0:  {stats.rejected_at_step0}  ({_pct(stats.rejected_at_step0, stats.total_sessions)})")
    print(f"  ├─ CASCADE_FAIL: {stats.cascade_failed}  ({_pct(stats.cascade_failed, stats.total_sessions)})")
    print(f"  └─ PENDING:      {stats.pending}")

    print(f"\n  [Plan-and-Solve]  total={stats.ps_total}  success={stats.ps_success}  rejected@s0={stats.ps_rejected}  cascade={stats.ps_cascade}")
    print(f"  [ReAct]           total={stats.react_total}  success={stats.react_success}  rejected@s0={stats.react_rejected}  cascade={stats.react_cascade}")

    print(f"\n  ── Goodput 指标 ──")
    print(f"  Raw Goodput (单步累加):       {stats.raw_goodput_total:.1f}")
    print(f"  Effective Goodput (全链路):    {stats.effective_goodput_total:.1f}")
    if stats.elapsed_seconds > 0:
        print(f"  Raw Goodput/s:                {stats.raw_goodput_total / stats.elapsed_seconds:.2f}")
        print(f"  Effective Goodput/s:          {stats.effective_goodput_total / stats.elapsed_seconds:.2f}")

    print(f"\n  ── 步骤级统计 ──")
    print(f"  总步骤数:          {stats.total_steps}")
    print(f"  步骤成功数:        {stats.total_step_success}  ({_pct(stats.total_step_success, stats.total_steps)})")
    print(f"  会话吞吐量:        {stats.total_sessions / max(stats.elapsed_seconds, 0.001):.1f} sessions/s")

    if stats.all_latencies:
        lats = sorted(stats.all_latencies)
        p50 = lats[len(lats) // 2]
        p95 = lats[int(len(lats) * 0.95)]
        p99 = lats[int(len(lats) * 0.99)]
        print(f"\n  ── 延迟 (ms) ──")
        print(f"  P50:   {p50:.1f}")
        print(f"  P95:   {p95:.1f}")
        print(f"  P99:   {p99:.1f}")
        print(f"  Mean:  {sum(lats)/len(lats):.1f}")

    if stats.session_e2e_latencies:
        e2e = sorted(stats.session_e2e_latencies)
        e2e_p50 = e2e[len(e2e) // 2]
        e2e_p95 = e2e[int(len(e2e) * 0.95)]
        e2e_p99 = e2e[int(len(e2e) * 0.99)]
        print(f"\n  ── 会话端到端延迟 (ms, 仅成功会话) ──")
        print(f"  E2E_P50:   {e2e_p50:.1f}")
        print(f"  E2E_P95:   {e2e_p95:.1f}")
        print(f"  E2E_P99:   {e2e_p99:.1f}")
        print(f"  E2E_Mean:  {sum(e2e)/len(e2e):.1f}")

    # 客户端价格预测统计
    if stats.client_shadow_checks > 0:
        match_rate = _pct(stats.client_shadow_match, stats.client_shadow_reject) if stats.client_shadow_reject > 0 else "N/A"
        mode_label = "Hard Reject" if _hard_reject_enabled else "Shadow Mode"
        print(f"\n  ── 客户端预测准入 ({mode_label}) ──")
        print(f"  总检查次数:        {stats.client_shadow_checks}")
        print(f"  Client 拒绝次数:   {stats.client_shadow_reject}  ({_pct(stats.client_shadow_reject, stats.client_shadow_checks)})")
        print(f"  Client→网关命中:   {stats.client_shadow_match}  (命中率={match_rate})")
        saved = stats.client_shadow_reject
        print(f"  Client_Saved_Requests: {saved}")

    print(f"{'='*60}")


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100 * n / total:.1f}%"


# ══════════════════════════════════════════════════
# 9. CSV 落盘
# ══════════════════════════════════════════════════
def save_step_csv(plans: List[SessionPlan], path: str):
    """每步一行的详细 CSV"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "session_id", "mode", "session_state",
            "step_id", "tool_name", "status", "latency_ms",
            "budget", "locked_price", "regime",
            "raw_goodput", "effective_goodput", "shadow_would_reject",
        ])
        for plan in plans:
            for sr in plan.step_results:
                writer.writerow([
                    f"{sr['timestamp']:.6f}",
                    plan.session_id,
                    plan.mode.value,
                    plan.state.value,
                    sr["step_id"],
                    sr["tool_name"],
                    sr["status"],
                    f"{sr['latency_ms']:.2f}",
                    plan.budget,
                    sr.get("locked_price", ""),
                    sr.get("regime", ""),
                    f"{plan.raw_goodput:.1f}",
                    f"{plan.total_weight:.1f}",
                    sr.get("shadow_would_reject", False),
                ])


def save_session_csv(plans: List[SessionPlan], path: str):
    """每会话一行的汇总 CSV"""
    session_path = path.replace(".csv", "_sessions.csv")
    os.makedirs(os.path.dirname(session_path) or ".", exist_ok=True)
    with open(session_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "session_id", "mode", "state", "n_steps",
            "budget", "raw_goodput", "effective_goodput",
            "total_latency_ms", "start_time", "end_time",
        ])
        for plan in plans:
            total_lat = sum(sr["latency_ms"] for sr in plan.step_results)
            writer.writerow([
                plan.session_id,
                plan.mode.value,
                plan.state.value,
                len(plan.steps),
                plan.budget,
                f"{plan.raw_goodput:.1f}",
                f"{plan.total_weight:.1f}",
                f"{total_lat:.2f}",
                f"{plan.start_time:.6f}",
                f"{plan.end_time:.6f}",
            ])


# ══════════════════════════════════════════════════
# 10. 主调度器
# ══════════════════════════════════════════════════
async def run(args):
    # 初始化客户端价格表
    global _price_table
    global _hard_reject_enabled
    if args.price_ttl > 0:
        _price_table = ClientPriceTable(ttl_sec=args.price_ttl)
        _hard_reject_enabled = args.hard_reject
        mode_str = "Hard Reject" if _hard_reject_enabled else "Shadow Mode"
        print(f"[DAG发压机] 客户端价格预测: ON (TTL={args.price_ttl}s, {mode_str})")
    else:
        _price_table = None
        _hard_reject_enabled = False

    # 生成会话计划
    plans: List[SessionPlan] = []
    for i in range(args.sessions):
        sid = f"sess-{i:04d}-{uuid.uuid4().hex[:8]}"
        if random.random() < args.ps_ratio:
            plan = generate_ps_session(
                sid, args.budget, args.heavy_ratio,
                min_steps=args.min_steps, max_steps=args.max_steps,
            )
        else:
            plan = generate_react_session(
                sid, args.budget, args.heavy_ratio,
                min_steps=1, max_steps=args.max_steps,
            )
        plans.append(plan)

    # 统计模式分布
    ps_count = sum(1 for p in plans if p.mode == AgentMode.PLAN_AND_SOLVE)
    react_count = len(plans) - ps_count
    total_steps = sum(len(p.steps) for p in plans)

    print(f"[DAG发压机] 目标: {args.target}")
    print(f"[DAG发压机] 总会话: {args.sessions}  (P&S={ps_count}, ReAct={react_count})")
    print(f"[DAG发压机] 总步骤: {total_steps}")
    print(f"[DAG发压机] 预算: {args.budget}")
    print(f"[DAG发压机] 重载比: {args.heavy_ratio*100:.0f}%")
    print(f"[DAG发压机] 并发: {args.concurrency}")
    print(f"[DAG发压机] 种子: {SEED}")

    # Poisson 到达间隔
    if args.arrival_rate > 0:
        mean_interval = 1.0 / args.arrival_rate
    else:
        mean_interval = 0.0

    semaphore = asyncio.Semaphore(args.concurrency)
    req_counter = [0]  # mutable counter
    completed_plans: List[SessionPlan] = []
    lock = asyncio.Lock()

    connector = aiohttp.TCPConnector(
        limit=args.concurrency, limit_per_host=args.concurrency,
    )

    async def run_one(plan: SessionPlan):
        async with semaphore:
            result = await execute_session(http_session, args.target, plan, req_counter)
            async with lock:
                completed_plans.append(result)

    start = time.time()

    async with aiohttp.ClientSession(connector=connector) as http_session:
        tasks = []
        for plan in plans:
            # 检查时间限制
            if args.duration > 0 and (time.time() - start) > args.duration:
                break

            tasks.append(asyncio.create_task(run_one(plan)))

            # Poisson 到达
            if mean_interval > 0:
                interval = np.random.exponential(mean_interval)
                await asyncio.sleep(interval)

        # 等待所有会话完成
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - start

    # 统计
    stats = compute_stats(completed_plans, elapsed)
    print_stats(stats)

    # 保存
    if args.output:
        save_step_csv(completed_plans, args.output)
        save_session_csv(completed_plans, args.output)
        print(f"\n[DAG发压机] 步骤级数据: {args.output}")
        print(f"[DAG发压机] 会话级数据: {args.output.replace('.csv', '_sessions.csv')}")
        print(f"[DAG发压机] 总记录: {sum(len(p.step_results) for p in completed_plans)} 步 / {len(completed_plans)} 会话")


def main():
    parser = argparse.ArgumentParser(
        description="MCP-DP DAG 会话发压机 — 双模式 + 状态机 + Raw/Effective 分离",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", required=True,
                        help="目标网关 URL, 如 http://127.0.0.1:9005")
    parser.add_argument("--sessions", type=int, default=100,
                        help="总会话数 (default: 100)")
    parser.add_argument("--ps-ratio", type=float, default=0.5,
                        help="Plan-and-Solve 模式占比 0.0-1.0 (default: 0.5)")
    parser.add_argument("--budget", type=int, default=500,
                        help="每个会话的预算 (default: 500)")
    parser.add_argument("--heavy-ratio", type=float, default=0.2,
                        help="重量工具 (mock_heavy) 占比 (default: 0.2)")
    parser.add_argument("--min-steps", type=int, default=3,
                        help="P&S 模式最小步数 (default: 3)")
    parser.add_argument("--max-steps", type=int, default=7,
                        help="最大步数 (default: 7)")
    parser.add_argument("--step-timeout", type=float, default=120.0,
                        help="单步调用超时秒数 (default: 120.0)")
    parser.add_argument("--concurrency", type=int, default=20,
                        help="最大并发会话数 (default: 20)")
    parser.add_argument("--arrival-rate", type=float, default=10.0,
                        help="会话到达速率 (sessions/s, Poisson), 0=无限制 (default: 10)")
    parser.add_argument("--duration", type=float, default=0,
                        help="时间限制/秒, 0=不限 (default: 0)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出 CSV 路径")
    parser.add_argument("--cpu-affinity", type=str, default="0,1",
                        help="CPU 核心绑定 (default: 0,1)")
    parser.add_argument("--heavy-burn-ms", type=int, default=800,
                        help="mock_heavy CPU 烧录时间/ms (default: 800)")
    parser.add_argument("--price-ttl", type=float, default=1.0,
                        help="客户端价格缓存 TTL/秒, 0=禁用价格预测 (default: 1.0)")
    parser.add_argument("--hard-reject", action="store_true", default=False,
                        help="启用客户端 Hard Reject: 价格预测超预算则本地拒绝，不发送请求")
    args = parser.parse_args()

    # CPU 亲和性
    if args.cpu_affinity:
        cores = [int(c.strip()) for c in args.cpu_affinity.split(",")]
        set_cpu_affinity(cores)

    # 更新 heavy tool 参数
    TOOL_CATALOG["mock_heavy"]["arguments"]["cpu_burn_ms"] = args.heavy_burn_ms
    # 更新步骤超时
    global _STEP_TIMEOUT
    _STEP_TIMEOUT = args.step_timeout

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
