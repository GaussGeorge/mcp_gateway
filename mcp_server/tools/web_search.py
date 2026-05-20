"""网页信息检索 (Web Search MCP) - 轻量级工具

真实场景：Agent 搜索最新新闻或开源文档以补充知识库。
系统特征：网络 I/O 密集型，有不可控的外部网络延迟波动（几十到几百毫秒），
         可以测试网关对外部长连接的管理能力。
"""

import json
import re
import html
import urllib.request
import urllib.error
from tools import ToolDefinition, ToolRegistry


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="web_fetch",
        description="网页信息检索：获取指定URL的网页内容并提取纯文本，用于获取最新信息或文档。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要获取的网页URL（必须以 http:// 或 https:// 开头）"},
                "max_length": {"type": "integer", "description": "返回文本的最大字符数", "default": 2000}
            },
            "required": ["url"]
        },
        handler=execute
    ))


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def execute(arguments: dict) -> str:
    url = arguments.get("url", "")
    max_length = arguments.get("max_length", 2000)

    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL 必须以 http:// 或 https:// 开头"}, ensure_ascii=False)

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MCP-Server/1.0)"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(max_length * 4)  # Read limited bytes

            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].strip().split(";")[0]

            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                text = raw.decode("utf-8", errors="replace")

            if "html" in content_type.lower():
                text = _strip_html(text)

            if len(text) > max_length:
                text = text[:max_length] + "... [truncated]"

            return json.dumps({
                "url": url,
                "content_length": len(text),
                "text": text,
            }, ensure_ascii=False)

    except urllib.error.HTTPError as e:
        return json.dumps({"url": url, "error": f"HTTP {e.code}: {e.reason}"}, ensure_ascii=False)
    except urllib.error.URLError as e:
        return json.dumps({"url": url, "error": f"连接失败: {str(e.reason)}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"url": url, "error": f"请求异常: {str(e)}"}, ensure_ascii=False)
