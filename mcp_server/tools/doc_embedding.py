"""长文档向量化 (Document Embedding MCP) - 重量级工具 [真实GPU消耗]

真实场景：企业级 RAG 知识库构建，调用本地大模型将文档分块向量化。
系统特征：持续 GPU 算力与显存消耗。每批 chunk 发送至模型处理，产生真实负载。

工作模式（自动检测，优先使用 Embedding API）：
  1. Embedding API 模式: 调用 /v1/embeddings 端点（如 vLLM embedding 模型）
  2. LLM 摘要模式 (fallback): 调用 /v1/chat/completions 对每个 chunk 生成语义摘要，
     真实消耗 GPU 算力，然后用摘要文本的哈希生成确定性向量。

配置：通过环境变量设置（默认复用 LLM 服务地址）。
  - EMBEDDING_API_BASE: API 地址 (默认跟随 LLM_API_BASE)
  - EMBEDDING_API_KEY:  API 密钥 (默认跟随 LLM_API_KEY)
  - EMBEDDING_MODEL:    模型名称 (默认跟随 LLM_MODEL)
"""

import json
import os
import time
import hashlib
import struct
import httpx
from openai import OpenAI
from tools import ToolDefinition, ToolRegistry

EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", os.getenv("LLM_API_BASE", "http://localhost:9999/v1"))
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", os.getenv("LLM_API_KEY", "EMPTY"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", os.getenv("LLM_MODEL", "qwen"))

_http_client = httpx.Client(proxy=None, timeout=120)
_client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_API_BASE, http_client=_http_client)


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="doc_embedding",
        description="长文档向量化：调用本地Embedding模型将文档分块向量化，模拟企业级RAG知识库构建。真实GPU消耗。",
        category="heavyweight",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要向量化的文档文本"},
                "chunk_size": {
                    "type": "integer",
                    "description": "分块大小(字符数)",
                    "default": 256
                },
                "batch_size": {
                    "type": "integer",
                    "description": "每批发送给Embedding API的chunk数量",
                    "default": 8
                }
            },
            "required": ["text"]
        },
        handler=execute
    ))


def _text_to_chunks(text: str, chunk_size: int) -> list:
    """Split text into fixed-size chunks."""
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def _hash_to_vector(text: str, dimensions: int) -> list:
    """Generate deterministic pseudo-vector from text via SHA-256 hash chain."""
    vector = []
    seed = hashlib.sha256(text.encode('utf-8')).digest()
    while len(vector) < dimensions:
        seed = hashlib.sha256(seed).digest()
        for i in range(0, len(seed) - 3, 4):
            if len(vector) >= dimensions:
                break
            val = struct.unpack('>I', seed[i:i + 4])[0]
            vector.append(round((val / 2147483647.5) - 1.0, 6))
    return vector[:dimensions]


def _cosine_similarity(vec_a: list, vec_b: list) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _try_embedding_api(chunks: list, batch_size: int):
    """尝试调用 /v1/embeddings 端点。成功返回 (embeddings, dims)，失败返回 None。"""
    try:
        all_embeddings = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            response = _client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
            for item in response.data:
                all_embeddings.append(item.embedding)
        dims = len(all_embeddings[0]) if all_embeddings else 0
        return all_embeddings, dims
    except Exception:
        return None


def _llm_summarize_chunks(chunks: list, batch_size: int):
    """Fallback: 用 LLM chat API 对每个 chunk 生成摘要，真实消耗 GPU 算力。
    返回摘要文本列表，后续用哈希转换成确定性向量。
    """
    summaries = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        # 将多个 chunk 打包到一个 LLM 请求中，减少调用次数
        numbered = "\n".join(f"[{j+1}] {c}" for j, c in enumerate(batch))
        prompt = f"为以下{len(batch)}段文本各生成一个10字以内的关键词摘要，每行一个：\n{numbered}"

        response = _client.chat.completions.create(
            model=EMBEDDING_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=len(batch) * 20,
            temperature=0.0,
        )
        result = response.choices[0].message.content or ""
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]

        # 如果返回行数不够，用原文补齐
        for j, chunk in enumerate(batch):
            summary = lines[j] if j < len(lines) else chunk[:30]
            summaries.append(summary)

    return summaries


def execute(arguments: dict) -> str:
    text = arguments.get("text", "")
    chunk_size = arguments.get("chunk_size", 256)
    batch_size = max(1, min(arguments.get("batch_size", 8), 32))

    if not text:
        return json.dumps({"error": "输入文本不能为空"}, ensure_ascii=False)

    start_time = time.time()

    # Step 1: 文档分块
    chunks = _text_to_chunks(text, chunk_size)

    # Step 2: 尝试 Embedding API，失败则回退到 LLM 摘要模式
    api_start = time.time()
    mode = "embedding_api"
    dimensions = 0

    embedding_result = _try_embedding_api(chunks, batch_size)

    if embedding_result is not None:
        all_embeddings, dimensions = embedding_result
    else:
        # Fallback: LLM 摘要 → 哈希向量
        mode = "llm_summarize"
        dimensions = 768  # 固定维度
        try:
            summaries = _llm_summarize_chunks(chunks, batch_size)
            all_embeddings = [_hash_to_vector(s, dimensions) for s in summaries]
        except Exception as e:
            return json.dumps({
                "error": f"Embedding 和 LLM fallback 均失败: {str(e)}",
                "hint": f"请确认服务已启动 ({EMBEDDING_API_BASE})",
            }, ensure_ascii=False)

    total_api_time = time.time() - api_start

    # Step 3: 计算 chunk 间相似度矩阵样本
    similarity_sample = []
    sample_size = min(len(chunks), 5)
    for i in range(sample_size):
        for j in range(i + 1, sample_size):
            sim = _cosine_similarity(all_embeddings[i], all_embeddings[j])
            similarity_sample.append({
                "chunk_i": i,
                "chunk_j": j,
                "similarity": round(sim, 4),
            })

    elapsed = time.time() - start_time

    return json.dumps({
        "mode": mode,
        "total_chunks": len(chunks),
        "chunk_size": chunk_size,
        "dimensions": dimensions,
        "embedding_model": EMBEDDING_MODEL,
        "api_time_s": round(total_api_time, 3),
        "processing_time_s": round(elapsed, 3),
        "first_embedding_preview": [round(x, 6) for x in all_embeddings[0][:8]] if all_embeddings else [],
        "similarity_sample": similarity_sample,
    }, ensure_ascii=False)
