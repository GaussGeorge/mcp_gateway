#!/usr/bin/env python3
"""
smoke_test_week2.py — Week 2 扩展冒烟测试
==========================================
运行 7 个网关 × 5 runs × 200 sessions，含 mode-stratified ABD 分析:
  NG, Rajomon, Rajomon+SB, SBAC, PP, PG-noRes, PlanGate

核心验证:
  1. ABD 证据链: 所有基线 ABD 都高，仅 PlanGate 显著低
  2. ABD_P&S ≈ 0 (PlanGate, 硬承诺), ABD_ReAct 显著低于基线 (软承诺)
  3. GP/s 与 ABD 趋势一致

用法:
  python scripts/smoke_test_week2.py
  python scripts/smoke_test_week2.py --repeats 3      # 快速测试
  python scripts/smoke_test_week2.py --dry-run         # 只打印命令
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_week4_formal")
MOCK_LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "mock")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9200

# 调优后的参数
TUNED_PARAMS = {
    "sbac":     {"max_sessions": 150},
    "pp":       {"max_sessions": 150},
    "rajomon":  {"price_step": 5},      # Week 4: best-case from sensitivity scan
    "rajomon_sb": {"price_step": 5},    # Week 4: best-case from sensitivity scan
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
    GatewayConfig("rajomon", "rajomon", [
        "--rajomon-price-step", str(TUNED_PARAMS["rajomon"]["price_step"]),
    ]),
    GatewayConfig("rajomon_sb", "rajomon-session", [
        "--rajomon-sb-price-step", str(TUNED_PARAMS["rajomon_sb"]["price_step"]),
    ]),
    GatewayConfig("sbac", "sbac", [
        "--sbac-max-sessions", str(TUNED_PARAMS["sbac"]["max_sessions"]),
    ]),
    GatewayConfig("pp", "pp", [
        "--pp-max-sessions", str(TUNED_PARAMS["pp"]["max_sessions"]),
    ]),
    GatewayConfig("pg_nores", "mcpdp-no-budgetlock", [
        "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
        "--plangate-max-sessions", str(TUNED_PARAMS["plangate"]["max_sessions"]),
        "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
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
    log_path = os.path.join(MOCK_LOG_DIR, "_backend_week2_smoke.log")
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

    log_path = os.path.join(MOCK_LOG_DIR, f"_gw_{gw.name}_week2_smoke.log")
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


def run_load_gen(target_url: str, output_csv: str):
    """运行发压机并返回 stdout"""
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
    """从发压机 stdout 解析关键指标，含 mode-stratified ABD"""
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
        # Mode-stratified ABD
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
        # Per-mode stats
        if "[Plan-and-Solve]" in line:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p.startswith("success="):
                        stats["ps_success"] = int(p.split("=")[1])
                    elif p.startswith("cascade="):
                        stats["ps_cascade"] = int(p.split("=")[1])
                    elif p.startswith("total="):
                        stats["ps_total"] = int(p.split("=")[1])
            except (ValueError, IndexError):
                pass
        if "[ReAct]" in line and "total=" in line:
            try:
                parts = line.split()
                for p in parts:
                    if p.startswith("success="):
                        stats["react_success"] = int(p.split("=")[1])
                    elif p.startswith("cascade="):
                        stats["react_cascade"] = int(p.split("=")[1])
                    elif p.startswith("total="):
                        stats["react_total"] = int(p.split("=")[1])
            except (ValueError, IndexError):
                pass

    # 计算 success_rate
    total = stats.get("success", 0) + stats.get("cascade_failed", 0) + stats.get("rejected_s0", 0)
    if total > 0:
        stats["success_rate"] = round(100 * stats.get("success", 0) / total, 1)
    else:
        stats["success_rate"] = 0

    # 如果 stdout 未输出 abd_ 字段, 从原始数据计算
    if "abd_total" not in stats:
        s = stats.get("success", 0)
        c = stats.get("cascade_failed", 0)
        admitted = s + c
        stats["abd_total"] = round(100 * c / admitted, 1) if admitted > 0 else 0

    return stats


def run_experiment(repeats: int, dry_run: bool = False):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    summary_path = os.path.join(RESULTS_DIR, "week2_smoke_summary.csv")
    summary_rows = []

    for gw in GATEWAYS:
        for run_idx in range(1, repeats + 1):
            port = BASE_PORT + GATEWAYS.index(gw)
            target_url = f"http://127.0.0.1:{port}"
            run_dir = os.path.join(RESULTS_DIR, gw.name, f"run{run_idx}")
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")

            print(f"\n{'='*60}")
            print(f"  [{gw.name}] Run {run_idx}/{repeats}")
            print(f"{'='*60}")

            if dry_run:
                print(f"  [DRY-RUN] 跳过实际执行")
                continue

            gw_proc = start_gateway(gw, port)

            try:
                stdout = run_load_gen(target_url, output_csv)
                stats = parse_session_stats(stdout)

                row = {
                    "gateway": gw.name,
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
                summary_rows.append(row)

                print(f"  结果: success={row['success']}, cascade={row['cascade_failed']}, rejected_s0={row['rejected_s0']}")
                print(f"         success_rate={row['success_rate']}%")
                print(f"         ABD_total={row['abd_total']:.1f}%  ABD_P&S={row['abd_ps']:.1f}%  ABD_ReAct={row['abd_react']:.1f}%")
                print(f"         goodput={row['goodput']:.1f} GP/s")

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

        print_summary_table(summary_rows, repeats)


def print_summary_table(rows, repeats):
    """打印最终汇总对照表 (含 mode-stratified ABD)"""
    import statistics

    print(f"\n{'='*100}")
    print("  Week 2 Smoke Test — 7-Gateway ABD 证据链")
    print(f"{'='*100}")
    print(f"{'Gateway':<18} {'SuccRate%':>10} {'ABD_total%':>11} {'ABD_P&S%':>10} {'ABD_ReAct%':>11} {'GP/s':>10}")
    print(f"{'-'*18} {'-'*10} {'-'*11} {'-'*10} {'-'*11} {'-'*10}")

    gw_stats = {}
    for row in rows:
        gw = row["gateway"]
        if gw not in gw_stats:
            gw_stats[gw] = {"sr": [], "abd": [], "abd_ps": [], "abd_react": [], "gp": []}
        gw_stats[gw]["sr"].append(row["success_rate"])
        gw_stats[gw]["abd"].append(row["abd_total"])
        gw_stats[gw]["abd_ps"].append(row["abd_ps"])
        gw_stats[gw]["abd_react"].append(row["abd_react"])
        gw_stats[gw]["gp"].append(row["goodput"])

    # 按 GATEWAYS 顺序输出
    gw_order = [g.name for g in GATEWAYS]
    for gw in gw_order:
        if gw not in gw_stats:
            continue
        s = gw_stats[gw]
        sr_mean = statistics.mean(s["sr"])
        abd_mean = statistics.mean(s["abd"])
        abd_ps_mean = statistics.mean(s["abd_ps"])
        abd_react_mean = statistics.mean(s["abd_react"])
        gp_mean = statistics.mean(s["gp"])

        sr_std = statistics.stdev(s["sr"]) if len(s["sr"]) > 1 else 0
        abd_std = statistics.stdev(s["abd"]) if len(s["abd"]) > 1 else 0
        gp_std = statistics.stdev(s["gp"]) if len(s["gp"]) > 1 else 0

        print(f"{gw:<18} {sr_mean:>8.1f}±{sr_std:<4.1f} {abd_mean:>9.1f}±{abd_std:<4.1f} "
              f"{abd_ps_mean:>9.1f} {abd_react_mean:>10.1f} {gp_mean:>8.1f}±{gp_std:<4.1f}")

    # ABD 证据链评估
    if "plangate_full" in gw_stats:
        pg_abd = statistics.mean(gw_stats["plangate_full"]["abd"])
        pg_abd_ps = statistics.mean(gw_stats["plangate_full"]["abd_ps"])
        pg_abd_react = statistics.mean(gw_stats["plangate_full"]["abd_react"])
        pg_gp = statistics.mean(gw_stats["plangate_full"]["gp"])

        print(f"\n  === ABD 证据链评估 ===")
        print(f"  PlanGate ABD_total:  {pg_abd:.1f}%")
        print(f"  PlanGate ABD_P&S:    {pg_abd_ps:.1f}% {'✓ 硬承诺' if pg_abd_ps < 5 else '⚠ 需检查'}")
        print(f"  PlanGate ABD_ReAct:  {pg_abd_react:.1f}% {'✓ 显著优于基线' if pg_abd_react < 40 else '⚠ 软承诺效果弱'}")
        print(f"  PlanGate GP/s:       {pg_gp:.1f}")

        # 与各基线对比
        for bname in ["ng", "rajomon", "rajomon_sb", "sbac", "pp", "pg_nores"]:
            if bname in gw_stats:
                b_abd = statistics.mean(gw_stats[bname]["abd"])
                b_gp = statistics.mean(gw_stats[bname]["gp"])
                abd_gap = b_abd - pg_abd
                gp_ratio = pg_gp / b_gp * 100 - 100 if b_gp > 0 else 0
                print(f"    vs {bname:<16}: ABD差={abd_gap:+.1f}pp  GP/s差={gp_ratio:+.1f}%")

        # 总体判断
        baseline_abds = []
        for bname in ["ng", "rajomon", "rajomon_sb", "sbac", "pp"]:
            if bname in gw_stats:
                baseline_abds.append(statistics.mean(gw_stats[bname]["abd"]))

        if baseline_abds:
            avg_baseline_abd = statistics.mean(baseline_abds)
            abd_advantage = avg_baseline_abd - pg_abd
            if abd_advantage > 30 and pg_abd_ps < 5:
                print(f"\n  ✓ ABD 证据链成立: 基线平均 ABD={avg_baseline_abd:.1f}%, PlanGate={pg_abd:.1f}%, 差距={abd_advantage:.1f}pp")
                print(f"    P&S 硬承诺验证: ABD_P&S={pg_abd_ps:.1f}% ≈ 0")
            elif abd_advantage > 15:
                print(f"\n  △ ABD 证据链部分成立: 差距={abd_advantage:.1f}pp, 需深入分析 ABD_ReAct")
            else:
                print(f"\n  ✗ ABD 证据链不成立: 差距仅={abd_advantage:.1f}pp, 需重新定位")

    print(f"{'='*100}")


def main():
    parser = argparse.ArgumentParser(description="Week 2 Expanded Smoke Test (7 Gateways)")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  Week 2 Smoke Test — 7-Gateway ABD 证据链验证")
    print("=" * 60)

    try:
        build_gateway()
        start_backend()
        run_experiment(args.repeats, args.dry_run)
    except KeyboardInterrupt:
        print("\n  用户中断")
    finally:
        stop_backend()

    print("\n  完成!")


if __name__ == "__main__":
    main()
