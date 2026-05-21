#!/usr/bin/env python3
"""
_compute_multigateway_shared_state_stats.py — 多网关共享状态实验聚合统计

读取 results/exp_multigateway_shared_state/{mode}/C{conc}/run{n}/steps.csv，计算：
  - 基础指标: success, rejected_s0, cascade_failed, abd_pct, effective_goodput_s
  - 延迟: p50/p95/p99 (成功会话端到端), gateway_latency_us 均值/P95
  - 多节点专项: cross_node_session_pct, state_miss_count, duplicate_admission_count
  - 流量分布: 各网关 URL 流量占比

输出：
  results/exp_multigateway_shared_state/multigateway_agg.csv    — 逐 run 原始数据
  results/exp_multigateway_shared_state/multigateway_summary_computed.csv — 均值±标准差

Usage:
  python scripts/_compute_multigateway_shared_state_stats.py
  python scripts/_compute_multigateway_shared_state_stats.py --results-dir results/exp_multigateway_shared_state
"""

import argparse
import csv
import json
import math
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DEFAULT_RESULTS_DIR = os.path.join(ROOT_DIR, "results", "exp_multigateway_shared_state_full")


# ============================================================
# Per-CSV stats computation
# ============================================================

@dataclass
class RunStats:
    mode: str = ""
    concurrency: int = 0
    run_idx: int = 0
    # session counts
    total_sessions: int = 0
    success: int = 0
    rejected_s0: int = 0
    cascade_failed: int = 0
    # rates
    abd_pct: float = 0.0           # cascade / admitted
    success_rate: float = 0.0
    rejected_s0_rate: float = 0.0
    semantic_failure_pct: float = 0.0  # state_miss / total_sessions
    effective_goodput_s: float = 0.0
    # latency (successful sessions only, in ms)
    e2e_p50_ms: float = 0.0
    e2e_p95_ms: float = 0.0
    e2e_p99_ms: float = 0.0
    e2e_mean_ms: float = 0.0
    # gateway latency
    gw_lat_mean_us: float = 0.0
    gw_lat_p95_us: float = 0.0
    # multi-node
    cross_node_session_pct: float = 0.0
    state_miss_count: int = 0
    duplicate_admission_count: int = 0
    max_global_active_sessions: int = 0  # peak concurrent admitted sessions
    # traffic distribution (json)
    gateway_traffic: str = "{}"
    # elapsed
    elapsed_s: float = 0.0


def _percentile(data: List[float], pct: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = max(0, min(len(s) - 1, int(len(s) * pct / 100)))
    return s[idx]


def compute_run_stats(mode: str, concurrency: int, run_idx: int, csv_path: str) -> RunStats:
    """Compute per-run stats from a steps.csv file."""
    rs = RunStats(mode=mode, concurrency=concurrency, run_idx=run_idx)

    if not os.path.isfile(csv_path):
        return rs

    # Group rows by session_id
    sessions: Dict[str, dict] = {}       # sid → {steps, state, start_ts, end_ts, urls}
    step_lats_ms: List[float] = []
    gw_lat_us: List[float] = []
    state_miss_count = 0
    dup_admission_count = 0
    min_ts = float("inf")
    max_ts = float("-inf")

    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row.get("session_id", "")
                if not sid:
                    continue

                ts = float(row.get("timestamp", 0) or 0)
                lat_ms = float(row.get("latency_ms", 0) or 0)
                status = row.get("status", "")
                gw_url = row.get("gateway_url", "")
                is_state_miss = row.get("state_miss", "False").strip().lower() in ("true", "1")
                is_dup = row.get("duplicate_admission", "False").strip().lower() in ("true", "1")
                gw_lat = float(row.get("gateway_latency_us", 0) or 0)

                min_ts = min(min_ts, ts)
                max_ts = max(max_ts, ts + lat_ms / 1000.0)

                step_lats_ms.append(lat_ms)
                if gw_lat > 0:
                    gw_lat_us.append(gw_lat)
                if is_state_miss:
                    state_miss_count += 1
                if is_dup:
                    dup_admission_count += 1

                if sid not in sessions:
                    sessions[sid] = {
                        "state": status, "steps": [], "start_ts": ts,
                        "end_ts": ts + lat_ms / 1000.0, "urls": set(),
                    }
                sess = sessions[sid]
                sess["steps"].append(status)
                sess["start_ts"] = min(sess["start_ts"], ts)
                sess["end_ts"] = max(sess["end_ts"], ts + lat_ms / 1000.0)
                if gw_url:
                    sess["urls"].add(gw_url)
                # track final state
                # last step status dominates
                sess["state"] = status

    except Exception as e:
        print(f"  [WARN] 读取 {csv_path} 失败: {e}")
        return rs

    # Session-level tallies
    success_sessions = 0
    rejected_s0 = 0
    cascade_failed = 0
    e2e_latencies_ms: List[float] = []
    cross_node_sessions = 0
    gateway_traffic: Dict[str, int] = defaultdict(int)
    
    # For max_global_active_sessions: build timeline of session admissions/releases
    admitted_sessions = []  # (start_time, end_time) for sessions that were admitted

    for sid, sess in sessions.items():
        steps = sess["steps"]
        state = sess["state"]
        e2e_ms = (sess["end_ts"] - sess["start_ts"]) * 1000.0

        if state == "success":
            success_sessions += 1
            e2e_latencies_ms.append(e2e_ms)
            admitted_sessions.append((sess["start_ts"], sess["end_ts"]))
        elif state in ("rejected", "state_miss") and len(steps) == 1 and steps[0] in ("rejected", "state_miss"):
            # rejected at step 0 (no admitted work)
            rejected_s0 += 1
        elif state in ("cascade_failed", "budget_exceeded", "timeout", "error"):
            cascade_failed += 1
            admitted_sessions.append((sess["start_ts"], sess["end_ts"]))  # was admitted, then failed

        if len(sess["urls"]) >= 2:
            cross_node_sessions += 1

        for url in sess["urls"]:
            gateway_traffic[url] += 1
    
    # Compute max concurrent admitted sessions
    max_global_active = 0
    if admitted_sessions:
        events: List[Tuple[float, int]] = []  # (time, delta)
        for start, end in admitted_sessions:
            events.append((start, +1))    # admission
            events.append((end, -1))       # release
        events.sort()
        current_active = 0
        for _, delta in events:
            current_active += delta
            max_global_active = max(max_global_active, current_active)

    total = len(sessions)
    admitted = success_sessions + cascade_failed
    elapsed = max(max_ts - min_ts, 0.001) if min_ts < float("inf") else 0.001

    rs.total_sessions = total
    rs.success = success_sessions
    rs.rejected_s0 = rejected_s0
    rs.cascade_failed = cascade_failed
    rs.abd_pct = 100.0 * cascade_failed / admitted if admitted > 0 else 0.0
    rs.success_rate = 100.0 * success_sessions / total if total > 0 else 0.0
    rs.rejected_s0_rate = 100.0 * rejected_s0 / total if total > 0 else 0.0
    rs.semantic_failure_pct = 100.0 * state_miss_count / total if total > 0 else 0.0
    rs.effective_goodput_s = success_sessions / elapsed

    rs.e2e_p50_ms  = _percentile(e2e_latencies_ms, 50)
    rs.e2e_p95_ms  = _percentile(e2e_latencies_ms, 95)
    rs.e2e_p99_ms  = _percentile(e2e_latencies_ms, 99)
    rs.e2e_mean_ms = statistics.mean(e2e_latencies_ms) if e2e_latencies_ms else 0.0

    rs.gw_lat_mean_us = statistics.mean(gw_lat_us) if gw_lat_us else 0.0
    rs.gw_lat_p95_us  = _percentile(gw_lat_us, 95)

    rs.cross_node_session_pct = 100.0 * cross_node_sessions / total if total > 0 else 0.0
    rs.state_miss_count = state_miss_count
    rs.duplicate_admission_count = dup_admission_count
    rs.gateway_traffic = json.dumps(dict(gateway_traffic))
    rs.elapsed_s = elapsed
    rs.max_global_active_sessions = max_global_active

    return rs


# ============================================================
# Aggregation across runs
# ============================================================

AGG_FIELDS = [
    "mode", "concurrency", "n_runs",
    "success_mean", "success_std",
    "rejected_s0_mean",
    "rejected_s0_rate_mean",
    "cascade_failed_mean",
    "abd_pct_mean", "abd_pct_std",
    "success_rate_mean", "success_rate_std",
    "semantic_failure_pct_mean",
    "effective_goodput_s_mean", "effective_goodput_s_std",
    "e2e_p50_ms_mean", "e2e_p95_ms_mean", "e2e_p99_ms_mean",
    "gw_lat_mean_us_mean", "gw_lat_p95_us_mean",
    "cross_node_session_pct_mean", "max_global_active_sessions_mean",
    "state_miss_total", "dup_admission_total",
]


def _mean_std(values: List[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    m = statistics.mean(values)
    s = statistics.stdev(values) if len(values) > 1 else 0.0
    return m, s


def aggregate_group(mode: str, conc: int, runs: List[RunStats]) -> dict:
    row: dict = {"mode": mode, "concurrency": conc, "n_runs": len(runs)}

    def _col(attr: str) -> List[float]:
        return [float(getattr(r, attr)) for r in runs]

    m, s = _mean_std(_col("success"))
    row["success_mean"] = round(m, 2); row["success_std"] = round(s, 2)
    row["rejected_s0_mean"] = round(statistics.mean(_col("rejected_s0")), 2)
    row["rejected_s0_rate_mean"] = round(statistics.mean(_col("rejected_s0_rate")), 2)
    row["cascade_failed_mean"] = round(statistics.mean(_col("cascade_failed")), 2)

    m, s = _mean_std(_col("abd_pct"))
    row["abd_pct_mean"] = round(m, 2); row["abd_pct_std"] = round(s, 2)

    m, s = _mean_std(_col("success_rate"))
    row["success_rate_mean"] = round(m, 2); row["success_rate_std"] = round(s, 2)
    row["semantic_failure_pct_mean"] = round(statistics.mean(_col("semantic_failure_pct")), 2)

    m, s = _mean_std(_col("effective_goodput_s"))
    row["effective_goodput_s_mean"] = round(m, 3); row["effective_goodput_s_std"] = round(s, 3)

    row["e2e_p50_ms_mean"] = round(statistics.mean(_col("e2e_p50_ms")), 1)
    row["e2e_p95_ms_mean"] = round(statistics.mean(_col("e2e_p95_ms")), 1)
    row["e2e_p99_ms_mean"] = round(statistics.mean(_col("e2e_p99_ms")), 1)

    row["gw_lat_mean_us_mean"] = round(statistics.mean(_col("gw_lat_mean_us")), 1)
    row["gw_lat_p95_us_mean"]  = round(statistics.mean(_col("gw_lat_p95_us")), 1)

    row["cross_node_session_pct_mean"] = round(statistics.mean(_col("cross_node_session_pct")), 2)
    row["max_global_active_sessions_mean"] = round(statistics.mean(_col("max_global_active_sessions")), 0)
    row["state_miss_total"]    = int(sum(_col("state_miss_count")))
    row["dup_admission_total"] = int(sum(_col("duplicate_admission_count")))

    return row


# ============================================================
# Raw-run CSV fields
# ============================================================

RAW_FIELDS = [
    "mode", "concurrency", "run_idx",
    "total_sessions", "success", "rejected_s0", "cascade_failed",
    "abd_pct", "success_rate", "rejected_s0_rate", "semantic_failure_pct", "effective_goodput_s",
    "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms", "e2e_mean_ms",
    "gw_lat_mean_us", "gw_lat_p95_us",
    "cross_node_session_pct", "state_miss_count", "duplicate_admission_count",
    "max_global_active_sessions",
    "elapsed_s", "gateway_traffic",
]


def write_rows(out_path: str, rows: List[dict], fields: List[str]):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ============================================================
# Discovery
# ============================================================

def discover_runs(results_dir: str) -> List[Tuple[str, int, int, str]]:
    """Return list of (mode, concurrency, run_idx, csv_path)."""
    found = []
    if not os.path.isdir(results_dir):
        return found
    for mode in sorted(os.listdir(results_dir)):
        mode_dir = os.path.join(results_dir, mode)
        if not os.path.isdir(mode_dir):
            continue
        for conc_dir_name in sorted(os.listdir(mode_dir)):
            if not conc_dir_name.startswith("C"):
                continue
            try:
                conc = int(conc_dir_name[1:])
            except ValueError:
                continue
            conc_path = os.path.join(mode_dir, conc_dir_name)
            for run_dir_name in sorted(os.listdir(conc_path)):
                if not run_dir_name.startswith("run"):
                    continue
                try:
                    run_idx = int(run_dir_name[3:])
                except ValueError:
                    continue
                csv_path = os.path.join(conc_path, run_dir_name, "steps.csv")
                found.append((mode, conc, run_idx, csv_path))
    return found


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="多网关共享状态实验聚合统计",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR,
                        help=f"实验结果目录 (default: {DEFAULT_RESULTS_DIR})")
    parser.add_argument("--show", action="store_true",
                        help="显示汇总表（兼容 runner 命令；当前默认总是显示）")
    args = parser.parse_args()

    results_dir = args.results_dir
    runs = discover_runs(results_dir)

    if not runs:
        # Check if frozen aggregated data exists (artifact frozen mode)
        agg_csv = os.path.join(results_dir, "multigateway_summary_computed.csv")
        if os.path.isfile(agg_csv):
            print(f"[INFO] 使用冻结聚合数据: {agg_csv}")
            agg_rows = []
            with open(agg_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    agg_rows.append(row)
            # Display summary
            print(f"{'='*115}")
            print(f"{'mode':<18} {'conc':>5} {'n':>3}  {'Succ%':>6}  {'Rej0%':>6}  {'StateMiss':>9}  {'SemFail%':>8}  {'Cross%':>7}  {'MaxActive':>9}  {'GP/s':>6}  {'P95ms':>7}")
            print(f"{'─'*115}")
            for r in agg_rows:
                print(
                    f"{r.get('mode',''):<18} {r.get('concurrency',''):>5} {r.get('n_runs',''):>3}  "
                    f"{float(r.get('success_rate_mean',0)):>6.1f}  "
                    f"{float(r.get('rejected_s0_rate_mean',0)):>6.1f}  "
                    f"{int(float(r.get('state_miss_total',0))):>9}  "
                    f"{float(r.get('semantic_failure_pct_mean',0)):>8.1f}  "
                    f"{float(r.get('cross_node_session_pct_mean',0)):>7.1f}  "
                    f"{float(r.get('max_global_active_sessions_mean',0)):>9.0f}  "
                    f"{float(r.get('effective_goodput_s_mean',0)):>6.2f}  "
                    f"{float(r.get('e2e_p95_ms_mean',0)):>7.1f}"
                )
            print(f"{'='*115}\n")
            return
        print(f"[WARN] 未找到任何 steps.csv 或冻结聚合数据，请先运行 run_multigateway_shared_state.py")
        print(f"       结果目录: {results_dir}")
        return

    print(f"发现 {len(runs)} 个 run，开始计算...")

    raw_rows: List[dict] = []
    group_data: Dict[Tuple[str, int], List[RunStats]] = defaultdict(list)

    for mode, conc, run_idx, csv_path in runs:
        print(f"  {mode}/C{conc}/run{run_idx} ... ", end="", flush=True)
        rs = compute_run_stats(mode, conc, run_idx, csv_path)
        print(
            f"success={rs.success}  gps={rs.effective_goodput_s:.2f}  "
            f"state_miss={rs.state_miss_count}  cross_node={rs.cross_node_session_pct:.1f}%"
        )

        row = {f: getattr(rs, f) for f in RAW_FIELDS}
        row["mode"] = mode
        row["concurrency"] = conc
        row["run_idx"] = run_idx
        raw_rows.append(row)
        group_data[(mode, conc)].append(rs)

    # Write raw-run CSV
    raw_csv = os.path.join(results_dir, "multigateway_agg.csv")
    write_rows(raw_csv, raw_rows, RAW_FIELDS)
    print(f"\n逐 run 数据: {raw_csv}")

    # Compute aggregates
    agg_rows: List[dict] = []
    for (mode, conc), rs_list in sorted(group_data.items()):
        agg_rows.append(aggregate_group(mode, conc, rs_list))

    agg_csv = os.path.join(results_dir, "multigateway_summary_computed.csv")
    write_rows(agg_csv, agg_rows, AGG_FIELDS)
    print(f"聚合汇总:   {agg_csv}")

    # Pretty print summary table
    print(f"\n{'='*115}")
    print(f"{'mode':<18} {'conc':>5} {'n':>3}  {'Succ%':>6}  {'Rej0%':>6}  {'StateMiss':>9}  {'SemFail%':>8}  {'Cross%':>7}  {'MaxActive':>9}  {'GP/s':>6}  {'P95ms':>7}")
    print(f"{'─'*115}")
    for r in agg_rows:
        print(
            f"{r['mode']:<18} {r['concurrency']:>5} {r['n_runs']:>3}  "
            f"{r['success_rate_mean']:>6.1f}  "
            f"{r['rejected_s0_rate_mean']:>6.1f}  "
            f"{r['state_miss_total']:>9}  "
            f"{r['semantic_failure_pct_mean']:>8.1f}  "
            f"{r['cross_node_session_pct_mean']:>7.1f}  "
            f"{r['max_global_active_sessions_mean']:>9.0f}  "
            f"{r['effective_goodput_s_mean']:>6.2f}  "
            f"{r['e2e_p95_ms_mean']:>7.1f}"
        )
    print(f"{'='*115}\n")


if __name__ == "__main__":
    main()
