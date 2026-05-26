#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import csv
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
BASE_RUNNER_PATH = SCRIPT_DIR / "run_selfhosted_vllm.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base_runner = load_module(BASE_RUNNER_PATH, "run_selfhosted_vllm_base")


@dataclass(frozen=True)
class StressProfile:
    name: str
    agents: int
    concurrency: int
    max_workers: int
    burst_size: int
    burst_gap: float
    max_steps: int
    budget: int
    gateways: tuple[str, ...]


@dataclass(frozen=True)
class GatewayConfig:
    name: str
    mode: str
    extra_args: tuple[str, ...] = ()
    label: str = ""
    aliases: tuple[str, ...] = ()


STRESS_PROFILES = {
    "stress": StressProfile(
        name="stress",
        agents=100,
        concurrency=20,
        max_workers=4,
        burst_size=25,
        burst_gap=4.0,
        max_steps=10,
        budget=800,
        gateways=("ng", "plangate_real"),
    ),
    "c20w4": StressProfile(
        name="c20w4",
        agents=100,
        concurrency=20,
        max_workers=4,
        burst_size=25,
        burst_gap=4.0,
        max_steps=10,
        budget=800,
        gateways=("ng", "plangate_real"),
    ),
    "c40w8": StressProfile(
        name="c40w8",
        agents=100,
        concurrency=40,
        max_workers=8,
        burst_size=25,
        burst_gap=4.0,
        max_steps=10,
        budget=800,
        gateways=("ng", "plangate_real"),
    ),
}

# Explicit stress-runner gateway registry (do not rely on base_runner.GATEWAYS).
# Keep default profile conservative (ng + plangate_real), but support richer
# baseline sets for formal dry-run / experiment planning.
GATEWAY_REGISTRY: dict[str, GatewayConfig] = {
    "ng": GatewayConfig(
        name="ng",
        mode="ng",
        label="no governance",
    ),
    "static": GatewayConfig(
        name="static",
        mode="srl",
        extra_args=(
            "--srl-qps", "65",
            "--srl-burst", "400",
            "--srl-max-conc", "55",
        ),
        label="static rate limit baseline",
        aliases=("srl", "static_rl", "rate_limit"),
    ),
    "pp": GatewayConfig(
        name="pp",
        mode="pp",
        extra_args=("--pp-max-sessions", "150"),
        label="price-priority / proxy pricing baseline",
    ),
    "rajomon": GatewayConfig(
        name="rajomon",
        mode="rajomon",
        extra_args=("--rajomon-price-step", "5"),
        label="rajomon baseline",
    ),
    "plangate_real": GatewayConfig(
        name="plangate_real",
        mode="mcpdp-real",
        extra_args=(
            "--plangate-price-step", "40",
            "--plangate-max-sessions", "12",
            "--plangate-sunk-cost-alpha", "0.7",
            "--plangate-session-cap-wait", "3",
            "--real-ratelimit-max", "9999",
            "--real-latency-threshold", "10000",
        ),
        label="PlanGate real-LLM mechanism",
    ),
    "plangate_relaxed": GatewayConfig(
        name="plangate_relaxed",
        mode="mcpdp-real",
        extra_args=(
            "--plangate-price-step", "30",
            "--plangate-max-sessions", "24",
            "--plangate-sunk-cost-alpha", "0.7",
            "--plangate-session-cap-wait", "6",
            "--real-ratelimit-max", "9999",
            "--real-latency-threshold", "10000",
        ),
        label="PlanGate relaxed admission baseline",
    ),
}

GATEWAY_ORDER: tuple[str, ...] = (
    "ng",
    "static",
    "pp",
    "rajomon",
    "plangate_real",
    "plangate_relaxed",
)


def _normalize_gateway_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _gateway_alias_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for canonical, cfg in GATEWAY_REGISTRY.items():
        mapping[_normalize_gateway_name(canonical)] = canonical
        for alias in cfg.aliases:
            mapping[_normalize_gateway_name(alias)] = canonical
    return mapping

SUMMARY_FIELDS = [
    "gateway",
    "run",
    "success",
    "partial",
    "all_rejected",
    "error",
    "abd_total",
    "success_rate",
    "cascade_agents",
    "cascade_steps",
    "eff_gps",
    "p50_ms",
    "p95_ms",
    "http_429_count",
    "client_rc",
    "client_timed_out",
    "csv",
]

NUMERIC_SUMMARY_FIELDS = [
    "success",
    "partial",
    "all_rejected",
    "error",
    "abd_total",
    "success_rate",
    "cascade_agents",
    "cascade_steps",
    "eff_gps",
    "p50_ms",
    "p95_ms",
    "http_429_count",
    "client_rc",
    "client_timed_out",
]


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Stress-oriented self-hosted vLLM runner (no mechanism changes)."
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--agents", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--burst-size", type=int, default=None)
    parser.add_argument("--burst-gap", type=float, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--stress-profile", choices=sorted(STRESS_PROFILES.keys()), default="stress")
    parser.add_argument("--out-dir", type=str, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gateways", nargs="+", default=None)
    parser.add_argument(
        "--gateway-binary",
        type=str,
        default="gateway.exe" if sys.platform == "win32" else "gateway",
    )
    parser.add_argument("--vllm-base", type=str, default=base_runner.VLLM_BASE_URL)
    parser.add_argument("--vllm-model", type=str, default=base_runner.VLLM_MODEL)
    parser.add_argument("--backend-url", type=str, default=base_runner.BACKEND_URL)
    parser.add_argument("--base-port", type=int, default=base_runner.BASE_PORT)
    parser.add_argument("--client-timeout", type=int, default=3600)
    parser.add_argument("--task-profile", choices=["default", "stress"], default="stress")
    return parser.parse_args(argv)


def available_gateways():
    return [GATEWAY_REGISTRY[name] for name in GATEWAY_ORDER if name in GATEWAY_REGISTRY]


def resolve_settings(args) -> dict[str, object]:
    profile = STRESS_PROFILES[args.stress_profile]
    selected_gateways = tuple(args.gateways) if args.gateways else profile.gateways
    return {
        "stress_profile": args.stress_profile,
        "agents": args.agents if args.agents is not None else profile.agents,
        "concurrency": args.concurrency if args.concurrency is not None else profile.concurrency,
        "max_workers": args.max_workers if args.max_workers is not None else profile.max_workers,
        "burst_size": args.burst_size if args.burst_size is not None else profile.burst_size,
        "burst_gap": args.burst_gap if args.burst_gap is not None else profile.burst_gap,
        "max_steps": args.max_steps if args.max_steps is not None else profile.max_steps,
        "budget": args.budget if args.budget is not None else profile.budget,
        "gateways": selected_gateways,
        "gateway_binary": str(Path(args.gateway_binary)),
        "vllm_base": args.vllm_base,
        "vllm_model": args.vllm_model,
        "backend_url": args.backend_url,
        "base_port": args.base_port,
        "client_timeout": args.client_timeout,
        "task_profile": args.task_profile,
    }


def results_dir(args, settings: dict[str, object]) -> Path:
    if args.out_dir:
        return Path(args.out_dir)
    return ROOT_DIR / "results" / f"exp_selfhosted_vllm_stress_{settings['stress_profile']}"


def summary_path(root: Path) -> Path:
    return root / "selfhosted_vllm_stress_summary.csv"


def aggregate_path(root: Path) -> Path:
    return root / "selfhosted_vllm_stress_agg.csv"


def run_dir_for(root: Path, gateway: str, run_idx: int) -> Path:
    return root / gateway / f"run{run_idx}"


def select_gateway_configs(names: Iterable[str]):
    alias_map = _gateway_alias_map()
    selected: list[GatewayConfig] = []
    seen: set[str] = set()
    missing: list[str] = []
    for raw_name in names:
        normalized = _normalize_gateway_name(str(raw_name))
        canonical = alias_map.get(normalized)
        if canonical is None:
            missing.append(str(raw_name))
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        selected.append(GATEWAY_REGISTRY[canonical])
    if missing:
        supported = ", ".join(name for name in GATEWAY_ORDER if name in GATEWAY_REGISTRY)
        raise SystemExit(
            f"unknown gateways requested: {', '.join(sorted(missing))}; "
            f"supported gateways: {supported}"
        )
    return selected


def ensure_gateway_binary(path: Path) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["go", "build", "-o", str(path), "./cmd/gateway"],
        cwd=str(ROOT_DIR),
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return path


def backend_command(settings: dict[str, object]) -> list[str]:
    return [
        sys.executable,
        str(Path(base_runner.SERVER_PY)),
        "--port",
        "8080",
        "--mode",
        "real_llm",
        "--max-workers",
        str(settings["max_workers"]),
        "--queue-timeout",
        "10.0",
        "--congestion-factor",
        "0.5",
    ]


def gateway_command(binary: Path, settings: dict[str, object], gateway_cfg, port: int) -> list[str]:
    return [
        str(binary),
        "--mode",
        gateway_cfg.mode,
        "--port",
        str(port),
        "--backend",
        str(settings["backend_url"]),
        "--host",
        "127.0.0.1",
        *list(gateway_cfg.extra_args),
    ]


def client_command(settings: dict[str, object], gateway_url: str, output_csv: Path, gateway_name: str) -> list[str]:
    return [
        sys.executable,
        str(Path(base_runner.REACT_CLIENT)),
        "--gateway",
        gateway_url,
        "--agents",
        str(settings["agents"]),
        "--concurrency",
        str(settings["concurrency"]),
        "--max-steps",
        str(settings["max_steps"]),
        "--budget",
        str(settings["budget"]),
        "--burst-size",
        str(settings["burst_size"]),
        "--burst-gap",
        str(settings["burst_gap"]),
        "--gateway-mode",
        gateway_name,
        "--task-profile",
        str(settings["task_profile"]),
        "--output",
        str(output_csv),
        "--llm-timeout",
        "60",
        "--progress-every",
        "5",
    ]


def print_dry_run(args, settings: dict[str, object], gateway_cfgs) -> None:
    out_root = results_dir(args, settings)
    print("[selfhosted-vllm-stress dry-run]")
    print(f"agents={settings['agents']}")
    print(f"concurrency={settings['concurrency']}")
    print(f"max_workers={settings['max_workers']}")
    print(f"burst_size={settings['burst_size']}")
    print(f"burst_gap={settings['burst_gap']}")
    print(f"max_steps={settings['max_steps']}")
    print(f"budget={settings['budget']}")
    print(f"gateways={', '.join(settings['gateways'])}")
    print(f"vllm_base={settings['vllm_base']}")
    print(f"vllm_model={settings['vllm_model']}")
    print(f"stress_profile={settings['stress_profile']}")
    print(f"task_profile={settings['task_profile']}")
    print(f"results_dir={out_root}")
    print(f"summary_path={summary_path(out_root)}")
    print(f"gateway_binary={settings['gateway_binary']}")
    print(f"resume={args.resume}")
    print("")
    print("backend_cmd:")
    print("  " + " ".join(backend_command(settings)))
    for idx, gateway_cfg in enumerate(gateway_cfgs):
        port = int(settings["base_port"]) + idx
        gateway_url = f"http://127.0.0.1:{port}"
        run_root = run_dir_for(out_root, gateway_cfg.name, 1)
        output_csv = run_root / "steps.csv"
        print("")
        print(f"gateway={gateway_cfg.name}")
        print(f"mode={gateway_cfg.mode}")
        print(f"port={port}")
        print(f"extra_args={list(gateway_cfg.extra_args)}")
        print(f"label={gateway_cfg.label}")
        print("backend_cmd:")
        print("  " + " ".join(backend_command(settings)))
        print("gateway_cmd:")
        print("  " + " ".join(gateway_command(Path(settings["gateway_binary"]), settings, gateway_cfg, port)))
        print("client_cmd:")
        print("  " + " ".join(client_command(settings, gateway_url, output_csv, gateway_cfg.name)))


def start_backend(settings: dict[str, object], log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "_backend_selfhosted_vllm_stress.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_API_BASE"] = str(settings["vllm_base"])
    env["LLM_API_KEY"] = base_runner.VLLM_API_KEY
    env["LLM_MODEL"] = str(settings["vllm_model"])
    proc = subprocess.Popen(
        backend_command(settings),
        cwd=str(Path(base_runner.SERVER_PY).parent),
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    time.sleep(4)
    if proc.poll() is not None:
        raise RuntimeError(f"backend failed to start; see {log_path}")
    return proc


def start_gateway(binary: Path, settings: dict[str, object], gateway_cfg, port: int, log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"_gw_{gateway_cfg.name}_stress.log"
    proc = subprocess.Popen(
        gateway_command(binary, settings, gateway_cfg, port),
        cwd=str(ROOT_DIR),
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"gateway {gateway_cfg.name} failed to start; see {log_path}")
    return proc


def stop_process(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        with contextlib.suppress(Exception):  # type: ignore[name-defined]
            proc.kill()


def run_client(settings: dict[str, object], gateway_url: str, output_csv: Path, gateway_name: str):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["AGENT_LLM_BASE"] = base_runner.AGENT_LLM_BASE
    env["AGENT_LLM_KEY"] = base_runner.AGENT_LLM_KEY
    env["AGENT_LLM_MODEL"] = base_runner.AGENT_LLM_MODEL
    cmd = client_command(settings, gateway_url, output_csv, gateway_name)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(settings["client_timeout"]),
        )
        return {
            "cmd": cmd,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "returncode": result.returncode,
            "timed_out": 0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "returncode": -1,
            "timed_out": 1,
        }


def build_summary_row(settings: dict[str, object], gateway_name: str, run_idx: int, client_result: dict[str, object], output_csv: Path):
    base_runner.AGENTS = int(settings["agents"])
    stats = base_runner.parse_stats(str(client_result["stdout"]))
    row = {field: "" for field in SUMMARY_FIELDS}
    row.update(stats)
    row["gateway"] = gateway_name
    row["run"] = run_idx
    row["client_rc"] = int(client_result["returncode"])
    row["client_timed_out"] = int(client_result["timed_out"])
    row["csv"] = str(output_csv)
    for key in ["success", "partial", "all_rejected", "error", "cascade_agents", "cascade_steps", "http_429_count"]:
        row[key] = int(row.get(key, 0) or 0)
    for key in ["abd_total", "success_rate", "eff_gps", "p50_ms", "p95_ms"]:
        row[key] = float(row.get(key, 0.0) or 0.0)
    return row


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def aggregate_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["gateway"])].append(row)
    out = []
    for gateway in sorted(grouped.keys()):
        bucket = grouped[gateway]
        record = {"gateway": gateway, "runs": len(bucket)}
        for field in NUMERIC_SUMMARY_FIELDS:
            values = [float(row[field]) for row in bucket]
            record[f"{field}_mean"] = round(sum(values) / len(values), 2)
        out.append(record)
    return out


def write_aggregate(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def validate_summary(path: Path):
    errors: list[str] = []
    if not path.exists():
        return {"errors": [f"missing summary: {path}"], "row_count": 0, "gateway_counts": {}}

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        missing = [field for field in SUMMARY_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            errors.append(f"missing summary fields: {', '.join(missing)}")

    gateway_counts = Counter(row.get("gateway", "") for row in rows)
    all_client_rc_zero = True
    all_client_timed_out_zero = True
    all_error_empty = True

    for idx, row in enumerate(rows, start=2):
        if not row.get("gateway"):
            errors.append(f"row {idx} has empty gateway")
        for field in ["client_rc", "client_timed_out", "success", "partial", "all_rejected"]:
            try:
                value = int(float(row.get(field, "0")))
            except ValueError:
                errors.append(f"row {idx} has non-numeric {field}: {row.get(field)!r}")
                continue
            if field == "client_rc" and value != 0:
                all_client_rc_zero = False
            if field == "client_timed_out" and value != 0:
                all_client_timed_out_zero = False
        error_value = str(row.get("error", "")).strip()
        if error_value not in ("", "0", "0.0"):
            all_error_empty = False
        for field in ["abd_total", "success_rate", "cascade_agents", "cascade_steps", "eff_gps", "p50_ms", "p95_ms", "http_429_count"]:
            try:
                float(row.get(field, "0"))
            except ValueError:
                errors.append(f"row {idx} has non-numeric {field}: {row.get(field)!r}")

    return {
        "row_count": len(rows),
        "gateway_counts": dict(gateway_counts),
        "all_client_rc_zero": all_client_rc_zero,
        "all_client_timed_out_zero": all_client_timed_out_zero,
        "all_error_empty": all_error_empty,
        "errors": errors,
    }


def run_experiment(args, settings: dict[str, object]) -> int:
    out_root = results_dir(args, settings)
    log_dir = out_root / "_logs"
    gateway_cfgs = select_gateway_configs(settings["gateways"])
    binary = ensure_gateway_binary(Path(str(settings["gateway_binary"])))
    out_root.mkdir(parents=True, exist_ok=True)

    backend_proc = start_backend(settings, log_dir)
    summary_rows: list[dict[str, object]] = []
    try:
        for gateway_index, gateway_cfg in enumerate(gateway_cfgs):
            port = int(settings["base_port"]) + gateway_index
            gateway_url = f"http://127.0.0.1:{port}"
            for run_idx in range(1, args.repeats + 1):
                run_root = run_dir_for(out_root, gateway_cfg.name, run_idx)
                run_root.mkdir(parents=True, exist_ok=True)
                output_csv = run_root / "steps.csv"
                if args.resume and output_csv.exists():
                    continue

                print(
                    f"[selfhosted-vllm-stress] gateway={gateway_cfg.name} run={run_idx}/{args.repeats} "
                    f"agents={settings['agents']} concurrency={settings['concurrency']} "
                    f"workers={settings['max_workers']} burst={settings['burst_size']}x{settings['burst_gap']} "
                    f"max_steps={settings['max_steps']} task_profile={settings['task_profile']}"
                )

                gateway_proc = start_gateway(binary, settings, gateway_cfg, port, log_dir)
                try:
                    client_result = run_client(settings, gateway_url, output_csv, gateway_cfg.name)
                    (run_root / "stdout.txt").write_text(str(client_result["stdout"]), encoding="utf-8")
                    (run_root / "stderr.txt").write_text(str(client_result["stderr"]), encoding="utf-8")
                    summary_rows.append(
                        build_summary_row(settings, gateway_cfg.name, run_idx, client_result, output_csv)
                    )
                finally:
                    stop_process(gateway_proc)
                    time.sleep(2)
    finally:
        stop_process(backend_proc)

    write_summary(summary_path(out_root), summary_rows)
    write_aggregate(aggregate_path(out_root), aggregate_rows(summary_rows))
    validation = validate_summary(summary_path(out_root))
    print(json.dumps(validation, indent=2))
    return 0 if not validation["errors"] else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = resolve_settings(args)
    gateway_cfgs = select_gateway_configs(settings["gateways"])
    if args.dry_run:
        print_dry_run(args, settings, gateway_cfgs)
        return 0
    return run_experiment(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
