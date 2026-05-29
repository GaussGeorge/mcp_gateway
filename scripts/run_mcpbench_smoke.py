#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import os
import random
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DAG_RUNNER_PATH = SCRIPT_DIR / "dag_load_generator.py"
MCPBENCH_DIR = ROOT_DIR / "external" / "mcp-bench"
ARTIFACT_DIR = ROOT_DIR / "artifact_results" / "mcpbench_smoke_v1"
BACKEND_PORT = 8080
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"

TASK_FILES = (
    MCPBENCH_DIR / "tasks" / "mcpbench_tasks_single_runner_format.json",
    MCPBENCH_DIR / "tasks" / "mcpbench_tasks_multi_2server_runner_format.json",
    MCPBENCH_DIR / "tasks" / "mcpbench_tasks_multi_3server_runner_format.json",
)

SUMMARY_COLUMNS = [
    "gateway",
    "repeat",
    "selected_tasks",
    "duration_s",
    "success",
    "success_rate",
    "abd",
    "abd_total",
    "cascade_failed",
    "cascade_rate",
    "all_rejected",
    "step0_rejected",
    "effective_goodput_s",
    "p50_ms",
    "p95_ms",
    "client_rc",
    "client_timed_out",
    "error",
    "backend_mode",
    "task_set_sha256",
]

AGG_COLUMNS = [
    "gateway",
    "runs",
    "selected_tasks_mean",
    "duration_s_mean",
    "success_mean",
    "success_rate_mean",
    "abd_mean",
    "abd_total_mean",
    "cascade_failed_mean",
    "cascade_rate_mean",
    "all_rejected_mean",
    "step0_rejected_mean",
    "effective_goodput_s_mean",
    "p50_ms_mean",
    "p95_ms_mean",
    "client_rc_mean",
    "client_timed_out_mean",
]

TASK_COLUMNS = [
    "task_id",
    "server_name",
    "combination_name",
    "combination_type",
    "source_file",
    "estimated_steps",
    "estimated_heavy_ratio",
    "mode_hint",
]

NUMERIC_FOR_AGG = [
    "selected_tasks",
    "duration_s",
    "success",
    "success_rate",
    "abd",
    "abd_total",
    "cascade_failed",
    "cascade_rate",
    "all_rejected",
    "step0_rejected",
    "effective_goodput_s",
    "p50_ms",
    "p95_ms",
    "client_rc",
    "client_timed_out",
]


@dataclass(frozen=True)
class GatewayConfig:
    name: str
    mode: str
    extra_args: tuple[str, ...]


GATEWAYS: tuple[GatewayConfig, ...] = (
    GatewayConfig("ng", "ng", ()),
    GatewayConfig("static", "srl", ("--srl-qps", "65", "--srl-burst", "400", "--srl-max-conc", "55")),
    GatewayConfig("pp", "pp", ("--pp-max-sessions", "150")),
    GatewayConfig("rajomon", "rajomon", ("--rajomon-price-step", "5")),
    GatewayConfig(
        "plangate_relaxed",
        "mcpdp-real",
        (
            "--plangate-price-step",
            "30",
            "--plangate-max-sessions",
            "24",
            "--plangate-sunk-cost-alpha",
            "0.7",
            "--plangate-session-cap-wait",
            "6",
            "--real-ratelimit-max",
            "9999",
            "--real-latency-threshold",
            "10000",
        ),
    ),
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dag = load_module(DAG_RUNNER_PATH, "mcpbench_smoke_dag_runner")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="MCP-Bench small workflow-shape smoke for PlanGate.")
    parser.add_argument("--selected-tasks", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--step-timeout", type=float, default=30.0)
    parser.add_argument("--arrival-rate", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--gateway-binary",
        type=str,
        default="gateway.exe" if sys.platform == "win32" else "gateway",
    )
    parser.add_argument("--backend-mode", type=str, default="mock_sterile_single_machine")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--queue-timeout", type=float, default=2.0)
    parser.add_argument("--congestion-factor", type=float, default=0.5)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_gateway_binary(path: Path) -> Path:
    if path.exists():
        return path
    subprocess.run(
        ["go", "build", "-o", str(path), "./cmd/gateway"],
        cwd=str(ROOT_DIR),
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )
    return path


def start_backend(args) -> subprocess.Popen:
    log_path = ARTIFACT_DIR / "_tmp_backend.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        str(ROOT_DIR / "mcp_server" / "server.py"),
        "--port",
        str(BACKEND_PORT),
        "--mode",
        "sterile",
        "--max-workers",
        str(args.max_workers),
        "--queue-timeout",
        str(args.queue_timeout),
        "--congestion-factor",
        str(args.congestion_factor),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR / "mcp_server"),
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"backend failed to start; see {log_path}")
    return proc


def start_gateway(binary: Path, gateway: GatewayConfig, port: int) -> subprocess.Popen:
    log_path = ARTIFACT_DIR / f"_tmp_gateway_{gateway.name}.log"
    cmd = [
        str(binary),
        "--mode",
        gateway.mode,
        "--port",
        str(port),
        "--backend",
        BACKEND_URL,
        "--host",
        "127.0.0.1",
        *gateway.extra_args,
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"gateway {gateway.name} failed to start; see {log_path}")
    return proc


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=12)
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(s.getsockname()[1])


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = min(len(sorted_vals) - 1, max(0, int(round((len(sorted_vals) - 1) * p))))
    return float(sorted_vals[idx])


def estimate_steps(task: dict[str, Any]) -> int:
    dep = str(task.get("dependency_analysis", ""))
    count = dep.lower().count("step ")
    if count >= 2:
        return max(3, min(14, count))
    ctype = str(task.get("combination_type", ""))
    if "three_server" in ctype:
        return 11
    if "two_server" in ctype:
        return 9
    return 7


def estimate_heavy_ratio(task: dict[str, Any]) -> float:
    text = (str(task.get("task_description", "")) + " " + str(task.get("dependency_analysis", ""))).lower()
    hints = ["download", "full text", "cross-validate", "for each", "parallel", "branch"]
    score = sum(1 for h in hints if h in text)
    ratio = 0.18 + 0.04 * score
    return max(0.15, min(0.48, ratio))


def normalize_tasks() -> list[dict[str, Any]]:
    all_tasks: list[dict[str, Any]] = []
    for src in TASK_FILES:
        if not src.exists():
            raise SystemExit(f"missing task file: {src}")
        data = json.loads(src.read_text(encoding="utf-8"))
        groups = data.get("server_tasks", [])
        for group in groups:
            server_name = str(group.get("server_name", ""))
            combo_name = str(group.get("combination_name", ""))
            combo_type = str(group.get("combination_type", ""))
            servers = group.get("servers", [])
            for t in group.get("tasks", []):
                item = {
                    "task_id": str(t.get("task_id", "")),
                    "server_name": server_name,
                    "servers": servers,
                    "combination_name": combo_name,
                    "combination_type": combo_type,
                    "source_file": src.name,
                    "task_description": t.get("task_description", ""),
                    "dependency_analysis": t.get("dependency_analysis", ""),
                }
                item["estimated_steps"] = estimate_steps(item)
                item["estimated_heavy_ratio"] = estimate_heavy_ratio(item)
                # Slight bias toward P&S for explicit multi-step dependencies.
                item["mode_hint"] = "ps" if item["estimated_steps"] >= 9 else "mix"
                if item["task_id"]:
                    all_tasks.append(item)
    if len(all_tasks) < 20:
        raise SystemExit("available mcp-bench tasks too few for smoke")
    return all_tasks


def stratified_select(tasks: list[dict[str, Any]], selected_tasks: int, seed: int) -> list[dict[str, Any]]:
    if not (20 <= selected_tasks <= 50):
        raise SystemExit("selected-tasks must be in [20, 50]")

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        by_type[t["combination_type"]].append(t)

    rng = random.Random(seed)
    for lst in by_type.values():
        rng.shuffle(lst)

    types = sorted(by_type.keys())
    if not types:
        raise SystemExit("no task groups found")

    # Evenly allocate quota to avoid one-type-only selection.
    base = selected_tasks // len(types)
    rem = selected_tasks % len(types)
    quotas = {tp: base for tp in types}
    for tp in types[:rem]:
        quotas[tp] += 1

    selected: list[dict[str, Any]] = []
    for tp in types:
        take = min(quotas[tp], len(by_type[tp]))
        selected.extend(by_type[tp][:take])

    if len(selected) < selected_tasks:
        leftovers: list[dict[str, Any]] = []
        selected_ids = {x["task_id"] for x in selected}
        for t in tasks:
            if t["task_id"] not in selected_ids:
                leftovers.append(t)
        rng.shuffle(leftovers)
        selected.extend(leftovers[: selected_tasks - len(selected)])

    selected = selected[:selected_tasks]
    selected.sort(key=lambda x: x["task_id"])
    return selected


def task_set_sha(task_files_sha: dict[str, str], selected: list[dict[str, Any]]) -> str:
    payload = {
        "files": task_files_sha,
        "task_ids": [x["task_id"] for x in selected],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_plan_for_task(task: dict[str, Any], budget: int, seed: int, repeat_idx: int):
    task_id = str(task["task_id"])
    rng = random.Random(seed + repeat_idx * 1000 + sum(ord(c) for c in task_id))
    est_steps = int(task["estimated_steps"])
    heavy_ratio = float(task["estimated_heavy_ratio"])
    mode_hint = str(task.get("mode_hint", "mix"))

    if mode_hint == "ps":
        mode = dag.AgentMode.PLAN_AND_SOLVE
    else:
        mode = dag.AgentMode.PLAN_AND_SOLVE if rng.random() < 0.55 else dag.AgentMode.REACT

    steps = []
    for i in range(est_steps):
        if rng.random() < heavy_ratio:
            tool_name = rng.choice(dag.HEAVY_TOOLS)
        else:
            tool_name = rng.choice(dag.LIGHT_TOOLS)
        step_id = f"s{i+1}" if mode == dag.AgentMode.PLAN_AND_SOLVE else f"r{i+1}"
        depends_on = [f"s{i}"] if (mode == dag.AgentMode.PLAN_AND_SOLVE and i > 0) else []
        if mode == dag.AgentMode.REACT and i > 0:
            depends_on = [f"r{i}"]
        steps.append(dag.DAGStep(step_id=step_id, tool_name=tool_name, depends_on=depends_on))

    return dag.SessionPlan(
        session_id=f"{task_id}-r{repeat_idx:02d}",
        mode=mode,
        steps=steps,
        budget=budget,
    )


async def run_cell(gateway_url: str, selected: list[dict[str, Any]], repeat_idx: int, args) -> dict[str, Any]:
    plans = [build_plan_for_task(t, args.budget, args.seed, repeat_idx) for t in selected]

    semaphore = asyncio.Semaphore(args.concurrency)
    req_counter = [0]
    connector = aiohttp.TCPConnector(limit=args.concurrency, limit_per_host=args.concurrency)

    arrival_rate = max(0.0, float(args.arrival_rate))
    mean_interval = (1.0 / arrival_rate) if arrival_rate > 0 else 0.0

    start = time.time()
    async with aiohttp.ClientSession(connector=connector) as http_session:
        tasks: list[asyncio.Task[Any]] = []

        async def run_one(plan):
            async with semaphore:
                return await dag.execute_session(http_session, gateway_url, plan, req_counter)

        for plan in plans:
            tasks.append(asyncio.create_task(run_one(plan)))
            if mean_interval > 0:
                await asyncio.sleep(random.expovariate(arrival_rate))

        completed = await asyncio.gather(*tasks, return_exceptions=False)

    elapsed = time.time() - start
    stats = dag.compute_stats(completed, elapsed)

    total = len(selected)
    success = int(stats.success_sessions)
    cascade_failed = int(stats.cascade_failed)
    step0_rejected = int(stats.rejected_at_step0)
    admitted = success + cascade_failed
    abd_total = (100.0 * cascade_failed / admitted) if admitted > 0 else 0.0
    cascade_rate = (100.0 * cascade_failed / total) if total > 0 else 0.0
    success_rate = (100.0 * success / total) if total > 0 else 0.0
    eff_gps = stats.effective_goodput_total / max(elapsed, 1e-6)
    p50 = percentile(stats.all_latencies, 0.50)
    p95 = percentile(stats.all_latencies, 0.95)

    return {
        "selected_tasks": total,
        "duration_s": round(elapsed, 3),
        "success": success,
        "success_rate": round(success_rate, 3),
        "abd": round(abd_total, 3),
        "abd_total": round(abd_total, 3),
        "cascade_failed": cascade_failed,
        "cascade_rate": round(cascade_rate, 3),
        "all_rejected": step0_rejected,
        "step0_rejected": step0_rejected,
        "effective_goodput_s": round(eff_gps, 6),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "client_rc": 0,
        "client_timed_out": 0,
        "error": "",
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def aggregate_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["gateway"])].append(row)

    out: list[dict[str, Any]] = []
    for gateway in sorted(grouped.keys()):
        bucket = grouped[gateway]
        record: dict[str, Any] = {
            "gateway": gateway,
            "runs": len(bucket),
        }
        for field in NUMERIC_FOR_AGG:
            vals = [float(r[field]) for r in bucket]
            record[f"{field}_mean"] = round(sum(vals) / len(vals), 6)
        out.append(record)
    return out


def clean_artifact_dir() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for p in ARTIFACT_DIR.iterdir():
        if p.is_file():
            try:
                p.unlink()
            except PermissionError:
                pass


def remove_tmp_files() -> None:
    for p in ARTIFACT_DIR.glob("_tmp_*"):
        if p.is_file():
            try:
                p.unlink()
            except PermissionError:
                pass


def build_validation(
    task_files_sha: dict[str, str],
    selected: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    agg_rows: list[dict[str, Any]],
    backend_mode: str,
) -> dict[str, Any]:
    errors: list[str] = []

    gateway_counts = Counter(str(r["gateway"]) for r in summary_rows)
    expected_gateways = ["ng", "static", "pp", "rajomon", "plangate_relaxed"]
    actual_gateways = sorted(gateway_counts.keys())
    if sorted(expected_gateways) != actual_gateways:
        errors.append(f"gateway_set_mismatch expected={expected_gateways} actual={actual_gateways}")

    gw_repeat_counts: dict[str, int] = {}
    for row in summary_rows:
        key = f"{row['gateway']}|{row['repeat']}"
        gw_repeat_counts[key] = gw_repeat_counts.get(key, 0) + 1

    repeats_ok = all(v == 1 for v in gw_repeat_counts.values()) and len(gw_repeat_counts) == 15
    if not repeats_ok:
        errors.append("gateway_repeat_coverage_not_exact_5x3")

    task_count = len(selected)
    selected_task_count_in_range = 20 <= task_count <= 50
    if not selected_task_count_in_range:
        errors.append("selected_task_count_out_of_range")

    plangate_real_absent = all(str(r["gateway"]) != "plangate_real" for r in summary_rows)
    if not plangate_real_absent:
        errors.append("plangate_real_present")

    all_client_rc_zero = all(int(float(r.get("client_rc", 0))) == 0 for r in summary_rows)
    all_client_timed_out_zero = all(int(float(r.get("client_timed_out", 0))) == 0 for r in summary_rows)
    all_error_empty_or_zero = all(str(r.get("error", "")).strip() in ("", "0", "0.0") for r in summary_rows)

    if not all_client_rc_zero:
        errors.append("client_rc_nonzero")
    if not all_client_timed_out_zero:
        errors.append("client_timed_out_nonzero")
    if not all_error_empty_or_zero:
        errors.append("error_field_nonempty")

    # Artifact hygiene: exactly five formal files, no raw logs or secrets.
    allowed_files = {
        "README_RESULT.md",
        "mcpbench_smoke_tasks.csv",
        "mcpbench_smoke_summary.csv",
        "mcpbench_smoke_agg.csv",
        "validation.json",
    }
    formal_files = []
    no_raw_or_secret_artifacts = True
    for p in ARTIFACT_DIR.iterdir():
        if not p.is_file():
            no_raw_or_secret_artifacts = False
            continue
        if p.name.startswith("_tmp_"):
            continue
        formal_files.append(p.name)
        if p.name not in allowed_files:
            no_raw_or_secret_artifacts = False

    formal_files = sorted(formal_files)
    exact_five_files_only = sorted(allowed_files) == formal_files
    if not exact_five_files_only:
        errors.append("artifact_hygiene_not_exact_five_files")

    strict_boundary_flags = {
        "single_machine_only": True,
        "cloudlab_not_used": True,
        "model_accuracy_claim_not_allowed": True,
        "production_claim_not_allowed": True,
        "mechanism_code_changed": False,
        "raw_or_secret_artifacts_present": not no_raw_or_secret_artifacts,
    }

    selected_tasks_brief = [
        {
            "task_id": t["task_id"],
            "combination_type": t["combination_type"],
            "source_file": t["source_file"],
            "estimated_steps": t["estimated_steps"],
        }
        for t in selected
    ]

    return {
        "artifact": "mcpbench_smoke_v1",
        "task_files_sha256": task_files_sha,
        "selected_task_count": task_count,
        "selected_task_count_in_range": selected_task_count_in_range,
        "selected_tasks": selected_tasks_brief,
        "row_count": len(summary_rows),
        "agg_row_count": len(agg_rows),
        "gateway_counts": dict(gateway_counts),
        "gateway_repeat_counts": gw_repeat_counts,
        "gateways": expected_gateways,
        "plangate_real_absent": plangate_real_absent,
        "all_client_rc_zero": all_client_rc_zero,
        "all_client_timed_out_zero": all_client_timed_out_zero,
        "all_error_empty_or_zero": all_error_empty_or_zero,
        "exact_five_files_only": exact_five_files_only,
        "no_raw_or_secret_artifacts": no_raw_or_secret_artifacts,
        "strict_boundary_flags": strict_boundary_flags,
        "backend_mode": backend_mode,
        "errors": errors,
    }


def build_readme(task_files_sha: dict[str, str], selected: list[dict[str, Any]], backend_mode: str, task_set_sha256: str) -> str:
    task_preview = "\n".join(
        [
            f"- {t['task_id']} | {t['combination_type']} | est_steps={t['estimated_steps']} | src={t['source_file']}"
            for t in selected[:15]
        ]
    )

    if len(selected) > 15:
        task_preview += f"\n- ... ({len(selected) - 15} more tasks)"

    sha_lines = "\n".join([f"- {name}: {sha}" for name, sha in sorted(task_files_sha.items())])

    return (
        "# MCP-Bench Small Workflow-Shape Smoke Evidence\n\n"
        "This artifact provides MCP-Bench-derived workflow-shape smoke evidence for PlanGate in a single-machine controlled backend setup.\n"
        "It uses task metadata and dependency structure as workflow-shape input and does not require full MCP-Bench server deployment.\n\n"
        "## Source Metadata\n\n"
        f"{sha_lines}\n"
        f"- selected task count: {len(selected)}\n"
        f"- task_set_sha256: {task_set_sha256}\n"
        f"- backend_mode: {backend_mode}\n\n"
        "## Selected Task Sample\n\n"
        f"{task_preview}\n\n"
        "## Claim Boundary\n\n"
        "Allowed claim:\n"
        "- PlanGate compatibility is smoke-tested against MCP-Bench-derived workflow-shape diversity under single-machine controlled backend conditions.\n\n"
        "Not allowed:\n"
        "- model accuracy or benchmark leaderboard claims\n"
        "- production-readiness claim\n"
        "- CloudLab/distributed-state claim\n"
        "- full MCP-Bench end-to-end deployment equivalence claim\n"
        "- universal policy dominance claim\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    random.seed(args.seed)

    if not (20 <= args.selected_tasks <= 50):
        raise SystemExit("--selected-tasks must be in [20, 50]")

    # Ensure DAG step timeout for this runner.
    dag._STEP_TIMEOUT = float(args.step_timeout)

    task_files_sha = {p.name: sha256_file(p) for p in TASK_FILES}
    all_tasks = normalize_tasks()
    selected = stratified_select(all_tasks, args.selected_tasks, args.seed)
    task_set_sha256 = task_set_sha(task_files_sha, selected)

    if args.dry_run:
        print(f"selected_tasks={len(selected)}")
        print(f"task_set_sha256={task_set_sha256}")
        print("gateways=" + ",".join(g.name for g in GATEWAYS))
        print(f"repeats={args.repeats}")
        return 0

    clean_artifact_dir()

    binary = ensure_gateway_binary((ROOT_DIR / args.gateway_binary) if not Path(args.gateway_binary).is_absolute() else Path(args.gateway_binary))

    summary_rows: list[dict[str, Any]] = []

    backend_proc = None
    try:
        backend_proc = start_backend(args)

        for gateway in GATEWAYS:
            gw_port = find_free_port()
            gw_url = f"http://127.0.0.1:{gw_port}"
            print(f"[mcpbench-smoke] gateway={gateway.name} port={gw_port}")
            gw_proc = start_gateway(binary, gateway, gw_port)
            try:
                for repeat in range(1, args.repeats + 1):
                    print(f"  run gateway={gateway.name} repeat={repeat}/{args.repeats} tasks={len(selected)}")
                    row: dict[str, Any] = {
                        "gateway": gateway.name,
                        "repeat": repeat,
                        "backend_mode": args.backend_mode,
                        "task_set_sha256": task_set_sha256,
                    }
                    try:
                        metrics = asyncio.run(run_cell(gw_url, selected, repeat, args))
                        row.update(metrics)
                    except Exception as exc:
                        row.update(
                            {
                                "selected_tasks": len(selected),
                                "duration_s": 0.0,
                                "success": 0,
                                "success_rate": 0.0,
                                "abd": 0.0,
                                "abd_total": 0.0,
                                "cascade_failed": 0,
                                "cascade_rate": 0.0,
                                "all_rejected": 0,
                                "step0_rejected": 0,
                                "effective_goodput_s": 0.0,
                                "p50_ms": 0.0,
                                "p95_ms": 0.0,
                                "client_rc": 1,
                                "client_timed_out": 0,
                                "error": str(exc),
                            }
                        )
                    summary_rows.append(row)
            finally:
                stop_process(gw_proc)
                time.sleep(1)
    finally:
        stop_process(backend_proc)

    agg_rows = aggregate_summary(summary_rows)

    tasks_path = ARTIFACT_DIR / "mcpbench_smoke_tasks.csv"
    summary_path = ARTIFACT_DIR / "mcpbench_smoke_summary.csv"
    agg_path = ARTIFACT_DIR / "mcpbench_smoke_agg.csv"
    validation_path = ARTIFACT_DIR / "validation.json"
    readme_path = ARTIFACT_DIR / "README_RESULT.md"

    task_rows = [
        {
            "task_id": t["task_id"],
            "server_name": t["server_name"],
            "combination_name": t["combination_name"],
            "combination_type": t["combination_type"],
            "source_file": t["source_file"],
            "estimated_steps": t["estimated_steps"],
            "estimated_heavy_ratio": t["estimated_heavy_ratio"],
            "mode_hint": t["mode_hint"],
        }
        for t in selected
    ]

    write_csv(tasks_path, TASK_COLUMNS, task_rows)
    write_csv(summary_path, SUMMARY_COLUMNS, summary_rows)
    write_csv(agg_path, AGG_COLUMNS, agg_rows)

    # Ensure hygiene checks run after tmp logs are dropped.
    remove_tmp_files()

    readme = build_readme(task_files_sha, selected, args.backend_mode, task_set_sha256)
    readme_path.write_text(readme, encoding="utf-8")

    # Place holder so hygiene checks evaluate final five-file artifact shape.
    if not validation_path.exists():
        validation_path.write_text("{}\n", encoding="utf-8")

    validation = build_validation(task_files_sha, selected, summary_rows, agg_rows, args.backend_mode)
    validation_path.write_text(json.dumps(validation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(validation, indent=2, ensure_ascii=False))
    return 0 if not validation["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
