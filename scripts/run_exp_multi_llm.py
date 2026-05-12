#!/usr/bin/env python3
"""
run_exp_multi_llm.py — 多 LLM 提供者对比实验

审稿人要求 (Review1 §2.2):
  "增加 OpenAI GPT-4o Mini、Anthropic Claude 3 Haiku 作为测试 LLM"

实验设计:
  每种 LLM 提供者运行相同的 Agent 任务集:
    - GLM-4-Flash (默认, 免费API)
    - DeepSeek-V3 (高性价比)
    - GPT-4o-Mini (OpenAI, 需 API Key)
    - Claude-3-Haiku (Anthropic, 需 API Key)

  对比维度:
    - 不同 LLM API 的响应延迟特征
    - PlanGate 在不同 API 延迟模式下的治理效果
    - 外部信号跟踪器对不同限流策略的适应性

  网关: NG / SRL / PlanGate-Real

用法:
  # 使用环境变量配置 API
  export OPENAI_API_KEY="sk-..."
  export ANTHROPIC_API_KEY="sk-ant-..."

  # 运行全部 LLM 提供者
  python scripts/run_exp_multi_llm.py --all

  # 只运行 GLM + DeepSeek（免费）
  python scripts/run_exp_multi_llm.py --providers glm deepseek

  # Dry run
  python scripts/run_exp_multi_llm.py --all --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Optional


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_multi_llm")
AGENT_RUNNER = os.path.join(SCRIPT_DIR, "react_agent_client.py")
SERVER_PY = os.path.join(ROOT_DIR, "mcp_server", "server.py")


@dataclass
class LLMProvider:
    """LLM 提供者配置"""
    name: str           # 短名称
    label: str          # 显示标签
    env_base: str       # 环境变量前缀 (LLM_API_BASE)
    env_key: str        # 环境变量 (LLM_API_KEY)
    env_model: str      # 环境变量 (LLM_API_MODEL)
    # 默认值 (可被环境变量覆盖)
    default_base: str = ""
    default_model: str = ""
    # Agent 大脑 (用于 langgraph runner)
    agent_base: str = ""
    agent_model: str = ""


PROVIDERS = {
    "glm": LLMProvider(
        name="glm",
        label="GLM-4-Flash (ZhipuAI)",
        env_base="LLM_API_BASE",
        env_key="LLM_API_KEY",
        env_model="LLM_API_MODEL",
        default_base="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        agent_base="https://open.bigmodel.cn/api/paas/v4",
        agent_model="glm-4-flash",
    ),
    "deepseek": LLMProvider(
        name="deepseek",
        label="DeepSeek-V3",
        env_base="LLM_API_BASE",
        env_key="LLM_API_KEY",
        env_model="LLM_API_MODEL",
        default_base="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        agent_base="https://api.deepseek.com/v1",
        agent_model="deepseek-chat",
    ),
    "gpt4o-mini": LLMProvider(
        name="gpt4o-mini",
        label="GPT-4o-Mini (OpenAI)",
        env_base="OPENAI_API_BASE",
        env_key="OPENAI_API_KEY",
        env_model="OPENAI_MODEL",
        default_base="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        agent_base="https://api.openai.com/v1",
        agent_model="gpt-4o-mini",
    ),
    "claude-haiku": LLMProvider(
        name="claude-haiku",
        label="Claude-3-Haiku (Anthropic)",
        env_base="ANTHROPIC_API_BASE",
        env_key="ANTHROPIC_API_KEY",
        env_model="ANTHROPIC_MODEL",
        default_base="https://api.anthropic.com/v1",
        default_model="claude-3-haiku-20240307",
        agent_base="https://api.anthropic.com/v1",
        agent_model="claude-3-haiku-20240307",
    ),
}

# 网关模式配置
GATEWAYS = [
    {"name": "ng", "mode": "ng", "port": 9001, "args": []},
    {"name": "srl", "mode": "srl", "port": 9002, "args": [
        "--srl-qps", "65", "--srl-burst", "400", "--srl-max-conc", "55",
    ]},
    {"name": "plangate_real", "mode": "mcpdp-real", "port": 9005, "args": [
        "--plangate-price-step", "40",
        "--plangate-max-sessions", "30",
        "--plangate-sunk-cost-alpha", "0.5",
        "--plangate-session-cap-wait", "3",
    ]},
]


def check_provider_available(provider: LLMProvider) -> bool:
    """检查 LLM 提供者的 API Key 是否可用"""
    key = os.environ.get(provider.env_key, "")
    if not key:
        # 尝试备用键名
        alt_keys = {
            "glm": "ZHIPUAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        alt = alt_keys.get(provider.name, "")
        if alt:
            key = os.environ.get(alt, "")
    return bool(key)


def print_provider_matrix(providers: List[str]):
    """打印 LLM 提供者可用性矩阵"""
    print(f"\n  LLM 提供者状态:")
    for name in providers:
        p = PROVIDERS[name]
        avail = check_provider_available(p)
        status = "✓" if avail else "✗ (需设置 API Key)"
        print(f"    {name:<15} {p.label:<30} [{status}]")
    print()


def run_experiment_instance(
    provider: LLMProvider,
    gateway: dict,
    agents: int,
    concurrency: int,
    result_dir: str,
    run_idx: int,
    dry_run: bool = False,
) -> dict:
    """运行单个实验实例"""
    tag = f"{provider.name}/{gateway['name']}/run{run_idx}"
    csv_name = f"{gateway['name']}_{provider.name}_run{run_idx}.csv"
    csv_path = os.path.join(result_dir, csv_name)

    print(f"  [{tag}]")
    if dry_run:
        print(f"    [DRY-RUN] {csv_name}")
        return {"dry_run": True}

    # 构建环境变量
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    # 设置 LLM API 配置
    env["AGENT_LLM_BASE"] = provider.agent_base or provider.default_base
    env["AGENT_LLM_MODEL"] = provider.agent_model or provider.default_model
    env[provider.env_base] = provider.default_base
    env[provider.env_model] = provider.default_model

    target_url = f"http://127.0.0.1:{gateway['port']}"

    cmd = [
        sys.executable, AGENT_RUNNER,
        "--target", target_url,
        "--sessions", str(agents),
        "--concurrency", str(concurrency),
        "--max-steps", "8",
        "--budget", "500",
        "--output", csv_path,
    ]

    log_path = csv_path.replace(".csv", "_stdout.log")
    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
            retcode = proc.wait(timeout=600)
    except subprocess.TimeoutExpired:
        print(f"    [TIMEOUT]")
        proc.kill()
        return {"error": "timeout"}
    except Exception as e:
        print(f"    [ERROR] {e}")
        return {"error": str(e)}

    return {
        "provider": provider.name,
        "gateway": gateway["name"],
        "run_idx": run_idx,
        "csv": csv_name,
        "returncode": retcode,
    }


def main():
    parser = argparse.ArgumentParser(
        description="多 LLM 提供者对比实验",
    )
    parser.add_argument("--providers", nargs="+", default=None,
                        choices=list(PROVIDERS.keys()),
                        help="指定 LLM 提供者")
    parser.add_argument("--all", action="store_true",
                        help="测试所有可用的 LLM 提供者")
    parser.add_argument("--agents", type=int, default=50,
                        help="Agent 数量 (default: 50)")
    parser.add_argument("--concurrency", type=int, default=10,
                        help="并发数 (default: 10)")
    parser.add_argument("--repeats", type=int, default=3,
                        help="每组重复次数 (default: 3)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.all:
        provider_names = list(PROVIDERS.keys())
    elif args.providers:
        provider_names = args.providers
    else:
        provider_names = ["glm"]  # 默认只跑 GLM

    print(f"\n{'#'*70}")
    print(f"  多 LLM 提供者对比实验 (Exp-MultiLLM)")
    print(f"  提供者: {', '.join(provider_names)}")
    print(f"  网关: {', '.join(g['name'] for g in GATEWAYS)}")
    print(f"  Agent 数: {args.agents}  并发: {args.concurrency}")
    print(f"  重复: {args.repeats}")
    print(f"{'#'*70}")

    print_provider_matrix(provider_names)

    os.makedirs(RESULTS_DIR, exist_ok=True)

    # 检查可用性
    available = []
    for name in provider_names:
        p = PROVIDERS[name]
        if check_provider_available(p):
            available.append(name)
        else:
            print(f"  [SKIP] {name}: API Key 未配置 ({p.env_key})")

    if not available and not args.dry_run:
        print("  错误: 无可用的 LLM 提供者，请设置 API Key 环境变量")
        sys.exit(1)

    if args.dry_run:
        available = provider_names  # dry-run 模式下全部列出

    total = len(available) * len(GATEWAYS) * args.repeats
    print(f"  总实例数: {total}")

    # 运行实验
    all_results = []
    for pname in available:
        provider = PROVIDERS[pname]
        provider_dir = os.path.join(RESULTS_DIR, pname)
        os.makedirs(provider_dir, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"  LLM: {provider.label}")
        print(f"{'='*60}")

        for gw in GATEWAYS:
            for run_idx in range(1, args.repeats + 1):
                result = run_experiment_instance(
                    provider, gw, args.agents, args.concurrency,
                    provider_dir, run_idx, dry_run=args.dry_run,
                )
                all_results.append(result)

    print(f"\n{'#'*70}")
    print(f"  实验完成!")
    print(f"  结果目录: {RESULTS_DIR}")
    print(f"{'#'*70}")


if __name__ == "__main__":
    main()
