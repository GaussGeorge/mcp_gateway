#!/usr/bin/env python3
"""
analyze_pareto_frontier.py — 分析 Pareto 前沿扫参结果

读取 run_pareto_frontier.py 生成的 pareto_summary.csv，
计算衍生指标，输出:
  tables/pareto_frontier_summary.csv    ← 每个变体的均值±标准差
  tables/pareto_frontier_key_points.csv ← 关键 Pareto 前沿点

衍生指标:
  useful_completion_rate = success / sessions
  admission_rate         = (sessions - rejected_s0) / sessions
  waste_per_success      = cascade_failed / max(success, 1)
  calls_per_success      = raw_goodput / max(success, 1)

用法:
  python scripts/analyze_pareto_frontier.py
  python scripts/analyze_pareto_frontier.py --input results/pareto_frontier_pilot/pareto_summary.csv
  python scripts/analyze_pareto_frontier.py --input results/pareto_frontier/pareto_summary.csv --sessions 500
"""

import argparse
import csv
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
TABLES_DIR = os.path.join(ROOT_DIR, "tables")


# ====== 数据加载 ======

def load_summary(csv_path: str) -> List[dict]:
    """加载 pareto_summary.csv，跳过有 error 的行"""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("error"):
                continue
            rows.append(row)
    return rows


def _float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val: str, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


# ====== 派生指标计算 ======

def compute_derived(row: dict, sessions: int) -> dict:
    """给单行补充衍生指标"""
    success = _int(row.get("success"))
    rej_s0 = _int(row.get("rejected_s0"))
    cascade = _int(row.get("cascade_failed"))
    raw_gp = _float(row.get("raw_goodput"))

    row["sessions_total"] = sessions
    row["useful_completion_rate"] = success / sessions if sessions > 0 else 0.0
    row["admission_rate"] = (sessions - rej_s0) / sessions if sessions > 0 else 0.0
    row["waste_per_success"] = cascade / max(success, 1)
    row["calls_per_success"] = raw_gp / max(success, 1)
    return row


# ====== 聚合（均值 + 标准差） ======

def aggregate_by_label(rows: List[dict]) -> Dict[str, dict]:
    """按 label 聚合多次重复，计算均值和标准差"""
    groups: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        groups[row["label"]].append(row)

    numeric_cols = [
        "success", "rejected_s0", "cascade_failed",
        "raw_goodput", "effective_goodput",
        "raw_goodput_s", "effective_goodput_s",
        "p50_ms", "p95_ms", "p99_ms",
        "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
        "jfi_steps", "jfi_latency",
        "useful_completion_rate", "admission_rate",
        "waste_per_success", "calls_per_success",
    ]

    aggregated = {}
    for label, group_rows in groups.items():
        first = group_rows[0]
        agg = {
            "label": label,
            "policy": first.get("policy", ""),
            "max_sessions": first.get("max_sessions", ""),
            "alpha": first.get("alpha", ""),
            "session_cap_wait": first.get("session_cap_wait", ""),
            "n_runs": len(group_rows),
        }
        for col in numeric_cols:
            vals = [_float(r.get(col)) for r in group_rows
                    if r.get(col) not in ("", None)]
            if not vals:
                agg[col] = ""
                agg[f"{col}_std"] = ""
                continue
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / max(len(vals) - 1, 1)
            agg[col] = round(mean, 4)
            agg[f"{col}_std"] = round(math.sqrt(variance), 4)
        aggregated[label] = agg

    return aggregated


# ====== Pareto 前沿点识别 ======

def find_pareto_front(variants: List[dict]) -> List[dict]:
    """
    识别 (useful_completion_rate, effective_goodput) 二维 Pareto 前沿。
    仅保留非被支配点（两个指标同时不劣于其他所有点）。
    只考虑 plangate 策略的 Pareto 变体（不含基线）。
    """
    pg_variants = [v for v in variants if v.get("policy") == "plangate"]

    pareto = []
    for v in pg_variants:
        ucr = _float(str(v.get("useful_completion_rate", 0)))
        egp = _float(str(v.get("effective_goodput", 0)))
        dominated = False
        for other in pg_variants:
            if other is v:
                continue
            other_ucr = _float(str(other.get("useful_completion_rate", 0)))
            other_egp = _float(str(other.get("effective_goodput", 0)))
            # other 支配 v 当且仅当两个目标均不劣且至少一个严格更优
            if other_ucr >= ucr and other_egp >= egp and (other_ucr > ucr or other_egp > egp):
                dominated = True
                break
        if not dominated:
            pareto.append(v)

    # 按 useful_completion_rate 升序排列
    pareto.sort(key=lambda x: _float(str(x.get("useful_completion_rate", 0))))
    return pareto


# ====== 写出 CSV ======

def write_summary_csv(aggregated: Dict[str, dict], output_path: str):
    """写出每变体均值±std汇总"""
    if not aggregated:
        return

    # 固定字段顺序（保证可读性）
    base_cols = [
        "label", "policy", "max_sessions", "alpha", "session_cap_wait", "n_runs",
        "success", "success_std",
        "rejected_s0", "rejected_s0_std",
        "cascade_failed", "cascade_failed_std",
        "useful_completion_rate", "useful_completion_rate_std",
        "admission_rate", "admission_rate_std",
        "waste_per_success", "waste_per_success_std",
        "calls_per_success", "calls_per_success_std",
        "effective_goodput", "effective_goodput_std",
        "raw_goodput", "raw_goodput_std",
        "p50_ms", "p50_ms_std",
        "p95_ms", "p95_ms_std",
        "p99_ms", "p99_ms_std",
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=base_cols, extrasaction="ignore")
        writer.writeheader()
        for row in aggregated.values():
            writer.writerow(row)
    print(f"  汇总 CSV 已保存: {output_path}")


def write_key_points_csv(pareto: List[dict], baselines: List[dict], output_path: str):
    """写出关键 Pareto 前沿点 + 基线对比"""
    key_cols = [
        "label", "policy", "max_sessions", "alpha", "session_cap_wait",
        "is_pareto_front",
        "useful_completion_rate", "admission_rate",
        "waste_per_success", "effective_goodput",
        "p50_ms", "p95_ms",
        "success", "rejected_s0", "cascade_failed",
    ]
    rows = []
    for v in pareto:
        row = dict(v)
        row["is_pareto_front"] = "yes"
        rows.append(row)
    for b in baselines:
        row = dict(b)
        row["is_pareto_front"] = "baseline"
        rows.append(row)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=key_cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"  关键点 CSV 已保存: {output_path}")


# ====== 终端打印 ======

def print_analysis_table(aggregated: Dict[str, dict], pareto_labels: set):
    """在终端打印分析结果"""
    order = ["ng", "rajomon", "sbac"] + sorted(
        [k for k in aggregated if k not in {"ng", "rajomon", "sbac"}]
    )
    print(f"\n{'='*110}")
    print(f"  {'label':<35} {'P':<3} {'UCR':>6} {'AdmR':>6} {'W/Suc':>7} "
          f"{'EffGP':>7} {'P95ms':>7} {'Pareto'}")
    print(f"  {'-'*35} {'---':<3} {'---':>6} {'---':>6} {'---':>7} {'---':>7} {'---':>7} {'---'}")
    for label in order:
        if label not in aggregated:
            continue
        v = aggregated[label]
        ucr = v.get("useful_completion_rate", "")
        adm = v.get("admission_rate", "")
        wps = v.get("waste_per_success", "")
        egp = v.get("effective_goodput", "")
        p95 = v.get("p95_ms", "")
        policy = v.get("policy", "")[:1].upper()
        marker = " <-- PARETO" if label in pareto_labels else ""
        print(f"  {label:<35} {policy:<3} "
              f"{str(ucr) if ucr != '' else '-':>6} "
              f"{str(adm) if adm != '' else '-':>6} "
              f"{str(wps) if wps != '' else '-':>7} "
              f"{str(egp) if egp != '' else '-':>7} "
              f"{str(p95) if p95 != '' else '-':>7}"
              f"{marker}")
    print(f"{'='*110}\n")


# ====== 主函数 ======

def main():
    parser = argparse.ArgumentParser(
        description="分析 Pareto 前沿扫参结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", type=str, default=None,
                        help="pareto_summary.csv 路径 (default: results/pareto_frontier/pareto_summary.csv)")
    parser.add_argument("--sessions", type=int, default=200,
                        help="实验使用的 session 总数 (default: 200)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="表格输出目录 (default: tables/)")
    args = parser.parse_args()

    # 默认输入路径
    if args.input:
        input_path = args.input
    else:
        # 优先查找 pilot 目录
        pilot_path = os.path.join(ROOT_DIR, "results", "pareto_frontier_pilot", "pareto_summary.csv")
        formal_path = os.path.join(ROOT_DIR, "results", "pareto_frontier", "pareto_summary.csv")
        if os.path.exists(formal_path):
            input_path = formal_path
        elif os.path.exists(pilot_path):
            input_path = pilot_path
            print(f"[INFO] 使用 pilot 结果: {pilot_path}")
        else:
            print(f"[ERROR] 未找到 pareto_summary.csv，请先运行 run_pareto_frontier.py")
            sys.exit(1)

    tables_dir = args.output_dir or TABLES_DIR
    summary_output = os.path.join(tables_dir, "pareto_frontier_summary.csv")
    keypoints_output = os.path.join(tables_dir, "pareto_frontier_key_points.csv")

    print(f"\n  读取: {input_path}")

    # 加载 + 计算衍生指标
    rows = load_summary(input_path)
    if not rows:
        print("[ERROR] CSV 为空或所有行均有错误")
        sys.exit(1)

    rows = [compute_derived(r, args.sessions) for r in rows]

    # 聚合
    aggregated = aggregate_by_label(rows)
    print(f"  共 {len(aggregated)} 个变体，{len(rows)} 行数据")

    # Pareto 前沿识别
    agg_list = list(aggregated.values())
    pareto = find_pareto_front(agg_list)
    pareto_labels = {v["label"] for v in pareto}
    baselines = [v for v in agg_list if v.get("policy") in ("ng", "sbac", "rajomon")]

    print(f"\n  Pareto 前沿点 ({len(pareto)} 个):")
    for v in pareto:
        print(f"    {v['label']}")

    # 写出 CSV
    write_summary_csv(aggregated, summary_output)
    write_key_points_csv(pareto, baselines, keypoints_output)

    # 终端可视化
    print_analysis_table(aggregated, pareto_labels)


if __name__ == "__main__":
    main()
