#!/usr/bin/env python3
"""Week 5 real-LLM runner with GLM preflight and better observability."""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from real_llm_utils import (
    classify_llm_error,
    format_exception_message,
    load_project_dotenv,
    mask_secret,
    resolve_llm_config,
    run_llm_preflight,
)


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
REACT_CLIENT = SCRIPT_DIR / "react_agent_client.py"
SERVER_PY = ROOT_DIR / "mcp_server" / "server.py"
LOG_DIR = ROOT_DIR / "results" / "log" / "real_llm"

BACKEND_URL = "http://127.0.0.1:8080"
BASE_PORT = 9300
BUDGET = 1000
DEFAULT_AGENTS = 200
DEFAULT_CONCURRENCY = 10
DEFAULT_MAX_STEPS = 10
DEFAULT_ARRIVAL_INTERVAL = 0.3
DEFAULT_CLIENT_TIMEOUT = 3600
DEFAULT_LLM_TIMEOUT = 60

TUNED_PARAMS = {
    "rajomon": {"price_step": 5},
    "pp": {"max_sessions": 150},
    "plangate": {"price_step": 40, "max_sessions": 50, "sunk_cost_alpha": 0.5},
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list[str] = field(default_factory=list)


GATEWAYS: list[GatewayConfig] = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("rajomon", "rajomon", [
        "--rajomon-price-step", str(TUNED_PARAMS["rajomon"]["price_step"]),
    ]),
    GatewayConfig("pp", "pp", [
        "--pp-max-sessions", str(TUNED_PARAMS["pp"]["max_sessions"]),
    ]),
    GatewayConfig("plangate_real", "mcpdp-real", [
        "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
        "--plangate-max-sessions", str(TUNED_PARAMS["plangate"]["max_sessions"]),
        "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
        "--plangate-session-cap-wait", "5",
        "--real-ratelimit-max", "200",
        "--real-latency-threshold", "5000",
    ]),
]

BACKEND_PROC: Optional[subprocess.Popen] = None
GATEWAY_BINARY: Optional[Path] = None


def get_results_dir(concurrency: int) -> Path:
    return ROOT_DIR / "results" / f"exp_week5_C{concurrency}"


def configure_gateways(concurrency: int) -> list[GatewayConfig]:
    gateways: list[GatewayConfig] = []
    pg_max_sessions = max(50, concurrency * 3)
    for gateway in GATEWAYS:
        if gateway.name == "plangate_real":
            gateways.append(GatewayConfig(
                name=gateway.name,
                mode=gateway.mode,
                extra_args=[
                    "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
                    "--plangate-max-sessions", str(pg_max_sessions),
                    "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
                    "--plangate-session-cap-wait", "5",
                    "--real-ratelimit-max", "200",
                    "--real-latency-threshold", "5000",
                ],
            ))
        else:
            gateways.append(GatewayConfig(gateway.name, gateway.mode, list(gateway.extra_args)))
    return gateways


def build_gateway() -> Path:
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = ROOT_DIR / bin_name
    print(f"  Building gateway binary: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", str(bin_path), "./cmd/gateway"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gateway build failed: {result.stderr.strip()[:500]}")
    print(f"  Gateway binary ready: {bin_path}")
    return bin_path


def stop_process(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def start_backend() -> None:
    global BACKEND_PROC
    stop_backend()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "_backend_week5.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        str(SERVER_PY),
        "--port", "8080",
        "--mode", "real_llm",
        "--max-workers", "10",
        "--queue-timeout", "8.0",
        "--congestion-factor", "0.5",
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        BACKEND_PROC = subprocess.Popen(
            cmd,
            cwd=SERVER_PY.parent,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(4)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"backend failed to start; inspect {log_path}")
    print(f"  Backend started: pid={BACKEND_PROC.pid} mode=real_llm log={log_path}")


def stop_backend() -> None:
    global BACKEND_PROC
    stop_process(BACKEND_PROC)
    BACKEND_PROC = None


def start_gateway(gateway: GatewayConfig, port: int) -> subprocess.Popen:
    assert GATEWAY_BINARY is not None
    cmd = [
        str(GATEWAY_BINARY),
        "--mode", gateway.mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", "127.0.0.1",
    ] + list(gateway.extra_args)
    log_path = LOG_DIR / f"_gw_{gateway.name}_week5.log"
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT_DIR,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"gateway {gateway.name} failed to start; inspect {log_path}")
    print(f"  Gateway [{gateway.name}] started: pid={proc.pid} port={port} mode={gateway.mode}")
    return proc


def run_glm_preflight_checked() -> None:
    cfg = resolve_llm_config()
    print("  LLM preflight:")
    print(f"    base:  {cfg.base}")
    print(f"    model: {cfg.model}")
    print(f"    key:   {mask_secret(cfg.key)}")
    try:
        result = run_llm_preflight(timeout_seconds=30.0, max_tokens=8)
    except Exception as exc:
        raise RuntimeError(f"GLM preflight failed ({classify_llm_error(exc)}): {format_exception_message(exc)}") from exc

    print(f"    reply:   {result['response_text'] or '(empty)'}")
    print(
        "    usage:  "
        f"prompt={result['usage']['prompt_tokens']} "
        f"completion={result['usage']['completion_tokens']} "
        f"total={result['usage']['total_tokens']}"
    )
    print(f"    elapsed: {result['elapsed_seconds']:.2f}s")


def parse_react_stats(stdout_text: str, agents: int) -> dict:
    stats: dict[str, float | int | str] = {}
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue

        matchers = {
            "success": r"\bSUCCESS:\s+(\d+)",
            "partial": r"\bPARTIAL:\s+(\d+)",
            "all_rejected": r"\bALL_REJECTED:\s+(\d+)",
            "error": r"\bERROR:\s+(\d+)",
            "cascade_agents": r"(?:cascade.*Agent|级联浪费 Agent):\s*(\d+)",
            "cascade_steps": r"(?:cascade.*steps?|级联浪费步骤):\s*(\d+)",
        }
        for key, pattern in matchers.items():
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                stats[key] = int(match.group(1))

        if "Effective GP/s:" in line:
            match = re.search(r"Effective GP/s:\s*([0-9.]+)", line)
            if match:
                stats["eff_gps"] = float(match.group(1))
        elif "Effective GP:" in line:
            match = re.search(r"Effective GP:\s*([0-9.]+)", line)
            if match:
                stats["eff_gp"] = float(match.group(1))

        if "Agent Brain:" in line:
            match = re.search(r"Agent Brain:\s*([0-9,]+)", line)
            if match:
                stats["agent_tokens"] = int(match.group(1).replace(",", ""))
        if "Backend LLM:" in line:
            match = re.search(r"Backend LLM:\s*([0-9,]+)", line)
            if match:
                stats["backend_tokens"] = int(match.group(1).replace(",", ""))

        if "P50:" in line and "P95:" in line:
            p50 = re.search(r"P50:\s*([0-9.]+)ms", line)
            p95 = re.search(r"P95:\s*([0-9.]+)ms", line)
            mean = re.search(r"Mean:\s*([0-9.]+)ms", line)
            if p50:
                stats["p50_ms"] = float(p50.group(1))
            if p95:
                stats["p95_ms"] = float(p95.group(1))
            if mean:
                stats["mean_ms"] = float(mean.group(1))

        if "elapsed" in line.lower() or "total time" in line.lower() or "总耗时" in line:
            match = re.search(r"([0-9.]+)s", line)
            if match:
                stats["elapsed_s"] = float(match.group(1))

    success = int(stats.get("success", 0))
    partial = int(stats.get("partial", 0))
    admitted = success + partial
    stats["abd_total"] = round(100 * partial / admitted, 1) if admitted > 0 else 0.0
    stats["success_rate"] = round(100 * success / agents, 1) if agents > 0 else 0.0
    return stats


def _reader_thread(stream, buffer: list[str], handle, live: bool) -> None:
    for line in iter(stream.readline, ""):
        buffer.append(line)
        handle.write(line)
        handle.flush()
        if live:
            print(line, end="")


def run_react_client(
    gateway_url: str,
    output_csv: Path,
    gateway_name: str,
    agents: int,
    concurrency: int,
    max_steps: int,
    arrival_interval: float,
    client_timeout: int,
    client_log_live: bool,
    llm_timeout: int = DEFAULT_LLM_TIMEOUT,
) -> dict:
    cmd = [
        sys.executable,
        str(REACT_CLIENT),
        "--gateway", gateway_url,
        "--agents", str(agents),
        "--concurrency", str(concurrency),
        "--max-steps", str(max_steps),
        "--budget", str(BUDGET),
        "--arrival-interval", str(arrival_interval),
        "--gateway-mode", gateway_name,
        "--output", str(output_csv),
        "--llm-timeout", str(llm_timeout),
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    log_path = LOG_DIR / f"_client_{gateway_name}_week5.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    capture: list[str] = []

    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n=== run start gateway={gateway_name} url={gateway_url} ts={time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log_handle.write("CMD: " + " ".join(cmd) + "\n")
        log_handle.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        reader = threading.Thread(target=_reader_thread, args=(proc.stdout, capture, log_handle, client_log_live), daemon=True)
        reader.start()

        timed_out = False
        try:
            proc.wait(timeout=client_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            log_handle.write(f"\n[runner] client timeout after {client_timeout}s; terminating process\n")
            log_handle.flush()
            stop_process(proc)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            reader.join(timeout=5)
            log_handle.write(f"=== run end rc={proc.poll()} timed_out={timed_out} ===\n")
            log_handle.flush()

    stdout_text = "".join(capture)
    if proc.returncode not in (0, None):
        print(f"  Client exited with rc={proc.returncode}; see {log_path}")
    if timed_out:
        print(f"  Client timed out after {client_timeout}s; see {log_path}")

    return {
        "stdout": stdout_text,
        "returncode": proc.returncode if proc.returncode is not None else -1,
        "timed_out": timed_out,
        "log_path": str(log_path),
    }


def print_summary_table(rows: list[dict], gateways: Iterable[GatewayConfig]) -> None:
    import statistics

    print("\n" + "=" * 96)
    print("  Week 5 real-LLM summary")
    print("=" * 96)
    print(f"{'Gateway':<18} {'SuccRate%':>10} {'ABD%':>10} {'GP/s':>10} {'P50ms':>10} {'P95ms':>10} {'Tokens':>12}")
    print(f"{'-'*18} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")

    grouped: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        bucket = grouped.setdefault(row["gateway"], {"sr": [], "abd": [], "gps": [], "p50": [], "p95": [], "tok": []})
        bucket["sr"].append(float(row["success_rate"]))
        bucket["abd"].append(float(row["abd_total"]))
        bucket["gps"].append(float(row["eff_gps"]))
        bucket["p50"].append(float(row["p50_ms"]))
        bucket["p95"].append(float(row["p95_ms"]))
        bucket["tok"].append(float(row["agent_tokens"]) + float(row["backend_tokens"]))

    for gateway in gateways:
        if gateway.name not in grouped:
            continue
        bucket = grouped[gateway.name]
        sr_mean = statistics.mean(bucket["sr"])
        abd_mean = statistics.mean(bucket["abd"])
        gps_mean = statistics.mean(bucket["gps"])
        p50_mean = statistics.mean(bucket["p50"])
        p95_mean = statistics.mean(bucket["p95"])
        tok_mean = statistics.mean(bucket["tok"])
        sr_std = statistics.stdev(bucket["sr"]) if len(bucket["sr"]) > 1 else 0.0
        abd_std = statistics.stdev(bucket["abd"]) if len(bucket["abd"]) > 1 else 0.0
        gps_std = statistics.stdev(bucket["gps"]) if len(bucket["gps"]) > 1 else 0.0
        print(
            f"{gateway.name:<18} "
            f"{sr_mean:>7.1f}±{sr_std:<4.1f} "
            f"{abd_mean:>7.1f}±{abd_std:<4.1f} "
            f"{gps_mean:>7.2f}±{gps_std:<5.2f} "
            f"{p50_mean:>9.0f} {p95_mean:>9.0f} {tok_mean:>11,.0f}"
        )


def run_experiment(args: argparse.Namespace, gateways: list[GatewayConfig]) -> None:
    results_dir = get_results_dir(args.concurrency)
    results_dir.mkdir(parents=True, exist_ok=True)
    summary_path = results_dir / "week5_summary.csv"
    summary_rows: list[dict] = []

    for gateway in gateways:
        for run_idx in range(1, args.repeats + 1):
            port = BASE_PORT + gateways.index(gateway)
            gateway_url = f"http://127.0.0.1:{port}"
            run_dir = results_dir / gateway.name / f"run{run_idx}"
            run_dir.mkdir(parents=True, exist_ok=True)
            output_csv = run_dir / "steps.csv"

            print("\n" + "=" * 72)
            print(f"  [{gateway.name}] Run {run_idx}/{args.repeats}")
            print(f"  Sessions/Agents = {args.agents}  Concurrency = {args.concurrency}  Max Steps = {args.max_steps}")
            print("=" * 72)

            if args.dry_run:
                print(f"  [DRY-RUN] gateway mode: {gateway.mode}")
                print(f"  [DRY-RUN] output path: {output_csv}")
                print(
                    "  [DRY-RUN] client cmd: "
                    f"react_agent_client --agents {args.agents} --concurrency {args.concurrency} "
                    f"--max-steps {args.max_steps} --arrival-interval {args.arrival_interval}"
                )
                continue

            gateway_proc = start_gateway(gateway, port)
            time.sleep(2)

            try:
                client_result = run_react_client(
                    gateway_url=gateway_url,
                    output_csv=output_csv,
                    gateway_name=gateway.name,
                    agents=args.agents,
                    concurrency=args.concurrency,
                    max_steps=args.max_steps,
                    arrival_interval=args.arrival_interval,
                    client_timeout=args.client_timeout,
                    client_log_live=args.client_log_live,
                )
                stats = parse_react_stats(client_result["stdout"], args.agents)
                row = {
                    "gateway": gateway.name,
                    "run": run_idx,
                    "agents": args.agents,
                    "success": int(stats.get("success", 0)),
                    "partial": int(stats.get("partial", 0)),
                    "all_rejected": int(stats.get("all_rejected", 0)),
                    "error": int(stats.get("error", 0)),
                    "cascade_agents": int(stats.get("cascade_agents", 0)),
                    "cascade_steps": int(stats.get("cascade_steps", 0)),
                    "success_rate": stats.get("success_rate", 0),
                    "abd_total": stats.get("abd_total", 0),
                    "eff_gps": stats.get("eff_gps", 0),
                    "agent_tokens": int(stats.get("agent_tokens", 0)),
                    "backend_tokens": int(stats.get("backend_tokens", 0)),
                    "p50_ms": stats.get("p50_ms", 0),
                    "p95_ms": stats.get("p95_ms", 0),
                    "elapsed_s": stats.get("elapsed_s", 0),
                    "client_rc": client_result["returncode"],
                    "client_timed_out": int(client_result["timed_out"]),
                    "client_log": client_result["log_path"],
                }
                summary_rows.append(row)

                print("\n  Result snapshot")
                print(
                    f"  success={row['success']} partial={row['partial']} "
                    f"rejected={row['all_rejected']} error={row['error']}"
                )
                print(f"  success_rate={row['success_rate']}%  ABD={row['abd_total']:.1f}%")
                print(f"  eff_GP/s={float(row['eff_gps']):.2f}")
                print(f"  P50={float(row['p50_ms']):.0f}ms  P95={float(row['p95_ms']):.0f}ms")
                print(f"  tokens: agent={row['agent_tokens']:,} backend={row['backend_tokens']:,}")
                print(f"  elapsed: {float(row['elapsed_s']):.1f}s")
                print(f"  client log: {row['client_log']}")
            finally:
                stop_process(gateway_proc)
                cooldown = 30 if run_idx < args.repeats else 10
                print(f"  Cooldown {cooldown}s before next run...")
                time.sleep(cooldown)

    if summary_rows:
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\n  Wrote summary CSV: {summary_path}")
        print_summary_table(summary_rows, gateways)


def resolve_requested_gateways(names: Optional[list[str]], configured_gateways: list[GatewayConfig]) -> list[GatewayConfig]:
    if not names:
        return configured_gateways
    selected = [gateway for gateway in configured_gateways if gateway.name in names]
    if not selected:
        raise ValueError(f"no matching gateways for {names}; available: {[gateway.name for gateway in configured_gateways]}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Week 5 real-LLM runner with GLM smoke preflight")
    parser.add_argument("--repeats", type=int, default=5, help="number of repeats")
    parser.add_argument("--agents", type=int, default=DEFAULT_AGENTS, help="number of agents/sessions")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="max concurrent agents")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="max tool steps per agent")
    parser.add_argument("--arrival-interval", type=float, default=DEFAULT_ARRIVAL_INTERVAL, help="agent arrival interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="print configuration only")
    parser.add_argument("--skip-build", action="store_true", help="reuse existing gateway binary")
    parser.add_argument("--gateways", nargs="*", help="subset of gateways to run, e.g. pp plangate_real")
    parser.add_argument("--client-timeout", type=int, default=DEFAULT_CLIENT_TIMEOUT, help="timeout for one react_agent_client process")
    parser.add_argument("--client-log-live", action="store_true", help="tee client stdout/stderr to the terminal")
    parser.add_argument("--llm-preflight", dest="llm_preflight", action="store_true", help="run GLM preflight before backend/gateway startup")
    parser.add_argument("--skip-llm-preflight", dest="llm_preflight", action="store_false", help="skip the GLM preflight")
    parser.set_defaults(llm_preflight=True)
    args = parser.parse_args()

    load_project_dotenv(ROOT_DIR)
    configured_gateways = configure_gateways(args.concurrency)
    try:
        run_gateways = resolve_requested_gateways(args.gateways, configured_gateways)
    except ValueError as exc:
        print(f"[runner] {exc}")
        return 2

    print("\n" + "=" * 72)
    print("  Week 5 real-LLM smoke")
    print("=" * 72)
    print(f"  Sessions/Agents = {args.agents}")
    print(f"  Concurrency     = {args.concurrency}")
    print(f"  Max Steps       = {args.max_steps}")
    print(f"  Budget          = {BUDGET}")
    print(f"  Arrival Interval= {args.arrival_interval}")
    print(f"  Repeats         = {args.repeats}")
    print(f"  Gateways        = {', '.join(gateway.name for gateway in run_gateways)}")
    print(f"  Client Timeout  = {args.client_timeout}s")
    print(f"  Results Dir     = {get_results_dir(args.concurrency)}")

    if args.dry_run:
        run_experiment(args, run_gateways)
        return 0

    if args.llm_preflight:
        try:
            run_glm_preflight_checked()
        except RuntimeError as exc:
            print(f"  {exc}")
            return 2

    global GATEWAY_BINARY
    if args.skip_build:
        bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
        GATEWAY_BINARY = ROOT_DIR / bin_name
        if not GATEWAY_BINARY.exists():
            print(f"  gateway binary not found: {GATEWAY_BINARY}")
            return 2
        print(f"  Reusing gateway binary: {GATEWAY_BINARY}")
    else:
        try:
            GATEWAY_BINARY = build_gateway()
        except RuntimeError as exc:
            print(f"  {exc}")
            return 2

    try:
        start_backend()
    except RuntimeError as exc:
        print(f"  {exc}")
        return 2

    try:
        run_experiment(args, run_gateways)
    finally:
        stop_backend()
        print("\n  Backend stopped.")

    print("\n  Real-LLM smoke run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
