#!/usr/bin/env python3
"""
run_pareto_frontier.py — PlanGate Pareto 前沿扫参实验

研究问题: "PlanGate 是否仅仅是更早地拒绝请求？"
通过扫描 max_sessions × session_cap_wait × sunk_cost_alpha 三个维度
生成 Pareto 前沿曲线（成功率 vs 有效吞吐 vs 尾延迟）

Stage A: max_sessions ∈ {20,30,40,60,80} × session_cap_wait ∈ {1,3}
Stage B: alpha         ∈ {0.3,0.5,0.7}  at ms=30, wait=1
Baselines: ng, sbac(ms=150), rajomon(ps=20)

用法:
  # 干跑：打印计划，不实际执行
  python scripts/run_pareto_frontier.py --dry-run

  # Pilot 模式（所有配置各跑 1 次，小规模）
  python scripts/run_pareto_frontier.py --pilot --gateway-binary gateway.exe

  # 正式模式（3 次重复，500 sessions）
  python scripts/run_pareto_frontier.py --repeats 3 --sessions 500 --concurrency 200 --gateway-binary gateway.exe

  # 指定输出目录
  python scripts/run_pareto_frontier.py --pilot --output-dir results/pareto_frontier_pilot
"""

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

# ====== 导入 run_all_experiments 工具函数 ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
sys.path.insert(0, SCRIPT_DIR)

import run_all_experiments as runner
from run_all_experiments import (
    GatewayConfig,
    ExperimentConfig,
    start_backend,
    stop_backend,
    start_gateway,
    stop_gateway,
    run_load_generator,
    build_gateway,
    GATEWAY_HOST,
    BASE_PORT,
)


# ====== Pareto 扫参配置 ======
@dataclass
class ParetoVariant:
    """一个 Pareto 扫参点"""
    label: str               # 用于文件名和摘要（无空格）
    policy: str              # "plangate" | "ng" | "sbac" | "rajomon"
    gw: GatewayConfig
    max_sessions: Optional[int] = None
    alpha: Optional[float] = None
    session_cap_wait: Optional[int] = None


# ====== 固定超参（与 TUNED_PARAMS 保持一致） ======
PRICE_STEP = 40
DEFAULT_ALPHA = 0.5
DEFAULT_WAIT = 1
SBAC_MAX_SESSIONS = 150
RAJOMON_PRICE_STEP = 20


def _pgargs(max_sessions: int, alpha: float, session_cap_wait: int,
            price_step: int = PRICE_STEP) -> list:
    """生成 PlanGate 网关 CLI 参数列表"""
    return [
        "--plangate-price-step", str(price_step),
        "--plangate-max-sessions", str(max_sessions),
        "--plangate-sunk-cost-alpha", str(alpha),
        "--plangate-session-cap-wait", str(session_cap_wait),
    ]


def build_variants() -> List[ParetoVariant]:
    """
    构建全部 Pareto 扫参点。

    Stage A: max_sessions × session_cap_wait (alpha 固定 0.5)
    Stage B: alpha at ms=30, wait=1
    Baselines: ng, sbac, rajomon
    """
    variants: List[ParetoVariant] = []

    # ---- Stage A: max_sessions × session_cap_wait ----
    for ms in [20, 30, 40, 60, 80]:
        for wait in [1, 3]:
            label = f"pg_ms{ms}_wait{wait}_a{DEFAULT_ALPHA}"
            gw = GatewayConfig(
                name=label,
                mode="mcpdp",
                extra_args=_pgargs(ms, DEFAULT_ALPHA, wait),
            )
            variants.append(ParetoVariant(
                label=label,
                policy="plangate",
                gw=gw,
                max_sessions=ms,
                alpha=DEFAULT_ALPHA,
                session_cap_wait=wait,
            ))

    # ---- Stage B: alpha sweep (ms=30, wait=1, 去重 a0.5 已在 A 中) ----
    for alpha in [0.3, 0.7]:  # 0.5 已在 Stage A ms=30,wait=1 中出现
        label = f"pg_ms30_wait1_a{alpha}"
        gw = GatewayConfig(
            name=label,
            mode="mcpdp",
            extra_args=_pgargs(30, alpha, DEFAULT_WAIT),
        )
        variants.append(ParetoVariant(
            label=label,
            policy="plangate",
            gw=gw,
            max_sessions=30,
            alpha=alpha,
            session_cap_wait=DEFAULT_WAIT,
        ))

    # ---- Baselines ----
    variants.append(ParetoVariant(
        label="ng",
        policy="ng",
        gw=GatewayConfig(name="ng", mode="ng"),
    ))
    variants.append(ParetoVariant(
        label="sbac",
        policy="sbac",
        gw=GatewayConfig(
            name="sbac",
            mode="sbac",
            extra_args=["--sbac-max-sessions", str(SBAC_MAX_SESSIONS)],
        ),
        max_sessions=SBAC_MAX_SESSIONS,
    ))
    variants.append(ParetoVariant(
        label="rajomon",
        policy="rajomon",
        gw=GatewayConfig(
            name="rajomon",
            mode="rajomon",
            extra_args=["--rajomon-price-step", str(RAJOMON_PRICE_STEP)],
        ),
    ))

    return variants


# ====== 实验执行器 ======

def run_variant(variant: ParetoVariant, exp: ExperimentConfig,
                run_idx: int, port: int, output_dir: str,
                dry_run: bool = False) -> dict:
    """执行单个 Pareto 变体的单次重复"""
    csv_name = f"{variant.label}_run{run_idx}.csv"
    csv_path = os.path.join(output_dir, csv_name)

    row = {
        "label": variant.label,
        "policy": variant.policy,
        "max_sessions": variant.max_sessions,
        "alpha": variant.alpha,
        "session_cap_wait": variant.session_cap_wait,
        "run_idx": run_idx,
    }

    if dry_run:
        print(f"    [DRY-RUN] {csv_name}  mode={variant.gw.mode}  args={variant.gw.extra_args}")
        row["dry_run"] = True
        return row

    proc = None
    try:
        proc = start_gateway(variant.gw, port)
        target_url = f"http://{GATEWAY_HOST}:{port}"
        stats = run_load_generator(target_url, exp, csv_path)
        row.update(stats)
        row["csv"] = csv_name
    except Exception as e:
        print(f"    [ERROR] {variant.label} run{run_idx}: {e}")
        row["error"] = str(e)
    finally:
        if proc:
            stop_gateway(proc)
        time.sleep(3)  # 等待端口释放 + 后端冷却

    return row


def run_pareto_sweep(variants: List[ParetoVariant], exp: ExperimentConfig,
                     repeats: int, output_dir: str, dry_run: bool = False) -> List[dict]:
    """遍历所有变体 × 重复次数"""
    os.makedirs(output_dir, exist_ok=True)
    all_results = []
    port_base = BASE_PORT + 200  # 避免与 run_all_experiments.py 冲突
    port_counter = 0

    total = len(variants) * repeats
    current = 0

    for variant in variants:
        for run_idx in range(1, repeats + 1):
            current += 1
            port_counter += 1
            port = port_base + (port_counter % 100)

            print(f"\n  [{current}/{total}] {variant.label}  run={run_idx}  port={port}")
            row = run_variant(variant, exp, run_idx, port, output_dir, dry_run)
            all_results.append(row)

    return all_results


def save_pareto_summary(output_dir: str, results: List[dict]):
    """保存 Pareto 汇总 CSV"""
    summary_path = os.path.join(output_dir, "pareto_summary.csv")
    fieldnames = [
        "label", "policy", "max_sessions", "alpha", "session_cap_wait", "run_idx",
        "success", "rejected_s0", "cascade_failed",
        "raw_goodput", "effective_goodput",
        "raw_goodput_s", "effective_goodput_s",
        "p50_ms", "p95_ms", "p99_ms",
        "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
        "jfi_steps", "jfi_latency",
        "csv", "error",
    ]
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            if not r.get("dry_run"):
                writer.writerow(r)
    print(f"\n  汇总已保存: {summary_path}")
    return summary_path


def print_summary_table(results: List[dict]):
    """在终端打印简要汇总表（仅含真实结果）"""
    real = [r for r in results if not r.get("dry_run") and not r.get("error")]
    if not real:
        return
    print(f"\n{'='*90}")
    print(f"  {'label':<30} {'success':>8} {'rej_s0':>8} {'casc':>6} {'eff_gp':>8} {'p95ms':>8}")
    print(f"  {'-'*30} {'---':>8} {'---':>8} {'---':>6} {'---':>8} {'---':>8}")
    for r in real:
        label = r.get("label", "?")
        success = r.get("success", "-")
        rej = r.get("rejected_s0", "-")
        casc = r.get("cascade_failed", "-")
        egp = r.get("effective_goodput", "-")
        p95 = r.get("p95_ms", "-")
        if isinstance(egp, float):
            egp = f"{egp:.1f}"
        if isinstance(p95, float):
            p95 = f"{p95:.0f}"
        print(f"  {label:<30} {str(success):>8} {str(rej):>8} {str(casc):>6} "
              f"{egp:>8} {p95:>8}")
    print(f"{'='*90}\n")


# ====== CLI ======

def main():
    parser = argparse.ArgumentParser(
        description="PlanGate Pareto 前沿扫参实验",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--pilot", action="store_true",
                        help="Pilot 模式: sessions=200, concurrency=100, repeats=1（快速验证）")
    parser.add_argument("--repeats", type=int, default=1,
                        help="每个配置重复次数 (default: 1, 正式建议 3)")
    parser.add_argument("--sessions", type=int, default=None,
                        help="覆盖 sessions 数 (pilot 默认 200, 正式默认 500)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="覆盖 concurrency (pilot 默认 100, 正式默认 200)")
    parser.add_argument("--arrival-rate", type=float, default=50.0,
                        help="到达速率 (default: 50.0)")
    parser.add_argument("--duration", type=int, default=60,
                        help="发压持续秒数 (default: 60)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="结果输出目录 (default: results/pareto_frontier)")
    parser.add_argument("--gateway-binary", type=str, default=None,
                        help="预编译网关二进制路径 (default: 自动 go build)")
    parser.add_argument("--backend-max-workers", type=int, default=10,
                        help="后端并发 worker 数 (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行：打印配置但不实际执行")
    parser.add_argument("--skip-backend", action="store_true",
                        help="跳过后端启动（外部已手动启动）")
    parser.add_argument("--stage", choices=["A", "B", "baselines", "all"], default="all",
                        help="只跑指定 Stage: A / B / baselines / all (default: all)")
    args = parser.parse_args()

    # ---- 参数解析 ----
    pilot = args.pilot
    sessions = args.sessions if args.sessions else (200 if pilot else 500)
    concurrency = args.concurrency if args.concurrency else (100 if pilot else 200)
    repeats = args.repeats if not pilot else 1
    output_dir = args.output_dir or os.path.join(
        ROOT_DIR, "results",
        "pareto_frontier_pilot" if pilot else "pareto_frontier"
    )

    # ---- 实验配置 ----
    exp = ExperimentConfig(
        name="ParetoFrontier",
        description="PlanGate Pareto 前沿扫参",
        sessions=sessions,
        ps_ratio=1.0,
        budget=500,
        heavy_ratio=0.3,
        concurrency=concurrency,
        arrival_rate=args.arrival_rate,
        duration=args.duration,
        step_timeout=2.0,
    )

    print(f"\n{'#'*70}")
    print(f"  PlanGate Pareto 前沿扫参实验")
    print(f"  模式: {'PILOT' if pilot else '正式'} | DRY-RUN: {args.dry_run}")
    print(f"  sessions={sessions}, concurrency={concurrency}, repeats={repeats}")
    print(f"  duration={args.duration}s, arrival_rate={args.arrival_rate}")
    print(f"  输出目录: {output_dir}")
    print(f"{'#'*70}\n")

    # ---- 构建变体列表 ----
    all_variants = build_variants()

    # 根据 --stage 过滤
    stage_a_labels = {
        f"pg_ms{ms}_wait{wait}_a{DEFAULT_ALPHA}"
        for ms in [20, 30, 40, 60, 80]
        for wait in [1, 3]
    }
    stage_b_labels = {f"pg_ms30_wait1_a{a}" for a in [0.3, 0.7]}
    baseline_labels = {"ng", "sbac", "rajomon"}

    if args.stage == "A":
        variants = [v for v in all_variants if v.label in stage_a_labels]
    elif args.stage == "B":
        # Stage B 需要包含 ms=30,wait=1,a=0.5 作为参照点
        stage_b_with_ref = stage_b_labels | {f"pg_ms30_wait1_a{DEFAULT_ALPHA}"}
        variants = [v for v in all_variants if v.label in stage_b_with_ref]
    elif args.stage == "baselines":
        variants = [v for v in all_variants if v.label in baseline_labels]
    else:
        variants = all_variants

    print(f"  变体数: {len(variants)}  ×  重复: {repeats}  =  {len(variants) * repeats} 次实验\n")
    for v in variants:
        print(f"    {v.label:<35} mode={v.gw.mode}")
    print()

    if args.dry_run:
        print("[DRY-RUN] 以下为实际执行时的操作计划:")
        run_pareto_sweep(variants, exp, repeats, output_dir, dry_run=True)
        sys.exit(0)

    # ---- 网关二进制 ----
    if args.gateway_binary:
        if not os.path.isfile(args.gateway_binary):
            # 尝试相对路径
            candidate = os.path.join(ROOT_DIR, args.gateway_binary)
            if os.path.isfile(candidate):
                args.gateway_binary = candidate
            else:
                print(f"[ERROR] 网关二进制不存在: {args.gateway_binary}")
                sys.exit(1)
        runner.GATEWAY_BINARY = args.gateway_binary
        print(f"  使用预编译网关: {runner.GATEWAY_BINARY}")
    else:
        print("  正在编译网关...")
        build_gateway()

    # ---- 启动后端 ----
    if not args.skip_backend:
        print(f"  启动后端 (max_workers={args.backend_max_workers})...")
        start_backend(args.backend_max_workers)
    else:
        print("  [SKIP] 跳过后端启动（使用外部已启动的后端）")

    # ---- 执行扫参 ----
    results = []
    try:
        results = run_pareto_sweep(variants, exp, repeats, output_dir, dry_run=False)
    except KeyboardInterrupt:
        print("\n[KeyboardInterrupt] 中断，保存已有结果...")
    finally:
        if not args.skip_backend:
            stop_backend()

    # ---- 保存汇总 ----
    if results:
        save_pareto_summary(output_dir, results)
        print_summary_table(results)

        # 错误汇总
        errors = [r for r in results if r.get("error")]
        if errors:
            print(f"\n[WARN] {len(errors)}/{len(results)} 次实验出错:")
            for r in errors:
                print(f"  - {r.get('label','?')} run{r.get('run_idx','?')}: {r.get('error','?')[:120]}")

    print("\n  完成。")


if __name__ == "__main__":
    main()
