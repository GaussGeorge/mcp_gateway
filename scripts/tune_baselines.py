#!/usr/bin/env python3
"""
tune_baselines.py — 使用 Optuna 贝叶斯优化为基线方案寻找最优参数

目标：在 DAG 会话负载下最大化 Effective Goodput
基线：Rajomon (price_step), Breakwater (init_credits, increase_step, decrease_ratio), SRL (qps, burst, max_conc)

用法：
    python scripts/tune_baselines.py --baseline rajomon --trials 30
    python scripts/tune_baselines.py --baseline breakwater --trials 30
    python scripts/tune_baselines.py --baseline srl --trials 30
    python scripts/tune_baselines.py --baseline all --trials 30

依赖：pip install optuna
"""

import argparse
import csv
import os
import signal
import subprocess
import sys
import time

try:
    import optuna
except ImportError:
    print("请先安装 optuna: pip install optuna")
    sys.exit(1)


# ====== 配置常量 ======
GATEWAY_BIN_DIR = os.path.join(os.path.dirname(__file__), "..", "cmd", "gateway")
BACKEND_URL = "http://127.0.0.1:8080"


def update_backend_url(url: str):
    global BACKEND_URL
    BACKEND_URL = url
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 9099  # 调优专用端口，避免与正式实验冲突
TUNE_DURATION = 30   # 每次试验的发压时长（秒）
TUNE_SESSIONS = 200  # 每次试验的会话数
TUNE_PS_RATIO = 1.0  # 纯 P&S 模式（最大化 DAG 特有差异）
TUNE_BUDGET = 500
TUNE_HEAVY_RATIO = 0.3
TUNE_CONCURRENCY = 100   # 与正式实验保持一致（高负载条件）
TUNE_ARRIVAL_RATE = 50.0
TUNE_STEP_TIMEOUT = 2.0  # 步骤超时（秒），与正式实验一致
BACKEND_MAX_WORKERS = 10 # MCP 后端最大并发工作线程数

DAG_LOAD_GEN = os.path.join(os.path.dirname(__file__), "dag_load_generator.py")
SERVER_PY = os.path.join(os.path.dirname(__file__), "..", "mcp_server", "server.py")
TUNE_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "tune")
ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
TUNE_GATEWAY_BINARY = None
_backend_proc: subprocess.Popen = None


def start_backend(max_workers: int = 10) -> subprocess.Popen:
    """启动 Python MCP 后端（限制并发工作线程以制造过载）"""
    global _backend_proc
    backend_port = int(BACKEND_URL.split(":")[-1])
    cmd = [sys.executable, os.path.abspath(SERVER_PY),
           "--port", str(backend_port),
           "--max-workers", str(max_workers),
           "--queue-timeout", "1.0",
           "--congestion-factor", "0.5"]
    print(f"  启动后端: python server.py --max-workers {max_workers} --queue-timeout 1.0 --congestion-factor 0.5")
    _backend_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.abspath(SERVER_PY)),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    time.sleep(5)
    if _backend_proc.poll() is not None:
        raise RuntimeError("Python 后端启动失败")
    print(f"  后端已启动 (pid={_backend_proc.pid})")
    return _backend_proc


def stop_backend():
    """停止 Python MCP 后端"""
    global _backend_proc
    if _backend_proc is None:
        return
    if _backend_proc.poll() is not None:
        _backend_proc = None
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(_backend_proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            _backend_proc.terminate()
            _backend_proc.wait(timeout=5)
    except Exception:
        try:
            _backend_proc.kill()
        except Exception:
            pass
    _backend_proc = None
    print("  后端已停止")


def build_gateway():
    """预编译网关二进制"""
    global TUNE_GATEWAY_BINARY
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)
    print(f"  预编译网关: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"编译失败: {result.stderr}")
    TUNE_GATEWAY_BINARY = bin_path
    print(f"  编译完成: {bin_path}")


def start_gateway(mode: str, extra_args: list) -> subprocess.Popen:
    """启动网关子进程（使用预编译二进制）"""
    if TUNE_GATEWAY_BINARY is None:
        build_gateway()

    cmd = [
        TUNE_GATEWAY_BINARY,
        "--mode", mode,
        "--port", str(GATEWAY_PORT),
        "--backend", BACKEND_URL,
        "--host", GATEWAY_HOST,
    ] + extra_args

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"网关启动失败 (exit={proc.returncode})")
    return proc


def stop_gateway(proc: subprocess.Popen):
    """安全终止网关进程（Windows 兼容）"""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


def run_load_generator(output_csv: str) -> float:
    """运行 DAG 发压机并返回 Effective Goodput/s"""
    target = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
    cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target", target,
        "--sessions", str(TUNE_SESSIONS),
        "--ps-ratio", str(TUNE_PS_RATIO),
        "--budget", str(TUNE_BUDGET),
        "--heavy-ratio", str(TUNE_HEAVY_RATIO),
        "--concurrency", str(TUNE_CONCURRENCY),
        "--arrival-rate", str(TUNE_ARRIVAL_RATE),
        "--duration", str(TUNE_DURATION),
        "--step-timeout", str(TUNE_STEP_TIMEOUT),
        "--price-ttl", "1.0",
        "--output", output_csv,
    ]

    # 使用日志文件代替管道捕获，避免 Windows asyncio + subprocess PIPE 死锁
    log_path = output_csv.replace(".csv", "_stdout.log")
    timeout_sec = TUNE_DURATION + 60

    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(DAG_LOAD_GEN),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
            try:
                retcode = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                print(f"  [TIMEOUT] 发压机超时 ({timeout_sec}s)")
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10)
                else:
                    proc.kill()
                proc.wait(timeout=5)
                return 0.0
    except Exception as e:
        print(f"  [ERROR] 发压机启动失败: {e}")
        return 0.0

    # 从日志文件中读取 stdout 内容
    stdout_text = ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            stdout_text = f.read()
    except Exception:
        pass

    if retcode != 0:
        print(f"  [WARN] 发压机退出码={retcode}")
        print(f"  输出: {stdout_text[-300:]}")
        return 0.0

    # 解析输出中的 Effective Goodput/s
    return parse_effective_goodput(stdout_text, output_csv)


def parse_effective_goodput(stdout_text: str, csv_path: str) -> float:
    """从发压机输出或 CSV 中解析 Effective Goodput/s"""
    # 方法1：从 stdout 中提取
    for line in stdout_text.split("\n"):
        if "Effective Goodput/s:" in line:
            try:
                return float(line.split(":")[-1].strip())
            except ValueError:
                pass

    # 方法2：从 session CSV 中计算
    session_csv = csv_path.replace(".csv", "_sessions.csv")
    if os.path.exists(session_csv):
        total_effective = 0.0
        with open(session_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("state") == "SUCCESS":
                    total_effective += float(row.get("effective_goodput", 0))
        # 用 TUNE_DURATION 做近似
        if TUNE_DURATION > 0:
            return total_effective / TUNE_DURATION

    return 0.0


# ====== Optuna 目标函数 ======

def objective_rajomon(trial: optuna.Trial) -> float:
    price_step = trial.suggest_int("price_step", 20, 500, step=10)

    trial_csv = os.path.join(TUNE_OUTPUT_DIR, f"rajomon_trial{trial.number}.csv")
    os.makedirs(TUNE_OUTPUT_DIR, exist_ok=True)

    extra_args = ["--rajomon-price-step", str(price_step)]
    proc = start_gateway("rajomon", extra_args)
    try:
        goodput = run_load_generator(trial_csv)
    finally:
        stop_gateway(proc)

    print(f"  [Rajomon] trial={trial.number} price_step={price_step} → EffGoodput/s={goodput:.2f}")
    return goodput


def objective_dagor(trial: optuna.Trial) -> float:
    rtt_threshold = trial.suggest_float("rtt_threshold", 100.0, 500.0, step=25.0)
    price_step = trial.suggest_int("price_step", 10, 200, step=10)

    trial_csv = os.path.join(TUNE_OUTPUT_DIR, f"dagor_trial{trial.number}.csv")
    os.makedirs(TUNE_OUTPUT_DIR, exist_ok=True)

    extra_args = [
        "--dagor-rtt-threshold", str(rtt_threshold),
        "--dagor-price-step", str(price_step),
    ]
    proc = start_gateway("dagor", extra_args)
    try:
        goodput = run_load_generator(trial_csv)
    finally:
        stop_gateway(proc)

    print(f"  [DAGOR] trial={trial.number} rtt_threshold={rtt_threshold} price_step={price_step} → EffGoodput/s={goodput:.2f}")
    return goodput


def objective_sbac(trial: optuna.Trial) -> float:
    max_sessions = trial.suggest_int("max_sessions", 5, 150, step=5)

    trial_csv = os.path.join(TUNE_OUTPUT_DIR, f"sbac_trial{trial.number}.csv")
    os.makedirs(TUNE_OUTPUT_DIR, exist_ok=True)

    extra_args = ["--sbac-max-sessions", str(max_sessions)]
    proc = start_gateway("sbac", extra_args)
    try:
        goodput = run_load_generator(trial_csv)
    finally:
        stop_gateway(proc)

    print(f"  [SBAC] trial={trial.number} max_sessions={max_sessions} → EffGoodput/s={goodput:.2f}")
    return goodput


def objective_srl(trial: optuna.Trial) -> float:
    qps = trial.suggest_float("qps", 10, 200, step=5)
    burst = trial.suggest_int("burst", 20, 400, step=10)
    max_conc = trial.suggest_int("max_conc", 5, 60, step=5)

    trial_csv = os.path.join(TUNE_OUTPUT_DIR, f"srl_trial{trial.number}.csv")
    os.makedirs(TUNE_OUTPUT_DIR, exist_ok=True)

    extra_args = [
        "--srl-qps", str(qps),
        "--srl-burst", str(burst),
        "--srl-max-conc", str(max_conc),
    ]
    proc = start_gateway("srl", extra_args)
    try:
        goodput = run_load_generator(trial_csv)
    finally:
        stop_gateway(proc)

    print(f"  [SRL] trial={trial.number} qps={qps} burst={burst} max_conc={max_conc} → EffGoodput/s={goodput:.2f}")
    return goodput


def objective_plangate(trial: optuna.Trial) -> float:
    max_sessions = trial.suggest_int("max_sessions", 5, 60, step=5)
    price_step = trial.suggest_int("price_step", 5, 100, step=5)

    trial_csv = os.path.join(TUNE_OUTPUT_DIR, f"plangate_trial{trial.number}.csv")
    os.makedirs(TUNE_OUTPUT_DIR, exist_ok=True)

    extra_args = [
        "--plangate-max-sessions", str(max_sessions),
        "--plangate-price-step", str(price_step),
    ]
    proc = start_gateway("mcpdp", extra_args)
    try:
        goodput = run_load_generator(trial_csv)
    finally:
        stop_gateway(proc)

    print(f"  [PlanGate] trial={trial.number} max_sessions={max_sessions} price_step={price_step} → EffGoodput/s={goodput:.2f}")
    return goodput


def tune_baseline(name: str, n_trials: int):
    """对单个基线运行 Optuna 调优"""
    objectives = {
        "rajomon": objective_rajomon,
        "dagor": objective_dagor,
        "sbac": objective_sbac,
        "srl": objective_srl,
        "plangate": objective_plangate,
    }

    if name not in objectives:
        print(f"未知基线: {name}")
        return

    print(f"\n{'='*60}")
    print(f"  Optuna 调优: {name.upper()}")
    print(f"  试验次数: {n_trials}")
    print(f"  每次时长: {TUNE_DURATION}s")
    print(f"{'='*60}\n")

    study = optuna.create_study(
        direction="maximize",
        study_name=f"tune_{name}",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(objectives[name], n_trials=n_trials, show_progress_bar=True)

    best = study.best_trial
    print(f"\n{'='*60}")
    print(f"  {name.upper()} 最优参数 (trial={best.number}, EffGoodput/s={best.value:.2f}):")
    for k, v in best.params.items():
        print(f"    {k} = {v}")
    print(f"{'='*60}\n")

    return best


def main():
    parser = argparse.ArgumentParser(
        description="Optuna 贝叶斯优化 — 为基线方案寻找最优参数",
    )
    parser.add_argument("--baseline", type=str, default="all",
                        choices=["rajomon", "dagor", "sbac", "srl", "plangate", "all"],
                        help="要调优的基线 (default: all)")
    parser.add_argument("--trials", type=int, default=30,
                        help="每个基线的 Optuna 试验次数 (default: 30)")
    parser.add_argument("--backend", type=str, default=BACKEND_URL,
                        help=f"Python MCP 后端地址 (default: {BACKEND_URL})")
    parser.add_argument("--backend-max-workers", type=int, default=BACKEND_MAX_WORKERS,
                        help=f"后端最大并发工作线程数，0=不管理后端 (default: {BACKEND_MAX_WORKERS})")
    args = parser.parse_args()

    # 更新后端地址
    update_backend_url(args.backend)

    backend_max_workers = args.backend_max_workers
    baselines = ["rajomon", "dagor", "sbac", "srl", "plangate"] if args.baseline == "all" else [args.baseline]

    if backend_max_workers > 0:
        start_backend(backend_max_workers)

    results = {}
    try:
        for bl in baselines:
            best = tune_baseline(bl, args.trials)
            if best:
                results[bl] = best
    finally:
        if backend_max_workers > 0:
            stop_backend()

    # 汇总打印（便于硬编码到正式脚本）
    if results:
        print("\n" + "=" * 60)
        print("  ★ 调优结果汇总 — 将以下参数硬编码到 run_all_experiments.py")
        print("=" * 60)
        for bl, best in results.items():
            params_str = ", ".join(f"{k}={v}" for k, v in best.params.items())
            print(f"  {bl}: {params_str}  (EffGoodput/s={best.value:.2f})")
        print("=" * 60)


if __name__ == "__main__":
    main()
