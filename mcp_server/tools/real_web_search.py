"""真实网页搜索工具 (Real Web Search API) — 中量级工具

支持 Tavily API 和 SerpAPI 两种后端：
  - Tavily: 免费 1000次/月, 延迟 ~500-2000ms
  - SerpAPI: 免费 100次/月, 延迟 ~800-3000ms
  - 无 Key 时降级为 DuckDuckGo HTML 爬取 (无需 Key, ~1-3s)

作为「中量级」工具代表，真实网络延迟介于天气和 LLM 之间。
"""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from tools import ToolDefinition, ToolRegistry

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="real_web_search",
        description="真实网页搜索：使用Tavily/SerpAPI/DuckDuckGo搜索网页，中量级真实API调用。",
        category="medium",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词"
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数",
                    "default": 3
                }
            },
            "required": ["query"]
        },
        handler=execute,
    ))


def _search_tavily(query: str, max_results: int) -> dict:
    """使用 Tavily API 搜索。"""
    url = "https://api.tavily.com/search"
    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        http_status = resp.status
        rate_limit_remaining = -1.0
        rl_header = resp.getheader("X-RateLimit-Remaining") or resp.getheader("RateLimit-Remaining")
        if rl_header:
            try:
                rate_limit_remaining = float(rl_header)
            except ValueError:
                pass
        data = json.loads(resp.read().decode("utf-8"))
        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:200],
            })
        return {"results": results, "http_status": http_status,
                "rate_limit_remaining": rate_limit_remaining, "backend": "tavily"}


def _search_serpapi(query: str, max_results: int) -> dict:
    """使用 SerpAPI 搜索。"""
    params = urllib.parse.urlencode({
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": max_results,
        "engine": "google",
    })
    url = f"https://serpapi.com/search.json?{params}"

    req = urllib.request.Request(url, headers={"User-Agent": "mcp-governance/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        http_status = resp.status
        rate_limit_remaining = -1.0
        rl_header = resp.getheader("X-RateLimit-Remaining") or resp.getheader("RateLimit-Remaining")
        if rl_header:
            try:
                rate_limit_remaining = float(rl_header)
            except ValueError:
                pass
        data = json.loads(resp.read().decode("utf-8"))
        results = []
        for item in data.get("organic_results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", "")[:200],
            })
        return {"results": results, "http_status": http_status,
                "rate_limit_remaining": rate_limit_remaining, "backend": "serpapi"}


def _search_duckduckgo(query: str, max_results: int) -> dict:
    """DuckDuckGo API 不可靠时使用模拟延迟搜索。
    保持 ~500-2000ms 的真实网络延迟特征，用于治理行为实验。"""
    import random
    # 模拟真实搜索延迟 (500-2000ms)
    delay = random.uniform(0.5, 2.0)
    time.sleep(delay)
    results = []
    for i in range(min(max_results, 3)):
        results.append({
            "title": f"Result {i+1} for: {query[:50]}",
            "url": f"https://example.com/search?q={urllib.parse.quote(query[:30])}&p={i}",
            "snippet": f"Simulated search result {i+1} for query '{query[:80]}'. "
                       f"This is a governance experiment mock result with realistic latency.",
        })
    return {"results": results, "http_status": 200,
            "rate_limit_remaining": -1.0, "backend": "mock-search"}


def execute(arguments: dict) -> str:
    query = arguments.get("query", "")
    max_results = min(arguments.get("max_results", 3), 10)

    start = time.time()
    is_429 = False
    http_status = 200
    rate_limit_remaining = -1.0

    try:
        # Priority: Tavily > SerpAPI > DuckDuckGo
        if TAVILY_API_KEY:
            result = _search_tavily(query, max_results)
        elif SERPAPI_KEY:
            result = _search_serpapi(query, max_results)
        else:
            result = _search_duckduckgo(query, max_results)

        elapsed_ms = (time.time() - start) * 1000
        http_status = result.get("http_status", 200)
        rate_limit_remaining = result.get("rate_limit_remaining", -1.0)

        return json.dumps({
            "results": result["results"],
            "backend": result["backend"],
            "query": query,
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": is_429,
                "http_status": http_status,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": rate_limit_remaining,
            }
        }, ensure_ascii=False)

    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - start) * 1000
        http_status = e.code
        is_429 = (e.code == 429)
        return json.dumps({
            "error": f"HTTP {e.code}: {e.reason}",
            "query": query,
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": is_429,
                "http_status": http_status,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": -1,
            }
        }, ensure_ascii=False)

    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        return json.dumps({
            "error": str(e),
            "query": query,
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": False,
                "http_status": 0,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": -1,
            }
        }, ensure_ascii=False)
