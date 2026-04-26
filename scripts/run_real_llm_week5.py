#!/usr/bin/env python3
"""
run_real_llm_week5.py — Week 5 Real-LLM 大样本实验
====================================================
4 网关 × 5 repeats × 200 sessions (GLM-4-Flash, all ReAct)
  NG, Rajomon (best-case ps=5), PP, PlanGate-Real

指标:
  - ABD (admitted-but-doomed): cascade_wasted_agents / (success + cascade)
  - Success rate, Goodput, E2E latency, Token usage

用法:
  python scripts/run_real_llm_week5.py --dry-run         # 检查配置
  python scripts/run_real_llm_week5.py --repeats 1        # 快速测试
  python scripts/run_real_llm_week5.py --repeats 5        # 正式实验
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
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_week5_real_llm")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "real_llm")


def get_results_dir():
    """Return concurrency-specific results dir."""
    return os.path.join(ROOT_DIR, "results", f"exp_week5_C{CONCURRENCY}")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9300

# ── 实验参数 ──
AGENTS = 200          # 每轮 session 数
CONCURRENCY = 10      # 最大并发 agent 数
MAX_STEPS = 10        # 每 agent 最大步数
BUDGET = 1000         # 每 agent token 预算
ARRIVAL_INTERVAL = 0.3  # agent 启动间隔 (秒)

# ── 调优参数 (与 Week 4 mock 一致) ──
TUNED_PARAMS = {
    "rajomon":  {"price_step": 5},    # best-case from sensitivity scan
    "pp":       {"max_sessions": 150},
    "plangate": {"price_step": 40, "max_sessions": 30, "sunk_cost_alpha": 0.5},
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


# 4 gateways for Week 5 real-LLM
GATEWAYS = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("rajomon", "rajomon", [
        "--rajomon-price-step", str(TUNED_PARAMS["rajomon"]["price_step"]),
    ]),
    GatewayConfig("pp", "pp", [
        "--pp-max-sessions", str(TUNED_PARAMS["pp"]["max_sessions"]),
    ]),
    GatewayConfig("plangate_real", "mcpdp-real", [
        "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
        "--plangate-max-sessions", "50",  # real-LLM: higher cap (sessions last ~30s + 60s TTL)
        "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
        "--plangate-session-cap-wait", "5",  # wait up to 5s for a slot
        "--real-ratelimit-max", "200",
        "--real-latency-threshold", "5000",
    ]),
]

BACKEND_PROC = None


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
    log_path = os.path.join(LOG_DIR, "_backend_week5.log")
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
    print(f"  后端已启动 (pid={BACKEND_PROC.pid}, mode=real_llm)")


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


def start_gateway(gw: GatewayConfig, port: int):
    cmd = [
        GATEWAY_BINARY,
        "--mode", gw.mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", "127.0.0.1",
    ] + gw.extra_args

    log_path = os.path.join(LOG_DIR, f"_gw_{gw.name}_week5.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"网关 {gw.name} 启动失败, 查看: {log_path}")
    print(f"  网关 [{gw.name}] 已启动 (pid={proc.pid}, port={port}, mode={gw.mode})")
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


def run_react_client(gateway_url: str, output_csv: str, gateway_mode: str) -> str:
    """运行 ReAct Agent client，返回 stdout"""
    cmd = [
        sys.executable, REACT_CLIENT,
        "--gateway", gateway_url,
        "--agents", str(AGENTS),
        "--concurrency", str(CONCURRENCY),
        "--max-steps", str(MAX_STEPS),
        "--budget", str(BUDGET),
        "--arrival-interval", str(ARRIVAL_INTERVAL),
        "--gateway-mode", gateway_mode,
        "--output", output_csv,
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    # Real-LLM experiments can be long (200 agents × ~30s each / 10 concurrency ≈ 1600s)
    result = subprocess.run(
        cmd, cwd=ROOT_DIR, capture_output=True,
        timeout=3600, env=env, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(f"  客户端错误: {result.stderr[:500] if result.stderr else 'N/A'}")
    return result.stdout or ""


def parse_react_stats(stdout_text: str) -> dict:
    """从 react_agent_client.py 的 stdout 解析关键指标"""
    stats = {}
    for line in stdout_text.split("\n"):
        line = line.strip()

        if "SUCCESS:" in line and "├" in line:
            try:
                val = line.split("SUCCESS:")[1].strip().split()[0]
                stats["success"] = int(val)
            except (ValueError, IndexError):
                pass
        if "PARTIAL:" in line:
            try:
                val = line.split("PARTIAL:")[1].strip().split()[0]
                stats["partial"] = int(val)
            except (ValueError, IndexError):
                pass
        if "ALL_REJECTED:" in line:
            try:
                val = line.split("ALL_REJECTED:")[1].strip().split()[0]
                stats["all_rejected"] = int(val)
            except (ValueError, IndexError):
                pass
        if "ERROR:" in line and "└" in line:
            try:
                val = line.split("ERROR:")[1].strip().split()[0]
                stats["error"] = int(val)
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
        if "Effective GP:" in line and "GP/s" not in line:
            try:
                stats["eff_gp"] = float(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass

        if "Agent Brain:" in line:
            try:
                val = line.split(":")[1].strip().replace(",", "").split()[0]
                stats["agent_tokens"] = int(val)
            except (ValueError, IndexError):
                pass
        if "Backend LLM:" in line:
            try:
                val = line.split(":")[1].strip().replace(",", "").split()[0]
                stats["backend_tokens"] = int(val)
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
                    if p == "Mean:":
                        stats["mean_ms"] = float(parts[i+1].replace("ms", ""))
            except (ValueError, IndexError):
                pass

        if "总耗时:" in line:
            try:
                stats["elapsed_s"] = float(line.split(":")[-1].strip().replace("s", ""))
            except (ValueError, IndexError):
                pass

    # Compute ABD: PARTIAL / (SUCCESS + PARTIAL)
    # ALL_REJECTED = step-0 rejections (zero cost), not cascade waste
    s = stats.get("success", 0)
    partial = stats.get("partial", 0)
    admitted = s + partial
    stats["abd_total"] = round(100 * partial / admitted, 1) if admitted > 0 else 0.0
    stats["success_rate"] = round(100 * s / AGENTS, 1) if AGENTS > 0 else 0.0

    return stats


def run_experiment(repeats: int, dry_run: bool = False, gateways=None):
    if gateways is None:
        gateways = GATEWAYS
    results_dir = get_results_dir()
    os.makedirs(results_dir, exist_ok=True)
    summary_path = os.path.join(results_dir, "week5_summary.csv")
    summary_rows = []

    for gw in gateways:
        for run_idx in range(1, repeats + 1):
            port = BASE_PORT + GATEWAYS.index(gw)
            gateway_url = f"http://127.0.0.1:{port}"
            run_dir = os.path.join(results_dir, gw.name, f"run{run_idx}")
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")

            print(f"\n{'='*65}")
            print(f"  [{gw.name}] Run {run_idx}/{repeats}  ({AGENTS} agents, C={CONCURRENCY})")
            print(f"{'='*65}")

            if dry_run:
                print(f"  [DRY-RUN] gateway: {gw.mode}, port: {port}")
                print(f"  [DRY-RUN] react_agent_client --agents {AGENTS} --concurrency {CONCURRENCY}")
                print(f"  [DRY-RUN] --budget {BUDGET} --max-steps {MAX_STEPS}")
                print(f"  [DRY-RUN] 输出: {output_csv}")
                continue

            gw_proc = start_gateway(gw, port)
            # Give gateway a bit more time to stabilize for real-LLM
            time.sleep(2)

            try:
                stdout = run_react_client(gateway_url, output_csv, gw.name)
                stats = parse_react_stats(stdout)

                row = {
                    "gateway": gw.name,
                    "run": run_idx,
                    "agents": AGENTS,
                    "success": stats.get("success", 0),
                    "partial": stats.get("partial", 0),
                    "all_rejected": stats.get("all_rejected", 0),
                    "error": stats.get("error", 0),
                    "cascade_agents": stats.get("cascade_agents", 0),
                    "cascade_steps": stats.get("cascade_steps", 0),
                    "success_rate": stats.get("success_rate", 0),
                    "abd_total": stats.get("abd_total", 0),
                    "eff_gps": stats.get("eff_gps", 0),
                    "agent_tokens": stats.get("agent_tokens", 0),
                    "backend_tokens": stats.get("backend_tokens", 0),
                    "p50_ms": stats.get("p50_ms", 0),
                    "p95_ms": stats.get("p95_ms", 0),
                    "elapsed_s": stats.get("elapsed_s", 0),
                }
                summary_rows.append(row)

                print(f"\n  ── 结果 ──")
                print(f"  success={row['success']}, partial={row['partial']}, "
                      f"rejected={row['all_rejected']}, error={row['error']}")
                print(f"  success_rate={row['success_rate']}%  ABD={row['abd_total']:.1f}%")
                print(f"  eff_GP/s={row['eff_gps']:.2f}")
                print(f"  P50={row['p50_ms']:.0f}ms  P95={row['p95_ms']:.0f}ms")
                print(f"  tokens: agent={row['agent_tokens']:,}  backend={row['backend_tokens']:,}")
                print(f"  elapsed: {row['elapsed_s']:.1f}s")

            finally:
                stop_process(gw_proc)
                # Longer cooldown between runs to let API rate limits reset
                cooldown = 30 if run_idx < repeats else 10
                print(f"  冷却 {cooldown}s (rate limit recovery)...")
                time.sleep(cooldown)

    # 写汇总 CSV
    if summary_rows:
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\n  汇总CSV: {summary_path}")
        print_summary_table(summary_rows, repeats)


def print_summary_table(rows, repeats):
    """打印汇总表"""
    import statistics

    print(f"\n{'='*90}")
    print("  Week 5 Real-LLM — GLM-4-Flash 200 sessions × N repeats")
    print(f"{'='*90}")
    print(f"{'Gateway':<18} {'SuccRate%':>10} {'ABD%':>10} {'GP/s':>10} {'P50ms':>10} {'P95ms':>10} {'Tokens':>12}")
    print(f"{'-'*18} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")

    gw_stats = {}
    for row in rows:
        gw = row["gateway"]
        if gw not in gw_stats:
            gw_stats[gw] = {"sr": [], "abd": [], "gps": [], "p50": [], "p95": [], "tok": []}
        gw_stats[gw]["sr"].append(row["success_rate"])
        gw_stats[gw]["abd"].append(row["abd_total"])
        gw_stats[gw]["gps"].append(row["eff_gps"])
        gw_stats[gw]["p50"].append(row["p50_ms"])
        gw_stats[gw]["p95"].append(row["p95_ms"])
        gw_stats[gw]["tok"].append(row["agent_tokens"] + row["backend_tokens"])

    gw_order = [g.name for g in GATEWAYS]
    for gw in gw_order:
        if gw not in gw_stats:
            continue
        s = gw_stats[gw]
        sr_mean = statistics.mean(s["sr"])
        abd_mean = statistics.mean(s["abd"])
        gps_mean = statistics.mean(s["gps"])
        p50_mean = statistics.mean(s["p50"])
        p95_mean = statistics.mean(s["p95"])
        tok_mean = statistics.mean(s["tok"])

        sr_std = statistics.stdev(s["sr"]) if len(s["sr"]) > 1 else 0
        abd_std = statistics.stdev(s["abd"]) if len(s["abd"]) > 1 else 0
        gps_std = statistics.stdev(s["gps"]) if len(s["gps"]) > 1 else 0

        print(f"{gw:<18} {sr_mean:>7.1f}±{sr_std:<4.1f} {abd_mean:>7.1f}±{abd_std:<4.1f} "
              f"{gps_mean:>7.2f}±{gps_std:<5.2f} {p50_mean:>9.0f} {p95_mean:>9.0f} "
              f"{tok_mean:>11,.0f}")

    # PlanGate 对比
    if "plangate_real" in gw_stats and "ng" in gw_stats:
        pg = gw_stats["plangate_real"]
        ng = gw_stats["ng"]
        pg_abd = statistics.mean(pg["abd"])
        ng_abd = statistics.mean(ng["abd"])
        pg_sr = statistics.mean(pg["sr"])
        ng_sr = statistics.mean(ng["sr"])

        print(f"\n  === Commitment Quality 对比 ===")
        print(f"  PlanGate ABD:     {pg_abd:.1f}%")
        print(f"  NG ABD:           {ng_abd:.1f}%")
        print(f"  ABD 降低:         {ng_abd - pg_abd:.1f} pp")
        print(f"  Success Rate:     PlanGate {pg_sr:.1f}% vs NG {ng_sr:.1f}% (Δ={pg_sr-ng_sr:+.1f}pp)")

    print(f"\n  数据目录: {get_results_dir()}")


def main():
    parser = argparse.ArgumentParser(description="Week 5 Real-LLM 实验 (GLM-4-Flash)")
    parser.add_argument("--repeats", type=int, default=5, help="重复次数 (默认 5)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="覆盖默认并发度 (默认使用 CONCURRENCY 常量)")
    parser.add_argument("--dry-run", action="store_true", help="仅打印配置，不执行")
    parser.add_argument("--skip-build", action="store_true", help="跳过网关编译")
    parser.add_argument("--gateways", nargs="*", help="只运行指定的网关 (如: pp plangate_real)")
    args = parser.parse_args()

    # Override concurrency if specified
    if args.concurrency is not None:
        global CONCURRENCY
        CONCURRENCY = args.concurrency
        # Scale PlanGate max_sessions with concurrency
        pg_max = max(50, args.concurrency * 3)
        for gw in GATEWAYS:
            if gw.name == "plangate_real":
                gw.extra_args = [
                    "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
                    "--plangate-max-sessions", str(pg_max),
                    "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
                    "--plangate-session-cap-wait", "5",
                    "--real-ratelimit-max", "200",
                    "--real-latency-threshold", "5000",
                ]

    # 过滤 gateway 列表
    run_gateways = GATEWAYS
    if args.gateways:
        run_gateways = [g for g in GATEWAYS if g.name in args.gateways]
        if not run_gateways:
            print(f"  ⚠ 未找到匹配的网关: {args.gateways}")
            print(f"  可用: {[g.name for g in GATEWAYS]}")
            return

    print(f"\n{'='*65}")
    print("  Week 5 Real-LLM 实验 — GLM-4-Flash")
    print(f"{'='*65}")
    print(f"  Sessions:     {AGENTS}")
    print(f"  Concurrency:  {CONCURRENCY}")
    print(f"  Max Steps:    {MAX_STEPS}")
    print(f"  Budget:       {BUDGET}")
    print(f"  Repeats:      {args.repeats}")
    print(f"  Gateways:     {', '.join(g.name for g in run_gateways)}")
    print(f"  结果目录:     {get_results_dir()}")

    if not args.dry_run:
        # 检查 API 配置
        _check_env()
        # 编译网关
        if not args.skip_build:
            build_gateway()
        else:
            global GATEWAY_BINARY
            bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
            GATEWAY_BINARY = os.path.join(ROOT_DIR, bin_name)
            print(f"  跳过编译, 使用: {GATEWAY_BINARY}")
        # 启动后端
        start_backend()

    try:
        run_experiment(args.repeats, dry_run=args.dry_run, gateways=run_gateways)
    finally:
        if not args.dry_run:
            stop_backend()
            print("\n  后端已停止。")

    print(f"\n  ✓ 实验完成！")


def _check_env():
    """检查 API 配置是否就绪"""
    # 加载 .env
    env_path = os.path.join(ROOT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip()
                    if value and len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    if key and value and key not in os.environ:
                        os.environ[key] = value
        print(f"  .env 已加载: {env_path}")

    llm_base = os.environ.get("AGENT_LLM_BASE", os.environ.get("LLM_API_BASE", ""))
    llm_key = os.environ.get("AGENT_LLM_KEY", os.environ.get("LLM_API_KEY", ""))
    llm_model = os.environ.get("AGENT_LLM_MODEL", os.environ.get("LLM_MODEL", ""))

    print(f"  LLM Base:  {llm_base[:50]}..." if len(llm_base) > 50 else f"  LLM Base:  {llm_base}")
    print(f"  LLM Model: {llm_model}")
    print(f"  LLM Key:   {'***已配置***' if llm_key else '⚠ 未配置!'}")

    if not llm_key:
        raise RuntimeError("LLM API Key 未配置! 请设置 AGENT_LLM_KEY 或 LLM_API_KEY 环境变量")


if __name__ == "__main__":
    main()
