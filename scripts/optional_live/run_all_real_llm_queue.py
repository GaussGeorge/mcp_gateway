#!/usr/bin/env python3
"""
run_all_real_llm_queue.py — 实验队列脚本
=========================================
等待 steady commercial API 实验完成后，自动依次运行：
  1. Bursty real-LLM 实验
  2. Self-hosted vLLM 实验

用法:
  python scripts/run_all_real_llm_queue.py [--skip-bursty] [--skip-vllm] [--dry-run]
  python scripts/run_all_real_llm_queue.py --bursty-only   # 只跑 bursty
  python scripts/run_all_real_llm_queue.py --vllm-only     # 只跑 vLLM

注意: 运行前确保 .env 中有正确的 API key。
"""

import argparse
import os
import subprocess
import sys
import time
import csv
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")

# Steady 完成标志文件
STEADY_SENTINEL = os.path.join(ROOT_DIR, "results", "exp_week5_C10", "week5_summary.csv")
STEADY_EXPECTED_ROWS = 20  # 4 gateways × 5 repeats

LOG_DIR = os.path.join(ROOT_DIR, "logs", "neutral_real_llm")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[QUEUE {ts}] {msg}", flush=True)


def load_dotenv():
    env_path = os.path.join(ROOT_DIR, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ:
                    os.environ[k] = v


def _steady_complete_today():
    """
    可靠地判断今日 steady 实验是否已经完成：
    - 旧 sentinel（week5_summary.csv 行数）容易误触发（April 15 旧文件也有 20 行）
    - 改为：plangate_real/run5/steps_summary.csv 存在 **且** 修改时间为今日
    """
    sentinel_path = os.path.join(ROOT_DIR, "results", "exp_week5_C10",
                                 "plangate_real", "run5", "steps_summary.csv")
    if not os.path.exists(sentinel_path):
        return False
    mtime = os.path.getmtime(sentinel_path)
    today_start = time.mktime(time.strptime(
        time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return mtime >= today_start


def wait_for_steady(max_wait_hours=12, poll_interval=120):
    """等待 steady 实验完成（通过 plangate_real/run5/steps_summary.csv 今日时间戳）"""
    if _steady_complete_today():
        log("Steady 实验已完成 (plangate_real/run5 今日已写入)")
        return True

    log(f"等待 steady 实验完成（每 {poll_interval}s 检查一次，最多等 {max_wait_hours}h）")
    deadline = time.time() + max_wait_hours * 3600

    while time.time() < deadline:
        time.sleep(poll_interval)
        if _steady_complete_today():
            log("Steady 实验完成！(plangate_real/run5 今日已写入)")
            return True
        # 进度提示
        recent = os.path.join(ROOT_DIR, "results", "exp_week5_C10", "plangate_real")
        runs_done = sum(
            1 for r in ["run1","run2","run3","run4","run5"]
            if os.path.exists(os.path.join(recent, r, "steps_summary.csv")) and
               os.path.getmtime(os.path.join(recent, r, "steps_summary.csv")) >=
               time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        )
        log(f"  plangate_real runs 今日完成: {runs_done}/5")

    log(f"ERROR: 等待超时 ({max_wait_hours}h)")
    return False


def run_bursty(dry_run=False):
    """运行 bursty 实验"""
    log("=== 启动 Bursty real-LLM 实验 ===")
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"run_bursty_neutral_{ts}.log")

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "run_real_llm_bursty.py")]
    if dry_run:
        cmd.append("--dry-run")

    log(f"命令: {' '.join(cmd)}")
    log(f"日志: {log_path}")

    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=ROOT_DIR, stdout=lf, stderr=subprocess.STDOUT)

    log(f"Bursty 实验完成，退出码: {proc.returncode}")
    return proc.returncode


def check_vllm():
    """检查 vLLM 是否可达"""
    import urllib.request, json
    try:
        r = urllib.request.urlopen("http://127.0.0.1:9999/v1/models", timeout=5)
        data = json.loads(r.read())
        models = [m["id"] for m in data.get("data", [])]
        log(f"vLLM 可达: models={models}")
        return True
    except Exception as e:
        log(f"vLLM 不可达: {e}")
        return False


def run_vllm(dry_run=False):
    """运行 self-hosted vLLM 实验"""
    log("=== 启动 Self-hosted vLLM 实验 ===")

    # 设置 vLLM 环境变量
    if not os.environ.get("AGENT_LLM_BASE_URL"):
        os.environ["AGENT_LLM_BASE_URL"] = "http://127.0.0.1:9999/v1"
        os.environ["AGENT_LLM_BASE"] = "http://127.0.0.1:9999/v1"
    if not os.environ.get("AGENT_LLM_MODEL"):
        os.environ["AGENT_LLM_MODEL"] = "qwen"
    if not os.environ.get("AGENT_LLM_KEY"):
        os.environ["AGENT_LLM_KEY"] = "EMPTY"

    if not check_vllm():
        log("ERROR: vLLM 不可达，请先启动 vLLM 服务 (port 9999)")
        log("  命令参考: python -m vllm.entrypoints.openai.api_server --model <path> --served-model-name qwen --port 9999")
        return 1

    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"run_selfhosted_vllm_neutral_{ts}.log")

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "run_selfhosted_vllm.py")]
    if dry_run:
        cmd.append("--dry-run")

    log(f"命令: {' '.join(cmd)}")
    log(f"日志: {log_path}")

    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=ROOT_DIR, stdout=lf, stderr=subprocess.STDOUT)

    log(f"vLLM 实验完成，退出码: {proc.returncode}")
    return proc.returncode


def archive_results(dry_run=False):
    """归档今日结果到 neutral 专用目录"""
    import shutil
    from datetime import date

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = str(date.today())

    log("=== 结果归档 ===")
    archive_base = os.path.join(ROOT_DIR, "results", "neutral_real_llm")

    targets = [
        ("steady", os.path.join(ROOT_DIR, "results", "exp_week5_C10")),
        ("bursty", os.path.join(ROOT_DIR, "results", "exp_bursty_C20_B30")),
        ("selfhosted_vllm", os.path.join(ROOT_DIR, "results", "exp_selfhosted_vllm_C10_W8")),
    ]

    for name, src_dir in targets:
        dst_dir = os.path.join(archive_base, name)
        if not os.path.exists(src_dir):
            log(f"  跳过 {name}: {src_dir} 不存在")
            continue

        # 找今日生成的 CSV 文件
        new_files = []
        for root, dirs, files in os.walk(src_dir):
            for f in files:
                fpath = os.path.join(root, f)
                if os.path.getmtime(fpath) >= time.time() - 86400 * 2:  # last 2 days
                    new_files.append(fpath)

        if not new_files:
            log(f"  {name}: 没有找到今日文件")
            continue

        os.makedirs(dst_dir, exist_ok=True)
        for src in new_files:
            rel = os.path.relpath(src, src_dir)
            dst = os.path.join(dst_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not dry_run:
                shutil.copy2(src, dst)
            log(f"  归档: {rel} -> {dst_dir}")

    # 归档日志
    log_src = os.path.join(ROOT_DIR, "logs", "neutral_real_llm")
    log_dst = os.path.join(archive_base, "logs")
    if os.path.exists(log_src) and not dry_run:
        os.makedirs(log_dst, exist_ok=True)
        for f in os.listdir(log_src):
            if today[:7] in f or ts[:8] in f:
                shutil.copy2(os.path.join(log_src, f), os.path.join(log_dst, f))


def main():
    parser = argparse.ArgumentParser(description="实验队列：等 steady → 运行 bursty → 运行 vLLM")
    parser.add_argument("--skip-wait", action="store_true", help="不等待 steady 完成（直接运行后续）")
    parser.add_argument("--skip-bursty", action="store_true", help="跳过 bursty 实验")
    parser.add_argument("--skip-vllm", action="store_true", help="跳过 vLLM 实验")
    parser.add_argument("--bursty-only", action="store_true", help="只运行 bursty")
    parser.add_argument("--vllm-only", action="store_true", help="只运行 vLLM")
    parser.add_argument("--no-archive", action="store_true", help="不归档结果")
    parser.add_argument("--dry-run", action="store_true", help="dry-run 模式")
    args = parser.parse_args()

    load_dotenv()

    if args.bursty_only:
        rc = run_bursty(dry_run=args.dry_run)
        if not args.no_archive:
            archive_results(dry_run=args.dry_run)
        sys.exit(rc)

    if args.vllm_only:
        rc = run_vllm(dry_run=args.dry_run)
        if not args.no_archive:
            archive_results(dry_run=args.dry_run)
        sys.exit(rc)

    # 等待 steady 完成
    if not args.skip_wait:
        ok = wait_for_steady()
        if not ok:
            log("WARNING: Steady 等待超时，继续运行后续实验...")

    # Run bursty
    if not args.skip_bursty:
        rc = run_bursty(dry_run=args.dry_run)
        if rc != 0:
            log(f"WARNING: Bursty 以错误码 {rc} 退出，继续 vLLM...")

    # Run vLLM
    if not args.skip_vllm:
        rc = run_vllm(dry_run=args.dry_run)
        if rc != 0:
            log(f"WARNING: vLLM 以错误码 {rc} 退出")

    if not args.no_archive:
        archive_results(dry_run=args.dry_run)

    log("=== 队列实验全部完成 ===")


if __name__ == "__main__":
    main()
