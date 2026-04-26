#!/usr/bin/env python3
"""
run_deepseek_n3.py — DeepSeek concurrency sweep N=3 (C7)
=========================================================
Runs the DeepSeek-V3 concurrency sweep at C=1,3,5 with N=3 repeats
for NG and PlanGate, upgrading Tab 6 from N=1 to N=3.

Usage:
  python scripts/run_deepseek_n3.py --dry-run
  python scripts/run_deepseek_n3.py --repeats 3
  python scripts/run_deepseek_n3.py --conc 3 --repeats 3   # focused on C=3 only
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
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "deepseek_n3")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_deepseek_n3")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_HOST = "127.0.0.1"
BASE_PORT = 9700

# DeepSeek API config
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"

# Load from .env if not set
if not DEEPSEEK_API_KEY:
    env_path = os.path.join(ROOT_DIR, ".env")
    if os.path.isfile(env_path):
        env_vars = {}
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"')
        # .env has DeepSeek as the last LLM_API_KEY definition
        DEEPSEEK_API_KEY = env_vars.get("LLM_API_KEY", "")
        DEEPSEEK_API_BASE = env_vars.get("LLM_API_BASE", DEEPSEEK_API_BASE)
        DEEPSEEK_MODEL = env_vars.get("LLM_MODEL", DEEPSEEK_MODEL)

# Experiment parameters
AGENTS = 50
MAX_STEPS = 8
BUDGET = 300
ARRIVAL_INTERVAL = 0.5  # seconds between agent starts
MAX_WORKERS = 2
CONC_LEVELS = [1, 3, 5]  # concurrency multiplier levels


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


GATEWAYS = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("plangate", "mcpdp-real", [
        "--plangate-price-step", "400",
        "--plangate-max-sessions", "10",
        "--plangate-sunk-cost-alpha", "0.5",
        "--plangate-session-cap-wait", "15",
        "--real-ratelimit-max", "60",
        "--real-latency-threshold", "5000",
    ]),
]

BACKEND_PROC = None


def find_gateway():
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    path = os.path.join(ROOT_DIR, bin_name)
    if os.path.isfile(path):
        return path
    print("  Building gateway...")
    result = subprocess.run(
        ["go", "build", "-o", path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Build failed: {result.stderr}")
    return path


def start_backend():
    global BACKEND_PROC
    stop_backend()
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "_backend.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_API_BASE"] = DEEPSEEK_API_BASE
    env["LLM_API_KEY"] = DEEPSEEK_API_KEY
    env["LLM_MODEL"] = DEEPSEEK_MODEL
    cmd = [
        sys.executable, SERVER_PY,
        "--port", "8080",
        "--mode", "real_llm",
        "--max-workers", str(MAX_WORKERS),
        "--queue-timeout", "4.0",
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
        raise RuntimeError(f"Backend failed; see {log_path}")
    print(f"  Backend started (workers={MAX_WORKERS})")


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


def start_gateway(binary, gw, port):
    cmd = [
        binary, "--mode", gw.mode,
        "--port", str(port), "--backend", BACKEND_URL,
        "--host", GATEWAY_HOST,
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
        raise RuntimeError(f"Gateway {gw.name} failed; see {log_path}")
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


def run_react_client(gateway_url, output_csv, gateway_mode, conc):
    cmd = [
        sys.executable, REACT_CLIENT,
        "--gateway", gateway_url,
        "--agents", str(AGENTS),
        "--concurrency", str(conc),
        "--max-steps", str(MAX_STEPS),
        "--budget", str(BUDGET),
        "--arrival-interval", str(ARRIVAL_INTERVAL),
        "--gateway-mode", f"{gateway_mode}_c{conc}",
        "--output", output_csv,
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["AGENT_LLM_BASE"] = DEEPSEEK_API_BASE
    env["AGENT_LLM_KEY"] = DEEPSEEK_API_KEY
    env["AGENT_LLM_MODEL"] = DEEPSEEK_MODEL
    env["LLM_API_BASE"] = DEEPSEEK_API_BASE
    env["LLM_API_KEY"] = DEEPSEEK_API_KEY
    env["LLM_MODEL"] = DEEPSEEK_MODEL
    result = subprocess.run(
        cmd, cwd=ROOT_DIR, capture_output=True,
        timeout=3600, env=env, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  Error: {(result.stderr or '')[:500]}")
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
        if "级联浪费步骤:" in line:
            try:
                stats["cascade_steps"] = int(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if "级联浪费 Agent:" in line:
            try:
                stats["cascade_agents"] = int(line.split(":")[-1].strip())
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
                stats["http_429"] = int(line.split("429 响应:")[1].strip().split("/")[0].strip())
            except (ValueError, IndexError):
                pass
    s = stats.get("success", 0)
    partial = stats.get("partial", 0)
    stats["success_rate"] = round(100 * s / AGENTS, 1) if AGENTS > 0 else 0
    admitted = s + partial
    stats["abd"] = round(100 * partial / admitted, 1) if admitted > 0 else 0.0
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="DeepSeek concurrency sweep N=3 (C7)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--conc", type=int, nargs="+", default=None,
                        help="Specific concurrency levels (default: 1,3,5)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conc_levels = args.conc if args.conc else CONC_LEVELS

    if not DEEPSEEK_API_KEY:
        print("ERROR: No DeepSeek API key found. Set DEEPSEEK_API_KEY or check .env")
        sys.exit(1)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    gateway_bin = find_gateway()

    print(f"\n{'='*65}")
    print(f"  DeepSeek Concurrency Sweep N={args.repeats} (C7)")
    print(f"  Concurrency levels: {conc_levels}")
    print(f"  Gateways: {[g.name for g in GATEWAYS]}")
    print(f"  Agents={AGENTS}, API key={'***' + DEEPSEEK_API_KEY[-4:]}")
    print(f"{'='*65}")

    total_runs = len(conc_levels) * len(GATEWAYS) * args.repeats
    print(f"  Total runs: {total_runs} (est. {total_runs * 15}-{total_runs * 30} min)")

    all_results = []

    if not args.dry_run:
        start_backend()

    try:
        port_counter = 0
        for conc in conc_levels:
            for gw in GATEWAYS:
                for run_idx in range(1, args.repeats + 1):
                    port_counter += 1
                    port = BASE_PORT + port_counter
                    tag = f"C={conc}/{gw.name}/run{run_idx}"

                    print(f"\n  [{tag}] port={port}")
                    if args.dry_run:
                        print(f"    [DRY-RUN]")
                        continue

                    run_dir = os.path.join(RESULTS_DIR, f"c{conc}", gw.name)
                    os.makedirs(run_dir, exist_ok=True)
                    csv_path = os.path.join(run_dir, f"run{run_idx}.csv")

                    proc = None
                    try:
                        proc = start_gateway(gateway_bin, gw, port)
                        stdout = run_react_client(
                            f"http://{GATEWAY_HOST}:{port}",
                            csv_path, gw.mode, conc)
                        stats = parse_stats(stdout)
                        stats["gateway"] = gw.name
                        stats["conc"] = conc
                        stats["run"] = run_idx
                        all_results.append(stats)

                        print(f"    Succ={stats.get('success','?')} "
                              f"({stats.get('success_rate','?')}%) "
                              f"Casc={stats.get('cascade_steps','?')} "
                              f"GP/s={stats.get('eff_gps','?')}")

                        with open(os.path.join(run_dir, f"stdout_run{run_idx}.txt"),
                                  "w", encoding="utf-8") as f:
                            f.write(stdout)
                    except Exception as e:
                        print(f"    [ERROR] {e}")
                        all_results.append({"gateway": gw.name, "conc": conc,
                                            "run": run_idx, "error": str(e)})
                    finally:
                        if proc:
                            stop_process(proc)
                        time.sleep(5)  # cooldown between runs (respect RPM)
    finally:
        if not args.dry_run:
            stop_backend()

    # Summary
    if all_results and not args.dry_run:
        summary_path = os.path.join(RESULTS_DIR, "deepseek_n3_summary.csv")
        keys = ["gateway", "conc", "run", "success", "partial",
                "all_rejected", "success_rate", "abd", "cascade_steps",
                "cascade_agents", "eff_gps", "p50_ms", "p95_ms", "http_429"]
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in all_results:
                w.writerow(r)
        print(f"\n  Summary: {summary_path}")

        # Aggregate table
        print(f"\n{'='*70}")
        print(f"  DeepSeek Sweep Results (N={args.repeats})")
        print(f"{'='*70}")
        from collections import defaultdict
        for conc in conc_levels:
            print(f"\n  C={conc}:")
            print(f"  {'Gateway':<15} {'Succ%':>8} {'Casc':>8} {'GP/s':>8}")
            print(f"  {'-'*40}")
            for gw in GATEWAYS:
                rows = [r for r in all_results
                        if r.get("gateway") == gw.name and r.get("conc") == conc
                        and "error" not in r]
                if not rows:
                    continue
                succs = [r.get("success_rate", 0) for r in rows]
                cascs = [r.get("cascade_steps", 0) for r in rows]
                gpss = [r.get("eff_gps", 0) for r in rows]
                n = len(rows)
                ms = sum(succs)/n
                mc = sum(cascs)/n
                mg = sum(gpss)/n
                if n > 1:
                    ss = math.sqrt(sum((v-ms)**2 for v in succs)/(n-1))
                    sc = math.sqrt(sum((v-mc)**2 for v in cascs)/(n-1))
                    sg = math.sqrt(sum((v-mg)**2 for v in gpss)/(n-1))
                    print(f"  {gw.name:<15} {ms:>5.1f}±{ss:.1f} {mc:>5.1f}±{sc:.1f} {mg:>5.2f}±{sg:.2f}")
                else:
                    print(f"  {gw.name:<15} {ms:>8.1f} {mc:>8.1f} {mg:>8.2f}")


if __name__ == "__main__":
    main()
