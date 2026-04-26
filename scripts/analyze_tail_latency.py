#!/usr/bin/env python3
"""
analyze_tail_latency.py — 深度尾延迟分析 (real-LLM C=10/C=40)
================================================================
分析维度:
  1. P50/P95/P99 端对端会话延迟 (per gateway, per C)
  2. 步级延迟分布 (成功步 vs 被拒步)
  3. 延迟稳定性 (跨 run 的 P95 变异系数 CV)
  4. 阶梯累计分布 (CDF) 数据导出
"""
import csv
import os
import sys
import numpy as np
from collections import defaultdict
from pathlib import Path


def load_agent_data(base_dir, gateways, runs=5):
    """加载所有 agents CSV, 返回 {gateway: {run: [agent_rows]}}"""
    data = {}
    for gw in gateways:
        data[gw] = {}
        for r in range(1, runs + 1):
            path = Path(base_dir) / gw / f"run{r}" / "steps_agents.csv"
            if not path.exists():
                continue
            agents = []
            with open(path) as f:
                for row in csv.DictReader(f):
                    agents.append(row)
            data[gw][r] = agents
    return data


def load_step_data(base_dir, gateways, runs=5):
    """加载所有 steps CSV, 返回 {gateway: {run: [step_rows]}}"""
    data = {}
    for gw in gateways:
        data[gw] = {}
        for r in range(1, runs + 1):
            path = Path(base_dir) / gw / f"run{r}" / "steps.csv"
            if not path.exists():
                continue
            steps = []
            with open(path) as f:
                for row in csv.DictReader(f):
                    steps.append(row)
            data[gw][r] = steps
    return data


def analyze_e2e_latency(agent_data, label):
    """端对端会话延迟分析"""
    print(f"\n{'='*70}")
    print(f"  端对端会话延迟 — {label}")
    print(f"{'='*70}")
    print(f"{'Gateway':16s} {'P50(s)':>8s} {'P95(s)':>8s} {'P99(s)':>8s} {'Mean(s)':>8s} {'CV(P95)':>8s}")
    print("-" * 60)

    for gw in agent_data:
        all_latencies = []
        per_run_p95 = []
        for r in sorted(agent_data[gw]):
            agents = agent_data[gw][r]
            # Only include successful sessions
            lats = [float(a['total_latency_ms']) / 1000.0
                    for a in agents if a['state'] == 'SUCCESS']
            all_latencies.extend(lats)
            if lats:
                per_run_p95.append(np.percentile(lats, 95))

        if not all_latencies:
            continue

        p50 = np.percentile(all_latencies, 50)
        p95 = np.percentile(all_latencies, 95)
        p99 = np.percentile(all_latencies, 99)
        mean = np.mean(all_latencies)
        cv_p95 = np.std(per_run_p95) / np.mean(per_run_p95) if per_run_p95 else 0

        print(f"{gw:16s} {p50:8.1f} {p95:8.1f} {p99:8.1f} {mean:8.1f} {cv_p95:8.3f}")


def analyze_step_latency(step_data, label):
    """步级延迟分析"""
    print(f"\n{'='*70}")
    print(f"  步级延迟 — {label}")
    print(f"{'='*70}")
    print(f"{'Gateway':16s} {'Succ P50':>10s} {'Succ P95':>10s} {'Rej P50':>10s} {'Rej P95':>10s} {'Succ N':>8s} {'Rej N':>8s}")
    print("-" * 76)

    for gw in step_data:
        succ_lats = []
        rej_lats = []
        for r in sorted(step_data[gw]):
            for s in step_data[gw][r]:
                lat = float(s['latency_ms'])
                if s['status'] == 'success':
                    succ_lats.append(lat)
                else:
                    rej_lats.append(lat)

        succ_p50 = np.percentile(succ_lats, 50) if succ_lats else 0
        succ_p95 = np.percentile(succ_lats, 95) if succ_lats else 0
        rej_p50 = np.percentile(rej_lats, 50) if rej_lats else 0
        rej_p95 = np.percentile(rej_lats, 95) if rej_lats else 0

        print(f"{gw:16s} {succ_p50:10.0f}ms {succ_p95:10.0f}ms {rej_p50:10.0f}ms {rej_p95:10.0f}ms {len(succ_lats):8d} {len(rej_lats):8d}")


def analyze_per_run_consistency(agent_data, label):
    """跨 run P95 一致性分析"""
    print(f"\n{'='*70}")
    print(f"  跨-Run P95 一致性 — {label}")
    print(f"{'='*70}")
    print(f"{'Gateway':16s} {'Run1':>8s} {'Run2':>8s} {'Run3':>8s} {'Run4':>8s} {'Run5':>8s} {'Mean±Std':>14s}")
    print("-" * 80)

    for gw in agent_data:
        per_run = []
        for r in range(1, 6):
            if r not in agent_data[gw]:
                per_run.append(None)
                continue
            agents = agent_data[gw][r]
            lats = [float(a['total_latency_ms']) / 1000.0
                    for a in agents if a['state'] == 'SUCCESS']
            if lats:
                per_run.append(np.percentile(lats, 95))
            else:
                per_run.append(None)

        vals = [v for v in per_run if v is not None]
        run_strs = [f"{v:8.1f}" if v else "    N/A " for v in per_run]
        mean_std = f"{np.mean(vals):.1f}±{np.std(vals):.1f}" if vals else "N/A"
        print(f"{gw:16s} {''.join(run_strs)} {mean_std:>14s}")


def main():
    gateways_c10 = ['ng', 'rajomon', 'pp', 'plangate_real']
    gateways_c40 = ['ng', 'rajomon', 'pp', 'plangate_real']

    for c_level, base_dir in [("C=10", "results/exp_week5_C10"),
                               ("C=40", "results/exp_week5_C40")]:
        if not os.path.exists(base_dir):
            print(f"[SKIP] {base_dir} not found")
            continue

        gws = gateways_c10 if c_level == "C=10" else gateways_c40
        agent_data = load_agent_data(base_dir, gws)
        step_data = load_step_data(base_dir, gws)

        analyze_e2e_latency(agent_data, c_level)
        analyze_step_latency(step_data, c_level)
        analyze_per_run_consistency(agent_data, c_level)


if __name__ == "__main__":
    main()
