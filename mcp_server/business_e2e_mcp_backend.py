#!/usr/bin/env python3
"""Business E2E MCP Backend — MCP tools that call HTTP business services.

Architecture:
  PlanGate Gateway → this MCP backend → HTTP business_http_services.py → SQLite

This backend does NOT write to SQLite directly. All side effects go through
the HTTP service layer. Supports failure injection for recovery testing.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import threading
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | [mcp-biz] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("mcp-biz")

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "business-e2e-mcp-backend"
SERVER_VERSION = "1.0.0"

TOOLS = [
    "reserve_flight", "reserve_hotel", "confirm_itinerary",
    "reserve_inventory", "authorize_payment", "confirm_order",
    "open_ticket", "apply_credit", "close_ticket",
]

TOOL_DESCRIPTIONS = {
    "reserve_flight": "Reserve a flight seat (side-effecting)",
    "reserve_hotel": "Reserve a hotel room (side-effecting)",
    "confirm_itinerary": "Confirm and finalize the travel itinerary (side-effecting)",
    "reserve_inventory": "Reserve inventory for the order (side-effecting)",
    "authorize_payment": "Authorize payment for the order (side-effecting)",
    "confirm_order": "Confirm and finalize the order (side-effecting)",
    "open_ticket": "Open a support ticket (side-effecting)",
    "apply_credit": "Apply credit to customer account (side-effecting)",
    "close_ticket": "Close the support ticket (side-effecting)",
}

HTTP_SERVICE_URL = "http://127.0.0.1:18081"

_failure_seen: set = set()
_failure_lock = threading.Lock()

FORCE_FAILURE_AFTER_STEP: int | None = None
FORCE_FAILURE_SESSION_ID: str | None = None
FORCE_FAILURE_TYPE: str = "recoverable_queue_timeout"
FORCE_FAILURE_STEP_HIT: bool = False


def set_force_failure(session_id: str, after_step: int, failure_type: str = "recoverable_queue_timeout"):
    global FORCE_FAILURE_AFTER_STEP, FORCE_FAILURE_SESSION_ID, FORCE_FAILURE_TYPE, FORCE_FAILURE_STEP_HIT
    FORCE_FAILURE_AFTER_STEP = after_step
    FORCE_FAILURE_SESSION_ID = session_id
    FORCE_FAILURE_TYPE = failure_type
    FORCE_FAILURE_STEP_HIT = False


def clear_force_failure():
    global FORCE_FAILURE_AFTER_STEP, FORCE_FAILURE_SESSION_ID, FORCE_FAILURE_TYPE, FORCE_FAILURE_STEP_HIT
    FORCE_FAILURE_AFTER_STEP = None
    FORCE_FAILURE_SESSION_ID = None
    FORCE_FAILURE_TYPE = "recoverable_queue_timeout"
    FORCE_FAILURE_STEP_HIT = False


def maybe_inject_failure(req_id, tool_name, arguments, call_meta, session_id, step_index):
    # Priority 1: arguments.__failure_injection (survives gateway forwarding)
    injection = {}
    arg_inj = arguments.get("__failure_injection") if isinstance(arguments, dict) else None
    if isinstance(arg_inj, dict):
        injection.update(arg_inj)

    if not injection:
        meta = call_meta if isinstance(call_meta, dict) else {}
        meta_inj = meta.get("failure_injection")
        if isinstance(meta_inj, dict):
            injection.update(meta_inj)

    # Priority 2: global force failure for deterministic recovery probe
    if not injection and FORCE_FAILURE_AFTER_STEP is not None:
        if session_id == FORCE_FAILURE_SESSION_ID and step_index == FORCE_FAILURE_AFTER_STEP + 1:
            inject_key = f"force:{session_id}:step{step_index}"
            with _failure_lock:
                if inject_key in _failure_seen:
                    return None
                _failure_seen.add(inject_key)
            global FORCE_FAILURE_STEP_HIT
            FORCE_FAILURE_STEP_HIT = True
            log.warning("deterministic force failure session=%s step=%d type=%s",
                        session_id, step_index, FORCE_FAILURE_TYPE)
            if FORCE_FAILURE_TYPE == "terminal_business_rule":
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": "Business rule failure: injected terminal",
                              "data": {"failure_category": "terminal_business_rule", "retryable": False}},
                }
            else:
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32000, "message": "Server overloaded: injected recoverable",
                              "data": {"failure_category": "recoverable_queue_timeout", "overloaded": True, "retryable": True}},
                }

    if not injection.get("inject_failure"):
        return None

    inject_key = str(injection.get("inject_key") or f"adhoc:{time.time_ns()}")
    with _failure_lock:
        if inject_key in _failure_seen:
            return None
        _failure_seen.add(inject_key)

    failure_type = str(injection.get("failure_type") or "recoverable_queue_timeout")
    if failure_type == "terminal_business_rule":
        reason = str(injection.get("business_reason") or "business rule violation")
        log.warning("injected terminal failure key=%s reason=%s", inject_key, reason)
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32603, "message": f"Business rule failure: {reason}",
                      "data": {"failure_category": "terminal_business_rule", "business_reason": reason, "retryable": False}},
        }
    else:
        log.warning("injected recoverable failure key=%s type=%s", inject_key, failure_type)
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32000, "message": f"Server overloaded: {failure_type}",
                      "data": {"failure_category": "recoverable_queue_timeout", "overloaded": True, "retryable": True}},
        }


def call_http_service(tool_name: str, session_id: str, workflow_id: str,
                      workflow_type: str, step_index: int, idempotency_key: str,
                      payload: dict | None = None) -> dict:
    endpoint_map = {
        "reserve_flight": "/travel/reserve_flight",
        "reserve_hotel": "/travel/reserve_hotel",
        "confirm_itinerary": "/travel/confirm_itinerary",
        "reserve_inventory": "/order/reserve_inventory",
        "authorize_payment": "/order/authorize_payment",
        "confirm_order": "/order/confirm_order",
        "open_ticket": "/support/open_ticket",
        "apply_credit": "/support/apply_credit",
        "close_ticket": "/support/close_ticket",
    }
    endpoint = endpoint_map.get(tool_name, f"/{tool_name}")
    url = f"{HTTP_SERVICE_URL}{endpoint}"
    body = {
        "session_id": session_id,
        "workflow_id": workflow_id,
        "workflow_type": workflow_type,
        "step_index": step_index,
        "tool_name": tool_name,
        "idempotency_key": idempotency_key,
        "payload": payload or {},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class BusinessMCPHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Empty request body"}}, 400)
            return

        raw = self.rfile.read(content_length)
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
            return

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if method == "initialize":
            self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }})
        elif method == "tools/list":
            tools = []
            for name in TOOLS:
                tools.append({
                    "name": name,
                    "description": TOOL_DESCRIPTIONS.get(name, name),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "workflow_id": {"type": "string"},
                            "workflow_type": {"type": "string"},
                            "step_index": {"type": "integer"},
                            "idempotency_key": {"type": "string"},
                            "payload": {"type": "object"},
                        },
                    },
                })
            self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            call_meta = params.get("_meta") or {}

            if tool_name not in TOOLS:
                self._send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Tool not found: {tool_name}"}})
                return

            session_id = str(arguments.get("session_id", call_meta.get("session_id", "")))
            workflow_id = str(arguments.get("workflow_id", ""))
            workflow_type = str(arguments.get("workflow_type", ""))
            step_index = int(arguments.get("step_index", 0))
            idempotency_key = str(arguments.get("idempotency_key", call_meta.get("idempotency_key", "")))
            payload = arguments.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}

            if not idempotency_key:
                idempotency_key = f"{session_id}:{workflow_id}:{step_index}:{tool_name}"

            # Check failure injection
            params_with_id = dict(params)
            params_with_id["_req_id"] = req_id
            error_resp = maybe_inject_failure(req_id, tool_name, arguments, call_meta, session_id, step_index)
            if error_resp is not None:
                self._send_json(error_resp)
                return

            try:
                svc_result = call_http_service(
                    tool_name, session_id, workflow_id, workflow_type,
                    step_index, idempotency_key, payload,
                )
                result_text = json.dumps(svc_result, ensure_ascii=False)

                se_meta = {
                    "side_effect_committed": True,
                    "side_effect_id": str(svc_result.get("side_effect_id", "")),
                    "duplicate_side_effect": svc_result.get("duplicate_side_effect", 0),
                    "idempotency_key": idempotency_key,
                }

                self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "_meta": {"tool": tool_name, "category": "heavy", **se_meta},
                }})
            except Exception as e:
                log.error("http service call failed tool=%s: %s", tool_name, e)
                self._send_json({"jsonrpc": "2.0", "id": req_id, "error": {
                    "code": -32603, "message": f"HTTP service error: {str(e)}",
                }}, 500)
        elif method == "ping":
            self._send_json({"jsonrpc": "2.0", "id": req_id, "result": {}})
        else:
            self._send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}, 404)

    def do_GET(self):
        self._send_json({
            "status": "ok",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocol": MCP_PROTOCOL_VERSION,
            "tools_count": len(TOOLS),
            "tools": TOOLS,
            "http_service_url": HTTP_SERVICE_URL,
        })

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def main():
    parser = argparse.ArgumentParser(description="Business E2E MCP Backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--http-service-url", default="http://127.0.0.1:18081")
    args = parser.parse_args()

    global HTTP_SERVICE_URL
    HTTP_SERVICE_URL = args.http_service_url

    server = ThreadedHTTPServer((args.host, args.port), BusinessMCPHandler)
    log.info("=" * 55)
    log.info("  Business E2E MCP Backend started")
    log.info("  Address:         http://%s:%d", args.host, args.port)
    log.info("  HTTP Service:    %s", HTTP_SERVICE_URL)
    log.info("  Tools:           %d", len(TOOLS))
    log.info("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
