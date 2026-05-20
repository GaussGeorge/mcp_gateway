"""大模型深度推理与总结 (LLM Reasoner MCP) - 重量级工具

真实场景：Agent 抓取了一篇长文，要求提取核心摘要并翻译。
系统特征：极端 GPU 算力与显存带宽消耗。耗时与请求的 Token 数量严格正相关。
         这是触发网关启动 Overload 保护的绝对主力。

配置：通过环境变量设置本地大模型地址，默认连接 localhost:9999 的 qwen 模型。
  - LLM_API_BASE: API 地址 (默认 http://localhost:9999/v1)
  - LLM_API_KEY:  API 密钥 (默认 EMPTY)
  - LLM_MODEL:    模型名称 (默认 qwen)
"""

import json
import os
import time
import httpx
from openai import OpenAI
from tools import ToolDefinition, ToolRegistry

LLM_API_BASE = os.getenv("LLM_API_BASE", "http://localhost:9999/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "EMPTY")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen")

# 创建全局客户端（绕过系统代理，复用连接池）
_http_client = httpx.Client(proxy=None, timeout=120)
_openai_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_API_BASE, http_client=_http_client)


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="llm_reason",
        description="大模型深度推理与总结：调用本地LLM进行文本摘要、翻译、推理分析。极端GPU算力消耗。",
        category="heavyweight",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["summarize", "translate", "reason", "analyze"],
                    "description": "推理操作类型：summarize=摘要, translate=翻译, reason=推理, analyze=分析"
                },
                "text": {"type": "string", "description": "输入文本"},
                "target_language": {
                    "type": "string",
                    "description": "目标语言（translate 时需要）",
                    "default": "English"
                },
                "question": {
                    "type": "string",
                    "description": "具体问题（reason/analyze 时可选）"
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
                "operation": {"type": "string"},
                "result": {"type": "string"},
                "metrics": {
                    "type": "object",
                    "properties": {
                        "ttft_ms": {"type": "number"},
                        "total_time_s": {"type": "number"},
                        "tokens": {"type": "integer"},
                        "tokens_per_sec": {"type": "number"}
                    }
                }
            }
        },
        handler=execute
    ))


PROMPTS = {
    "summarize": "请对以下文本进行精炼摘要，提取核心要点：\n\n{text}",
    "translate": "请将以下文本翻译为{target_language}，保持原意准确：\n\n{text}",
    "reason": "请基于以下文本进行深度推理分析。{question}\n\n文本内容：\n{text}",
    "analyze": "请对以下文本进行详细分析，包括主题、观点、逻辑结构等：\n\n{text}",
}


def execute(arguments: dict) -> str:
    op = arguments.get("operation", "summarize")
    text = arguments.get("text", "")
    target_language = arguments.get("target_language", "English")
    question = arguments.get("question", "")
    max_tokens = arguments.get("max_tokens", 500)

    if not text:
        return json.dumps({"error": "输入文本不能为空"}, ensure_ascii=False)

    prompt_template = PROMPTS.get(op, PROMPTS["summarize"])
    prompt = prompt_template.format(
        text=text,
        target_language=target_language,
        question=f"问题：{question}" if question else ""
    )

    try:
        start_time = time.time()

        response = _openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
        )

        # 流式接收，记录首字延迟
        result_chunks = []
        first_token_time = None
        token_count = 0

        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                if first_token_time is None:
                    first_token_time = time.time()
                result_chunks.append(chunk.choices[0].delta.content)
                token_count += 1

        end_time = time.time()
        result_text = "".join(result_chunks)

        ttft_ms = (first_token_time - start_time) * 1000 if first_token_time else 0
        total_time_s = end_time - start_time
        tps = token_count / total_time_s if total_time_s > 0 else 0

        return json.dumps({
            "operation": op,
            "result": result_text,
            "metrics": {
                "ttft_ms": round(ttft_ms, 2),
                "total_time_s": round(total_time_s, 3),
                "tokens": token_count,
                "tokens_per_sec": round(tps, 2),
            },
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"LLM 调用失败: {str(e)}",
            "hint": f"请确认本地大模型服务已启动 ({LLM_API_BASE})"
        }, ensure_ascii=False)
