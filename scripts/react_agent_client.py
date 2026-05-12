"""
react_agent_client.py — 真·ReAct Agent 客户端 (Exp-Real-3)
===========================================================
每个 Agent 接收自然语言任务，通过 LLM function calling 驱动工具选择，
工具调用经 MCP 网关准入控制后到达后端执行。与 Trace-driven 的核心区别:
**LLM 根据上一步的实际返回值决定下一步调哪个工具。**

架构:
  Task Prompt → LLM (function calling)
      → 选择工具 → HTTP POST → 网关 (准入控制)
      → 转发到 MCP 后端 → 返回结果
      → LLM 决定下一步 → ... 直到完成或失败

导师三大细节:
  1. 容错 System Prompt — 遇到拒绝不死循环重试
  2. 异常格式化 — 网关拒绝包装成 Tool Response
  3. asyncio + Semaphore — 精准并发控制

用法:
  python scripts/react_agent_client.py \\
      --gateway http://127.0.0.1:9005 \\
      --agents 50 --concurrency 10 \\
      --gateway-mode mcpdp-real \\
      --output results/exp_real3/plangate.csv
"""

import asyncio
import aiohttp
import argparse
import csv
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ── 加载 .env ──
def _load_dotenv():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if value and len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key and value and key not in os.environ:
                    os.environ[key] = value

_load_dotenv()

# ══════════════════════════════════════════════════
# 1. MCP 工具定义 (OpenAI function calling 格式)
# ══════════════════════════════════════════════════
MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Precise math calculator: add, subtract, multiply, divide, power, sqrt, modulo, abs, log, factorial. Use this for any numeric computation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide",
                                 "power", "sqrt", "modulo", "abs", "log", "factorial"],
                        "description": "Math operation type"
                    },
                    "a": {"type": "number", "description": "First operand"},
                    "b": {"type": "number", "description": "Second operand (not needed for sqrt/abs/log/factorial)"}
                },
                "required": ["operation", "a"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "real_weather",
            "description": "Real weather query via wttr.in API. Returns current temperature, humidity, wind, and conditions for any city worldwide.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name in English (e.g. Beijing, Tokyo, London)"},
                    "format": {"type": "string", "enum": ["brief", "detailed"], "description": "Output format"}
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "real_web_search",
            "description": "Real web search via Tavily/SerpAPI. Returns titles, URLs and snippets. Use ONLY when you genuinely need to look up real-time information that you don't already know.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords"},
                    "max_results": {"type": "integer", "description": "Max results to return (1-5)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "text_format",
            "description": "Text processing: JSON formatting/minifying, regex extract/replace, base64 encode/decode, word/line count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["json_format", "json_minify", "regex_extract", "regex_replace",
                                 "base64_encode", "base64_decode", "word_count", "line_count"],
                        "description": "Text processing operation"
                    },
                    "text": {"type": "string", "description": "Input text"},
                    "pattern": {"type": "string", "description": "Regex pattern (for regex_extract/regex_replace)"},
                    "replacement": {"type": "string", "description": "Replacement text (for regex_replace)"}
                },
                "required": ["operation", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "deepseek_llm",
            "description": "Heavy LLM inference tool for summarization, translation, reasoning, or code generation. Consumes significant resources. Use only when the task specifically requires LLM-level intelligence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["summarize", "translate", "reason", "code"],
                        "description": "Operation type"
                    },
                    "text": {"type": "string", "description": "Input text / problem description"},
                    "max_tokens": {"type": "integer", "description": "Max tokens to generate (50-500)"}
                },
                "required": ["operation", "text"]
            }
        }
    },
]

# 工具权重 (与 mock 实验一致)
TOOL_WEIGHTS = {
    "calculate": 1, "real_weather": 1, "text_format": 1,
    "real_web_search": 2, "deepseek_llm": 5,
}

# ══════════════════════════════════════════════════
# 2. System Prompt — 容错设计 (导师细节1)
# ══════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a helpful assistant that completes tasks by calling available tools step-by-step.

IMPORTANT RULES:
1. You have access to cloud-based tools with LIMITED capacity. Tools may fail due to server congestion.
2. If a tool returns an "overload", "rejected", "congestion" or "timeout" error, DO NOT retry the exact same tool immediately.
   Instead, either:
   a) Try an alternative tool (e.g., use 'calculate' instead of 'deepseek_llm' for simple math), or
   b) Provide the best possible answer using the information you have already gathered.
3. Keep your tool usage efficient — avoid unnecessary calls. Aim to complete the task in 2-5 tool calls.
4. When you have enough information, respond with a final text answer (no more tool calls).
5. Always respond in the same language as the task prompt."""

# ══════════════════════════════════════════════════
# 3. 任务 Prompt 池
# ══════════════════════════════════════════════════
TASK_PROMPTS = [
    # ── 纯计算类 (30%) ── 触发 calculate + text_format
    "计算 (253 × 49) + (1024 / 16)，然后统计结果数字的位数",
    "帮我算一下 12 的阶乘，再算它的平方根，最后把结果保留小数点后两位",
    "计算 2 的 20 次方，然后对结果取模 1000，最后算模值的对数",
    "先算 987654 除以 123，再算商的绝对值，然后统计这串数字有多少位",
    "计算 15 的阶乘和 10 的阶乘，将两个结果相除",
    "算出 256 的平方根，然后乘以 3.14159，再做 base64 编码",

    # ── 天气+计算类 (30%) ── 触发 real_weather + calculate
    "查一下北京和东京的天气，如果温差超过 10 度就计算两城市温度的平均值",
    "查上海的实时天气，然后把摄氏温度转换为华氏温度（公式：F = C × 9/5 + 32）",
    "查询 London 和 Paris 的天气，计算两座城市湿度的差值",
    "查一下 Sydney 的天气，如果温度高于 20 度，计算 20 的平方；否则计算 10 的阶乘",
    "查 Berlin 天气，然后用 text_format 的 word_count 功能统计天气描述的词数",
    "查询 Seoul 和 Mumbai 的天气，比较两地温度，计算较高温度与较低温度的比值",

    # ── 搜索类 (10%) ── 触发 real_web_search (Tavily)
    "搜索 'MCP protocol Model Context Protocol' 最新进展，总结前 3 条结果的标题",
    "搜索 'LLM agent tool calling benchmark 2024' 相关论文，列出找到的论文标题",
    "搜索 'API gateway rate limiting best practices'，简要总结搜索结果",

    # ── LLM推理类 (20%) ── 触发 deepseek_llm
    "用 LLM 工具总结一下什么是「动态定价在微服务中的应用」，不超过 100 字",
    "用 LLM 工具解释 Token Bucket 和 Leaky Bucket 限流算法的区别，要求简明扼要",
    "用 LLM 工具写一段 Python 代码，实现简单的滑动窗口限流器",
    "用 LLM 工具推理：如果一个 API 的 QPS 限制是 100，每个请求平均耗时 50ms，最大并发数是多少？",
    "用 LLM 工具将以下文本翻译成英文：'计划感知的网关能有效避免级联算力浪费'",
    "用 LLM 工具分析：为什么多步 Agent 任务中，中间步骤被拒绝会导致前序计算全部浪费？",

    # ── 多步组合类 (10%) ── 触发 3-4 种工具
    "查北京天气，计算温度的平方，然后用 text_format 把结果做 base64 编码",
    "计算 42 × 58，然后用 LLM 工具解释这个数字在数学中有什么有趣的性质",
    "查 Tokyo 天气，搜索 'Tokyo travel tips'，然后用 LLM 根据天气和搜索结果推荐一个活动",

    # ── 密集多步组合类 ── 触发 4-6 种工具 (bursty 实验更多出现)
    "查北京和上海天气，计算两地温差，再用 LLM 工具解释温差原因，最后搜索'中国天气预报准确率'",
    "计算 2 的 15 次方和 3 的 10 次方，比较大小，用 text_format 做 base64 编码，再用 LLM 翻译结果说明",
    "搜索 'Python asyncio tutorial'，查 Tokyo 天气，计算华氏温度，用 LLM 总结搜索结果和天气信息",
    "查 New York、London、Tokyo 三城天气，计算三城平均温度，用 text_format 统计结果词数",
    "计算 100 的阶乘的位数，搜索'大数阶乘算法'，用 LLM 写验证代码，用 text_format 格式化代码",
    "查 Seoul 天气，计算温度的平方根，用 LLM 分析该温度对出行的影响，搜索 'Seoul travel guide'，用 text_format 统计搜索结果词数",
]


# ══════════════════════════════════════════════════
# 4. 数据结构
# ══════════════════════════════════════════════════
@dataclass
class ToolCallRecord:
    """单次工具调用记录。"""
    step: int
    tool_name: str
    arguments: dict
    status: str           # success / rejected / error / timeout
    latency_ms: float
    llm_tokens_used: int  # 后端 deepseek_llm 工具消耗的 token
    response_text: str    # 工具返回内容 (截断)
    timestamp: float = 0.0
    http_status: int = 0              # HTTP 响应状态码 (429 = rate limited)
    rate_limit_remaining: int = -1    # X-RateLimit-Remaining header (-1 = N/A)
    retry_after: float = 0.0          # Retry-After header (秒)


@dataclass
class AgentResult:
    """单个 Agent 完整运行结果。"""
    agent_id: str
    task_prompt: str
    task_category: str
    state: str            # SUCCESS / PARTIAL / ALL_REJECTED / ERROR
    total_steps: int
    success_steps: int
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    agent_llm_tokens: int = 0       # Agent brain 消耗的 token 总数
    backend_llm_tokens: int = 0     # 后端 deepseek_llm 工具消耗的 token 总数
    total_latency_ms: float = 0.0
    final_answer: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    raw_goodput: float = 0.0
    effective_goodput: float = 0.0


def _categorize_task(prompt: str) -> str:
    """根据 prompt 内容推断任务类别。"""
    if "搜索" in prompt or "search" in prompt.lower():
        return "search"
    if "LLM" in prompt or "翻译" in prompt or "总结" in prompt or "推理" in prompt or "写一段" in prompt:
        return "llm_reasoning"
    if "天气" in prompt or "weather" in prompt.lower():
        return "weather_calc"
    if "查" in prompt and ("计算" in prompt or "算" in prompt):
        return "multi_step"
    return "pure_calc"


# ══════════════════════════════════════════════════
# 5. MCP 网关工具调用 — 异常格式化 (导师细节2)
# ══════════════════════════════════════════════════
async def call_mcp_tool(
    http_session: aiohttp.ClientSession,
    gateway_url: str,
    session_id: str,
    tool_name: str,
    arguments: dict,
    step_idx: int,
    budget: int = 500,
) -> ToolCallRecord:
    """通过 MCP 网关调用工具。网关拒绝时包装为文本返回给 LLM。"""
    payload = {
        "jsonrpc": "2.0",
        "id": step_idx + 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
            "_meta": {
                "tokens": budget,
                "name": f"agent-{session_id}",
                "method": tool_name,
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Session-ID": session_id,
    }

    ts = time.time()
    try:
        async with http_session.post(
            gateway_url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            http_status = resp.status
            rl_remaining = -1
            rl_retry_after = 0.0
            try:
                rl_val = resp.headers.get("X-RateLimit-Remaining", "")
                if rl_val:
                    rl_remaining = int(rl_val)
            except (ValueError, TypeError):
                pass
            try:
                ra_val = resp.headers.get("Retry-After", "")
                if ra_val:
                    rl_retry_after = float(ra_val)
            except (ValueError, TypeError):
                pass

            # Handle 429 Too Many Requests
            if http_status == 429:
                latency = (time.time() - ts) * 1000
                return ToolCallRecord(
                    step=step_idx, tool_name=tool_name,
                    arguments=arguments, status="rejected",
                    latency_ms=latency, llm_tokens_used=0,
                    response_text='{"error": "Rate limited (HTTP 429). Server at quota limit."}',
                    timestamp=ts,
                    http_status=http_status,
                    rate_limit_remaining=rl_remaining,
                    retry_after=rl_retry_after,
                )

            body = await resp.json()
            latency = (time.time() - ts) * 1000
            llm_tokens_used = 0

            if "error" in body and body["error"] is not None:
                # ★ 导师细节2: 网关拒绝 → 包装成工具返回文本
                code = body["error"].get("code", 0)
                msg = body["error"].get("message", "Unknown error")
                if code in (-32001, -32002, -32003):
                    status = "rejected"
                    response_text = (
                        f'{{"error": "Tool call rejected by gateway due to system congestion. '
                        f'Error: {msg}. Try an alternative approach or provide your best answer '
                        f'with the information gathered so far."}}'
                    )
                elif code == -32000:
                    status = "rejected"
                    response_text = (
                        f'{{"error": "Server overloaded — queue timeout. '
                        f'The backend is at full capacity. Use a different tool or conclude."}}'
                    )
                else:
                    status = "error"
                    response_text = f'{{"error": "{msg}"}}'
            else:
                status = "success"
                result = body.get("result", {})
                content_list = result.get("content", [])
                if content_list:
                    response_text = content_list[0].get("text", "{}")
                    # 提取 deepseek_llm 的 token 消耗
                    try:
                        result_obj = json.loads(response_text)
                        if isinstance(result_obj, dict) and "usage" in result_obj:
                            llm_tokens_used = result_obj["usage"].get("total_tokens", 0)
                    except (json.JSONDecodeError, KeyError):
                        pass
                else:
                    response_text = "{}"

            return ToolCallRecord(
                step=step_idx, tool_name=tool_name,
                arguments=arguments, status=status,
                latency_ms=latency, llm_tokens_used=llm_tokens_used,
                response_text=response_text[:2000], timestamp=ts,
                http_status=http_status,
                rate_limit_remaining=rl_remaining,
                retry_after=rl_retry_after,
            )

    except asyncio.TimeoutError:
        latency = (time.time() - ts) * 1000
        return ToolCallRecord(
            step=step_idx, tool_name=tool_name,
            arguments=arguments, status="timeout",
            latency_ms=latency, llm_tokens_used=0,
            response_text='{"error": "Tool call timed out (120s). Server may be overloaded."}',
            timestamp=ts,
        )
    except Exception as e:
        latency = (time.time() - ts) * 1000
        return ToolCallRecord(
            step=step_idx, tool_name=tool_name,
            arguments=arguments, status="error",
            latency_ms=latency, llm_tokens_used=0,
            response_text=f'{{"error": "Connection error: {str(e)[:200]}"}}',
            timestamp=ts,
        )


# ══════════════════════════════════════════════════
# 6. 真·ReAct Agent 执行逻辑
# ══════════════════════════════════════════════════
async def run_agent(
    http_session: aiohttp.ClientSession,
    gateway_url: str,
    agent_id: str,
    task_prompt: str,
    max_steps: int = 8,
    budget: int = 500,
) -> AgentResult:
    """运行单个 ReAct Agent，LLM 驱动工具选择。"""
    from openai import OpenAI
    import httpx

    llm_base = os.getenv("AGENT_LLM_BASE", os.getenv("LLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4"))
    llm_key = os.getenv("AGENT_LLM_KEY", os.getenv("LLM_API_KEY", ""))
    llm_model = os.getenv("AGENT_LLM_MODEL", os.getenv("LLM_MODEL", "glm-4-flash"))

    client = OpenAI(
        api_key=llm_key,
        base_url=llm_base,
        http_client=httpx.Client(proxy=None, timeout=120),
    )

    result = AgentResult(
        agent_id=agent_id,
        task_prompt=task_prompt,
        task_category=_categorize_task(task_prompt),
        state="ERROR",
        total_steps=0,
        success_steps=0,
        start_time=time.time(),
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task_prompt},
    ]

    session_id = f"agent-{agent_id}"
    consecutive_rejects = 0

    for step_idx in range(max_steps):
        # ── 调用 LLM 决定下一步 ──
        try:
            llm_resp = client.chat.completions.create(
                model=llm_model,
                messages=messages,
                tools=MCP_TOOLS,
                tool_choice="auto",
                max_tokens=1024,
                temperature=0.7,
            )
        except Exception as e:
            result.state = "ERROR"
            result.final_answer = f"LLM API error: {str(e)[:200]}"
            break

        # 统计 brain token 消耗
        if llm_resp.usage:
            result.agent_llm_tokens += llm_resp.usage.total_tokens

        choice = llm_resp.choices[0]

        # ── LLM 选择结束 (不再调工具) ──
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            result.final_answer = (choice.message.content or "")[:500]
            if result.total_steps == 0:
                result.state = "SUCCESS"  # 直接回答，不需要工具
            elif result.success_steps == result.total_steps:
                result.state = "SUCCESS"  # 所有工具调用成功
            elif result.success_steps > 0:
                result.state = "PARTIAL"  # 部分成功后被拒绝/放弃
            else:
                result.state = "ALL_REJECTED"  # 所有工具调用失败
            break

        # ── LLM 选择调用工具 ──
        # 先把 assistant message 加入历史
        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            result.total_steps += 1

            # ── 通过网关调用 MCP 工具 ──
            record = await call_mcp_tool(
                http_session, gateway_url, session_id,
                fn_name, fn_args, step_idx, budget,
            )
            result.tool_calls.append(record)

            if record.status == "success":
                result.success_steps += 1
                result.raw_goodput += TOOL_WEIGHTS.get(fn_name, 1)
                consecutive_rejects = 0
            else:
                consecutive_rejects += 1

            result.backend_llm_tokens += record.llm_tokens_used

            # ★ 关键: 把工具结果 (包括拒绝信息) 喂回 LLM
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": record.response_text,
            })

        # 连续被拒 3 次 → 强制结束 (避免 token 浪费)
        if consecutive_rejects >= 3:
            result.state = "ALL_REJECTED" if result.success_steps == 0 else "PARTIAL"
            result.final_answer = "Terminated: 3 consecutive tool rejections"
            break
    else:
        # 达到 max_steps
        if result.success_steps > 0:
            result.state = "PARTIAL"
        else:
            result.state = "ALL_REJECTED"

    result.end_time = time.time()
    result.total_latency_ms = (result.end_time - result.start_time) * 1000
    if result.state == "SUCCESS":
        result.effective_goodput = result.raw_goodput
    return result


# ══════════════════════════════════════════════════
# 7. 并发调度 — asyncio + Semaphore (导师细节3)
# ══════════════════════════════════════════════════
async def run_all_agents(args):
    """批量运行所有 Agent，精确控制并发数。"""
    import random
    random.seed(42)

    # 根据任务分布选择 prompt
    n = args.agents
    prompts = []
    for i in range(n):
        prompts.append(random.choice(TASK_PROMPTS))

    semaphore = asyncio.Semaphore(args.concurrency)
    results: List[AgentResult] = []
    lock = asyncio.Lock()
    completed = [0]

    connector = aiohttp.TCPConnector(
        limit=args.concurrency * 2,
        limit_per_host=args.concurrency * 2,
    )

    async def run_one(idx: int, prompt: str):
        agent_id = f"{idx:04d}-{uuid.uuid4().hex[:6]}"
        async with semaphore:
            agent_result = await run_agent(
                http_session, args.gateway, agent_id,
                prompt, max_steps=args.max_steps, budget=args.budget,
            )
            async with lock:
                results.append(agent_result)
                completed[0] += 1
                if completed[0] % 10 == 0 or completed[0] == n:
                    print(f"  进度: {completed[0]}/{n} agents 完成")

    print(f"[ReAct Agent] 网关: {args.gateway}")
    print(f"[ReAct Agent] Agents: {n}  并发: {args.concurrency}  最大步数: {args.max_steps}")
    print(f"[ReAct Agent] 网关模式: {args.gateway_mode}")
    if args.burst_size > 0:
        print(f"[ReAct Agent] 突发模式: batch={args.burst_size}  gap={args.burst_gap}s")

    start = time.time()
    async with aiohttp.ClientSession(connector=connector) as http_session:
        tasks = []
        if args.burst_size > 0:
            # ★ 突发模式: 分批瞬时投放, 制造 quota edge 峰值
            batch_idx = 0
            for batch_start in range(0, len(prompts), args.burst_size):
                batch_end = min(batch_start + args.burst_size, len(prompts))
                batch_idx += 1
                for i in range(batch_start, batch_end):
                    tasks.append(asyncio.create_task(run_one(i, prompts[i])))
                print(f"  [突发] batch {batch_idx}: 投放 {batch_end - batch_start} agents "
                      f"(累计 {batch_end}/{len(prompts)})")
                if batch_end < len(prompts):
                    await asyncio.sleep(args.burst_gap)
        else:
            for i, prompt in enumerate(prompts):
                tasks.append(asyncio.create_task(run_one(i, prompt)))
                # 小间隔避免瞬间涌入
                if args.arrival_interval > 0:
                    await asyncio.sleep(args.arrival_interval)
        await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - start
    print_summary(results, elapsed, args.gateway_mode)
    if args.output:
        save_results(results, args.output, args.gateway_mode, elapsed)
    return results


# ══════════════════════════════════════════════════
# 8. 统计输出
# ══════════════════════════════════════════════════
def print_summary(results: List[AgentResult], elapsed: float, gateway_mode: str):
    total = len(results)
    success = sum(1 for r in results if r.state == "SUCCESS")
    partial = sum(1 for r in results if r.state == "PARTIAL")
    rejected = sum(1 for r in results if r.state == "ALL_REJECTED")
    error = sum(1 for r in results if r.state == "ERROR")

    total_steps = sum(r.total_steps for r in results)
    success_steps = sum(r.success_steps for r in results)
    agent_tokens = sum(r.agent_llm_tokens for r in results)
    backend_tokens = sum(r.backend_llm_tokens for r in results)
    raw_gp = sum(r.raw_goodput for r in results)
    eff_gp = sum(r.effective_goodput for r in results)

    # 级联浪费: PARTIAL 或 ALL_REJECTED 的 agent 中已成功步骤
    cascade_wasted_steps = sum(r.success_steps for r in results if r.state in ("PARTIAL", "ALL_REJECTED"))
    cascade_wasted_agents = partial + rejected

    e2e_latencies = [r.total_latency_ms for r in results if r.state == "SUCCESS"]

    pct = lambda n: f"{100 * n / total:.1f}%" if total > 0 else "0%"

    print(f"\n{'=' * 65}")
    print(f"  Exp-Real-3 真·ReAct Agent — {gateway_mode}")
    print(f"{'=' * 65}")
    print(f"  总 Agent 数:     {total}")
    print(f"  ├─ SUCCESS:      {success}  ({pct(success)})")
    print(f"  ├─ PARTIAL:      {partial}  ({pct(partial)})")
    print(f"  ├─ ALL_REJECTED: {rejected}  ({pct(rejected)})")
    print(f"  └─ ERROR:        {error}  ({pct(error)})")
    print(f"\n  ── 步骤统计 ──")
    print(f"  总工具调用:      {total_steps}")
    print(f"  成功调用:        {success_steps}")
    print(f"  级联浪费 Agent:  {cascade_wasted_agents}")
    print(f"  级联浪费步骤:    {cascade_wasted_steps}")
    print(f"\n  ── Goodput ──")
    print(f"  Raw Goodput:     {raw_gp:.1f}")
    print(f"  Effective GP:    {eff_gp:.1f}")
    if elapsed > 0:
        print(f"  Effective GP/s:  {eff_gp / elapsed:.2f}")
    print(f"\n  ── Token 消耗 ──")
    print(f"  Agent Brain:     {agent_tokens:,} tokens")
    print(f"  Backend LLM:     {backend_tokens:,} tokens")
    print(f"  Total:           {agent_tokens + backend_tokens:,} tokens")
    if e2e_latencies:
        e2e = sorted(e2e_latencies)
        p50 = e2e[len(e2e) // 2]
        p95 = e2e[int(len(e2e) * 0.95)]
        print(f"\n  ── E2E 延迟 (成功 Agent) ──")
        print(f"  P50: {p50:.0f}ms  P95: {p95:.0f}ms  Mean: {sum(e2e)/len(e2e):.0f}ms")

    # ── Rate Limit 信号 ──
    total_429 = sum(1 for r in results for tc in r.tool_calls if tc.http_status == 429)
    total_calls = sum(len(r.tool_calls) for r in results)
    rl_values = [tc.rate_limit_remaining for r in results for tc in r.tool_calls
                 if tc.rate_limit_remaining >= 0]
    if total_429 > 0 or rl_values:
        print(f"\n  ── Rate Limit 信号 ──")
        print(f"  429 响应:        {total_429} / {total_calls} ({100*total_429/max(total_calls,1):.1f}%)")
        if rl_values:
            print(f"  X-RateLimit-Remaining: min={min(rl_values)}, "
                  f"mean={sum(rl_values)/len(rl_values):.0f}, max={max(rl_values)}")

    print(f"\n  总耗时: {elapsed:.1f}s")
    print(f"{'=' * 65}")


# ══════════════════════════════════════════════════
# 9. CSV 存储
# ══════════════════════════════════════════════════
def save_results(results: List[AgentResult], path: str, gateway_mode: str, elapsed: float):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # ── 步骤级 CSV ──
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "agent_id", "task_category", "agent_state",
            "step", "tool_name", "status", "latency_ms",
            "llm_tokens_used", "timestamp",
            "http_status", "rate_limit_remaining", "retry_after",
        ])
        for r in results:
            for tc in r.tool_calls:
                w.writerow([
                    r.agent_id, r.task_category, r.state,
                    tc.step, tc.tool_name, tc.status,
                    f"{tc.latency_ms:.2f}",
                    tc.llm_tokens_used, f"{tc.timestamp:.6f}",
                    tc.http_status, tc.rate_limit_remaining,
                    f"{tc.retry_after:.1f}",
                ])

    # ── Agent 级 CSV ──
    agent_path = path.replace(".csv", "_agents.csv")
    with open(agent_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "agent_id", "task_category", "state",
            "total_steps", "success_steps",
            "agent_llm_tokens", "backend_llm_tokens",
            "total_latency_ms", "raw_goodput", "effective_goodput",
        ])
        for r in results:
            w.writerow([
                r.agent_id, r.task_category, r.state,
                r.total_steps, r.success_steps,
                r.agent_llm_tokens, r.backend_llm_tokens,
                f"{r.total_latency_ms:.2f}",
                f"{r.raw_goodput:.1f}", f"{r.effective_goodput:.1f}",
            ])

    # ── 汇总 CSV (append) ──
    summary_path = path.replace(".csv", "_summary.csv")
    total = len(results)
    success = sum(1 for r in results if r.state == "SUCCESS")
    partial = sum(1 for r in results if r.state == "PARTIAL")
    rejected = sum(1 for r in results if r.state == "ALL_REJECTED")
    cascade_wasted = sum(r.success_steps for r in results if r.state in ("PARTIAL", "ALL_REJECTED"))
    agent_tokens = sum(r.agent_llm_tokens for r in results)
    backend_tokens = sum(r.backend_llm_tokens for r in results)
    raw_gp = sum(r.raw_goodput for r in results)
    eff_gp = sum(r.effective_goodput for r in results)
    e2e = sorted([r.total_latency_ms for r in results if r.state == "SUCCESS"])

    write_header = not os.path.exists(summary_path)
    with open(summary_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow([
                "gateway", "agents", "success", "partial", "all_rejected",
                "cascade_wasted_steps", "agent_llm_tokens", "backend_llm_tokens",
                "raw_goodput", "effective_goodput", "eff_gp_per_s",
                "e2e_p50_ms", "e2e_p95_ms", "elapsed_s",
            ])
        w.writerow([
            gateway_mode, total, success, partial, rejected,
            cascade_wasted, agent_tokens, backend_tokens,
            f"{raw_gp:.1f}", f"{eff_gp:.1f}",
            f"{eff_gp / max(elapsed, 0.001):.2f}",
            f"{e2e[len(e2e)//2]:.0f}" if e2e else "0",
            f"{e2e[int(len(e2e)*0.95)]:.0f}" if e2e else "0",
            f"{elapsed:.1f}",
        ])

    print(f"\n  [保存] 步骤级: {path}")
    print(f"  [保存] Agent 级: {agent_path}")
    print(f"  [保存] 汇总: {summary_path}")


# ══════════════════════════════════════════════════
# 10. CLI
# ══════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="真·ReAct Agent 客户端 — LLM 驱动工具选择, 经 MCP 网关准入控制",
    )
    parser.add_argument("--gateway", required=True,
                        help="网关 URL, 如 http://127.0.0.1:9005")
    parser.add_argument("--agents", type=int, default=50,
                        help="Agent 总数 (default: 50)")
    parser.add_argument("--concurrency", type=int, default=10,
                        help="最大并发 Agent 数 (default: 10)")
    parser.add_argument("--max-steps", type=int, default=8,
                        help="每个 Agent 最大工具调用步数 (default: 8)")
    parser.add_argument("--budget", type=int, default=500,
                        help="每个 Agent 的预算 token 数 (default: 500)")
    parser.add_argument("--arrival-interval", type=float, default=0.5,
                        help="Agent 启动间隔/秒 (default: 0.5)")
    parser.add_argument("--burst-size", type=int, default=0,
                        help="突发模式: 每批投放 agent 数 (0=关闭, 使用 arrival-interval)")
    parser.add_argument("--burst-gap", type=float, default=5.0,
                        help="突发模式: 批次间等待秒数 (default: 5.0)")
    parser.add_argument("--gateway-mode", type=str, default="unknown",
                        help="网关模式标签 (用于 CSV, 如 ng/srl/mcpdp-real)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出 CSV 路径")
    args = parser.parse_args()
    asyncio.run(run_all_agents(args))


if __name__ == "__main__":
    main()
