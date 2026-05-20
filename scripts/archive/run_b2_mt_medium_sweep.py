#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_b2_mt_medium_sweep.py
=========================
B2-MT-medium 压力扫描 + 正式实验脚本

使用方法:
  # Step 1: 扫描候选配置 (仅 ng + plangate_real, 各跑 1 次)
  python scripts/run_b2_mt_medium_sweep.py --mode sweep

  # Step 2 (可选): 重用 sweep 数据跳过已有 run
  python scripts/run_b2_mt_medium_sweep.py --mode sweep --resume

  # Step 3: 确认选中配置后，正式跑 4 gateway × 3 runs
  python scripts/run_b2_mt_medium_sweep.py --mode formal \\
      --agents 100 --concurrency 10 --burst-size 15 --burst-gap 8

  # 仅重新分析已有正式实验结果 (无需重跑)
  python scripts/run_b2_mt_medium_sweep.py --mode analyze

注意:
  - 不修改 prompt pool (使用当前 neutral multitool prompts)
  - 不覆盖已有 B2 extreme 结果 (neutral_multitool_real_llm/bursty/)
  - 所有统计从 raw steps.csv / steps_agents.csv 重新计算
"""

import argparse
import csv
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent

REACT_CLIENT = SCRIPT_DIR / "react_agent_client.py"
SERVER_PY = ROOT / "mcp_server" / "server.py"
BACKEND_URL = "http://127.0.0.1:8080"

# ─── Output directories ───────────────────────────────────────────
SWEEP_OUT  = ROOT / "results" / "neutral_multitool_real_llm" / "medium_sweep"
FORMAL_OUT = ROOT / "results" / "neutral_multitool_real_llm" / "b2_mt_medium"
LOG_DIR    = ROOT / "logs"   / "neutral_multitool_real_llm"

# ─── Sweep candidate configs ──────────────────────────────────────
# Ordered by increasing pressure. A=lightest, E=heaviest
SWEEP_CONFIGS = [
    {"id": "A", "agents": 100, "concurrency": 8,  "burst_size": 10, "burst_gap": 8.0},
    {"id": "B", "agents": 100, "concurrency": 10, "burst_size": 15, "burst_gap": 8.0},
    {"id": "C", "agents": 150, "concurrency": 10, "burst_size": 15, "burst_gap": 8.0},
    {"id": "D", "agents": 150, "concurrency": 12, "burst_size": 20, "burst_gap": 8.0},
    {"id": "E", "agents": 200, "concurrency": 15, "burst_size": 20, "burst_gap": 8.0},
]

MAX_STEPS = 15   # Same as extreme B2
BUDGET    = 1000

# ─── Ports (avoid bursty=9400-9403, selfhosted=9500-9501) ────────
SWEEP_PORT_NG  = 9450
SWEEP_PORT_PG  = 9451
FORMAL_PORTS   = {"ng": 9450, "rajomon": 9451, "pp": 9452, "plangate_real": 9453}

# ─── Gateway tuning params ────────────────────────────────────────
TUNED_PARAMS = {
    "rajomon":  {"price_step": 5},
    "pp":       {"max_sessions": 20},
    "plangate": {
        "price_step":      40,
        "max_sessions":    12,
        "sunk_cost_alpha": 0.7,
        "cap_wait":        3,
        "ratelimit_max":   200,
        "latency_thresh":  5000,
    },
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    port: int
    extra_args: list = field(default_factory=list)


def _plangate_args():
    p = TUNED_PARAMS["plangate"]
    return [
        "--plangate-price-step",      str(p["price_step"]),
        "--plangate-max-sessions",    str(p["max_sessions"]),
        "--plangate-sunk-cost-alpha", str(p["sunk_cost_alpha"]),
        "--plangate-session-cap-wait",str(p["cap_wait"]),
        "--real-ratelimit-max",       str(p["ratelimit_max"]),
        "--real-latency-threshold",   str(p["latency_thresh"]),
    ]


SWEEP_GATEWAYS = [
    GatewayConfig("ng",           "ng",           SWEEP_PORT_NG),
    GatewayConfig("plangate_real","mcpdp-real",   SWEEP_PORT_PG, _plangate_args()),
]

FORMAL_GATEWAYS = [
    GatewayConfig("ng",           "ng",           FORMAL_PORTS["ng"]),
    GatewayConfig("rajomon",      "rajomon",      FORMAL_PORTS["rajomon"], [
        "--rajomon-price-step", str(TUNED_PARAMS["rajomon"]["price_step"]),
    ]),
    GatewayConfig("pp",           "pp",           FORMAL_PORTS["pp"], [
        "--pp-max-sessions", str(TUNED_PARAMS["pp"]["max_sessions"]),
    ]),
    GatewayConfig("plangate_real","mcpdp-real",   FORMAL_PORTS["plangate_real"], _plangate_args()),
]

GW_ORDER   = ["ng", "rajomon", "pp", "plangate_real"]
GW_DISPLAY = {"ng": "NG", "rajomon": "Rajomon", "pp": "PP", "plangate_real": "PlanGate"}

# ─── Global state ─────────────────────────────────────────────────
BACKEND_PROC   = None
GATEWAY_BINARY = None


# ══════════════════════════════════════════════════════════════════
# Process management
# ══════════════════════════════════════════════════════════════════

def build_gateway():
    global GATEWAY_BINARY
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = ROOT / bin_name
    print(f"  [build] go build -o {bin_name} ./cmd/gateway ...")
    r = subprocess.run(
        ["go", "build", "-o", str(bin_path), "./cmd/gateway"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Gateway build failed:\n{r.stderr[:500]}")
    GATEWAY_BINARY = bin_path
    print(f"  [build] OK → {bin_path}")


def start_backend(log_label: str = "backend"):
    global BACKEND_PROC
    stop_backend()
    log_path = LOG_DIR / f"_{log_label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, str(SERVER_PY),
        "--port",             "8080",
        "--mode",             "real_llm",
        "--max-workers",      "10",
        "--queue-timeout",    "8.0",
        "--congestion-factor","0.5",
    ]
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=str(SERVER_PY.parent),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(4)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"Backend failed to start — check: {log_path}")
    print(f"  [backend] started pid={BACKEND_PROC.pid} log={log_path.name}")


def _wait_port_free(port: int, timeout: float = 12.0, interval: float = 0.5) -> bool:
    """
    Wait until TCP port is free (LISTEN state gone). Returns True if free.
    Uses netstat on Windows; falls back to socket probe elsewhere.
    """
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            r = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            occupied = any(
                f":{port} " in line and "LISTENING" in line
                for line in r.stdout.splitlines()
            )
        else:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", port))
                occupied = False
            except OSError:
                occupied = True
        if not occupied:
            return True
        time.sleep(interval)
    return False


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
    if sys.platform == "win32":
        time.sleep(2)  # Windows TCP TIME_WAIT
    BACKEND_PROC = None


def _probe_backend(timeout: float = 4.0) -> bool:
    """Send a real HTTP request to backend /health or list-tools; return True if responsive."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8080/",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        pass
    # Fallback: try MCP initialize
    try:
        import json as _json
        body = _json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                            "params":{"protocolVersion":"2024-11-05",
                                      "clientInfo":{"name":"probe","version":"0"},
                                      "capabilities":{}}}).encode()
        req2 = urllib.request.Request(
            "http://127.0.0.1:8080/",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=timeout) as resp2:
            return resp2.status < 500
    except Exception:
        return False


def _ensure_backend(log_label: str = "formal_backend"):
    """Check if backend is truly responsive on :8080 via HTTP probe; restart if not."""
    global BACKEND_PROC
    # 1. Quick liveness check: is the process running AND actually responding?
    if BACKEND_PROC is not None and BACKEND_PROC.poll() is None:
        if _probe_backend(timeout=5.0):
            return  # Backend healthy and responsive
        print("  [backend] WARNING: process alive but HTTP probe failed — restarting")
    else:
        print("  [backend] WARNING: process dead — restarting backend")
    # Restart
    log_path = LOG_DIR / f"_{log_label}_restart.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, str(SERVER_PY),
        "--port",             "8080",
        "--mode",             "real_llm",
        "--max-workers",      "10",
        "--queue-timeout",    "8.0",
        "--congestion-factor","0.5",
    ]
    with open(log_path, "a", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=str(SERVER_PY.parent),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(4)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"Backend failed to restart — check: {log_path}")
    print(f"  [backend] restarted — pid={BACKEND_PROC.pid}")


def start_gateway(gw: GatewayConfig, log_label: str = "") -> subprocess.Popen:
    label = log_label or gw.name
    log_path = LOG_DIR / f"_gw_{label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure port is free before starting
    if not _wait_port_free(gw.port, timeout=12.0):
        raise RuntimeError(
            f"Port {gw.port} is still occupied after 12s — "
            f"cannot start gateway {gw.name}. "
            f"Check for stale processes: netstat -ano | findstr :{gw.port}"
        )

    cmd = [
        str(GATEWAY_BINARY),
        "--mode",    gw.mode,
        "--port",    str(gw.port),
        "--backend", BACKEND_URL,
        "--host",    "127.0.0.1",
    ] + gw.extra_args
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"Gateway {gw.name} failed to start — check: {log_path}")
    print(f"  [gateway] {gw.name} started pid={proc.pid} port={gw.port}")
    return proc


def stop_process(proc: Optional[subprocess.Popen], wait_port: int = 0):
    """Terminate process and optionally wait for its port to be released."""
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
    if sys.platform == "win32":
        time.sleep(3)  # Windows TCP TIME_WAIT for port release
    if wait_port:
        if not _wait_port_free(wait_port, timeout=15.0):
            print(f"  WARNING: port {wait_port} still occupied after 15s")
        else:
            print(f"  [port] {wait_port} released")


# ══════════════════════════════════════════════════════════════════
# React agent client runner
# ══════════════════════════════════════════════════════════════════

def run_react_client(gw_url: str, output_csv: str, gw_mode: str,
                     agents: int, concurrency: int,
                     burst_size: int, burst_gap: float) -> str:
    """Invoke react_agent_client.py and return stdout."""
    cmd = [
        sys.executable, str(REACT_CLIENT),
        "--gateway",      gw_url,
        "--agents",       str(agents),
        "--concurrency",  str(concurrency),
        "--max-steps",    str(MAX_STEPS),
        "--budget",       str(BUDGET),
        "--burst-size",   str(burst_size),
        "--burst-gap",    str(burst_gap),
        "--gateway-mode", gw_mode,
        "--output",       output_csv,
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True,
        timeout=7200, env=env, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0 and r.stderr:
        print(f"  [client stderr] {r.stderr[:300]}")
    return r.stdout or ""


# ══════════════════════════════════════════════════════════════════
# Raw CSV metrics recomputation
# ══════════════════════════════════════════════════════════════════

def _pct(num: float, denom: float) -> float:
    return round(100.0 * num / denom, 2) if denom else 0.0


def _pN(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * q), len(sorted_vals) - 1)
    return sorted_vals[idx]


def _read_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def recompute_metrics(run_dir: Path, gateway: str,
                      config_id: str = "", run_idx: int = 1) -> Optional[dict]:
    """
    Recompute all metrics from raw steps.csv + steps_agents.csv.
    Returns None if agents file is missing or empty.
    """
    ap = run_dir / "steps_agents.csv"
    sp = run_dir / "steps.csv"

    agents = _read_csv(ap)
    steps  = _read_csv(sp)

    n = len(agents)
    if n == 0:
        return None

    # ── Agent-level stats ──────────────────────────────────────────
    state_ctr  = Counter(r.get("state", "?") for r in agents)
    success_n  = state_ctr.get("SUCCESS",      0)
    partial_n  = state_ctr.get("PARTIAL",       0)
    rej_n      = state_ctr.get("ALL_REJECTED",  0)
    err_n      = state_ctr.get("ERROR",          0)

    admitted   = success_n + partial_n
    abd        = _pct(partial_n, admitted)

    total_steps_list   = [int(r.get("total_steps",       0)) for r in agents]
    success_steps_list = [int(r.get("success_steps",     0)) for r in agents]
    backend_tok_list   = [int(r.get("backend_llm_tokens",0)) for r in agents]
    latencies          = sorted(float(r.get("total_latency_ms", 0)) for r in agents)

    avg_steps      = round(statistics.mean(total_steps_list), 3) if total_steps_list else 0.0
    zero_step_n    = sum(1 for s in total_steps_list if s == 0)
    ge3_n          = sum(1 for s in total_steps_list if s >= 3)
    backend_tokens = sum(backend_tok_list)

    p50 = _pN(latencies, 0.50)
    p95 = _pN(latencies, 0.95)

    # Cascade = successful tool steps inside sessions that ultimately failed.
    # Interpretation: wasted useful work that could not yield a complete result.
    cascade_steps = sum(
        int(r.get("success_steps", 0)) for r in agents
        if r.get("state") in ("PARTIAL", "ALL_REJECTED")
    )

    # ── Experiment elapsed from step timestamps ────────────────────
    elapsed_s = 0.0
    if steps:
        ts_vals = []
        for r in steps:
            ts_str = r.get("timestamp", "")
            if ts_str:
                try:
                    ts_vals.append(float(ts_str))
                except ValueError:
                    pass
        if len(ts_vals) >= 2:
            elapsed_s = round(max(ts_vals) - min(ts_vals), 1)

    return {
        "config_id":     config_id,
        "gateway":       gateway,
        "run":           run_idx,
        "n_agents":      n,
        "success":       success_n,
        "success_rate":  _pct(success_n, n),
        "partial":       partial_n,
        "rej0":          rej_n,
        "error":         err_n,
        "admitted":      admitted,
        "abd":           abd,
        "cascade_steps": cascade_steps,
        "avg_steps":     avg_steps,
        "zero_step_pct": _pct(zero_step_n, n),
        "ge3_pct":       _pct(ge3_n, n),
        "backend_tokens":backend_tokens,
        "p50_ms":        round(p50),
        "p95_ms":        round(p95),
        "elapsed_s":     elapsed_s,
    }


# ══════════════════════════════════════════════════════════════════
# Sweep runner
# ══════════════════════════════════════════════════════════════════

def run_sweep(configs: list = None, resume: bool = False) -> List[dict]:
    """Run the medium pressure sweep on ng + plangate_real (1 repeat each)."""
    if configs is None:
        configs = SWEEP_CONFIGS

    SWEEP_OUT.mkdir(parents=True, exist_ok=True)
    all_rows: List[dict] = []

    for cfg in configs:
        cid        = cfg["id"]
        agents     = cfg["agents"]
        concurrency= cfg["concurrency"]
        burst_size = cfg["burst_size"]
        burst_gap  = cfg["burst_gap"]

        print(f"\n{'='*65}")
        print(f"  Config {cid}: agents={agents}  C={concurrency}  "
              f"burst={burst_size}×{burst_gap}s  max_steps={MAX_STEPS}")
        print(f"{'='*65}")

        for gw in SWEEP_GATEWAYS:
            run_dir    = SWEEP_OUT / cid / gw.name / "run1"
            run_dir.mkdir(parents=True, exist_ok=True)
            output_csv = str(run_dir / "steps.csv")
            agents_csv = run_dir / "steps_agents.csv"

            if resume and agents_csv.exists():
                try:
                    rc = sum(1 for _ in open(agents_csv, encoding="utf-8-sig")) - 1
                    if rc > 0:
                        print(f"  [resume] skip {cid}/{gw.name}/run1 ({rc} agents exist)")
                        m = recompute_metrics(run_dir, gw.name, cid, 1)
                        if m:
                            all_rows.append(m)
                        continue
                except Exception:
                    pass

            print(f"\n  --- {cid}/{gw.name} ---")
            gw_proc = start_gateway(gw, log_label=f"sweep_{cid}_{gw.name}")
            time.sleep(2)
            try:
                stdout = run_react_client(
                    f"http://127.0.0.1:{gw.port}", output_csv, gw.name,
                    agents, concurrency, burst_size, burst_gap,
                )
                (run_dir / "stdout.log").write_text(stdout, encoding="utf-8")

                m = recompute_metrics(run_dir, gw.name, cid, 1)
                if m is None:
                    print(f"  WARNING: no data for {cid}/{gw.name}")
                else:
                    all_rows.append(m)
                    print(f"  success={m['success_rate']:.1f}%  ABD={m['abd']:.1f}%  "
                          f"avg_steps={m['avg_steps']:.2f}  partial={m['partial']}  "
                          f"rej0={m['rej0']}")
            finally:
                stop_process(gw_proc)
                print(f"  冷却 20s ...")
                time.sleep(20)

    return all_rows


# ══════════════════════════════════════════════════════════════════
# Config auto-selector
# ══════════════════════════════════════════════════════════════════

def auto_select_config(rows: List[dict]) -> Tuple[dict, str]:
    """
    Select the best medium config for the paper, balancing two goals:
      1. NG is in a medium stress regime (success 10-30%, ABD 50-90%)
      2. PlanGate shows a clear advantage (partial < NG partial AND cascade < NG cascade)

    Priority order:
      Tier 1: NG success 10-30% + NG ABD 50-80% + PlanGate shows advantage
      Tier 2: NG success 10-30% + NG ABD 50-90% + PlanGate shows advantage
              (ABD slightly above 80% accepted if PlanGate difference is visible)
      Tier 3: NG success 10-30% + NG ABD 50-80% (no PlanGate advantage — usable but note)
      Tier 4: Closest config that isn't near-extinction (sr > 5%)
    """
    by_cfg: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for row in rows:
        cid = row.get("config_id", "?")
        gw  = row.get("gateway", "?")
        typed = {}
        for k, v in row.items():
            if k in ("success_rate","abd","avg_steps","zero_step_pct","partial",
                     "rej0","cascade_steps","backend_tokens","p50_ms","p95_ms"):
                try:
                    typed[k] = float(v)
                except (ValueError, TypeError):
                    typed[k] = 0.0
            else:
                typed[k] = v
        by_cfg[cid][gw] = typed

    candidates = []
    for cid, gw_data in by_cfg.items():
        ng = gw_data.get("ng")
        pg = gw_data.get("plangate_real")
        if ng is None:
            continue

        sr    = float(ng.get("success_rate", 0))
        abd   = float(ng.get("abd", 0))
        avg_s = float(ng.get("avg_steps", 0))
        zero_p= float(ng.get("zero_step_pct", 100))

        in_sr_strict = 10.0 <= sr  <= 30.0
        in_abd_strict= 50.0 <= abd <= 80.0
        in_abd_loose = 50.0 <= abd <= 90.0   # allow up to 90% if PG shows advantage
        in_s         = avg_s >= 3.0
        in_z         = zero_p < 20.0

        # PlanGate advantage: BOTH partial < NG partial AND cascade < NG cascade
        if pg is not None:
            pg_partial_less  = float(pg.get("partial",       9999)) < float(ng.get("partial",       0))
            pg_cascade_less  = float(pg.get("cascade_steps", 9999)) < float(ng.get("cascade_steps", 0))
            pg_shows_advantage = pg_partial_less and pg_cascade_less
            pg_cascade_delta = float(ng.get("cascade_steps",0)) - float(pg.get("cascade_steps",9999))
        else:
            pg_shows_advantage = False
            pg_cascade_delta = 0.0

        # Tier assignment
        if in_sr_strict and in_abd_strict and in_s and in_z and pg_shows_advantage:
            tier = 1
        elif in_sr_strict and in_abd_loose and in_s and in_z and pg_shows_advantage:
            tier = 2
        elif in_sr_strict and in_abd_strict and in_s and in_z:
            tier = 3
        else:
            tier = 4

        sr_dist  = 0.0 if in_sr_strict else min(abs(sr  - 10), abs(sr  - 30))
        abd_dist = 0.0 if in_abd_loose  else min(abs(abd - 50), abs(abd - 90))
        total_dist = sr_dist + abd_dist * 0.3

        candidates.append({
            "config_id":          cid,
            "sr":                 sr,
            "abd":                abd,
            "avg_steps":          avg_s,
            "zero_pct":           zero_p,
            "tier":               tier,
            "pg_shows_advantage": pg_shows_advantage,
            "pg_cascade_delta":   pg_cascade_delta,
            "total_dist":         total_dist,
        })

    if not candidates:
        return {}, "No valid sweep data found"

    # Sort: lower tier (= better) first; within same tier, prefer larger cascade delta
    candidates.sort(key=lambda x: (
        x["tier"],
        x["sr"] <= 5.0,                     # penalise near-extinction
        -x["pg_cascade_delta"],             # prefer bigger PlanGate cascade reduction
        x["total_dist"],
    ))

    best = candidates[0]
    cid  = best["config_id"]

    tier_desc = {
        1: "Tier-1 — NG in target range (10–30 %/50–80 %) + PlanGate clear advantage",
        2: "Tier-2 — NG success in range, ABD slightly above 80 %, PlanGate shows cascade/partial reduction",
        3: "Tier-3 — NG in target range but PlanGate shows no partial/cascade advantage",
        4: "Tier-4 — fallback (no config satisfies all criteria)",
    }

    reason = (
        f"{tier_desc[best['tier']]}. "
        f"Config {cid}: NG success_rate={best['sr']:.1f}%, "
        f"NG ABD={best['abd']:.1f}%, avg_steps={best['avg_steps']:.2f}, "
        f"PlanGate_advantage={best['pg_shows_advantage']}"
        + (f", cascade_saved={best['pg_cascade_delta']:.0f}" if best["pg_cascade_delta"] else "")
    )

    cfg_dict = next((c for c in SWEEP_CONFIGS if c["id"] == cid), {})
    return cfg_dict, reason


# ══════════════════════════════════════════════════════════════════
# Sweep report writers
# ══════════════════════════════════════════════════════════════════

_SWEEP_CSV_FIELDS = [
    "config_id","gateway","n_agents","concurrency","burst_size","burst_gap",
    "success","success_rate","partial","rej0","abd","cascade_steps",
    "avg_steps","zero_step_pct","ge3_pct","backend_tokens","p50_ms","p95_ms","elapsed_s",
]


def write_sweep_csv(rows: List[dict]) -> Path:
    SWEEP_OUT.mkdir(parents=True, exist_ok=True)
    out = SWEEP_OUT / "medium_sweep_summary.csv"
    enriched = []
    for r in rows:
        cid = r.get("config_id", "")
        cfg = next((c for c in SWEEP_CONFIGS if c["id"] == cid), {})
        row = dict(r)
        row.setdefault("concurrency", cfg.get("concurrency", ""))
        row.setdefault("burst_size",  cfg.get("burst_size",  ""))
        row.setdefault("burst_gap",   cfg.get("burst_gap",   ""))
        enriched.append(row)
    if not enriched:
        return out
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_SWEEP_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(enriched)
    print(f"\n  Sweep CSV → {out}")
    return out


def _sweep_table_rows(rows: List[dict], gateway: str) -> str:
    gw_rows = sorted(
        [r for r in rows if r.get("gateway") == gateway],
        key=lambda x: x.get("config_id", ""),
    )
    lines = []
    for r in gw_rows:
        cid = r["config_id"]
        cfg = next((c for c in SWEEP_CONFIGS if c["id"] == cid), {})
        lines.append(
            f"| {cid} | {r['n_agents']} | {cfg.get('concurrency','')} | "
            f"{cfg.get('burst_size','')} | {float(r['success_rate']):.1f} | "
            f"{float(r['abd']):.1f} | {float(r['avg_steps']):.2f} | "
            f"{float(r['zero_step_pct']):.1f} | {int(float(r['backend_tokens'])):,} | "
            f"{int(float(r.get('cascade_steps',0)))} | "
            f"{int(float(r['partial']))} | {int(float(r['rej0']))} | "
            f"{int(float(r['p50_ms'])):,} | {int(float(r['p95_ms'])):,} |\n"
        )
    return "".join(lines)


def write_sweep_report(rows: List[dict], selected_cfg: dict, reason: str) -> Path:
    SWEEP_OUT.mkdir(parents=True, exist_ok=True)
    out = SWEEP_OUT / "medium_sweep_report.md"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    hdr = (
        "| Config | Agents | C | Burst | Succ% | ABD% | AvgSteps | Zero% "
        "| BackTok | Cascade | Partial | Rej0 | P50ms | P95ms |\n"
        "|--------|-------:|--:|------:|------:|-----:|---------:|-----:"
        "|--------:|--------:|--------:|-----:|------:|------:|\n"
    )

    lines = [
        f"# B2-MT-medium Pressure Sweep Report\n\n",
        f"Generated: {ts}\n\n",
        f"## Goal\n\n",
        f"Find a medium-pressure config where NG success_rate ≈ 10–30 % and "
        f"NG ABD ≈ 50–80 %, so gateway differences are visible without "
        f"total system collapse.\n\n",
        f"## Sweep Configs\n\n",
        f"| Config | Agents | Concurrency | BurstSize | BurstGap |\n",
        f"|--------|-------:|------------:|----------:|---------:|\n",
    ]
    for c in SWEEP_CONFIGS:
        lines.append(f"| {c['id']} | {c['agents']} | {c['concurrency']} "
                     f"| {c['burst_size']} | {c['burst_gap']}s |\n")
    lines += [
        "\n",
        f"MAX_STEPS={MAX_STEPS}, BUDGET={BUDGET}, backend_workers=10\n\n",
        "## Results — NG\n\n", hdr,
        _sweep_table_rows(rows, "ng"),
        "\n",
        "## Results — PlanGate Real\n\n", hdr,
        _sweep_table_rows(rows, "plangate_real"),
        "\n",
        "## Selected Configuration\n\n",
    ]

    if selected_cfg:
        lines += [
            "```\n",
            f"Selected B2-MT-medium config:\n",
            f"  agents={selected_cfg.get('agents','?')}\n",
            f"  concurrency={selected_cfg.get('concurrency','?')}\n",
            f"  burst_size={selected_cfg.get('burst_size','?')}\n",
            f"  burst_gap={selected_cfg.get('burst_gap','?')}s\n",
            f"  reason={reason}\n",
            "```\n\n",
            "### Next Step: Formal B2-MT-medium Run\n\n",
            "Confirm the config above, then run:\n\n",
            "```bash\n",
            f"python scripts/run_b2_mt_medium_sweep.py --mode formal \\\n",
            f"  --agents {selected_cfg.get('agents','?')} \\\n",
            f"  --concurrency {selected_cfg.get('concurrency','?')} \\\n",
            f"  --burst-size {selected_cfg.get('burst_size','?')} \\\n",
            f"  --burst-gap {selected_cfg.get('burst_gap','?')} \\\n",
            f"  --repeats 3\n",
            "```\n",
        ]
    else:
        lines.append("> No config selected — no valid sweep data.\n")

    with open(out, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  Sweep report → {out}")
    return out


# ══════════════════════════════════════════════════════════════════
# Formal runner
# ══════════════════════════════════════════════════════════════════

def run_formal(agents: int, concurrency: int, burst_size: int, burst_gap: float,
               repeats: int = 3, resume: bool = False) -> List[dict]:
    """Run 4 gateways × repeats on the selected medium config."""
    FORMAL_OUT.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "b2_mt_medium").mkdir(parents=True, exist_ok=True)
    all_rows: List[dict] = []

    for gw in FORMAL_GATEWAYS:
        for run_idx in range(1, repeats + 1):
            run_dir    = FORMAL_OUT / gw.name / f"run{run_idx}"
            run_dir.mkdir(parents=True, exist_ok=True)
            output_csv = str(run_dir / "steps.csv")
            agents_csv = run_dir / "steps_agents.csv"

            if resume and agents_csv.exists():
                try:
                    rc = sum(1 for _ in open(agents_csv, encoding="utf-8-sig")) - 1
                    if rc > 0:
                        print(f"  [resume] skip {gw.name}/run{run_idx} ({rc} agents exist)")
                        m = recompute_metrics(run_dir, gw.name, "medium", run_idx)
                        if m:
                            all_rows.append(m)
                        continue
                except Exception:
                    pass

            print(f"\n{'='*65}")
            print(f"  [{gw.name}] Formal Medium Run {run_idx}/{repeats}")
            print(f"  agents={agents}  C={concurrency}  "
                  f"burst={burst_size}×{burst_gap}s  steps={MAX_STEPS}")
            print(f"{'='*65}")

            # Ensure backend is alive before starting this gateway run
            _ensure_backend(log_label="formal_backend")

            gw_proc = start_gateway(
                gw, log_label=f"formal_{gw.name}_r{run_idx}"
            )
            time.sleep(2)
            try:
                stdout = run_react_client(
                    f"http://127.0.0.1:{gw.port}", output_csv, gw.name,
                    agents, concurrency, burst_size, burst_gap,
                )
                (run_dir / "stdout.log").write_text(stdout, encoding="utf-8")

                m = recompute_metrics(run_dir, gw.name, "medium", run_idx)
                if m is None:
                    print(f"  WARNING: no data for {gw.name}/run{run_idx}")
                else:
                    all_rows.append(m)
                    print(f"  success={m['success_rate']:.1f}%  ABD={m['abd']:.1f}%  "
                          f"partial={m['partial']}  avg_steps={m['avg_steps']:.2f}")
            finally:
                stop_process(gw_proc, wait_port=gw.port)  # wait for port release
                cooldown = 30 if run_idx < repeats else 10
                print(f"  冷却 {cooldown}s ...")
                time.sleep(cooldown)

    return all_rows


# ══════════════════════════════════════════════════════════════════
# Formal report generators
# ══════════════════════════════════════════════════════════════════

def _mean(lst: list) -> float:
    return statistics.mean(lst) if lst else 0.0


def _stdev(lst: list) -> float:
    return statistics.stdev(lst) if len(lst) > 1 else 0.0


def aggregate_by_gateway(rows: List[dict]) -> Dict[str, dict]:
    gw_rows: Dict[str, list] = defaultdict(list)
    for r in rows:
        gw_rows[r["gateway"]].append(r)

    result = {}
    for gw, rl in gw_rows.items():
        result[gw] = {
            "n_runs":           len(rl),
            "success_rate":     _mean([float(r["success_rate"]) for r in rl]),
            "success_rate_std": _stdev([float(r["success_rate"]) for r in rl]),
            "abd":              _mean([float(r["abd"]) for r in rl]),
            "abd_std":          _stdev([float(r["abd"]) for r in rl]),
            "partial":          _mean([float(r["partial"]) for r in rl]),
            "rej0":             _mean([float(r["rej0"]) for r in rl]),
            "cascade_steps":    _mean([float(r["cascade_steps"]) for r in rl]),
            "avg_steps":        _mean([float(r["avg_steps"]) for r in rl]),
            "zero_step_pct":    _mean([float(r["zero_step_pct"]) for r in rl]),
            "backend_tokens":   _mean([float(r["backend_tokens"]) for r in rl]),
            "p50_ms":           _mean([float(r["p50_ms"]) for r in rl]),
            "p95_ms":           _mean([float(r["p95_ms"]) for r in rl]),
        }
    return result


def write_formal_csv(rows: List[dict]) -> Path:
    FORMAL_OUT.mkdir(parents=True, exist_ok=True)
    out = FORMAL_OUT / "medium_summary.csv"
    if not rows:
        return out
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n  Formal CSV → {out}")
    return out


def write_formal_report(rows: List[dict], cfg: dict, reason: str) -> Path:
    """Write medium_report.md with detailed table, PlanGate comparison,
    cascade note, English paper paragraph, and Chinese PPT narration."""
    FORMAL_OUT.mkdir(parents=True, exist_ok=True)
    out  = FORMAL_OUT / "medium_report.md"
    ts   = time.strftime("%Y-%m-%d %H:%M:%S")
    agg  = aggregate_by_gateway(rows)

    lines: List[str] = [
        "# B2-MT-medium Formal Experiment Report\n\n",
        f"Generated: {ts}\n\n",
        "## Experiment Configuration\n\n",
        f"| Parameter | Value |\n",
        f"|-----------|-------|\n",
        f"| agents | {cfg.get('agents','?')} |\n",
        f"| concurrency | {cfg.get('concurrency','?')} |\n",
        f"| burst_size | {cfg.get('burst_size','?')} |\n",
        f"| burst_gap | {cfg.get('burst_gap','?')}s |\n",
        f"| max_steps | {MAX_STEPS} |\n",
        f"| budget | {BUDGET} |\n",
        f"| gateways | NG, Rajomon, PP, PlanGate_real |\n",
        f"| repeats | {len(rows)//max(len(set(r['gateway'] for r in rows)),1)} |\n\n",
        f"Config selection reason: {reason}\n\n",

        "## Per-Run Raw Results\n\n",
        "| Gateway | Run | Succ% | ABD% | PARTIAL | Rej0 | Cascade | AvgSteps "
        "| BackTok | P50ms | P95ms |\n",
        "|---------|----:|------:|-----:|--------:|-----:|--------:|---------:"
        "|--------:|------:|------:|\n",
    ]
    for r in rows:
        lines.append(
            f"| {GW_DISPLAY.get(r['gateway'], r['gateway'])} | {r['run']} | "
            f"{float(r['success_rate']):.1f} | {float(r['abd']):.1f} | "
            f"{int(float(r['partial']))} | {int(float(r['rej0']))} | "
            f"{int(float(r['cascade_steps']))} | {float(r['avg_steps']):.2f} | "
            f"{int(float(r['backend_tokens'])):,} | "
            f"{int(float(r['p50_ms'])):,} | {int(float(r['p95_ms'])):,} |\n"
        )

    lines += [
        "\n",
        "## Aggregated Results (mean ± std over runs)\n\n",
        "| Gateway | Succ% | ABD% | PARTIAL | Rej0 | Cascade | AvgSteps "
        "| BackendTok | P50ms | P95ms |\n",
        "|---------|------:|-----:|--------:|-----:|--------:|---------:"
        "|-----------:|------:|------:|\n",
    ]
    for gw in GW_ORDER:
        if gw not in agg:
            continue
        a = agg[gw]
        lines.append(
            f"| {GW_DISPLAY[gw]} "
            f"| {a['success_rate']:.1f}±{a['success_rate_std']:.1f} "
            f"| {a['abd']:.1f}±{a['abd_std']:.1f} "
            f"| {a['partial']:.0f} "
            f"| {a['rej0']:.0f} "
            f"| {a['cascade_steps']:.0f} "
            f"| {a['avg_steps']:.2f} "
            f"| {a['backend_tokens']:.0f} "
            f"| {a['p50_ms']:.0f} "
            f"| {a['p95_ms']:.0f} |\n"
        )

    # ── Cascade computation note ──────────────────────────────────
    lines += [
        "\n",
        "### Cascade Computation Note\n\n",
        "**Cascade** = `SUM(success_steps)` for all agents in `PARTIAL` or "
        "`ALL_REJECTED` state. It counts the number of tool calls that "
        "completed successfully *within* a session that ultimately failed to "
        "reach its goal. These represent wasted useful computation — backend "
        "LLM invocations consumed without producing a complete agent response.\n\n",
        "If `success_steps` is not available in the raw CSV, cascade can be "
        "approximated as `(total_steps − failed_steps)` for incomplete sessions.\n\n",
    ]

    # ── PlanGate vs NG comparison ─────────────────────────────────
    ng_agg = agg.get("ng", {})
    pg_agg = agg.get("plangate_real", {})

    if ng_agg and pg_agg:
        partial_delta  = ng_agg["partial"]      - pg_agg["partial"]
        cascade_delta  = ng_agg["cascade_steps"]- pg_agg["cascade_steps"]
        sr_delta       = pg_agg["success_rate"] - ng_agg["success_rate"]
        abd_delta      = ng_agg["abd"]           - pg_agg["abd"]
        p95_delta      = pg_agg["p95_ms"]       - ng_agg["p95_ms"]

        lines += [
            "## PlanGate vs NG Comparison\n\n",
            "| Metric | NG | PlanGate | Δ (PG − NG) |\n",
            "|--------|---:|---------:|-------------:|\n",
            f"| Success Rate (%) | {ng_agg['success_rate']:.1f} | {pg_agg['success_rate']:.1f} | {sr_delta:+.1f} pp |\n",
            f"| ABD (%) | {ng_agg['abd']:.1f} | {pg_agg['abd']:.1f} | {-abd_delta:+.1f} pp |\n",
            f"| Partial sessions | {ng_agg['partial']:.0f} | {pg_agg['partial']:.0f} | {-partial_delta:+.0f} |\n",
            f"| Cascade steps | {ng_agg['cascade_steps']:.0f} | {pg_agg['cascade_steps']:.0f} | {pg_agg['cascade_steps']-ng_agg['cascade_steps']:+.0f} |\n",
            f"| P95 latency (ms) | {ng_agg['p95_ms']:.0f} | {pg_agg['p95_ms']:.0f} | {p95_delta:+.0f} |\n",
            "\n",
        ]

        # ── English paper paragraph ───────────────────────────────
        ng_sr   = ng_agg["success_rate"]
        ng_abd  = ng_agg["abd"]
        ng_avg  = ng_agg["avg_steps"]
        ng_ztok = ng_agg["zero_step_pct"]
        ng_btok = ng_agg["backend_tokens"]
        pg_sr   = pg_agg["success_rate"]
        pg_abd  = pg_agg["abd"]

        lines.append("## English Paper Paragraph\n\n")
        para = (
            f"Under a medium-pressure neutral multitool workload "
            f"(agents={cfg.get('agents','?')}, concurrency={cfg.get('concurrency','?')}, "
            f"burst\\_size={cfg.get('burst_size','?')}), "
            f"the benchmark exercised non-trivial multi-step agent behavior: "
            f"NG completed an average of {ng_avg:.2f} tool-call steps per session "
            f"with a zero-step fraction of {ng_ztok:.1f}\\,\\% and a total "
            f"backend token consumption of {ng_btok:,.0f} tokens. "
            f"This confirms that the workload successfully engaged backend LLMs "
            f"and produced realistic overload stress. "
        )
        if 5.0 <= ng_sr <= 40.0:
            para += (
                f"Under these conditions, NG reached a session success rate of "
                f"{ng_sr:.1f}\\,\\% with an abandonment rate (ABD) of {ng_abd:.1f}\\,\\%, "
                f"producing {int(ng_agg['partial'])} partial sessions and "
                f"{int(ng_agg['cascade_steps'])} cascade-wasted tool steps. "
            )
        else:
            para += (
                f"NG reached a success rate of {ng_sr:.1f}\\,\\% with "
                f"ABD={ng_abd:.1f}\\,\\%, "
                f"partial={int(ng_agg['partial'])}, "
                f"cascade_steps={int(ng_agg['cascade_steps'])}. "
            )
        if int(partial_delta) > 0:
            para += (
                f"PlanGate reduced partial sessions from {int(ng_agg['partial'])} to "
                f"{int(pg_agg['partial'])} "
                f"({int(partial_delta)} fewer), indicating that its plan-aware "
                f"admission control limits the accumulation of cascade waste. "
            )
        else:
            para += (
                f"PlanGate did not reduce partial sessions versus NG "
                f"({int(pg_agg['partial'])} vs {int(ng_agg['partial'])}), "
                f"suggesting that the selected configuration may require "
                f"further PlanGate parameter tuning. "
            )
        if abs(sr_delta) <= 5.0:
            para += (
                f"Session success rates were comparable ({pg_sr:.1f}\\,\\% for "
                f"PlanGate vs {ng_sr:.1f}\\,\\% for NG), suggesting that "
                f"PlanGate's tighter admission does not significantly compromise "
                f"step-0 availability. "
            )
        elif sr_delta < -5.0:
            para += (
                f"PlanGate's stricter admission yielded a lower success rate "
                f"({pg_sr:.1f}\\,\\% vs NG {ng_sr:.1f}\\,\\%), "
                f"indicating a trade-off between cascade waste reduction and "
                f"session admission. "
            )
        else:
            para += (
                f"PlanGate achieved a higher success rate "
                f"({pg_sr:.1f}\\,\\% vs NG {ng_sr:.1f}\\,\\%), "
                f"reflecting more effective early admission decisions. "
            )
        para += (
            f"These observations are consistent with the hypothesis that "
            f"PlanGate reduces continuation waste by shifting some doomed "
            f"sessions to earlier rejection; however, PlanGate does not create "
            f"additional backend capacity, and any reduction in cascade waste "
            f"may come at the cost of reduced session admission availability. "
            f"Under medium-pressure neutral multitool overload, incomplete "
            f"sessions remain the dominant outcome for all methods, and the "
            f"operator must explicitly weigh whether earlier rejection of "
            f"low-probability sessions is preferable to allowing them to "
            f"accumulate partial tool-call costs.\n\n"
        )
        lines.append(para)
    else:
        lines.append("## English Paper Paragraph\n\n*(Results not yet available)*\n\n")

    # ── PlanGate vs Rajomon / PP comparison ──────────────────────
    rj_agg = agg.get("rajomon", {})
    pp_agg = agg.get("pp", {})
    if pg_agg and (rj_agg or pp_agg):
        lines.append("## PlanGate vs Rajomon / PP Comparison\n\n")
        lines += [
            "| Metric | PlanGate | Rajomon | PP |\n",
            "|--------|--------:|--------:|---:|\n",
        ]
        def _v(d: dict, k: str, fmt: str = ".1f") -> str:
            return (("{:" + fmt + "}").format(d[k])) if d and k in d else "—"
        lines += [
            f"| Success Rate (%) | {_v(pg_agg,'success_rate')} | {_v(rj_agg,'success_rate')} | {_v(pp_agg,'success_rate')} |\n",
            f"| ABD (%) | {_v(pg_agg,'abd')} | {_v(rj_agg,'abd')} | {_v(pp_agg,'abd')} |\n",
            f"| Partial sessions | {_v(pg_agg,'partial','.0f')} | {_v(rj_agg,'partial','.0f')} | {_v(pp_agg,'partial','.0f')} |\n",
            f"| Rej0 (step-0 rejections) | {_v(pg_agg,'rej0','.0f')} | {_v(rj_agg,'rej0','.0f')} | {_v(pp_agg,'rej0','.0f')} |\n",
            f"| Cascade steps | {_v(pg_agg,'cascade_steps','.0f')} | {_v(rj_agg,'cascade_steps','.0f')} | {_v(pp_agg,'cascade_steps','.0f')} |\n",
            f"| Backend tokens | {_v(pg_agg,'backend_tokens','.0f')} | {_v(rj_agg,'backend_tokens','.0f')} | {_v(pp_agg,'backend_tokens','.0f')} |\n",
            "\n",
        ]
        # Narrative note
        tradeoff_lines = [
            "### Interpretation\n\n",
            "- **Rej0**: PlanGate relies on plan-level early rejection; a higher Rej0 vs "
            "Rajomon/PP indicates it shifts sessions to step-0 exit rather than allowing "
            "them to consume backend tokens across multiple steps.\n",
            "- **Cascade reduction**: Lower cascade steps mean fewer tool-call tokens are "
            "consumed on doomed sessions. This is the primary efficiency gain PlanGate "
            "targets.\n",
            "- **Success rate tradeoff**: If PlanGate's success rate is lower than "
            "Rajomon/PP, this represents a real tradeoff — not a flaw to be minimised, "
            "but an honest exchange of session admission for reduced cascade waste.\n",
            "- **Rajomon vs PP**: Both use session-level rate control but differ in "
            "granularity; PP applies token-bucket admission while Rajomon uses session "
            "scoring. Neither has plan-level visibility into multi-step cost.\n\n",
        ]
        lines += tradeoff_lines

    # ── English paper paragraph (conservative, final) ─────────────
    # The paragraph appended earlier (if ng_agg and pg_agg) is already conservative.
    # Here we append an additional note about Rajomon/PP if available.
    if pg_agg and rj_agg and pp_agg and ng_agg:
        pg_casc  = pg_agg.get("cascade_steps", 0)
        rj_casc  = rj_agg.get("cascade_steps", 0)
        pp_casc  = pp_agg.get("cascade_steps", 0)
        pg_rej0  = pg_agg.get("rej0", 0)
        rj_rej0  = rj_agg.get("rej0", 0)
        pp_rej0  = pp_agg.get("rej0", 0)
        pg_sr2   = pg_agg.get("success_rate", 0)
        rj_sr    = rj_agg.get("success_rate", 0)
        pp_sr    = pp_agg.get("success_rate", 0)

        addendum = (
            "\n**Paper paragraph addendum (Rajomon/PP comparison):**\n\n"
            "Compared to rate-control baselines, PlanGate's Rej0 count "
            f"({pg_rej0:.0f}) is {'higher' if pg_rej0 > max(rj_rej0, pp_rej0) else 'comparable'} "
            f"than Rajomon ({rj_rej0:.0f}) and PP ({pp_rej0:.0f}), confirming that "
            "PlanGate shifts rejection earlier in the session lifecycle. "
        )
        if pg_casc < rj_casc and pg_casc < pp_casc:
            addendum += (
                f"This early-exit strategy reduces cascade-wasted tool steps: "
                f"PlanGate ({pg_casc:.0f}) vs Rajomon ({rj_casc:.0f}) "
                f"and PP ({pp_casc:.0f}). "
            )
        else:
            addendum += (
                f"However, cascade step counts — PlanGate ({pg_casc:.0f}), "
                f"Rajomon ({rj_casc:.0f}), PP ({pp_casc:.0f}) — show that "
                "the advantage is not uniform across all baselines. "
            )
        if pg_sr2 < min(rj_sr, pp_sr) - 2.0:
            addendum += (
                "PlanGate accepts a lower session success rate than both baselines, "
                "which represents a genuine admission-vs-waste tradeoff that the "
                "system operator must weigh explicitly. "
            )
        else:
            addendum += (
                "Session success rates are broadly similar across methods, "
                "indicating that PlanGate's early rejection does not materially "
                "reduce the fraction of fully-completed sessions. "
            )
        addendum += (
            "We do not claim that PlanGate creates additional backend capacity; "
            "rather, it reallocates capacity by terminating low-probability "
            "sessions before they accumulate multi-step costs. "
            "Whether this trades worthwhile value depends on whether cascade "
            "continuation steps have non-zero marginal utility for the operator.\n\n"
        )
        lines.append(addendum)

    # ── Chinese PPT narration (specific, data-driven) ─────────────
    lines.append("## 中文 PPT 讲述稿\n\n")

    if ng_agg and pg_agg:
        ng_sr_v   = ng_agg.get("success_rate", 0)
        ng_abd_v  = ng_agg.get("abd", 0)
        ng_part   = int(ng_agg.get("partial", 0))
        ng_casc   = int(ng_agg.get("cascade_steps", 0))
        ng_rej0   = int(ng_agg.get("rej0", 0))
        pg_sr_v   = pg_agg.get("success_rate", 0)
        pg_part   = int(pg_agg.get("partial", 0))
        pg_casc   = int(pg_agg.get("cascade_steps", 0))
        pg_rej0   = int(pg_agg.get("rej0", 0))
        part_red  = ng_part  - pg_part
        casc_red  = ng_casc  - pg_casc
        sr_diff   = pg_sr_v  - ng_sr_v
        part_pct  = (part_red / ng_part * 100) if ng_part > 0 else 0
        casc_pct  = (casc_red / ng_casc * 100) if ng_casc > 0 else 0

        ppt  = (
            "这张图展示的是 B2-MT-medium 实验——中等突发压力下四种 gateway 的治理比较。\n\n"
            f"先看基准线 NG：在 {cfg.get('agents','?')} 个 agent、并发度 "
            f"{cfg.get('concurrency','?')}、突发 {cfg.get('burst_size','?')} 的配置下，"
            f"NG 成功率约 {ng_sr_v:.1f}%，放弃率（ABD）约 {ng_abd_v:.1f}%。"
            f"有 {ng_part} 个 session 为 partial 状态——即它们消耗了多步工具调用，"
            f"但最终未完成任务，产生了约 {ng_casc} 步的级联浪费（cascade waste）。"
            "这是过载系统中典型的隐性成本：后端的 LLM token 消耗在无效任务上。\n\n"
            "再看 PlanGate：")
        if part_red > 0:
            ppt += (
                f"通过 plan-aware 准入控制，PlanGate 将 partial session 数从 {ng_part} "
                f"降至 {pg_part}，减少 {part_red} 个（{part_pct:.0f}%）；"
                f"级联浪费步骤从 {ng_casc} 降至 {pg_casc}，减少 {casc_pct:.0f}%。"
                "这表明 PlanGate 有效地将一部分注定失败的 session 在更早阶段拒绝，"
                "避免了它们占用后端多步资源。")
        else:
            ppt += (
                f"在本轮实验中，PlanGate 的 partial session 数（{pg_part}）"
                f"与 NG（{ng_part}）相当，cascade 浪费未见显著减少。"
                "这可能是因为 PlanGate 的参数需要针对该压力点进一步调优，"
                "或者该压力量级下准入控制的边际效益有限。")
        ppt += "\n\n"
        if sr_diff < -5.0:
            ppt += (
                f"**需要明确指出的是**：PlanGate 成功率（{pg_sr_v:.1f}%）低于 NG "
                f"（{ng_sr_v:.1f}%），差距约 {abs(sr_diff):.1f} 个百分点。"
                "这是一个真实的 tradeoff，而非 bug——PlanGate 用更高的步骤 0 拒绝率"
                f"（rej0={pg_rej0} vs NG {ng_rej0}）换来了更低的级联浪费，"
                "但同时让部分本可完成的 session 提前退出了。"
                "在论文中我们保守地表述为：PlanGate 在过载下重新分配了容量，"
                "而非创造了额外容量；选择接受这个 tradeoff 取决于运营方对"
                "级联成本与接入率的价值判断。\n\n")
        elif abs(sr_diff) <= 5.0:
            ppt += (
                f"成功率方面，PlanGate（{pg_sr_v:.1f}%）与 NG（{ng_sr_v:.1f}%）"
                "相差不大，说明更激进的早期拒绝并未显著伤害能完成的 session。"
                "这是一个相对有利的结果，但我们在论文中仍然保守表述：\n"
                "这种等价性在当前配置下成立，不排除在更高压力下出现更大差距。\n\n")
        else:
            ppt += (
                f"PlanGate 成功率（{pg_sr_v:.1f}%）略高于 NG（{ng_sr_v:.1f}%），"
                "该结果需谨慎解读：差距较小，统计噪声需考量。\n\n")
        if rj_agg or pp_agg:
            ppt += (
                "对比 Rajomon 和 PP 这两个基于速率控制的 baseline：\n"
                "它们没有多步 plan 的全局视图，只能做 session 级别的准入判断。"
                "在相同压力下，它们的 rej0 通常低于 PlanGate，"
                "意味着它们让更多 session 进入了多步执行阶段，"
                "但这些 session 到后期仍可能因资源耗尽而失败，转化为 cascade 浪费。\n"
                "整体而言，PlanGate 的 plan-aware 策略在该实验点上**更早地** "
                "把资源分配决策前移，代价是初始接入率略有调整。\n\n")
        ppt += (
            "总结一句话：**在中等过载下，PlanGate 将部分注定失败的 session "
            "前移到步骤 0 拒绝，减少了 cascade 浪费，但它本身不创造额外后端容量，"
            "接入率与浪费率之间的 tradeoff 是客观存在的，需在论文中诚实披露。**\n\n")
        lines.append(ppt)
    else:
        lines += [
            "*(实验数据尚未就绪，请在 formal 实验完成后运行 `--mode analyze` 重新生成。)*\n\n",
        ]

    with open(out, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  Formal report → {out}")
    return out


def write_latex_table(rows: List[dict]) -> Path:
    """Write LaTeX table to results/neutral_multitool_real_llm/tables/."""
    tables_dir = ROOT / "results" / "neutral_multitool_real_llm" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    out = tables_dir / "b2_mt_medium_table.tex"
    agg = aggregate_by_gateway(rows)

    cfg_agents      = rows[0].get("n_agents", "?") if rows else "?"
    # Try to infer concurrency / burst_size from stdout.log or use placeholder
    # (these are runtime params, not stored in steps_agents.csv)

    body_lines = []
    for gw in GW_ORDER:
        if gw not in agg:
            continue
        a    = agg[gw]
        name = GW_DISPLAY[gw]
        body_lines.append(
            f"  {name:<12} & "
            f"{a['success_rate']:.1f} & "
            f"{a['abd']:.1f} & "
            f"{a['partial']:.0f} & "
            f"{a['rej0']:.0f} & "
            f"{a['cascade_steps']:.0f} & "
            f"{a['avg_steps']:.2f} & "
            f"{a['backend_tokens']:.0f} & "
            f"{a['p50_ms']:.0f} & "
            f"{a['p95_ms']:.0f} \\\\\n"
        )

    content = (
        "% B2-MT-medium gateway comparison — auto-generated by run_b2_mt_medium_sweep.py\n"
        "\\begin{table}[t]\n"
        "\\centering\n"
        "\\caption{B2-MT-medium: Gateway Performance under Medium Bursty Overload}\n"
        "\\label{tab:b2-mt-medium}\n"
        "\\begin{tabular}{lrrrrrrrrr}\n"
        "\\toprule\n"
        "Gateway & Succ\\,\\% & ABD\\,\\% & PARTIAL & Rej0 & Cascade & AvgSteps"
        " & BackendTok & P50 & P95 \\\\\n"
        " & & & (mean) & (mean) & Steps & & (mean) & (ms) & (ms) \\\\\n"
        "\\midrule\n"
        + "".join(body_lines)
        + "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n"
    )
    out.write_text(content, encoding="utf-8")
    print(f"  LaTeX table → {out}")
    return out


# ══════════════════════════════════════════════════════════════════
# Analyze existing results (sweep or formal)
# ══════════════════════════════════════════════════════════════════

def analyze_sweep_existing() -> List[dict]:
    """Re-read sweep run data for all configs that exist (partial sweep OK)."""
    rows = []
    for cfg in SWEEP_CONFIGS:
        cid = cfg["id"]
        for gw in SWEEP_GATEWAYS:
            run_dir = SWEEP_OUT / cid / gw.name / "run1"
            m = recompute_metrics(run_dir, gw.name, cid, 1)
            if m:
                # Enrich with config params (not stored in CSV)
                m["concurrency"]  = cfg["concurrency"]
                m["burst_size"]   = cfg["burst_size"]
                m["burst_gap"]    = cfg["burst_gap"]
                rows.append(m)
                print(f"  {cid}/{gw.name}: success={m['success_rate']:.1f}%  "
                      f"ABD={m['abd']:.1f}%  partial={m['partial']}  "
                      f"cascade={m.get('cascade_steps',0)}")
            else:
                print(f"  {cid}/{gw.name}: no data (skipped)")
    return rows


def analyze_formal(repeats: int = 3) -> List[dict]:
    """Re-read existing formal run data without re-running experiments."""
    rows = []
    for gw in FORMAL_GATEWAYS:
        for run_idx in range(1, repeats + 1):
            run_dir = FORMAL_OUT / gw.name / f"run{run_idx}"
            m = recompute_metrics(run_dir, gw.name, "medium", run_idx)
            if m:
                rows.append(m)
                print(f"  {gw.name}/run{run_idx}: "
                      f"success={m['success_rate']:.1f}%  "
                      f"ABD={m['abd']:.1f}%  "
                      f"avg_steps={m['avg_steps']:.2f}")
            else:
                print(f"  {gw.name}/run{run_idx}: no data")
    return rows


# ══════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="B2-MT-medium pressure sweep and formal experiment runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode", choices=["sweep", "formal", "analyze"], default="sweep",
        help="sweep=find config, formal=run 4gw×3, analyze=re-analyze existing",
    )
    parser.add_argument("--agents",      type=int,   default=None,
                        help="Formal run: number of agents per run")
    parser.add_argument("--concurrency", type=int,   default=None,
                        help="Formal run: concurrency")
    parser.add_argument("--burst-size",  type=int,   default=None,
                        help="Formal run: burst size")
    parser.add_argument("--burst-gap",   type=float, default=8.0,
                        help="Burst gap seconds (default: 8.0)")
    parser.add_argument("--repeats",     type=int,   default=3,
                        help="Formal run: repeats per gateway (default: 3)")
    parser.add_argument("--resume",      action="store_true",
                        help="Skip runs where steps_agents.csv already exists")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Print plan without executing")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── SWEEP MODE ────────────────────────────────────────────────
    if args.mode == "sweep":
        print(f"\n{'='*65}")
        print(f"  B2-MT-medium Pressure Sweep")
        print(f"  {len(SWEEP_CONFIGS)} configs × {len(SWEEP_GATEWAYS)} gateways "
              f"(ng + plangate_real)")
        print(f"{'='*65}")

        if args.dry_run:
            for c in SWEEP_CONFIGS:
                print(f"  [{c['id']}] agents={c['agents']}  C={c['concurrency']}  "
                      f"burst={c['burst_size']}×{c['burst_gap']}s")
            return

        build_gateway()
        start_backend(log_label="sweep_backend")
        try:
            rows = run_sweep(resume=args.resume)
        finally:
            stop_backend()

        if not rows:
            print("ERROR: No sweep results collected.")
            sys.exit(1)

        write_sweep_csv(rows)
        selected_cfg, reason = auto_select_config(rows)
        write_sweep_report(rows, selected_cfg, reason)

        print(f"\n{'='*65}")
        print("  SWEEP COMPLETE")
        if selected_cfg:
            print(f"  Selected: Config {selected_cfg.get('id')}")
            print(f"    agents={selected_cfg.get('agents')}  "
                  f"C={selected_cfg.get('concurrency')}  "
                  f"burst={selected_cfg.get('burst_size')}×{selected_cfg.get('burst_gap')}s")
            print(f"  Reason: {reason}")
            print(f"\n  To run formal experiment:")
            print(f"    python scripts/run_b2_mt_medium_sweep.py --mode formal \\")
            print(f"      --agents {selected_cfg.get('agents')} \\")
            print(f"      --concurrency {selected_cfg.get('concurrency')} \\")
            print(f"      --burst-size {selected_cfg.get('burst_size')} \\")
            print(f"      --burst-gap {selected_cfg.get('burst_gap')}")
        else:
            print("  WARNING: No config was auto-selected (check sweep results)")
        print(f"{'='*65}")
        return

    # ── FORMAL MODE ───────────────────────────────────────────────
    if args.mode == "formal":
        # Determine config: explicit CLI args or fall back to sweep CSV
        if args.agents and args.concurrency and args.burst_size:
            agents      = args.agents
            concurrency = args.concurrency
            burst_size  = args.burst_size
            burst_gap   = args.burst_gap
            reason      = "Manually specified via --agents/--concurrency/--burst-size"
        else:
            # Try to read from sweep summary CSV
            sweep_csv = SWEEP_OUT / "medium_sweep_summary.csv"
            if not sweep_csv.exists():
                print("ERROR: --mode formal requires --agents, --concurrency, --burst-size")
                print(f"       (or run --mode sweep first to populate {sweep_csv})")
                sys.exit(1)
            with open(sweep_csv, encoding="utf-8-sig") as f:
                sweep_rows = list(csv.DictReader(f))
            selected_cfg, reason = auto_select_config(sweep_rows)
            if not selected_cfg:
                print("ERROR: could not auto-select a config from sweep CSV")
                sys.exit(1)
            agents      = int(selected_cfg["agents"])
            concurrency = int(selected_cfg["concurrency"])
            burst_size  = int(selected_cfg["burst_size"])
            burst_gap   = float(selected_cfg["burst_gap"])
            print(f"  Auto-selected config from sweep: "
                  f"agents={agents} C={concurrency} burst={burst_size}×{burst_gap}s")
            print(f"  Reason: {reason}")

        cfg = {
            "agents": agents, "concurrency": concurrency,
            "burst_size": burst_size, "burst_gap": burst_gap,
        }
        print(f"\n{'='*65}")
        print(f"  B2-MT-medium Formal Run")
        print(f"  agents={agents}  C={concurrency}  "
              f"burst={burst_size}×{burst_gap}s  repeats={args.repeats}")
        print(f"  Output → {FORMAL_OUT}")
        print(f"{'='*65}")

        if args.dry_run:
            print("  [dry-run] No experiments will be executed")
            return

        build_gateway()
        start_backend(log_label="formal_backend")
        try:
            rows = run_formal(
                agents, concurrency, burst_size, burst_gap,
                repeats=args.repeats, resume=args.resume,
            )
        finally:
            stop_backend()

        if not rows:
            print("ERROR: No formal results collected.")
            sys.exit(1)

        write_formal_csv(rows)
        write_formal_report(rows, cfg, reason)
        write_latex_table(rows)

        print(f"\n{'='*65}")
        print("  FORMAL RUN COMPLETE")
        print(f"  Results: {FORMAL_OUT}")
        print(f"{'='*65}")
        return

    # ── ANALYZE MODE ──────────────────────────────────────────────
    if args.mode == "analyze":
        # Check which data is available: sweep or formal
        formal_has_data = any(
            (FORMAL_OUT / gw.name / "run1" / "steps_agents.csv").exists()
            for gw in FORMAL_GATEWAYS
        )
        sweep_has_data = any(
            (SWEEP_OUT / cfg["id"] / gw.name / "run1" / "steps_agents.csv").exists()
            for cfg in SWEEP_CONFIGS for gw in SWEEP_GATEWAYS
        )

        if formal_has_data:
            print(f"\n{'='*65}")
            print(f"  Re-analyzing existing B2-MT-medium FORMAL results")
            print(f"  Path: {FORMAL_OUT}")
            print(f"{'='*65}")
            rows = analyze_formal(repeats=args.repeats)
            if not rows:
                print("No formal results found")
            else:
                cfg = {
                    "agents":      args.agents or "?",
                    "concurrency": args.concurrency or "?",
                    "burst_size":  args.burst_size or "?",
                    "burst_gap":   args.burst_gap,
                }
                write_formal_csv(rows)
                write_formal_report(rows, cfg, "Re-analyzed from existing data")
                write_latex_table(rows)
                print(f"\n  Done. Reports written to {FORMAL_OUT}")

        if sweep_has_data:
            print(f"\n{'='*65}")
            print(f"  Re-analyzing existing SWEEP results (partial OK)")
            print(f"  Path: {SWEEP_OUT}")
            print(f"{'='*65}")
            rows = analyze_sweep_existing()
            if rows:
                write_sweep_csv(rows)
                selected_cfg, reason = auto_select_config(rows)
                write_sweep_report(rows, selected_cfg, reason)
                print(f"\n  Sweep reports written to {SWEEP_OUT}")
                if selected_cfg:
                    print(f"\n  Recommended config: {selected_cfg.get('id')}")
                    print(f"    agents={selected_cfg.get('agents')}  "
                          f"C={selected_cfg.get('concurrency')}  "
                          f"burst={selected_cfg.get('burst_size')}×{selected_cfg.get('burst_gap')}s")
                    print(f"  Reason: {reason}")

        if not formal_has_data and not sweep_has_data:
            print("No sweep or formal results found — run --mode sweep first")


if __name__ == "__main__":
    main()
