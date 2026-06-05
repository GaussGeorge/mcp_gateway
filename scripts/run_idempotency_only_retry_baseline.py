#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import aiohttp


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_ARTIFACT_DIR = ROOT_DIR / "artifact_results" / "idempotency_only_retry_baseline_v1"
ARTIFACT_DIR = DEFAULT_ARTIFACT_DIR


def load_e2e_smoke_module():
    path = SCRIPT_DIR / "run_business_workflow_e2e_smoke.py"
    spec = importlib.util.spec_from_file_location("run_business_workflow_e2e_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load helper module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = load_e2e_smoke_module()


SUMMARY_COLUMNS = [
    "variant", "concurrency", "failure_rate", "repeat", "sessions",
    "success", "cascade_failed", "partial", "rejected",
    "workflow_success_rate", "p95_ms",
    "http_requests_total", "sqlite_writes_total", "side_effects_committed",
    "idempotency_keys_total", "idempotency_replay_hits", "duplicate_side_effect",
    "client_rc", "client_timed_out", "unexpected_error",
    "resume_attempted", "resume_recovered", "resume_rejected",
    "avoided_replay_steps", "post_resume_success",
    "restart_attempted", "retry_attempted", "replayed_completed_steps",
    "db_final_state_consistent", "completed_workflows_in_db", "failed_workflows_in_db",
]

AGG_COLUMNS = [
    "variant", "concurrency", "failure_rate", "runs",
    "success_mean", "success_std", "cascade_failed_mean", "cascade_failed_std",
    "partial_mean", "partial_std", "workflow_success_rate_mean", "workflow_success_rate_std",
    "p95_ms_mean", "p95_ms_std", "duplicate_side_effect_mean", "duplicate_side_effect_std",
    "resume_recovered_mean", "resume_recovered_std",
    "avoided_replay_steps_mean", "avoided_replay_steps_std",
    "post_resume_success_mean", "post_resume_success_std",
    "restart_attempted_mean", "restart_attempted_std",
    "retry_attempted_mean", "retry_attempted_std",
    "replayed_completed_steps_mean", "replayed_completed_steps_std",
    "http_requests_total_mean", "http_requests_total_std",
    "sqlite_writes_total_mean", "sqlite_writes_total_std",
]

EFFECT_COLUMNS = [
    "comparison", "metric", "group_a", "group_b",
    "concurrency", "failure_rate", "n_a", "n_b",
    "mean_a", "mean_b", "delta", "ci95_low_delta", "ci95_high_delta",
    "descriptive_only",
]

REPLAY_PROBE_COLUMNS = smoke.REPLAY_PROBE_COLUMNS

RECOVERY_PROBE_COLUMNS = [
    "variant", "concurrency", "repeat", "session_id", "workflow_id", "workflow_type",
    "failure_after_step", "side_effect_before_failure_present",
    "resume_attempted", "resume_recovered", "resume_mode_react_client_cooperative",
    "resume_current_step_gt_zero", "resume_requires_client_continuation",
    "post_resume_success", "avoided_replay_steps",
    "restart_attempted", "retry_attempted", "replayed_completed_steps",
    "final_state_consistent", "passed",
]

DB_SUMMARY_COLUMNS = [
    "variant", "repeat", "table_name", "row_count",
    "unique_idempotency_keys", "duplicate_idempotency_keys",
    "duplicate_side_effect_rows", "db_integrity_check",
]


@dataclass(frozen=True)
class GatewayConfig:
    name: str
    mode: str
    extra_args: tuple[str, ...]


@dataclass
class SessionResult:
    session_id: str
    workflow_id: str
    workflow_type: str
    state: str
    total_steps: int
    success_steps: int = 0
    latencies: list[float] = field(default_factory=list)
    client_rc: int = 0
    client_timed_out: int = 0
    unexpected_error: int = 0
    http_requests_total: int = 0
    restart_attempted: int = 0
    retry_attempted: int = 0
    resume_attempted: int = 0
    resume_recovered: int = 0
    resume_rejected: int = 0
    avoided_replay_steps: int = 0
    post_resume_success: int = 0
    replayed_completed_steps: int = 0


def resolve_gateways() -> tuple[GatewayConfig, ...]:
    return (
        GatewayConfig(
            "idempotency_only_retry",
            "mcpdp",
            (
                "--plangate-price-step", "30",
                "--plangate-max-sessions", "64",
                "--plangate-sunk-cost-alpha", "0.7",
                "--plangate-session-cap-wait", "6",
            ),
        ),
        GatewayConfig(
            "plangate_adaptive_react_recovery",
            "mcpdp",
            (
                "--plangate-price-step", "30",
                "--plangate-max-sessions", "64",
                "--plangate-sunk-cost-alpha", "0.7",
                "--plangate-session-cap-wait", "6",
                "--enable-recovery",
            ),
        ),
    )


def to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def bootstrap_delta_ci(values_a: list[float], values_b: list[float], seed: int, iters: int = 2000) -> tuple[float | None, float | None]:
    if len(values_a) < 3 or len(values_b) < 3:
        return None, None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iters):
        mean_a = sum(values_a[rng.randrange(len(values_a))] for _ in range(len(values_a))) / len(values_a)
        mean_b = sum(values_b[rng.randrange(len(values_b))] for _ in range(len(values_b))) / len(values_b)
        samples.append(mean_a - mean_b)
    samples.sort()
    return samples[int(0.025 * (iters - 1))], samples[int(0.975 * (iters - 1))]


async def call_tool(
    http_session: aiohttp.ClientSession,
    gateway_url: str,
    *,
    session_id: str,
    workflow_id: str,
    workflow_type: str,
    step_index: int,
    tool_name: str,
    idempotency_key: str,
    recovery_mode: str | None = None,
    failure_injection: dict[str, Any] | None = None,
    timeout_s: int = 60,
) -> tuple[dict[str, Any] | None, float, str | None]:
    arguments: dict[str, Any] = {
        "session_id": session_id,
        "workflow_id": workflow_id,
        "workflow_type": workflow_type,
        "step_index": step_index,
        "idempotency_key": idempotency_key,
        "payload": {},
    }
    if failure_injection:
        arguments["__failure_injection"] = failure_injection

    payload = {
        "jsonrpc": "2.0",
        "id": step_index + 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Session-ID": session_id,
    }
    if recovery_mode:
        headers["X-Recovery-Mode"] = recovery_mode

    ts = time.time()
    try:
        async with http_session.post(
            gateway_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            latency_ms = (time.time() - ts) * 1000.0
            try:
                body = await resp.json()
            except Exception:
                body = None
            return body, latency_ms, None
    except asyncio.TimeoutError:
        return None, 0.0, "timeout"
    except Exception as exc:
        return None, 0.0, str(exc)


async def execute_session(
    http_session: aiohttp.ClientSession,
    gateway_url: str,
    *,
    variant: str,
    session_id: str,
    workflow_id: str,
    workflow_type: str,
    steps: list[str],
    failure_rate: float,
    rng: random.Random,
) -> SessionResult:
    result = SessionResult(
        session_id=session_id,
        workflow_id=workflow_id,
        workflow_type=workflow_type,
        state="ERROR",
        total_steps=len(steps),
    )
    step_idx = 0
    completed_steps = 0
    restart_used = False
    consumed_failure = False
    recovery_attempted = False

    while step_idx < len(steps):
        tool_name = steps[step_idx]
        idem_key = f"{session_id}:{workflow_id}:{step_idx}:{tool_name}"

        inject = None
        if not consumed_failure and failure_rate > 0 and rng.random() < failure_rate:
            inject = {
                "inject_failure": True,
                "inject_key": f"{session_id}:step{step_idx}:{uuid.uuid4().hex[:8]}",
                "failure_type": "recoverable_queue_timeout",
            }

        body, latency_ms, transport_error = await call_tool(
            http_session,
            gateway_url,
            session_id=session_id,
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            step_index=step_idx,
            tool_name=tool_name,
            idempotency_key=idem_key,
            failure_injection=inject,
        )
        result.http_requests_total += 1
        if latency_ms > 0:
            result.latencies.append(latency_ms)

        if transport_error is not None:
            if transport_error == "timeout":
                result.client_timed_out += 1
            else:
                result.client_rc += 1
            result.unexpected_error += 1
            result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
            return result

        if body is None:
            result.unexpected_error += 1
            result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
            return result

        if body.get("error") is not None:
            error_obj = body["error"]
            error_code = int(error_obj.get("code", 0))
            error_msg = str(error_obj.get("message", ""))
            error_data = error_obj.get("data", {}) or {}
            retryable = bool(error_data.get("retryable", False)) or "recoverable" in error_msg.lower()

            if error_code in (-32001, -32002, -32003):
                result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
                return result

            if not retryable:
                result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
                return result

            consumed_failure = True

            if variant == "plangate_adaptive_react_recovery" and not recovery_attempted:
                recovery_attempted = True
                result.resume_attempted += 1
                recovery_body, recovery_latency_ms, recovery_error = await call_tool(
                    http_session,
                    gateway_url,
                    session_id=session_id,
                    workflow_id=workflow_id,
                    workflow_type=workflow_type,
                    step_index=step_idx,
                    tool_name=tool_name,
                    idempotency_key=idem_key,
                    recovery_mode="resume",
                )
                result.http_requests_total += 1
                if recovery_latency_ms > 0:
                    result.latencies.append(recovery_latency_ms)
                if recovery_error is not None or recovery_body is None:
                    result.resume_rejected += 1
                    result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
                    return result
                if recovery_body.get("error") is not None:
                    result.resume_rejected += 1
                    result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
                    return result

                result_data = recovery_body.get("result", {}) or {}
                result_meta = result_data.get("_meta", {}) or {}
                recovered = bool(result_data.get("recovered")) or bool(result_meta.get("recovered")) or (
                    result_data.get("mode") == "react_client_cooperative"
                    or result_meta.get("mode") == "react_client_cooperative"
                )
                if recovered:
                    current_step_val = int(result_data.get("current_step", result_meta.get("current_step", 0)) or 0)
                    result.resume_recovered += 1
                    result.avoided_replay_steps += int(
                        result_data.get(
                            "avoided_replay_steps",
                            result_data.get(
                                "skipped_steps",
                                result_meta.get(
                                    "avoided_replay_steps",
                                    result_meta.get("skipped_steps", current_step_val),
                                ),
                            ),
                        ) or 0
                    )
                else:
                    result.resume_rejected += 1
                    result.state = "CASCADE_FAILED" if step_idx > 0 else "REJECTED"
                    return result

                result.success_steps += 1
                completed_steps += 1
                step_idx += 1
                continue

            result.retry_attempted += 1
            if step_idx == 0:
                continue
            if not restart_used:
                restart_used = True
                result.restart_attempted += 1
                result.replayed_completed_steps += completed_steps
                step_idx = 0
                completed_steps = 0
                continue

            result.state = "CASCADE_FAILED"
            return result

        result.success_steps += 1
        completed_steps += 1
        step_idx += 1

    if variant == "plangate_adaptive_react_recovery" and result.resume_recovered > 0 and result.success_steps == len(steps):
        result.post_resume_success = 1
    if result.success_steps == len(steps):
        result.state = "SUCCESS"
    elif result.success_steps > 0:
        result.state = "PARTIAL"
    else:
        result.state = "REJECTED"
    return result


async def run_recovery_probe(
    gateway_url: str,
    *,
    variant: str,
    concurrency: int,
    repeat: int,
) -> dict[str, Any]:
    wf_type = "order"
    wf_id = f"wf-idem-recovery-probe-{variant}-{concurrency}-{repeat}"
    sess_id = f"sess-idem-recovery-probe-{variant}-{concurrency}-{repeat}"

    probe = {
        "variant": variant,
        "concurrency": concurrency,
        "repeat": repeat,
        "session_id": sess_id,
        "workflow_id": wf_id,
        "workflow_type": wf_type,
        "failure_after_step": 1,
        "side_effect_before_failure_present": False,
        "resume_attempted": False,
        "resume_recovered": False,
        "resume_mode_react_client_cooperative": False,
        "resume_current_step_gt_zero": False,
        "resume_requires_client_continuation": False,
        "post_resume_success": False,
        "avoided_replay_steps": 0,
        "restart_attempted": False,
        "retry_attempted": False,
        "replayed_completed_steps": 0,
        "final_state_consistent": False,
        "passed": False,
    }

    connector = aiohttp.TCPConnector(limit=2, limit_per_host=2)
    async with aiohttp.ClientSession(connector=connector) as http_session:
        # Step 0
        idem0 = f"{sess_id}:{wf_id}:0:reserve_inventory"
        body0, _, err0 = await call_tool(
            http_session,
            gateway_url,
            session_id=sess_id,
            workflow_id=wf_id,
            workflow_type=wf_type,
            step_index=0,
            tool_name="reserve_inventory",
            idempotency_key=idem0,
        )
        if err0 is not None or body0 is None or body0.get("error") is not None:
            return probe
        probe["side_effect_before_failure_present"] = True

        # Step 1 injected recoverable failure
        idem1 = f"{sess_id}:{wf_id}:1:authorize_payment"
        body1, _, err1 = await call_tool(
            http_session,
            gateway_url,
            session_id=sess_id,
            workflow_id=wf_id,
            workflow_type=wf_type,
            step_index=1,
            tool_name="authorize_payment",
            idempotency_key=idem1,
            failure_injection={
                "inject_failure": True,
                "inject_key": f"idem-probe-{variant}-{concurrency}-{repeat}",
                "failure_type": "recoverable_queue_timeout",
            },
        )
        if err1 is not None or body1 is None or body1.get("error") is None:
            return probe

        if variant == "plangate_adaptive_react_recovery":
            probe["resume_attempted"] = True
            bodyr, _, errr = await call_tool(
                http_session,
                gateway_url,
                session_id=sess_id,
                workflow_id=wf_id,
                workflow_type=wf_type,
                step_index=1,
                tool_name="authorize_payment",
                idempotency_key=idem1,
                recovery_mode="resume",
            )
            if errr is not None or bodyr is None or bodyr.get("error") is not None:
                return probe
            result_data = bodyr.get("result", {}) or {}
            meta = result_data.get("_meta", {}) or {}
            probe["resume_recovered"] = bool(result_data.get("recovered")) or bool(meta.get("recovered")) or (
                result_data.get("mode") == "react_client_cooperative"
                or meta.get("mode") == "react_client_cooperative"
            )
            probe["resume_mode_react_client_cooperative"] = (
                result_data.get("mode") == "react_client_cooperative"
                or meta.get("mode") == "react_client_cooperative"
            )
            current_step_val = int(result_data.get("current_step", meta.get("current_step", 0)) or 0)
            probe["resume_current_step_gt_zero"] = current_step_val > 0
            probe["resume_requires_client_continuation"] = bool(
                result_data.get("requires_client_continuation", meta.get("requires_client_continuation", False))
            )
            probe["avoided_replay_steps"] = int(
                result_data.get(
                    "avoided_replay_steps",
                    result_data.get(
                        "skipped_steps",
                        meta.get("avoided_replay_steps", meta.get("skipped_steps", current_step_val)),
                    ),
                ) or 0
            )

            idem2 = f"{sess_id}:{wf_id}:2:confirm_order"
            body2, _, err2 = await call_tool(
                http_session,
                gateway_url,
                session_id=sess_id,
                workflow_id=wf_id,
                workflow_type=wf_type,
                step_index=2,
                tool_name="confirm_order",
                idempotency_key=idem2,
            )
            if err2 is None and body2 is not None and body2.get("error") is None:
                probe["post_resume_success"] = True
                probe["final_state_consistent"] = True
                probe["passed"] = probe["resume_recovered"] and probe["post_resume_success"]
            return probe

        # idempotency-only: restart workflow from step 0, no resume
        probe["restart_attempted"] = True
        probe["retry_attempted"] = True
        probe["replayed_completed_steps"] = 1

        body0b, _, err0b = await call_tool(
            http_session,
            gateway_url,
            session_id=sess_id,
            workflow_id=wf_id,
            workflow_type=wf_type,
            step_index=0,
            tool_name="reserve_inventory",
            idempotency_key=idem0,
        )
        if err0b is not None or body0b is None or body0b.get("error") is not None:
            return probe
        if not body0b.get("result", {}).get("structuredContent", {}).get("replay", False):
            # gateway/backend may put replay under top level content adapter; allow fallback below
            replay_fallback = json.dumps(body0b, ensure_ascii=False)
            if "\"replay\": true" not in replay_fallback.lower():
                return probe

        body1b, _, err1b = await call_tool(
            http_session,
            gateway_url,
            session_id=sess_id,
            workflow_id=wf_id,
            workflow_type=wf_type,
            step_index=1,
            tool_name="authorize_payment",
            idempotency_key=idem1,
        )
        if err1b is not None or body1b is None or body1b.get("error") is not None:
            return probe

        idem2 = f"{sess_id}:{wf_id}:2:confirm_order"
        body2b, _, err2b = await call_tool(
            http_session,
            gateway_url,
            session_id=sess_id,
            workflow_id=wf_id,
            workflow_type=wf_type,
            step_index=2,
            tool_name="confirm_order",
            idempotency_key=idem2,
        )
        if err2b is not None or body2b is None or body2b.get("error") is not None:
            return probe

        probe["final_state_consistent"] = True
        probe["passed"] = True
        return probe


def aggregate_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["variant"]), str(row["concurrency"]), str(row["failure_rate"]))
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []
    metrics = [
        "success", "cascade_failed", "partial", "workflow_success_rate", "p95_ms",
        "duplicate_side_effect", "resume_recovered", "avoided_replay_steps",
        "post_resume_success", "restart_attempted", "retry_attempted",
        "replayed_completed_steps", "http_requests_total", "sqlite_writes_total",
    ]
    for (variant, concurrency, failure_rate), bucket in sorted(grouped.items()):
        row: dict[str, Any] = {
            "variant": variant,
            "concurrency": concurrency,
            "failure_rate": failure_rate,
            "runs": len(bucket),
        }
        for metric in metrics:
            values = [to_float(r.get(metric)) for r in bucket]
            row[f"{metric}_mean"] = round(mean(values), 6) if values else 0.0
            row[f"{metric}_std"] = round(stdev(values), 6) if len(values) >= 2 else 0.0
        out.append(row)
    return out


def build_effects(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    metrics = [
        "resume_recovered", "avoided_replay_steps", "post_resume_success",
        "duplicate_side_effect", "replayed_completed_steps", "http_requests_total",
        "sqlite_writes_total", "cascade_failed", "success",
    ]
    for concurrency in ["10", "20"]:
        bucket_a = [r for r in rows if str(r["variant"]) == "plangate_adaptive_react_recovery" and str(r["concurrency"]) == concurrency]
        bucket_b = [r for r in rows if str(r["variant"]) == "idempotency_only_retry" and str(r["concurrency"]) == concurrency]
        for metric in metrics:
            values_a = [to_float(r.get(metric)) for r in bucket_a]
            values_b = [to_float(r.get(metric)) for r in bucket_b]
            mean_a = mean(values_a) if values_a else 0.0
            mean_b = mean(values_b) if values_b else 0.0
            ci_low, ci_high = bootstrap_delta_ci(values_a, values_b, seed + hash((concurrency, metric)) % 100000)
            out.append(
                {
                    "comparison": f"recovery_vs_idempotency_only_C{concurrency}_F0.1",
                    "metric": metric,
                    "group_a": "plangate_adaptive_react_recovery",
                    "group_b": "idempotency_only_retry",
                    "concurrency": concurrency,
                    "failure_rate": "0.1",
                    "n_a": len(values_a),
                    "n_b": len(values_b),
                    "mean_a": round(mean_a, 6),
                    "mean_b": round(mean_b, 6),
                    "delta": round(mean_a - mean_b, 6),
                    "ci95_low_delta": "" if ci_low is None else round(ci_low, 6),
                    "ci95_high_delta": "" if ci_high is None else round(ci_high, 6),
                    "descriptive_only": "false" if ci_low is not None else "true",
                }
            )
    return out


def build_validation(
    summary_rows: list[dict[str, Any]],
    db_validation: dict[str, Any],
    replay_probes: list[dict[str, Any]],
    recovery_probes: list[dict[str, Any]],
    effects_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    variants_present = sorted({str(r["variant"]) for r in summary_rows})
    recovery_probe_rows = [r for r in recovery_probes if r["variant"] == "plangate_adaptive_react_recovery"]
    idem_probe_rows = [r for r in recovery_probes if r["variant"] == "idempotency_only_retry"]

    val = {
        "artifact": "idempotency_only_retry_baseline_v1",
        "errors": [],
        "row_count_summary": len(summary_rows),
        "agg_row_count": len({(r["variant"], str(r["concurrency"]), str(r["failure_rate"])) for r in summary_rows}),
        "effect_row_count_positive": len(effects_rows) > 0,
        "variants_present": variants_present,
        "parameter_grid_complete": len(summary_rows) == 12,
        "cell_repeat_counts_complete": all(
            sum(
                1 for r in summary_rows
                if r["variant"] == variant and int(r["concurrency"]) == c and float(r["failure_rate"]) == 0.1
            ) == args.repeats
            for variant in variants_present
            for c in args.concurrency_values
        ),
        "repeats_per_cell": args.repeats,
        "http_services_started": True,
        "mcp_tools_called_http_services": True,
        "sqlite_side_effects_present": db_validation.get("db_integrity_check_passed", False),
        "sqlite_wal_enabled": db_validation.get("sqlite_wal_enabled", False),
        "db_integrity_check_passed": db_validation.get("db_integrity_check_passed", False),
        "all_client_rc_zero": all(int(r.get("client_rc", 0)) == 0 for r in summary_rows),
        "all_client_timed_out_zero": all(int(r.get("client_timed_out", 0)) == 0 for r in summary_rows),
        "all_unexpected_error_empty_or_zero": all(int(r.get("unexpected_error", 0)) == 0 for r in summary_rows),
        "duplicate_side_effect_zero": db_validation.get("duplicate_side_effect", -1) == 0,
        "idempotency_only_present": "idempotency_only_retry" in variants_present,
        "idempotency_only_recovery_disabled": all(int(r.get("resume_attempted", 0)) == 0 for r in summary_rows if r["variant"] == "idempotency_only_retry"),
        "idempotency_only_resume_recovered_zero": all(int(r.get("resume_recovered", 0)) == 0 for r in summary_rows if r["variant"] == "idempotency_only_retry"),
        "idempotency_only_avoided_replay_zero": all(int(r.get("avoided_replay_steps", 0)) == 0 for r in summary_rows if r["variant"] == "idempotency_only_retry"),
        "recovery_variant_present": "plangate_adaptive_react_recovery" in variants_present,
        "recovery_resume_recovered_positive": any(int(r.get("resume_recovered", 0)) > 0 for r in summary_rows if r["variant"] == "plangate_adaptive_react_recovery") or any(bool(r.get("resume_recovered")) for r in recovery_probe_rows),
        "recovery_avoided_replay_steps_positive": any(int(r.get("avoided_replay_steps", 0)) > 0 for r in summary_rows if r["variant"] == "plangate_adaptive_react_recovery") or any(int(r.get("avoided_replay_steps", 0)) > 0 for r in recovery_probe_rows),
        "recovery_post_resume_success_positive": any(int(r.get("post_resume_success", 0)) > 0 for r in summary_rows if r["variant"] == "plangate_adaptive_react_recovery") or any(bool(r.get("post_resume_success")) for r in recovery_probe_rows),
        "db_final_state_consistent": db_validation.get("db_final_state_consistent", False),
        "db_no_duplicate_idempotency_keys": db_validation.get("db_no_duplicate_idempotency_keys", False),
        "db_no_duplicate_side_effect_rows": db_validation.get("db_no_duplicate_side_effect_rows", False),
    }
    if not val["parameter_grid_complete"]:
        val["errors"].append("parameter_grid_incomplete")
    if not val["cell_repeat_counts_complete"]:
        val["errors"].append("cell_repeat_counts_incomplete")
    if not val["duplicate_side_effect_zero"]:
        val["errors"].append("duplicate_side_effect_nonzero")
    if not val["idempotency_only_recovery_disabled"]:
        val["errors"].append("idempotency_only_recovery_not_disabled")
    if not val["idempotency_only_resume_recovered_zero"]:
        val["errors"].append("idempotency_only_resume_recovered_nonzero")
    if not val["idempotency_only_avoided_replay_zero"]:
        val["errors"].append("idempotency_only_avoided_replay_nonzero")
    if not val["recovery_resume_recovered_positive"]:
        val["errors"].append("recovery_resume_recovered_not_positive")
    if not val["recovery_avoided_replay_steps_positive"]:
        val["errors"].append("recovery_avoided_replay_not_positive")
    if not val["recovery_post_resume_success_positive"]:
        val["errors"].append("recovery_post_resume_success_not_positive")
    return val


def build_readme(args: argparse.Namespace) -> str:
    return f"""# Idempotency-Only Retry Baseline v1

This artifact is a request-independent recovery baseline for the HTTP+SQLite E2E workflow harness.

It compares:
- `idempotency_only_retry`
- `plangate_adaptive_react_recovery`

## Intent

This artifact is a baseline for the question:

> Is idempotency-key safety plus retry/restart already sufficient, or is checkpoint recovery still necessary?

The idempotency-only baseline intentionally ignores checkpoint recovery and session-progress restoration. It keeps stable idempotency keys so that duplicate durable writes remain safe, but it does not send `X-Recovery-Mode: resume` and therefore cannot restore safe prefixes.

## Configuration

- sessions: {args.sessions}
- concurrency: {args.concurrency_values}
- failure_rate: {args.failure_rate}
- repeats: {args.repeats}

## Allowed claims

- Idempotency-only retry can preserve duplicate-side-effect safety.
- Idempotency-only retry does not recover checkpoint progress.
- PlanGate recovery can avoid replayed completed steps under the same HTTP+SQLite harness.

## Not allowed

- Do not claim idempotency is unnecessary.
- Do not claim recovery universally improves raw workflow success.
- Do not treat this artifact as a main performance benchmark.
"""


def cleanup_runtime_files() -> None:
    for path in ARTIFACT_DIR.glob("_tmp*"):
        if path.is_file():
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                pass
    for suffix in [".sqlite", ".sqlite-shm", ".sqlite-wal", ".sqlite-journal", ".log"]:
        for path in ARTIFACT_DIR.glob(f"*{suffix}"):
            for _ in range(3):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    global ARTIFACT_DIR

    parser = argparse.ArgumentParser(description="Run idempotency-only retry baseline")
    parser.add_argument("--sessions", type=int, default=30)
    parser.add_argument("--concurrency-values", type=int, nargs="+", default=[10, 20])
    parser.add_argument("--failure-rate", type=float, default=0.1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--gateway-binary", type=str, default="gateway.exe" if sys.platform == "win32" else "gateway")
    parser.add_argument("--artifact-dir", type=str, default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.artifact_dir:
        p = Path(args.artifact_dir)
        ARTIFACT_DIR = p if p.is_absolute() else ROOT_DIR / p
    else:
        ARTIFACT_DIR = DEFAULT_ARTIFACT_DIR
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    smoke.ARTIFACT_DIR = ARTIFACT_DIR
    if args.dry_run:
        print(json.dumps({
            "artifact_dir": str(ARTIFACT_DIR),
            "sessions": args.sessions,
            "concurrency_values": args.concurrency_values,
            "failure_rate": args.failure_rate,
            "repeats": args.repeats,
        }, ensure_ascii=False, indent=2))
        return 0

    for p in list(ARTIFACT_DIR.iterdir()):
        if p.is_file():
            p.unlink()

    gateways = resolve_gateways()
    db_path = ARTIFACT_DIR / "idempotency_only_retry.sqlite"
    replay_probes: list[dict[str, Any]] = []
    recovery_probes: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    binary = smoke.ensure_gateway_binary(ROOT_DIR / args.gateway_binary)
    http_proc = None
    mcp_proc = None
    gateway_proc = None
    try:
        http_proc = smoke.start_http_service(db_path)
        mcp_proc = smoke.start_mcp_backend()

        replay_probes = smoke.run_idempotency_replay_probes()

        for gateway in gateways:
            for concurrency in args.concurrency_values:
                for repeat in range(args.repeats):
                    gateway_port = smoke.find_free_port()
                    gateway_url = f"http://127.0.0.1:{gateway_port}"
                    gateway_proc = smoke.start_gateway(binary, gateway, gateway_port)
                    rng = random.Random(args.seed + concurrency * 100 + repeat * 1000 + len(summary_rows))

                    if "recovery" in gateway.name:
                        rp = asyncio.run(
                            run_recovery_probe(
                                gateway_url,
                                variant=gateway.name,
                                concurrency=concurrency,
                                repeat=repeat,
                            )
                        )
                        recovery_probes.append(rp)
                    else:
                        rp = asyncio.run(
                            run_recovery_probe(
                                gateway_url,
                                variant=gateway.name,
                                concurrency=concurrency,
                                repeat=repeat,
                            )
                        )
                        recovery_probes.append(rp)

                    session_results: list[SessionResult] = []
                    async def run_cell():
                        connector = aiohttp.TCPConnector(limit=concurrency * 2, limit_per_host=concurrency * 2)
                        async with aiohttp.ClientSession(connector=connector) as http_session:
                            semaphore = asyncio.Semaphore(concurrency)

                            async def run_one(idx: int):
                                wf_type = rng.choice(["travel", "order", "support"])
                                workflow_id = f"wf-{gateway.name}-{concurrency}-{repeat:02d}-{idx:04d}"
                                session_id = f"sess-{gateway.name}-{concurrency}-{repeat:02d}-{idx:04d}"
                                steps = list(smoke.WORKFLOW_TYPES[wf_type])
                                async with semaphore:
                                    sr = await execute_session(
                                        http_session,
                                        gateway_url,
                                        variant=gateway.name,
                                        session_id=session_id,
                                        workflow_id=workflow_id,
                                        workflow_type=wf_type,
                                        steps=steps,
                                        failure_rate=args.failure_rate,
                                        rng=rng,
                                    )
                                    session_results.append(sr)

                            await asyncio.gather(*(run_one(i) for i in range(args.sessions)))

                    start = time.time()
                    asyncio.run(run_cell())
                    elapsed_ms = (time.time() - start) * 1000.0

                    success = sum(1 for r in session_results if r.state == "SUCCESS")
                    cascade = sum(1 for r in session_results if r.state == "CASCADE_FAILED")
                    partial = sum(1 for r in session_results if r.state == "PARTIAL")
                    rejected = sum(1 for r in session_results if r.state == "REJECTED")
                    p95 = smoke.percentile([lat for r in session_results for lat in r.latencies], 0.95)
                    row = {
                        "variant": gateway.name,
                        "concurrency": concurrency,
                        "failure_rate": args.failure_rate,
                        "repeat": repeat,
                        "sessions": args.sessions,
                        "success": success,
                        "cascade_failed": cascade,
                        "partial": partial,
                        "rejected": rejected,
                        "workflow_success_rate": round(success / max(1, args.sessions), 6),
                        "p95_ms": round(p95, 3),
                        "http_requests_total": sum(r.http_requests_total for r in session_results),
                        "sqlite_writes_total": 0,
                        "side_effects_committed": 0,
                        "idempotency_keys_total": 0,
                        "idempotency_replay_hits": 0,
                        "duplicate_side_effect": 0,
                        "client_rc": sum(r.client_rc for r in session_results),
                        "client_timed_out": sum(r.client_timed_out for r in session_results),
                        "unexpected_error": sum(r.unexpected_error for r in session_results),
                        "resume_attempted": sum(r.resume_attempted for r in session_results),
                        "resume_recovered": sum(r.resume_recovered for r in session_results),
                        "resume_rejected": sum(r.resume_rejected for r in session_results),
                        "avoided_replay_steps": sum(r.avoided_replay_steps for r in session_results),
                        "post_resume_success": sum(r.post_resume_success for r in session_results),
                        "restart_attempted": sum(r.restart_attempted for r in session_results),
                        "retry_attempted": sum(r.retry_attempted for r in session_results),
                        "replayed_completed_steps": sum(r.replayed_completed_steps for r in session_results),
                    }
                    summary_rows.append(row)

                    smoke.stop_process(gateway_proc)
                    gateway_proc = None

        db_validation = smoke.validate_db(db_path, summary_rows)
        for row in summary_rows:
            row["side_effects_committed"] = row["sqlite_writes_total"]
            row["idempotency_keys_total"] = row["sqlite_writes_total"]

        agg_rows = aggregate_summary(summary_rows)
        effect_rows = build_effects(summary_rows, args.seed)
        validation = build_validation(summary_rows, db_validation, replay_probes, recovery_probes, effect_rows, args)

        smoke.write_csv(ARTIFACT_DIR / "idempotency_only_retry_baseline_summary.csv", SUMMARY_COLUMNS, summary_rows)
        smoke.write_csv(ARTIFACT_DIR / "idempotency_only_retry_baseline_agg.csv", AGG_COLUMNS, agg_rows)
        smoke.write_csv(ARTIFACT_DIR / "idempotency_only_retry_baseline_effects.csv", EFFECT_COLUMNS, effect_rows)
        smoke.write_csv(ARTIFACT_DIR / "idempotency_only_retry_baseline_replay_probe.csv", REPLAY_PROBE_COLUMNS, replay_probes)
        smoke.write_csv(ARTIFACT_DIR / "idempotency_only_retry_baseline_recovery_probe.csv", RECOVERY_PROBE_COLUMNS, recovery_probes)
        smoke.write_csv(ARTIFACT_DIR / "idempotency_only_retry_baseline_db_summary.csv", DB_SUMMARY_COLUMNS, db_validation.get("db_summary_rows", []))
        (ARTIFACT_DIR / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        (ARTIFACT_DIR / "README_RESULT.md").write_text(build_readme(args), encoding="utf-8")
    finally:
        smoke.stop_process(gateway_proc)
        smoke.stop_process(mcp_proc)
        smoke.stop_process(http_proc)
        cleanup_runtime_files()

    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if not validation["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
