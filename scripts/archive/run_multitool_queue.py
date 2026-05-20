#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_multitool_queue.py
等待 B2 bursty 实验完成后，自动启动 B3 vLLM，再启动 diagnose。

用法：
  python scripts/run_multitool_queue.py
  python scripts/run_multitool_queue.py --skip-b2-wait  # B2 已完成，直接跑 B3
  python scripts/run_multitool_queue.py --only-diagnose  # 只跑诊断
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

B2_OUT = ROOT / "results" / "neutral_multitool_real_llm" / "bursty"
B3_OUT = ROOT / "results" / "neutral_multitool_real_llm" / "selfhosted_vllm"
LOG_DIR = ROOT / "logs" / "neutral_multitool_real_llm"
LOG_DIR.mkdir(parents=True, exist_ok=True)

GATEWAYS_B2 = ["ng", "rajomon", "pp", "plangate_real"]
GATEWAYS_B3 = ["ng", "plangate_real"]
REPEATS = 3


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ:
                    os.environ[k] = v


def count_completed_runs(out_dir, gateways, repeats):
    """Return count of completed (agents CSV exists) runs."""
    count = 0
    for gw in gateways:
        for run in range(1, repeats + 1):
            ap = out_dir / gw / f"run{run}" / "steps_agents.csv"
            if ap.exists():
                n = sum(1 for _ in open(ap, encoding="utf-8-sig")) - 1
                if n > 0:
                    count += 1
    return count


def wait_for_b2(timeout_hours=4):
    """Poll until all B2 runs complete or timeout."""
    total_runs = len(GATEWAYS_B2) * REPEATS
    start = time.time()
    while True:
        completed = count_completed_runs(B2_OUT, GATEWAYS_B2, REPEATS)
        elapsed = (time.time() - start) / 3600
        print(f"  [B2 進度] {completed}/{total_runs} runs 完成 ({elapsed:.1f}h elapsed)")
        if completed >= total_runs:
            print(f"  ✓ B2 全部完成!")
            return True
        if elapsed >= timeout_hours:
            print(f"  ⚠ 超时 ({timeout_hours}h). B2 只完成 {completed}/{total_runs} runs.")
            return completed > 0  # partial success
        time.sleep(60)


def run_b3():
    """Run B3 vLLM experiment."""
    env = os.environ.copy()
    # Use vLLM as agent brain
    env["AGENT_LLM_BASE_URL"] = "http://127.0.0.1:9999/v1"
    env["AGENT_LLM_MODEL"] = "qwen"
    env["AGENT_LLM_KEY"] = "EMPTY"

    log_path = LOG_DIR / f"b3_vllm_{int(time.time())}.log"
    print(f"\n  [B3] 启动 vLLM 实验 (log: {log_path})")

    cmd = [
        sys.executable, str(SCRIPTS / "run_selfhosted_vllm.py"),
        "--repeats", str(REPEATS),
        "--out-dir", str(B3_OUT),
        "--agents", "100",
        "--concurrency", "20",
    ]
    print(f"  CMD: {' '.join(cmd)}")

    with open(log_path, "w", encoding="utf-8") as lf:
        p = subprocess.run(cmd, cwd=str(ROOT), env=env,
                           stdout=lf, stderr=lf, timeout=7200)
    return p.returncode == 0


def run_diagnose():
    """Run diagnosis in multitool mode."""
    log_path = LOG_DIR / f"diagnose_{int(time.time())}.log"
    print(f"\n  [DIAGNOSE] 运行诊断脚本 (log: {log_path})")
    cmd = [
        sys.executable, str(SCRIPTS / "diagnose_neutral_real_llm_results.py"),
        "--mode", "multitool",
    ]
    with open(log_path, "w", encoding="utf-8") as lf:
        p = subprocess.run(cmd, cwd=str(ROOT),
                           stdout=lf, stderr=lf, timeout=300)
    # Also print to console
    report_path = ROOT / "results" / "neutral_multitool_real_llm" / "diagnosis" / "diagnosis_report.md"
    if report_path.exists():
        print(f"  ✓ 报告已写入: {report_path}")
    return p.returncode == 0


def quick_stats(out_dir, gateways, repeats):
    """Print quick stats from completed runs."""
    import csv, statistics
    print(f"\n  {'Gateway':<18} {'Runs':>4} {'Succ%':>7} {'ABD%':>6} {'AvgSteps':>9} {'0-step%':>8} {'BackTok':>9}")
    print(f"  {'-'*18} {'-'*4} {'-'*7} {'-'*6} {'-'*9} {'-'*8} {'-'*9}")
    by_gw = {}
    for gw in gateways:
        rows = []
        for run in range(1, repeats + 1):
            ap = out_dir / gw / f"run{run}" / "steps_agents.csv"
            if not ap.exists():
                continue
            with open(ap, encoding="utf-8-sig") as f:
                agents = list(csv.DictReader(f))
            if not agents:
                continue
            n = len(agents)
            succ = sum(1 for r in agents if r.get("state") == "SUCCESS")
            part = sum(1 for r in agents if r.get("state") == "PARTIAL")
            steps = [int(r.get("total_steps", 0)) for r in agents]
            zero = sum(1 for s in steps if s == 0)
            btok = sum(int(r.get("backend_llm_tokens", 0)) for r in agents)
            admitted = succ + part
            abd = 100 * part / admitted if admitted > 0 else 0
            rows.append({
                "succ_rate": 100 * succ / n,
                "abd": abd,
                "avg_steps": sum(steps) / n,
                "zero_pct": 100 * zero / n,
                "btok": btok,
            })
        if not rows:
            continue
        n_runs = len(rows)
        print(f"  {gw:<18} {n_runs:>4}"
              f" {statistics.mean([r['succ_rate'] for r in rows]):>7.1f}"
              f" {statistics.mean([r['abd'] for r in rows]):>6.1f}"
              f" {statistics.mean([r['avg_steps'] for r in rows]):>9.2f}"
              f" {statistics.mean([r['zero_pct'] for r in rows]):>8.1f}"
              f" {sum(r['btok'] for r in rows):>9,}")


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-b2-wait", action="store_true",
                        help="B2 已完成，直接跑 B3")
    parser.add_argument("--only-diagnose", action="store_true",
                        help="只跑诊断脚本")
    parser.add_argument("--skip-b3", action="store_true",
                        help="跳过 B3 只跑诊断")
    args = parser.parse_args()

    print("\n" + "="*65)
    print("  Multitool 实验队列")
    print(f"  B2 out: {B2_OUT}")
    print(f"  B3 out: {B3_OUT}")
    print("="*65)

    if args.only_diagnose:
        run_diagnose()
        return

    if not args.skip_b2_wait:
        print("\n  等待 B2 bursty 完成...")
        b2_ok = wait_for_b2(timeout_hours=4)
        if not b2_ok:
            print("  ⚠ B2 long timeout, continuing anyway...")
    else:
        print("  [skip-b2-wait] 直接进行 B3")

    # B2 quick stats
    print("\n  B2 quick stats:")
    quick_stats(B2_OUT, GATEWAYS_B2, REPEATS)

    if not args.skip_b3:
        # Check vLLM availability
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:9999/v1/models")
            with urllib.request.urlopen(req, timeout=5):
                pass
            print("\n  vLLM OK — starting B3")
            b3_ok = run_b3()
            if b3_ok:
                print("  ✓ B3 完成!")
            else:
                print("  ⚠ B3 运行异常，继续诊断")
        except Exception as e:
            print(f"  ⚠ vLLM 不可达 ({e})，跳过 B3")

    # Run diagnose
    run_diagnose()

    # Print final stats
    print("\n  ── B2 最终统计 ──")
    quick_stats(B2_OUT, GATEWAYS_B2, REPEATS)
    print("\n  ── B3 最终统计 ──")
    quick_stats(B3_OUT, GATEWAYS_B3, REPEATS)


if __name__ == "__main__":
    main()
