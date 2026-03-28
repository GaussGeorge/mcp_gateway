"""精确数学计算器 (Calculator MCP) - 轻量级工具

真实场景：Agent 在规划财务报表或进行逻辑推理时，调用它进行不会产生"幻觉"的精确计算。
系统特征：纯 CPU 极轻量级计算，耗时 < 1ms。
"""

import math
import json
from tools import ToolDefinition, ToolRegistry


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="calculate",
        description="精确数学计算器：支持加减乘除、幂运算、开方、取模、对数、阶乘等，避免大模型数学幻觉。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "subtract", "multiply", "divide",
                             "power", "sqrt", "modulo", "abs", "log", "factorial"],
                    "description": "数学运算类型"
                },
                "a": {"type": "number", "description": "第一个操作数"},
                "b": {"type": "number", "description": "第二个操作数（sqrt/abs/log/factorial 不需要）"}
            },
            "required": ["operation", "a"]
        },
        output_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string"},
                "a": {"type": "number"},
                "b": {"type": "number"},
                "result": {"type": "number"}
            }
        },
        handler=execute
    ))


def execute(arguments: dict) -> str:
    op = arguments.get("operation")
    a = arguments.get("a")
    b = arguments.get("b")

    try:
        if op == "add":
            result = a + b
        elif op == "subtract":
            result = a - b
        elif op == "multiply":
            result = a * b
        elif op == "divide":
            if b == 0:
                return json.dumps({"error": "除数不能为零"}, ensure_ascii=False)
            result = a / b
        elif op == "power":
            result = a ** b
        elif op == "sqrt":
            if a < 0:
                return json.dumps({"error": "不能对负数开方"}, ensure_ascii=False)
            result = math.sqrt(a)
        elif op == "modulo":
            if b == 0:
                return json.dumps({"error": "模数不能为零"}, ensure_ascii=False)
            result = a % b
        elif op == "abs":
            result = abs(a)
        elif op == "log":
            if a <= 0:
                return json.dumps({"error": "对数的真数必须为正数"}, ensure_ascii=False)
            result = math.log(a)
        elif op == "factorial":
            if a < 0 or a != int(a):
                return json.dumps({"error": "阶乘要求非负整数"}, ensure_ascii=False)
            result = math.factorial(int(a))
        else:
            return json.dumps({"error": f"未知运算: {op}"}, ensure_ascii=False)

        return json.dumps({"operation": op, "a": a, "b": b, "result": result}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
