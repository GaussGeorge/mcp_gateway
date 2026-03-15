"""基础文本正则与格式化 (Text Formatting MCP) - 轻量级工具

真实场景：将杂乱的 JSON 或日志数据进行初步清洗。
系统特征：轻度 CPU 消耗。
"""

import json
import re
import base64
from tools import ToolDefinition, ToolRegistry


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="text_format",
        description="文本正则与格式化：JSON美化/压缩、正则匹配/替换、Base64编解码、字数统计。",
        category="lightweight",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["json_format", "json_minify", "regex_extract", "regex_replace",
                             "base64_encode", "base64_decode", "word_count", "line_count"],
                    "description": "文本处理操作类型"
                },
                "text": {"type": "string", "description": "输入文本"},
                "pattern": {"type": "string", "description": "正则表达式（regex_extract/regex_replace 需要）"},
                "replacement": {"type": "string", "description": "替换文本（regex_replace 需要）"}
            },
            "required": ["operation", "text"]
        },
        handler=execute
    ))


def execute(arguments: dict) -> str:
    op = arguments.get("operation")
    text = arguments.get("text", "")
    pattern = arguments.get("pattern", "")
    replacement = arguments.get("replacement", "")

    try:
        if op == "json_format":
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2, ensure_ascii=False)

        elif op == "json_minify":
            parsed = json.loads(text)
            return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)

        elif op == "regex_extract":
            if not pattern:
                return json.dumps({"error": "regex_extract 需要提供 pattern 参数"}, ensure_ascii=False)
            matches = re.findall(pattern, text)
            return json.dumps({"matches": matches, "count": len(matches)}, ensure_ascii=False)

        elif op == "regex_replace":
            if not pattern:
                return json.dumps({"error": "regex_replace 需要提供 pattern 参数"}, ensure_ascii=False)
            result = re.sub(pattern, replacement, text)
            return result

        elif op == "base64_encode":
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            return encoded

        elif op == "base64_decode":
            decoded = base64.b64decode(text.encode("ascii")).decode("utf-8")
            return decoded

        elif op == "word_count":
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
            english_words = len(re.findall(r'[a-zA-Z]+', text))
            return json.dumps({
                "chinese_characters": chinese_chars,
                "english_words": english_words,
                "total_characters": len(text),
                "lines": text.count('\n') + 1,
            }, ensure_ascii=False)

        elif op == "line_count":
            lines = text.split('\n')
            non_empty = [line for line in lines if line.strip()]
            return json.dumps({
                "total_lines": len(lines),
                "non_empty_lines": len(non_empty),
                "empty_lines": len(lines) - len(non_empty),
            }, ensure_ascii=False)

        else:
            return json.dumps({"error": f"未知操作: {op}"}, ensure_ascii=False)

    except json.JSONDecodeError as e:
        return json.dumps({"error": f"JSON 解析失败: {str(e)}"}, ensure_ascii=False)
    except re.error as e:
        return json.dumps({"error": f"正则表达式错误: {str(e)}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
