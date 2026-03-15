"""MCP Tool Server — Model Context Protocol 服务器

实现标准 MCP 协议 (JSON-RPC 2.0 over HTTP)，提供三类工具矩阵：
  - 轻量级工具（老鼠流）：Calculator, Weather/Time, WebFetch, TextFormat
  - 重量级工具（大象流）：LLM Reasoner, Doc Embedding, Python Sandbox
  - 基准对照工具：Mock Heavy Tool

协议版本: 2024-11-05
传输方式: HTTP POST (JSON-RPC 2.0)

Usage:
    python server.py [--host HOST] [--port PORT]
    python server.py --port 8080
"""

import json
import time
import logging
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from tools import ToolRegistry

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("mcp-server")

# ──────────────────────────────────────────────
# MCP Protocol Constants
# ──────────────────────────────────────────────
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mcp-tool-server"
SERVER_VERSION = "1.0.0"

# ──────────────────────────────────────────────
# Tool Registry
# ──────────────────────────────────────────────
registry = ToolRegistry()


def load_tools():
    """Import and register all tool modules."""
    from tools import calculator, mock_weather, mock_web_fetch, text_formatter
    from tools import doc_embedding, python_sandbox, mock_heavy

    # 轻量级工具（老鼠流）
    calculator.register(registry)
    mock_weather.register(registry)
    mock_web_fetch.register(registry)
    text_formatter.register(registry)

    # 重量级工具（大象流）
    doc_embedding.register(registry)
    python_sandbox.register(registry)
    mock_heavy.register(registry)

    log.info(f"已注册 {len(registry.all())} 个工具:")
    for name, tool in registry.all().items():
        log.info(f"  [{tool.category:>11}] {name}")


# ──────────────────────────────────────────────
# JSON-RPC Response Builders
# ──────────────────────────────────────────────
def make_response(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id, code, message, data=None):
    err = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        err["error"]["data"] = data
    return err


# ──────────────────────────────────────────────
# MCP Method Handlers
# ──────────────────────────────────────────────
def handle_initialize(req_id, params):
    """MCP initialize handshake — 协商协议版本和能力。"""
    return make_response(req_id, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    })


def handle_tools_list(req_id, params):
    """MCP tools/list — 返回所有已注册工具的元信息。"""
    return make_response(req_id, {"tools": registry.list_tools()})


def handle_tools_call(req_id, params):
    """MCP tools/call — 调用指定工具并返回结果。"""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    tool = registry.get(tool_name)
    if not tool:
        return make_error(req_id, -32602, f"Tool not found: {tool_name}")

    start = time.time()
    try:
        result_text = tool.handler(arguments)
        elapsed_ms = (time.time() - start) * 1000

        log.info(f"✓ [{tool.category}] {tool_name} — {elapsed_ms:.1f}ms")

        return make_response(req_id, {
            "content": [{"type": "text", "text": result_text}],
            "_meta": {
                "tool": tool_name,
                "category": tool.category,
                "latency_ms": round(elapsed_ms, 2),
            },
        })
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        log.error(f"✗ [{tool.category}] {tool_name} — {elapsed_ms:.1f}ms — {e}")
        return make_error(req_id, -32603, f"Tool execution failed: {str(e)}")


def handle_ping(req_id, params):
    """MCP ping — 健康检查。"""
    return make_response(req_id, {})


# Method dispatch table
HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "ping": handle_ping,
}


# ──────────────────────────────────────────────
# HTTP Request Handler
# ──────────────────────────────────────────────
class MCPRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing MCP JSON-RPC 2.0 protocol."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(make_error(None, -32700, "Empty request body"), 400)
            return

        raw = self.rfile.read(content_length)
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(make_error(None, -32700, "Parse error: invalid JSON"), 400)
            return

        if request.get("jsonrpc") != "2.0":
            self._send_json(
                make_error(request.get("id"), -32600, "Invalid JSON-RPC version"), 400
            )
            return

        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        handler = HANDLERS.get(method)
        if not handler:
            self._send_json(
                make_error(req_id, -32601, f"Method not found: {method}"), 404
            )
            return

        log.info(f"← {method} (id={req_id})")
        response = handler(req_id, params)
        self._send_json(response)

    def do_GET(self):
        """GET / — 服务器健康状态检查。"""
        tools_by_category = {}
        for name, tool in registry.all().items():
            tools_by_category.setdefault(tool.category, []).append(name)

        self._send_json({
            "status": "ok",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocol": MCP_PROTOCOL_VERSION,
            "tools_count": len(registry.all()),
            "tools_by_category": tools_by_category,
        })

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default HTTP request log; use our structured logger instead."""
        pass


# ──────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server — each request runs in its own thread."""
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="MCP Tool Server")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址 (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="监听端口 (default: 8080)")
    args = parser.parse_args()

    load_tools()

    server = ThreadedHTTPServer((args.host, args.port), MCPRequestHandler)
    log.info("=" * 55)
    log.info(f"  MCP Tool Server started")
    log.info(f"  Address:  http://{args.host}:{args.port}")
    log.info(f"  Protocol: MCP {MCP_PROTOCOL_VERSION}")
    log.info("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
