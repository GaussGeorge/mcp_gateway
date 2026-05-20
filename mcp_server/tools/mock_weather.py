"""Mock 天气查询 (Mock Weather MCP) - 轻量级 I/O 模拟工具

替代真实的 get_weather，通过 time.sleep() 模拟网络往返延迟（RTT），
返回伪造的静态天气数据。保持"轻量级网络 I/O 密集型"的特性。

系统特征：纯本地 sleep 模拟，无外网依赖。延迟可控（默认 50~200ms 随机抖动）。
"""

import json
import time
import random
from datetime import datetime, timezone, timedelta
from tools import ToolDefinition, ToolRegistry

# 预置城市天气数据库 — 覆盖常用测试城市
_WEATHER_DB = {
    "Beijing":   {"temp_C": "22", "feels_like_C": "20", "humidity": "45%", "weather": "Partly Cloudy",   "wind_kmph": "12", "wind_dir": "NW",  "visibility": "10"},
    "Shanghai":  {"temp_C": "26", "feels_like_C": "28", "humidity": "72%", "weather": "Light Rain",      "wind_kmph": "8",  "wind_dir": "SE",  "visibility": "6"},
    "Guangzhou": {"temp_C": "30", "feels_like_C": "33", "humidity": "80%", "weather": "Thunderstorm",    "wind_kmph": "15", "wind_dir": "S",   "visibility": "4"},
    "Shenzhen":  {"temp_C": "29", "feels_like_C": "32", "humidity": "78%", "weather": "Overcast",        "wind_kmph": "10", "wind_dir": "SSE", "visibility": "7"},
    "Tokyo":     {"temp_C": "18", "feels_like_C": "16", "humidity": "55%", "weather": "Sunny",           "wind_kmph": "20", "wind_dir": "NE",  "visibility": "15"},
    "London":    {"temp_C": "12", "feels_like_C": "9",  "humidity": "85%", "weather": "Fog",             "wind_kmph": "25", "wind_dir": "W",   "visibility": "2"},
    "New York":  {"temp_C": "15", "feels_like_C": "13", "humidity": "60%", "weather": "Clear",           "wind_kmph": "18", "wind_dir": "NW",  "visibility": "12"},
    "Paris":     {"temp_C": "14", "feels_like_C": "11", "humidity": "70%", "weather": "Light Drizzle",   "wind_kmph": "14", "wind_dir": "SW",  "visibility": "8"},
    "Sydney":    {"temp_C": "24", "feels_like_C": "23", "humidity": "50%", "weather": "Sunny",           "wind_kmph": "22", "wind_dir": "E",   "visibility": "20"},
    "Moscow":    {"temp_C": "-5", "feels_like_C": "-12","humidity": "90%", "weather": "Heavy Snow",      "wind_kmph": "30", "wind_dir": "N",   "visibility": "1"},
}

# 未知城市的随机天气生成模板
_WEATHER_OPTIONS = ["Sunny", "Cloudy", "Partly Cloudy", "Light Rain", "Overcast", "Clear", "Fog"]
_WIND_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="get_weather",
        description="[Mock] 天气查询：模拟网络I/O延迟，返回本地伪造天气数据，无需外网。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称（英文），如 Beijing, Shanghai, Tokyo, London"
                },
                "simulate_rtt_ms": {
                    "type": "integer",
                    "description": "模拟网络往返延迟(ms)，0 表示随机 50~200ms",
                    "default": 0
                }
            },
            "required": ["city"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "temperature_c": {"type": "string"},
                "humidity": {"type": "string"},
                "condition": {"type": "string"},
                "wind": {"type": "string"}
            }
        },
        handler=execute
    ))

    # get_current_time 是纯本地计算，不需要网络，直接保留
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
        output_schema={
            "type": "object",
            "properties": {
                "timezone_offset": {"type": "number"},
                "datetime": {"type": "string"},
                "timezone": {"type": "string"}
            }
        },
        handler=execute_time
    ))


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


def execute(arguments: dict) -> str:
    city = arguments.get("city", "Beijing")
    simulate_rtt_ms = arguments.get("simulate_rtt_ms", 0)

    # 模拟网络 I/O 延迟
    if simulate_rtt_ms > 0:
        delay = simulate_rtt_ms / 1000.0
    else:
        delay = random.uniform(0.05, 0.2)  # 50~200ms 随机抖动

    time.sleep(delay)
    actual_rtt_ms = delay * 1000

    # 查询预置数据库，未知城市用确定性随机生成
    if city in _WEATHER_DB:
        data = _WEATHER_DB[city]
    else:
        # 用城市名哈希确保同一城市每次返回相同结果
        seed = hash(city) % 10000
        rng = random.Random(seed)
        data = {
            "temp_C": str(rng.randint(-10, 40)),
            "feels_like_C": str(rng.randint(-15, 42)),
            "humidity": f"{rng.randint(20, 95)}%",
            "weather": rng.choice(_WEATHER_OPTIONS),
            "wind_kmph": str(rng.randint(0, 40)),
            "wind_dir": rng.choice(_WIND_DIRS),
            "visibility": str(rng.randint(1, 20)),
        }

    return json.dumps({
        "city": city,
        "temperature_c": data["temp_C"],
        "feels_like_c": data["feels_like_C"],
        "humidity": data["humidity"],
        "weather": data["weather"],
        "wind_speed_kmph": data["wind_kmph"],
        "wind_direction": data["wind_dir"],
        "visibility_km": data["visibility"],
        "_mock": True,
        "_simulated_rtt_ms": round(actual_rtt_ms, 2),
    }, ensure_ascii=False)
