#!/usr/bin/env python3
"""Failure-specific mechanism ablation wrapper for P3 recovery/amendment workloads.

This runner intentionally reuses the existing controlled P3 workload generator
and only toggles one gateway-side mechanism at a time:

- commitment
- amendment
- recovery

The default use for this script is a lightweight dry-run/config validation.
Actual long runs are supported, but are not required for this task.
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import p3_recovery_amendment_runner as p3_runner


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
SERVER_PY = ROOT_DIR / "mcp_server" / "server.py"

DEFAULT_RESULTS_DIR = ROOT_DIR / "results" / "p3_failure_mechanism_ablation"
DEFAULT_SUMMARY_NAME = "p3_failure_mechanism_ablation_summary.csv"
DEFAULT_COMMITMENT_SECRET = "local-p3-ablation-secret"


@dataclass(frozen=True)
class VariantConfig:
    name: str
    commitment_token_mode: str
    plan_amendment_mode: str
    enable_recovery: bool
    recovery_store: str = "inmemory"

    def gateway_extra_args(self) -> List[str]:
        return [
            "--commitment-token-mode",
            self.commitment_token_mode,
            "--plan-amendment-mode",
            self.plan_amendment_mode,
            f"--enable-recovery={'true' if self.enable_recovery else 'false'}",
            "--recovery-store",
            self.recovery_store,
        ]


def normalize_recovery_store(store: str) -> str:
    normalized = (store or "inmemory").strip().lower()
    if normalized == "memory":
        return "inmemory"
    if normalized not in {"inmemory", "redis"}:
        raise ValueError(f"unsupported recovery store: {store}")
    return normalized


def mechanism_variants(recovery_store: str = "inmemory") -> List[VariantConfig]:
    store = normalize_recovery_store(recovery_store)
    return [
        VariantConfig(
            name="plangate_full",
            commitment_token_mode="optional",
            plan_amendment_mode="recovery-only",
            enable_recovery=True,
            recovery_store=store,
        ),
        VariantConfig(
            name="wo_commitment",
            commitment_token_mode="off",
            plan_amendment_mode="recovery-only",
            enable_recovery=True,
            recovery_store=store,
        ),
        VariantConfig(
            name="wo_amendment",
            commitment_token_mode="optional",
            plan_amendment_mode="off",
            enable_recovery=True,
            recovery_store=store,
        ),
        VariantConfig(
            name="wo_recovery",
            commitment_token_mode="optional",
            plan_amendment_mode="recovery-only",
            enable_recovery=False,
            recovery_store=store,
        ),
    ]


def summary_path(results_dir: Path) -> Path:
    return results_dir / DEFAULT_SUMMARY_NAME


def run_dir_for_variant(results_dir: Path, variant_name: str, run_idx: int) -> Path:
    return results_dir / variant_name / f"run{run_idx}"


def gateway_binary_path(arg_value: str) -> Path:
    if arg_value:
        return Path(arg_value).resolve()
    return p3_runner.gateway_binary_path()


def gateway_command_for_variant(
    binary: Path,
    args: argparse.Namespace,
    variant: VariantConfig,
) -> List[str]:
    return [
        str(binary),
        "--mode",
        "mcpdp",
        "--host",
        args.host,
        "--port",
        str(args.gateway_port),
        "--backend",
        p3_runner.gateway_url(args.host, args.backend_port),
        "--node-id",
        f"p3-failure:{variant.name}:{args.gateway_port}",
        "--commitment-token-secret",
        args.commitment_secret,
        "--plan-amendment-require-commitment=true",
        "--plan-amendment-max-count",
        "3",
        "--plan-amendment-max-budget-delta",
        "0",
        "--plangate-max-sessions",
        str(args.max_sessions),
        "--plangate-price-step",
        str(args.price_step),
        *variant.gateway_extra_args(),
    ]


def p3_runner_command(
    args: argparse.Namespace,
    run_results_dir: Path,
    gateway_url: str,
) -> List[str]:
    return [
        sys.executable,
        str(SCRIPT_DIR / "p3_recovery_amendment_runner.py"),
        "--policies",
        p3_runner.POLICY_AR,
        "--sessions",
        str(args.sessions),
        "--concurrency",
        str(args.concurrency),
        "--failure-rate",
        str(args.failure_rate),
        "--failure-type",
        args.failure_type,
        "--amendment-rate",
        str(args.amendment_rate),
        "--adversarial-amendment-rate",
        "0",
        "--results-dir",
        str(run_results_dir),
        "--commitment-secret",
        args.commitment_secret,
        "--budget",
        str(args.budget),
        "--fail-step-index",
        str(args.fail_step_index),
        "--gateway-url",
        gateway_url,
        "--no-start-services",
    ]


def start_local_services(
    args: argparse.Namespace,
    variant: VariantConfig,
    binary: Path,
    run_results_dir: Path,
) -> p3_runner.ServiceHandles:
    log_dir = run_results_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    backend_log = open(log_dir / "backend.log", "w", encoding="utf-8")
    gateway_log = open(log_dir / f"gateway_{variant.name}.log", "w", encoding="utf-8")

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
    p3_runner.wait_for_jsonrpc_ready(p3_runner.gateway_url(args.host, args.backend_port))

    gateway_cmd = gateway_command_for_variant(binary, args, variant)
    gateway_proc = subprocess.Popen(
        gateway_cmd,
        cwd=ROOT_DIR,
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    gw_url = p3_runner.gateway_url(args.host, args.gateway_port)
    p3_runner.wait_for_jsonrpc_ready(gw_url)
    return p3_runner.ServiceHandles(
        backend_proc=backend_proc,
        gateway_proc=gateway_proc,
        gateway_url=gw_url,
        backend_log=backend_log,
        gateway_log=gateway_log,
    )


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_variant_run(
    variant: VariantConfig,
    run_idx: int,
    run_results_dir: Path,
    elapsed_sec: float,
    client_rc: int,
    client_timed_out: bool,
    error_message: str = "",
) -> Dict[str, Any]:
    policy_dir = run_results_dir / p3_runner.POLICY_AR
    sessions_csv = policy_dir / "sessions.csv"
    steps_csv = policy_dir / "steps.csv"
    sessions = read_csv_rows(sessions_csv)
    steps = read_csv_rows(steps_csv)

    main_rows = [row for row in sessions if row.get("scenario", p3_runner.SCENARIO_MAIN) == p3_runner.SCENARIO_MAIN]
    success = sum(1 for row in main_rows if row.get("status") == "success")
    partial = sum(1 for row in main_rows if row.get("status") == "recovery_failed")
    all_rejected = max(0, len(main_rows) - success - partial)
    cascade_failed = max(0, len(main_rows) - success)
    effective_goodput = success
    effective_goodput_s = round(success / elapsed_sec, 4) if elapsed_sec > 0 else 0.0
    recovery_attempts = sum(int(row.get("recovery_attempted", 0) or 0) for row in main_rows)
    recovery_success = sum(int(row.get("recovery_success", 0) or 0) for row in main_rows)
    amendment_attempts = sum(int(row.get("amendment_submitted", 0) or 0) for row in main_rows)
    amendment_success = sum(int(row.get("amendment_accepted", 0) or 0) for row in main_rows)
    v2_commitment_issued = sum(int(row.get("v2_commitment_issued", 0) or 0) for row in main_rows)
    commitment_issued = len(
        {
            row.get("session_id", "")
            for row in steps
            if row.get("phase") == "initial" and row.get("commitment_status", "").lower() == "issued"
        }
    )

    return {
        "gateway": variant.name,
        "run_idx": run_idx,
        "success": success,
        "partial": partial,
        "all_rejected": all_rejected,
        "error": error_message,
        "cascade_failed": cascade_failed,
        "effective_goodput": effective_goodput,
        "effective_goodput_s": effective_goodput_s,
        "recovery_attempts": recovery_attempts,
        "recovery_success": recovery_success,
        "amendment_attempts": amendment_attempts,
        "amendment_success": amendment_success,
        "commitment_issued": commitment_issued,
        "v2_commitment_issued": v2_commitment_issued,
        "commitment_required": 0 if variant.commitment_token_mode == "off" else 1,
        "client_rc": client_rc,
        "client_timed_out": 1 if client_timed_out else 0,
        "csv": str(sessions_csv),
    }


def write_summary(rows: Sequence[Dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_dry_run(args: argparse.Namespace, variants: Sequence[VariantConfig], binary: Path) -> None:
    print("P3FailureMechanismAblation dry-run")
    print(f"sessions = {args.sessions}")
    print(f"concurrency = {args.concurrency}")
    print(f"failure-rate = {args.failure_rate}")
    print(f"amendment-rate = {args.amendment_rate}")
    print(f"results-dir = {args.results_dir}")
    print(f"gateway-binary = {binary}")
    print(f"summary-path = {summary_path(Path(args.results_dir))}")
    print(f"recovery-store = {normalize_recovery_store(args.recovery_store)}")
    for variant in variants:
        print()
        print(f"variant = {variant.name}")
        print(f"commitment-token-mode = {variant.commitment_token_mode}")
        print(f"plan-amendment-mode = {variant.plan_amendment_mode}")
        print(f"enable-recovery = {'true' if variant.enable_recovery else 'false'}")
        print(f"recovery-store = {variant.recovery_store}")
        print(f"gateway-extra-args = {' '.join(variant.gateway_extra_args())}")


def run_variant_once(
    args: argparse.Namespace,
    variant: VariantConfig,
    run_idx: int,
    binary: Path,
) -> Dict[str, Any]:
    run_results_dir = run_dir_for_variant(Path(args.results_dir), variant.name, run_idx)
    run_results_dir.mkdir(parents=True, exist_ok=True)
    client_log_path = run_results_dir / "p3_runner.log"
    services: Optional[p3_runner.ServiceHandles] = None
    client_rc = -1
    client_timed_out = False
    error_message = ""
    started = time.time()
    try:
        services = start_local_services(args, variant, binary, run_results_dir)
        runner_cmd = p3_runner_command(args, run_results_dir, services.gateway_url)
        with client_log_path.open("w", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                runner_cmd,
                cwd=ROOT_DIR,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            try:
                client_rc = proc.wait(timeout=args.client_timeout)
            except subprocess.TimeoutExpired:
                client_timed_out = True
                error_message = "p3 runner timeout"
                p3_runner.stop_process(proc)
                client_rc = -1
    except Exception as exc:
        error_message = str(exc)
    finally:
        if services is not None:
            services.close()

    elapsed_sec = max(time.time() - started, 0.0)
    if client_rc != 0 and not error_message:
        error_message = f"p3 runner exited with rc={client_rc}"
    return summarize_variant_run(
        variant=variant,
        run_idx=run_idx,
        run_results_dir=run_results_dir,
        elapsed_sec=elapsed_sec,
        client_rc=client_rc,
        client_timed_out=client_timed_out,
        error_message=error_message,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P3 failure-specific mechanism ablation runner")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--sessions", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--failure-rate", type=float, default=0.2)
    parser.add_argument("--failure-type", choices=["backend_timeout", "tool_unavailable", "backend_overloaded"], default=p3_runner.DEFAULT_FAILURE_TYPE)
    parser.add_argument("--amendment-rate", type=float, default=0.2)
    parser.add_argument("--results-dir", type=str, default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--gateway-binary", type=str, default="")
    parser.add_argument("--commitment-secret", type=str, default=DEFAULT_COMMITMENT_SECRET)
    parser.add_argument("--host", type=str, default=p3_runner.DEFAULT_GATEWAY_HOST)
    parser.add_argument("--backend-port", type=int, default=p3_runner.DEFAULT_BACKEND_PORT)
    parser.add_argument("--gateway-port", type=int, default=9701)
    parser.add_argument("--backend-max-workers", type=int, default=16)
    parser.add_argument("--budget", type=int, default=p3_runner.DEFAULT_BUDGET)
    parser.add_argument("--fail-step-index", type=int, default=p3_runner.DEFAULT_FAIL_STEP_INDEX)
    parser.add_argument("--price-step", type=int, default=p3_runner.DEFAULT_PRICE_STEP)
    parser.add_argument("--max-sessions", type=int, default=p3_runner.DEFAULT_MAX_SESSIONS)
    parser.add_argument("--recovery-store", choices=["inmemory", "memory", "redis"], default="inmemory")
    parser.add_argument("--client-timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.repeats <= 0:
        parser.error("--repeats must be > 0")
    if args.sessions <= 0:
        parser.error("--sessions must be > 0")
    if args.concurrency <= 0:
        parser.error("--concurrency must be > 0")
    if not 0 <= args.failure_rate <= 1:
        parser.error("--failure-rate must be in [0, 1]")
    if not 0 <= args.amendment_rate <= 1:
        parser.error("--amendment-rate must be in [0, 1]")
    if args.fail_step_index < 0:
        parser.error("--fail-step-index must be >= 0")
    args.recovery_store = normalize_recovery_store(args.recovery_store)
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    variants = mechanism_variants(args.recovery_store)
    binary = gateway_binary_path(args.gateway_binary)

    if args.dry_run:
        print_dry_run(args, variants, binary)
        return 0

    if not binary.exists():
        binary = p3_runner.build_gateway()

    summary_rows: List[Dict[str, Any]] = []
    for variant in variants:
        for run_idx in range(1, args.repeats + 1):
            summary_rows.append(run_variant_once(args, variant, run_idx, binary))
    out_path = summary_path(Path(args.results_dir))
    write_summary(summary_rows, out_path)
    print(f"summary_path = {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
