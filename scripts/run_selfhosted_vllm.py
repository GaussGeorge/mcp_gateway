#!/usr/bin/env python3
"""
run_selfhosted_vllm.py — Self-Hosted vLLM Experiment (B2)
==========================================================
Validates PlanGate under self-hosted inference with hard capacity limits
(vLLM max-num-seqs=8), where the bottleneck is GPU inference capacity
rather than API rate limits.

Design:
  - Agent brain: GLM-4-Flash (reliable commercial API, same as bursty real-LLM)
  - Backend deepseek_llm tool: Qwen3.5-4B via local vLLM (self-hosted bottleneck)
  - Other backend tools (calculate, weather, etc.): local, no bottleneck
  This isolates the governance effect on self-hosted tool infrastructure
  while keeping agent reasoning reliable.

Usage:
  python scripts/run_selfhosted_vllm.py --dry-run
  python scripts/run_selfhosted_vllm.py --repeats 1
  python scripts/run_selfhosted_vllm.py --repeats 3       # formal
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
REACT_CLIENT = os.path.join(SCRIPT_DIR, "react_agent_client.py")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "selfhosted_vllm")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9500

# ══════════════════════════════════════════════════
# Self-hosted vLLM (for backend deepseek_llm tool)
# ══════════════════════════════════════════════════
VLLM_BASE_URL = "http://127.0.0.1:9999/v1"
VLLM_MODEL = "qwen"
VLLM_API_KEY = "dummy"

# ══════════════════════════════════════════════════
# Agent brain: GLM-4-Flash (reliable function calling)
# ══════════════════════════════════════════════════
AGENT_LLM_BASE = "https://open.bigmodel.cn/api/paas/v4"
AGENT_LLM_KEY = "a22713062fa041e5a04b35b47ecbd7f9.yYrIROfseXak4pZA"
AGENT_LLM_MODEL = "glm-4-flash"

# ══════════════════════════════════════════════════
# Experiment Parameters (bursty, matching bursty real-LLM)
# ══════════════════════════════════════════════════
AGENTS = 50
CONCURRENCY = 10
MAX_STEPS = 10
BUDGET = 800
BURST_SIZE = 15
BURST_GAP = 6.0

MAX_WORKERS = 8   # backend workers; vLLM max-num-seqs=8 is the real limit

TUNED_PARAMS = {
    "plangate": {"price_step": 40, "max_sessions": 12,
                 "sunk_cost_alpha": 0.7, "session_cap_wait": 3},
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


GATEWAYS = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("plangate_real", "mcpdp-real", [
        "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
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


def build_gateway():
    global GATEWAY_BINARY
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)
    print(f"  Compiling gateway: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Build failed: {result.stderr}")
    GATEWAY_BINARY = bin_path
    print(f"  Built: {bin_path}")


def start_backend():
    global BACKEND_PROC
    stop_backend()
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "_backend_selfhosted.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # Point backend's deepseek_llm tool to local vLLM
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
        raise RuntimeError(f"Backend failed to start, see: {log_path}")
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


def start_gateway(gw: GatewayConfig, port: int):
    cmd = [
        GATEWAY_BINARY,
        "--mode", gw.mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", "127.0.0.1",
    ] + gw.extra_args
    log_path = os.path.join(LOG_DIR, f"_gw_{gw.name}_selfhosted.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           if sys.platform == "win32" else 0),
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"Gateway {gw.name} failed, see: {log_path}")
    print(f"  Gateway [{gw.name}] started (pid={proc.pid}, port={port})")
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


def run_react_client(gateway_url: str, output_csv: str, gateway_mode: str):
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
    # Agent brain → GLM-4-Flash (reliable function calling)
    env["AGENT_LLM_BASE"] = AGENT_LLM_BASE
    env["AGENT_LLM_KEY"] = AGENT_LLM_KEY
    env["AGENT_LLM_MODEL"] = AGENT_LLM_MODEL

    result = subprocess.run(
        cmd, cwd=ROOT_DIR, capture_output=True,
        timeout=3600, env=env, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  Client error: {result.stderr[:500] if result.stderr else 'N/A'}")
    return result.stdout or ""


def parse_stats(stdout_text: str) -> dict:
    stats = {}
    for line in stdout_text.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├" in line:
            try:
                stats["success"] = int(
                    line.split("SUCCESS:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "PARTIAL:" in line:
            try:
                stats["partial"] = int(
                    line.split("PARTIAL:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "ALL_REJECTED:" in line:
            try:
                stats["all_rejected"] = int(
                    line.split("ALL_REJECTED:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "ERROR:" in line and "└" in line:
            try:
                stats["error"] = int(
                    line.split("ERROR:")[1].strip().split()[0])
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
                        stats["p50_ms"] = float(
                            parts[i + 1].replace("ms", ""))
                    if p == "P95:":
                        stats["p95_ms"] = float(
                            parts[i + 1].replace("ms", ""))
            except (ValueError, IndexError):
                pass
        if "429 响应:" in line:
            try:
                parts = line.split("429 响应:")[1].strip().split("/")
                stats["http_429_count"] = int(parts[0].strip())
            except (ValueError, IndexError):
                pass

    s = stats.get("success", 0)
    partial = stats.get("partial", 0)
    admitted = s + partial
    stats["abd_total"] = (round(100 * partial / admitted, 1)
                          if admitted > 0 else 0.0)
    stats["success_rate"] = (round(100 * s / AGENTS, 1)
                             if AGENTS > 0 else 0.0)
    return stats


def run_experiment(repeats: int, dry_run: bool = False):
    results_dir = get_results_dir()
    os.makedirs(results_dir, exist_ok=True)
    summary_path = os.path.join(results_dir, "selfhosted_summary.csv")
    summary_rows = []

    for gw in GATEWAYS:
        for run_idx in range(1, repeats + 1):
            port = BASE_PORT + GATEWAYS.index(gw)
            gateway_url = f"http://127.0.0.1:{port}"
            run_dir = os.path.join(results_dir, gw.name, f"run{run_idx}")
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")

            print(f"\n{'=' * 65}")
            print(f"  [{gw.name}] Self-Hosted vLLM Run {run_idx}/{repeats}")
            print(f"  ({AGENTS} agents, C={CONCURRENCY}, burst={BURST_SIZE}"
                  f"x{BURST_GAP}s, workers={MAX_WORKERS})")
            print(f"  Brain: {AGENT_LLM_MODEL}  Tool LLM: {VLLM_MODEL} @ vLLM")
            print(f"{'=' * 65}")

            if dry_run:
                print(f"  [DRY-RUN] gateway: {gw.mode}, port: {port}")
                continue

            gw_proc = start_gateway(gw, port)
            try:
                stdout = run_react_client(gateway_url, output_csv, gw.mode)
                stats = parse_stats(stdout)
                stats["gateway"] = gw.name
                stats["run"] = run_idx
                summary_rows.append(stats)

                print(f"\n  Results [{gw.name}] run {run_idx}:")
                print(f"    Success: {stats.get('success', '?')} "
                      f"({stats.get('success_rate', '?')}%)")
                print(f"    PARTIAL: {stats.get('partial', '?')}")
                print(f"    ABD: {stats.get('abd_total', '?')}%")
                print(f"    Cascade agents: {stats.get('cascade_agents', '?')}")
                print(f"    P50: {stats.get('p50_ms', '?')} ms  "
                      f"P95: {stats.get('p95_ms', '?')} ms")
                print(f"    429 count: {stats.get('http_429_count', '?')}")

                # Save per-run stdout
                with open(os.path.join(run_dir, "stdout.txt"),
                           "w", encoding="utf-8") as f:
                    f.write(stdout)
            finally:
                stop_process(gw_proc)
                time.sleep(2)

    # Write summary CSV
    if summary_rows:
        keys = ["gateway", "run", "success", "partial", "all_rejected",
                "error", "abd_total", "success_rate", "cascade_agents",
                "cascade_steps", "eff_gps", "p50_ms", "p95_ms",
                "http_429_count"]
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for row in summary_rows:
                w.writerow(row)
        print(f"\n  Summary written: {summary_path}")

        # Print aggregated per-gateway statistics
        print(f"\n{'=' * 65}")
        print("  AGGREGATED RESULTS (Self-Hosted vLLM)")
        print(f"{'=' * 65}")
        for gw in GATEWAYS:
            gw_rows = [r for r in summary_rows if r["gateway"] == gw.name]
            if not gw_rows:
                continue
            n = len(gw_rows)
            for metric in ["success", "partial", "abd_total",
                           "success_rate", "cascade_agents"]:
                vals = [r.get(metric, 0) for r in gw_rows]
                avg = sum(vals) / n if n else 0
                if n > 1:
                    import math
                    std = math.sqrt(sum((v - avg) ** 2 for v in vals) / (n - 1))
                    print(f"  [{gw.name}] {metric}: {avg:.1f} ± {std:.1f}")
                else:
                    print(f"  [{gw.name}] {metric}: {avg:.1f}")


def main():
    parser = argparse.ArgumentParser(
        description="Self-hosted vLLM experiment for PlanGate")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("\n" + "=" * 65)
    print("  Self-Hosted vLLM Experiment (B2)")
    print(f"  Agent brain: {AGENT_LLM_MODEL} @ {AGENT_LLM_BASE[:40]}...")
    print(f"  Backend tool LLM: {VLLM_MODEL} @ {VLLM_BASE_URL}")
    print(f"  Workers: {MAX_WORKERS}, Concurrency: {CONCURRENCY}")
    print(f"  Repeats: {args.repeats}")
    print("=" * 65)

    # Verify vLLM is reachable
    try:
        import urllib.request
        req = urllib.request.Request(f"{VLLM_BASE_URL}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"  vLLM OK: {resp.read().decode()[:100]}")
    except Exception as e:
        print(f"  ERROR: vLLM not reachable at {VLLM_BASE_URL}: {e}")
        sys.exit(1)

    build_gateway()
    start_backend()
    try:
        run_experiment(args.repeats, dry_run=args.dry_run)
    finally:
        stop_backend()

    print("\n  Experiment complete.")


if __name__ == "__main__":
    main()
