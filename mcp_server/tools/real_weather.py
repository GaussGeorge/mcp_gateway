"""真实天气查询工具 (Real Weather API) — 轻量级工具

使用 wttr.in 免费 API 获取真实天气数据。
真实网络延迟 (~200-800ms) 作为「老鼠流」代表。
"""

import json
import os
import time
import urllib.request
import urllib.error
from tools import ToolDefinition, ToolRegistry

WEATHER_API_URL = os.getenv("WEATHER_API_URL", "https://wttr.in")


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="real_weather",
        description="真实天气查询(wttr.in)：查询全球城市实时天气，轻量级真实API调用。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称 (英文, 如 Beijing, Tokyo, London)"
                },
                "format": {
                    "type": "string",
                    "enum": ["brief", "detailed"],
                    "description": "输出格式: brief=单行摘要, detailed=详细信息",
                    "default": "brief"
                }
            },
            "required": ["city"]
        },
        handler=execute,
    ))


def execute(arguments: dict) -> str:
    city = arguments.get("city", "Beijing")
    fmt = arguments.get("format", "brief")

    # wttr.in URL format: https://wttr.in/City?format=...
    if fmt == "brief":
        url = f"{WEATHER_API_URL}/{city}?format=%l:+%C+%t+%h+%w"
    else:
        url = f"{WEATHER_API_URL}/{city}?format=j1"

    start = time.time()
    is_429 = False
    http_status = 200

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mcp-governance/1.0"})
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(8)
        try:
            resp_obj = urllib.request.urlopen(req, timeout=8)
        finally:
            socket.setdefaulttimeout(old_timeout)
        with resp_obj as resp:
            http_status = resp.status
            data = resp.read().decode("utf-8")

        elapsed_ms = (time.time() - start) * 1000

        if fmt == "detailed":
            try:
                weather_json = json.loads(data)
                current = weather_json.get("current_condition", [{}])[0]
                result = {
                    "city": city,
                    "temp_C": current.get("temp_C"),
                    "humidity": current.get("humidity"),
                    "weather_desc": current.get("weatherDesc", [{}])[0].get("value"),
                    "wind_speed_kmph": current.get("windspeedKmph"),
                    "feels_like_C": current.get("FeelsLikeC"),
                }
            except (json.JSONDecodeError, IndexError, KeyError):
                result = {"raw": data[:500]}
        else:
            result = {"weather": data.strip()}

        return json.dumps({
            **result,
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": is_429,
                "http_status": http_status,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": -1,
            }
        }, ensure_ascii=False)

    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - start) * 1000
        http_status = e.code
        is_429 = (e.code == 429)
        return json.dumps({
            "error": f"HTTP {e.code}: {e.reason}",
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
            "latency_ms": round(elapsed_ms, 1),
            "_signals": {
                "is_429": False,
                "http_status": 0,
                "api_latency_ms": round(elapsed_ms, 1),
                "rate_limit_remaining": -1,
            }
        }, ensure_ascii=False)
