#!/usr/bin/env python3
"""
run_bursty_extra2.py — 跑 2 次额外 bursty 实验 (run6-7)
避免覆盖已有 run1-5 数据；只跑 ng 和 plangate_real
"""
import argparse
import csv
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
REACT_CLIENT = os.path.join(SCRIPT_DIR, "react_agent_client.py")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "real_llm_bursty")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9400

# 与原始实验完全一致的参数
AGENTS = 200
CONCURRENCY = 20
MAX_STEPS = 15
BUDGET = 1000
BURST_SIZE = 30
BURST_GAP = 8.0

BACKEND_PROC = None

def get_results_dir():
    return os.path.join(ROOT_DIR, "results", f"exp_bursty_C{CONCURRENCY}_B{BURST_SIZE}")

def build_gateway():
    global GATEWAY_BINARY
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)
    print(f"  编译网关: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"编译失败: {result.stderr}")
    GATEWAY_BINARY = bin_path
    print(f"  编译完成: {bin_path}")

def start_backend():
    global BACKEND_PROC
    stop_backend()
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "_backend_bursty_extra.log")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, SERVER_PY,
        "--port", "8080",
        "--mode", "real_llm",
        "--max-workers", "10",
        "--queue-timeout", "8.0",
        "--congestion-factor", "0.5",
    ]
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd, cwd=os.path.dirname(SERVER_PY),
            stdout=lf, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(4)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"后端启动失败, 查看: {log_path}")
    print(f"  后端已启动 (pid={BACKEND_PROC.pid}, mode=real_llm, workers=10)")

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

def start_gateway(name, mode, extra_args, port):
    cmd = [
        GATEWAY_BINARY,
        "--mode", mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", "127.0.0.1",
    ] + extra_args
    log_path = os.path.join(LOG_DIR, f"_gw_{name}_extra.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"网关 {name} 启动失败, 查看: {log_path}")
    print(f"  网关 [{name}] 已启动 (pid={proc.pid}, port={port}, mode={mode})")
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
    result = subprocess.run(
        cmd, cwd=ROOT_DIR, capture_output=True,
        timeout=3600, env=env, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  客户端错误: {result.stderr[:500] if result.stderr else 'N/A'}")
    return result.stdout or ""

def parse_stats(stdout_text):
    import re
    stats = {}
    patterns = {
        "success": r"SUCCESS:\s*(\d+)",
        "partial": r"PARTIAL:\s*(\d+)",
        "all_rejected": r"ALL_REJECTED:\s*(\d+)",
        "error": r"ERROR:\s*(\d+)",
        "cascade_agents": r"级联浪费 Agent:\s*(\d+)",
        "cascade_steps": r"级联浪费步骤:\s*(\d+)",
        "eff_gps": r"Effective GP/s:\s*([0-9.]+)",
        "agent_tokens": r"Agent Brain:\s*([0-9,]+)",
        "backend_tokens": r"Backend LLM:\s*([0-9,]+)",
        "p50_ms": r"P50:\s*([0-9.]+)ms",
        "p95_ms": r"P95:\s*([0-9.]+)ms",
        "elapsed_s": r"总耗时:\s*([0-9.]+)s",
        "http_429": r"429 响应:\s*(\d+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, stdout_text)
        if m:
            val = m.group(1).replace(",", "")
            stats[key] = float(val) if "." in val else int(val)
    
    s = stats.get("success", 0)
    p = stats.get("partial", 0)
    admitted = s + p
    stats["abd"] = round(100 * p / admitted, 1) if admitted > 0 else 0.0
    stats["sr"] = round(100 * s / AGENTS, 1)
    return stats


# Gateway configs (identical to original experiment)
GATEWAYS = {
    "ng": {"mode": "ng", "extra": [], "port": 9400},
    "plangate_real": {
        "mode": "mcpdp-real",
        "extra": [
            "--plangate-price-step", "40",
            "--plangate-max-sessions", "12",
            "--plangate-sunk-cost-alpha", "0.7",
            "--plangate-session-cap-wait", "3",
            "--real-ratelimit-max", "200",
            "--real-latency-threshold", "5000",
        ],
        "port": 9403,
    },
}


def main():
    parser = argparse.ArgumentParser(description="跑 2 次额外 bursty 实验 (run6-7)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start-run", type=int, default=6, help="起始 run 编号")
    parser.add_argument("--count", type=int, default=2, help="额外跑几次")
    args = parser.parse_args()

    results_dir = get_results_dir()
    
    print(f"\n{'='*65}")
    print(f"  Bursty Extra Runs (run{args.start_run}-{args.start_run+args.count-1})")
    print(f"  Gateways: ng, plangate_real")
    print(f"  Config: {AGENTS} agents, C={CONCURRENCY}, burst={BURST_SIZE}x{BURST_GAP}s, steps={MAX_STEPS}")
    print(f"  Results: {results_dir}")
    print(f"{'='*65}")

    if args.dry_run:
        for gw_name, gw_cfg in GATEWAYS.items():
            for run_idx in range(args.start_run, args.start_run + args.count):
                run_dir = os.path.join(results_dir, gw_name, f"run{run_idx}")
                print(f"  [DRY-RUN] {gw_name} run{run_idx} -> {run_dir}")
        print("\n  DRY-RUN 完成，一切就绪。")
        return

    try:
        build_gateway()
        start_backend()

        for run_idx in range(args.start_run, args.start_run + args.count):
            for gw_name, gw_cfg in GATEWAYS.items():
                run_dir = os.path.join(results_dir, gw_name, f"run{run_idx}")
                os.makedirs(run_dir, exist_ok=True)
                output_csv = os.path.join(run_dir, "steps.csv")

                print(f"\n{'='*65}")
                print(f"  [{gw_name}] Bursty Run {run_idx}")
                print(f"  ({AGENTS} agents, C={CONCURRENCY}, burst={BURST_SIZE}x{BURST_GAP}s)")
                print(f"{'='*65}")

                gw_proc = start_gateway(gw_name, gw_cfg["mode"], gw_cfg["extra"], gw_cfg["port"])
                time.sleep(2)

                try:
                    stdout = run_react_client(
                        f"http://127.0.0.1:{gw_cfg['port']}", output_csv, gw_name
                    )
                    stats = parse_stats(stdout)

                    print(f"\n  -- 结果 --")
                    print(f"  success={stats.get('success',0)}, partial={stats.get('partial',0)}, "
                          f"rejected={stats.get('all_rejected',0)}")
                    print(f"  cascade_steps={stats.get('cascade_steps',0)}, "
                          f"cascade_agents={stats.get('cascade_agents',0)}")
                    print(f"  ABD={stats.get('abd',0):.1f}%, SR={stats.get('sr',0):.1f}%")
                    print(f"  P50={stats.get('p50_ms',0):.0f}ms, P95={stats.get('p95_ms',0):.0f}ms")
                    print(f"  429 count: {stats.get('http_429',0)}")
                    print(f"  elapsed: {stats.get('elapsed_s',0):.1f}s")

                    # Save stdout.log
                    with open(os.path.join(run_dir, "stdout.log"), "w", encoding="utf-8") as f:
                        f.write(stdout)

                    # Save steps_summary.csv
                    summary_path = os.path.join(run_dir, "steps_summary.csv")
                    with open(summary_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=[
                            "success", "partial", "all_rejected", "error",
                            "cascade_steps", "cascade_agents",
                            "abd", "sr", "p50_ms", "p95_ms", "elapsed_s",
                            "agent_tokens", "backend_tokens", "http_429",
                        ])
                        writer.writeheader()
                        writer.writerow({
                            "success": stats.get("success", 0),
                            "partial": stats.get("partial", 0),
                            "all_rejected": stats.get("all_rejected", 0),
                            "error": stats.get("error", 0),
                            "cascade_steps": stats.get("cascade_steps", 0),
                            "cascade_agents": stats.get("cascade_agents", 0),
                            "abd": stats.get("abd", 0),
                            "sr": stats.get("sr", 0),
                            "p50_ms": stats.get("p50_ms", 0),
                            "p95_ms": stats.get("p95_ms", 0),
                            "elapsed_s": stats.get("elapsed_s", 0),
                            "agent_tokens": stats.get("agent_tokens", 0),
                            "backend_tokens": stats.get("backend_tokens", 0),
                            "http_429": stats.get("http_429", 0),
                        })

                finally:
                    stop_process(gw_proc)
                    cooldown = 30
                    print(f"  冷却 {cooldown}s (rate limit recovery)...")
                    time.sleep(cooldown)

    except KeyboardInterrupt:
        print("\n  中断! 清理进程...")
    finally:
        stop_backend()

    print(f"\n  额外实验完成! 数据在: {results_dir}")


if __name__ == "__main__":
    main()
