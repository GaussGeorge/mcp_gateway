#!/usr/bin/env python3
"""
run_multigateway_shared_state.py — 双节点 / 多网关共享状态实验

验证 PlanGate 的 session commitment 在多 gateway 部署下是否依赖单进程内存。

四种路由模式：
  single        — 单网关 (GW-A), inmemory state, routing=single
  local_random  — 双网关, 各自 inmemory state, routing=random (随机路由)
  local_sticky  — 双网关, 各自 inmemory state, routing=sticky (会话亲和)
  shared_random — 双网关, Redis 共享 state, routing=random (验证共享状态)

输出：
  results/exp_multigateway_shared_state/{mode}/C{conc}/run{n}/steps.csv
  results/exp_multigateway_shared_state/multigateway_summary.csv

Usage:
  python scripts/run_multigateway_shared_state.py                         # full run
  python scripts/run_multigateway_shared_state.py --dry-run               # 配置预检
  python scripts/run_multigateway_shared_state.py --modes single local_random
  python scripts/run_multigateway_shared_state.py --concurrency 20 40 --repeats 2
  python scripts/run_multigateway_shared_state.py --modes shared_random --redis-addr 127.0.0.1:6379
  python scripts/run_multigateway_shared_state.py --modes shared_random --commitment-token-secret test-shared-secret
"""

import argparse
import csv
import json
import os
import secrets
import sys
import time
import subprocess
from typing import Dict, List, Optional
from urllib.request import urlopen, Request

# ==============================
# Paths
# ==============================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_multigateway_shared_state")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "multigateway")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_HOST = "127.0.0.1"

# GW-A = 9601, GW-B = 9602
GW_A_PORT = 9601
GW_B_PORT = 9602

# ==============================
# Fixed workload parameters
# ==============================
SESSIONS = 300
PS_RATIO = 1.0             # Phase 1: P&S-only; ReAct is Phase 2 future work
BUDGET = 500
HEAVY_RATIO = 0.3
ARRIVAL_RATE = 50.0        # 高速注入，让并发成为瓶颈
DURATION = 0               # 无时间上限，让所有 session 跑完
MIN_STEPS = 3
MAX_STEPS = 7
STEP_TIMEOUT = 30.0
BACKEND_MAX_WORKERS = 10

PG_PRICE_STEP = 40
PG_MAX_SESSIONS = 30
PG_SUNK_COST_ALPHA = 0.5

DEFAULT_MODES = ["single", "local_random", "local_sticky", "shared_random"]
DEFAULT_CONC_LEVELS = [20, 40, 60]
DEFAULT_REPEATS = 5
DEFAULT_REDIS_ADDR = "127.0.0.1:6379"
DEFAULT_COMMITMENT_TOKEN_MODE = "optional"

# ==============================
# Mode → routing & gateway config
# ==============================
#  mode_name → {n_gateways, routing, use_redis}
MODE_META: Dict[str, dict] = {
    "single":       {"n_gateways": 1, "routing": "single",  "use_redis": False},
    "local_random": {"n_gateways": 2, "routing": "random",  "use_redis": False},
    "local_sticky": {"n_gateways": 2, "routing": "sticky",  "use_redis": False},
    "shared_random":{"n_gateways": 2, "routing": "random",  "use_redis": True},
}

# ==============================
# Global process handles
# ==============================
BACKEND_PROC: Optional[subprocess.Popen] = None
GATEWAY_BINARY: Optional[str] = None


# ============================================================
# Gateway binary
# ============================================================

def find_or_build_gateway() -> str:
    global GATEWAY_BINARY
    if GATEWAY_BINARY:
        return GATEWAY_BINARY
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)
    print(f"  Building gateway: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Gateway build failed:\n{result.stderr}")
    GATEWAY_BINARY = bin_path
    return bin_path


# ============================================================
# Backend
# ============================================================

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
    log_path = os.path.join(LOG_DIR, "_backend.log")
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
    _kill_proc(BACKEND_PROC)
    BACKEND_PROC = None


def _kill_proc(proc: subprocess.Popen):
    """Platform-aware process termination."""
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
    # Close attached log file if any
    lf = getattr(proc, "_log_file", None)
    if lf:
        try:
            lf.close()
        except Exception:
            pass


# ============================================================
# Gateway start / stop
# ============================================================

def wait_for_gateway(port: int, proc: subprocess.Popen, timeout: int = 20) -> bool:
    """Poll until gateway responds to a ping or the process dies."""
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


def start_plangate_gateway(
    port: int,
    node_id: str,
    use_redis: bool,
    redis_addr: str,
    log_tag: str,
    commitment_token_mode: str = DEFAULT_COMMITMENT_TOKEN_MODE,
    commitment_token_secret: str = "",
) -> subprocess.Popen:
    binary = find_or_build_gateway()
    cmd = [
        binary,
        "--mode",   "mcpdp",
        "--port",   str(port),
        "--backend", BACKEND_URL,
        "--host",   GATEWAY_HOST,
        "--plangate-price-step",      str(PG_PRICE_STEP),
        "--plangate-max-sessions",    str(PG_MAX_SESSIONS),
        "--plangate-sunk-cost-alpha", str(PG_SUNK_COST_ALPHA),
        "--node-id", node_id,
        "--plangate-state-store", "redis" if use_redis else "inmemory",
    ]
    if use_redis:
        cmd += ["--plangate-redis-addr", redis_addr]
    cmd += ["--commitment-token-mode", commitment_token_mode]
    if commitment_token_mode != "off" and commitment_token_secret:
        cmd += ["--commitment-token-secret", commitment_token_secret]

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"_gw_{log_tag}_{port}.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    proc._log_file = log_file
    if not wait_for_gateway(port, proc):
        _kill_proc(proc)
        raise RuntimeError(
            f"Gateway {node_id} startup timeout (port={port}). See {log_path}"
        )
    print(f"    GW [{node_id}] started — port={port}, redis={use_redis}")
    return proc


# ============================================================
# Redis cleanup
# ============================================================

def cleanup_redis_keys(redis_addr: str):
    """Clean up pg:* keys from Redis before running an experiment."""
    host, port_str = (redis_addr + ":6379").split(":")[:2]
    port = int(port_str)
    try:
        # Try using redis-cli first

        # Try via redis-cli
        result = subprocess.run(
            ["redis-cli", "-p", port_str, "KEYS", "pg:*"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            keys = result.stdout.strip().split("\n")
            if keys and keys[0]:  # has keys
                for key in keys:
                    subprocess.run(
                        ["redis-cli", "-p", port_str, "DEL", key],
                        capture_output=True, timeout=5
                    )
                print(f"    [Redis] Cleaned {len(keys)} keys from {redis_addr}")
                return
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: try via raw socket/RESP
    try:
        import socket

        def _resp_cmd(*parts: str) -> bytes:
            out = f"*{len(parts)}\r\n".encode("utf-8")
            for p in parts:
                b = p.encode("utf-8")
                out += f"${len(b)}\r\n".encode("utf-8") + b + b"\r\n"
            return out

        def _read_line(sock) -> bytes:
            buf = b""
            while not buf.endswith(b"\r\n"):
                ch = sock.recv(1)
                if not ch:
                    break
                buf += ch
            return buf

        def _read_resp(sock):
            head = _read_line(sock)
            if not head:
                return None
            t = head[:1]
            payload = head[1:-2]
            if t == b"+":
                return payload.decode("utf-8", errors="replace")
            if t == b":" or t == b"-":
                return payload.decode("utf-8", errors="replace")
            if t == b"$":
                n = int(payload)
                if n < 0:
                    return None
                data = b""
                while len(data) < n+2:
                    chunk = sock.recv(n + 2 - len(data))
                    if not chunk:
                        break
                    data += chunk
                return data[:n].decode("utf-8", errors="replace")
            if t == b"*":
                count = int(payload)
                if count < 0:
                    return None
                arr = []
                for _ in range(count):
                    arr.append(_read_resp(sock))
                return arr
            return None

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((host, port))

        sock.sendall(_resp_cmd("KEYS", "pg:*"))
        keys = _read_resp(sock)
        if isinstance(keys, list) and keys:
            sock.sendall(_resp_cmd("DEL", *keys))
            deleted = _read_resp(sock)
            print(f"    [Redis] Cleaned {deleted} keys from {redis_addr} (RESP)")
        else:
            print(f"    [Redis] No pg:* keys to clean in {redis_addr}")
        sock.close()
    except Exception as e:
        print(f"    [WARN] Redis cleanup failed: {e}")


# ============================================================
# Load generator
# ============================================================

def run_load_gen(
    targets: List[str],
    routing: str,
    concurrency: int,
    output_csv: str,
) -> dict:
    cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target",       targets[0],       # 单目标时的 fallback
        "--sessions",     str(SESSIONS),
        "--ps-ratio",     str(PS_RATIO),
        "--budget",       str(BUDGET),
        "--heavy-ratio",  str(HEAVY_RATIO),
        "--concurrency",  str(concurrency),
        "--arrival-rate", str(ARRIVAL_RATE),
        "--min-steps",    str(MIN_STEPS),
        "--max-steps",    str(MAX_STEPS),
        "--step-timeout", str(STEP_TIMEOUT),
        "--price-ttl",    "1.0",
        "--routing",      routing,
        "--output",       output_csv,
    ]
    if len(targets) > 1:
        cmd += ["--targets"] + targets

    log_path = output_csv.replace(".csv", "_stdout.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    timeout_sec = max(600, SESSIONS * 3)   # 保守超时

    retcode = -1
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                cmd, cwd=SCRIPT_DIR,
                stdout=lf, stderr=subprocess.STDOUT, env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
            try:
                retcode = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                print(f"    [TIMEOUT] load generator 超时 {timeout_sec}s")
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10)
                else:
                    proc.kill()
                proc.wait(timeout=5)
                return {"error": "timeout"}
    except Exception as e:
        print(f"    [ERROR] load gen 启动失败: {e}")
        return {"error": str(e)}

    stdout_text = ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            stdout_text = f.read()
    except Exception as e:
        print(f"    [WARN] 无法读取日志 {log_path}: {e}")

    if retcode != 0:
        tail = stdout_text[-500:] if stdout_text else "(empty log)"
        print(f"    [WARN] load gen 退出码={retcode}  日志尾部:\n{tail}")

    stats = parse_stdout(stdout_text)
    if not stats.get("success") and not stats.get("rejected_s0") and not stats.get("cascade_failed"):
        tail = stdout_text[-300:] if stdout_text else "(empty log)"
        print(f"    [WARN] 所有指标为零，可能崩溃。日志尾部:\n{tail}")

    return stats


def parse_stdout(text: str) -> dict:
    """从 dag_load_generator 的标准输出中解析关键指标。"""
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
        elif "state_miss 步骤:" in line:
            try: stats["state_miss_count"] = int(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError): pass
        elif "dup_admission 步骤:" in line:
            try: stats["dup_admission_count"] = int(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError): pass
        elif "跨节点会话数:" in line:
            try: stats["cross_node_sessions"] = int(line.split(":")[1].strip().split()[0])
            except (ValueError, IndexError): pass
    return stats


# ============================================================
# Summary CSV
# ============================================================

SUMMARY_FIELDS = [
    "mode", "concurrency", "run_idx",
    "n_gateways", "routing", "use_redis",
    "success", "rejected_s0", "cascade_failed",
    "effective_goodput_s", "raw_goodput_s",
    "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
    "state_miss_count", "dup_admission_count", "cross_node_sessions",
]


def write_summary_row(out_path: str, row: dict):
    file_exists = os.path.isfile(out_path)
    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ============================================================
# Main sweep
# ============================================================

def run_sweep(
    modes: List[str],
    conc_levels: List[int],
    repeats: int,
    redis_addr: str,
    results_dir: str,
    commitment_token_mode: str,
    commitment_token_secret: str,
    dry_run: bool = False,
):
    os.makedirs(results_dir, exist_ok=True)
    summary_csv = os.path.join(results_dir, "multigateway_summary.csv")

    print(f"\n{'='*70}")
    print(f"  多网关共享状态实验")
    print(f"  模式:         {modes}")
    print(f"  并发级:       {conc_levels}")
    print(f"  重复次数:     {repeats}")
    print(f"  Sessions:     {SESSIONS}  PS_ratio={PS_RATIO}")
    print(f"  Budget:       {BUDGET}  Heavy={HEAVY_RATIO}")
    print(f"  PG_MaxSess:   {PG_MAX_SESSIONS}  PG_PriceStep={PG_PRICE_STEP}")
    print(f"  Redis:        {redis_addr}")
    print(f"  Commitment:   mode={commitment_token_mode}  shared_secret={'yes' if commitment_token_secret else 'no'}")
    print(f"{'='*70}\n")

    if dry_run:
        print("[DRY-RUN] 将会运行:")
        for mode in modes:
            meta = MODE_META[mode]
            for conc in conc_levels:
                for r in range(1, repeats + 1):
                    print(f"  mode={mode}  conc={conc}  run={r}  "
                          f"gateways={meta['n_gateways']}  routing={meta['routing']}  "
                          f"redis={meta['use_redis']}  commitment={commitment_token_mode}  "
                          f"secret={'yes' if commitment_token_secret else 'no'}")
        return

    start_backend()

    try:
        for mode in modes:
            meta = MODE_META[mode]
            n_gw = meta["n_gateways"]
            routing = meta["routing"]
            use_redis = meta["use_redis"]

            # 检查 Redis 可达性（shared_random 模式需要 Redis）
            if use_redis:
                host, port_str = (redis_addr + ":6379").split(":")[:2]
                import socket
                try:
                    s = socket.create_connection((host, int(port_str)), timeout=2)
                    s.close()
                    print(f"  [Redis] {redis_addr} reachable")
                except Exception as e:
                    print(f"  [WARN] Redis {redis_addr} 不可达: {e}")
                    print(f"  [WARN] 跳过 {mode} 模式")
                    continue

            print(f"\n{'-'*60}")
            print(f"  模式: {mode}  (n_gw={n_gw}, routing={routing}, redis={use_redis})")

            for conc in conc_levels:
                print(f"\n  --- 并发 = {conc} ---")
                for run_idx in range(1, repeats + 1):
                    out_dir = os.path.join(results_dir, mode, f"C{conc}", f"run{run_idx}")
                    os.makedirs(out_dir, exist_ok=True)
                    csv_path = os.path.join(out_dir, "steps.csv")

                    print(f"  [{mode}] run {run_idx}/{repeats} ...", end="", flush=True)

                    gw_procs = []
                    try:
                        # Redis cleanup before starting
                        if use_redis:
                            cleanup_redis_keys(redis_addr)

                        # --- 启动网关 ---
                        gw_a = start_plangate_gateway(
                            GW_A_PORT, f"gw-a:{GW_A_PORT}", use_redis, redis_addr,
                            f"{mode}_run{run_idx}_a",
                            commitment_token_mode, commitment_token_secret,
                        )
                        gw_procs.append(gw_a)

                        targets = [f"http://{GATEWAY_HOST}:{GW_A_PORT}"]
                        if n_gw >= 2:
                            gw_b = start_plangate_gateway(
                                GW_B_PORT, f"gw-b:{GW_B_PORT}", use_redis, redis_addr,
                                f"{mode}_run{run_idx}_b",
                                commitment_token_mode, commitment_token_secret,
                            )
                            gw_procs.append(gw_b)
                            targets.append(f"http://{GATEWAY_HOST}:{GW_B_PORT}")

                        # --- 发压 ---
                        stats = run_load_gen(targets, routing, conc, csv_path)

                        row = {
                            "mode":               mode,
                            "concurrency":        conc,
                            "run_idx":            run_idx,
                            "n_gateways":         n_gw,
                            "routing":            routing,
                            "use_redis":          use_redis,
                            "success":            stats.get("success", 0),
                            "rejected_s0":        stats.get("rejected_s0", 0),
                            "cascade_failed":     stats.get("cascade_failed", 0),
                            "effective_goodput_s":stats.get("effective_goodput_s", 0.0),
                            "raw_goodput_s":      stats.get("raw_goodput_s", 0.0),
                            "e2e_p50_ms":         stats.get("e2e_p50_ms", 0.0),
                            "e2e_p95_ms":         stats.get("e2e_p95_ms", 0.0),
                            "e2e_p99_ms":         stats.get("e2e_p99_ms", 0.0),
                            "state_miss_count":   stats.get("state_miss_count", 0),
                            "dup_admission_count":stats.get("dup_admission_count", 0),
                            "cross_node_sessions":stats.get("cross_node_sessions", 0),
                        }
                        write_summary_row(summary_csv, row)

                        gps  = row["effective_goodput_s"]
                        p95  = row["e2e_p95_ms"]
                        casc = row["cascade_failed"]
                        rej0 = row["rejected_s0"]
                        sm   = row["state_miss_count"]
                        dup  = row["dup_admission_count"]
                        xn   = row["cross_node_sessions"]
                        print(
                            f" GP/s={gps:.1f}  P95={p95:.0f}ms  "
                            f"casc={casc}  rej0={rej0}  "
                            f"state_miss={sm}  dup={dup}  cross_node={xn}"
                        )

                    except Exception as e:
                        print(f" ERROR: {e}")
                        write_summary_row(summary_csv, {
                            "mode": mode, "concurrency": conc, "run_idx": run_idx,
                        })

                    finally:
                        for proc in gw_procs:
                            _kill_proc(proc)
                        gw_procs.clear()
                        time.sleep(1)   # 端口复用等待

    finally:
        stop_backend()

    print(f"\n{'='*70}")
    print(f"  实验完成。汇总: {summary_csv}")
    print(f"{'='*70}\n")


# ============================================================
# CLI
# ============================================================

def commitment_secret_required(modes: List[str], commitment_token_mode: str) -> bool:
    return (
        commitment_token_mode == "strict"
        and any(MODE_META[mode]["use_redis"] for mode in modes)
    )


def commitment_secret_should_be_shared(modes: List[str], commitment_token_mode: str) -> bool:
    return (
        commitment_token_mode != "off"
        and any(MODE_META[mode]["n_gateways"] > 1 for mode in modes)
    )


def resolve_commitment_token_secret(
    modes: List[str],
    commitment_token_mode: str,
    provided_secret: str,
) -> str:
    if commitment_token_mode == "off":
        return ""
    if provided_secret:
        return provided_secret
    if commitment_secret_required(modes, commitment_token_mode):
        raise ValueError(
            "--commitment-token-mode strict with Redis/shared modes requires "
            "--commitment-token-secret"
        )
    if commitment_secret_should_be_shared(modes, commitment_token_mode):
        return "mgw-" + secrets.token_urlsafe(32)
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="多网关共享状态实验 (PlanGate dual-node experiment)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--modes", nargs="+", default=DEFAULT_MODES,
                        choices=list(MODE_META.keys()),
                        help=f"要运行的模式 (default: {DEFAULT_MODES})")
    parser.add_argument("--concurrency", nargs="+", type=int, default=DEFAULT_CONC_LEVELS,
                        help=f"并发级列表 (default: {DEFAULT_CONC_LEVELS})")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                        help=f"每组重复次数 (default: {DEFAULT_REPEATS})")
    parser.add_argument("--redis-addr", default=DEFAULT_REDIS_ADDR,
                        help=f"Redis 地址 (default: {DEFAULT_REDIS_ADDR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印执行计划，不实际运行")
    parser.add_argument("--sessions", type=int, default=None,
                        help="覆盖 SESSIONS 参数")
    parser.add_argument("--pg-max-sessions", type=int, default=None,
                        help="覆盖 PG_MAX_SESSIONS 参数")
    parser.add_argument("--ps-ratio", type=float, default=None,
                        help="Plan-and-Solve 会话占比 0.0-1.0 (default: 1.0 — P&S only)")
    parser.add_argument("--results-dir", type=str, default=RESULTS_DIR,
                        help=f"实验输出目录 (default: {RESULTS_DIR})")

    parser.add_argument("--commitment-token-mode", choices=["off", "optional", "strict"],
                        default=DEFAULT_COMMITMENT_TOKEN_MODE,
                        help="Commitment token mode passed to every gateway")
    parser.add_argument("--commitment-token-secret", type=str, default="",
                        help="Shared commitment token secret for all gateways")

    args = parser.parse_args()

    # 允许命令行覆盖全局常量
    global SESSIONS, PG_MAX_SESSIONS, PS_RATIO
    if args.sessions is not None:
        SESSIONS = args.sessions
    if args.pg_max_sessions is not None:
        PG_MAX_SESSIONS = args.pg_max_sessions
    if args.ps_ratio is not None:
        PS_RATIO = args.ps_ratio

    results_dir = args.results_dir
    if not os.path.isabs(results_dir):
        results_dir = os.path.join(ROOT_DIR, results_dir)

    try:
        commitment_token_secret = resolve_commitment_token_secret(
            args.modes,
            args.commitment_token_mode,
            args.commitment_token_secret,
        )
    except ValueError as e:
        parser.error(str(e))

    if commitment_token_secret and not args.commitment_token_secret:
        print("  Commitment token secret: generated shared per-run secret")

    run_sweep(
        modes=args.modes,
        conc_levels=args.concurrency,
        repeats=args.repeats,
        redis_addr=args.redis_addr,
        results_dir=results_dir,
        commitment_token_mode=args.commitment_token_mode,
        commitment_token_secret=commitment_token_secret,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
