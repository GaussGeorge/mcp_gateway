"""真实战场模式工具集 (Real Battlefield Mode)

包含 7 个工具，用于 Exp8 的真实场景验证：

轻量级工具 (4个):
  - calculate      [真实] 精确数学计算器
  - get_weather    [Mock] 模拟天气查询 (无需真实API)
  - web_fetch      [Mock] 模拟网页抓取 (无需真实API)
  - text_format    [真实] 文本格式化处理器

重量级工具 (3个，全部真实):
  - llm_reason     [真实] 本地大模型深度推理 (GPU密集)
  - doc_embedding  [真实] 本地Embedding向量化 (GPU密集)
  - python_sandbox [真实] Python沙盒执行器 (CPU独占)

设计原则：
  - Weather/WebFetch 仍用 Mock，因为真实 API 的网络延迟不可控且与网关治理无关
  - LLM/Embedding/Sandbox 必须真实，GPU 争抢是测试核心
"""

from tools import ToolRegistry

TOOLS = {
    "lightweight": ["calculate", "get_weather", "web_fetch", "text_format"],
    "heavyweight": ["llm_reason", "doc_embedding", "python_sandbox"],
}


def register_all(registry: ToolRegistry):
    """注册真实战场模式的全部 7 个工具。"""
    from tools import calculator, mock_weather, mock_web_fetch, text_formatter
    from tools import llm_reasoner, doc_embedding, python_sandbox

    # 轻量级工具
    calculator.register(registry)
    mock_weather.register(registry)
    mock_web_fetch.register(registry)
    text_formatter.register(registry)

    # 重量级工具（全部真实）
    llm_reasoner.register(registry)
    doc_embedding.register(registry)
    python_sandbox.register(registry)
