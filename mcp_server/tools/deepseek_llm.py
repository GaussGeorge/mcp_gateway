"""真实 LLM 推理工具 (Real LLM Heavy Tool) — 重量级工具

用于小规模真实 LLM 验证实验：
  - 调用 OpenAI 兼容 API (GLM-4-Flash / DeepSeek / Qwen 等)
  - 真实网络延迟 + 真实 token 消耗 → 真实的「大象流」过载特征
  - 通过 max_tokens 控制单次调用的资源消耗量级

环境变量 (在 .env 文件中配置):
  - LLM_API_BASE: API 端点
  - LLM_API_KEY:  API 密钥
  - LLM_MODEL:    模型名称

支持的 API 提供商:
  - 智谱 GLM-4-Flash: https://open.bigmodel.cn/api/paas/v4
  - DeepSeek:          https://api.deepseek.com/v1
  - 通义千问:          https://dashscope.aliyuncs.com/compatible-mode/v1
"""

import json
import os
import time
from tools import ToolDefinition, ToolRegistry

LLM_API_BASE = os.getenv("LLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4-flash")

_client = None


def _get_client():
    """延迟初始化 OpenAI 兼容客户端。"""
    global _client
    if _client is None:
        import httpx
        from openai import OpenAI
        http_client = httpx.Client(proxy=None, timeout=120)
        _client = OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_API_BASE,
            http_client=http_client,
        )
    return _client


# 操作 → 系统提示词映射
_SYSTEM_PROMPTS = {
    "summarize": "You are a concise summarizer. Summarize the given text in a clear and structured way.",
    "translate": "You are a professional translator. Translate the given text accurately.",
    "reason": "You are a logical reasoning expert. Analyze the problem step by step.",
    "code": "You are a senior programmer. Write clean, well-commented code to solve the problem.",
}


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="deepseek_llm",
        description=f"真实LLM推理({LLM_MODEL})：调用真实API进行摘要/翻译/推理/代码生成。真实网络延迟与token消耗。",
        category="heavyweight",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["summarize", "translate", "reason", "code"],
                    "description": "操作类型"
                },
                "text": {"type": "string", "description": "输入文本"},
                "target_language": {
                    "type": "string",
                    "description": "目标语言 (translate 时使用)",
                    "default": "English"
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "最大生成 token 数",
                    "default": 500
                }
            },
            "required": ["operation", "text"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
                "model": {"type": "string"},
                "usage": {"type": "object"},
                "latency_ms": {"type": "number"},
            }
        },
        handler=execute,
    ))


def execute(arguments: dict) -> str:
    if not LLM_API_KEY:
        return json.dumps({"error": "LLM_API_KEY not set. Please configure .env file."}, ensure_ascii=False)

    operation = arguments.get("operation", "summarize")
    text = arguments.get("text", "")
    max_tokens = min(arguments.get("max_tokens", 500), 4096)
    target_lang = arguments.get("target_language", "English")

    system_prompt = _SYSTEM_PROMPTS.get(operation, _SYSTEM_PROMPTS["reason"])

    if operation == "translate":
        user_msg = f"Translate the following text to {target_lang}:\n\n{text}"
    elif operation == "summarize":
        user_msg = f"Summarize the following text:\n\n{text}"
    elif operation == "code":
        user_msg = f"Write code to solve the following problem:\n\n{text}"
    else:
        user_msg = text

    start = time.time()
    is_429 = False
    rate_limit_remaining = -1.0

    try:
        client = _get_client()
        # 使用 with_raw_response 获取 HTTP 头信息（429/rate-limit）
        raw_resp = client.chat.completions.with_raw_response.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        elapsed_ms = (time.time() - start) * 1000

        # 提取 rate-limit 头
        http_status = raw_resp.status_code
        for header_name in ("X-RateLimit-Remaining", "RateLimit-Remaining",
                            "x-ratelimit-remaining", "ratelimit-remaining"):
            val = raw_resp.headers.get(header_name)
            if val is not None:
                try:
                    rate_limit_remaining = float(val)
                except ValueError:
                    pass
                break

        resp = raw_resp.parse()
        result_text = resp.choices[0].message.content if resp.choices else ""
        usage = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }

        return json.dumps({
            "result": result_text,
            "model": LLM_MODEL,
            "usage": usage,
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": is_429,
                "http_status": http_status,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": rate_limit_remaining,
            }
        }, ensure_ascii=False)

    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        error_str = str(e)
        # 检测 429 限流错误
        is_429 = "429" in error_str or "rate" in error_str.lower()
        return json.dumps({
            "error": error_str,
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": is_429,
                "http_status": 429 if is_429 else 0,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": -1,
            }
        }, ensure_ascii=False)
