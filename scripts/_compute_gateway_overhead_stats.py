#!/usr/bin/env python3
"""Aggregate gateway-overhead measurements.

Inputs:
  1. Go in-process benchmark output captured from:
       results/exp_gateway_overhead/go_bench_overhead.txt
  2. Live gateway traces emitted by scripts/dag_load_generator.py, which include
     gateway_latency_us in each step-level CSV.

Outputs:
  results/exp_gateway_overhead/go_bench_overhead.csv
  results/exp_gateway_overhead/gateway_overhead_agg.csv
  results/exp_gateway_overhead/gateway_overhead_cdf.csv

The live aggregation reports three cohorts as diagnostic signals:
    - success_only: successful admitted tool-call steps
    - step0_reject: step-0 rejected requests
    - all_requests: all gateway-handled requests

Important: gateway_latency_us is a gateway-observed service-time header and
may include proxied backend/tool execution. The primary overhead claim should
use the Go in-process microbenchmarks.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_RESULTS_DIR = ROOT_DIR / "results" / "exp_gateway_overhead"
DEFAULT_GO_BENCH_TXT = DEFAULT_RESULTS_DIR / "go_bench_overhead.txt"
DEFAULT_LIVE_ROOT = DEFAULT_RESULTS_DIR / "live"

BENCH_RE = re.compile(
    r"^(Benchmark[\w]+?)(?:-\d+)?\s+\d+\s+([0-9.]+)\s+ns/op"
    r"(?:\s+([0-9.]+)\s+B/op)?(?:\s+([0-9.]+)\s+allocs/op)?"
)


def mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * pct / 100.0))
    index = max(0, min(index, len(ordered) - 1))
    return ordered[index]


def load_go_bench(path: Path) -> List[dict]:
    if not path.is_file():
        return []

    rows: List[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = BENCH_RE.match(line.strip())
            if not match:
                continue
            benchmark = match.group(1)
            ns_per_op = float(match.group(2))
            b_per_op = float(match.group(3)) if match.group(3) else 0.0
            allocs_per_op = float(match.group(4)) if match.group(4) else 0.0
            rows.append(
                {
                    "benchmark": benchmark,
                    "ns_per_op": ns_per_op,
                    "us_per_op": ns_per_op / 1000.0,
                    "b_per_op": b_per_op,
                    "allocs_per_op": allocs_per_op,
                }
            )
    return rows


def write_go_bench_csv(rows: Sequence[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["benchmark", "ns_per_op", "us_per_op", "b_per_op", "allocs_per_op"]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize_go_bench(rows: Sequence[dict]) -> None:
    if not rows:
        print("No Go benchmark rows found.")
        return

    print("\nGo In-Process Overhead Summary")
    print("-" * 78)
    print(f"{'Benchmark':42s} {'ns/op':>12s} {'us/op':>10s} {'B/op':>10s} {'allocs/op':>10s}")
    print("-" * 78)
    for row in rows:
        print(
            f"{row['benchmark'][:42]:42s} {row['ns_per_op']:12.2f} {row['us_per_op']:10.3f} "
            f"{row['b_per_op']:10.1f} {row['allocs_per_op']:10.2f}"
        )
    print("-" * 78)


def find_live_csvs(root: Path) -> List[Path]:
    if not root.exists():
        return []
    csvs: List[Path] = []
    for path in root.rglob("*.csv"):
        name = path.name.lower()
        if name.endswith("_sessions.csv"):
            continue
        if name in {"gateway_overhead_agg.csv", "gateway_overhead_cdf.csv", "go_bench_overhead.csv"}:
            continue
        csvs.append(path)
    return sorted(csvs)


def derive_target_label(root: Path, csv_path: Path) -> str:
    try:
        relative_parts = csv_path.relative_to(root).parts
    except ValueError:
        return csv_path.parent.name
    if "live" in relative_parts:
        idx = relative_parts.index("live")
        if idx + 1 < len(relative_parts):
            return relative_parts[idx + 1]
    if len(relative_parts) >= 2:
        return relative_parts[0]
    return csv_path.parent.name


def derive_concurrency(csv_path: Path, raw_value: str) -> int:
    try:
        parsed = int(float(raw_value or 0))
    except ValueError:
        parsed = 0
    if parsed > 0:
        return parsed
    # Fallback to directory hints like .../C40/run1.csv
    for part in csv_path.parts:
        upper = part.upper()
        if upper.startswith("C") and upper[1:].isdigit():
            return int(upper[1:])
    return 0


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def load_live_rows(live_root: Path) -> List[dict]:
    rows: List[dict] = []
    for csv_path in find_live_csvs(live_root):
        target_label = derive_target_label(live_root, csv_path)
        try:
            with csv_path.open("r", encoding="utf-8", errors="replace") as handle:
                reader = csv.DictReader(handle)
                for raw in reader:
                    if not raw.get("session_id"):
                        continue
                    mode = (raw.get("mode") or "unknown").strip() or "unknown"
                    concurrency = derive_concurrency(csv_path, raw.get("concurrency", "0"))
                    try:
                        latency_ms = float(raw.get("latency_ms", 0) or 0)
                    except ValueError:
                        latency_ms = 0.0
                    try:
                        gw_us = float(raw.get("gateway_latency_us", 0) or 0)
                    except ValueError:
                        gw_us = 0.0
                    if gw_us <= 0 and latency_ms <= 0:
                        continue
                    try:
                        step_id = int(float(raw.get("step_id", 0) or 0))
                    except ValueError:
                        step_id = 0
                    status = (raw.get("status") or "").strip().lower()
                    session_state = (raw.get("session_state") or "").strip().lower()
                    is_step0_reject = (
                        step_id == 0
                        and (status in {"rejected", "overloaded", "error", "state_miss"} or session_state == "rejected")
                    )
                    is_success = status == "success"
                    gw_share_pct = (gw_us / (latency_ms * 1000.0) * 100.0) if latency_ms > 0 else 0.0
                    rows.append(
                        {
                            "target": target_label,
                            "mode": mode,
                            "concurrency": concurrency,
                            "step_id": step_id,
                            "status": status,
                            "session_state": session_state,
                            "latency_ms": latency_ms,
                            "gateway_latency_us": gw_us,
                            "gw_share_pct": gw_share_pct,
                            "is_success": is_success,
                            "is_step0_reject": is_step0_reject,
                            "source_csv": str(csv_path),
                            "state_miss": parse_bool(raw.get("state_miss", "false")),
                        }
                    )
        except Exception as exc:
            print(f"[WARN] failed to read {csv_path}: {exc}")
    return rows


def aggregate_live_rows(rows: Sequence[dict]) -> List[dict]:
    grouped: DefaultDict[Tuple[str, str, int, str], List[dict]] = defaultdict(list)
    for row in rows:
        target = str(row.get("target", "unknown"))
        mode = str(row.get("mode", "unknown"))
        concurrency = int(row.get("concurrency", 0) or 0)
        grouped[(target, mode, concurrency, "all_requests")].append(row)
        if row.get("is_success"):
            grouped[(target, mode, concurrency, "success_only")].append(row)
        if row.get("is_step0_reject"):
            grouped[(target, mode, concurrency, "step0_reject")].append(row)

    summary_rows: List[dict] = []
    for (target, mode, concurrency, cohort), cohort_rows in sorted(grouped.items()):
        if not cohort_rows:
            continue
        gw_us_all = [float(r["gateway_latency_us"]) for r in cohort_rows]
        gw_us_nonzero = [value for value in gw_us_all if value > 0]
        lat_ms = [float(r["latency_ms"]) for r in cohort_rows]
        share = [float(r["gw_share_pct"]) for r in cohort_rows if float(r["latency_ms"]) > 0 and float(r["gateway_latency_us"]) > 0]
        success_count = sum(1 for r in cohort_rows if r.get("is_success"))
        step0_reject_count = sum(1 for r in cohort_rows if r.get("is_step0_reject"))
        header_available = len(gw_us_nonzero) > 0
        summary_rows.append(
            {
                "target": target,
                "mode": mode,
                "concurrency": concurrency,
                "cohort": cohort,
                "n_rows": len(cohort_rows),
            "header_available": str(header_available).lower(),
            "header_nonzero_rows": len(gw_us_nonzero),
                "success_count": success_count,
                "step0_reject_count": step0_reject_count,
            "gw_p50_us": percentile(gw_us_nonzero, 50),
            "gw_p95_us": percentile(gw_us_nonzero, 95),
            "gw_p99_us": percentile(gw_us_nonzero, 99),
            "gw_mean_us": mean(gw_us_nonzero),
                "e2e_p50_ms": percentile(lat_ms, 50),
                "e2e_p95_ms": percentile(lat_ms, 95),
                "e2e_p99_ms": percentile(lat_ms, 99),
                "e2e_mean_ms": mean(lat_ms),
                "gw_share_mean_pct": mean(share),
            }
        )
    return summary_rows


def write_live_csv(summary_rows: Sequence[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "target",
        "mode",
        "concurrency",
        "cohort",
        "n_rows",
        "header_available",
        "header_nonzero_rows",
        "success_count",
        "step0_reject_count",
        "gw_p50_us",
        "gw_p95_us",
        "gw_p99_us",
        "gw_mean_us",
        "e2e_p50_ms",
        "e2e_p95_ms",
        "e2e_p99_ms",
        "e2e_mean_ms",
        "gw_share_mean_pct",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)


def write_cdf_csv(rows: Sequence[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["target", "mode", "concurrency", "cohort", "gateway_latency_us", "cdf"])
        grouped: DefaultDict[Tuple[str, str, int, str], List[float]] = defaultdict(list)
        for row in rows:
            gw_us = float(row["gateway_latency_us"])
            if gw_us <= 0:
                continue
            key = (str(row.get("target", "unknown")), str(row.get("mode", "unknown")), int(row.get("concurrency", 0) or 0), "all_requests")
            grouped[key].append(gw_us)
            if row.get("is_success"):
                grouped[(key[0], key[1], key[2], "success_only")].append(gw_us)
            if row.get("is_step0_reject"):
                grouped[(key[0], key[1], key[2], "step0_reject")].append(gw_us)

        for (target, mode, concurrency, cohort), values in sorted(grouped.items()):
            if not values:
                continue
            ordered = sorted(values)
            total = len(ordered)
            for index, value in enumerate(ordered, start=1):
                writer.writerow([target, mode, concurrency, cohort, f"{value:.3f}", f"{index / total:.6f}"])


def print_live_summary(summary_rows: Sequence[dict]) -> None:
    if not summary_rows:
        print("No live gateway rows found.")
        return

    interesting = [row for row in summary_rows if row["cohort"] == "success_only"]
    if not interesting:
        interesting = list(summary_rows)

    print("\nLive Gateway Overhead Summary")
    print("-" * 110)
    print(f"{'Target':12s} {'Mode':16s} {'C':>4s} {'Cohort':14s} {'Hdr':>5s} {'P50(us)':>10s} {'P95(us)':>10s} {'P99(us)':>10s} {'GW/E2E%':>10s}")
    print("-" * 110)
    for row in interesting:
        print(
            f"{row['target'][:12]:12s} {row['mode'][:16]:16s} {row['concurrency']:4d} {row['cohort'][:14]:14s} {row['header_available']:>5s} "
            f"{row['gw_p50_us']:10.2f} {row['gw_p95_us']:10.2f} {row['gw_p99_us']:10.2f} {row['gw_share_mean_pct']:10.3f}"
        )
    print("-" * 110)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate gateway overhead benchmark results.")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR), help=f"Results directory (default: {DEFAULT_RESULTS_DIR})")
    parser.add_argument("--go-bench-input", default=str(DEFAULT_GO_BENCH_TXT), help=f"Go benchmark text file (default: {DEFAULT_GO_BENCH_TXT})")
    parser.add_argument("--live-root", default=str(DEFAULT_LIVE_ROOT), help=f"Live trace root directory (default: {DEFAULT_LIVE_ROOT})")
    args = parser.parse_args()

    results_dir = Path(args.results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    go_rows = load_go_bench(Path(args.go_bench_input))
    if go_rows:
        write_go_bench_csv(go_rows, results_dir / "go_bench_overhead.csv")
        summarize_go_bench(go_rows)
    else:
        print(f"No Go benchmark file found at {args.go_bench_input}")

    live_rows = load_live_rows(Path(args.live_root))
    if live_rows:
        summary_rows = aggregate_live_rows(live_rows)
        write_live_csv(summary_rows, results_dir / "gateway_overhead_agg.csv")
        write_cdf_csv(live_rows, results_dir / "gateway_overhead_cdf.csv")
        print_live_summary(summary_rows)
    else:
        print(f"No live traces found under {args.live_root}")

    if not go_rows and not live_rows:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
