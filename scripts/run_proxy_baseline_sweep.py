#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import signal
import socket
import subprocess
import shutil
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent.parent
DAG_GEN = ROOT / "scripts" / "dag_load_generator.py"
SERVER_PY = ROOT / "mcp_server" / "server.py"

BACKEND_PROC = None

GATEWAY_MODE_MAP = {
    "ng": "ng",
    "srl": "srl",
    "sbac": "sbac",
    "envoy_approx": "envoy-approx",
    "kong_approx": "kong-approx",
    "plangate_full": "mcpdp",
    "plangate_real": "mcpdp-real",
}

# Per-gateway extra CLI flags passed to the gateway binary.
# Aligns plangate_full with the tuned params used in run_tput_latency_sweep.py.
GATEWAY_EXTRA_ARGS = {
    "plangate_full": [
        "--plangate-price-step",      "40",
        "--plangate-max-sessions",    "30",
        "--plangate-sunk-cost-alpha", "0.5",
    ],
    "plangate_real": [
        "--plangate-price-step",      "40",
        "--plangate-max-sessions",    "30",
        "--plangate-sunk-cost-alpha", "0.5",
    ],
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ping_gateway(url: str, timeout_s: float = 0.5) -> bool:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_gateway(url: str, max_wait_s: float = 20) -> bool:
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        if ping_gateway(url):
            return True
        time.sleep(0.2)
    return False


def preflight_vllm(base: str, model: str) -> None:
    req = Request(base.rstrip("/") + "/v1/models")
    with urlopen(req, timeout=3) as resp:
        if resp.status != 200:
            raise RuntimeError(f"vLLM preflight failed: HTTP {resp.status}")
        payload = json.loads(resp.read().decode("utf-8"))
    names = [x.get("id", "") for x in payload.get("data", []) if isinstance(x, dict)]
    if model not in names:
        raise RuntimeError(f"vLLM preflight failed: model '{model}' not in {names}")


def start_backend_mock() -> subprocess.Popen:
    """Start mcp_server in mock mode on port 8080."""
    global BACKEND_PROC
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, str(SERVER_PY),
        "--port", "8080",
        "--mode", "sterile",
        "--max-workers", "10",
    ]
    print("[proxy-sweep] starting backend (mock mode):")
    print("  cmd:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    # Wait for server to start and become responsive
    for i in range(60):
        time.sleep(0.5)
        if proc.poll() is not None:
            # Process crashed
            raise RuntimeError(f"Backend process exited immediately (code {proc.returncode})")
        # Try to ping backend
        try:
            req = Request("http://127.0.0.1:8080/", method="GET")
            with urlopen(req, timeout=1) as resp:
                if resp.status < 500:
                    print(f"[proxy-sweep] backend ready (pid={proc.pid})")
                    BACKEND_PROC = proc
                    return proc
        except Exception:
            pass
    raise RuntimeError(f"Backend startup timeout after 30s")


def start_backend_real_llm(vllm_base: str, vllm_model: str) -> subprocess.Popen:
    """Start mcp_server in real_llm mode on port 8080, pointing internally to vLLM."""
    global BACKEND_PROC
    # First verify vLLM is accessible and serving the expected model.
    preflight_vllm(vllm_base, vllm_model)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_API_BASE"] = vllm_base.rstrip("/") + "/v1"
    env["LLM_MODEL"] = vllm_model
    env["LLM_API_KEY"] = "EMPTY"
    cmd = [
        sys.executable, str(SERVER_PY),
        "--port", "8080",
        "--mode", "real_llm",
        "--max-workers", "10",
    ]
    print("[proxy-sweep] starting backend (real_llm mode):")
    print("  cmd:", " ".join(cmd))
    print(f"  LLM_API_BASE={env['LLM_API_BASE']}  LLM_MODEL={env['LLM_MODEL']}")
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    for _ in range(60):
        time.sleep(0.5)
        if proc.poll() is not None:
            raise RuntimeError(f"Backend (real_llm) exited immediately (code {proc.returncode})")
        try:
            req = Request("http://127.0.0.1:8080/", method="GET")
            with urlopen(req, timeout=1) as resp:
                if resp.status < 500:
                    print(f"[proxy-sweep] backend (real_llm) ready (pid={proc.pid})")
                    BACKEND_PROC = proc
                    return proc
        except Exception:
            pass
    raise RuntimeError("Backend (real_llm) startup timeout after 30s")


def stop_backend() -> None:
    """Gracefully stop backend process."""
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
    except Exception as e:
        print(f"[proxy-sweep] warning: backend cleanup failed: {e}")
        try:
            BACKEND_PROC.kill()
        except Exception:
            pass
    BACKEND_PROC = None


def p95(values):
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(0.95 * (len(vals) - 1))
    return float(vals[idx])


def summarize_run(raw_csv: Path, session_csv: Path, gateway: str, run_idx: int, conc: int, default_duration: float):
    states = []
    success_lat_s = []
    eff_total = 0.0
    with session_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            st = row.get("state", "")
            states.append(st)
            if st == "SUCCESS":
                eff_total += float(row.get("effective_goodput", 0) or 0)
                success_lat_s.append(float(row.get("total_latency_ms", 0) or 0) / 1000.0)

    ts = []
    cascade_steps = 0
    with raw_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                ts.append(float(row.get("timestamp", 0) or 0))
            except ValueError:
                pass
            if row.get("session_state") == "CASCADE_FAILED" and row.get("status") == "success":
                cascade_steps += 1

    elapsed = max(ts) - min(ts) if len(ts) >= 2 else default_duration
    elapsed = max(elapsed, 0.001)

    success = sum(1 for s in states if s == "SUCCESS")
    cascade = sum(1 for s in states if s == "CASCADE_FAILED")
    rej0 = sum(1 for s in states if s == "REJECTED_AT_STEP_0")
    partial = 0

    admitted = success + partial + cascade
    doomed = partial + cascade
    abd = (100.0 * doomed / admitted) if admitted > 0 else 0.0
    casc_adm = (100.0 * cascade / admitted) if admitted > 0 else 0.0

    return {
        "gateway": gateway,
        "run": run_idx,
        "concurrency": conc,
        "sessions": len(states),
        "success": success,
        "partial": partial,
        "all_rejected": rej0,
        "cascade_failed": cascade,
        "cascade_steps": cascade_steps,
        "abd_total": round(abd, 3),
        "cascade_admitted_pct": round(casc_adm, 3),
        "success_sessions_per_s": round(success / elapsed, 6),
        "effective_goodput_s": round(eff_total / elapsed, 6),
        "p95_success_s": round(p95(success_lat_s), 6),
    }


def _parse_run_conc_from_path(raw_csv: Path, result_dir: Path):
    rel = raw_csv.relative_to(result_dir)
    # Expected layout: <gateway>/run<idx>/conc<n>/steps.csv
    if len(rel.parts) != 4:
        raise ValueError(f"unexpected artifact path layout: {rel}")
    gw, run_part, conc_part, name = rel.parts
    if name != "steps.csv":
        raise ValueError(f"unexpected artifact filename: {rel}")
    run_m = re.fullmatch(r"run(\d+)", run_part)
    conc_m = re.fullmatch(r"conc(\d+)", conc_part)
    if run_m is None or conc_m is None:
        raise ValueError(f"unexpected run/conc folder naming: {rel}")
    return gw, int(run_m.group(1)), int(conc_m.group(1))


def rebuild_summary_from_artifacts(result_dir: Path, summary_path: Path, default_duration: float) -> int:
    rows = []
    raw_files = sorted(result_dir.glob("*/run*/conc*/steps.csv"))
    if not raw_files:
        raise RuntimeError(f"no artifact files found under {result_dir}")

    for raw_csv in raw_files:
        gw, run_idx, conc = _parse_run_conc_from_path(raw_csv, result_dir)
        sess_csv = raw_csv.parent / "steps_sessions.csv"
        if not sess_csv.exists():
            raise RuntimeError(f"missing session csv for {raw_csv}: {sess_csv}")
        row = summarize_run(raw_csv, sess_csv, gw, run_idx, conc, default_duration)
        rows.append(row)

    fields = [
        "gateway", "run", "concurrency", "sessions",
        "success", "partial", "all_rejected", "cascade_failed", "cascade_steps",
        "abd_total", "cascade_admitted_pct", "success_sessions_per_s",
        "effective_goodput_s", "p95_success_s",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Run Envoy/Kong proxy approximation baseline sweep")
    ap.add_argument("--backend", choices=["mock", "selfhosted-vllm"], default="mock")
    ap.add_argument("--output-dir", default=None,
        help="Result directory root. Defaults to results/exp_proxy_baselines/mock/ or "
             "results/exp_proxy_baselines/selfhosted_vllm_smoke/ based on --backend.")
    ap.add_argument("--vllm-base", default="http://127.0.0.1:9999",
        help="vLLM server base URL (used only for selfhosted-vllm mode preflight and LLM_API_BASE)")
    ap.add_argument("--vllm-model", default="qwen", help="served model name for preflight")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--conc", nargs="+", type=int, default=[20, 40, 80])
    ap.add_argument("--gateways", nargs="+", default=["ng", "srl", "sbac", "envoy_approx", "kong_approx", "plangate_full"])
    ap.add_argument("--sessions", type=int, default=300)
    ap.add_argument("--ps-ratio", type=float, default=0.5)
    ap.add_argument("--heavy-ratio", type=float, default=0.3)
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--force", action="store_true",
        help="Delete output-dir contents before running (prevents stale mixed runs).")
    ap.add_argument("--rebuild-summary", action="store_true",
        help="Rebuild proxy_baseline_summary.csv from existing steps.csv/steps_sessions.csv; no experiments are run.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.force and args.rebuild_summary:
        raise ValueError("--force and --rebuild-summary cannot be used together")

    # Gateway always connects to MCP server at 8080 — whether mock or real_llm mode.
    # The MCP server then internally routes to vLLM when in real_llm mode.
    backend_url = "http://127.0.0.1:8080"

    # Resolve output directory.
    if args.output_dir:
        result_dir = Path(args.output_dir)
    elif args.backend == "mock":
        result_dir = ROOT / "results" / "exp_proxy_baselines" / "mock"
    else:
        result_dir = ROOT / "results" / "exp_proxy_baselines" / "selfhosted_vllm_smoke"

    result_dir.mkdir(parents=True, exist_ok=True)
    summary_path = result_dir / "proxy_baseline_summary.csv"

    if args.rebuild_summary:
        rows = rebuild_summary_from_artifacts(result_dir, summary_path, args.duration)
        print(f"[proxy-sweep] rebuilt summary from artifacts: {summary_path} (rows={rows})")
        return

    if args.force and not args.dry_run:
        print(f"[proxy-sweep] --force set; deleting existing output dir: {result_dir}")
        if result_dir.exists():
            shutil.rmtree(result_dir)
        result_dir.mkdir(parents=True, exist_ok=True)
        summary_path = result_dir / "proxy_baseline_summary.csv"

    if summary_path.exists() and not args.force and not args.dry_run:
        print(
            "[proxy-sweep] ERROR: summary already exists and would be overwritten:\n"
            f"  {summary_path}\n"
            "Use one of:\n"
            "  1) --force (clear current output-dir and rerun)\n"
            "  2) --output-dir <new_dir> (recommended for debug reruns)\n"
            "  3) --rebuild-summary (rebuild summary from existing artifacts)"
        )
        sys.exit(2)

    matrix = []
    for gw in args.gateways:
        if gw not in GATEWAY_MODE_MAP:
            raise ValueError(f"unknown gateway {gw}")
        for c in args.conc:
            for r in range(1, args.repeats + 1):
                matrix.append((gw, c, r))

    print(f"[proxy-sweep] matrix size = {len(matrix)}")
    for gw, c, r in matrix:
        print(f"  - gateway={gw}, conc={c}, run={r}")
    if args.dry_run:
        return

    # Start backend according to mode.
    if args.backend == "mock":
        try:
            start_backend_mock()
        except Exception as e:
            print(f"[proxy-sweep] ERROR: failed to start backend: {e}")
            sys.exit(1)
    elif args.backend == "selfhosted-vllm":
        try:
            start_backend_real_llm(args.vllm_base, args.vllm_model)
        except Exception as e:
            print(f"[proxy-sweep] ERROR: failed to start real_llm backend: {e}")
            sys.exit(1)

    rows = []
    try:
        for gw, c, r in matrix:
            mode = GATEWAY_MODE_MAP[gw]
            port = free_port()
            target = f"http://127.0.0.1:{port}"
            run_dir = result_dir / gw / f"run{r}" / f"conc{c}"
            run_dir.mkdir(parents=True, exist_ok=True)
            raw_csv = run_dir / "steps.csv"
            sess_csv = run_dir / "steps_sessions.csv"

            cmd = [
                "go", "run", "./cmd/gateway",
                "--mode", mode,
                "--host", "127.0.0.1",
                "--port", str(port),
                "--backend", backend_url,
                "--proxy-global-qps", "65",
                "--proxy-global-burst", "400",
                "--proxy-max-conc", "55",
                "--proxy-route-qps", "35",
                "--proxy-route-burst", "100",
                "--proxy-route-max-conc", "20",
                "--kong-session-qps", "2",
                "--kong-session-burst", "5",
                "--kong-session-ttl", "300",
            ] + GATEWAY_EXTRA_ARGS.get(gw, [])

            print("[proxy-sweep] start gateway:", " ".join(cmd))
            gw_proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if not wait_gateway(target, max_wait_s=25):
                    raise RuntimeError(f"gateway failed to become ready: {target}")

                dag_cmd = [
                    sys.executable, str(DAG_GEN),
                    "--target", target,
                    "--sessions", str(args.sessions),
                    "--ps-ratio", str(args.ps_ratio),
                    "--heavy-ratio", str(args.heavy_ratio),
                    "--concurrency", str(c),
                    "--duration", str(args.duration),
                    "--budget", "500",
                    "--arrival-rate", "50",
                    "--output", str(raw_csv),
                ]
                print("[proxy-sweep] run loadgen:", " ".join(dag_cmd))
                subprocess.check_call(dag_cmd, cwd=str(ROOT))
                if not sess_csv.exists():
                    raise RuntimeError(f"missing session csv: {sess_csv}")

                row = summarize_run(raw_csv, sess_csv, gw, r, c, args.duration)
                rows.append(row)

            finally:
                try:
                    gw_proc.send_signal(signal.SIGINT)
                    gw_proc.wait(timeout=5)
                except Exception:
                    gw_proc.kill()

    finally:
        # Always clean up backend
        stop_backend()

    fields = [
        "gateway", "run", "concurrency", "sessions",
        "success", "partial", "all_rejected", "cascade_failed", "cascade_steps",
        "abd_total", "cascade_admitted_pct", "success_sessions_per_s",
        "effective_goodput_s", "p95_success_s",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    print(f"[proxy-sweep] wrote summary: {summary_path}")


if __name__ == "__main__":
    main()
