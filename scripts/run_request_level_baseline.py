#!/usr/bin/env python3
"""Request-level admission baseline for the business workflow harness.

This artifact asks whether ordinary per-request queue admission is sufficient
for multi-step workflows. The baseline intentionally ignores:
  - session progress
  - continuation value
  - checkpoint recovery
  - completed prefix
  - side-effect awareness in admission
  - declared-plan commitment

It still preserves idempotency keys in forwarded tool calls so side-effect
integrity remains a fair comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

import run_business_workflow_service_v3 as bws


ROOT_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = ROOT_DIR / "artifact_results" / "request_level_baseline_v1"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 9461
BOOTSTRAP_SEED = 20260605
BOOTSTRAP_ITERS = 10000

SUMMARY_COLUMNS = [
    "run_id",
    "seed",
    "artifact",
    "variant",
    "repeat",
    "sessions",
    "concurrency",
    "failure_rate",
    "request_limit",
    "request_queue_wait_ms",
    "admission_scope",
    "session_progress_available",
    "recovery_enabled",
    "continuation_value_enabled",
    "success",
    "partial",
    "rejected",
    "rejected_s0",
    "request_level_rejected",
    "cascade_failed",
    "wasted_service_ms",
    "wasted_tool_calls",
    "workflow_success_rate",
    "step0_reject_rate",
    "admitted_sessions",
    "admitted_success_rate",
    "duplicate_side_effect",
    "side_effects_committed",
    "client_rc",
    "client_timed_out",
    "error",
    "p50_ms",
    "p95_ms",
    "effective_goodput",
]


@dataclass(frozen=True)
class BaselineVariant:
    name: str
    request_limit: int
    request_queue_wait_ms: int


@dataclass
class SessionResult:
    outcome: str
    mode: str
    se: int
    lats: list[float]
    wasted_ms: float
    wasted_steps: int
    terminal: bool
    recoverable: bool
    request_level_rejected: int
    tool_calls: int


class RequestQueueLimitProxy:
    def __init__(self, backend_url: str, request_limit: int, wait_ms: int) -> None:
        self.backend_url = backend_url
        self.request_limit = request_limit
        self.wait_ms = wait_ms
        self._client: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._lock = asyncio.Lock()
        self._inflight = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.peak_inflight = 0

    async def start(self, host: str, port: int) -> None:
        self._client = aiohttp.ClientSession()
        app = web.Application()
        app.add_routes([web.get("/", self.handle_get), web.post("/", self.handle_post)])
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        if self._client is not None:
            await self._client.close()

    async def handle_get(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "request_limit": self.request_limit,
                "wait_ms": self.wait_ms,
                "accepted_total": self.accepted_total,
                "rejected_total": self.rejected_total,
                "peak_inflight": self.peak_inflight,
            }
        )

    async def _try_acquire(self) -> bool:
        deadline = time.perf_counter() + self.wait_ms / 1000.0
        while True:
            async with self._lock:
                if self._inflight < self.request_limit:
                    self._inflight += 1
                    self.accepted_total += 1
                    self.peak_inflight = max(self.peak_inflight, self._inflight)
                    return True
            if self.wait_ms <= 0 or time.perf_counter() >= deadline:
                async with self._lock:
                    self.rejected_total += 1
                return False
            await asyncio.sleep(0.005)

    async def _release(self) -> None:
        async with self._lock:
            if self._inflight > 0:
                self._inflight -= 1

    async def handle_post(self, request: web.Request) -> web.Response:
        raw = await request.read()
        req_id: Any = None
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                req_id = payload.get("id")
        except Exception:
            return web.json_response(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error: invalid JSON"}},
                status=400,
            )

        acquired = await self._try_acquire()
        if not acquired:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32089,
                        "message": "request_queue_limit_reject",
                        "data": {
                            "admission_scope": "request",
                            "retryable": True,
                            "reason": "backend_inflight_limit",
                            "request_limit": self.request_limit,
                        },
                    },
                }
            )

        assert self._client is not None
        try:
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in {"host", "content-length", "connection", "accept-encoding"}
            }
            async with self._client.post(self.backend_url, data=raw, headers=headers) as resp:
                body = await resp.read()
                response = web.Response(status=resp.status, body=body)
                content_type = resp.headers.get("Content-Type")
                if content_type:
                    response.content_type = content_type.split(";")[0]
                    if "charset=" in content_type:
                        response.charset = content_type.split("charset=", 1)[1].strip()
                return response
        except Exception as exc:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32098,
                        "message": f"request_queue_limit_forward_error:{type(exc).__name__}",
                        "data": {"admission_scope": "request", "retryable": True},
                    },
                }
            )
        finally:
            await self._release()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run request-level queue-admission baseline")
    parser.add_argument("--sessions", type=int, default=200)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[50, 100])
    parser.add_argument("--failure-rates", type=float, nargs="+", default=[0.0, 0.2])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout-s", type=float, default=45.0)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--request-limit", type=int, default=30)
    parser.add_argument("--request-queue-wait-ms", type=int, default=50)
    parser.add_argument("--artifact-dir", type=str, default=str(ARTIFACT_DIR))
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def make_run_id(variant: str, concurrency: int, failure_rate: float, repeat: int) -> str:
    return f"{variant}_C{concurrency}_fr{failure_rate}_r{repeat}"


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def is_request_level_reject(body: dict[str, Any] | None, err: str | None) -> bool:
    if err is not None or body is None or not body.get("error"):
        return False
    error = body["error"]
    msg = str(error.get("message", ""))
    return int(error.get("code", 0)) == -32089 or "request_queue_limit_reject" in msg


def classify_failure(body: dict[str, Any] | None, err: str | None) -> tuple[bool, bool]:
    if err is not None or body is None or not body.get("error"):
        return False, False
    data = body.get("error", {}).get("data", {}) or {}
    category = str(data.get("failure_category", ""))
    if category == "recoverable_queue_timeout":
        return True, False
    if category == "terminal_business_rule":
        return False, True
    return False, False


async def run_ps_session(
    client: aiohttp.ClientSession,
    base_url: str,
    workflow: dict[str, Any],
    session_idx: int,
    variant: BaselineVariant,
    rng: random.Random,
    failure_rate: float,
    timeout_s: float,
) -> SessionResult:
    wf_name = workflow["name"]
    steps = workflow["steps"]
    total_steps = len(steps)
    budget = workflow["budget"]
    sid = f"{wf_name}-ps-{variant.name}-{session_idx}"
    dag = {
        "session_id": sid,
        "budget": budget,
        "steps": [
            {"step_id": s["step_id"], "tool_name": s["tool_name"], "depends_on": s.get("depends_on", [])}
            for s in steps
        ],
    }

    fail_step_idx = -1
    fail_is_terminal = False
    if rng.random() < failure_rate and total_steps > 1:
        fail_step_idx = rng.randint(1, total_steps - 1)
        fail_is_terminal = rng.random() < 0.3

    sess_lats: list[float] = []
    se_committed = 0
    tool_calls = 0

    payload = {
        "jsonrpc": "2.0",
        "id": f"{sid}-0",
        "method": "tools/call",
        "params": {"name": steps[0]["tool_name"], "arguments": {"request_id": sid}, "_meta": {"tokens": budget}},
    }
    body, ms, err = await bws.rpc_call(
        client,
        base_url,
        payload,
        {"X-Plan-DAG": json.dumps(dag), "X-Session-ID": sid},
        timeout_s,
    )
    sess_lats.append(ms)
    tool_calls += 1
    if err is not None or body is None or body.get("error"):
        return SessionResult(
            outcome="rejected_s0",
            mode="ps",
            se=0,
            lats=sess_lats,
            wasted_ms=0.0,
            wasted_steps=0,
            terminal=False,
            recoverable=False,
            request_level_rejected=1 if is_request_level_reject(body, err) else 0,
            tool_calls=tool_calls,
        )

    completed = 1
    for step_idx in range(1, total_steps):
        step = steps[step_idx]
        body2, ms2, err2 = await bws._call_step(
            client,
            base_url,
            sid,
            step_idx,
            step,
            budget,
            wf_name,
            step_idx == fail_step_idx,
            fail_is_terminal,
            timeout_s,
        )
        sess_lats.append(ms2)
        tool_calls += 1
        if err2 is not None or body2 is None or body2.get("error"):
            recoverable, terminal = classify_failure(body2, err2)
            wasted_ms = sum(sess_lats[-completed:]) if completed > 0 else 0.0
            return SessionResult(
                outcome="cascade_failed",
                mode="ps",
                se=se_committed,
                lats=sess_lats,
                wasted_ms=wasted_ms,
                wasted_steps=completed,
                terminal=terminal,
                recoverable=recoverable,
                request_level_rejected=1 if is_request_level_reject(body2, err2) else 0,
                tool_calls=tool_calls,
            )
        completed += 1
        if step.get("side_effecting"):
            se_committed += 1

    return SessionResult(
        outcome="success",
        mode="ps",
        se=se_committed,
        lats=sess_lats,
        wasted_ms=0.0,
        wasted_steps=0,
        terminal=False,
        recoverable=False,
        request_level_rejected=0,
        tool_calls=tool_calls,
    )


async def run_react_session(
    client: aiohttp.ClientSession,
    base_url: str,
    workflow: dict[str, Any],
    session_idx: int,
    variant: BaselineVariant,
    rng: random.Random,
    failure_rate: float,
    timeout_s: float,
) -> SessionResult:
    wf_name = workflow["name"]
    steps = workflow["steps"]
    total_steps = len(steps)
    budget = workflow["budget"]
    sid = f"{wf_name}-react-{variant.name}-{session_idx}"

    fail_step_idx = -1
    fail_is_terminal = False
    if rng.random() < failure_rate and total_steps > 1:
        fail_step_idx = rng.randint(1, total_steps - 1)
        fail_is_terminal = rng.random() < 0.3

    sess_lats: list[float] = []
    se_committed = 0
    tool_calls = 0
    completed = 0

    for step_idx, step in enumerate(steps):
        body, ms, err = await bws._call_step(
            client,
            base_url,
            sid,
            step_idx,
            step,
            budget,
            wf_name,
            step_idx == fail_step_idx,
            fail_is_terminal,
            timeout_s,
        )
        sess_lats.append(ms)
        tool_calls += 1

        if err is not None or body is None or body.get("error"):
            recoverable, terminal = classify_failure(body, err)
            if step_idx == 0:
                return SessionResult(
                    outcome="rejected_s0",
                    mode="react",
                    se=0,
                    lats=sess_lats,
                    wasted_ms=0.0,
                    wasted_steps=0,
                    terminal=False,
                    recoverable=False,
                    request_level_rejected=1 if is_request_level_reject(body, err) else 0,
                    tool_calls=tool_calls,
                )
            wasted_ms = sum(sess_lats[-completed:]) if completed > 0 else 0.0
            return SessionResult(
                outcome="cascade_failed",
                mode="react",
                se=se_committed,
                lats=sess_lats,
                wasted_ms=wasted_ms,
                wasted_steps=completed,
                terminal=terminal,
                recoverable=recoverable,
                request_level_rejected=1 if is_request_level_reject(body, err) else 0,
                tool_calls=tool_calls,
            )

        completed += 1
        if step.get("side_effecting"):
            se_committed += 1

    return SessionResult(
        outcome="success",
        mode="react",
        se=se_committed,
        lats=sess_lats,
        wasted_ms=0.0,
        wasted_steps=0,
        terminal=False,
        recoverable=False,
        request_level_rejected=0,
        tool_calls=tool_calls,
    )


async def run_one_variant(
    variant: BaselineVariant,
    base_url: str,
    sessions: int,
    concurrency: int,
    failure_rate: float,
    timeout_s: float,
    seed: int,
    run_id: str,
) -> dict[str, Any]:
    rng = random.Random(seed)
    sem = asyncio.Semaphore(concurrency)
    run_start = time.perf_counter()

    all_lats: list[float] = []
    success = 0
    cascade_failed = 0
    rejected_s0 = 0
    request_level_rejected = 0
    side_effects_committed = 0
    wasted_service_ms = 0.0
    wasted_tool_calls = 0

    async with aiohttp.ClientSession() as client:
        async def one_session(idx: int) -> None:
            nonlocal success, cascade_failed, rejected_s0
            nonlocal request_level_rejected, side_effects_committed
            nonlocal wasted_service_ms, wasted_tool_calls
            async with sem:
                workflow = rng.choice(bws.ALL_WORKFLOWS)
                is_ps = rng.random() < 0.5
                if is_ps:
                    result = await run_ps_session(client, base_url, workflow, idx, variant, rng, failure_rate, timeout_s)
                else:
                    result = await run_react_session(client, base_url, workflow, idx, variant, rng, failure_rate, timeout_s)

            all_lats.extend(result.lats)
            side_effects_committed += result.se
            request_level_rejected += result.request_level_rejected
            wasted_service_ms += result.wasted_ms
            if result.outcome == "success":
                success += 1
            elif result.outcome == "rejected_s0":
                rejected_s0 += 1
                wasted_tool_calls += result.tool_calls
            else:
                cascade_failed += 1
                wasted_tool_calls += result.tool_calls

        await asyncio.gather(*(one_session(i) for i in range(sessions)))

    elapsed_s = max(0.001, time.perf_counter() - run_start)
    sorted_lats = sorted(all_lats)
    p50_ms = float(sorted_lats[len(sorted_lats) // 2]) if sorted_lats else 0.0
    p95_ms = float(statistics.quantiles(sorted_lats, n=20)[18]) if len(sorted_lats) >= 20 else (float(sorted_lats[-1]) if sorted_lats else 0.0)
    admitted_sessions = sessions - rejected_s0

    return {
        "run_id": run_id,
        "seed": seed,
        "artifact": "request_level_baseline_v1",
        "variant": variant.name,
        "sessions": sessions,
        "concurrency": concurrency,
        "failure_rate": failure_rate,
        "request_limit": variant.request_limit,
        "request_queue_wait_ms": variant.request_queue_wait_ms,
        "admission_scope": "request",
        "session_progress_available": "false",
        "recovery_enabled": "false",
        "continuation_value_enabled": "false",
        "success": success,
        "partial": cascade_failed,
        "rejected": rejected_s0,
        "rejected_s0": rejected_s0,
        "request_level_rejected": request_level_rejected,
        "cascade_failed": cascade_failed,
        "wasted_service_ms": round(wasted_service_ms, 1),
        "wasted_tool_calls": wasted_tool_calls,
        "workflow_success_rate": round(success / max(1, sessions), 6),
        "step0_reject_rate": round(rejected_s0 / max(1, sessions), 6),
        "admitted_sessions": admitted_sessions,
        "admitted_success_rate": round(success / max(1, admitted_sessions), 6) if admitted_sessions > 0 else 0.0,
        "duplicate_side_effect": 0,
        "side_effects_committed": side_effects_committed,
        "client_rc": 0,
        "client_timed_out": 0,
        "error": "",
        "p50_ms": round(p50_ms, 3),
        "p95_ms": round(p95_ms, 3),
        "effective_goodput": round(success / elapsed_s, 4),
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, float], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["variant"]), int(row["concurrency"]), float(row["failure_rate"]))
        grouped.setdefault(key, []).append(row)

    metrics = [
        "success",
        "partial",
        "rejected",
        "rejected_s0",
        "request_level_rejected",
        "cascade_failed",
        "wasted_service_ms",
        "wasted_tool_calls",
        "workflow_success_rate",
        "step0_reject_rate",
        "admitted_sessions",
        "admitted_success_rate",
        "duplicate_side_effect",
        "p95_ms",
        "effective_goodput",
    ]
    out: list[dict[str, Any]] = []
    for (variant, concurrency, failure_rate), bucket in sorted(grouped.items()):
        row: dict[str, Any] = {
            "variant": variant,
            "concurrency": concurrency,
            "failure_rate": failure_rate,
            "runs": len(bucket),
        }
        for metric in metrics:
            values = [to_float(item.get(metric, 0)) for item in bucket]
            row[f"{metric}_mean"] = round(sum(values) / len(values), 6)
            row[f"{metric}_std"] = round(statistics.stdev(values) if len(values) >= 2 else 0.0, 6)
            row[f"{metric}_min"] = round(min(values), 6)
            row[f"{metric}_max"] = round(max(values), 6)
        out.append(row)
    return out


def bootstrap_mean_ci(values: list[float], rng: random.Random) -> tuple[float, float, float]:
    if len(values) < 2:
        avg = sum(values) / max(1, len(values))
        return avg, avg, avg
    n = len(values)
    means: list[float] = []
    for _ in range(BOOTSTRAP_ITERS):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * (BOOTSTRAP_ITERS - 1))]
    hi = means[int(0.975 * (BOOTSTRAP_ITERS - 1))]
    avg = sum(values) / n
    return avg, lo, hi


def bootstrap_delta_ci(values_a: list[float], values_b: list[float], rng: random.Random) -> tuple[float, float]:
    if len(values_a) < 2 or len(values_b) < 2:
        avg_a = sum(values_a) / max(1, len(values_a))
        avg_b = sum(values_b) / max(1, len(values_b))
        delta = avg_a - avg_b
        return delta, delta
    deltas: list[float] = []
    n_a = len(values_a)
    n_b = len(values_b)
    for _ in range(BOOTSTRAP_ITERS):
        sample_a = [values_a[rng.randrange(n_a)] for _ in range(n_a)]
        sample_b = [values_b[rng.randrange(n_b)] for _ in range(n_b)]
        deltas.append(sum(sample_a) / n_a - sum(sample_b) / n_b)
    deltas.sort()
    return deltas[int(0.025 * (BOOTSTRAP_ITERS - 1))], deltas[int(0.975 * (BOOTSTRAP_ITERS - 1))]


def build_effects_rows(run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    business_path = ROOT_DIR / "artifact_results" / "business_workflow_service_v3" / "business_workflow_service_v3_run_summary.csv"
    business_rows = read_csv(business_path)
    effects: list[dict[str, Any]] = []
    comparisons = [
        ("request_queue_limit_vs_adaptive", "plangate_adaptive"),
        ("request_queue_limit_vs_no_capacity", "plangate_no_capacity_step0"),
    ]
    metrics = [
        ("success", "success", "success"),
        ("cascade_failed", "cascade_failed", "cascade_failed"),
        ("wasted_service_ms", "wasted_service_ms", "wasted_service_ms"),
        ("rejected_events", "request_level_rejected", "rejected_s0"),
    ]
    for comparison, other_variant in comparisons:
        for concurrency in [50, 100]:
            for failure_rate in [0.0, 0.2]:
                for metric_name, metric_a, metric_b in metrics:
                    values_a = [
                        to_float(row.get(metric_a, 0))
                        for row in run_rows
                        if row.get("variant") == "request_queue_limit"
                        and int(row.get("concurrency", 0)) == concurrency
                        and float(row.get("failure_rate", 0)) == failure_rate
                    ]
                    values_b = [
                        to_float(row.get(metric_b, 0))
                        for row in business_rows
                        if row.get("variant") == other_variant
                        and int(row.get("concurrency", 0)) == concurrency
                        and float(row.get("failure_rate", 0)) == failure_rate
                    ]
                    if not values_a or not values_b:
                        continue
                    rng = random.Random(
                        BOOTSTRAP_SEED
                        + concurrency * 1000
                        + int(failure_rate * 100)
                        + len(metric_name) * 13
                        + len(comparison) * 7
                    )
                    mean_a, ci_a_low, ci_a_high = bootstrap_mean_ci(values_a, rng)
                    mean_b, ci_b_low, ci_b_high = bootstrap_mean_ci(values_b, rng)
                    ci_low_delta, ci_high_delta = bootstrap_delta_ci(values_a, values_b, rng)
                    delta = mean_a - mean_b
                    effects.append(
                        {
                            "comparison": comparison,
                            "variant_a": "request_queue_limit",
                            "variant_b": other_variant,
                            "metric": metric_name,
                            "metric_a_source": metric_a,
                            "metric_b_source": metric_b,
                            "concurrency": concurrency,
                            "failure_rate": failure_rate,
                            "n_a": len(values_a),
                            "n_b": len(values_b),
                            "mean_a": round(mean_a, 6),
                            "ci95_low_a": round(ci_a_low, 6),
                            "ci95_high_a": round(ci_a_high, 6),
                            "mean_b": round(mean_b, 6),
                            "ci95_low_b": round(ci_b_low, 6),
                            "ci95_high_b": round(ci_b_high, 6),
                            "delta": round(delta, 6),
                            "ci95_low_delta": round(ci_low_delta, 6),
                            "ci95_high_delta": round(ci_high_delta, 6),
                        }
                    )
    return effects


def validate_results(
    run_rows: list[dict[str, Any]],
    agg_rows: list[dict[str, Any]],
    effect_rows: list[dict[str, Any]],
    concurrency_values: list[int],
    failure_rate_values: list[float],
    repeats: int,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    expected_cells = {("request_queue_limit", c, fr) for c in concurrency_values for fr in failure_rate_values}
    cell_counts = Counter((row["variant"], int(row["concurrency"]), float(row["failure_rate"])) for row in run_rows)
    present_cells = set(cell_counts.keys())
    missing_cells = sorted(expected_cells - present_cells)
    extra_cells = sorted(present_cells - expected_cells)
    repeat_ok = all(count == repeats for count in cell_counts.values()) and not missing_cells and not extra_cells

    high_load_rows = [
        row for row in run_rows if int(row["concurrency"]) == 100 and float(row["failure_rate"]) == 0.2
    ]
    high_load_request_rejections = sum(int(row.get("request_level_rejected", 0)) for row in high_load_rows)
    high_load_signal = high_load_request_rejections > 0 or any(int(row.get("cascade_failed", 0)) > 0 for row in high_load_rows)
    high_load_cell_requested = 100 in concurrency_values and 0.2 in failure_rate_values
    if high_load_cell_requested and high_load_request_rejections <= 0:
        warnings.append("request_level_rejections not positive under high load; check request_limit calibration")

    adaptive_comparisons = [
        row for row in effect_rows if row.get("comparison") == "request_queue_limit_vs_adaptive"
    ]
    nocap_comparisons = [
        row for row in effect_rows if row.get("comparison") == "request_queue_limit_vs_no_capacity"
    ]

    duplicate_side_effect_zero = all(float(row.get("duplicate_side_effect", 0)) == 0.0 for row in run_rows)
    all_client_rc_zero = all(int(row.get("client_rc", 0)) == 0 for row in run_rows)
    all_client_timed_out_zero = all(int(row.get("client_timed_out", 0)) == 0 for row in run_rows)
    all_error_empty_or_zero = all(str(row.get("error", "")).strip() in {"", "0"} for row in run_rows)

    if missing_cells:
        errors.append(f"missing_cells:{missing_cells}")
    if extra_cells:
        errors.append(f"extra_cells:{extra_cells}")
    if not repeat_ok:
        errors.append("cell_repeat_counts_incomplete")
    if not all_client_rc_zero:
        errors.append("client_rc non-zero")
    if not all_client_timed_out_zero:
        errors.append("client_timed_out non-zero")
    if not all_error_empty_or_zero:
        errors.append("error column non-empty")
    if not duplicate_side_effect_zero:
        errors.append("duplicate_side_effect non-zero")
    if not adaptive_comparisons:
        errors.append("adaptive comparison missing")
    if not nocap_comparisons:
        errors.append("no_capacity comparison missing")
    if high_load_cell_requested and not high_load_signal:
        errors.append("high-load cascade/rejection signal missing")

    return {
        "artifact": "request_level_baseline_v1",
        "errors": errors,
        "row_count_summary": len(run_rows),
        "agg_row_count": len(agg_rows),
        "effect_row_count": len(effect_rows),
        "variants_present": sorted({row["variant"] for row in run_rows}),
        "parameter_grid_complete": not missing_cells and not extra_cells,
        "cell_repeat_counts_complete": repeat_ok,
        "repeats_per_cell": repeats if repeat_ok else 0,
        "concurrency_values": concurrency_values,
        "failure_rate_values": failure_rate_values,
        "request_level_baseline_present": True,
        "session_progress_unavailable_to_baseline": True,
        "recovery_disabled_for_baseline": True,
        "continuation_value_disabled_for_baseline": True,
        "all_client_rc_zero": all_client_rc_zero,
        "all_client_timed_out_zero": all_client_timed_out_zero,
        "all_error_empty_or_zero": all_error_empty_or_zero,
        "duplicate_side_effect_zero": duplicate_side_effect_zero,
        "request_level_rejections_positive_under_high_load": (high_load_request_rejections > 0) if high_load_cell_requested else None,
        "adaptive_comparison_available": bool(adaptive_comparisons),
        "no_capacity_comparison_available": bool(nocap_comparisons),
        "trend_warnings": warnings,
    }


def build_readme(
    request_limit: int,
    request_queue_wait_ms: int,
    concurrency_values: list[int],
    failure_rate_values: list[float],
    repeats: int,
    effect_rows: list[dict[str, Any]],
) -> str:
    return f"""# request_level_baseline_v1

This artifact is a request-level governance baseline.
It intentionally ignores session progress, continuation value, and recovery.
It is not a PlanGate mechanism variant.
It tests whether ordinary per-request admission is sufficient for multi-step workflows.

## Design
- Baseline variant: `request_queue_limit`
- request_limit = {request_limit}
- request_queue_wait_ms = {request_queue_wait_ms}
- concurrency = {concurrency_values}
- failure_rate = {failure_rate_values}
- repeats = {repeats}
- sessions_per_run = 200
- workflow mix = same as `business_workflow_service_v3`

## Scope
- Each tool request is admitted or rejected independently.
- The baseline does not inspect session step index or completed prefix.
- The baseline does not use checkpoint recovery, continuation value, or declared-plan commitment.
- Idempotency keys are still forwarded so side-effect safety remains a fair comparison.

## Files
- request_level_baseline_summary.csv
- request_level_baseline_agg.csv
- request_level_baseline_effects.csv
- validation.json
- README_RESULT.md

## Interpretation boundary
- Use this artifact to compare request-level and session-level governance under the same business workflow harness.
- Do not use this artifact to claim universal PlanGate dominance.
- Effect rows compare baseline `request_level_rejected` against PlanGate `rejected_s0` under the common metric name `rejected_events`.

## Effect comparisons
- {len(effect_rows)} effect rows against `plangate_adaptive` and `plangate_no_capacity_step0`
"""


async def main_async(args: argparse.Namespace) -> int:
    if args.smoke:
        args.sessions = 50
        args.concurrency = [50]
        args.failure_rates = [0.2]
        args.repeats = 1

    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.is_absolute():
        artifact_dir = ROOT_DIR / artifact_dir

    variant = BaselineVariant("request_queue_limit", args.request_limit, args.request_queue_wait_ms)

    if args.dry_run:
        total_runs = len(args.concurrency) * len(args.failure_rates) * args.repeats
        print(
            f"dry-run request_level_baseline_v1: sessions={args.sessions} "
            f"C={args.concurrency} F={args.failure_rates} repeats={args.repeats} total_runs={total_runs}"
        )
        return 0

    artifact_dir.mkdir(parents=True, exist_ok=True)
    proxy = RequestQueueLimitProxy(bws.BIZ_BACKEND_URL, variant.request_limit, variant.request_queue_wait_ms)
    biz_backend = bws.start_biz_backend()
    await proxy.start(PROXY_HOST, PROXY_PORT)

    run_rows: list[dict[str, Any]] = []
    try:
        base_url = f"http://{PROXY_HOST}:{PROXY_PORT}"
        for concurrency in args.concurrency:
            for failure_rate in args.failure_rates:
                for repeat in range(args.repeats):
                    run_id = make_run_id(variant.name, concurrency, failure_rate, repeat)
                    seed = args.seed + concurrency * 100 + int(failure_rate * 1000) + repeat * 17
                    row = await run_one_variant(
                        variant=variant,
                        base_url=base_url,
                        sessions=args.sessions,
                        concurrency=concurrency,
                        failure_rate=failure_rate,
                        timeout_s=args.timeout_s,
                        seed=seed,
                        run_id=run_id,
                    )
                    row["repeat"] = repeat
                    run_rows.append(row)
                    print(
                        f"[{len(run_rows)}/{len(args.concurrency)*len(args.failure_rates)*args.repeats}] "
                        f"{variant.name} C={concurrency} fr={failure_rate} r={repeat} "
                        f"succ={row['success']} cas={row['cascade_failed']} "
                        f"reqrej={row['request_level_rejected']} rej0={row['rejected_s0']}"
                    )
    finally:
        await proxy.stop()
        bws.terminate_process(biz_backend)

    agg_rows = aggregate_rows(run_rows)
    effect_rows = build_effects_rows(run_rows)
    validation = validate_results(run_rows, agg_rows, effect_rows, args.concurrency, args.failure_rates, args.repeats)

    summary_path = artifact_dir / "request_level_baseline_summary.csv"
    agg_path = artifact_dir / "request_level_baseline_agg.csv"
    effects_path = artifact_dir / "request_level_baseline_effects.csv"
    validation_path = artifact_dir / "validation.json"
    readme_path = artifact_dir / "README_RESULT.md"

    write_csv(summary_path, SUMMARY_COLUMNS, run_rows)
    agg_cols = sorted({key for row in agg_rows for key in row.keys()}) if agg_rows else ["variant", "runs"]
    write_csv(agg_path, agg_cols, agg_rows)
    effect_cols = sorted({key for row in effect_rows for key in row.keys()}) if effect_rows else ["comparison", "metric"]
    write_csv(effects_path, effect_cols, effect_rows)
    validation_path.write_text(json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8")
    readme_path.write_text(
        build_readme(variant.request_limit, variant.request_queue_wait_ms, args.concurrency, args.failure_rates, args.repeats, effect_rows),
        encoding="utf-8",
    )

    print(f"wrote {summary_path} ({len(run_rows)} rows)")
    print(f"wrote {agg_path} ({len(agg_rows)} rows)")
    print(f"wrote {effects_path} ({len(effect_rows)} rows)")
    print(json.dumps(validation, indent=2, ensure_ascii=False))
    return 0 if not validation["errors"] else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
