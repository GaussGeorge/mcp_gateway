"""Mock 网页信息检索 (Mock Web Fetch MCP) - 轻量级 I/O 模拟工具

替代真实的 web_fetch，通过 time.sleep() 模拟网络往返延迟（RTT），
返回伪造的静态网页内容。保持"轻量级网络 I/O 密集型"的特性。

系统特征：纯本地 sleep 模拟，无外网依赖。延迟可控（默认 100~500ms 随机抖动，
         模拟真实网页抓取的更长延迟）。
"""

import json
import time
import random
import hashlib
from tools import ToolDefinition, ToolRegistry

# 预置 URL → 内容 映射表
_PAGE_DB = {
    "https://example.com": {
        "title": "Example Domain",
        "text": "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission. More information available at https://www.iana.org/domains/example.",
    },
    "https://news.example.com/tech": {
        "title": "Tech News - AI Advances in 2026",
        "text": "Artificial intelligence continues to reshape industries in 2026. Major breakthroughs in model context protocols (MCP) have enabled seamless integration between AI agents and external tools. The new governance frameworks ensure fair resource allocation in cloud computing environments, preventing overload scenarios that were common in earlier deployments.",
    },
    "https://docs.example.com/mcp": {
        "title": "MCP Protocol Documentation",
        "text": "The Model Context Protocol (MCP) is a standardized protocol for communication between AI models and external tool servers. It uses JSON-RPC 2.0 over HTTP. Key methods include: initialize (handshake), tools/list (enumerate tools), tools/call (invoke a tool), and ping (health check). The protocol version 2024-11-05 introduces governance extensions with token-based admission control.",
    },
    "https://api.example.com/status": {
        "title": "API Status Page",
        "text": "All systems operational. API response time: 45ms (P50), 120ms (P95), 350ms (P99). Uptime: 99.97% over the last 30 days. Active connections: 12,847. Requests per second: 3,420.",
    },
    "https://blog.example.com/cloud-computing": {
        "title": "Cloud Computing Load Balancing Best Practices",
        "text": "Load balancing is a critical component in cloud computing architecture. Key strategies include: round-robin distribution, least-connections routing, weighted algorithms, and dynamic pricing-based admission control. The Rajomon framework introduces token-based governance that adapts to real-time system load, ensuring fair resource allocation between lightweight and heavyweight service requests.",
    },
}

# 用于未知 URL 的随机内容模板
_LOREM_PARAGRAPHS = [
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.",
    "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.",
    "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.",
    "Curabitur pretium tincidunt lacus. Nulla gravida orci a odio. Nullam varius, turpis et commodo pharetra.",
]


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="web_fetch",
        description="[Mock] 网页检索：模拟网络I/O延迟，返回本地伪造网页内容，无需外网。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要获取的网页URL"
                },
                "max_length": {
                    "type": "integer",
                    "description": "返回文本的最大字符数",
                    "default": 2000
                },
                "simulate_rtt_ms": {
                    "type": "integer",
                    "description": "模拟网络往返延迟(ms)，0 表示随机 100~500ms",
                    "default": 0
                }
            },
            "required": ["url"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "content_length": {"type": "integer"},
                "text": {"type": "string"}
            }
        },
        handler=execute
    ))


def execute(arguments: dict) -> str:
    url = arguments.get("url", "")
    max_length = arguments.get("max_length", 2000)
    simulate_rtt_ms = arguments.get("simulate_rtt_ms", 0)

    # 模拟网络 I/O 延迟（网页抓取比 API 调用更慢）
    if simulate_rtt_ms > 0:
        delay = simulate_rtt_ms / 1000.0
    else:
        delay = random.uniform(0.1, 0.5)  # 100~500ms 随机抖动

    time.sleep(delay)
    actual_rtt_ms = delay * 1000

    # 查询预置页面数据库
    if url in _PAGE_DB:
        page = _PAGE_DB[url]
        text = f"{page['title']}\n\n{page['text']}"
    else:
        # 用 URL 哈希生成确定性伪内容
        url_hash = hashlib.md5(url.encode()).hexdigest()
        seed = int(url_hash[:8], 16)
        rng = random.Random(seed)

        title = f"Page: {url.split('/')[-1] or 'index'}"
        num_paragraphs = rng.randint(2, 5)
        paragraphs = [rng.choice(_LOREM_PARAGRAPHS) for _ in range(num_paragraphs)]
        text = f"{title}\n\n" + "\n\n".join(paragraphs)

    # 截断
    if len(text) > max_length:
        text = text[:max_length] + "... [truncated]"

    return json.dumps({
        "url": url,
        "content_length": len(text),
        "text": text,
        "_mock": True,
        "_simulated_rtt_ms": round(actual_rtt_ms, 2),
    }, ensure_ascii=False)
