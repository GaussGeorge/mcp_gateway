#!/usr/bin/env python3
"""
run_selfhosted_c20.py — Self-Hosted vLLM High-Contention Experiment (C6)
========================================================================
Runs the self-hosted vLLM experiment at C=20 (higher contention than B2's C=10)
to test whether PlanGate's governance advantage grows under heavier load.

Uses 100 agents (2x B2), C=20, burst_size=25, and N=3 repeats.
Compares NG vs PlanGate.

Usage:
  python scripts/run_selfhosted_c20.py --dry-run
  python scripts/run_selfhosted_c20.py --repeats 3
"""

import argparse
import csv
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
REACT_CLIENT = os.path.join(SCRIPT_DIR, "react_agent_client.py")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "selfhosted_c20")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9600

# vLLM config
VLLM_BASE_URL = "http://127.0.0.1:9999/v1"
VLLM_MODEL = "qwen"
VLLM_API_KEY = "dummy"

# Agent brain
AGENT_LLM_BASE = "https://open.bigmodel.cn/api/paas/v4"
AGENT_LLM_KEY = "a22713062fa041e5a04b35b47ecbd7f9.yYrIROfseXak4pZA"
AGENT_LLM_MODEL = "glm-4-flash"

# C6 parameters: higher contention
AGENTS = 100          # 2x B2
CONCURRENCY = 20      # 2x B2
MAX_STEPS = 10
BUDGET = 800
BURST_SIZE = 25       # larger bursts
BURST_GAP = 5.0       # tighter gap
MAX_WORKERS = 8       # same backend capacity → more contention


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


GATEWAYS = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("plangate_real", "mcpdp-real", [
        "--plangate-price-step", "40",
        "--plangate-max-sessions", "12",
        "--plangate-sunk-cost-alpha", "0.7",
        "--plangate-session-cap-wait", "3",
        "--real-ratelimit-max", "9999",
        "--real-latency-threshold", "10000",
    ]),
]

BACKEND_PROC = None


def get_results_dir():
    return os.path.join(ROOT_DIR, "results",
                        f"exp_selfhosted_vllm_C{CONCURRENCY}_W{MAX_WORKERS}")


def find_gateway():
    global GATEWAY_BINARY
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    path = os.path.join(ROOT_DIR, bin_name)
    if os.path.isfile(path):
        GATEWAY_BINARY = path
        print(f"  Using existing gateway: {path}")
        return
    print(f"  Building gateway...")
    result = subprocess.run(
        ["go", "build", "-o", path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Build failed: {result.stderr}")
    GATEWAY_BINARY = path


def start_backend():
    global BACKEND_PROC
    stop_backend()
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "_backend.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_API_BASE"] = VLLM_BASE_URL
    env["LLM_API_KEY"] = VLLM_API_KEY
    env["LLM_MODEL"] = VLLM_MODEL
    cmd = [
        sys.executable, SERVER_PY,
        "--port", "8080",
        "--mode", "real_llm",
        "--max-workers", str(MAX_WORKERS),
        "--queue-timeout", "10.0",
        "--congestion-factor", "0.5",
    ]
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=os.path.dirname(SERVER_PY),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           if sys.platform == "win32" else 0),
        )
    time.sleep(4)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"Backend failed, see: {log_path}")
    print(f"  Backend started (pid={BACKEND_PROC.pid}, workers={MAX_WORKERS})")


def stop_backend():
    global BACKEND_PROC
    if BACKEND_PROC is None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID",
                            str(BACKEND_PROC.pid)],
                           capture_output=True, timeout=10)
        else:
            BACKEND_PROC.terminate()
            BACKEND_PROC.wait(timeout=5)
    except Exception:
        try:
            BACKEND_PROC.kill()
        except Exception:
            pass
    BACKEND_PROC = None


def start_gateway(gw, port):
    cmd = [
        GATEWAY_BINARY, "--mode", gw.mode,
        "--port", str(port), "--backend", BACKEND_URL, "--host", "127.0.0.1",
    ] + gw.extra_args
    log_path = os.path.join(LOG_DIR, f"_gw_{gw.name}.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR, stdout=lf, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           if sys.platform == "win32" else 0),
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"Gateway {gw.name} failed, see: {log_path}")
    print(f"  Gateway [{gw.name}] port={port}")
    return proc


def stop_process(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_react_client(gateway_url, output_csv, gateway_mode):
    cmd = [
        sys.executable, REACT_CLIENT,
        "--gateway", gateway_url,
        "--agents", str(AGENTS),
        "--concurrency", str(CONCURRENCY),
        "--max-steps", str(MAX_STEPS),
        "--budget", str(BUDGET),
        "--burst-size", str(BURST_SIZE),
        "--burst-gap", str(BURST_GAP),
        "--gateway-mode", gateway_mode,
        "--output", output_csv,
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["AGENT_LLM_BASE"] = AGENT_LLM_BASE
    env["AGENT_LLM_KEY"] = AGENT_LLM_KEY
    env["AGENT_LLM_MODEL"] = AGENT_LLM_MODEL
    result = subprocess.run(
        cmd, cwd=ROOT_DIR, capture_output=True,
        timeout=3600, env=env, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  Client error: {(result.stderr or '')[:500]}")
    return result.stdout or ""


def parse_stats(stdout_text):
    stats = {}
    for line in stdout_text.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├" in line:
            try:
                stats["success"] = int(line.split("SUCCESS:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "PARTIAL:" in line:
            try:
                stats["partial"] = int(line.split("PARTIAL:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "ALL_REJECTED:" in line:
            try:
                stats["all_rejected"] = int(line.split("ALL_REJECTED:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "级联浪费 Agent:" in line:
            try:
                stats["cascade_agents"] = int(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if "级联浪费步骤:" in line:
            try:
                stats["cascade_steps"] = int(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if "Effective GP/s:" in line:
            try:
                stats["eff_gps"] = float(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if "P50:" in line:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "P50:":
                        stats["p50_ms"] = float(parts[i+1].replace("ms", ""))
                    if p == "P95:":
                        stats["p95_ms"] = float(parts[i+1].replace("ms", ""))
            except (ValueError, IndexError):
                pass
        if "429 响应:" in line:
            try:
                stats["http_429_count"] = int(line.split("429 响应:")[1].strip().split("/")[0].strip())
            except (ValueError, IndexError):
                pass
        # Also capture step-0 rejections
        if "Rej@S0:" in line:
            try:
                stats["rej_s0"] = int(line.split("Rej@S0:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass

    s = stats.get("success", 0)
    partial = stats.get("partial", 0)
    admitted = s + partial
    stats["abd_total"] = round(100 * partial / admitted, 1) if admitted > 0 else 0.0
    stats["success_rate"] = round(100 * s / AGENTS, 1) if AGENTS > 0 else 0.0
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Self-hosted vLLM high-contention experiment (C6)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print(f"  Self-Hosted vLLM HIGH-CONTENTION Experiment (C6)")
    print(f"  Agents={AGENTS}, C={CONCURRENCY}, Burst={BURST_SIZE}x{BURST_GAP}s")
    print(f"  Backend workers={MAX_WORKERS}, Repeats={args.repeats}")
    print(f"{'='*65}")

    # Check vLLM
    try:
        import urllib.request
        req = urllib.request.Request(f"{VLLM_BASE_URL}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"  vLLM OK: {resp.read().decode()[:100]}")
    except Exception as e:
        print(f"  ERROR: vLLM not reachable at {VLLM_BASE_URL}: {e}")
        sys.exit(1)

    find_gateway()

    results_dir = get_results_dir()
    os.makedirs(results_dir, exist_ok=True)
    summary_rows = []

    start_backend()
    try:
        for gw in GATEWAYS:
            for run_idx in range(1, args.repeats + 1):
                port = BASE_PORT + GATEWAYS.index(gw)
                gateway_url = f"http://127.0.0.1:{port}"
                run_dir = os.path.join(results_dir, gw.name, f"run{run_idx}")
                os.makedirs(run_dir, exist_ok=True)
                output_csv = os.path.join(run_dir, "steps.csv")

                print(f"\n{'='*60}")
                print(f"  [{gw.name}] C={CONCURRENCY} Run {run_idx}/{args.repeats}")
                print(f"{'='*60}")

                if args.dry_run:
                    print(f"  [DRY-RUN]")
                    continue

                gw_proc = start_gateway(gw, port)
                try:
                    stdout = run_react_client(gateway_url, output_csv, gw.mode)
                    stats = parse_stats(stdout)
                    stats["gateway"] = gw.name
                    stats["run"] = run_idx
                    summary_rows.append(stats)

                    print(f"  Result: Succ={stats.get('success','?')} "
                          f"({stats.get('success_rate','?')}%) "
                          f"PARTIAL={stats.get('partial','?')} "
                          f"ABD={stats.get('abd_total','?')}% "
                          f"Cascade={stats.get('cascade_steps','?')}")

                    with open(os.path.join(run_dir, "stdout.txt"),
                              "w", encoding="utf-8") as f:
                        f.write(stdout)
                finally:
                    stop_process(gw_proc)
                    time.sleep(2)
    finally:
        stop_backend()

    # Summary
    if summary_rows:
        summary_path = os.path.join(results_dir, "selfhosted_c20_summary.csv")
        keys = ["gateway", "run", "success", "partial", "all_rejected",
                "abd_total", "success_rate", "cascade_agents", "cascade_steps",
                "eff_gps", "p50_ms", "p95_ms", "http_429_count", "rej_s0"]
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for row in summary_rows:
                w.writerow(row)
        print(f"\n  Summary: {summary_path}")

        print(f"\n{'='*65}")
        print(f"  AGGREGATED RESULTS (C={CONCURRENCY})")
        print(f"{'='*65}")
        for gw in GATEWAYS:
            rows = [r for r in summary_rows if r["gateway"] == gw.name]
            if not rows:
                continue
            n = len(rows)
            for metric in ["success", "partial", "abd_total",
                           "success_rate", "cascade_agents", "cascade_steps"]:
                vals = [r.get(metric, 0) for r in rows]
                avg = sum(vals) / n
                if n > 1:
                    std = math.sqrt(sum((v - avg)**2 for v in vals) / (n - 1))
                    print(f"  [{gw.name}] {metric}: {avg:.1f} ± {std:.1f}")
                else:
                    print(f"  [{gw.name}] {metric}: {avg:.1f}")


if __name__ == "__main__":
    main()
