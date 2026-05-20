#!/usr/bin/env python3
"""
run_sbac30_experiment.py — Run SBAC with max_sessions=30 (cap-matched to PlanGate)
==================================================================================
Runs SBAC gateway with the same session cap as PlanGate (30) for fair comparison.
5 repeats × 200 sessions, same workload config as the formal experiment.

Usage:
  python scripts/run_sbac30_experiment.py
  python scripts/run_sbac30_experiment.py --repeats 3
"""

import argparse
import csv
import os
import statistics
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_sbac30")
MOCK_LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "mock")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
PORT = 9200

LOAD_CONFIG = {
    "sessions": 200,
    "concurrency": 200,
    "ps_ratio": 0.5,
    "budget": 500,
    "heavy_ratio": 0.3,
    "min_steps": 3,
    "max_steps": 7,
    "arrival_rate": 50.0,
    "duration": 60,
    "step_timeout": 2.0,
}

BACKEND_PROC = None


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
    print(f"  Build OK: {bin_path}")


def start_backend():
    global BACKEND_PROC
    stop_backend()
    os.makedirs(MOCK_LOG_DIR, exist_ok=True)
    log_path = os.path.join(MOCK_LOG_DIR, "_backend_sbac30.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, SERVER_PY,
        "--port", "8080",
        "--max-workers", "10",
        "--queue-timeout", "1.0",
        "--congestion-factor", "0.5",
    ]
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=os.path.dirname(SERVER_PY),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"Backend start failed, see: {log_path}")
    print(f"  Backend started (pid={BACKEND_PROC.pid})")


def stop_backend():
    global BACKEND_PROC
    if BACKEND_PROC is None:
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


def start_gateway():
    cmd = [
        GATEWAY_BINARY,
        "--mode", "sbac",
        "--port", str(PORT),
        "--backend", BACKEND_URL,
        "--host", "127.0.0.1",
        "--sbac-max-sessions", "30",
    ]
    log_path = os.path.join(MOCK_LOG_DIR, "_gw_sbac30.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"Gateway start failed, see: {log_path}")
    print(f"  SBAC-30 gateway started (pid={proc.pid}, port={PORT})")
    return proc


def run_load_gen(output_csv):
    cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target", f"http://127.0.0.1:{PORT}",
        "--sessions", str(LOAD_CONFIG["sessions"]),
        "--concurrency", str(LOAD_CONFIG["concurrency"]),
        "--ps-ratio", str(LOAD_CONFIG["ps_ratio"]),
        "--budget", str(LOAD_CONFIG["budget"]),
        "--heavy-ratio", str(LOAD_CONFIG["heavy_ratio"]),
        "--min-steps", str(LOAD_CONFIG["min_steps"]),
        "--max-steps", str(LOAD_CONFIG["max_steps"]),
        "--arrival-rate", str(LOAD_CONFIG["arrival_rate"]),
        "--duration", str(LOAD_CONFIG["duration"]),
        "--step-timeout", str(LOAD_CONFIG["step_timeout"]),
        "--output", output_csv,
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd, cwd=ROOT_DIR, capture_output=True,
        timeout=300, env=env, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  Load gen error: {result.stderr[:500] if result.stderr else 'N/A'}")
    return result.stdout or ""


def parse_stats(stdout_text):
    stats = {}
    for line in stdout_text.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├" in line:
            try:
                val = line.split("SUCCESS:")[1].strip().split()[0]
                stats["success"] = int(val)
            except (ValueError, IndexError):
                pass
        if "CASCADE_FAIL:" in line:
            try:
                val = line.split("CASCADE_FAIL:")[1].strip().split()[0]
                stats["cascade_failed"] = int(val)
            except (ValueError, IndexError):
                pass
        if "REJECTED@S0:" in line:
            try:
                val = line.split("REJECTED@S0:")[1].strip().split()[0]
                stats["rejected_s0"] = int(val)
            except (ValueError, IndexError):
                pass
        if "Effective Goodput/s:" in line:
            try:
                val = line.split(":")[-1].strip()
                stats["goodput"] = float(val)
            except (ValueError, IndexError):
                pass
        if "ABD_total:" in line:
            try:
                val = line.split("ABD_total:")[1].strip().split("%")[0]
                stats["abd_total"] = float(val)
            except (ValueError, IndexError):
                pass
        if "ABD_P&S:" in line:
            try:
                val = line.split("ABD_P&S:")[1].strip().split("%")[0]
                stats["abd_ps"] = float(val)
            except (ValueError, IndexError):
                pass
        if "ABD_ReAct:" in line:
            try:
                val = line.split("ABD_ReAct:")[1].strip().split("%")[0]
                stats["abd_react"] = float(val)
            except (ValueError, IndexError):
                pass

    total = stats.get("success", 0) + stats.get("cascade_failed", 0) + stats.get("rejected_s0", 0)
    if total > 0:
        stats["success_rate"] = round(100 * stats.get("success", 0) / total, 1)
    else:
        stats["success_rate"] = 0

    if "abd_total" not in stats:
        s = stats.get("success", 0)
        c = stats.get("cascade_failed", 0)
        admitted = s + c
        stats["abd_total"] = round(100 * c / admitted, 1) if admitted > 0 else 0

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    summary_path = os.path.join(RESULTS_DIR, "sbac30_summary.csv")

    print("=" * 60)
    print("  SBAC-30 Cap-Matched Experiment")
    print(f"  {args.repeats} repeats x 200 sessions, max_sessions=30")
    print("=" * 60)

    build_gateway()
    start_backend()

    rows = []
    try:
        for run_idx in range(1, args.repeats + 1):
            print(f"\n{'='*60}")
            print(f"  [sbac_30] Run {run_idx}/{args.repeats}")
            print(f"{'='*60}")

            run_dir = os.path.join(RESULTS_DIR, "sbac_30", f"run{run_idx}")
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")

            gw_proc = start_gateway()
            try:
                stdout = run_load_gen(output_csv)
                stats = parse_stats(stdout)

                row = {
                    "gateway": "sbac_30",
                    "run": run_idx,
                    "success": stats.get("success", 0),
                    "cascade_failed": stats.get("cascade_failed", 0),
                    "rejected_s0": stats.get("rejected_s0", 0),
                    "success_rate": stats.get("success_rate", 0),
                    "abd_total": stats.get("abd_total", 0),
                    "abd_ps": stats.get("abd_ps", 0),
                    "abd_react": stats.get("abd_react", 0),
                    "goodput": stats.get("goodput", 0),
                }
                rows.append(row)

                print(f"  Result: success={row['success']}, cascade={row['cascade_failed']}, rejected_s0={row['rejected_s0']}")
                print(f"          success_rate={row['success_rate']}%")
                print(f"          ABD_total={row['abd_total']:.1f}%  ABD_P&S={row['abd_ps']:.1f}%  ABD_ReAct={row['abd_react']:.1f}%")
                print(f"          goodput={row['goodput']:.1f} GP/s")
            finally:
                stop_process(gw_proc)
                time.sleep(1)
    finally:
        stop_backend()

    if rows:
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  Summary CSV: {summary_path}")

        sr = [r["success_rate"] for r in rows]
        abd = [r["abd_total"] for r in rows]
        abd_ps = [r["abd_ps"] for r in rows]
        abd_react = [r["abd_react"] for r in rows]
        gp = [r["goodput"] for r in rows]

        sr_mean, sr_std = statistics.mean(sr), (statistics.stdev(sr) if len(sr) > 1 else 0)
        abd_mean, abd_std = statistics.mean(abd), (statistics.stdev(abd) if len(abd) > 1 else 0)
        abd_ps_mean = statistics.mean(abd_ps)
        abd_react_mean = statistics.mean(abd_react)
        gp_mean, gp_std = statistics.mean(gp), (statistics.stdev(gp) if len(gp) > 1 else 0)

        print(f"\n{'='*60}")
        print(f"  SBAC-30 Results (mean ± std over {args.repeats} runs):")
        print(f"    Succ%:      {sr_mean:.1f} ± {sr_std:.1f}")
        print(f"    ABD_total:  {abd_mean:.1f} ± {abd_std:.1f}")
        print(f"    ABD_P&S:    {abd_ps_mean:.1f}")
        print(f"    ABD_ReAct:  {abd_react_mean:.1f}")
        print(f"    GP/s:       {gp_mean:.1f} ± {gp_std:.1f}")
        print(f"{'='*60}")

        # Comparison with PlanGate and SBAC-150 from formal experiment
        print(f"\n  === Comparison ===")
        print(f"  SBAC-30  (this):  ABD={abd_mean:.1f}%  GP/s={gp_mean:.1f}")
        print(f"  SBAC-150 (paper): ABD=56.0%  GP/s=32.3")
        print(f"  PG-noRes (paper): ABD=27.8%  GP/s=48.6")
        print(f"  PlanGate (paper): ABD=18.9%  GP/s=50.4")


if __name__ == "__main__":
    main()
