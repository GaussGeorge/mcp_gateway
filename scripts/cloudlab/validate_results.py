#!/usr/bin/env python3
"""Validate CloudLab distributed experiment results."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional


COMMITMENT_MODE_RE = re.compile(r"commitment-token-mode=(\S+)")
REDIS_ADDR_RE = re.compile(r"redis-addr=(\S+)")


@dataclass
class ValidationSummary:
    total_sessions: int
    success_sessions: int
    success_rate: float
    rejected_at_step0: int
    cascade_failed: int
    state_miss: int
    duplicate_admission: int
    cross_node_sessions: int
    commitment_invalid: int
    commitment_mismatch: int
    commitment_expired: int
    gateway_p95_latency_us: float
    effective_goodput: float
    elapsed_seconds: float


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct / 100.0))
    idx = max(0, min(len(ordered) - 1, idx))
    return ordered[idx]


def summarize_steps_csv(steps_path: str) -> ValidationSummary:
    sessions: Dict[str, Dict[str, object]] = {}
    gateway_latencies: List[float] = []
    min_ts: Optional[float] = None
    max_ts: Optional[float] = None
    state_miss = 0
    duplicate_admission = 0
    commitment_invalid = 0
    commitment_mismatch = 0
    commitment_expired = 0

    with open(steps_path, "r", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            session_id = row.get("session_id", "").strip()
            if not session_id:
                continue

            ts = float(row.get("timestamp", 0) or 0)
            latency_ms = float(row.get("latency_ms", 0) or 0)
            session_state = row.get("session_state", "").strip()
            gateway_url = row.get("gateway_url", "").strip()
            commitment_status = row.get("commitment_status", "").strip().lower()
            effective_goodput = float(row.get("effective_goodput", 0) or 0)

            if row.get("gateway_latency_us"):
                try:
                    gateway_latencies.append(float(row["gateway_latency_us"]))
                except ValueError:
                    pass

            if _parse_bool(row.get("state_miss", "")):
                state_miss += 1
            if _parse_bool(row.get("duplicate_admission", "")):
                duplicate_admission += 1
            if commitment_status == "invalid":
                commitment_invalid += 1
            elif commitment_status == "mismatch":
                commitment_mismatch += 1
            elif commitment_status == "expired":
                commitment_expired += 1

            step_end = ts + latency_ms / 1000.0
            min_ts = ts if min_ts is None else min(min_ts, ts)
            max_ts = step_end if max_ts is None else max(max_ts, step_end)

            session = sessions.setdefault(
                session_id,
                {
                    "final_state": session_state,
                    "gateway_urls": set(),
                    "effective_goodput": 0.0,
                },
            )
            session["final_state"] = session_state
            if gateway_url:
                session["gateway_urls"].add(gateway_url)
            session["effective_goodput"] = max(float(session["effective_goodput"]), effective_goodput)

    total_sessions = len(sessions)
    success_sessions = sum(1 for session in sessions.values() if session["final_state"] == "SUCCESS")
    rejected_at_step0 = sum(1 for session in sessions.values() if session["final_state"] == "REJECTED_AT_STEP_0")
    cascade_failed = sum(1 for session in sessions.values() if session["final_state"] == "CASCADE_FAILED")
    cross_node_sessions = sum(1 for session in sessions.values() if len(session["gateway_urls"]) >= 2)
    elapsed = max((max_ts or 0) - (min_ts or 0), 0.001)
    effective_goodput_total = sum(float(session["effective_goodput"]) for session in sessions.values())

    return ValidationSummary(
        total_sessions=total_sessions,
        success_sessions=success_sessions,
        success_rate=(success_sessions / total_sessions) if total_sessions else 0.0,
        rejected_at_step0=rejected_at_step0,
        cascade_failed=cascade_failed,
        state_miss=state_miss,
        duplicate_admission=duplicate_admission,
        cross_node_sessions=cross_node_sessions,
        commitment_invalid=commitment_invalid,
        commitment_mismatch=commitment_mismatch,
        commitment_expired=commitment_expired,
        gateway_p95_latency_us=_percentile(gateway_latencies, 95),
        effective_goodput=effective_goodput_total / elapsed,
        elapsed_seconds=elapsed,
    )


def inspect_gateway_logs(log_dir: str) -> Dict[str, object]:
    modes: List[str] = []
    redis_addrs: List[str] = []
    files_seen = 0
    for root, _dirs, files in os.walk(log_dir):
        for name in files:
            if not name.endswith(".log"):
                continue
            files_seen += 1
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            modes.extend(COMMITMENT_MODE_RE.findall(text))
            redis_addrs.extend(REDIS_ADDR_RE.findall(text))
    return {
        "modes": modes,
        "redis_addrs": redis_addrs,
        "files_seen": files_seen,
    }


def validate_summary(
    summary: ValidationSummary,
    *,
    expected_sessions: int,
    gateway_count: int,
    routing: str,
    failure_rate: float,
    min_success_rate: float,
    gateway_log_dir: Optional[str],
    expected_commitment_mode: Optional[str],
    expected_redis_addr: Optional[str],
) -> List[str]:
    errors: List[str] = []
    if summary.total_sessions != expected_sessions:
        errors.append(f"total_sessions={summary.total_sessions} expected={expected_sessions}")
    if summary.state_miss != 0:
        errors.append(f"state_miss={summary.state_miss} expected=0")
    if summary.duplicate_admission != 0:
        errors.append(f"duplicate_admission={summary.duplicate_admission} expected=0")
    if gateway_count >= 2 and routing == "random" and summary.cross_node_sessions <= 0:
        errors.append("cross_node_sessions must be > 0 for multi-gateway random routing")
    if failure_rate == 0 and summary.cascade_failed != 0:
        errors.append(f"cascade_failed={summary.cascade_failed} expected=0 for no-failure smoke")
    if summary.commitment_invalid != 0:
        errors.append(f"commitment_invalid={summary.commitment_invalid} expected=0")
    if summary.commitment_mismatch != 0:
        errors.append(f"commitment_mismatch={summary.commitment_mismatch} expected=0")
    if summary.commitment_expired != 0:
        errors.append(f"commitment_expired={summary.commitment_expired} expected=0")
    if summary.success_rate < min_success_rate:
        errors.append(f"success_rate={summary.success_rate:.4f} below threshold={min_success_rate:.4f}")

    if gateway_log_dir:
        inspected = inspect_gateway_logs(gateway_log_dir)
        if int(inspected["files_seen"]) == 0:
            errors.append(f"no gateway logs found under {gateway_log_dir}")
        if expected_commitment_mode:
            modes = set(inspected["modes"])
            if modes != {expected_commitment_mode}:
                errors.append(f"gateway commitment modes={sorted(modes)} expected={[expected_commitment_mode]}")
        if expected_redis_addr:
            redis_addrs = set(inspected["redis_addrs"])
            if redis_addrs != {expected_redis_addr}:
                errors.append(f"gateway redis_addrs={sorted(redis_addrs)} expected={[expected_redis_addr]}")
    return errors


def write_summary(path: str, summary: ValidationSummary, errors: Iterable[str]) -> None:
    payload = {"summary": asdict(summary), "errors": list(errors)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate CloudLab PlanGate results")
    parser.add_argument("--steps", required=True, help="Merged steps.csv path")
    parser.add_argument("--expected-sessions", required=True, type=int)
    parser.add_argument("--gateway-count", required=True, type=int)
    parser.add_argument("--routing", default="random", choices=["single", "random", "sticky", "round_robin"])
    parser.add_argument("--failure-rate", type=float, default=0.0)
    parser.add_argument("--gateway-log-dir", default="")
    parser.add_argument("--expected-commitment-mode", default="")
    parser.add_argument("--expected-redis-addr", default="")
    parser.add_argument("--min-success-rate", type=float, default=0.95)
    parser.add_argument("--summary-out", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = summarize_steps_csv(args.steps)
    errors = validate_summary(
        summary,
        expected_sessions=args.expected_sessions,
        gateway_count=args.gateway_count,
        routing=args.routing,
        failure_rate=args.failure_rate,
        min_success_rate=args.min_success_rate,
        gateway_log_dir=args.gateway_log_dir or None,
        expected_commitment_mode=args.expected_commitment_mode or None,
        expected_redis_addr=args.expected_redis_addr or None,
    )

    print(json.dumps(asdict(summary), indent=2, sort_keys=True))
    if args.summary_out:
        write_summary(args.summary_out, summary, errors)
    if errors:
        for error in errors:
            print(f"[validate_results] ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
