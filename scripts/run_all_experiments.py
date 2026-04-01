#!/usr/bin/env python3
"""
run_all_experiments.py — 无人值守自动化跑批脚本

5 组实验 × 6 个网关 × N=5 次重复 = 最多 125 个实验实例
每次循环: 启动 Go 网关 → 运行 DAG 发压机 → 收集 CSV → 终止网关

实验配置：
  Exp1_Core:        Step 脉冲突发，ps_ratio=1.0，核心性能对比
  Exp2_HeavyRatio:  重量工具占比扫参 [0.1, 0.3, 0.5, 0.7]
  Exp3_MixedMode:   混合模式 P&S+ReAct, ps_ratio=[0.3, 0.5, 0.7, 1.0]
  Exp4_Ablation:    消融实验 Full vs w/o-BudgetLock vs w/o-SessionCap vs Rajomon
  Exp5_ScaleConc:   并发扩展测试 (P&S) concurrency=[10, 20, 40, 60]
  Exp6_ScaleConcReact: 并发扩展测试 (纯ReAct) concurrency=[10, 20, 40, 60]

用法：
  python scripts/run_all_experiments.py                          # 跑全部
  python scripts/run_all_experiments.py --exp Exp1_Core          # 只跑 Exp1
  python scripts/run_all_experiments.py --exp Exp4_Ablation      # 只跑 Exp4 消融
  python scripts/run_all_experiments.py --repeats 3              # 每组只重复 3 次
  python scripts/run_all_experiments.py --dry-run                # 试运行不实际执行
"""

import argparse
import csv
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

# ====== 路径配置 ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
GATEWAY_BIN_DIR = os.path.join(ROOT_DIR, "cmd", "gateway")
GATEWAY_BINARY = None  # 预编译后设置
DAG_LOAD_GEN = os.path.join(SCRIPT_DIR, "dag_load_generator.py")
RESULTS_DIR = os.path.join(ROOT_DIR, "results")

# ====== 全局默认参数 ======
BACKEND_URL = "http://127.0.0.1:8080"


def update_backend_url(url: str):
    global BACKEND_URL
    BACKEND_URL = url
GATEWAY_HOST = "127.0.0.1"

# ====== taskset CPU 绑核配置 (Linux only) ======
CPU_BACKEND = None   # e.g. "8-15"
CPU_GATEWAY = None   # e.g. "4-7"
CPU_LOADGEN = None   # e.g. "0-3"


def _taskset_prefix(cpu_spec: str) -> list:
    """如果指定了 CPU 核心且运行在 Linux 上，返回 taskset 前缀"""
    if cpu_spec and sys.platform != "win32":
        return ["taskset", "-c", cpu_spec]
    return []
BASE_PORT = 9100  # 实验专用端口段起始
STARTUP_WAIT = 4  # 网关启动等待秒数
DEFAULT_REPEATS = 5

# ====== 调优后的基线最优参数（由 tune_baselines.py 输出后硬编码） ======
TUNED_PARAMS = {
    "rajomon":       {"price_step": 20},
    "dagor":         {"rtt_threshold": 400.0, "price_step": 10},
    "sbac":          {"max_sessions": 150},
    "srl":           {"qps": 65.0, "burst": 400, "max_conc": 55},
    # PlanGate: Optuna 调优结果 (max_sessions=30, price_step=40, sunk_cost_alpha=0.5)
    "plangate_full": {"price_step": 40, "max_sessions": 30, "sunk_cost_alpha": 0.5},
}


# ====== 网关定义 ======
@dataclass
class GatewayConfig:
    """网关模式配置"""
    name: str          # 显示名称 (用于目录和文件名)
    mode: str          # --mode 参数
    extra_args: list = field(default_factory=list)  # 额外 CLI 参数


def get_gateways(experiment_name: str) -> List[GatewayConfig]:
    """根据实验名称返回需要测试的网关列表"""
    rj = TUNED_PARAMS["rajomon"]
    dg = TUNED_PARAMS["dagor"]
    sb = TUNED_PARAMS["sbac"]
    srl = TUNED_PARAMS["srl"]
    pg = TUNED_PARAMS["plangate_full"]

    common = [
        GatewayConfig("ng", "ng"),
        GatewayConfig("srl", "srl", [
            "--srl-qps", str(srl["qps"]),
            "--srl-burst", str(srl["burst"]),
            "--srl-max-conc", str(srl["max_conc"]),
        ]),
        GatewayConfig("rajomon", "rajomon", [
            "--rajomon-price-step", str(rj["price_step"]),
        ]),
        GatewayConfig("dagor", "dagor", [
            "--dagor-rtt-threshold", str(dg["rtt_threshold"]),
            "--dagor-price-step", str(dg["price_step"]),
        ]),
        GatewayConfig("sbac", "sbac", [
            "--sbac-max-sessions", str(sb["max_sessions"]),
        ]),
        GatewayConfig("plangate_full", "mcpdp", [
            "--plangate-price-step", str(pg["price_step"]),
            "--plangate-max-sessions", str(pg["max_sessions"]),
            "--plangate-sunk-cost-alpha", str(pg["sunk_cost_alpha"]),
        ]),
    ]

    if experiment_name == "Exp4_Ablation":
        return [
            GatewayConfig("plangate_full", "mcpdp", [
                "--plangate-price-step", str(pg["price_step"]),
                "--plangate-max-sessions", str(pg["max_sessions"]),
                "--plangate-sunk-cost-alpha", str(pg["sunk_cost_alpha"]),
            ]),
            GatewayConfig("wo_budgetlock", "mcpdp-no-budgetlock", [
                "--plangate-price-step", str(pg["price_step"]),
                "--plangate-max-sessions", str(pg["max_sessions"]),
                "--plangate-sunk-cost-alpha", str(pg["sunk_cost_alpha"]),
            ]),
            GatewayConfig("wo_sessioncap", "mcpdp-no-sessioncap", [
                "--plangate-price-step", str(pg["price_step"]),
                "--plangate-max-sessions", str(pg["max_sessions"]),
                "--plangate-sunk-cost-alpha", str(pg["sunk_cost_alpha"]),
            ]),
            GatewayConfig("rajomon", "rajomon", [
                "--rajomon-price-step", str(rj["price_step"]),
            ]),
        ]

    if experiment_name == "Exp7_ClientReject":
        # Exp7: 对比 PlanGate (Shadow) vs PlanGate (Hard Reject)
        # 两者都使用相同网关, hard_reject 在发压机侧控制
        return [
            GatewayConfig("plangate_full", "mcpdp", [
                "--plangate-price-step", str(pg["price_step"]),
                "--plangate-max-sessions", str(pg["max_sessions"]),
                "--plangate-sunk-cost-alpha", str(pg["sunk_cost_alpha"]),
            ]),
        ]

    # 其余实验: 全部 6 个网关 (NG, SRL, Rajomon, DAGOR, SBAC, PlanGate-Full)
    return common


# ====== 实验定义 ======
@dataclass
class ExperimentConfig:
    """单组实验配置"""
    name: str
    description: str
    # DAG 发压机参数（可扫参）
    sessions: int = 200
    ps_ratio: float = 1.0
    budget: int = 500
    heavy_ratio: float = 0.3
    concurrency: int = 20
    arrival_rate: float = 15.0
    duration: int = 60
    min_steps: int = 3
    max_steps: int = 7
    step_timeout: float = 2.0  # 单步超时秒数（短超时是造成系统真实过载的关键）
    hard_reject: bool = False   # 客户端 Hard Reject 模式
    # 扫参维度 (key → list of values)
    sweep: Optional[Dict[str, list]] = None


EXPERIMENTS = {
    "Exp1_Core": ExperimentConfig(
        name="Exp1_Core",
        description="Step 脉冲突发 — 核心性能对比（全载常态）",
        sessions=500,
        ps_ratio=1.0,
        duration=60,
        arrival_rate=50.0,
        heavy_ratio=0.3,
        concurrency=200,
    ),
    "Exp2_HeavyRatio": ExperimentConfig(
        name="Exp2_HeavyRatio",
        description="重量工具占比扫参",
        sessions=200,
        ps_ratio=1.0,
        duration=60,
        sweep={"heavy_ratio": [0.1, 0.3, 0.5, 0.7]},
    ),
    "Exp3_MixedMode": ExperimentConfig(
        name="Exp3_MixedMode",
        description="混合模式 P&S + ReAct 比例扫参",
        sessions=200,
        duration=60,
        sweep={"ps_ratio": [0.0, 0.3, 0.5, 0.7, 1.0]},
    ),
    "Exp4_Ablation": ExperimentConfig(
        name="Exp4_Ablation",
        description="严格单变量消融实验: Full vs w/o-BudgetLock vs w/o-SessionCap vs Rajomon(SOTA)",
        sessions=500,
        ps_ratio=1.0,
        duration=60,
        arrival_rate=50.0,
        heavy_ratio=0.3,
        concurrency=200,
    ),
    "Exp5_ScaleConc": ExperimentConfig(
        name="Exp5_ScaleConc",
        description="并发扩展测试",
        sessions=200,
        ps_ratio=1.0,
        duration=60,
        sweep={"concurrency": [10, 20, 40, 60]},
    ),
    "Exp6_ScaleConcReact": ExperimentConfig(
        name="Exp6_ScaleConcReact",
        description="纯 ReAct 下的并发扩展测试 (ps_ratio=0.0)",
        sessions=200,
        ps_ratio=0.0,
        duration=60,
        sweep={"concurrency": [10, 20, 40, 60]},
    ),
    "Exp7_ClientReject": ExperimentConfig(
        name="Exp7_ClientReject",
        description="客户端 Hard Reject — price_ttl 扫参验证最优缓存时效",
        sessions=500,
        ps_ratio=1.0,
        duration=60,
        arrival_rate=50.0,
        heavy_ratio=0.3,
        concurrency=200,
        hard_reject=True,
        sweep={"price_ttl": [0.1, 0.2, 0.5, 1.0, 2.0]},
    ),
}


# ====== 进程管理 ======

def build_gateway():
    """预编译网关二进制文件（避免每次 go run 重新编译）"""
    global GATEWAY_BINARY
    # 如果已通过 --gateway-binary 指定了预编译二进制, 则跳过编译
    if GATEWAY_BINARY is not None:
        if os.path.isfile(GATEWAY_BINARY):
            print(f"  使用预编译网关: {GATEWAY_BINARY}")
            return
        else:
            raise RuntimeError(f"指定的网关二进制不存在: {GATEWAY_BINARY}")

    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = os.path.join(ROOT_DIR, bin_name)

    print(f"  预编译网关: go build -o {bin_name} ./cmd/gateway")
    result = subprocess.run(
        ["go", "build", "-o", bin_path, "./cmd/gateway"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"网关编译失败: {result.stderr}")
    GATEWAY_BINARY = bin_path
    print(f"  编译完成: {bin_path}")


# ====== 后端生命周期管理 ======
BACKEND_PROC: Optional[subprocess.Popen] = None
BACKEND_MAX_WORKERS = 10        # 全局并发 worker 数（模拟 ThreadPoolExecutor 瓶颈）
SERVER_PY = os.path.join(os.path.dirname(SCRIPT_DIR), "mcp_server", "server.py")
if not os.path.exists(SERVER_PY):
    SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")


def start_backend(max_workers: int = BACKEND_MAX_WORKERS):
    """启动 Python MCP 后端（限制全局并发 worker 数以造成真实过载）"""
    global BACKEND_PROC
    stop_backend()  # 先终止旧实例

    base_cmd = [
        sys.executable, SERVER_PY,
        "--port", "8080",
        "--max-workers", str(max_workers),
        "--queue-timeout", "1.0",
        "--congestion-factor", "0.5",
    ]
    cmd = _taskset_prefix(CPU_BACKEND) + base_cmd
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = os.path.join(RESULTS_DIR, "_backend.log")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as lf:
        BACKEND_PROC = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(SERVER_PY),
            stdout=lf, stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    time.sleep(3)
    if BACKEND_PROC.poll() is not None:
        raise RuntimeError(f"后端启动失败，查看日志: {log_path}")
    print(f"  后端已启动 (pid={BACKEND_PROC.pid}, max_workers={max_workers})")


def stop_backend():
    """安全终止后端进程"""
    global BACKEND_PROC
    if BACKEND_PROC is None:
        return
    if BACKEND_PROC.poll() is not None:
        BACKEND_PROC = None
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


def start_gateway(gw: GatewayConfig, port: int) -> subprocess.Popen:
    """启动网关子进程（使用预编译二进制）"""
    if GATEWAY_BINARY is None:
        build_gateway()

    base_cmd = [
        GATEWAY_BINARY,
        "--mode", gw.mode,
        "--port", str(port),
        "--backend", BACKEND_URL,
        "--host", GATEWAY_HOST,
    ] + gw.extra_args
    cmd = _taskset_prefix(CPU_GATEWAY) + base_cmd

    print(f"    启动网关: {gw.name} (mode={gw.mode}, port={port})")
    # 网关日志写入文件，避免 PIPE 缓冲区满导致网关阻塞
    gw_log_path = os.path.join(RESULTS_DIR, f"_gateway_{gw.name}_{port}.log")
    gw_log_file = open(gw_log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=gw_log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    proc._gw_log_file = gw_log_file  # 保留引用，stop 时关闭
    # 等待网关启动并验证可访问性
    if not wait_for_gateway(port, proc, timeout=15):
        gw_log_file.close()
        log_content = ""
        try:
            with open(gw_log_path, "r", encoding="utf-8") as f:
                log_content = f.read()[:500]
        except Exception:
            pass
        stop_gateway(proc)
        raise RuntimeError(f"网关 {gw.name} 启动超时 (port={port}): {log_content}")
    return proc


def wait_for_gateway(port: int, proc: subprocess.Popen, timeout: int = 15) -> bool:
    """轮询直到网关能响应 ping 请求"""
    deadline = time.time() + timeout
    ping_body = json.dumps({"jsonrpc": "2.0", "id": "hc", "method": "ping"}).encode()
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            req = Request(
                f"http://{GATEWAY_HOST}:{port}",
                data=ping_body,
                headers={"Content-Type": "application/json"},
            )
            resp = urlopen(req, timeout=2)
            data = json.loads(resp.read())
            if data.get("jsonrpc") == "2.0":
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def stop_gateway(proc: subprocess.Popen):
    """安全终止网关进程（Windows 兼容）"""
    # 关闭网关日志文件句柄
    gw_log = getattr(proc, "_gw_log_file", None)
    if gw_log:
        try:
            gw_log.close()
        except Exception:
            pass
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            # Windows 上直接 kill 进程树最可靠
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


def run_load_generator(target_url: str, exp: ExperimentConfig,
                       output_csv: str, overrides: dict = None) -> dict:
    """运行 DAG 发压机，返回解析后的统计结果。
    使用文件重定向代替 capture_output=True，避免 Windows 上 asyncio+PIPE 死锁。
    """
    params = {
        "sessions": exp.sessions,
        "ps_ratio": exp.ps_ratio,
        "budget": exp.budget,
        "heavy_ratio": exp.heavy_ratio,
        "concurrency": exp.concurrency,
        "arrival_rate": exp.arrival_rate,
        "duration": exp.duration,
        "min_steps": exp.min_steps,
        "max_steps": exp.max_steps,
        "step_timeout": exp.step_timeout,
        "hard_reject": exp.hard_reject,
    }
    if overrides:
        params.update(overrides)

    base_cmd = [
        sys.executable, DAG_LOAD_GEN,
        "--target", target_url,
        "--sessions", str(params["sessions"]),
        "--ps-ratio", str(params["ps_ratio"]),
        "--budget", str(params["budget"]),
        "--heavy-ratio", str(params["heavy_ratio"]),
        "--concurrency", str(params["concurrency"]),
        "--arrival-rate", str(params["arrival_rate"]),
        "--duration", str(params["duration"]),
        "--min-steps", str(params["min_steps"]),
        "--max-steps", str(params["max_steps"]),
        "--step-timeout", str(params["step_timeout"]),
        "--price-ttl", str(params.get("price_ttl", 1.0)),
        "--output", output_csv,
    ]
    if params.get("hard_reject"):
        base_cmd.append("--hard-reject")
    cmd = _taskset_prefix(CPU_LOADGEN) + base_cmd

    print(f"    发压: sessions={params['sessions']} ps_ratio={params['ps_ratio']} "
          f"heavy={params['heavy_ratio']} conc={params['concurrency']} dur={params['duration']}s")

    # 使用日志文件代替管道捕获，避免 Windows asyncio + subprocess PIPE 死锁
    log_path = output_csv.replace(".csv", "_stdout.log")
    timeout_sec = params["duration"] + 180
    # 强制子进程使用 UTF-8 输出
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
            try:
                retcode = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                print(f"    [TIMEOUT] 发压机超时 ({timeout_sec}s), 强制终止...")
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10)
                else:
                    proc.kill()
                proc.wait(timeout=5)
                return {"error": "timeout", "returncode": -1}
    except Exception as e:
        print(f"    [ERROR] 发压机启动失败: {e}")
        return {"error": str(e), "returncode": -1}

    # 从日志文件中读取 stdout 内容
    stdout_text = ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            stdout_text = f.read()
    except Exception:
        pass

    stats = parse_stdout_stats(stdout_text)
    stats["returncode"] = retcode
    if retcode != 0:
        # 从日志末尾提取错误信息
        stats["error"] = stdout_text[-300:] if stdout_text else "unknown error"
        print(f"    [WARN] 发压机退出码={retcode}")

    return stats


def parse_stdout_stats(stdout: str) -> dict:
    """从发压机 stdout 中提取关键指标"""
    stats = {}
    for line in stdout.split("\n"):
        line = line.strip()
        if "SUCCESS:" in line and "├─" in line:
            try:
                stats["success"] = int(line.split("SUCCESS:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "REJECTED@S0:" in line:
            try:
                stats["rejected_s0"] = int(line.split("REJECTED@S0:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "CASCADE_FAIL:" in line:
            try:
                stats["cascade_failed"] = int(line.split("CASCADE_FAIL:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "Raw Goodput (单步累加):" in line:
            try:
                stats["raw_goodput"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "Effective Goodput (全链路):" in line:
            try:
                stats["effective_goodput"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "Raw Goodput/s:" in line:
            try:
                stats["raw_goodput_s"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "Effective Goodput/s:" in line:
            try:
                stats["effective_goodput_s"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "P50:" in line and "E2E" not in line:
            try:
                stats["p50_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "P95:" in line and "E2E" not in line:
            try:
                stats["p95_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "P99:" in line and "E2E" not in line:
            try:
                stats["p99_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "E2E_P50:" in line:
            try:
                stats["e2e_p50_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "E2E_P95:" in line:
            try:
                stats["e2e_p95_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "E2E_P99:" in line:
            try:
                stats["e2e_p99_ms"] = float(line.split(":")[-1].strip())
            except ValueError:
                pass
    return stats


# ====== 实验执行器 ======

def run_single_trial(exp: ExperimentConfig, gw: GatewayConfig,
                     run_idx: int, port: int, output_dir: str,
                     overrides: dict = None, dry_run: bool = False) -> dict:
    """执行单次实验实例"""
    # 构建文件名
    suffix = ""
    if overrides:
        for k, v in overrides.items():
            suffix += f"_{k}{v}"
    csv_name = f"{gw.name}{suffix}_run{run_idx}.csv"
    csv_path = os.path.join(output_dir, csv_name)

    if dry_run:
        print(f"    [DRY-RUN] {csv_name}")
        return {"dry_run": True}

    proc = None
    try:
        proc = start_gateway(gw, port)
        target_url = f"http://{GATEWAY_HOST}:{port}"
        stats = run_load_generator(target_url, exp, csv_path, overrides)
        stats["gateway"] = gw.name
        stats["run_idx"] = run_idx
        stats["csv"] = csv_name
        return stats
    except Exception as e:
        print(f"    [ERROR] {e}")
        return {"gateway": gw.name, "run_idx": run_idx, "error": str(e)}
    finally:
        if proc:
            stop_gateway(proc)
        # 冷却期（等待端口释放 + 后端冷却）
        time.sleep(3)


def run_experiment(exp_name: str, repeats: int, dry_run: bool = False):
    """执行一组完整实验"""
    if exp_name not in EXPERIMENTS:
        print(f"未知实验: {exp_name}")
        return []

    exp = EXPERIMENTS[exp_name]
    gateways = get_gateways(exp_name)

    print(f"\n{'='*70}")
    print(f"  实验: {exp.name} — {exp.description}")
    print(f"  网关: {', '.join(g.name for g in gateways)}")
    print(f"  重复: {repeats} 次")
    if exp.sweep:
        for k, vals in exp.sweep.items():
            print(f"  扫参: {k} = {vals}")
    print(f"{'='*70}\n")

    exp_dir = os.path.join(RESULTS_DIR, exp_name.lower())
    os.makedirs(exp_dir, exist_ok=True)

    all_results = []
    port_counter = BASE_PORT

    if exp.sweep:
        # 有扫参：三层嵌套（sweep_val × gateway × repeat）
        for sweep_key, sweep_vals in exp.sweep.items():
            for sweep_val in sweep_vals:
                for gw in gateways:
                    for run_idx in range(1, repeats + 1):
                        port_counter += 1
                        port = BASE_PORT + (port_counter % 100)

                        overrides = {sweep_key: sweep_val}
                        tag = f"{exp.name}/{gw.name}/{sweep_key}={sweep_val}/run{run_idx}"
                        print(f"  [{tag}]")

                        stats = run_single_trial(
                            exp, gw, run_idx, port, exp_dir,
                            overrides=overrides, dry_run=dry_run,
                        )
                        stats["sweep_key"] = sweep_key
                        stats["sweep_val"] = sweep_val
                        all_results.append(stats)
    else:
        # 无扫参：二层嵌套（gateway × repeat）
        for gw in gateways:
            for run_idx in range(1, repeats + 1):
                port_counter += 1
                port = BASE_PORT + (port_counter % 100)

                tag = f"{exp.name}/{gw.name}/run{run_idx}"
                print(f"  [{tag}]")

                stats = run_single_trial(
                    exp, gw, run_idx, port, exp_dir,
                    dry_run=dry_run,
                )
                all_results.append(stats)

    # 保存实验汇总
    if not dry_run and all_results:
        save_experiment_summary(exp_name, exp_dir, all_results)

    return all_results


def save_experiment_summary(exp_name: str, exp_dir: str, results: list):
    """保存实验汇总 CSV"""
    summary_path = os.path.join(exp_dir, f"{exp_name.lower()}_summary.csv")
    fieldnames = [
        "gateway", "run_idx", "sweep_key", "sweep_val",
        "success", "rejected_s0", "cascade_failed",
        "raw_goodput", "effective_goodput",
        "raw_goodput_s", "effective_goodput_s",
        "p50_ms", "p95_ms", "p99_ms",
        "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
        "csv", "error",
    ]

    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            if not r.get("dry_run"):
                writer.writerow(r)

    print(f"\n  汇总已保存: {summary_path}")


# ====== 主函数 ======

def main():
    parser = argparse.ArgumentParser(
        description="MCP-DP 无人值守自动化跑批脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--exp", type=str, default="all",
                        help="指定实验 (Exp1_Core/Exp2_HeavyRatio/Exp3_MixedMode/Exp4_Ablation/Exp5_ScaleConc/Exp6_ScaleConcReact/all)")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                        help=f"每组重复次数 (default: {DEFAULT_REPEATS})")
    parser.add_argument("--backend", type=str, default=BACKEND_URL,
                        help=f"Python MCP 后端地址 (default: {BACKEND_URL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行：打印执行计划但不实际启动")
    parser.add_argument("--exp-list", type=str, nargs="+", default=None,
                        help="指定多个实验 (空格分隔)")
    parser.add_argument("--backend-max-workers", type=int, default=BACKEND_MAX_WORKERS,
                        help=f"后端全局并发 worker 数，0=外部自行启动 (default: {BACKEND_MAX_WORKERS})")
    parser.add_argument("--gateway-binary", type=str, default=None,
                        help="预编译网关二进制路径 (跳过 go build)")
    parser.add_argument("--cpu-backend", type=str, default=None,
                        help="后端 CPU 核心 (taskset -c, 如 8-15)")
    parser.add_argument("--cpu-gateway", type=str, default=None,
                        help="网关 CPU 核心 (taskset -c, 如 4-7)")
    parser.add_argument("--cpu-loadgen", type=str, default=None,
                        help="发压机 CPU 核心 (taskset -c, 如 0-3)")
    args = parser.parse_args()

    # 更新后端地址
    update_backend_url(args.backend)

    # 设置预编译网关路径
    global GATEWAY_BINARY, CPU_BACKEND, CPU_GATEWAY, CPU_LOADGEN
    if args.gateway_binary:
        GATEWAY_BINARY = args.gateway_binary

    # 设置 taskset CPU 绑核
    CPU_BACKEND = args.cpu_backend
    CPU_GATEWAY = args.cpu_gateway
    CPU_LOADGEN = args.cpu_loadgen

    # 确定要运行的实验列表
    if args.exp_list:
        exp_names = args.exp_list
    elif args.exp == "all":
        exp_names = list(EXPERIMENTS.keys())
    else:
        exp_names = [args.exp]

    # 验证实验名
    for name in exp_names:
        if name not in EXPERIMENTS:
            print(f"未知实验: {name}")
            print(f"可选: {', '.join(EXPERIMENTS.keys())}")
            sys.exit(1)

    # 计算总实验实例数
    total = 0
    for name in exp_names:
        exp = EXPERIMENTS[name]
        gw_count = len(get_gateways(name))
        if exp.sweep:
            sweep_count = sum(len(v) for v in exp.sweep.values())
            total += sweep_count * gw_count * args.repeats
        else:
            total += gw_count * args.repeats

    print(f"\n{'#'*70}")
    print(f"  MCP-DP 自动化跑批脚本")
    print(f"  实验: {', '.join(exp_names)}")
    print(f"  总实例数: {total}")
    print(f"  重复次数: {args.repeats}")
    print(f"  后端: {BACKEND_URL}")
    if args.dry_run:
        print(f"  模式: DRY-RUN")
    print(f"  后端 max_workers: {args.backend_max_workers}")
    if CPU_BACKEND or CPU_GATEWAY or CPU_LOADGEN:
        print(f"  CPU 绑核 (taskset): 后端={CPU_BACKEND or 'off'}, 网关={CPU_GATEWAY or 'off'}, 发压={CPU_LOADGEN or 'off'}")
    if GATEWAY_BINARY:
        print(f"  网关二进制: {GATEWAY_BINARY}")
    print(f"{'#'*70}")

    os.makedirs(RESULTS_DIR, exist_ok=True)

    # 启动/重启 Python 后端（max_workers 限制全局并发）
    if args.backend_max_workers > 0 and not args.dry_run:
        print(f"  启动后端 (max_workers={args.backend_max_workers})…")
        start_backend(args.backend_max_workers)

    start_time = time.time()
    all_results = {}
    try:
        for name in exp_names:
            results = run_experiment(name, args.repeats, dry_run=args.dry_run)
            all_results[name] = results
    finally:
        if args.backend_max_workers > 0 and not args.dry_run:
            stop_backend()

    elapsed = time.time() - start_time
    print(f"\n{'#'*70}")
    print(f"  全部完成!")
    print(f"  耗时: {elapsed/60:.1f} 分钟")
    print(f"  结果目录: {RESULTS_DIR}")
    print(f"{'#'*70}")


if __name__ == "__main__":
    main()
