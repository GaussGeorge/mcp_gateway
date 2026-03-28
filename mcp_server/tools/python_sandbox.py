"""Python 沙盒数据分析 (Python Sandbox MCP) - 重量级工具 [阻塞队列杀手]

真实场景：用户丢给 Agent 一个大型 CSV 文件，要求分析趋势并画出折线图。
系统特征：长时间的 CPU 独占与队列阻塞。会把后端 Worker 线程长时间卡死。
         用来验证网关能否在后端排队严重时，保障轻量级请求依然畅通（Fairness 公平性）。
"""

import json
import subprocess
import sys
import time
import tempfile
import os
from tools import ToolDefinition, ToolRegistry

# 最大执行时间限制
MAX_TIMEOUT_SECONDS = 60


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="python_sandbox",
        description="Python沙盒执行器：在隔离子进程中执行Python代码进行数据分析和计算。长时间CPU独占。",
        category="heavyweight",
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的Python代码"},
                "timeout": {
                    "type": "integer",
                    "description": "执行超时时间(秒)，最大60秒",
                    "default": 30
                }
            },
            "required": ["code"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "exit_code": {"type": "integer"},
                "execution_time_s": {"type": "number"},
                "stdout": {"type": "string"},
                "stderr": {"type": "string"}
            }
        },
        handler=execute
    ))


def execute(arguments: dict) -> str:
    code = arguments.get("code", "")
    timeout = min(arguments.get("timeout", 30), MAX_TIMEOUT_SECONDS)

    if not code:
        return json.dumps({"error": "代码不能为空"}, ensure_ascii=False)

    start_time = time.time()
    temp_path = None

    try:
        # Write code to temp file
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            f.write(code)
            temp_path = f.name

        # Execute in isolated subprocess
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        elapsed = time.time() - start_time

        response = {
            "exit_code": result.returncode,
            "execution_time_s": round(elapsed, 3),
        }
        if result.stdout:
            response["stdout"] = result.stdout[:5000]
        if result.stderr:
            response["stderr"] = result.stderr[:2000]

        return json.dumps(response, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "error": f"执行超时 ({timeout}秒)",
            "execution_time_s": timeout,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"执行异常: {str(e)}"}, ensure_ascii=False)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
