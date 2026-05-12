"""无菌实验室模式工具集 (Sterile Laboratory Mode)

仅包含 3 个工具，用于 Exp1-Exp7 的受控实验：
  - calculate      [轻量级] 精确数学计算器
  - web_fetch      [轻量级] 模拟网页抓取 (Mock，确定性延迟)
  - mock_heavy     [重量级] 可控重载基准工具 (精确CPU/内存控制)

设计原则：
  - 轻量工具用 Mock 消除外部 I/O 抖动
  - 重量工具用 mock_heavy 精确控制资源消耗
  - 实验结果完全可复现
"""

from tools import ToolRegistry

TOOLS = {
    "lightweight": ["calculate", "web_fetch"],
    "heavyweight": ["mock_heavy"],
}


def register_all(registry: ToolRegistry):
    """注册无菌实验室模式的全部 3 个工具。"""
    from tools import calculator, mock_web_fetch, mock_heavy

    calculator.register(registry)
    mock_web_fetch.register(registry)
    mock_heavy.register(registry)
