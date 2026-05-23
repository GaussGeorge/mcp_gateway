#!/usr/bin/env python3
"""Aggregate controlled P3 recovery/amendment results."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


def parse_bool(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    return 1 if text in {"1", "true", "yes"} else 0


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(float(v) for v in values)
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    frac = pos - lo
    return values[lo] * (1.0 - frac) + values[hi] * frac


def read_sessions_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def collect_policy_rows(results_dir: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for policy_dir in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        rows.extend(read_sessions_csv(policy_dir / "sessions.csv"))
    return rows


def summarize_main(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("scenario") != "main":
            continue
        grouped[(row["policy"], row["failure_rate"])].append(row)

    summaries: List[Dict[str, Any]] = []
    for (policy, failure_rate), group in sorted(grouped.items()):
        sessions = len(group)
        success = sum(1 for row in group if row.get("status") == "success")
        recovery_attempted = sum(parse_bool(row.get("recovery_attempted", 0)) for row in group)
        recovery_success = sum(parse_bool(row.get("recovery_success", 0)) for row in group)
        amendment_submitted = sum(parse_bool(row.get("amendment_submitted", 0)) for row in group)
        amendment_accepted = sum(parse_bool(row.get("amendment_accepted", 0)) for row in group)
        amendment_rejected = sum(parse_bool(row.get("amendment_rejected", 0)) for row in group)
        v2_issued = sum(parse_bool(row.get("v2_commitment_issued", 0)) for row in group)
        false_accept = sum(parse_bool(row.get("false_accept", 0)) for row in group)
        executed_after_rejected = sum(int(parse_float(row.get("executed_after_rejected_amendment", 0))) for row in group)
        total_tool_calls = [parse_float(row.get("total_tool_calls", 0)) for row in group]
        saved_steps = [parse_float(row.get("saved_steps", 0)) for row in group]
        latencies = [parse_float(row.get("latency_ms", 0)) for row in group]
        summaries.append(
            {
                "policy": policy,
                "failure_rate": failure_rate,
                "sessions": sessions,
                "success_rate": round(success / sessions if sessions else 0.0, 6),
                "recovery_success_rate": round(recovery_success / recovery_attempted if recovery_attempted else 0.0, 6),
                "amendment_accept_rate": round(amendment_accepted / amendment_submitted if amendment_submitted else 0.0, 6),
                "amendment_reject_rate": round(amendment_rejected / amendment_submitted if amendment_submitted else 0.0, 6),
                "v2_commitment_issued": v2_issued,
                "false_accept": false_accept,
                "executed_after_rejected_amendment": executed_after_rejected,
                "avg_total_tool_calls": round(sum(total_tool_calls) / len(total_tool_calls) if total_tool_calls else 0.0, 6),
                "avg_saved_steps": round(sum(saved_steps) / len(saved_steps) if saved_steps else 0.0, 6),
                "p95_latency_ms": round(percentile(latencies, 0.95), 6),
            }
        )
    return summaries


def summarize_adversarial(rows: Iterable[Dict[str, str]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        scenario = row.get("scenario", "")
        if scenario in ("", "main"):
            continue
        grouped[(scenario, row["failure_rate"])].append(row)

    summaries: List[Dict[str, Any]] = []
    for (scenario, failure_rate), group in sorted(grouped.items()):
        sessions = len(group)
        rejected = sum(parse_bool(row.get("invalid_amendment_rejected", 0)) for row in group)
        false_accept = sum(parse_bool(row.get("false_accept", 0)) for row in group)
        stale_parent_rejected = sum(parse_bool(row.get("stale_parent_rejected", 0)) for row in group)
        executed_after_rejected = sum(int(parse_float(row.get("executed_after_rejected_amendment", 0))) for row in group)
        summaries.append(
            {
                "scenario": scenario,
                "failure_rate": failure_rate,
                "sessions": sessions,
                "reject_rate": round(rejected / sessions if sessions else 0.0, 6),
                "stale_parent_rejected": stale_parent_rejected,
                "false_accept": false_accept,
                "executed_after_rejected_amendment": executed_after_rejected,
            }
        )
    return summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate P3 recovery/amendment results")
    parser.add_argument("--results-dir", required=True, type=str)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results_dir = Path(args.results_dir)
    rows = collect_policy_rows(results_dir)
    main_summary = summarize_main(rows)
    adversarial_summary = summarize_adversarial(rows)
    write_csv(results_dir / "p3_summary.csv", main_summary)
    write_csv(results_dir / "p3_adversarial_summary.csv", adversarial_summary)
    print(f"Wrote {results_dir / 'p3_summary.csv'}")
    print(f"Wrote {results_dir / 'p3_adversarial_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
