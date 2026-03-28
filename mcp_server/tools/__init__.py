"""MCP Server Tool Registry and Base Class."""

from dataclasses import dataclass
from typing import Dict, Any, Callable, List


@dataclass
class ToolDefinition:
    """MCP tool definition with metadata."""
    name: str
    description: str
    category: str  # "lightweight", "heavyweight", "benchmark"
    input_schema: Dict[str, Any]
    handler: Callable
    output_schema: Dict[str, Any] = None  # 可选: 输出 JSON Schema (用于 DAG 编排)


class ToolRegistry:
    """Central registry for all MCP tools."""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return tools in MCP protocol format."""
        return [
            {
                "name": t.name,
                "description": f"[{t.category}] {t.description}",
                "inputSchema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def all(self) -> Dict[str, ToolDefinition]:
        return self._tools
