"""长文档向量化 (Document Embedding MCP) - 重量级工具 [内存杀手]

真实场景：企业级 RAG 知识库构建，将大型文档灌入向量数据库。
系统特征：极端内存/显存爆发消耗 (Memory Burst)。
         瞬间申请大量内存，可能导致 OOM，
         用来测试网关瞬间调高 Price 以拒绝后续重载请求的反应速度。
"""

import json
import time
import hashlib
import struct
from tools import ToolDefinition, ToolRegistry


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="doc_embedding",
        description="长文档向量化：将文档分块并生成向量嵌入，模拟企业级RAG知识库构建。极端内存爆发消耗。",
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
                "dimensions": {
                    "type": "integer",
                    "description": "向量维度",
                    "default": 1024
                },
                "simulate_memory_mb": {
                    "type": "integer",
                    "description": "模拟显存占用(MB)，越大内存压力越高",
                    "default": 50
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


def _generate_embedding(text: str, dimensions: int) -> list:
    """Generate a deterministic pseudo-embedding vector from text using SHA-256 hash chain.

    Produces a normalized float vector that is deterministic for the same input text,
    simulating the output of a real embedding model.
    """
    vector = []
    seed = hashlib.sha256(text.encode('utf-8')).digest()

    while len(vector) < dimensions:
        seed = hashlib.sha256(seed).digest()
        for i in range(0, len(seed) - 3, 4):
            if len(vector) >= dimensions:
                break
            # Unpack 4 bytes as unsigned int, normalize to [-1, 1]
            val = struct.unpack('>I', seed[i:i + 4])[0]
            normalized = (val / 2147483647.5) - 1.0  # Map [0, 2^32) -> [-1, 1)
            vector.append(round(normalized, 6))

    return vector[:dimensions]


def _cosine_similarity(vec_a: list, vec_b: list) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def execute(arguments: dict) -> str:
    text = arguments.get("text", "")
    chunk_size = arguments.get("chunk_size", 256)
    dimensions = arguments.get("dimensions", 1024)
    simulate_memory_mb = min(arguments.get("simulate_memory_mb", 50), 512)  # Cap at 512MB

    if not text:
        return json.dumps({"error": "输入文本不能为空"}, ensure_ascii=False)

    start_time = time.time()

    # Step 1: 文档分块
    chunks = _text_to_chunks(text, chunk_size)

    # Step 2: 模拟显存爆发占用 — 分配大块连续内存
    memory_block = None
    if simulate_memory_mb > 0:
        memory_block = bytearray(simulate_memory_mb * 1024 * 1024)
        # Touch every page to force OS physical allocation
        for i in range(0, len(memory_block), 4096):
            memory_block[i] = 0xFF

    # Step 3: 为每个 chunk 生成 embedding 向量
    embeddings = []
    for chunk in chunks:
        vec = _generate_embedding(chunk, dimensions)
        embeddings.append(vec)

    # Step 4: 计算 chunk 间相似度矩阵（CPU + 内存密集）
    similarity_sample = []
    sample_size = min(len(chunks), 5)
    for i in range(sample_size):
        for j in range(i + 1, sample_size):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            similarity_sample.append({
                "chunk_i": i,
                "chunk_j": j,
                "similarity": round(sim, 4),
            })

    # Release memory
    if memory_block:
        del memory_block

    elapsed = time.time() - start_time

    return json.dumps({
        "total_chunks": len(chunks),
        "chunk_size": chunk_size,
        "dimensions": dimensions,
        "memory_simulated_mb": simulate_memory_mb,
        "processing_time_s": round(elapsed, 3),
        "first_embedding_preview": embeddings[0][:8] if embeddings else [],
        "similarity_sample": similarity_sample,
    }, ensure_ascii=False)
