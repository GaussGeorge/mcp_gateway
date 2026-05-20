#!/usr/bin/env python3
"""
run_real_llm_bursty.py — Bursty Real-LLM 实验
================================================
CCF-A 朋友建议: "不要先加 repeats，要先改 workload"
  - 增强 burstiness: 分批瞬时投放 (burst_size=30, burst_gap=8s)
  - 压缩 think time: arrival_interval=0 (burst 模式内无间隔)
  - 更长会话: max_steps=15 (denser tool chains)
  - 记录 429/rate-limit 信号

设计理念:
  商业 API 更容易被瞬时峰值打到 quota edge，而不是被稳态并发打穿。
  PlanGate 的 plan-aware admission 在突发负载下可以更早拒绝无望请求，
  减少 cascade waste，保持 tail latency 一致性。

用法:
  python scripts/run_real_llm_bursty.py --dry-run         # 检查配置
  python scripts/run_real_llm_bursty.py --repeats 1        # 快速测试
  python scripts/run_real_llm_bursty.py --repeats 3        # 正式实验
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
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "real_llm_bursty")

BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9400  # 与 week5 错开 (week5=9300)

# ══════════════════════════════════════════════════
# 实验参数 — Bursty Workload
# ══════════════════════════════════════════════════
AGENTS = 200          # 每轮 session 数
CONCURRENCY = 20      # 提高并发 (10 → 20) 加大瞬时争抢
MAX_STEPS = 15        # 更长会话 (10 → 15) 让准入价值更大
BUDGET = 1000         # 每 agent token 预算
BURST_SIZE = 30       # 每批瞬时投放 agent 数
BURST_GAP = 8.0       # 批次间等待秒数
# → 200 agents / 30 per batch ≈ 7 batches, 7 × 8s = 56s dispatch time
# → 每批 30 agents 争抢 20 concurrency slots → 10 立即排队
# → 峰值: 20 concurrent agents × ~2 tool calls/agent/sec → 40 tool calls/s
#   backend max_workers=10 → 队列溢出 → 触发准入控制差异

# ── 调优参数 (与 Week 5 一致)
TUNED_PARAMS = {
    "rajomon":  {"price_step": 5},
    "pp":       {"max_sessions": 150},
    "plangate": {"price_step": 40, "max_sessions": 50, "sunk_cost_alpha": 0.5},
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


# 4 gateways
GATEWAYS = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("rajomon", "rajomon", [
        "--rajomon-price-step", str(TUNED_PARAMS["rajomon"]["price_step"]),
    ]),
    GatewayConfig("pp", "pp", [
        "--pp-max-sessions", "20",  # bursty: 匹配 C=20 并发
    ]),
    GatewayConfig("plangate_real", "mcpdp-real", [
        "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
        "--plangate-max-sessions", "12",  # bursty: 略超 backend 10 workers
        "--plangate-sunk-cost-alpha", "0.7",  # 更强沉没成本保护
        "--plangate-session-cap-wait", "3",  # 短排队而非即时拒绝
        "--real-ratelimit-max", "200",
        "--real-latency-threshold", "5000",
    ]),
]

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
    log_path = os.path.join(LOG_DIR, "_backend_bursty.log")
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

    log_path = os.path.join(LOG_DIR, f"_gw_{gw.name}_bursty.log")
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
    """运行 ReAct Agent client (bursty mode)，返回 stdout"""
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

    # Bursty experiments: higher concurrency but same total agents → can be faster per run
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

        # ★ 新增: 429 信号解析
        if "429 响应:" in line:
            try:
                parts = line.split("429 响应:")[1].strip().split("/")
                stats["http_429_count"] = int(parts[0].strip())
            except (ValueError, IndexError):
                pass

    # Compute ABD
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
    summary_path = os.path.join(results_dir, "bursty_summary.csv")
    summary_rows = []

    for gw in gateways:
        for run_idx in range(1, repeats + 1):
            port = BASE_PORT + GATEWAYS.index(gw)
            gateway_url = f"http://127.0.0.1:{port}"
            run_dir = os.path.join(results_dir, gw.name, f"run{run_idx}")
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")

            print(f"\n{'='*65}")
            print(f"  [{gw.name}] Bursty Run {run_idx}/{repeats}")
            print(f"  ({AGENTS} agents, C={CONCURRENCY}, burst={BURST_SIZE}×{BURST_GAP}s, steps={MAX_STEPS})")
            print(f"{'='*65}")

            if dry_run:
                print(f"  [DRY-RUN] gateway: {gw.mode}, port: {port}")
                print(f"  [DRY-RUN] react_agent_client --agents {AGENTS} --concurrency {CONCURRENCY}")
                print(f"  [DRY-RUN] --burst-size {BURST_SIZE} --burst-gap {BURST_GAP}")
                print(f"  [DRY-RUN] --max-steps {MAX_STEPS} --budget {BUDGET}")
                print(f"  [DRY-RUN] 输出: {output_csv}")
                continue

            gw_proc = start_gateway(gw, port)
            time.sleep(2)

            try:
                stdout = run_react_client(gateway_url, output_csv, gw.name)
                stats = parse_react_stats(stdout)

                row = {
                    "gateway": gw.name,
                    "run": run_idx,
                    "agents": AGENTS,
                    "concurrency": CONCURRENCY,
                    "burst_size": BURST_SIZE,
                    "burst_gap": BURST_GAP,
                    "max_steps": MAX_STEPS,
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
                    "http_429_count": stats.get("http_429_count", 0),
                }
                summary_rows.append(row)

                print(f"\n  ── 结果 ──")
                print(f"  success={row['success']}, partial={row['partial']}, "
                      f"rejected={row['all_rejected']}, error={row['error']}")
                print(f"  success_rate={row['success_rate']}%  ABD={row['abd_total']:.1f}%")
                print(f"  eff_GP/s={row['eff_gps']:.2f}")
                print(f"  P50={row['p50_ms']:.0f}ms  P95={row['p95_ms']:.0f}ms")
                print(f"  tokens: agent={row['agent_tokens']:,}  backend={row['backend_tokens']:,}")
                print(f"  429 count: {row['http_429_count']}")
                print(f"  elapsed: {row['elapsed_s']:.1f}s")

                # 保存 stdout 完整日志
                stdout_log = os.path.join(run_dir, "stdout.log")
                with open(stdout_log, "w", encoding="utf-8") as f:
                    f.write(stdout)

            finally:
                stop_process(gw_proc)
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

    print(f"\n{'='*95}")
    print(f"  Bursty Real-LLM — GLM-4-Flash {AGENTS} sessions")
    print(f"  C={CONCURRENCY}, burst={BURST_SIZE}×{BURST_GAP}s, steps={MAX_STEPS}")
    print(f"{'='*95}")
    print(f"{'Gateway':<18} {'SuccRate%':>10} {'ABD%':>10} {'GP/s':>10} "
          f"{'P50ms':>10} {'P95ms':>10} {'429s':>6} {'Tokens':>12}")
    print(f"{'-'*18} {'-'*10} {'-'*10} {'-'*10} "
          f"{'-'*10} {'-'*10} {'-'*6} {'-'*12}")

    gw_stats = {}
    for row in rows:
        gw = row["gateway"]
        if gw not in gw_stats:
            gw_stats[gw] = {"sr": [], "abd": [], "gps": [], "p50": [], "p95": [],
                            "tok": [], "h429": []}
        gw_stats[gw]["sr"].append(row["success_rate"])
        gw_stats[gw]["abd"].append(row["abd_total"])
        gw_stats[gw]["gps"].append(row["eff_gps"])
        gw_stats[gw]["p50"].append(row["p50_ms"])
        gw_stats[gw]["p95"].append(row["p95_ms"])
        gw_stats[gw]["tok"].append(row["agent_tokens"] + row["backend_tokens"])
        gw_stats[gw]["h429"].append(row["http_429_count"])

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
        h429_mean = statistics.mean(s["h429"])

        sr_std = statistics.stdev(s["sr"]) if len(s["sr"]) > 1 else 0
        abd_std = statistics.stdev(s["abd"]) if len(s["abd"]) > 1 else 0
        gps_std = statistics.stdev(s["gps"]) if len(s["gps"]) > 1 else 0

        print(f"{gw:<18} {sr_mean:>7.1f}±{sr_std:<4.1f} {abd_mean:>7.1f}±{abd_std:<4.1f} "
              f"{gps_mean:>7.2f}±{gps_std:<5.2f} {p50_mean:>9.0f} {p95_mean:>9.0f} "
              f"{h429_mean:>5.0f} {tok_mean:>11,.0f}")

    # PlanGate 对比
    if "plangate_real" in gw_stats and "ng" in gw_stats:
        pg = gw_stats["plangate_real"]
        ng = gw_stats["ng"]
        pg_abd = statistics.mean(pg["abd"])
        ng_abd = statistics.mean(ng["abd"])
        pg_sr = statistics.mean(pg["sr"])
        ng_sr = statistics.mean(ng["sr"])
        pg_p95 = statistics.mean(pg["p95"])
        ng_p95 = statistics.mean(ng["p95"])

        print(f"\n  === Bursty Commitment Quality 对比 ===")
        print(f"  PlanGate ABD:     {pg_abd:.1f}%")
        print(f"  NG ABD:           {ng_abd:.1f}%")
        print(f"  ABD 降低:         {ng_abd - pg_abd:.1f} pp")
        print(f"  Success Rate:     PlanGate {pg_sr:.1f}% vs NG {ng_sr:.1f}% (Δ={pg_sr-ng_sr:+.1f}pp)")
        print(f"  P95 Latency:      PlanGate {pg_p95:.0f}ms vs NG {ng_p95:.0f}ms")

    print(f"\n  数据目录: {get_results_dir()}")


def main():
    global CONCURRENCY, BURST_SIZE, BURST_GAP, MAX_STEPS

    parser = argparse.ArgumentParser(
        description="Bursty Real-LLM 实验 — 突发负载下的准入控制对比",
    )
    parser.add_argument("--repeats", type=int, default=3,
                        help="每网关重复次数 (default: 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印配置不执行")
    parser.add_argument("--gateways", nargs="+", default=None,
                        help="指定运行的网关 (如: ng plangate_real)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help=f"覆盖并发数 (default: {CONCURRENCY})")
    parser.add_argument("--burst-size", type=int, default=None,
                        help=f"覆盖每批投放数 (default: {BURST_SIZE})")
    parser.add_argument("--burst-gap", type=float, default=None,
                        help=f"覆盖批次间距 (default: {BURST_GAP})")
    parser.add_argument("--max-steps", type=int, default=None,
                        help=f"覆盖最大步数 (default: {MAX_STEPS})")
    args = parser.parse_args()

    # 允许命令行覆盖参数
    if args.concurrency is not None:
        CONCURRENCY = args.concurrency
    if args.burst_size is not None:
        BURST_SIZE = args.burst_size
    if args.burst_gap is not None:
        BURST_GAP = args.burst_gap
    if args.max_steps is not None:
        MAX_STEPS = args.max_steps

    # 筛选网关
    selected_gateways = GATEWAYS
    if args.gateways:
        selected_gateways = [gw for gw in GATEWAYS if gw.name in args.gateways]
        if not selected_gateways:
            print(f"错误: 未找到指定网关 {args.gateways}")
            print(f"可用: {[gw.name for gw in GATEWAYS]}")
            sys.exit(1)

    print(f"\n{'='*65}")
    print(f"  Bursty Real-LLM 实验")
    print(f"{'='*65}")
    print(f"  Agents:     {AGENTS}")
    print(f"  Concurrency:{CONCURRENCY}")
    print(f"  Burst:      {BURST_SIZE} agents × {BURST_GAP}s gap")
    print(f"  Max Steps:  {MAX_STEPS}")
    print(f"  Budget:     {BUDGET}")
    print(f"  Repeats:    {args.repeats}")
    print(f"  Gateways:   {[gw.name for gw in selected_gateways]}")
    print(f"  Results:    {get_results_dir()}")
    print(f"{'='*65}")

    try:
        build_gateway()
        start_backend()
        run_experiment(args.repeats, dry_run=args.dry_run, gateways=selected_gateways)
    except KeyboardInterrupt:
        print("\n  中断! 清理进程...")
    finally:
        stop_backend()

    print("\n  Bursty 实验完成!")


if __name__ == "__main__":
    main()
