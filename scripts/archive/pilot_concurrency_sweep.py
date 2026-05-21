#!/usr/bin/env python3
"""
pilot_concurrency_sweep.py — 真实 LLM 并发校准 Pilot
=====================================================
目标: 找到 "contention onset" — PlanGate 收益开始显现的并发度。
跑 C=20,30 各 1 repeat (C=10 已有数据)，对比四个信号:
  1. Step-level rejection rate (rejected / total_steps)
  2. ABD% (partial / (success + partial))
  3. P95 latency
  4. ALL_REJECTED agent count (step-0 rejections, proxy for 429)

用法:
  python scripts/pilot_concurrency_sweep.py --dry-run
  python scripts/pilot_concurrency_sweep.py                 # 跑 C=20,30
  python scripts/pilot_concurrency_sweep.py --conc 20       # 只跑 C=20
  python scripts/pilot_concurrency_sweep.py --analyze-only  # 只分析已有数据
"""

import argparse
import csv
import glob
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
RESULTS_BASE = os.path.join(ROOT_DIR, "results", "exp_week5_pilot")
LOG_DIR = os.path.join(ROOT_DIR, "results", "log", "real_llm")
BACKEND_URL = "http://127.0.0.1:8080"
GATEWAY_BINARY = None
BASE_PORT = 9300

# ── 实验参数 ──
AGENTS = 200
MAX_STEPS = 10
BUDGET = 1000
ARRIVAL_INTERVAL = 0.3

# ── 调优参数 ──
TUNED_PARAMS = {
    "rajomon":  {"price_step": 5},
    "pp":       {"max_sessions": 150},
    "plangate": {"price_step": 40, "sunk_cost_alpha": 0.5},
}


@dataclass
class GatewayConfig:
    name: str
    mode: str
    extra_args: list = field(default_factory=list)


def make_gateways(concurrency: int) -> List[GatewayConfig]:
    """Build gateway configs; PlanGate max_sessions scales with concurrency."""
    # max_sessions needs to accommodate: active + TTL-pending sessions
    # At C=N with ~30s sessions + 60s TTL: need ~3N slots
    pg_max_sessions = max(50, concurrency * 3)
    return [
        GatewayConfig("ng", "ng"),
        GatewayConfig("rajomon", "rajomon", [
            "--rajomon-price-step", str(TUNED_PARAMS["rajomon"]["price_step"]),
        ]),
        GatewayConfig("pp", "pp", [
            "--pp-max-sessions", str(TUNED_PARAMS["pp"]["max_sessions"]),
        ]),
        GatewayConfig("plangate_real", "mcpdp-real", [
            "--plangate-price-step", str(TUNED_PARAMS["plangate"]["price_step"]),
            "--plangate-max-sessions", str(pg_max_sessions),
            "--plangate-sunk-cost-alpha", str(TUNED_PARAMS["plangate"]["sunk_cost_alpha"]),
            "--plangate-session-cap-wait", "5",
            "--real-ratelimit-max", "200",
            "--real-latency-threshold", "5000",
        ]),
    ]


BACKEND_PROC = None


def start_backend():
    global BACKEND_PROC
    stop_backend()
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "_backend_pilot.log")
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

    log_path = os.path.join(LOG_DIR, f"_gw_{gw.name}_pilot.log")
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


def run_react_client(gateway_url: str, output_csv: str, gateway_mode: str,
                     concurrency: int) -> str:
    cmd = [
        sys.executable, REACT_CLIENT,
        "--gateway", gateway_url,
        "--agents", str(AGENTS),
        "--concurrency", str(concurrency),
        "--max-steps", str(MAX_STEPS),
        "--budget", str(BUDGET),
        "--arrival-interval", str(ARRIVAL_INTERVAL),
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


def parse_stdout_stats(stdout_text: str) -> dict:
    stats = {}
    for line in stdout_text.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├" in line:
            try:
                stats["success"] = int(line.split("SUCCESS:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "PARTIAL:" in line:
            try:
                stats["partial"] = int(line.split("PARTIAL:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "ALL_REJECTED:" in line:
            try:
                stats["all_rejected"] = int(line.split("ALL_REJECTED:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "ERROR:" in line and "└" in line:
            try:
                stats["error"] = int(line.split("ERROR:")[1].strip().split()[0])
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
        if "P50:" in line:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "P50:":
                        stats["p50_ms"] = float(parts[i + 1].replace("ms", ""))
                    if p == "P95:":
                        stats["p95_ms"] = float(parts[i + 1].replace("ms", ""))
            except (ValueError, IndexError):
                pass
        if "Agent Brain:" in line:
            try:
                stats["agent_tokens"] = int(line.split(":")[1].strip().replace(",", "").split()[0])
            except (ValueError, IndexError):
                pass
        if "Backend LLM:" in line:
            try:
                stats["backend_tokens"] = int(line.split(":")[1].strip().replace(",", "").split()[0])
            except (ValueError, IndexError):
                pass
        if "总耗时:" in line:
            try:
                stats["elapsed_s"] = float(line.split(":")[-1].strip().replace("s", ""))
            except (ValueError, IndexError):
                pass
        if "总工具调用:" in line:
            try:
                stats["total_steps"] = int(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if "成功调用:" in line:
            try:
                stats["success_steps"] = int(line.split(":")[-1].strip())
            except (ValueError, IndexError):
                pass

    s = stats.get("success", 0)
    partial = stats.get("partial", 0)
    admitted = s + partial
    stats["abd_pct"] = round(100 * partial / admitted, 1) if admitted > 0 else 0.0
    stats["success_rate"] = round(100 * s / AGENTS, 1) if AGENTS > 0 else 0.0

    # Step-level rejection rate
    total_st = stats.get("total_steps", 0)
    succ_st = stats.get("success_steps", 0)
    rejected_steps = total_st - succ_st
    stats["step_reject_pct"] = round(100 * rejected_steps / total_st, 1) if total_st > 0 else 0.0

    return stats


def analyze_steps_csv(csv_path: str) -> dict:
    """从 steps.csv 提取 step-level 信号 (rejected 比例, 429 proxy)"""
    if not os.path.exists(csv_path):
        return {}
    total = 0
    rejected = 0
    timeout = 0
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                status = row.get("status", "")
                if status == "rejected":
                    rejected += 1
                elif status == "timeout":
                    timeout += 1
    except Exception:
        return {}
    return {
        "csv_total_steps": total,
        "csv_rejected_steps": rejected,
        "csv_timeout_steps": timeout,
        "csv_reject_pct": round(100 * rejected / total, 1) if total > 0 else 0.0,
    }


def run_pilot(concurrency_levels: List[int], dry_run: bool = False):
    os.makedirs(RESULTS_BASE, exist_ok=True)
    all_rows = []

    for conc in concurrency_levels:
        gateways = make_gateways(conc)
        conc_dir = os.path.join(RESULTS_BASE, f"C{conc}")
        os.makedirs(conc_dir, exist_ok=True)

        for gw in gateways:
            port = BASE_PORT + gateways.index(gw)
            gateway_url = f"http://127.0.0.1:{port}"
            run_dir = os.path.join(conc_dir, gw.name)
            os.makedirs(run_dir, exist_ok=True)
            output_csv = os.path.join(run_dir, "steps.csv")

            print(f"\n{'=' * 65}")
            print(f"  PILOT  C={conc}  [{gw.name}]  ({AGENTS} agents)")
            print(f"{'=' * 65}")

            if dry_run:
                print(f"  [DRY-RUN] gateway: {gw.mode}, port: {port}")
                print(f"  [DRY-RUN] concurrency: {conc}")
                pg_ms = max(50, conc * 3)
                if gw.name == "plangate_real":
                    print(f"  [DRY-RUN] plangate max_sessions: {pg_ms}")
                print(f"  [DRY-RUN] 输出: {output_csv}")
                continue

            gw_proc = start_gateway(gw, port)
            time.sleep(2)

            try:
                stdout = run_react_client(gateway_url, output_csv, gw.name, conc)
                stats = parse_stdout_stats(stdout)
                csv_stats = analyze_steps_csv(output_csv)
                stats.update(csv_stats)

                row = {
                    "concurrency": conc,
                    "gateway": gw.name,
                    "success": stats.get("success", 0),
                    "partial": stats.get("partial", 0),
                    "all_rejected": stats.get("all_rejected", 0),
                    "error": stats.get("error", 0),
                    "success_rate": stats.get("success_rate", 0),
                    "abd_pct": stats.get("abd_pct", 0),
                    "eff_gps": stats.get("eff_gps", 0),
                    "p50_ms": stats.get("p50_ms", 0),
                    "p95_ms": stats.get("p95_ms", 0),
                    "step_reject_pct": stats.get("step_reject_pct", 0),
                    "csv_reject_pct": stats.get("csv_reject_pct", 0),
                    "csv_rejected_steps": stats.get("csv_rejected_steps", 0),
                    "csv_timeout_steps": stats.get("csv_timeout_steps", 0),
                    "cascade_steps": stats.get("cascade_steps", 0),
                    "elapsed_s": stats.get("elapsed_s", 0),
                    "agent_tokens": stats.get("agent_tokens", 0),
                    "backend_tokens": stats.get("backend_tokens", 0),
                }
                all_rows.append(row)

                print(f"\n  ── PILOT 结果 C={conc} [{gw.name}] ──")
                print(f"  Success: {row['success']}/{AGENTS} ({row['success_rate']}%)")
                print(f"  ABD: {row['abd_pct']:.1f}%")
                print(f"  Step Reject%: {row['csv_reject_pct']:.1f}% "
                      f"({row['csv_rejected_steps']} rejected, {row['csv_timeout_steps']} timeout)")
                print(f"  P95: {row['p95_ms']:.0f}ms  GP/s: {row['eff_gps']:.2f}")
                print(f"  Elapsed: {row['elapsed_s']:.0f}s")

            finally:
                stop_process(gw_proc)
                print(f"  冷却 15s...")
                time.sleep(15)

    # 写汇总
    if all_rows:
        summary_path = os.path.join(RESULTS_BASE, "pilot_summary.csv")
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\n  汇总CSV: {summary_path}")
        print_pilot_table(all_rows)

    return all_rows


def load_c10_data():
    """加载已有的 C=10 验证数据 (session 2 validation run)"""
    c10_dir = os.path.join(ROOT_DIR, "results", "exp_week5_real_llm")
    rows = []
    gw_names = ["ng", "rajomon", "pp", "plangate_real"]
    for gw_name in gw_names:
        csv_path = os.path.join(c10_dir, gw_name, "run1", "steps.csv")
        summary_path = os.path.join(c10_dir, gw_name, "run1", "steps_summary.csv")
        if not os.path.exists(summary_path):
            continue
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                # May have multiple rows (old + new runs), take last
                for r in reader:
                    summary_row = r
                csv_stats = analyze_steps_csv(csv_path)
                row = {
                    "concurrency": 10,
                    "gateway": gw_name,
                    "success": int(summary_row.get("success", 0)),
                    "partial": int(float(summary_row.get("agents", 200))) -
                               int(summary_row.get("success", 0)) -
                               int(summary_row.get("all_rejected", 0)),
                    "all_rejected": int(summary_row.get("all_rejected", 0)),
                    "error": 0,
                    "success_rate": round(100 * int(summary_row.get("success", 0)) / AGENTS, 1),
                    "abd_pct": 0,
                    "eff_gps": float(summary_row.get("eff_gp_per_s", 0)),
                    "p50_ms": float(summary_row.get("e2e_p50_ms", 0)),
                    "p95_ms": float(summary_row.get("e2e_p95_ms", 0)),
                    "step_reject_pct": csv_stats.get("csv_reject_pct", 0),
                    "csv_reject_pct": csv_stats.get("csv_reject_pct", 0),
                    "csv_rejected_steps": csv_stats.get("csv_rejected_steps", 0),
                    "csv_timeout_steps": csv_stats.get("csv_timeout_steps", 0),
                    "cascade_steps": int(summary_row.get("cascade_wasted_steps", 0)),
                    "elapsed_s": float(summary_row.get("elapsed_s", 0)),
                    "agent_tokens": int(summary_row.get("agent_llm_tokens", 0)),
                    "backend_tokens": int(summary_row.get("backend_llm_tokens", 0)),
                }
                s = row["success"]
                p = row["partial"]
                admitted = s + p
                row["abd_pct"] = round(100 * p / admitted, 1) if admitted > 0 else 0.0
                rows.append(row)
        except Exception as e:
            print(f"  ⚠ 加载 C=10 {gw_name} 失败: {e}")
    return rows


def print_pilot_table(rows, include_c10=True):
    """打印 pilot 对比表 — 核心关注 contention 信号"""
    if include_c10:
        c10_rows = load_c10_data()
        if c10_rows:
            rows = c10_rows + rows

    print(f"\n{'=' * 100}")
    print("  PILOT SWEEP — Contention Onset 校准")
    print(f"{'=' * 100}")
    print(f"  {'C':>3}  {'Gateway':<18} {'Succ%':>7} {'ABD%':>7} "
          f"{'StepRej%':>9} {'Rej#':>5} {'Tmout#':>6} "
          f"{'P95ms':>8} {'GP/s':>6} {'Time':>6}")
    print(f"  {'─' * 3}  {'─' * 18} {'─' * 7} {'─' * 7} "
          f"{'─' * 9} {'─' * 5} {'─' * 6} "
          f"{'─' * 8} {'─' * 6} {'─' * 6}")

    # Group by concurrency
    from collections import OrderedDict
    by_conc = OrderedDict()
    for r in rows:
        c = r["concurrency"]
        if c not in by_conc:
            by_conc[c] = []
        by_conc[c].append(r)

    gw_order = ["ng", "rajomon", "pp", "plangate_real"]

    for conc in sorted(by_conc.keys()):
        group = by_conc[conc]
        gw_map = {r["gateway"]: r for r in group}
        for gw_name in gw_order:
            if gw_name not in gw_map:
                continue
            r = gw_map[gw_name]
            print(f"  {conc:>3}  {gw_name:<18} {r['success_rate']:>6.1f}% {r['abd_pct']:>6.1f}% "
                  f"{r['csv_reject_pct']:>8.1f}% {r['csv_rejected_steps']:>5} "
                  f"{r['csv_timeout_steps']:>6} "
                  f"{r['p95_ms']:>7.0f} {r['eff_gps']:>5.2f} {r['elapsed_s']:>5.0f}s")
        if conc != max(by_conc.keys()):
            print()

    # Contention onset analysis
    print(f"\n  ── Contention Onset 分析 ──")
    ng_by_c = {r["concurrency"]: r for r in rows if r["gateway"] == "ng"}
    pg_by_c = {r["concurrency"]: r for r in rows if r["gateway"] == "plangate_real"}

    for conc in sorted(by_conc.keys()):
        ng = ng_by_c.get(conc)
        pg = pg_by_c.get(conc)
        if ng and pg:
            sr_delta = pg["success_rate"] - ng["success_rate"]
            abd_delta = ng["abd_pct"] - pg["abd_pct"]
            rej_pct = ng.get("csv_reject_pct", 0)
            print(f"  C={conc:>2}: NG StepReject={rej_pct:.1f}%  |  "
                  f"PG-NG SuccΔ={sr_delta:+.1f}pp  ABD_Δ={abd_delta:+.1f}pp  "
                  f"{'✓ CONTENTION' if rej_pct > 5 or ng['abd_pct'] > 5 else '— low contention'}")

    # Recommendation
    onset_c = None
    for conc in sorted(by_conc.keys()):
        ng = ng_by_c.get(conc)
        if ng and (ng.get("csv_reject_pct", 0) > 5 or ng["abd_pct"] > 5 or ng["success_rate"] < 90):
            onset_c = conc
            break

    print(f"\n  ── 建议 ──")
    if onset_c:
        print(f"  Contention onset ≈ C={onset_c}")
        print(f"  推荐正式实验: C=10 (boundary) + C={onset_c} (scarcity regime)")
    else:
        print(f"  所有测试并发度下 contention 仍偏低")
        print(f"  建议追加 C=40 或 C=50 pilot")


def analyze_only():
    """只分析已有 pilot 数据 (不跑实验)"""
    summary_path = os.path.join(RESULTS_BASE, "pilot_summary.csv")
    rows = []
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                # Convert numeric fields
                for k in r:
                    try:
                        if "." in r[k]:
                            r[k] = float(r[k])
                        else:
                            r[k] = int(r[k])
                    except (ValueError, TypeError):
                        pass
                rows.append(r)
    if not rows:
        print("  ⚠ 没有找到 pilot 数据")
        print(f"  期望路径: {summary_path}")
        return
    print_pilot_table(rows, include_c10=True)


def _check_env():
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

    llm_key = os.environ.get("AGENT_LLM_KEY", os.environ.get("LLM_API_KEY", ""))
    if not llm_key:
        raise RuntimeError("LLM API Key 未配置!")
    print(f"  API Key: ***已配置***")


def main():
    parser = argparse.ArgumentParser(description="Pilot 并发校准 sweep (C=20,30)")
    parser.add_argument("--conc", type=int, nargs="*", default=[20, 30],
                        help="并发度列表 (默认: 20 30)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--analyze-only", action="store_true",
                        help="只分析已有数据，不跑实验")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    if args.analyze_only:
        analyze_only()
        return

    print(f"\n{'=' * 65}")
    print("  Pilot Concurrency Sweep — GLM-4-Flash")
    print(f"{'=' * 65}")
    print(f"  Sessions:    {AGENTS}")
    print(f"  Concurrency: {args.conc}")
    print(f"  Gateways:    NG, Rajomon, PP, PlanGate")
    print(f"  Repeats:     1 (pilot)")

    if not args.dry_run:
        _check_env()
        global GATEWAY_BINARY
        bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
        GATEWAY_BINARY = os.path.join(ROOT_DIR, bin_name)
        if not args.skip_build:
            print(f"  编译网关...")
            result = subprocess.run(
                ["go", "build", "-o", GATEWAY_BINARY, "./cmd/gateway"],
                cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"编译失败: {result.stderr}")
        print(f"  网关: {GATEWAY_BINARY}")
        start_backend()

    try:
        run_pilot(args.conc, dry_run=args.dry_run)
    finally:
        if not args.dry_run:
            stop_backend()
            print("\n  后端已停止。")

    print(f"\n  ✓ Pilot 完成！")


if __name__ == "__main__":
    main()
