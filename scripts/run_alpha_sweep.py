#!/usr/bin/env python3
"""
run_alpha_sweep.py — α sensitivity mock sweep (C3)

Runs Exp1-like workload (200 sessions, 200 concurrency, N=3 repeats)
with PlanGate sunk_cost_alpha = {0.2, 0.5, 0.8}.
Also runs NG baseline for comparison.

Usage:
  python scripts/run_alpha_sweep.py
  python scripts/run_alpha_sweep.py --repeats 5
  python scripts/run_alpha_sweep.py --dry-run
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen, Request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_alpha_sweep")
MOCK_LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "mock")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_HOST = "127.0.0.1"
BASE_PORT = 9200

# Workload: Exp1-like but 200 sessions for speed
SESSIONS = 200
CONCURRENCY = 200
PS_RATIO = 1.0
BUDGET = 500
HEAVY_RATIO = 0.3
ARRIVAL_RATE = 50.0
DURATION = 60
MIN_STEPS = 3
MAX_STEPS = 7
STEP_TIMEOUT = 2.0
DEFAULT_REPEATS = 3

# α values to sweep
ALPHA_VALUES = [0.2, 0.5, 0.8]

# PlanGate base params (from TUNED_PARAMS)
PG_PRICE_STEP = 40
PG_MAX_SESSIONS = 30


def find_gateway_binary():
    """Find the gateway binary."""
    if sys.platform == "win32":
        path = os.path.join(ROOT_DIR, "gateway.exe")
    else:
        path = os.path.join(ROOT_DIR, "gateway")
    if os.path.isfile(path):
        return path
    # Try building
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)
    print(f"  Building gateway: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Gateway build failed: {result.stderr}")
    return bin_path


SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
BACKEND_PROC = None
BACKEND_MAX_WORKERS = 10


def start_backend():
    global BACKEND_PROC
    stop_backend()
    cmd = [
        sys.executable, SERVER_PY,
        "--port", "8080",
        "--max-workers", str(BACKEND_MAX_WORKERS),
        "--queue-timeout", "1.0",
        "--congestion-factor", "0.5",
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    os.makedirs(MOCK_LOG_DIR, exist_ok=True)
    log_path = os.path.join(MOCK_LOG_DIR, "_backend_alpha.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=os.path.dirname(SERVER_PY),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"Backend failed to start, check: {log_path}")
    print(f"  Backend started (pid={BACKEND_PROC.pid})")


def stop_backend():
    global BACKEND_PROC
    if BACKEND_PROC is None:
        return
    if BACKEND_PROC.poll() is not None:
        BACKEND_PROC = None
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(BACKEND_PROC.pid)],
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


def wait_for_gateway(port, proc, timeout=15):
    deadline = time.time() + timeout
    ping_body = json.dumps({"jsonrpc": "2.0", "id": "hc", "method": "ping"}).encode()
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            req = Request(
                f"http://{GATEWAY_HOST}:{port}",
                data=ping_body,
                headers={"Content-Type": "application/json"},
            )
            resp = urlopen(req, timeout=2)
            data = json.loads(resp.read())
            if data.get("jsonrpc") == "2.0":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_gateway(binary, mode, port, extra_args=None):
    cmd = [
        binary, "--mode", mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", GATEWAY_HOST,
    ]
    if extra_args:
        cmd.extend(extra_args)
    os.makedirs(MOCK_LOG_DIR, exist_ok=True)
    log_path = os.path.join(MOCK_LOG_DIR, f"_gw_alpha_{mode}_{port}.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    proc._log_file = log_file
    if not wait_for_gateway(port, proc):
        log_file.close()
        stop_gateway(proc)
        raise RuntimeError(f"Gateway startup timeout (port={port})")
    return proc


def stop_gateway(proc):
    lf = getattr(proc, "_log_file", None)
    if lf:
        try:
            lf.close()
        except Exception:
            pass
    if proc.poll() is not None:
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


def run_load_gen(target_url, output_csv):
    cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target", target_url,
        "--sessions", str(SESSIONS),
        "--ps-ratio", str(PS_RATIO),
        "--budget", str(BUDGET),
        "--heavy-ratio", str(HEAVY_RATIO),
        "--concurrency", str(CONCURRENCY),
        "--arrival-rate", str(ARRIVAL_RATE),
        "--duration", str(DURATION),
        "--min-steps", str(MIN_STEPS),
        "--max-steps", str(MAX_STEPS),
        "--step-timeout", str(STEP_TIMEOUT),
        "--output", output_csv,
    ]
    log_path = output_csv.replace(".csv", "_stdout.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    timeout_sec = DURATION + 180
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=SCRIPT_DIR,
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        try:
            retcode = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10)
            else:
                proc.kill()
            proc.wait(timeout=5)
            return {"error": "timeout"}
    stdout_text = ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            stdout_text = f.read()
    except Exception:
        pass
    return parse_stats(stdout_text)


def parse_stats(stdout):
    stats = {}
    for line in stdout.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├─" in line:
            try:
                stats["success"] = int(line.split("SUCCESS:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "REJECTED@S0:" in line:
            try:
                stats["rejected_s0"] = int(line.split("REJECTED@S0:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "CASCADE_FAIL:" in line:
            try:
                stats["cascade_failed"] = int(line.split("CASCADE_FAIL:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "Effective Goodput/s:" in line:
            try:
                stats["effective_gps"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "P50:" in line and "E2E" not in line:
            try:
                stats["p50_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "P95:" in line and "E2E" not in line:
            try:
                stats["p95_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "JFI_Steps:" in line:
            try:
                stats["jfi_steps"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
    return stats


def main():
    parser = argparse.ArgumentParser(description="α sensitivity mock sweep (C3)")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    gateway_bin = find_gateway_binary()
    print(f"  Gateway: {gateway_bin}")

    # Configs: NG baseline + PlanGate at each α
    configs = [
        {"name": "ng", "mode": "ng", "alpha": None, "extra_args": []},
    ]
    for alpha in ALPHA_VALUES:
        configs.append({
            "name": f"plangate_a{alpha}",
            "mode": "mcpdp",
            "alpha": alpha,
            "extra_args": [
                "--plangate-price-step", str(PG_PRICE_STEP),
                "--plangate-max-sessions", str(PG_MAX_SESSIONS),
                "--plangate-sunk-cost-alpha", str(alpha),
            ],
        })

    all_results = []

    if not args.dry_run:
        print("\n  Starting backend...")
        start_backend()

    try:
        port_counter = 0
        for cfg in configs:
            for run_idx in range(1, args.repeats + 1):
                port_counter += 1
                port = BASE_PORT + port_counter
                tag = f"{cfg['name']}/run{run_idx}"
                print(f"\n  [{tag}] port={port}")

                if args.dry_run:
                    print(f"    [DRY-RUN] {tag}")
                    continue

                csv_path = os.path.join(RESULTS_DIR, f"{cfg['name']}_run{run_idx}.csv")
                proc = None
                try:
                    proc = start_gateway(gateway_bin, cfg["mode"], port, cfg["extra_args"])
                    target = f"http://{GATEWAY_HOST}:{port}"
                    stats = run_load_gen(target, csv_path)
                    stats["gateway"] = cfg["name"]
                    stats["alpha"] = cfg["alpha"]
                    stats["run_idx"] = run_idx
                    all_results.append(stats)

                    succ = stats.get("success", "?")
                    casc = stats.get("cascade_failed", "?")
                    gps = stats.get("effective_gps", "?")
                    abd_pct = "?"
                    if isinstance(succ, int) and isinstance(casc, int):
                        total_admitted = succ + casc
                        abd_pct = f"{casc / total_admitted * 100:.1f}" if total_admitted > 0 else "0.0"
                    print(f"    Result: Succ={succ} Casc={casc} ABD={abd_pct}% GP/s={gps}")
                except Exception as e:
                    print(f"    [ERROR] {e}")
                    all_results.append({"gateway": cfg["name"], "alpha": cfg["alpha"],
                                        "run_idx": run_idx, "error": str(e)})
                finally:
                    if proc:
                        stop_gateway(proc)
                    time.sleep(3)
    finally:
        if not args.dry_run:
            stop_backend()

    # Save summary
    if all_results and not args.dry_run:
        summary_path = os.path.join(RESULTS_DIR, "alpha_sweep_summary.csv")
        fieldnames = ["gateway", "alpha", "run_idx", "success", "rejected_s0",
                      "cascade_failed", "effective_gps", "p50_ms", "p95_ms",
                      "jfi_steps", "error"]
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in all_results:
                writer.writerow(r)
        print(f"\n  Summary saved: {summary_path}")

        # Print aggregate table
        print(f"\n{'='*70}")
        print(f"  α Sensitivity Sweep Results (N={args.repeats})")
        print(f"{'='*70}")
        from collections import defaultdict
        agg = defaultdict(list)
        for r in all_results:
            if "error" not in r:
                agg[r["gateway"]].append(r)

        print(f"  {'Gateway':<20} {'α':>5} {'Succ':>8} {'Casc':>8} {'ABD%':>8} {'GP/s':>8}")
        print(f"  {'-'*58}")
        for gw_name in ["ng"] + [f"plangate_a{a}" for a in ALPHA_VALUES]:
            runs = agg.get(gw_name, [])
            if not runs:
                continue
            import statistics
            succs = [r.get("success", 0) for r in runs]
            cascs = [r.get("cascade_failed", 0) for r in runs]
            gpss = [r.get("effective_gps", 0) for r in runs]
            mean_s = statistics.mean(succs)
            mean_c = statistics.mean(cascs)
            mean_g = statistics.mean(gpss)
            total = mean_s + mean_c
            abd = mean_c / total * 100 if total > 0 else 0
            alpha_str = str(runs[0].get("alpha", "-"))
            print(f"  {gw_name:<20} {alpha_str:>5} {mean_s:>8.1f} {mean_c:>8.1f} {abd:>7.1f}% {mean_g:>8.1f}")


if __name__ == "__main__":
    main()
