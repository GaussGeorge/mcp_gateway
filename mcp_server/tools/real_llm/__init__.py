"""真实 LLM 验证模式工具集 (Real LLM Validation Mode)

用于真实 LLM API 验证实验 (Exp-Real-1/2)：
  - calculate        [轻量级] 数学计算 (本地, ~1ms)
  - real_weather      [轻量级] 真实天气查询 (wttr.in, ~200-800ms)
  - real_web_search   [中量级] 真实网页搜索 (Tavily/SerpAPI/DDG, ~500-3000ms)
  - text_formatter    [轻量级] 文本格式化 (本地, ~1ms)
  - deepseek_llm      [重量级] 真实 LLM API 推理 (GLM-4-Flash, ~1-10s)

所有真实 API 工具在返回结果中包含 _signals 字段:
  - is_429: 是否遇到 429 限流
  - http_status: HTTP 状态码
  - api_latency_ms: API 实际延迟
  - rate_limit_remaining: 剩余配额 (-1 表示不可用)

环境变量 (运行前在 .env 中配置):
  LLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
  LLM_API_KEY=你的API Key
  LLM_MODEL=glm-4-flash
  TAVILY_API_KEY=你的Tavily Key (可选)
"""

from tools import ToolRegistry

TOOLS = {
    "lightweight": ["calculate", "real_weather", "text_formatter"],
    "medium":      ["real_web_search"],
    "heavyweight": ["deepseek_llm"],
}


def register_all(registry: ToolRegistry):
    """注册真实 LLM 验证模式的全部 5 个工具。"""
    from tools import calculator, real_weather, real_web_search, text_formatter, deepseek_llm

    calculator.register(registry)
    real_weather.register(registry)
    real_web_search.register(registry)
    text_formatter.register(registry)
    deepseek_llm.register(registry)
