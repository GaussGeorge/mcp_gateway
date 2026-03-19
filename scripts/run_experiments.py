"""
run_experiments.py — MCP 服务治理 · Phase 3 实验自动化跑批
================================================================
自动执行 4 组核心实验，每组重复 3 次。

实验清单:
  Exp1: 核心负载与恢复能力 (Step 脉冲)       — NG / SRL / DP
  Exp2: Heavy Ratio 敏感性 (Poisson 稳态)     — NG / SRL / DP × [10%, 30%, 50%]
  Exp3: 预算公平性 (Poisson 稳态)             — NG / SRL / DP × budget_groups
  Exp4: 极简消融实验 (Poisson 稳态)           — DP-Full / DP-NoRegime / SRL

全部结果保存在 results/ 目录下，按实验分文件夹。

用法:
  python scripts/run_experiments.py                  # 跑全部
  python scripts/run_experiments.py --exp 1          # 只跑 Exp1
  python scripts/run_experiments.py --exp 2 --exp 3  # 跑 Exp2 和 Exp3
  python scripts/run_experiments.py --repeats 5      # 改为重复 5 次
  python scripts/run_experiments.py --dry-run        # 只打印命令不执行
"""

import argparse
import csv
import json
import os
import platform
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
GATEWAY_EXE = PROJECT_ROOT / "gateway.exe"
SERVER_PY = PROJECT_ROOT / "mcp_server" / "server.py"
LOAD_GEN = PROJECT_ROOT / "scripts" / "load_generator.py"
MONITOR_PY = PROJECT_ROOT / "scripts" / "monitor.py"

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8080
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

# 网关端口映射
GATEWAY_PORTS = {
    "ng":          9001,
    "srl":         9002,
    "dp":          9003,
    "dp-noregime": 9004,
}

# SRL 参数 — 和 DP 在同等负载下的通过率匹配
SRL_QPS = 50
SRL_BURST = 100
SRL_MAX_CONC = 20

# 实验通用参数
HEAVY_BURN_MS = 800          # mock_heavy CPU 烧录时间
CONCURRENCY = 100            # 最大并发数
WARMUP_SEC = 3               # 网关启动预热时间
COOLDOWN_SEC = 5             # 实验间冷却时间
BACKEND_AFFINITY = "4,5,6,7,8,9,10,11,12,13,14,15"
LOADGEN_AFFINITY = "0,1"


# ══════════════════════════════════════════════════
# 进程管理
# ══════════════════════════════════════════════════
class ProcessManager:
    """管理后端和网关进程的启停。"""

    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}

    def start_backend(self, mode="sterile"):
        """启动 Python MCP 后端。"""
        if "backend" in self.processes:
            return
        print(f"  [启动] MCP Backend (port {BACKEND_PORT}, mode={mode})")
        cmd = [
            sys.executable, str(SERVER_PY),
            "--host", BACKEND_HOST,
            "--port", str(BACKEND_PORT),
            "--mode", mode,
            "--cpu-affinity", BACKEND_AFFINITY,
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0,
        )
        self.processes["backend"] = proc
        time.sleep(WARMUP_SEC)
        if not self._check_port(BACKEND_PORT):
            raise RuntimeError(f"Backend 启动失败 (port {BACKEND_PORT})")
        print(f"  [就绪] Backend PID={proc.pid}")

    def start_gateway(self, mode: str):
        """启动指定策略的 Go 网关。"""
        key = f"gw-{mode}"
        if key in self.processes:
            return
        port = GATEWAY_PORTS[mode]
        print(f"  [启动] {mode.upper()} Gateway (port {port})")
        cmd = [
            str(GATEWAY_EXE),
            "--mode", mode,
            "--port", str(port),
            "--host", BACKEND_HOST,
            "--backend", BACKEND_URL,
        ]
        if mode == "srl":
            cmd += ["--srl-qps", str(SRL_QPS), "--srl-burst", str(SRL_BURST),
                     "--srl-max-conc", str(SRL_MAX_CONC)]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0,
        )
        self.processes[key] = proc
        time.sleep(WARMUP_SEC)
        if not self._check_port(port):
            raise RuntimeError(f"{mode} Gateway 启动失败 (port {port})")
        print(f"  [就绪] {mode.upper()} Gateway PID={proc.pid}")

    def stop_gateway(self, mode: str):
        """停止指定网关。"""
        key = f"gw-{mode}"
        if key in self.processes:
            proc = self.processes.pop(key)
            self._kill(proc)
            print(f"  [停止] {mode.upper()} Gateway")

    def stop_all_gateways(self):
        """停止所有网关。"""
        keys = [k for k in self.processes if k.startswith("gw-")]
        for key in keys:
            proc = self.processes.pop(key)
            self._kill(proc)
        if keys:
            print(f"  [停止] 所有网关已关闭")

    def stop_all(self):
        """停止所有进程。"""
        for key, proc in self.processes.items():
            self._kill(proc)
        self.processes.clear()
        print("  [停止] 所有进程已关闭")

    def _kill(self, proc: subprocess.Popen):
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass

    def _check_port(self, port: int, retries=5) -> bool:
        """检查端口是否已就绪。"""
        import urllib.request
        for i in range(retries):
            try:
                payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}).encode()
                req = urllib.request.Request(
                    f"http://{BACKEND_HOST}:{port}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=3)
                return True
            except Exception:
                time.sleep(1)
        return False


# ══════════════════════════════════════════════════
# 实验执行器
# ══════════════════════════════════════════════════
def run_load_generator(
    target_url: str,
    output_csv: str,
    waveform: str = "poisson",
    qps: float = 30,
    duration: float = 60,
    heavy_ratio: float = 0.2,
    budget: int = 100,
    budget_groups: str = None,
    step_stages: str = None,
    heavy_burn_ms: int = HEAVY_BURN_MS,
) -> bool:
    """运行发压机并等待完成。"""
    cmd = [
        sys.executable, str(LOAD_GEN),
        "--target", target_url,
        "--waveform", waveform,
        "--duration", str(duration),
        "--heavy-ratio", str(heavy_ratio),
        "--concurrency", str(CONCURRENCY),
        "--output", output_csv,
        "--cpu-affinity", LOADGEN_AFFINITY,
        "--heavy-burn-ms", str(heavy_burn_ms),
    ]
    if waveform == "poisson":
        cmd += ["--qps", str(qps)]
    if budget_groups:
        cmd += ["--budget-groups", budget_groups]
    else:
        cmd += ["--budget", str(budget)]
    if step_stages and waveform == "step":
        cmd += ["--step-stages", step_stages]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    # 打印输出（简要）
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            print(f"    {line}")

    if result.returncode != 0:
        print(f"    [ERROR] 发压机返回码 {result.returncode}")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-5:]:
                print(f"    STDERR: {line}")
        return False
    return True


def run_single_experiment(
    pm: ProcessManager,
    gateway_mode: str,
    exp_name: str,
    run_idx: int,
    output_dir: Path,
    **load_kwargs,
) -> str:
    """运行单次实验（启动网关→发压→停止网关）。"""
    output_csv = str(output_dir / f"{exp_name}_{gateway_mode}_run{run_idx}.csv")
    port = GATEWAY_PORTS[gateway_mode]
    target_url = f"http://{BACKEND_HOST}:{port}"

    print(f"\n  --- {exp_name} | {gateway_mode.upper()} | Run {run_idx} ---")

    # 启动网关
    pm.start_gateway(gateway_mode)

    # 运行发压
    success = run_load_generator(target_url=target_url, output_csv=output_csv, **load_kwargs)

    # 停止网关（每次实验后重启，保持状态干净）
    pm.stop_gateway(gateway_mode)

    # 冷却
    time.sleep(COOLDOWN_SEC)

    return output_csv if success else None


# ══════════════════════════════════════════════════
# Exp1: 核心负载与恢复能力 (Step 脉冲)
# ══════════════════════════════════════════════════
def run_exp1(pm: ProcessManager, repeats: int, dry_run: bool):
    """
    Exp1: Step 脉冲 — 低→高→低→高→低
    对比 NG (崩溃) / SRL (盲拒) / DP (智能熔断+恢复)
    """
    exp_dir = RESULTS_DIR / "exp1_step_pulse"
    exp_dir.mkdir(parents=True, exist_ok=True)

    # Step 阶段设计:
    #   0-10s:  QPS=10  (低负载基线)
    #   10-25s: QPS=60  (高负载浪涌)
    #   25-35s: QPS=10  (恢复期)
    #   35-45s: QPS=80  (更强浪涌)
    #   45-60s: QPS=10  (最终恢复)
    step_stages = "0:10:10,10:60:15,25:10:10,35:80:10,45:10:15"
    duration = 60
    heavy_ratio = 0.2

    gateways = ["ng", "srl", "dp"]

    print(f"\n{'=' * 60}")
    print(f"  Exp1: 核心负载与恢复能力 (Step 脉冲)")
    print(f"  阶段: {step_stages}")
    print(f"  网关: {gateways}")
    print(f"  重复: {repeats} 次")
    print(f"{'=' * 60}")

    if dry_run:
        print("  [DRY-RUN] 跳过执行")
        return

    for run_idx in range(1, repeats + 1):
        for gw in gateways:
            run_single_experiment(
                pm, gw, "exp1", run_idx, exp_dir,
                waveform="step",
                step_stages=step_stages,
                duration=duration,
                heavy_ratio=heavy_ratio,
                budget=100,
            )


# ══════════════════════════════════════════════════
# Exp2: Heavy Ratio 敏感性 (Poisson 稳态)
# ══════════════════════════════════════════════════
def run_exp2(pm: ProcessManager, repeats: int, dry_run: bool):
    """
    Exp2: 改变 heavy ratio (10%, 30%, 50%)
    固定 QPS 看不同重载比对吞吐和 Goodput 的影响
    """
    exp_dir = RESULTS_DIR / "exp2_heavy_ratio"
    exp_dir.mkdir(parents=True, exist_ok=True)

    qps = 40
    duration = 60
    heavy_ratios = [0.1, 0.3, 0.5]
    gateways = ["ng", "srl", "dp"]

    print(f"\n{'=' * 60}")
    print(f"  Exp2: Heavy Ratio 敏感性 (Poisson)")
    print(f"  QPS: {qps}, Heavy Ratios: {heavy_ratios}")
    print(f"  网关: {gateways}")
    print(f"  重复: {repeats} 次")
    print(f"{'=' * 60}")

    if dry_run:
        print("  [DRY-RUN] 跳过执行")
        return

    for run_idx in range(1, repeats + 1):
        for hr in heavy_ratios:
            hr_tag = f"hr{int(hr * 100)}"
            for gw in gateways:
                run_single_experiment(
                    pm, gw, f"exp2_{hr_tag}", run_idx, exp_dir,
                    waveform="poisson",
                    qps=qps,
                    duration=duration,
                    heavy_ratio=hr,
                    budget=100,
                )


# ══════════════════════════════════════════════════
# Exp3: 预算公平性 (Poisson 稳态)
# ══════════════════════════════════════════════════
def run_exp3(pm: ProcessManager, repeats: int, dry_run: bool):
    """
    Exp3: 高负载下双预算组 (budget=10 vs 100, 各 50%)
    对比各网关是否能区分高低预算用户
    """
    exp_dir = RESULTS_DIR / "exp3_budget_fairness"
    exp_dir.mkdir(parents=True, exist_ok=True)

    qps = 50
    duration = 60
    heavy_ratio = 0.3
    budget_groups = "10:50,100:50"
    gateways = ["ng", "srl", "dp"]

    print(f"\n{'=' * 60}")
    print(f"  Exp3: 预算公平性 (Poisson)")
    print(f"  QPS: {qps}, Heavy Ratio: {heavy_ratio}")
    print(f"  预算组: {budget_groups}")
    print(f"  网关: {gateways}")
    print(f"  重复: {repeats} 次")
    print(f"{'=' * 60}")

    if dry_run:
        print("  [DRY-RUN] 跳过执行")
        return

    for run_idx in range(1, repeats + 1):
        for gw in gateways:
            run_single_experiment(
                pm, gw, "exp3", run_idx, exp_dir,
                waveform="poisson",
                qps=qps,
                duration=duration,
                heavy_ratio=heavy_ratio,
                budget_groups=budget_groups,
            )


# ══════════════════════════════════════════════════
# Exp4: 消融实验 (Composite 复合流量 — 验证自适应档位在多模式负载下的价值)
# ══════════════════════════════════════════════════
def run_exp4(pm: ProcessManager, repeats: int, dry_run: bool):
    """
    Exp4: 消融实验 — DP-Full vs DP-NoRegime vs SRL
    使用 120 秒复合流量 (过山车波形):
      Phase 1 (0-20s):   Poisson 稳态 QPS=30
      Phase 2 (20-45s):  突发 QPS=120
      Phase 3 (45-80s):  正弦波 QPS=30~90, 周期=5s
      Phase 4 (80-100s): 极低负载 QPS=10
      Phase 5 (100-120s): 微脉冲方波 QPS=100/10, 周期=4s
    """
    exp_dir = RESULTS_DIR / "exp4_ablation"
    exp_dir.mkdir(parents=True, exist_ok=True)

    duration = 120
    heavy_ratio = 0.3
    gateways = ["dp", "dp-noregime", "srl"]

    print(f"\n{'=' * 60}")
    print(f"  Exp4: 消融实验 (Composite 复合流量, 120s 过山车)")
    print(f"  阶段: steady(30)→burst(120)→sine(30~90)→idle(10)→square(100/10)")
    print(f"  Heavy Ratio: {heavy_ratio}")
    print(f"  网关: {gateways}")
    print(f"  重复: {repeats} 次")
    print(f"{'=' * 60}")

    if dry_run:
        print("  [DRY-RUN] 跳过执行")
        return

    for run_idx in range(1, repeats + 1):
        for gw in gateways:
            run_single_experiment(
                pm, gw, "exp4", run_idx, exp_dir,
                waveform="composite",
                duration=duration,
                heavy_ratio=heavy_ratio,
                budget=100,
            )


# ══════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="MCP Phase 3 实验自动化跑批")
    parser.add_argument("--exp", type=int, action="append", default=None,
                        help="指定要运行的实验编号 (1-4), 可多次指定")
    parser.add_argument("--repeats", type=int, default=3,
                        help="每组实验重复次数 (default: 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划不执行")
    args = parser.parse_args()

    exps_to_run = args.exp or [1, 2, 3, 4]

    print("=" * 60)
    print("  MCP 服务治理 · Phase 3 实验跑批")
    print(f"  实验: {exps_to_run}")
    print(f"  重复: {args.repeats} 次/组")
    print(f"  结果: {RESULTS_DIR}")
    print("=" * 60)

    # 确保 gateway 已编译
    if not GATEWAY_EXE.exists():
        print("[编译] 构建 Go 网关...")
        result = subprocess.run(
            ["go", "build", "-o", str(GATEWAY_EXE), "./cmd/gateway/"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"[错误] 编译失败:\n{result.stderr}")
            sys.exit(1)

    pm = ProcessManager()

    try:
        # 启动后端 (全程共享)
        pm.start_backend(mode="sterile")

        exp_funcs = {
            1: run_exp1,
            2: run_exp2,
            3: run_exp3,
            4: run_exp4,
        }

        start_time = time.time()

        for exp_id in exps_to_run:
            if exp_id in exp_funcs:
                exp_funcs[exp_id](pm, args.repeats, args.dry_run)
            else:
                print(f"[警告] 未知实验编号: {exp_id}")

        # 确保所有网关已关闭
        pm.stop_all_gateways()

        elapsed = time.time() - start_time
        print(f"\n{'=' * 60}")
        print(f"  全部实验完成！")
        print(f"  总耗时: {elapsed/60:.1f} 分钟")
        print(f"  结果目录: {RESULTS_DIR}")
        print(f"{'=' * 60}")

    except KeyboardInterrupt:
        print("\n[中断] 用户取消，正在清理...")
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
    finally:
        pm.stop_all()


if __name__ == "__main__":
    main()
