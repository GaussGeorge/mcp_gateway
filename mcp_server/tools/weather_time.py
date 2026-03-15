"""实时天气与时间查询 (Weather & Time MCP) - 轻量级工具

真实场景：大模型获取当前的物理世界状态（天气、时间）。
系统特征：极轻量的网络 I/O 密集型，主要受外部 API 响应时间影响。
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from tools import ToolDefinition, ToolRegistry


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="get_weather",
        description="实时天气查询：获取指定城市的当前天气信息，使用 wttr.in 免费API。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称（英文），如 Beijing, Shanghai, Tokyo, London"
                }
            },
            "required": ["city"]
        },
        handler=execute_weather
    ))

    registry.register(ToolDefinition(
        name="get_current_time",
        description="获取当前时间：返回指定时区的当前日期和时间。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "timezone_offset": {
                    "type": "number",
                    "description": "UTC偏移量(小时)，如中国为8，日本为9，美国东部为-5",
                    "default": 8
                }
            }
        },
        handler=execute_time
    ))


def execute_weather(arguments: dict) -> str:
    city = arguments.get("city", "Beijing")
    try:
        url = f"https://wttr.in/{urllib.request.quote(city)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "MCP-Server/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        current = data["current_condition"][0]
        result = {
            "city": city,
            "temperature_c": current["temp_C"],
            "feels_like_c": current["FeelsLikeC"],
            "humidity": current["humidity"] + "%",
            "weather": current["weatherDesc"][0]["value"],
            "wind_speed_kmph": current["windspeedKmph"],
            "wind_direction": current["winddir16Point"],
            "visibility_km": current["visibility"],
        }
        return json.dumps(result, ensure_ascii=False)

    except urllib.error.URLError:
        return json.dumps({"city": city, "error": "天气服务暂时不可用，请稍后重试"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"city": city, "error": f"查询失败: {str(e)}"}, ensure_ascii=False)


def execute_time(arguments: dict) -> str:
    offset = arguments.get("timezone_offset", 8)
    tz = timezone(timedelta(hours=offset))
    now = datetime.now(tz)
    return json.dumps({
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": f"UTC{'+' if offset >= 0 else ''}{offset}",
        "weekday": now.strftime("%A"),
        "timestamp": int(now.timestamp()),
    }, ensure_ascii=False)
