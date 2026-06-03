#!/usr/bin/env python3
"""Business HTTP Services — SQLite-backed side-effect services for E2E smoke.

Architecture:
  MCP tools (business_e2e_mcp_backend.py) → HTTP → this service → SQLite

Each endpoint receives session/workflow metadata, writes to SQLite,
and returns JSON. All write operations use short transactions.
Supports idempotency replay via idempotency_key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | [http-svc] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("http-svc")

DB_PATH: Path | None = None
_db_local = threading.local()
_db_write_lock = threading.Lock()

ENDPOINTS = {
    "/travel/reserve_flight": "orders",
    "/travel/reserve_hotel": "reservations",
    "/travel/confirm_itinerary": "orders",
    "/order/reserve_inventory": "reservations",
    "/order/authorize_payment": "payments",
    "/order/confirm_order": "orders",
    "/support/open_ticket": "tickets",
    "/support/apply_credit": "payments",
    "/support/close_ticket": "tickets",
}


def get_db() -> sqlite3.Connection:
    if not hasattr(_db_local, "conn") or _db_local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _db_local.conn = conn
    return _db_local.conn


def init_db(db_path: Path) -> None:
    global DB_PATH
    DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ok',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ok',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ok',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ok',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            side_effect_table TEXT NOT NULL,
            side_effect_id INTEGER NOT NULL,
            request_hash TEXT NOT NULL DEFAULT '',
            response_json TEXT NOT NULL DEFAULT '{}',
            replay_hits INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            session_mode TEXT NOT NULL DEFAULT 'react',
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'step_completed',
            status TEXT NOT NULL DEFAULT 'ok',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def _request_hash(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _handle_endpoint(endpoint: str, body: dict) -> dict:
    table = ENDPOINTS[endpoint]
    tool_name = endpoint.rsplit("/", 1)[-1]

    session_id = str(body.get("session_id", ""))
    workflow_id = str(body.get("workflow_id", ""))
    workflow_type = str(body.get("workflow_type", ""))
    idempotency_key = str(body.get("idempotency_key", ""))
    step_index = int(body.get("step_index", 0))
    payload = body.get("payload", {})

    if not idempotency_key:
        idempotency_key = f"{session_id}:{workflow_id}:{step_index}:{tool_name}"

    rhash = _request_hash(body)
    conn = get_db()

    # Idempotency check
    existing = conn.execute(
        "SELECT id, side_effect_id, response_json, replay_hits FROM idempotency_keys WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()

    if existing is not None:
        new_hits = existing["replay_hits"] + 1
        conn.execute(
            "UPDATE idempotency_keys SET replay_hits = ?, request_hash = ? WHERE idempotency_key = ?",
            (new_hits, rhash, idempotency_key),
        )
        conn.commit()
        resp = json.loads(existing["response_json"])
        resp["duplicate_side_effect"] = 0
        resp["side_effect_id"] = existing["side_effect_id"]
        resp["replay"] = True
        resp["replay_hits"] = new_hits
        log.info("idempotent replay tool=%s key=%s se_id=%s hits=%d",
                 tool_name, idempotency_key, existing["side_effect_id"], new_hits)
        return resp

    # Write side effect
    payload_json = json.dumps(payload, ensure_ascii=False)
    with _db_write_lock:
        cur = conn.execute(
            f"INSERT INTO {table} (session_id, workflow_id, workflow_type, step_index, tool_name, idempotency_key, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, workflow_id, workflow_type, step_index, tool_name, idempotency_key, payload_json),
        )
        side_effect_id = cur.lastrowid
        conn.commit()

    # Build response
    response = {
        "status": "ok",
        "tool": tool_name,
        "side_effect_table": table,
        "side_effect_id": side_effect_id,
        "idempotency_key": idempotency_key,
        "duplicate_side_effect": 0,
        "replay": False,
        "replay_hits": 0,
        "timestamp": time.time(),
    }

    # Tool-specific response fields
    if tool_name == "reserve_flight":
        response.update({"reservation_id": f"FL-{side_effect_id:06d}", "flight_number": f"UA{100 + (side_effect_id % 900)}"})
    elif tool_name == "reserve_hotel":
        response.update({"reservation_id": f"HT-{side_effect_id:06d}", "room_number": str(100 + (side_effect_id % 900))})
    elif tool_name == "confirm_itinerary":
        response.update({"confirmation_code": f"ITN-{side_effect_id:06d}", "status": "confirmed"})
    elif tool_name == "reserve_inventory":
        response.update({"reservation_id": f"INV-{side_effect_id:06d}", "items_reserved": 1 + (side_effect_id % 5)})
    elif tool_name == "authorize_payment":
        response.update({"auth_code": f"AUTH-{side_effect_id:06d}", "amount": round(100 + (side_effect_id % 2900), 2)})
    elif tool_name == "confirm_order":
        response.update({"order_id": f"ORD-{side_effect_id:06d}", "status": "confirmed"})
    elif tool_name == "open_ticket":
        response.update({"ticket_id": f"TKT-{side_effect_id:06d}", "status": "open"})
    elif tool_name == "apply_credit":
        response.update({"credit_amount": round(5 + (side_effect_id % 195), 2), "transaction_id": f"CRD-{side_effect_id:06d}"})
    elif tool_name == "close_ticket":
        response.update({"ticket_id": f"TKT-{side_effect_id:06d}", "status": "closed"})

    resp_json = json.dumps(response, ensure_ascii=False)

    # Record idempotency key
    conn.execute(
        "INSERT INTO idempotency_keys (idempotency_key, session_id, workflow_id, workflow_type, step_index, tool_name, "
        "side_effect_table, side_effect_id, request_hash, response_json, replay_hits) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (idempotency_key, session_id, workflow_id, workflow_type, step_index, tool_name,
         table, side_effect_id, rhash, resp_json),
    )
    conn.commit()

    log.info("side_effect tool=%s table=%s se_id=%s session=%s workflow=%s",
             tool_name, table, side_effect_id, session_id, workflow_id)
    return response


class BusinessServiceHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.rstrip("/") or "/"
        if path not in ENDPOINTS:
            self._send_json({"error": f"unknown endpoint: {path}"}, 404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json({"error": "empty body"}, 400)
            return

        raw = self.rfile.read(content_length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, 400)
            return

        try:
            result = _handle_endpoint(path, body)
            self._send_json(result)
        except Exception as e:
            log.error("endpoint error path=%s: %s", path, e)
            self._send_json({"error": str(e)}, 500)

    def do_GET(self):
        self._send_json({
            "status": "ok",
            "service": "business-http-services",
            "endpoints": sorted(ENDPOINTS.keys()),
        })

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def main():
    parser = argparse.ArgumentParser(description="Business HTTP Services (SQLite-backed)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--db-path", type=str, required=True, help="Path to SQLite database file")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    init_db(db_path)

    server = ThreadedHTTPServer((args.host, args.port), BusinessServiceHandler)
    log.info("=" * 55)
    log.info("  Business HTTP Services started")
    log.info("  Address: http://%s:%d", args.host, args.port)
    log.info("  DB:      %s", db_path)
    log.info("  WAL:     enabled  busy_timeout: 5000ms")
    log.info("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
