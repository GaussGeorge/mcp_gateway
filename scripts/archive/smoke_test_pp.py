#!/usr/bin/env python3
"""
smoke_test_pp.py — Week 1 PP 基线冒烟测试
==========================================
运行 4 个网关 (NG, SBAC, PP, PlanGate) × 5 runs × 200 sessions
对照核心指标: success_rate, cascade_failures, admitted-but-doomed rate

用法:
  python scripts/smoke_test_pp.py
  python scripts/smoke_test_pp.py --repeats 3      # 快速测试只跑 3 轮
  python scripts/smoke_test_pp.py --dry-run         # 只打印命令不实际执行
"""

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_pp_smoke")
MOCK_LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "mock")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9200

# 调优后的参数
TUNED_PARAMS = {
    "sbac":  {"max_sessions": 150},
    "pp":    {"max_sessions": 150},  # 与 SBAC 相同的 maxSessions，确保公平对比
    "plangate": {"price_step": 40, "max_sessions": 30, "sunk_cost_alpha": 0.5},
}

# 发压参数
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


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


GATEWAYS = [
    GatewayConfig("ng", "ng"),
    GatewayConfig("sbac", "sbac", [
        "--sbac-max-sessions", str(TUNED_PARAMS["sbac"]["max_sessions"]),
    ]),
    GatewayConfig("pp", "pp", [
        "--pp-max-sessions", str(TUNED_PARAMS["pp"]["max_sessions"]),
    ]),
    GatewayConfig("plangate_full", "mcpdp", [
        "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
        "--plangate-max-sessions", str(TUNED_PARAMS["plangate"]["max_sessions"]),
        "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
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
    os.makedirs(MOCK_LOG_DIR, exist_ok=True)
    log_path = os.path.join(MOCK_LOG_DIR, "_backend_pp_smoke.log")
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
        raise RuntimeError(f"后端启动失败, 查看: {log_path}")
    print(f"  后端已启动 (pid={BACKEND_PROC.pid})")


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

    log_path = os.path.join(MOCK_LOG_DIR, f"_gw_{gw.name}_pp_smoke.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"网关 {gw.name} 启动失败, 查看: {log_path}")
    print(f"  网关 [{gw.name}] 已启动 (pid={proc.pid}, port={port})")
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


def run_load_gen(target_url: str, output_csv: str, session_csv: str):
    """运行发压机并返回 session 级别结果"""
    cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target", target_url,
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
        print(f"  发压机错误: {result.stderr[:500] if result.stderr else 'N/A'}")
    return result.stdout or ""


def parse_session_stats(stdout_text: str) -> dict:
    """从发压机 stdout 中解析关键指标"""
    stats = {}
    for line in stdout_text.split("\n"):
        line = line.strip()
        # 解析: "  ├─ SUCCESS:      123  (45.6%)"
        if "SUCCESS:" in line and "├" in line:
            try:
                val = line.split("SUCCESS:")[1].strip().split()[0]
                stats["success"] = int(val)
            except (ValueError, IndexError):
                pass
        # 解析: "  ├─ CASCADE_FAIL: 45  (12.3%)"
        if "CASCADE_FAIL:" in line:
            try:
                val = line.split("CASCADE_FAIL:")[1].strip().split()[0]
                stats["cascade_failed"] = int(val)
            except (ValueError, IndexError):
                pass
        # 解析: "  ├─ REJECTED@S0:  67  (23.4%)"
        if "REJECTED@S0:" in line:
            try:
                val = line.split("REJECTED@S0:")[1].strip().split()[0]
                stats["rejected_s0"] = int(val)
            except (ValueError, IndexError):
                pass
        # 解析: "  Effective Goodput/s:          12.34"
        if "Effective Goodput/s:" in line:
            try:
                val = line.split(":")[-1].strip()
                stats["goodput"] = float(val)
            except (ValueError, IndexError):
                pass

    # 计算 success_rate
    total = stats.get("success", 0) + stats.get("cascade_failed", 0) + stats.get("rejected_s0", 0)
    if total > 0:
        stats["success_rate"] = round(100 * stats.get("success", 0) / total, 1)
    else:
        stats["success_rate"] = 0
    return stats


def run_experiment(repeats: int, dry_run: bool = False):
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # 汇总结果
    summary_path = os.path.join(RESULTS_DIR, "pp_smoke_summary.csv")
    summary_rows = []

    for gw in GATEWAYS:
        for run_idx in range(1, repeats + 1):
            port = BASE_PORT + GATEWAYS.index(gw)
            target_url = f"http://127.0.0.1:{port}"
            run_dir = os.path.join(RESULTS_DIR, gw.name, f"run{run_idx}")
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")
            session_csv = os.path.join(run_dir, "sessions.csv")

            print(f"\n{'='*60}")
            print(f"  [{gw.name}] Run {run_idx}/{repeats}")
            print(f"{'='*60}")

            if dry_run:
                print(f"  [DRY-RUN] 跳过实际执行")
                continue

            # 启动网关
            gw_proc = start_gateway(gw, port)

            try:
                # 运行发压
                stdout = run_load_gen(target_url, output_csv, session_csv)
                stats = parse_session_stats(stdout)

                # 计算 admitted-but-doomed rate
                success = stats.get("success", 0)
                cascade = stats.get("cascade_failed", 0)
                rejected = stats.get("rejected_s0", 0)
                admitted = success + cascade
                abd_rate = cascade / admitted * 100 if admitted > 0 else 0

                row = {
                    "gateway": gw.name,
                    "run": run_idx,
                    "success": success,
                    "cascade_failed": cascade,
                    "rejected_s0": rejected,
                    "success_rate": stats.get("success_rate", 0),
                    "admitted_but_doomed_pct": round(abd_rate, 2),
                    "goodput": stats.get("goodput", 0),
                }
                summary_rows.append(row)

                print(f"  结果: success={success}, cascade={cascade}, rejected_s0={rejected}")
                print(f"         success_rate={stats.get('success_rate', '?')}%")
                print(f"         admitted-but-doomed={abd_rate:.1f}%")
                print(f"         goodput={stats.get('goodput', '?')} GP/s")

            finally:
                stop_process(gw_proc)
                time.sleep(1)

    # 写汇总 CSV
    if summary_rows:
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\n{'='*60}")
        print(f"  汇总CSV: {summary_path}")
        print(f"{'='*60}")

        # 打印汇总表格
        print_summary_table(summary_rows, repeats)


def print_summary_table(rows, repeats):
    """打印最终汇总对照表"""
    import statistics

    print(f"\n{'='*80}")
    print("  PP Smoke Test 汇总 — PP 判定门槛评估")
    print(f"{'='*80}")
    print(f"{'Gateway':<18} {'SuccRate%':>10} {'Cascade':>10} {'ABD%':>10} {'GP/s':>10}")
    print(f"{'-'*18} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    gw_stats = {}
    for row in rows:
        gw = row["gateway"]
        if gw not in gw_stats:
            gw_stats[gw] = {"sr": [], "cf": [], "abd": [], "gp": []}
        gw_stats[gw]["sr"].append(row["success_rate"])
        gw_stats[gw]["cf"].append(row["cascade_failed"])
        gw_stats[gw]["abd"].append(row["admitted_but_doomed_pct"])
        gw_stats[gw]["gp"].append(row["goodput"])

    for gw in ["ng", "sbac", "pp", "plangate_full"]:
        if gw not in gw_stats:
            continue
        s = gw_stats[gw]
        sr_mean = statistics.mean(s["sr"]) if s["sr"] else 0
        cf_mean = statistics.mean(s["cf"]) if s["cf"] else 0
        abd_mean = statistics.mean(s["abd"]) if s["abd"] else 0
        gp_mean = statistics.mean(s["gp"]) if s["gp"] else 0
        print(f"{gw:<18} {sr_mean:>9.1f}% {cf_mean:>10.1f} {abd_mean:>9.1f}% {gp_mean:>10.1f}")

    # PP 判定门槛评估
    if "pp" in gw_stats and "plangate_full" in gw_stats:
        pp_sr = statistics.mean(gw_stats["pp"]["sr"])
        pg_sr = statistics.mean(gw_stats["plangate_full"]["sr"])
        pp_cf = statistics.mean(gw_stats["pp"]["cf"])
        pg_cf = statistics.mean(gw_stats["plangate_full"]["cf"])
        pp_abd = statistics.mean(gw_stats["pp"]["abd"])
        pg_abd = statistics.mean(gw_stats["plangate_full"]["abd"])

        sr_gap = pg_sr - pp_sr
        cf_gap = pp_cf - pg_cf
        abd_gap = pp_abd - pg_abd

        print(f"\n  === PP 判定门槛 ===")
        print(f"  Success Rate 差: {sr_gap:+.1f}pp (PlanGate - PP)")
        print(f"  Cascade 差:      {cf_gap:+.1f} (PP - PlanGate)")
        print(f"  ABD% 差:         {abd_gap:+.1f}pp (PP - PlanGate)")

        if sr_gap > 10 and cf_gap > 10 and abd_gap > 15:
            print(f"\n  🟢 绿灯: 当前主线最稳，继续推进")
        elif abd_gap > 15:
            print(f"\n  🟡 黄灯: 主线可行，论文重心转向 commitment quality")
        elif sr_gap < 5 and cf_gap < 5:
            print(f"\n  🔴 红灯: 立即停止当前主线，重新定位贡献")
        else:
            print(f"\n  🟡 黄灯: 差距中等，需进一步实验确认")


def main():
    parser = argparse.ArgumentParser(description="PP Baseline Smoke Test")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  PP Baseline Smoke Test — Week 1 决策门验证")
    print("=" * 60)

    try:
        # 1. 编译网关
        build_gateway()

        # 2. 启动后端
        start_backend()

        # 3. 运行实验
        run_experiment(args.repeats, args.dry_run)

    except KeyboardInterrupt:
        print("\n  用户中断")
    finally:
        stop_backend()

    print("\n  完成!")


if __name__ == "__main__":
    main()
