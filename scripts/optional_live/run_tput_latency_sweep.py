#!/usr/bin/env python3
"""
run_tput_latency_sweep.py — Throughput-Latency sweep experiment (Exp13_TputLatency)

Sweeps offered load (concurrency = 10, 20, 30, 40, 60, 80) with a fixed mock
workload (P&S+ReAct 50/50, budget=500, heavy_ratio=0.3, 300 sessions, 5 repeats).
Measures per-gateway: effective goodput (GP/s), P50/P95/P99 of successful sessions
only, Rej0, ABD, and cascade waste.

Key design decisions:
  - P50/P95/P99 measure *successful* session latency only (step-0 rejections are
    excluded so PlanGate's proactive admission control is not artificially penalised
    by fast-fail rejects inflating the latency distribution).
  - "Effective goodput" is sessions with all steps completed / elapsed seconds.
  - The figure generated from this data is: P95 latency vs effective goodput (main
    claim), plus goodput vs offered-load and ABD vs offered-load as companion panels.

Usage:
  python scripts/run_tput_latency_sweep.py                  # full run, N=5
  python scripts/run_tput_latency_sweep.py --repeats 3      # quick validation
  python scripts/run_tput_latency_sweep.py --dry-run        # check config
  python scripts/run_tput_latency_sweep.py --conc 10 20 40  # custom concurrency list
"""

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.request import urlopen, Request

# ==============================
# Paths
# ==============================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_tput_latency")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "mock")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_HOST = "127.0.0.1"
BASE_PORT = 9400          # dedicated port range for this experiment

SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
GATEWAY_BINARY: Optional[str] = None
BACKEND_PROC: Optional[subprocess.Popen] = None

# ==============================
# Fixed workload parameters
# ==============================
SESSIONS = 300             # enough for stable statistics at low concurrency
PS_RATIO = 0.5             # 50% P&S + 50% ReAct
BUDGET = 500
HEAVY_RATIO = 0.3
ARRIVAL_RATE = 50.0        # high enough that concurrency is the binding constraint
DURATION = 90              # seconds; longer than alpha_sweep so low-conc runs finish
MIN_STEPS = 3
MAX_STEPS = 7
STEP_TIMEOUT = 2.0
BACKEND_MAX_WORKERS = 10

# Concurrency levels to sweep (offered load axis)
DEFAULT_CONC_LEVELS = [10, 20, 30, 40, 60, 80]
DEFAULT_REPEATS = 5

# ==============================
# Gateway configs
# ==============================
TUNED_PARAMS = {
    "sbac":          {"max_sessions": 150},
    "srl":           {"qps": 65.0, "burst": 400, "max_conc": 55},
    "plangate_full": {"price_step": 40, "max_sessions": 30, "sunk_cost_alpha": 0.5},
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


def get_gateways() -> List[GatewayConfig]:
    sb = TUNED_PARAMS["sbac"]
    srl = TUNED_PARAMS["srl"]
    pg = TUNED_PARAMS["plangate_full"]
    return [
        GatewayConfig("ng", "ng"),
        GatewayConfig("srl", "srl", [
            "--srl-qps",      str(srl["qps"]),
            "--srl-burst",    str(srl["burst"]),
            "--srl-max-conc", str(srl["max_conc"]),
        ]),
        GatewayConfig("sbac", "sbac", [
            "--sbac-max-sessions", str(sb["max_sessions"]),
        ]),
        GatewayConfig("plangate_full", "mcpdp", [
            "--plangate-price-step",       str(pg["price_step"]),
            "--plangate-max-sessions",     str(pg["max_sessions"]),
            "--plangate-sunk-cost-alpha",  str(pg["sunk_cost_alpha"]),
        ]),
    ]


# ==============================
# Process management (mirrors run_alpha_sweep.py)
# ==============================

def find_or_build_gateway() -> str:
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)
    if os.path.isfile(bin_path):
        return bin_path
    print(f"  Building gateway: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Gateway build failed:\n{result.stderr}")
    return bin_path


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
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "_backend_tput_latency.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=os.path.dirname(SERVER_PY),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"Backend failed to start, see {log_path}")
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


def wait_for_gateway(port: int, proc: subprocess.Popen, timeout: int = 15) -> bool:
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
            if json.loads(resp.read()).get("jsonrpc") == "2.0":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_gateway(gw: GatewayConfig, port: int) -> subprocess.Popen:
    global GATEWAY_BINARY
    if GATEWAY_BINARY is None:
        GATEWAY_BINARY = find_or_build_gateway()
    cmd = [
        GATEWAY_BINARY, "--mode", gw.mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", GATEWAY_HOST,
    ] + gw.extra_args
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"_gw_tl_{gw.name}_{port}.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    proc._log_file = log_file
    if not wait_for_gateway(port, proc):
        log_file.close()
        stop_gateway(proc)
        raise RuntimeError(f"Gateway {gw.name} startup timeout (port={port})")
    return proc


def stop_gateway(proc: subprocess.Popen):
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


# ==============================
# Load generator
# ==============================

def run_load_gen(target_url: str, concurrency: int, output_csv: str) -> dict:
    cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target",       target_url,
        "--sessions",     str(SESSIONS),
        "--ps-ratio",     str(PS_RATIO),
        "--budget",       str(BUDGET),
        "--heavy-ratio",  str(HEAVY_RATIO),
        "--concurrency",  str(concurrency),
        "--arrival-rate", str(ARRIVAL_RATE),
        "--duration",     str(DURATION),
        "--min-steps",    str(MIN_STEPS),
        "--max-steps",    str(MAX_STEPS),
        "--step-timeout", str(STEP_TIMEOUT),
        "--price-ttl",    "1.0",
        "--output",       output_csv,
    ]
    log_path = output_csv.replace(".csv", "_stdout.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    timeout_sec = max(DURATION * 3, 300)   # generous: wait 3× the duration limit
    retcode = -1
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                cmd, cwd=SCRIPT_DIR, stdout=lf, stderr=subprocess.STDOUT, env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
            try:
                retcode = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                print(f"    [TIMEOUT] load generator exceeded {timeout_sec}s")
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10)
                else:
                    proc.kill()
                proc.wait(timeout=5)
                return {"error": "timeout"}
    except Exception as e:
        print(f"    [ERROR] load gen popen failed: {e}")
        return {"error": str(e)}

    # Read log with UTF-8; fall back to replacement to avoid silent empty reads
    stdout_text = ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            stdout_text = f.read()
    except Exception as e:
        print(f"    [WARN] could not read log {log_path}: {e}")

    if retcode != 0:
        tail = stdout_text[-500:] if stdout_text else "(empty log)"
        print(f"    [WARN] load gen exit code={retcode}\n"
              f"    --- log tail ---\n{tail}\n    ----------------")

    stats = parse_stdout(stdout_text)

    # Warn when all key metrics are zero (likely crash or bad run)
    if not stats.get("success") and not stats.get("rejected_s0") and not stats.get("cascade_failed"):
        tail = stdout_text[-300:] if stdout_text else "(empty log)"
        print(f"    [WARN] all stats zero — possible load gen failure. Log tail:\n{tail}")

    return stats


def parse_stdout(text: str) -> dict:
    """Parse key metrics from dag_load_generator stdout."""
    stats = {}
    for line in text.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├─" in line:
            try: stats["success"] = int(line.split("SUCCESS:")[1].strip().split()[0])
            except (ValueError, IndexError): pass
        elif "REJECTED@S0:" in line:
            try: stats["rejected_s0"] = int(line.split("REJECTED@S0:")[1].strip().split()[0])
            except (ValueError, IndexError): pass
        elif "CASCADE_FAIL:" in line:
            try: stats["cascade_failed"] = int(line.split("CASCADE_FAIL:")[1].strip().split()[0])
            except (ValueError, IndexError): pass
        elif "PARTIAL:" in line:
            try: stats["partial"] = int(line.split("PARTIAL:")[1].strip().split()[0])
            except (ValueError, IndexError): pass
        elif "Effective Goodput/s:" in line:
            try: stats["effective_goodput_s"] = float(line.split(":")[-1].strip())
            except ValueError: pass
        elif "Raw Goodput/s:" in line:
            try: stats["raw_goodput_s"] = float(line.split(":")[-1].strip())
            except ValueError: pass
        elif "E2E_P50:" in line:
            try: stats["e2e_p50_ms"] = float(line.split(":")[-1].strip())
            except ValueError: pass
        elif "E2E_P95:" in line:
            try: stats["e2e_p95_ms"] = float(line.split(":")[-1].strip())
            except ValueError: pass
        elif "E2E_P99:" in line:
            try: stats["e2e_p99_ms"] = float(line.split(":")[-1].strip())
            except ValueError: pass
        elif "JFI_Steps:" in line:
            try: stats["jfi_steps"] = float(line.split(":")[-1].strip())
            except ValueError: pass
    return stats


# ==============================
# Summary CSV
# ==============================

SUMMARY_FIELDS = [
    "gateway", "concurrency", "run_idx",
    "success", "partial", "rejected_s0", "cascade_failed",
    "effective_goodput_s", "raw_goodput_s",
    "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
    "jfi_steps",
]


def write_summary_row(out_path: str, row: dict):
    file_exists = os.path.isfile(out_path)
    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ==============================
# Main sweep loop
# ==============================

def run_sweep(conc_levels: List[int], repeats: int, dry_run: bool = False):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    summary_csv = os.path.join(RESULTS_DIR, "tput_latency_summary.csv")
    # Clear existing summary to avoid double-counting on re-run
    if os.path.isfile(summary_csv):
        os.remove(summary_csv)

    gateways = get_gateways()

    print(f"\n{'='*70}")
    print(f"  Exp13_TputLatency: Throughput–Latency Sweep")
    print(f"  Gateways: {', '.join(g.name for g in gateways)}")
    print(f"  Concurrency levels: {conc_levels}")
    print(f"  Repeats: {repeats}  |  Sessions: {SESSIONS}  |  PS_ratio: {PS_RATIO}")
    print(f"  Backend workers: {BACKEND_MAX_WORKERS}  |  Heavy ratio: {HEAVY_RATIO}")
    print(f"{'='*70}\n")

    if dry_run:
        print("[DRY-RUN] Would run:")
        for conc in conc_levels:
            for gw in gateways:
                for r in range(1, repeats + 1):
                    print(f"  conc={conc}  gw={gw.name}  run={r}")
        return

    start_backend()
    port = BASE_PORT

    try:
        for conc in conc_levels:
            print(f"\n--- Concurrency = {conc} ---")
            for gw in gateways:
                for run_idx in range(1, repeats + 1):
                    conc_dir = os.path.join(RESULTS_DIR, f"conc{conc}", gw.name)
                    os.makedirs(conc_dir, exist_ok=True)
                    csv_path = os.path.join(conc_dir, f"run{run_idx}.csv")

                    print(f"  [{gw.name}] run {run_idx}/{repeats} ...", end="", flush=True)
                    gw_proc = None
                    try:
                        gw_proc = start_gateway(gw, port)
                        target = f"http://{GATEWAY_HOST}:{port}"
                        stats = run_load_gen(target, conc, csv_path)
                        row = {
                            "gateway":             gw.name,
                            "concurrency":         conc,
                            "run_idx":             run_idx,
                            "success":             stats.get("success", 0),
                            "partial":             stats.get("partial", 0),
                            "rejected_s0":         stats.get("rejected_s0", 0),
                            "cascade_failed":      stats.get("cascade_failed", 0),
                            "effective_goodput_s": stats.get("effective_goodput_s", 0.0),
                            "raw_goodput_s":       stats.get("raw_goodput_s", 0.0),
                            "e2e_p50_ms":          stats.get("e2e_p50_ms", 0.0),
                            "e2e_p95_ms":          stats.get("e2e_p95_ms", 0.0),
                            "e2e_p99_ms":          stats.get("e2e_p99_ms", 0.0),
                            "jfi_steps":           stats.get("jfi_steps", 0.0),
                        }
                        write_summary_row(summary_csv, row)
                        gps = row["effective_goodput_s"]
                        p95 = row["e2e_p95_ms"]
                        casc = row["cascade_failed"]
                        rej0 = row["rejected_s0"]
                        print(f" GP/s={gps:.1f}  P95={p95:.0f}ms  casc={casc}  rej0={rej0}")
                    except Exception as e:
                        print(f" ERROR: {e}")
                        row = {"gateway": gw.name, "concurrency": conc, "run_idx": run_idx,
                               "error": str(e)}
                        write_summary_row(summary_csv, row)
                    finally:
                        if gw_proc:
                            stop_gateway(gw_proc)
                        time.sleep(3)  # cool-down between trials
    finally:
        stop_backend()

    print(f"\nDone. Summary: {summary_csv}")
    aggregate_and_print(summary_csv)


def aggregate_and_print(summary_csv: str):
    """Compute per-(gateway, concurrency) mean±std and print a comparison table."""
    if not os.path.isfile(summary_csv):
        return

    rows = []
    with open(summary_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Group by (gateway, concurrency)
    from collections import defaultdict
    groups: Dict = defaultdict(list)
    for r in rows:
        try:
            groups[(r["gateway"], int(r["concurrency"]))].append(r)
        except (KeyError, ValueError):
            pass

    gw_order = ["ng", "srl", "sbac", "plangate_full"]
    conc_levels = sorted(set(k[1] for k in groups))

    def avg(vals): return sum(vals) / len(vals) if vals else 0.0
    def sd(vals): return statistics.stdev(vals) if len(vals) > 1 else 0.0

    print(f"\n{'='*90}")
    print(f"  THROUGHPUT–LATENCY SWEEP RESULTS")
    print(f"{'='*90}")
    print(f"  {'Gateway':20s} | {'Conc':>5} | {'GP/s':>7} | {'P95(s)':>8} | {'ABD%':>6} | {'Casc':>5} | {'Rej0':>5}")
    print(f"  {'-'*80}")

    for gw in gw_order:
        for conc in conc_levels:
            rs = groups.get((gw, conc), [])
            if not rs:
                continue
            gps_vals = [float(r.get("effective_goodput_s", 0)) for r in rs]
            p95_vals = [float(r.get("e2e_p95_ms", 0)) / 1000 for r in rs]
            succ_vals = [float(r.get("success", 0)) for r in rs]
            part_vals = [float(r.get("partial", 0)) for r in rs]
            casc_vals = [float(r.get("cascade_failed", 0)) for r in rs]
            rej0_vals = [float(r.get("rejected_s0", 0)) for r in rs]
            abd_vals = [
                100 * p / (s + p) if (s + p) > 0 else 0.0
                for s, p in zip(succ_vals, part_vals)
            ]
            print(f"  {gw:20s} | {conc:>5} | "
                  f"{avg(gps_vals):>5.1f}±{sd(gps_vals):.1f} | "
                  f"{avg(p95_vals):>6.1f}±{sd(p95_vals):.1f} | "
                  f"{avg(abd_vals):>5.1f} | "
                  f"{avg(casc_vals):>5.1f} | "
                  f"{avg(rej0_vals):>5.1f}")
        print(f"  {'-'*80}")


# ==============================
# Entry point
# ==============================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Throughput-Latency sweep experiment (Exp13_TputLatency)"
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                        help=f"Repeats per (gateway, concurrency) point (default: {DEFAULT_REPEATS})")
    parser.add_argument("--conc", type=int, nargs="+", default=DEFAULT_CONC_LEVELS,
                        metavar="C",
                        help=f"Concurrency levels to sweep (default: {DEFAULT_CONC_LEVELS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the planned run matrix without executing")
    args = parser.parse_args()

    run_sweep(
        conc_levels=sorted(set(args.conc)),
        repeats=args.repeats,
        dry_run=args.dry_run,
    )
