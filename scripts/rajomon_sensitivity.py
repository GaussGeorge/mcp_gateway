#!/usr/bin/env python3
"""
rajomon_sensitivity.py — Rajomon price_step 敏感性扫描
=====================================================
证明 per-request pricing 对多步 session 的结构性失配:
  price_step ∈ {5, 10, 20, 50, 100} × 5 repeats × 200 sessions

若所有 price_step 下 ABD>70%, 证明是结构性缺陷, 非调参造成。
若某 price_step 下 ABD<50%, 需用该参数重跑正式实验。

用法:
  python scripts/rajomon_sensitivity.py
  python scripts/rajomon_sensitivity.py --price-steps 5,10,20,50,100
  python scripts/rajomon_sensitivity.py --repeats 3 --dry-run
"""

import argparse
import csv
import os
import subprocess
import sys
import time
import statistics

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_rajomon_sensitivity")
MOCK_LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "mock")
BACKEND_URL = "http://127.0.0.1:8080"

# 与 Week 2 smoke test 一致的发压参数
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

PORT = 9200
GATEWAY_BINARY = None
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
    log_path = os.path.join(MOCK_LOG_DIR, "_backend_rajomon_sens.log")
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


def start_gateway(price_step: int):
    cmd = [
        GATEWAY_BINARY,
        "--mode", "rajomon",
        "--port", str(PORT),
        "--backend", BACKEND_URL,
        "--host", "127.0.0.1",
        "--rajomon-price-step", str(price_step),
    ]
    log_path = os.path.join(MOCK_LOG_DIR, f"_gw_rajomon_ps{price_step}.log")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"Rajomon (price_step={price_step}) 启动失败, 查看: {log_path}")
    print(f"  Rajomon (price_step={price_step}) 已启动 (pid={proc.pid})")
    return proc


def run_load_gen(output_csv: str) -> str:
    target_url = f"http://127.0.0.1:{PORT}"
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


def parse_stats(stdout_text: str) -> dict:
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
                stats["goodput"] = float(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if "ABD_total:" in line:
            try:
                stats["abd_total"] = float(line.split("ABD_total:")[1].strip().split("%")[0])
            except (ValueError, IndexError):
                pass
        if "ABD_P&S:" in line:
            try:
                stats["abd_ps"] = float(line.split("ABD_P&S:")[1].strip().split("%")[0])
            except (ValueError, IndexError):
                pass
        if "ABD_ReAct:" in line:
            try:
                stats["abd_react"] = float(line.split("ABD_ReAct:")[1].strip().split("%")[0])
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
    parser = argparse.ArgumentParser(description="Rajomon price_step sensitivity scan")
    parser.add_argument("--price-steps", default="5,10,20,50,100",
                        help="Comma-separated price_step values")
    parser.add_argument("--repeats", type=int, default=5, help="Repeats per config")
    parser.add_argument("--dry-run", action="store_true", help="Only print commands")
    args = parser.parse_args()

    price_steps = [int(x.strip()) for x in args.price_steps.split(",")]
    repeats = args.repeats
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  Rajomon 敏感性扫描: price_step ∈ {price_steps}")
    print(f"  repeats={repeats}, sessions={LOAD_CONFIG['sessions']}")
    print(f"{'='*70}")

    if args.dry_run:
        for ps in price_steps:
            for r in range(1, repeats + 1):
                print(f"  [DRY-RUN] price_step={ps}, run={r}")
        return

    build_gateway()
    start_backend()

    summary_rows = []

    try:
        for ps in price_steps:
            for run_idx in range(1, repeats + 1):
                print(f"\n{'─'*60}")
                print(f"  price_step={ps}, run {run_idx}/{repeats}")
                print(f"{'─'*60}")

                run_dir = os.path.join(RESULTS_DIR, f"ps{ps}", f"run{run_idx}")
                os.makedirs(run_dir, exist_ok=True)
                output_csv = os.path.join(run_dir, "steps.csv")

                gw_proc = start_gateway(ps)
                try:
                    stdout = run_load_gen(output_csv)
                    stats = parse_stats(stdout)

                    row = {
                        "price_step": ps,
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

                    print(f"  结果: success={row['success']}, cascade={row['cascade_failed']}, rej_s0={row['rejected_s0']}")
                    print(f"         ABD_total={row['abd_total']:.1f}%  ABD_P&S={row['abd_ps']:.1f}%  ABD_ReAct={row['abd_react']:.1f}%")
                    print(f"         GP/s={row['goodput']:.1f}")
                finally:
                    stop_process(gw_proc)
                    time.sleep(1)
    finally:
        stop_backend()

    # 保存 CSV
    if not summary_rows:
        return

    csv_path = os.path.join(RESULTS_DIR, "rajomon_sensitivity.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\n  CSV 已保存: {csv_path}")

    # 汇总分析
    print(f"\n{'='*80}")
    print(f"  Rajomon 敏感性扫描结果")
    print(f"{'='*80}")
    print(f"{'price_step':>12}  {'ABD_total%':>11}  {'ABD_P&S%':>10}  {'ABD_ReAct%':>11}  {'SuccRate%':>10}  {'GP/s':>10}")
    print(f"{'-'*12}  {'-'*11}  {'-'*10}  {'-'*11}  {'-'*10}  {'-'*10}")

    all_abd_above_70 = True
    any_abd_below_50 = False
    best_ps = None
    best_abd = 100.0

    for ps in price_steps:
        ps_rows = [r for r in summary_rows if r["price_step"] == ps]
        if not ps_rows:
            continue

        abd_vals = [r["abd_total"] for r in ps_rows]
        abd_ps_vals = [r["abd_ps"] for r in ps_rows]
        abd_react_vals = [r["abd_react"] for r in ps_rows]
        sr_vals = [r["success_rate"] for r in ps_rows]
        gp_vals = [r["goodput"] for r in ps_rows]

        abd_m = statistics.mean(abd_vals)
        abd_ps_m = statistics.mean(abd_ps_vals)
        abd_re_m = statistics.mean(abd_react_vals)
        sr_m = statistics.mean(sr_vals)
        gp_m = statistics.mean(gp_vals)

        abd_s = statistics.stdev(abd_vals) if len(abd_vals) > 1 else 0

        print(f"{ps:>12}  {abd_m:>8.1f}±{abd_s:<4.1f} {abd_ps_m:>9.1f} {abd_re_m:>10.1f} {sr_m:>8.1f} {gp_m:>8.1f}")

        if abd_m < 70:
            all_abd_above_70 = False
        if abd_m < 50:
            any_abd_below_50 = True
        if abd_m < best_abd:
            best_abd = abd_m
            best_ps = ps

    print(f"\n  === 判定 ===")
    if all_abd_above_70:
        print(f"  ✓ 所有 price_step 下 ABD >70% → 结构性失配已证明")
        print(f"    per-request pricing 无法为 multi-step sessions 提供有效承诺")
    elif any_abd_below_50:
        print(f"  ⚠ price_step={best_ps} 下 ABD={best_abd:.1f}% < 50%")
        print(f"    需考虑用该参数重跑正式实验以确保公平性")
    else:
        print(f"  △ 最佳 price_step={best_ps} ABD={best_abd:.1f}% (在50-70%之间)")
        print(f"    Rajomon 仍表现显著差于 PlanGate，但需在论文中标注最佳参数")


if __name__ == "__main__":
    main()
