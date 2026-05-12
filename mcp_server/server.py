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
import os
import platform
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from tools import ToolRegistry

# ──────────────────────────────────────────────
# Load .env — 自动加载项目根目录的 .env 文件
# ──────────────────────────────────────────────
def _load_dotenv():
    """从项目根目录加载 .env 文件到环境变量。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                # 去除包裹引号
                if value and len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key and value and key not in os.environ:
                    os.environ[key] = value

_load_dotenv()


# ──────────────────────────────────────────────
# CPU Affinity — 进程绑核隔离 (实验资源隔离)
# ──────────────────────────────────────────────
def set_cpu_affinity(cores=None):
    """将当前进程绑定到指定的 CPU 核心集合。

    Args:
        cores: CPU 核心列表, 如 [4,5,6,...,15]。None 表示不绑核。
    """
    if cores is None or platform.system() != "Windows":
        return
    try:
        import psutil
        p = psutil.Process(os.getpid())
        p.cpu_affinity(cores)
        log.info(f"CPU 亲和性已设置: cores={cores}")
    except ImportError:
        log.warning("psutil 未安装，跳过 CPU 亲和性设置。请安装: pip install psutil")
    except Exception as e:
        log.warning(f"CPU 亲和性设置失败: {e}")

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


def load_tools(mode="sterile"):
    """Import and register tool modules based on experiment mode.

    Args:
        mode: "sterile"      — 无菌实验室模式 (3 tools, Exp1-Exp7)
              "battlefield"  — 真实战场模式   (7 tools, Exp8)
    """
    if mode == "sterile":
        from tools.sterile import register_all
        register_all(registry)
    elif mode == "battlefield":
        from tools.battlefield import register_all
        register_all(registry)
    elif mode == "real_llm":
        from tools.real_llm import register_all
        register_all(registry)
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'sterile', 'battlefield', or 'real_llm'.")

    log.info(f"实验模式: {mode}")
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
# Concurrency Limiter — 模拟后端有限资源（如 GPU 槽位、API 速率限制）
# ──────────────────────────────────────────────
# 全局工作线程信号量：限制所有 tools/call 的并发数（模拟 ThreadPoolExecutor(max_workers=N)）。
# 超过限制的请求在此处排队，造成延迟堆积，触发网关侧的过载感知。
TOOL_CONCURRENCY_LIMIT = 8  # 最多 8 个工具调用同时执行（仅对重量级工具的旧逻辑）
_tool_semaphore = threading.Semaphore(TOOL_CONCURRENCY_LIMIT)
# 全局并发限制：applies to ALL tools/call requests
_GLOBAL_MAX_WORKERS = 100   # 由 main() 中 --max-workers 覆盖
_global_semaphore: threading.Semaphore = None  # 延迟初始化
# 队列超时：模拟真实服务器的有界队列
_QUEUE_TIMEOUT = 1.0        # 由 main() 中 --queue-timeout 覆盖
# 拥塞惩罚：模拟 CPU 上下文切换导致的吞吐崩溃
_in_flight_count = 0
_in_flight_lock = threading.Lock()
_CONGESTION_FACTOR = 0.5    # penalty_ms = (excess^1.5) * factor, capped at 2000ms
# 工具执行延迟注入：模拟真实云工具的物理耗时（秒）
# 由 CLI --tool-delay-lightweight/medium/heavyweight 填充
_TOOL_EXEC_DELAYS = {}      # {"lightweight": (1.5, 3.0), "medium": (2.5, 5.0), ...}


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
    """MCP tools/call — 调用指定工具并返回结果。

    物理极限机制（模拟真实服务器行为）：
      1. 有界队列：semaphore.acquire(timeout) 超时则立即返回 503
      2. 拥塞惩罚：当 in_flight > max_workers 时，按超载程度增加处理延迟，
         模拟 CPU 上下文切换导致的吞吐崩溃（Death Spiral）。
    """
    global _in_flight_count
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    tool = registry.get(tool_name)
    if not tool:
        return make_error(req_id, -32602, f"Tool not found: {tool_name}")

    # 计量当前并发
    with _in_flight_lock:
        _in_flight_count += 1
        in_flight = _in_flight_count

    start = time.time()
    try:
        if _global_semaphore is not None:
            # 有界队列：等不到 worker 就直接拒绝
            acquired = _global_semaphore.acquire(timeout=_QUEUE_TIMEOUT)
            if not acquired:
                elapsed_ms = (time.time() - start) * 1000
                log.warning(f"✗ [{tool.category}] {tool_name} — QUEUE TIMEOUT "
                            f"({elapsed_ms:.0f}ms, in_flight={in_flight})")
                return make_error(req_id, -32000,
                    f"Server overloaded: queue timeout ({_QUEUE_TIMEOUT}s)",
                    {"overloaded": True, "in_flight": in_flight})

            try:
                # 拥塞惩罚：模拟 CPU 抢占导致的吞吐退化
                if _GLOBAL_MAX_WORKERS > 0:
                    excess = max(0, in_flight - _GLOBAL_MAX_WORKERS)
                    if excess > 0:
                        penalty_ms = min(excess ** 1.5 * _CONGESTION_FACTOR, 2000)
                        time.sleep(penalty_ms / 1000.0)

                # 工具执行延迟注入：模拟真实云工具的物理耗时
                delay_range = _TOOL_EXEC_DELAYS.get(tool.category)
                if delay_range and delay_range[1] > 0:
                    delay = random.uniform(delay_range[0], delay_range[1])
                    time.sleep(delay)

                result_text = tool.handler(arguments)
            finally:
                _global_semaphore.release()
        else:
            result_text = tool.handler(arguments)

        elapsed_ms = (time.time() - start) * 1000
        log.info(f"✓ [{tool.category}] {tool_name} — {elapsed_ms:.1f}ms")

        # 提取工具返回的外部 API 信号 (_signals)，合并到 _meta 传递给网关
        signals = {}
        try:
            result_obj = json.loads(result_text)
            if isinstance(result_obj, dict) and "_signals" in result_obj:
                signals = result_obj.pop("_signals")
                # 重新序列化去掉 _signals 的结果
                result_text = json.dumps(result_obj, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass

        return make_response(req_id, {
            "content": [{"type": "text", "text": result_text}],
            "_meta": {
                "tool": tool_name,
                "category": tool.category,
                "latency_ms": round(elapsed_ms, 2),
                **signals,
            },
        })
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        log.error(f"✗ [{tool.category}] {tool_name} — {elapsed_ms:.1f}ms — {e}")
        return make_error(req_id, -32603, f"Tool execution failed: {str(e)}")
    finally:
        with _in_flight_lock:
            _in_flight_count -= 1


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
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="监听端口 (default: 8080)")
    parser.add_argument("--mode", default="sterile", choices=["sterile", "battlefield", "real_llm"],
                        help="实验模式: sterile=无菌实验室(3工具), battlefield=真实战场(7工具), real_llm=DeepSeek真实验证(3工具)")
    parser.add_argument("--cpu-affinity", type=str, default=None,
                        help="CPU 亲和性核心列表, 如 '4,5,6,7,8,9,10,11,12,13,14,15'")
    parser.add_argument("--max-concurrency", type=int, default=8,
                        help="最大重量级工具并发数 (legacy, default: 8)")
    parser.add_argument("--max-workers", type=int, default=0,
                        help="全局最大并发 tools/call 数, 0=不限制 (模拟 ThreadPoolExecutor, default: 0)")
    parser.add_argument("--queue-timeout", type=float, default=1.0,
                        help="队列等待超时秒数，超时直接返回过载错误 (default: 1.0)")
    parser.add_argument("--congestion-factor", type=float, default=0.5,
                        help="拥塞惩罚系数: penalty_ms = excess^1.5 * factor (default: 0.5)")
    parser.add_argument("--tool-delay-lightweight", type=str, default="0,0",
                        help="轻量级工具额外延迟范围 (秒), 如 '1.5,3.0' (default: 0,0)")
    parser.add_argument("--tool-delay-medium", type=str, default="0,0",
                        help="中量级工具额外延迟范围 (秒), 如 '2.5,5.0' (default: 0,0)")
    parser.add_argument("--tool-delay-heavyweight", type=str, default="0,0",
                        help="重量级工具额外延迟范围 (秒), 如 '0,0' (default: 0,0)")
    args = parser.parse_args()

    # 设置重量级工具并发限制（旧逻辑保留）
    global _tool_semaphore
    _tool_semaphore = threading.Semaphore(args.max_concurrency)

    # 设置工具执行延迟注入
    global _TOOL_EXEC_DELAYS
    for cat, arg_val in [("lightweight", args.tool_delay_lightweight),
                         ("medium", args.tool_delay_medium),
                         ("heavyweight", args.tool_delay_heavyweight)]:
        lo, hi = [float(x) for x in arg_val.split(",")]
        if hi > 0:
            _TOOL_EXEC_DELAYS[cat] = (lo, hi)
    if _TOOL_EXEC_DELAYS:
        log.info(f"工具执行延迟注入: {_TOOL_EXEC_DELAYS}")

    # 设置全局并发限制（新逻辑：模拟 max_workers 瓶颈）
    global _global_semaphore, _GLOBAL_MAX_WORKERS, _QUEUE_TIMEOUT, _CONGESTION_FACTOR
    _QUEUE_TIMEOUT = args.queue_timeout
    _CONGESTION_FACTOR = args.congestion_factor
    if args.max_workers > 0:
        _global_semaphore = threading.Semaphore(args.max_workers)
        _GLOBAL_MAX_WORKERS = args.max_workers
        log.info(f"全局并发限制 (max_workers): {args.max_workers}")
        log.info(f"队列超时: {_QUEUE_TIMEOUT}s, 拥塞系数: {_CONGESTION_FACTOR}")
    else:
        _global_semaphore = None

    # 设置 CPU 亲和性 (实验资源隔离)
    if args.cpu_affinity:
        cores = [int(c.strip()) for c in args.cpu_affinity.split(",")]
        set_cpu_affinity(cores)

    load_tools(mode=args.mode)

    server = ThreadedHTTPServer((args.host, args.port), MCPRequestHandler)
    log.info("=" * 55)
    log.info(f"  MCP Tool Server started")
    log.info(f"  Address:  http://{args.host}:{args.port}")
    log.info(f"  Protocol: MCP {MCP_PROTOCOL_VERSION}")
    log.info(f"  MaxConc:  {args.max_concurrency}")
    log.info("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
