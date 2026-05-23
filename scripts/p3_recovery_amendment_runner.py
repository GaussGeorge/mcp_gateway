#!/usr/bin/env python3
"""Controlled P3 runner for PlanGate-R / AR recovery-amendment experiments."""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import hmac
import json
import os
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
SERVER_PY = ROOT_DIR / "mcp_server" / "server.py"

DEFAULT_BACKEND_PORT = 8080
DEFAULT_GATEWAY_PORT = 9601
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_BUDGET = 10000
DEFAULT_FAIL_STEP_INDEX = 2
DEFAULT_FAILURE_TYPE = "backend_timeout"
DEFAULT_COMMITMENT_MODE = "optional"
DEFAULT_MAX_SESSIONS = 0
DEFAULT_PRICE_STEP = 40
DEFAULT_RECOVERY_RETRY_ATTEMPTS = 12
DEFAULT_RECOVERY_RETRY_DELAY_SEC = 0.2
DEFAULT_STEP_RETRY_ATTEMPTS = 4
DEFAULT_STEP_RETRY_DELAY_SEC = 0.15

POLICY_BASE = "plangate_base"
POLICY_R = "plangate_r"
POLICY_AR = "plangate_ar"
POLICY_NAIVE = "naive_retry"
ALL_POLICIES = [POLICY_BASE, POLICY_R, POLICY_AR, POLICY_NAIVE]

SCENARIO_MAIN = "main"
INVALID_AMENDMENT_KINDS = [
    "modify_completed_prefix",
    "unknown_tool",
    "budget_overflow",
    "dag_cycle",
    "stale_parent",
    "checkpoint_hash_mismatch",
]

aiohttp = None


class MissingDependencyError(RuntimeError):
    pass


def ensure_aiohttp() -> Any:
    global aiohttp
    if aiohttp is not None:
        return aiohttp
    try:
        import aiohttp as aiohttp_module
    except ModuleNotFoundError as exc:
        raise MissingDependencyError(
            "missing Python dependency 'aiohttp'. Activate .venv first or run "
            "`python -m pip install -r requirements.txt` from the repository root."
        ) from exc
    aiohttp = aiohttp_module
    return aiohttp


def now_ms() -> float:
    return time.time() * 1000.0


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def commitment_token_hash(token: str) -> str:
    return b64url_encode(hashlib.sha256(token.encode("utf-8")).digest())


def decode_commitment_token_unverified(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed commitment token")
    payload = json.loads(b64url_decode(parts[1]).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("commitment payload is not an object")
    return payload


def sign_commitment_token(secret: str, claims: Dict[str, Any]) -> str:
    header = {
        "alg": "HS256",
        "typ": "plangate.commitment",
        "v": int(claims["v"]),
    }
    header_part = b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_part = b64url_encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signed_part = f"{header_part}.{payload_part}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signed_part, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{b64url_encode(signature)}"


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_values = sorted(float(v) for v in values)
    pos = (len(sorted_values) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    if low == high:
        return sorted_values[low]
    weight = pos - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def base_plan_steps() -> List[Dict[str, Any]]:
    return [
        {
            "step_id": "s1",
            "tool_name": "calculate",
            "depends_on": [],
            "arguments": {"operation": "multiply", "a": 7, "b": 6},
        },
        {
            "step_id": "s2",
            "tool_name": "web_fetch",
            "depends_on": ["s1"],
            "arguments": {
                "url": "https://docs.example.com/mcp",
                "max_length": 300,
                "simulate_rtt_ms": 20,
            },
        },
        {
            "step_id": "s3",
            "tool_name": "web_fetch",
            "depends_on": ["s2"],
            "arguments": {
                "url": "https://api.example.com/status",
                "max_length": 240,
                "simulate_rtt_ms": 20,
            },
        },
        {
            "step_id": "s4",
            "tool_name": "web_fetch",
            "depends_on": ["s3"],
            "arguments": {
                "url": "https://example.com",
                "max_length": 220,
                "simulate_rtt_ms": 20,
            },
        },
        {
            "step_id": "s5",
            "tool_name": "web_fetch",
            "depends_on": ["s4"],
            "arguments": {
                "url": "https://blog.example.com/cloud-computing",
                "max_length": 320,
                "simulate_rtt_ms": 20,
            },
        },
    ]


def amended_suffix_steps(base_step: int = DEFAULT_FAIL_STEP_INDEX) -> List[Dict[str, Any]]:
    plan = base_plan_steps()
    if base_step <= 0 or base_step >= len(plan):
        raise ValueError(f"base_step must be in [1, {len(plan) - 1}], got {base_step}")

    suffix: List[Dict[str, Any]] = []
    previous_id = plan[base_step - 1]["step_id"]
    for index in range(base_step, len(plan)):
        step_id = f"s{index + 1}_retry"
        suffix.append(
            {
                "step_id": step_id,
                "tool_name": "web_fetch",
                "depends_on": [previous_id],
            }
        )
        previous_id = step_id
    return suffix


def dag_header(session_id: str, budget: int, steps: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "budget": budget,
        "steps": [
            {
                "step_id": step["step_id"],
                "tool_name": step["tool_name"],
                "depends_on": list(step.get("depends_on", [])),
            }
            for step in steps
        ],
    }


def build_step_arguments(
    step: Dict[str, Any],
    inject_failure: bool,
    failure_type: str,
    fail_once_key: str,
) -> Dict[str, Any]:
    arguments = dict(step.get("arguments", {}))
    if inject_failure:
        arguments["_meta"] = {
            "inject_failure": True,
            "failure_type": failure_type,
            "fail_once_key": fail_once_key,
        }
    return arguments


def build_legal_amendment(
    session_id: str,
    parent_claims: Dict[str, Any],
    parent_token: str,
    base_step: int,
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "amendment_id": f"amend-{session_id}",
        "base_step": base_step,
        "base_plan_hash": parent_claims["plan_hash"],
        "parent_commitment_digest": commitment_token_hash(parent_token),
        "reason": "tool_failure",
        "budget_delta": 0,
        "replacement_suffix": amended_suffix_steps(base_step),
    }


def build_synthetic_v2_parent(
    secret: str,
    base_claims: Dict[str, Any],
    parent_token: str,
    checkpoint_hash: str,
    amendment_id: str,
) -> str:
    now = int(time.time())
    claims = {
        "v": 2,
        "typ": "ps_amended_commitment",
        "sid": base_claims["sid"],
        "plan_hash": base_claims["plan_hash"],
        "price_hash": base_claims["price_hash"],
        "budget": base_claims.get("budget", DEFAULT_BUDGET),
        "total_cost": base_claims["total_cost"],
        "total_steps": base_claims["total_steps"],
        "iat": now,
        "exp": max(now + 300, int(base_claims.get("exp", now + 300))),
        "policy_version": base_claims.get("policy_version", "plangate-v1"),
        "node_id": base_claims.get("node_id", "runner"),
        "state_store": base_claims.get("state_store", "local"),
        "recovery_enabled": bool(base_claims.get("recovery_enabled", True)),
        "amendment_version": 1,
        "amendment_id": amendment_id,
        "parent_commitment_hash": commitment_token_hash(parent_token),
        "delta_hash": f"synthetic-delta-{amendment_id}",
        "amendment_chain_hash": f"synthetic-chain-{amendment_id}",
        "checkpoint_hash": checkpoint_hash,
        "base_step": DEFAULT_FAIL_STEP_INDEX,
    }
    return sign_commitment_token(secret, claims)


def build_invalid_amendment_case(
    kind: str,
    secret: str,
    session_id: str,
    parent_token: str,
    parent_claims: Dict[str, Any],
    base_step: int,
    budget: int,
) -> Tuple[Dict[str, Any], str]:
    amendment = build_legal_amendment(session_id, parent_claims, parent_token, base_step)
    token_override = parent_token
    first_retry_num = base_step + 1
    parent_dep = base_plan_steps()[base_step - 1]["step_id"] if base_step > 0 else ""

    if kind == "modify_completed_prefix":
        amendment["replacement_suffix"][0]["step_id"] = "s1"
    elif kind == "unknown_tool":
        amendment["replacement_suffix"][0]["tool_name"] = "ghost_tool"
    elif kind == "budget_overflow":
        amendment["budget_delta"] = 1
        replacement: List[Dict[str, Any]] = []
        previous_id = parent_dep
        for offset in range(5):
            step_id = f"s{first_retry_num + offset}_retry"
            replacement.append(
                {
                    "step_id": step_id,
                    "tool_name": "mock_heavy",
                    "depends_on": [previous_id] if previous_id else [],
                }
            )
            previous_id = step_id
        amendment["replacement_suffix"] = replacement
    elif kind == "dag_cycle":
        step_a = f"s{first_retry_num}_retry"
        step_b = f"s{first_retry_num + 1}_retry"
        amendment["replacement_suffix"] = [
            {"step_id": step_a, "tool_name": "web_fetch", "depends_on": [step_b]},
            {"step_id": step_b, "tool_name": "web_fetch", "depends_on": [step_a]},
        ]
    elif kind == "stale_parent":
        token_override = build_synthetic_v2_parent(
            secret,
            parent_claims,
            parent_token,
            "stale-checkpoint-hash",
            f"stale-{session_id}",
        )
    elif kind == "checkpoint_hash_mismatch":
        token_override = build_synthetic_v2_parent(
            secret,
            parent_claims,
            parent_token,
            "wrong-checkpoint-hash",
            f"checkpoint-{session_id}",
        )
    else:
        raise ValueError(f"unknown invalid amendment kind: {kind}")

    return amendment, token_override


def gateway_binary_path() -> Path:
    return ROOT_DIR / ("gateway.exe" if sys.platform == "win32" else "gateway")


def gateway_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def normalize_gateway_urls(values: Sequence[str]) -> List[str]:
    urls: List[str] = []
    seen = set()
    for value in values:
        for part in str(value).split(","):
            item = part.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            urls.append(item)
    return urls


class GatewayRouter:
    def __init__(self, urls: Sequence[str], routing: str) -> None:
        normalized = normalize_gateway_urls(urls)
        if not normalized:
            raise ValueError("at least one gateway URL is required")
        self.urls = normalized
        self.routing = routing
        self._rng = random.Random(1337)
        self._round_robin = 0
        self._sticky: Dict[str, str] = {}

    def choose(self, session_id: str, *, phase: str = "", request_index: int = 0) -> str:
        del phase, request_index
        if len(self.urls) == 1 or self.routing == "single":
            return self.urls[0]
        if self.routing == "sticky":
            if session_id not in self._sticky:
                digest = hashlib.sha256(session_id.encode("utf-8")).digest()
                slot = int.from_bytes(digest[:4], "big") % len(self.urls)
                self._sticky[session_id] = self.urls[slot]
            return self._sticky[session_id]
        if self.routing == "round_robin":
            url = self.urls[self._round_robin % len(self.urls)]
            self._round_robin += 1
            return url
        if self.routing == "random":
            return self._rng.choice(self.urls)
        raise ValueError(f"unsupported routing mode: {self.routing}")


def ping_payload() -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": "ping", "method": "ping"}


def build_gateway() -> Path:
    binary = gateway_binary_path()
    cmd = ["go", "build", "-o", str(binary), "./cmd/gateway"]
    env = os.environ.copy()
    env["GOCACHE"] = str(ROOT_DIR / ".gocache")
    Path(env["GOCACHE"]).mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, cwd=ROOT_DIR, check=True, capture_output=True, text=True, env=env)
    return binary


def wait_for_jsonrpc_ready(url: str, timeout_sec: float = 30.0) -> None:
    deadline = time.time() + timeout_sec
    last_error = None
    while time.time() < deadline:
        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                data=json.dumps(ping_payload()).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("jsonrpc") == "2.0":
                return
        except Exception as exc:  # pragma: no cover - readiness races are environment-specific
            last_error = exc
            time.sleep(0.5)
        raise RuntimeError(f"startup timeout for {url}: {last_error}")


@dataclass
class ServiceHandles:
    backend_proc: Optional[subprocess.Popen]
    gateway_proc: Optional[subprocess.Popen]
    gateway_url: str
    backend_log: Optional[Any] = None
    gateway_log: Optional[Any] = None

    def close(self) -> None:
        stop_process(self.gateway_proc)
        stop_process(self.backend_proc)
        if self.gateway_log:
            self.gateway_log.close()
        if self.backend_log:
            self.backend_log.close()


def stop_process(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def start_local_services(args: argparse.Namespace) -> ServiceHandles:
    log_dir = Path(args.results_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    backend_log = open(log_dir / "backend.log", "w", encoding="utf-8")
    gateway_log = open(log_dir / "gateway.log", "w", encoding="utf-8")

    backend_cmd = [
        sys.executable,
        str(SERVER_PY),
        "--host",
        args.host,
        "--port",
        str(args.backend_port),
        "--max-workers",
        str(args.backend_max_workers),
        "--queue-timeout",
        "1.0",
        "--congestion-factor",
        "0.5",
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=ROOT_DIR,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    if backend_proc.poll() is not None:
        raise RuntimeError("backend failed to start")
    wait_for_jsonrpc_ready(gateway_url(args.host, args.backend_port))

    binary = build_gateway()
    gateway_cmd = [
        str(binary),
        "--mode",
        "mcpdp",
        "--host",
        args.host,
        "--port",
        str(args.gateway_port),
        "--backend",
        gateway_url(args.host, args.backend_port),
        "--node-id",
        f"p3-local:{args.gateway_port}",
        "--commitment-token-mode",
        DEFAULT_COMMITMENT_MODE,
        "--commitment-token-secret",
        args.commitment_secret,
        "--enable-recovery=true",
        "--plan-amendment-mode",
        "recovery-only",
        "--plan-amendment-require-commitment=true",
        "--plan-amendment-max-count",
        "3",
        "--plan-amendment-max-budget-delta",
        "0",
        "--plangate-max-sessions",
        str(DEFAULT_MAX_SESSIONS),
        "--plangate-price-step",
        str(DEFAULT_PRICE_STEP),
    ]
    gateway_proc = subprocess.Popen(
        gateway_cmd,
        cwd=ROOT_DIR,
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    gw_url = gateway_url(args.host, args.gateway_port)
    wait_for_jsonrpc_ready(gw_url)
    return ServiceHandles(
        backend_proc=backend_proc,
        gateway_proc=gateway_proc,
        gateway_url=gw_url,
        backend_log=backend_log,
        gateway_log=gateway_log,
    )


async def post_jsonrpc(
    session: aiohttp.ClientSession,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout_sec: float,
) -> Tuple[Dict[str, Any], Dict[str, str], float]:
    started = now_ms()
    async with session.post(
        url,
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_sec),
    ) as resp:
        body = await resp.json()
        return body, dict(resp.headers), now_ms() - started


def jsonrpc_payload(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": f"req-{time.time_ns()}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
            "_meta": {
                "tokens": DEFAULT_BUDGET,
                "name": "p3-runner",
                "method": tool_name,
            },
        },
    }


def recovery_payload() -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": f"recovery-{time.time_ns()}",
        "method": "tools/call",
        "params": {"name": "noop"},
    }


def success_response(body: Dict[str, Any]) -> bool:
    return body.get("error") in (None, False)


def error_message(body: Dict[str, Any]) -> str:
    error_obj = body.get("error") or {}
    message = error_obj.get("message", "")
    data = error_obj.get("data")
    if isinstance(data, str) and data:
        return f"{message} {data}".strip()
    return str(message)


def is_retryable_recovery_response(body: Dict[str, Any]) -> bool:
    if success_response(body):
        return False
    message = error_message(body).lower()
    retry_markers = (
        "still active",
        "active_checkpoint",
        "cannot resume a live session",
        "no checkpoint found",
        "recovery is already in progress",
    )
    return any(marker in message for marker in retry_markers)


def is_retryable_backend_blip(body: Dict[str, Any]) -> bool:
    if success_response(body):
        return False
    message = error_message(body).lower()
    retry_markers = (
        "dial tcp",
        "connection refused",
        "actively refused",
        "connectex",
        "econnrefused",
    )
    return any(marker in message for marker in retry_markers)


def extract_error_flags(body: Dict[str, Any]) -> Tuple[bool, bool]:
    state_miss = False
    duplicate_admission = False
    error_obj = body.get("error") or {}
    if not isinstance(error_obj, dict):
        return state_miss, duplicate_admission

    try:
        code = int(error_obj.get("code", 0) or 0)
    except (TypeError, ValueError):
        code = 0

    data = error_obj.get("data")
    if isinstance(data, dict):
        state_miss = bool(data.get("state_miss", False))
        duplicate_admission = bool(data.get("duplicate_admission", False))
    if code == -32010:
        state_miss = True
    return state_miss, duplicate_admission


def apply_record_flags_from_response(
    record: Dict[str, Any],
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> None:
    commitment_status = str((headers or {}).get("X-Commitment-Status", "")).strip().lower()
    if commitment_status == "invalid":
        record["commitment_invalid"] = 1
    elif commitment_status == "mismatch":
        record["commitment_mismatch"] = 1
    elif commitment_status == "expired":
        record["commitment_expired"] = 1

    state_miss, duplicate_admission = extract_error_flags(body)
    if state_miss:
        record["state_miss"] = 1
    if duplicate_admission:
        record["duplicate_admission"] = 1


def make_step_row(
    *,
    session_id: str,
    actual_session_id: str,
    policy: str,
    scenario: str,
    failure_rate: float,
    phase: str,
    step_id: str,
    tool_name: str,
    status: str,
    gateway_url_value: str,
    commitment_status: str,
    amendment_status: str,
    state_miss: bool,
    duplicate_admission: bool,
    is_reexecuted: bool,
    latency_ms: float,
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "actual_session_id": actual_session_id,
        "policy": policy,
        "scenario": scenario,
        "failure_rate": failure_rate,
        "phase": phase,
        "step_id": step_id,
        "tool_name": tool_name,
        "status": status,
        "gateway_url": gateway_url_value,
        "commitment_status": commitment_status,
        "amendment_status": amendment_status,
        "state_miss": int(state_miss),
        "duplicate_admission": int(duplicate_admission),
        "is_reexecuted": int(is_reexecuted),
        "latency_ms": round(latency_ms, 3),
    }


async def execute_plan_attempt(
    session: aiohttp.ClientSession,
    router: GatewayRouter,
    logical_session_id: str,
    actual_session_id: str,
    policy: str,
    scenario: str,
    failure_rate: float,
    budget: int,
    steps: Sequence[Dict[str, Any]],
    failure_enabled: bool,
    failure_type: str,
    fail_step_index: int,
    attempted_steps: Optional[set] = None,
    phase: str = "initial",
    inherited_token: str = "",
) -> Tuple[List[Dict[str, Any]], str, Optional[int], Optional[Dict[str, Any]], float]:
    rows: List[Dict[str, Any]] = []
    commitment_token = inherited_token
    first_failure_index: Optional[int] = None
    failure_body: Optional[Dict[str, Any]] = None
    total_latency = 0.0
    attempted_steps = attempted_steps if attempted_steps is not None else set()

    for index, step in enumerate(steps):
        current_gw_url = router.choose(logical_session_id, phase=phase, request_index=index)
        inject_failure = failure_enabled and index == fail_step_index
        fail_once_key = f"{logical_session_id}:{step['step_id']}"
        arguments = build_step_arguments(step, inject_failure, failure_type, fail_once_key)
        payload = jsonrpc_payload(step["tool_name"], arguments)
        headers = {
            "Content-Type": "application/json",
            "X-Session-ID": actual_session_id,
            "X-Session-Step": str(index),
        }
        if index == 0:
            headers["X-Plan-DAG"] = json.dumps(dag_header(actual_session_id, budget, steps), separators=(",", ":"))
            headers["X-Total-Budget"] = str(budget)
        elif commitment_token:
            headers["X-Commitment-Token"] = commitment_token

        body, resp_headers, latency_ms = await post_step_request_with_retry(
            session,
            current_gw_url,
            payload,
            headers,
            inject_failure,
        )
        total_latency += latency_ms
        if index == 0 and resp_headers.get("X-Commitment-Token"):
            commitment_token = resp_headers["X-Commitment-Token"]

        attempted_before = step["step_id"] in attempted_steps
        attempted_steps.add(step["step_id"])
        step_state_miss, step_duplicate_admission = extract_error_flags(body)
        if success_response(body):
            status = "success"
        elif step_state_miss:
            status = "state_miss"
        else:
            status = "error"
        rows.append(
            make_step_row(
                session_id=logical_session_id,
                actual_session_id=actual_session_id,
                policy=policy,
                scenario=scenario,
                failure_rate=failure_rate,
                phase=phase,
                step_id=step["step_id"],
                tool_name=step["tool_name"],
                status=status,
                gateway_url_value=current_gw_url,
                commitment_status=resp_headers.get("X-Commitment-Status", ""),
                amendment_status=resp_headers.get("X-Amendment-Status", ""),
                state_miss=step_state_miss,
                duplicate_admission=step_duplicate_admission,
                is_reexecuted=attempted_before,
                latency_ms=latency_ms,
            )
        )
        if not success_response(body):
            first_failure_index = index
            failure_body = body
            break

    return rows, commitment_token, first_failure_index, failure_body, total_latency


async def send_recovery_request(
    session: aiohttp.ClientSession,
    gw_url: str,
    session_id: str,
    commitment_token: str,
    amendment: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, str], float]:
    headers = {
        "Content-Type": "application/json",
        "X-Recovery-Mode": "resume",
        "X-Session-ID": session_id,
    }
    if commitment_token:
        headers["X-Commitment-Token"] = commitment_token
    if amendment is not None:
        headers["X-Plan-Amendment"] = json.dumps(amendment, separators=(",", ":"))
    return await post_jsonrpc(session, gw_url, recovery_payload(), headers, timeout_sec=60.0)


async def send_recovery_request_with_retry(
    session: aiohttp.ClientSession,
    router: GatewayRouter,
    session_id: str,
    commitment_token: str,
    amendment: Optional[Dict[str, Any]],
    max_attempts: int = DEFAULT_RECOVERY_RETRY_ATTEMPTS,
    delay_sec: float = DEFAULT_RECOVERY_RETRY_DELAY_SEC,
) -> Tuple[Dict[str, Any], Dict[str, str], float, str]:
    total_latency = 0.0
    body: Dict[str, Any] = {}
    resp_headers: Dict[str, str] = {}
    last_url = ""
    for attempt in range(max_attempts):
        last_url = router.choose(session_id, phase="recovery", request_index=attempt)
        body, resp_headers, latency = await send_recovery_request(
            session,
            last_url,
            session_id,
            commitment_token,
            amendment,
        )
        total_latency += latency
        if success_response(body) or not is_retryable_recovery_response(body) or attempt == max_attempts - 1:
            return body, resp_headers, total_latency, last_url
        await asyncio.sleep(delay_sec * (attempt + 1))
    return body, resp_headers, total_latency, last_url


async def post_step_request_with_retry(
    session: aiohttp.ClientSession,
    gw_url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    inject_failure: bool,
    max_attempts: int = DEFAULT_STEP_RETRY_ATTEMPTS,
    delay_sec: float = DEFAULT_STEP_RETRY_DELAY_SEC,
) -> Tuple[Dict[str, Any], Dict[str, str], float]:
    total_latency = 0.0
    body: Dict[str, Any] = {}
    resp_headers: Dict[str, str] = {}
    for attempt in range(max_attempts):
        body, resp_headers, latency = await post_jsonrpc(
            session,
            gw_url,
            payload,
            headers,
            timeout_sec=30.0,
        )
        total_latency += latency
        if inject_failure or success_response(body) or not is_retryable_backend_blip(body) or attempt == max_attempts - 1:
            return body, resp_headers, total_latency
        await asyncio.sleep(delay_sec * (attempt + 1))
    return body, resp_headers, total_latency


def make_session_record_template(
    *,
    session_id: str,
    policy: str,
    scenario: str,
    failure_rate: float,
    failure_injected: bool,
    failure_step: Optional[int],
    failure_type: str,
    n_steps: int,
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "policy": policy,
        "scenario": scenario,
        "failure_rate": failure_rate,
        "n_steps": n_steps,
        "failure_injected": int(failure_injected),
        "failure_step": "" if failure_step is None else failure_step,
        "failure_type": failure_type if failure_injected else "",
        "initial_success": 0,
        "recovery_attempted": 0,
        "recovery_success": 0,
        "amendment_submitted": 0,
        "amendment_accepted": 0,
        "amendment_rejected": 0,
        "v2_commitment_issued": 0,
        "stale_parent_rejected": 0,
        "invalid_amendment_rejected": 0,
        "executed_after_rejected_amendment": 0,
        "state_miss": 0,
        "duplicate_admission": 0,
        "commitment_invalid": 0,
        "commitment_mismatch": 0,
        "commitment_expired": 0,
        "saved_steps": 0,
        "total_tool_calls": 0,
        "status": "pending",
        "latency_ms": 0.0,
        "false_accept": 0,
        "error_reason": "",
        "amendment_kind": "" if scenario == SCENARIO_MAIN else scenario,
    }


def commitment_version(token: str) -> int:
    if not token:
        return 0
    try:
        return int(decode_commitment_token_unverified(token).get("v", 0))
    except Exception:
        return 0


def synthesize_recovery_rows(
    *,
    logical_session_id: str,
    actual_session_id: str,
    policy: str,
    scenario: str,
    failure_rate: float,
    gw_url: str,
    steps: Sequence[Dict[str, Any]],
    commitment_status: str,
    amendment_status: str,
    executed_steps: int,
    total_latency_ms: float,
    attempted_steps: set,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if executed_steps <= 0:
        return rows
    per_step_latency = total_latency_ms / max(executed_steps, 1)
    for step in list(steps)[:executed_steps]:
        rows.append(
            make_step_row(
                session_id=logical_session_id,
                actual_session_id=actual_session_id,
                policy=policy,
                scenario=scenario,
                failure_rate=failure_rate,
                phase="recovery",
                step_id=step["step_id"],
                tool_name=step["tool_name"],
                status="success",
                gateway_url_value=gw_url,
                commitment_status=commitment_status,
                amendment_status=amendment_status,
                state_miss=False,
                duplicate_admission=False,
                is_reexecuted=step["step_id"] in attempted_steps,
                latency_ms=per_step_latency,
            )
        )
        attempted_steps.add(step["step_id"])
    return rows


async def run_single_session(
    session: aiohttp.ClientSession,
    args: argparse.Namespace,
    router: GatewayRouter,
    policy: str,
    failure_rate: float,
    index: int,
    scenario: str,
    failure_injected: Optional[bool] = None,
    submit_amendment: Optional[bool] = None,
    invalid_kind: str = "",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    logical_session_id = f"{policy}-fr{failure_rate:.1f}-{scenario}-{index:04d}"
    steps = base_plan_steps()
    attempted_steps: set = set()
    if failure_injected is None:
        failure_injected = scenario != SCENARIO_MAIN
    record = make_session_record_template(
        session_id=logical_session_id,
        policy=policy,
        scenario=scenario,
        failure_rate=failure_rate,
        failure_injected=failure_injected,
        failure_step=args.fail_step_index if failure_injected else None,
        failure_type=args.failure_type,
        n_steps=len(steps),
    )

    phase_rows, parent_token, failed_at, failure_body, initial_latency = await execute_plan_attempt(
        session=session,
        router=router,
        logical_session_id=logical_session_id,
        actual_session_id=logical_session_id,
        policy=policy,
        scenario=scenario,
        failure_rate=failure_rate,
        budget=args.budget,
        steps=steps,
        failure_enabled=failure_injected,
        failure_type=args.failure_type,
        fail_step_index=args.fail_step_index,
        attempted_steps=attempted_steps,
        phase="initial",
    )
    rows = list(phase_rows)
    record["total_tool_calls"] = len(phase_rows)
    record["latency_ms"] = round(initial_latency, 3)

    if failed_at is None:
        record["initial_success"] = 1
        record["status"] = "success"
        return record, rows

    if not failure_injected and policy == POLICY_BASE:
        apply_record_flags_from_response(record, failure_body or {})
        record["status"] = "failed_unexpected"
        record["error_reason"] = json.dumps(failure_body.get("error", {}), ensure_ascii=False)
        return record, rows

    remaining_original = steps[failed_at:]

    if policy == POLICY_BASE:
        record["status"] = "failed_no_recovery"
        return record, rows

    if policy == POLICY_NAIVE:
        retry_session_id = f"{logical_session_id}-retry"
        retry_rows, _retry_token, retry_failed_at, retry_failure_body, retry_latency = await execute_plan_attempt(
            session=session,
            router=router,
            logical_session_id=logical_session_id,
            actual_session_id=retry_session_id,
            policy=policy,
            scenario=scenario,
            failure_rate=failure_rate,
            budget=args.budget,
            steps=steps,
            failure_enabled=False,
            failure_type=args.failure_type,
            fail_step_index=args.fail_step_index,
            attempted_steps=attempted_steps,
            phase="naive_retry",
        )
        rows.extend(retry_rows)
        record["total_tool_calls"] += len(retry_rows)
        record["latency_ms"] = round(record["latency_ms"] + retry_latency, 3)
        if retry_failed_at is None:
            record["status"] = "success"
        else:
            apply_record_flags_from_response(record, retry_failure_body or {})
            record["status"] = "naive_retry_failed"
            record["error_reason"] = json.dumps(retry_failure_body.get("error", {}), ensure_ascii=False)
        return record, rows

    record["recovery_attempted"] = 1

    amendment: Optional[Dict[str, Any]] = None
    recovery_token = parent_token
    expected_recovery_steps = remaining_original
    if submit_amendment is None:
        submit_amendment = scenario != SCENARIO_MAIN

    if policy == POLICY_AR and scenario == SCENARIO_MAIN and submit_amendment:
        if not parent_token:
            record["status"] = "missing_parent_token"
            record["error_reason"] = "step-0 commitment token missing"
            return record, rows
        parent_claims = decode_commitment_token_unverified(parent_token)
        amendment = build_legal_amendment(
            logical_session_id,
            parent_claims,
            parent_token,
            failed_at,
        )
        expected_recovery_steps = amended_suffix_steps(failed_at)
        record["amendment_submitted"] = 1
    elif policy == POLICY_AR and scenario != SCENARIO_MAIN:
        if not parent_token:
            record["status"] = "missing_parent_token"
            record["error_reason"] = "step-0 commitment token missing"
            return record, rows
        parent_claims = decode_commitment_token_unverified(parent_token)
        amendment, recovery_token = build_invalid_amendment_case(
            invalid_kind,
            args.commitment_secret,
            logical_session_id,
            parent_token,
            parent_claims,
            failed_at,
            args.budget,
        )
        record["amendment_submitted"] = 1

    body, resp_headers, recovery_latency, recovery_gw_url = await send_recovery_request_with_retry(
        session,
        router,
        logical_session_id,
        recovery_token,
        amendment,
    )
    record["latency_ms"] = round(record["latency_ms"] + recovery_latency, 3)
    commitment_status = resp_headers.get("X-Commitment-Status", "")
    amendment_status = resp_headers.get("X-Amendment-Status", "")
    apply_record_flags_from_response(record, body, resp_headers)

    if success_response(body):
        result = body.get("result") or {}
        executed_steps = int(result.get("executed_steps", 0) or 0)
        record["recovery_success"] = 1
        record["saved_steps"] = int(result.get("saved_compute_steps", 0) or 0)
        record["total_tool_calls"] += executed_steps
        record["status"] = "success"
        if amendment is not None:
            record["amendment_accepted"] = 1 if amendment_status == "accepted" else 0
            new_token = resp_headers.get("X-Commitment-Token", "")
            record["v2_commitment_issued"] = 1 if commitment_version(new_token) == 2 else 0
        rows.extend(
            synthesize_recovery_rows(
                logical_session_id=logical_session_id,
                actual_session_id=logical_session_id,
                policy=policy,
                scenario=scenario,
                failure_rate=failure_rate,
                gw_url=recovery_gw_url,
                steps=expected_recovery_steps,
                commitment_status=commitment_status,
                amendment_status=amendment_status,
                executed_steps=executed_steps,
                total_latency_ms=recovery_latency,
                attempted_steps=attempted_steps,
            )
        )
        if scenario != SCENARIO_MAIN:
            record["false_accept"] = 1
        return record, rows

    error_obj = body.get("error") or {}
    reason = (
        resp_headers.get("X-Amendment-Error")
        or resp_headers.get("X-Commitment-Error")
        or error_message(body)
    )
    record["error_reason"] = str(reason)
    if amendment is None:
        record["status"] = "recovery_failed"
        return record, rows

    record["amendment_rejected"] = 1 if amendment_status == "rejected" else 0
    record["status"] = "amendment_rejected"
    if scenario != SCENARIO_MAIN and record["amendment_rejected"]:
        record["invalid_amendment_rejected"] = 1
        if invalid_kind == "stale_parent":
            record["stale_parent_rejected"] = 1
    data = error_obj.get("data") or {}
    if isinstance(data, dict):
        executed_after = int(data.get("recovered_so_far", 0) or 0)
        record["executed_after_rejected_amendment"] = executed_after
        if executed_after > 0:
            record["false_accept"] = 1
    return record, rows


async def run_policy_matrix(
    args: argparse.Namespace,
    router: GatewayRouter,
    policy: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    session_records: List[Dict[str, Any]] = []
    step_rows: List[Dict[str, Any]] = []
    async with aiohttp.ClientSession() as session:
        for failure_rate in args.failure_rate:
            sem = asyncio.Semaphore(args.concurrency)
            failure_quota = min(args.sessions, max(0, int(round(args.sessions * failure_rate))))
            amendment_quota = min(failure_quota, max(0, int(round(failure_quota * args.amendment_rate))))

            async def run_main(index: int) -> None:
                async with sem:
                    record, rows = await run_single_session(
                        session=session,
                        args=args,
                        router=router,
                        policy=policy,
                        failure_rate=failure_rate,
                        index=index,
                        scenario=SCENARIO_MAIN,
                        failure_injected=index < failure_quota,
                        submit_amendment=index < amendment_quota,
                    )
                    session_records.append(record)
                    step_rows.extend(rows)

            await asyncio.gather(*(run_main(i) for i in range(args.sessions)))

            if policy == POLICY_AR and args.adversarial_amendment_rate > 0:
                for offset, invalid_kind in enumerate(INVALID_AMENDMENT_KINDS):
                    record, rows = await run_single_session(
                        session=session,
                        args=args,
                        router=router,
                        policy=policy,
                        failure_rate=failure_rate,
                        index=100000 + offset,
                        scenario=invalid_kind,
                        invalid_kind=invalid_kind,
                    )
                    session_records.append(record)
                    step_rows.extend(rows)
    return session_records, step_rows


def ensure_clean_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    ensure_clean_dir(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def summarize_console(records: Sequence[Dict[str, Any]]) -> str:
    if not records:
        return "no sessions"
    success = sum(1 for row in records if row["status"] == "success" and row["scenario"] == SCENARIO_MAIN)
    main = [row for row in records if row["scenario"] == SCENARIO_MAIN]
    avg_calls = statistics.mean(float(row["total_tool_calls"]) for row in main) if main else 0.0
    v2 = sum(int(row["v2_commitment_issued"]) for row in records)
    rejected = sum(int(row["invalid_amendment_rejected"]) for row in records if row["scenario"] != SCENARIO_MAIN)
    return f"main_success={success}/{len(main)} avg_tool_calls={avg_calls:.2f} v2_tokens={v2} invalid_rejected={rejected}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled P3 recovery/amendment runner")
    parser.add_argument("--policies", nargs="+", default=[POLICY_NAIVE, POLICY_R, POLICY_AR], choices=ALL_POLICIES)
    parser.add_argument("--sessions", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--failure-rate", nargs="+", type=float, default=[0.1, 0.2, 0.3])
    parser.add_argument("--failure-type", choices=["backend_timeout", "tool_unavailable", "backend_overloaded"], default=DEFAULT_FAILURE_TYPE)
    parser.add_argument("--amendment-rate", type=float, default=1.0)
    parser.add_argument("--adversarial-amendment-rate", type=float, default=1.0)
    parser.add_argument("--results-dir", type=str, default=str(ROOT_DIR / "results" / "p3_controlled"))
    parser.add_argument("--commitment-secret", type=str, required=True)
    parser.add_argument("--host", type=str, default=DEFAULT_GATEWAY_HOST)
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument("--backend-max-workers", type=int, default=16)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--fail-step-index", type=int, default=DEFAULT_FAIL_STEP_INDEX)
    parser.add_argument("--gateway-url", type=str, default="")
    parser.add_argument("--gateway-urls", nargs="+", default=[])
    parser.add_argument("--routing", choices=["single", "random", "sticky", "round_robin"], default="single")
    parser.add_argument("--start-services", action="store_true", help="Force local backend/gateway startup")
    parser.add_argument("--no-start-services", action="store_true", help="Require an already-running gateway")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.sessions <= 0:
        parser.error("--sessions must be > 0")
    if args.concurrency <= 0:
        parser.error("--concurrency must be > 0")
    if args.fail_step_index < 0:
        parser.error("--fail-step-index must be >= 0")
    for rate in args.failure_rate:
        if rate < 0 or rate > 1:
            parser.error("all --failure-rate values must be in [0, 1]")
    if args.amendment_rate < 0 or args.amendment_rate > 1:
        parser.error("--amendment-rate must be in [0, 1]")
    if args.adversarial_amendment_rate < 0 or args.adversarial_amendment_rate > 1:
        parser.error("--adversarial-amendment-rate must be in [0, 1]")
    return args


async def async_main(args: argparse.Namespace) -> int:
    results_dir = Path(args.results_dir)
    ensure_clean_dir(results_dir)

    external_gateway_urls = normalize_gateway_urls([args.gateway_url, *args.gateway_urls])
    start_services = args.start_services or (not args.no_start_services and not external_gateway_urls)
    services: Optional[ServiceHandles] = None
    if args.dry_run:
        plan = {
            "policies": args.policies,
            "sessions_per_cell": args.sessions,
            "concurrency": args.concurrency,
            "failure_rate": args.failure_rate,
            "start_services": start_services,
            "gateway_urls": external_gateway_urls or [gateway_url(args.host, args.gateway_port)],
            "routing": args.routing,
            "invalid_suite": INVALID_AMENDMENT_KINDS if args.adversarial_amendment_rate > 0 else [],
        }
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    ensure_aiohttp()

    try:
        if start_services:
            services = start_local_services(args)
            external_gateway_urls = [services.gateway_url]
        elif not external_gateway_urls:
            raise RuntimeError("gateway URL(s) required when --no-start-services is set")

        router = GatewayRouter(external_gateway_urls, args.routing)

        for policy in args.policies:
            records, rows = await run_policy_matrix(args, router, policy)
            policy_dir = results_dir / policy
            write_csv(policy_dir / "sessions.csv", records)
            write_csv(policy_dir / "steps.csv", rows)
            print(f"[P3] policy={policy} {summarize_console(records)}")
    finally:
        if services is not None:
            services.close()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        return asyncio.run(async_main(args))
    except MissingDependencyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
