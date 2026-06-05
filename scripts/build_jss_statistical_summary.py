#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = REPO_ROOT / "artifact_results"

SEED_DEFAULT = 20260603
BOOTSTRAP_ITERS_DEFAULT = 3000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build JSS-oriented statistical summary artifact")
    parser.add_argument("--bootstrap-iters", type=int, default=BOOTSTRAP_ITERS_DEFAULT)
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--out-dir-name", type=str, default="statistical_summary_v2")
    parser.add_argument("--include-request-baseline", action="store_true")
    parser.add_argument("--include-idempotency-baseline", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return 1.0 if lowered == "true" else 0.0
    try:
        val = float(text)
    except ValueError:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return val


def fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def bootstrap_mean_ci(values: list[float], rng: random.Random, iters: int) -> tuple[float | None, float | None]:
    n = len(values)
    if n < 3:
        return None, None
    samples: list[float] = []
    for _ in range(iters):
        samples.append(mean(values[rng.randrange(n)] for _ in range(n)))
    samples.sort()
    return samples[int(0.025 * (iters - 1))], samples[int(0.975 * (iters - 1))]


def bootstrap_delta_ci(
    values_a: list[float], values_b: list[float], rng: random.Random, iters: int
) -> tuple[float | None, float | None]:
    if len(values_a) < 3 or len(values_b) < 3:
        return None, None
    samples: list[float] = []
    for _ in range(iters):
        sample_a = mean(values_a[rng.randrange(len(values_a))] for _ in range(len(values_a)))
        sample_b = mean(values_b[rng.randrange(len(values_b))] for _ in range(len(values_b)))
        samples.append(sample_a - sample_b)
    samples.sort()
    return samples[int(0.025 * (iters - 1))], samples[int(0.975 * (iters - 1))]


def group_key(row: dict[str, str], fields: list[str]) -> str:
    return "|".join(f"{field}={row.get(field, '')}" for field in fields)


def collect_stat_rows(
    *,
    experiment: str,
    source_artifact: str,
    source_csv: str,
    rows: list[dict[str, str]],
    group_fields: list[str],
    metrics: list[str],
    evidence_role: str,
    claim_boundary: str,
    rng: random.Random,
    bootstrap_iters: int,
    force_descriptive: bool = False,
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(field, "") for field in group_fields)].append(row)

    out: list[dict[str, str]] = []
    for key, bucket in sorted(grouped.items()):
        rep = {group_fields[i]: key[i] for i in range(len(group_fields))}
        gkey = group_key(rep, group_fields)
        for metric in metrics:
            values = [v for v in (to_float(row.get(metric)) for row in bucket) if v is not None]
            n = len(values)
            descriptive_only = force_descriptive or n < 3
            avg = mean(values) if values else None
            std = stdev(values) if n >= 2 else 0.0 if n == 1 else None
            ci_low, ci_high = (None, None) if descriptive_only else bootstrap_mean_ci(values, rng, bootstrap_iters)
            out.append(
                {
                    "experiment": experiment,
                    "evidence_role": evidence_role,
                    "source_artifact": source_artifact,
                    "source_csv": source_csv,
                    "group_key": gkey,
                    "metric": metric,
                    "n": str(n),
                    "mean": fmt_num(avg),
                    "std": fmt_num(std),
                    "ci95_low": fmt_num(ci_low),
                    "ci95_high": fmt_num(ci_high),
                    "descriptive_only": "true" if descriptive_only else "false",
                    "claim_boundary": claim_boundary,
                }
            )
    return out


HIGHER_BETTER = {
    "success",
    "workflow_success_rate",
    "admitted_success_rate",
    "effective_goodput",
    "effective_goodput_s",
    "throughput_workflows_s",
    "resume_recovered",
    "react_recovered_success",
    "post_resume_success",
    "avoided_replay_steps",
    "continued_after_resume",
    "tool_calls_total",
    "backend_llm_tokens_total",
    "backend_llm_tokens",
    "success_rate",
    "validation_passed_bool",
    "passed_bool",
    "cross_gateway_bool",
    "side_effect_before_failure_present_bool",
    "resume_attempted_bool",
    "resume_recovered_bool",
    "resume_mode_react_client_cooperative_bool",
    "resume_current_step_gt_zero_bool",
    "resume_requires_client_continuation_bool",
    "resume_continuation_completed_bool",
    "continuation_completed_bool",
    "post_resume_success_bool",
    "final_state_consistent_bool",
    "failure_safe_state_bool",
    "side_effect_id_same_bool",
    "component_present",
    "numeric_value_available_bool",
}

LOWER_BETTER = {
    "cascade_failed",
    "wasted_service_ms",
    "wasted_tool_calls",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "rejected_s0",
    "rejected_s0_capacity",
    "step0_reject_rate",
    "duplicate_side_effect",
    "state_miss",
    "duplicate_admission",
    "validation_error_count",
    "client_timed_out",
    "client_rc",
    "auto_executed_future_tool_bool",
    "state_miss_bool",
    "replayed_completed_side_effects",
    "replayed_completed_side_effect_count",
    "duplicate_side_effect_count",
}


def interpretation(metric: str, delta: float | None, ci_low: float | None, ci_high: float | None, descriptive: bool) -> str:
    if delta is None or descriptive:
        return "descriptive_only"
    if ci_low is None or ci_high is None or ci_low <= 0.0 <= ci_high:
        return "mixed_or_boundary"
    if metric in HIGHER_BETTER:
        return "positive_for_group_a" if delta > 0 else "negative_for_group_a"
    if metric in LOWER_BETTER:
        return "positive_for_group_a" if delta < 0 else "negative_for_group_a"
    return "direction_unclassified"


def filter_values(rows: list[dict[str, str]], filters: dict[str, str], metric: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        if all(str(row.get(key, "")) == str(value) for key, value in filters.items()):
            val = to_float(row.get(metric))
            if val is not None:
                out.append(val)
    return out


def add_effect(
    effect_rows: list[dict[str, str]],
    *,
    experiment: str,
    evidence_role: str,
    source_artifact: str,
    comparison: str,
    metric: str,
    group_a: str,
    group_b: str,
    values_a: list[float],
    values_b: list[float],
    claim_boundary: str,
    rng: random.Random,
    bootstrap_iters: int,
    force_descriptive: bool = False,
) -> None:
    n_a = len(values_a)
    n_b = len(values_b)
    descriptive = force_descriptive or n_a < 3 or n_b < 3
    mean_a = mean(values_a) if values_a else None
    mean_b = mean(values_b) if values_b else None
    delta = mean_a - mean_b if mean_a is not None and mean_b is not None else None
    ci_low, ci_high = (None, None) if descriptive else bootstrap_delta_ci(values_a, values_b, rng, bootstrap_iters)
    rel = delta / mean_b * 100.0 if delta is not None and mean_b not in (None, 0.0) else None
    effect_rows.append(
        {
            "experiment": experiment,
            "evidence_role": evidence_role,
            "source_artifact": source_artifact,
            "comparison": comparison,
            "metric": metric,
            "group_a": group_a,
            "group_b": group_b,
            "n_a": str(n_a),
            "n_b": str(n_b),
            "mean_a": fmt_num(mean_a),
            "mean_b": fmt_num(mean_b),
            "delta": fmt_num(delta),
            "relative_delta_pct": fmt_num(rel),
            "ci95_low_delta": fmt_num(ci_low),
            "ci95_high_delta": fmt_num(ci_high),
            "descriptive_only": "true" if descriptive else "false",
            "interpretation": interpretation(metric, delta, ci_low, ci_high, descriptive),
            "claim_boundary": claim_boundary,
        }
    )


def lookup_effect(effect_rows: list[dict[str, str]], comparison: str, metric: str) -> dict[str, str] | None:
    for row in effect_rows:
        if row["comparison"] == comparison and row["metric"] == metric:
            return row
    return None


def count_passed(rows: list[dict[str, str]]) -> tuple[int, int]:
    total = 0
    passed = 0
    for row in rows:
        if "passed" in row:
            total += 1
            if str(row.get("passed", "")).lower() == "true":
                passed += 1
    return passed, total


def count_true(rows: list[dict[str, str]], field: str, filters: dict[str, str] | None = None) -> tuple[int, int]:
    total = 0
    true_count = 0
    filters = filters or {}
    for row in rows:
        if not all(str(row.get(key, "")) == str(value) for key, value in filters.items()):
            continue
        total += 1
        if str(row.get(field, "")).lower() in {"true", "1"}:
            true_count += 1
    return true_count, total


def build_claim_rows(
    effect_rows: list[dict[str, str]], datasets: dict[str, list[dict[str, str]]], validation: dict[str, Any]
) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []

    def claim(
        claim_id: str,
        source_artifact: str,
        evidence_role: str,
        primary_metrics: str,
        result_summary: str,
        support_level: str,
        caveat: str,
        allowed_use: str,
        not_allowed: str,
    ) -> None:
        claims.append(
            {
                "claim_id": claim_id,
                "source_artifact": source_artifact,
                "evidence_role": evidence_role,
                "primary_metrics": primary_metrics,
                "result_summary": result_summary,
                "support_level": support_level,
                "caveat": caveat,
                "allowed_use": allowed_use,
                "not_allowed": not_allowed,
            }
        )

    e = lookup_effect(effect_rows, "business_adaptive_vs_no_capacity_C100_F0.2", "cascade_failed")
    w = lookup_effect(effect_rows, "business_adaptive_vs_no_capacity_C100_F0.2", "wasted_service_ms")
    if e and w:
        claim(
            "JSS-C1",
            "business_workflow_service_v3",
            "core_service_workflow",
            "cascade_failed,wasted_service_ms",
            f"At C=100,F=0.2 adaptive vs no-capacity delta cascade={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}], wasted_ms={w['delta']} CI[{w['ci95_low_delta']},{w['ci95_high_delta']}].",
            "strong" if e["interpretation"] == "positive_for_group_a" and w["interpretation"] == "positive_for_group_a" else "boundary",
            "Raw workflow_success_rate can be lower under admission control; this claim is about waste/cascade governance.",
            "Use as the main JSS service-governance result.",
            "Do not claim raw-success dominance.",
        )

    e = lookup_effect(effect_rows, "business_recovery_vs_adaptive_C100_F0.2", "react_recovered_success")
    a = lookup_effect(effect_rows, "business_recovery_vs_adaptive_C100_F0.2", "avoided_replay_steps")
    if e and a:
        claim(
            "JSS-C2",
            "business_workflow_service_v3",
            "core_recovery",
            "react_recovered_success,avoided_replay_steps",
            f"At C=100,F=0.2 recovery vs adaptive delta recovered={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}], avoided={a['delta']} CI[{a['ci95_low_delta']},{a['ci95_high_delta']}].",
            "strong" if e["interpretation"] == "positive_for_group_a" and a["interpretation"] == "positive_for_group_a" else "boundary",
            "Client-cooperative recovery resumes from checkpoints; it does not auto-execute future tools.",
            "Use as the main ReAct recovery effectiveness result.",
            "Do not claim transparent server-side replay.",
        )

    e = lookup_effect(effect_rows, "operator_adaptive_vs_baseline_C100", "p95_ms")
    t = lookup_effect(effect_rows, "operator_adaptive_vs_baseline_C100", "throughput_workflows_s")
    if e and t:
        claim(
            "JSS-C3",
            "operator_overhead_v1",
            "deployability_overhead",
            "p95_ms,throughput_workflows_s",
            f"C=100 adaptive vs baseline p95 delta={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}], throughput delta={t['delta']} CI[{t['ci95_low_delta']},{t['ci95_high_delta']}].",
            "moderate",
            "Baseline is the same gateway/proxy path, not direct-to-backend.",
            "Use as bounded steady-state overhead evidence.",
            "Do not claim recovery effectiveness from this failure-free artifact.",
        )

    e = lookup_effect(
        effect_rows,
        "config_adaptive_react_recovery_strict_vs_default_C100_F0.2",
        "react_recovered_success",
    )
    if e:
        claim(
            "JSS-C4",
            "config_sensitivity_v1",
            "configuration_sensitivity",
            "react_recovered_success,cascade_failed,wasted_service_ms",
            f"Strict-vs-default recovery sensitivity at C=100,F=0.2 recovered delta={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}].",
            "moderate",
            "Sensitivity supports qualitative robustness, not an optimal parameter setting.",
            "Use to show signals persist under moderate governance-intensity perturbations.",
            "Do not select a best configuration from this artifact.",
        )

    passed, total = count_passed(datasets["recovery_edges"])
    claim(
        "JSS-C5",
        "recovery_protocol_edge_cases_v1",
        "protocol_usability",
        "passed_bool,duplicate_side_effect,auto_executed_future_tool_bool",
        f"{passed}/{total} declared recovery protocol edge cases passed.",
        "strong_protocol" if total and passed == total else "boundary",
        "Deterministic protocol harness; not a performance experiment.",
        "Use as recovery API usability and safety-boundary evidence.",
        "Do not use as throughput or real-LLM performance evidence.",
    )

    matrix = datasets["component_matrix"]
    evidence = datasets["component_evidence"]
    claim(
        "JSS-C6",
        "architecture_component_ablation_v1",
        "architecture_mapping",
        "component_present,evidence_record_count",
        f"{len(matrix)} architecture components mapped to {len(evidence)} evidence records.",
        "strong_mapping" if len(matrix) >= 9 and len(evidence) >= 18 else "moderate",
        "Evidence synthesis, not a newly executed performance experiment.",
        "Use to organize architecture rationale and failure-mode mapping.",
        "Do not claim every component improves raw success.",
    )

    e = lookup_effect(effect_rows, "vllm_recovery_vs_adaptive_C2", "react_recovered_success")
    if e:
        claim(
            "JSS-C7",
            "vllm_react_recovery_arm_v2",
            "real_backend_recovery",
            "react_recovered_success,avoided_replay_steps,continued_after_resume",
            f"vLLM recovery arm C=2 recovery vs adaptive recovered delta={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}].",
            "strong" if e["interpretation"] == "positive_for_group_a" else "boundary",
            "Hybrid DeepSeek agent brain plus local Qwen vLLM backend; targeted recovery arm.",
            "Use as real-backend recovery feasibility evidence.",
            "Do not merge with full-vLLM natural recovery claims.",
        )

    e = lookup_effect(effect_rows, "cloud_backend_no_capacity_vs_adaptive", "success")
    if e:
        claim(
            "JSS-C8",
            "cloud_backend_smoke_v1",
            "provider_boundary",
            "success,cascade_failed,backend_llm_tokens",
            f"Cloud backend smoke no-capacity vs adaptive success delta={e['delta']} (descriptive single-repeat boundary).",
            "diagnostic",
            "Single-repeat provider smoke; provider elasticity can invert the admission trade-off.",
            "Use as provider-boundary interpretation.",
            "Do not use as global policy ranking.",
        )

    e = lookup_effect(effect_rows, "cloudlab_redis_vs_memory", "state_miss")
    if e:
        claim(
            "JSS-C9",
            "cloudlab_random_redis_memory_v1",
            "distributed_state_diagnostic",
            "state_miss,duplicate_admission",
            f"CloudLab Redis vs memory state_miss delta={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}].",
            "strong_diagnostic" if e["interpretation"] == "positive_for_group_a" else "boundary",
            "Shared-state correctness diagnostic only.",
            "Use as distributed shared-state support.",
            "Do not claim production Redis HA.",
        )

    mcp_val = validation.get("mcpbench_smoke_v3", {})
    claim(
        "JSS-C10",
        "mcpbench_smoke_v3",
        "workflow_shape_smoke",
        "selected_task_count,all_client_rc_zero",
        f"MCP-Bench-derived smoke selected {mcp_val.get('selected_task_count', 'unknown')} tasks with validation errors={mcp_val.get('errors', [])}.",
        "diagnostic",
        "Metadata-derived single-machine workflow-shape smoke.",
        "Use as compatibility/workflow-shape diversity evidence.",
        "Do not claim full MCP-Bench deployment or model accuracy.",
    )

    burst_val = validation.get("burstgpt_trace_replay_v3", {})
    claim(
        "JSS-C11",
        "burstgpt_trace_replay_v3",
        "arrival_realism",
        "selected_trace_rows,windows,scale_factors",
        f"BurstGPT-shaped replay covers {burst_val.get('windows', [])} windows and scales {burst_val.get('scale_factors', [])}.",
        "diagnostic",
        "Uses real LLM-serving arrivals only; session structure remains controlled.",
        "Use as arrival-pattern realism evidence.",
        "Do not claim real agent trace performance.",
    )

    e2e_val = validation.get("business_workflow_e2e_smoke_v1", {})
    claim(
        "JSS-C12",
        "business_workflow_e2e_smoke_v1",
        "e2e_correctness_smoke",
        "http_services_started,sqlite_side_effects_present,idempotency_replay_probe_passed,deterministic_recovery_probe_passed",
        f"E2E HTTP+SQLite smoke validation errors={e2e_val.get('errors', [])}; summary rows={e2e_val.get('row_count_summary', 'unknown')}.",
        "strong_correctness" if not e2e_val.get("errors") else "boundary",
        "Correctness smoke only; raw workflow success is not the claim.",
        "Use as end-to-end service workflow correctness evidence.",
        "Do not claim real-service performance improvement.",
    )

    redis_recovered, redis_total = count_true(
        datasets["cloudlab_business_dist_recovery"], "resume_recovered", {"store": "redis"}
    )
    memory_state_miss, memory_total = count_true(
        datasets["cloudlab_business_dist_recovery"], "state_miss", {"store": "memory"}
    )
    claim(
        "JSS-C13",
        "cloudlab_business_workflow_distributed_v1",
        "distributed_e2e_correctness",
        "cross_gateway,resume_recovered,state_miss,duplicate_side_effect",
        f"CloudLab distributed probe: Redis recovered {redis_recovered}/{redis_total} forced cross-gateway resumes; memory state_miss {memory_state_miss}/{memory_total}.",
        "supporting_correctness" if redis_recovered == redis_total and memory_state_miss == memory_total else "boundary",
        "Earlier distributed correctness probe; random workload rows include expected ACTIVE_CHECKPOINT conflicts and the clean CloudLab headline is JSS-C16.",
        "Use only as supporting CloudLab distributed shared-state and cross-gateway recovery evidence.",
        "Do not claim CloudLab performance dominance or production Redis HA.",
    )

    smoke_val = validation.get("adaptive_react_recovery_smoke_v1", {})
    claim(
        "JSS-C14",
        "adaptive_react_recovery_smoke_v1",
        "cross_layer_smoke",
        "mock_included,cloudllm_included,vllm_included,all_layer_validations_errors_empty",
        f"Cross-layer smoke index includes layers={smoke_val.get('layers', [])} with validation errors={smoke_val.get('errors', [])}.",
        "diagnostic",
        "Smoke evidence only; full effects are taken from full/mock/vLLM and business-workflow artifacts.",
        "Use as execution-stability and parameter-consistency evidence across mock, cloud LLM, and vLLM layers.",
        "Do not use as headline performance ranking.",
    )

    perf_val = validation.get("business_workflow_e2e_perf_sanity_v1", {})
    perf_rows = datasets["business_e2e_perf"]
    perf_duplicate_side_effect = sum(int(to_float(row.get("duplicate_side_effect")) or 0) for row in perf_rows)
    perf_db_consistent, perf_db_total = count_true(perf_rows, "db_final_state_consistent")
    claim(
        "JSS-C15",
        "business_workflow_e2e_perf_sanity_v1",
        "e2e_perf_sanity",
        "http_requests_total,sqlite_writes_total,duplicate_side_effect,db_final_state_consistent,p95_ms",
        f"E2E HTTP+SQLite performance sanity covers {perf_val.get('row_count_summary', len(perf_rows))} runs over C={perf_val.get('concurrency_levels', [])}; duplicate_side_effect total={perf_duplicate_side_effect}, DB-consistent rows={perf_db_consistent}/{perf_db_total}, validation errors={perf_val.get('errors', [])}.",
        "strong_sanity" if not perf_val.get("errors") and perf_duplicate_side_effect == 0 and perf_db_consistent == perf_db_total else "boundary",
        "Small performance/stability sanity only; recovery probe is path-availability and raw success is not the claim.",
        "Use as real HTTP+SQLite E2E stability and side-effect-safety evidence.",
        "Do not claim PlanGate performance dominance on real services.",
    )

    det_val = validation.get("cloudlab_business_workflow_distributed_deterministic_v1", {})
    det_redis_recovered, det_redis_total = count_true(
        datasets["cloudlab_business_det_recovery"], "resume_recovered", {"store": "redis"}
    )
    det_redis_state_miss, _ = count_true(
        datasets["cloudlab_business_det_recovery"], "state_miss", {"store": "redis"}
    )
    det_memory_state_miss, det_memory_total = count_true(
        datasets["cloudlab_business_det_recovery"], "state_miss", {"store": "memory"}
    )
    det_replayed = sum(int(to_float(row.get("replayed_completed_side_effects")) or 0) for row in datasets["cloudlab_business_det_recovery"])
    claim(
        "JSS-C16",
        "cloudlab_business_workflow_distributed_deterministic_v1",
        "distributed_deterministic_correctness",
        "cross_gateway,resume_recovered,state_miss,post_resume_success,replayed_completed_side_effects",
        f"Deterministic CloudLab probe: Redis recovered {det_redis_recovered}/{det_redis_total} forced cross-gateway resumes with {det_redis_state_miss} state misses; memory state_miss {det_memory_state_miss}/{det_memory_total}; replayed completed side effects={det_replayed}; validation errors={det_val.get('errors', [])}.",
        "strong_correctness"
        if not det_val.get("errors")
        and det_redis_total
        and det_redis_recovered == det_redis_total
        and det_redis_state_miss == 0
        and det_memory_total
        and det_memory_state_miss == det_memory_total
        and det_replayed == 0
        else "boundary",
        "Deterministic distributed correctness probe; no random workload and no throughput/latency benchmark.",
        "Use as the clean CloudLab cross-gateway ReAct recovery and Redis shared-state necessity result.",
        "Do not claim CloudLab performance dominance or production Redis HA.",
    )

    e = lookup_effect(effect_rows, "request_baseline_vs_adaptive_C100_F0.2", "cascade_failed")
    w = lookup_effect(effect_rows, "request_baseline_vs_adaptive_C100_F0.2", "wasted_service_ms")
    r = lookup_effect(effect_rows, "request_baseline_vs_adaptive_C100_F0.2", "rejected_events")
    if e and w and r:
        claim(
            "JSS-C17",
            "request_level_baseline_v1",
            "request_level_baseline",
            "cascade_failed,wasted_service_ms,rejected_events",
            f"At C=100,F=0.2 request-level queue admission vs adaptive delta cascade={e['delta']} CI[{e['ci95_low_delta']},{e['ci95_high_delta']}], wasted_ms={w['delta']} CI[{w['ci95_low_delta']},{w['ci95_high_delta']}], rejected_events={r['delta']} CI[{r['ci95_low_delta']},{r['ci95_high_delta']}].",
            "baseline_evidence",
            "Baseline rejects individual requests and cannot reason about completed session prefixes; rejected_events compare request-level rejects against PlanGate step-0 rejects.",
            "Use as evidence that ordinary per-request admission does not capture session-prefix waste semantics.",
            "Do not claim universal PlanGate dominance or direct equivalence between request-level reject counts and session-aware step-0 semantics.",
        )

    rr = lookup_effect(effect_rows, "recovery_vs_idempotency_only_C20_F0.1", "resume_recovered")
    ar = lookup_effect(effect_rows, "recovery_vs_idempotency_only_C20_F0.1", "avoided_replay_steps")
    rp = lookup_effect(effect_rows, "recovery_vs_idempotency_only_C20_F0.1", "replayed_completed_steps")
    ds = lookup_effect(effect_rows, "recovery_vs_idempotency_only_C20_F0.1", "duplicate_side_effect")
    if rr and ar and rp and ds:
        claim(
            "JSS-C18",
            "idempotency_only_retry_baseline_v1",
            "idempotency_only_baseline",
            "duplicate_side_effect,avoided_replay_steps,resume_recovered,replayed_completed_steps",
            f"At C=20,F=0.1 recovery vs idempotency-only delta resume_recovered={rr['delta']} CI[{rr['ci95_low_delta']},{rr['ci95_high_delta']}], avoided_replay_steps={ar['delta']} CI[{ar['ci95_low_delta']},{ar['ci95_high_delta']}], replayed_completed_steps={rp['delta']} CI[{rp['ci95_low_delta']},{rp['ci95_high_delta']}], duplicate_side_effect={ds['delta']} CI[{ds['ci95_low_delta']},{ds['ci95_high_delta']}].",
            "baseline_evidence",
            "Idempotency-only retry preserves durable-write safety but does not restore checkpoint progress; the comparison is about progress recovery, not universal raw-success ranking.",
            "Use to distinguish idempotent retry from checkpoint recovery in side-effecting workflows.",
            "Do not claim idempotency is unnecessary or that recovery universally improves raw workflow success.",
        )

    return claims


def read_validation(artifact: str) -> dict[str, Any]:
    path = ARTIFACT_ROOT / artifact / "validation.json"
    if not path.exists():
        return {"missing_validation": True, "errors": [f"missing_validation:{artifact}"]}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)
    errors: list[str] = []
    out_dir = ARTIFACT_ROOT / args.out_dir_name

    sources = {
        "business": ("business_workflow_service_v3", "business_workflow_service_v3_run_summary.csv"),
        "business_agg": ("business_workflow_service_v3", "business_workflow_service_v3_agg.csv"),
        "business_e2e": ("business_workflow_e2e_smoke_v1", "business_workflow_e2e_smoke_summary.csv"),
        "business_e2e_replay": ("business_workflow_e2e_smoke_v1", "business_workflow_e2e_replay_probe.csv"),
        "business_e2e_recovery": ("business_workflow_e2e_smoke_v1", "business_workflow_e2e_recovery_probe.csv"),
        "business_e2e_db": ("business_workflow_e2e_smoke_v1", "business_workflow_e2e_db_summary.csv"),
        "business_e2e_perf": (
            "business_workflow_e2e_perf_sanity_v1",
            "business_workflow_e2e_perf_sanity_summary.csv",
        ),
        "business_e2e_perf_recovery": (
            "business_workflow_e2e_perf_sanity_v1",
            "business_workflow_e2e_perf_sanity_recovery_probe.csv",
        ),
        "business_e2e_perf_db": (
            "business_workflow_e2e_perf_sanity_v1",
            "business_workflow_e2e_perf_sanity_db_summary.csv",
        ),
        "operator": ("operator_overhead_v1", "operator_overhead_summary.csv"),
        "config": ("config_sensitivity_v1", "config_sensitivity_summary.csv"),
        "recovery_edges": ("recovery_protocol_edge_cases_v1", "recovery_protocol_edge_cases_summary.csv"),
        "component_matrix": ("architecture_component_ablation_v1", "component_ablation_matrix.csv"),
        "component_evidence": ("architecture_component_ablation_v1", "component_ablation_evidence.csv"),
        "adaptive_full": ("adaptive_step0_react_recovery_full_v1", "adaptive_step0_react_recovery_full_summary.csv"),
        "mock_smoke": ("adaptive_react_recovery_mock_smoke_v1", "mock_smoke_summary.csv"),
        "vllm_smoke": ("adaptive_react_recovery_vllm_smoke_v1", "vllm_smoke_summary.csv"),
        "cloudllm_smoke": ("adaptive_react_recovery_cloudllm_smoke_v1", "cloudllm_smoke_summary.csv"),
        "smoke_index": ("adaptive_react_recovery_smoke_v1", "smoke_index.csv"),
        "vllm_recovery": ("vllm_react_recovery_arm_v2", "summary.csv"),
        "cloud_backend": ("cloud_backend_smoke_v1", "cloud_backend_smoke_summary.csv"),
        "cloudlab": ("cloudlab_random_redis_memory_v1", "cloudlab_random_redis_memory_summary.csv"),
        "cloudlab_business_dist": (
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_summary.csv",
        ),
        "cloudlab_business_dist_recovery": (
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_recovery_probe.csv",
        ),
        "cloudlab_business_dist_replay": (
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_replay_probe.csv",
        ),
        "cloudlab_business_dist_db": (
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_db_summary.csv",
        ),
        "cloudlab_business_det_agg": (
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_agg.csv",
        ),
        "cloudlab_business_det_recovery": (
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_recovery_probe.csv",
        ),
        "cloudlab_business_det_replay": (
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_replay_probe.csv",
        ),
        "cloudlab_business_det_db": (
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_db_summary.csv",
        ),
        "mcpbench": ("mcpbench_smoke_v3", "mcpbench_smoke_summary.csv"),
        "burstgpt": ("burstgpt_trace_replay_v3", "burstgpt_trace_replay_summary.csv"),
    }
    if args.include_request_baseline:
        sources["request_baseline"] = ("request_level_baseline_v1", "request_level_baseline_summary.csv")
    if args.include_idempotency_baseline:
        sources["idempotency_baseline"] = (
            "idempotency_only_retry_baseline_v1",
            "idempotency_only_retry_baseline_summary.csv",
        )

    paths = {key: ARTIFACT_ROOT / artifact / filename for key, (artifact, filename) in sources.items()}
    for key, path in paths.items():
        if not path.exists():
            errors.append(f"missing_source:{key}:{path}")

    validations = {artifact: read_validation(artifact) for artifact, _ in sorted(set(sources.values()))}
    for artifact, payload in validations.items():
        if payload.get("errors"):
            errors.append(f"source_validation_errors:{artifact}:{payload.get('errors')}")

    out_dir.mkdir(parents=True, exist_ok=True)
    if errors:
        validation = {
            "artifact": args.out_dir_name,
            "required_jss_sources_present": not any(e.startswith("missing_source") for e in errors),
            "source_validation_errors_empty": not any(e.startswith("source_validation_errors") for e in errors),
            "errors": errors,
        }
        (out_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    datasets = {key: read_csv(path) for key, path in paths.items()}

    # Synthetic descriptive rows for protocol and architecture artifacts.
    for row in datasets["recovery_edges"]:
        row["passed_bool"] = "1" if row.get("passed", "").lower() == "true" else "0"
        row["recovered_bool"] = "1" if row.get("recovered", "").lower() == "true" else "0"
        row["rejected_bool"] = "1" if row.get("rejected", "").lower() == "true" else "0"
        row["auto_executed_future_tool_bool"] = "1" if row.get("auto_executed_future_tool", "").lower() == "true" else "0"
    for row in datasets["component_matrix"]:
        row["component_present"] = "1"
    for row in datasets["component_evidence"]:
        row["evidence_record"] = "1"
        row["numeric_value_available_bool"] = "1" if row.get("numeric_value_available", "").lower() == "true" else "0"
    for row in datasets["cloudlab"]:
        row["validation_passed_bool"] = "1" if row.get("validation_passed", "").lower() == "true" else "0"
    for row in (
        datasets["business_e2e_replay"]
        + datasets["cloudlab_business_dist_replay"]
        + datasets["cloudlab_business_det_replay"]
    ):
        row["passed_bool"] = "1" if row.get("passed", "").lower() == "true" else "0"
        row["side_effect_id_same_bool"] = "1" if row.get("side_effect_id_same", "").lower() == "true" else "0"
    for row in datasets["business_e2e_recovery"] + datasets["business_e2e_perf_recovery"]:
        for field in [
            "side_effect_before_failure_present",
            "resume_attempted",
            "resume_recovered",
            "resume_mode_react_client_cooperative",
            "resume_current_step_gt_zero",
            "resume_requires_client_continuation",
            "continuation_completed",
            "post_resume_success",
            "final_state_consistent",
            "passed",
        ]:
            row[f"{field}_bool"] = "1" if row.get(field, "").lower() == "true" else "0"
    for row in datasets["cloudlab_business_dist_recovery"] + datasets["cloudlab_business_det_recovery"]:
        for field in [
            "cross_gateway",
            "side_effect_before_failure_present",
            "resume_attempted",
            "resume_recovered",
            "resume_mode_react_client_cooperative",
            "resume_current_step_gt_zero",
            "resume_requires_client_continuation",
            "resume_continuation_completed",
            "post_resume_success",
            "state_miss",
            "final_state_consistent",
            "failure_safe_state",
            "passed",
        ]:
            row[f"{field}_bool"] = "1" if row.get(field, "").lower() == "true" else "0"

    stat_rows: list[dict[str, str]] = []
    stat_specs = [
        (
            "business_workflow_service",
            "core_service_workflow",
            "business_workflow_service_v3",
            "business_workflow_service_v3_run_summary.csv",
            datasets["business"],
            ["variant", "concurrency", "failure_rate"],
            [
                "success",
                "workflow_success_rate",
                "admitted_success_rate",
                "rejected_s0",
                "step0_reject_rate",
                "cascade_failed",
                "wasted_service_ms",
                "wasted_tool_calls",
                "p95_ms",
                "effective_goodput",
                "resume_recovered",
                "react_recovered_success",
                "post_resume_success",
                "avoided_replay_steps",
                "duplicate_side_effect",
                "sla_violation_count",
            ],
            "Main JSS service-workflow evidence; do not claim raw-success dominance.",
            False,
        ),
        (
            "business_workflow_e2e_smoke",
            "e2e_correctness_smoke",
            "business_workflow_e2e_smoke_v1",
            "business_workflow_e2e_smoke_summary.csv",
            datasets["business_e2e"],
            ["variant"],
            [
                "success",
                "workflow_success_rate",
                "admitted_success_rate",
                "cascade_failed",
                "unexpected_errors",
                "http_requests_total",
                "http_2xx_total",
                "sqlite_writes_total",
                "side_effects_committed",
                "duplicate_side_effect",
                "resume_recovered",
                "post_resume_success",
                "resume_continuation_completed",
                "db_final_state_consistent",
                "p95_ms",
            ],
            "End-to-end HTTP+SQLite correctness smoke; not a performance ranking.",
            False,
        ),
        (
            "business_workflow_e2e_replay_probe",
            "e2e_correctness_smoke",
            "business_workflow_e2e_smoke_v1",
            "business_workflow_e2e_replay_probe.csv",
            datasets["business_e2e_replay"],
            ["endpoint"],
            [
                "passed_bool",
                "side_effect_id_same_bool",
                "side_effect_rows_added",
                "idempotency_rows_added",
                "idempotency_replay_hits",
                "duplicate_side_effect",
            ],
            "Idempotency replay probe; descriptive correctness evidence.",
            True,
        ),
        (
            "business_workflow_e2e_recovery_probe",
            "e2e_correctness_smoke",
            "business_workflow_e2e_smoke_v1",
            "business_workflow_e2e_recovery_probe.csv",
            datasets["business_e2e_recovery"],
            ["variant"],
            [
                "side_effect_before_failure_present_bool",
                "resume_attempted_bool",
                "resume_recovered_bool",
                "resume_mode_react_client_cooperative_bool",
                "resume_current_step_gt_zero_bool",
                "resume_requires_client_continuation_bool",
                "continuation_completed_bool",
                "post_resume_success_bool",
                "avoided_replay_steps",
                "replayed_completed_side_effects",
                "final_state_consistent_bool",
                "passed_bool",
            ],
            "Deterministic E2E recovery probe; descriptive correctness evidence.",
            True,
        ),
        (
            "business_workflow_e2e_perf_sanity",
            "e2e_perf_sanity",
            "business_workflow_e2e_perf_sanity_v1",
            "business_workflow_e2e_perf_sanity_summary.csv",
            datasets["business_e2e_perf"],
            ["variant", "concurrency", "failure_rate"],
            [
                "success",
                "workflow_success_rate",
                "admitted_success_rate",
                "rejected_s0",
                "step0_reject_rate",
                "cascade_failed",
                "unexpected_errors",
                "http_requests_total",
                "http_2xx_total",
                "http_error_total",
                "sqlite_writes_total",
                "side_effects_committed",
                "duplicate_side_effect",
                "db_final_state_consistent",
                "p50_ms",
                "p95_ms",
                "effective_goodput",
            ],
            "Small HTTP+SQLite performance sanity; not a performance-dominance claim.",
            False,
        ),
        (
            "business_workflow_e2e_perf_recovery_probe",
            "e2e_perf_sanity",
            "business_workflow_e2e_perf_sanity_v1",
            "business_workflow_e2e_perf_sanity_recovery_probe.csv",
            datasets["business_e2e_perf_recovery"],
            ["variant"],
            [
                "side_effect_before_failure_present_bool",
                "resume_attempted_bool",
                "resume_recovered_bool",
                "resume_mode_react_client_cooperative_bool",
                "resume_current_step_gt_zero_bool",
                "resume_requires_client_continuation_bool",
                "continuation_completed_bool",
                "post_resume_success_bool",
                "avoided_replay_steps",
                "replayed_completed_side_effects",
                "final_state_consistent_bool",
                "passed_bool",
            ],
            "Path-availability recovery probe inside the E2E performance sanity artifact.",
            True,
        ),
        (
            "business_workflow_e2e_perf_db_summary",
            "e2e_perf_sanity",
            "business_workflow_e2e_perf_sanity_v1",
            "business_workflow_e2e_perf_sanity_db_summary.csv",
            datasets["business_e2e_perf_db"],
            ["table_name"],
            [
                "row_count",
                "unique_idempotency_keys",
                "duplicate_idempotency_keys",
                "duplicate_side_effect_rows",
            ],
            "SQLite final-state summary for the small E2E performance sanity artifact.",
            True,
        ),
        (
            "operator_overhead",
            "deployability_overhead",
            "operator_overhead_v1",
            "operator_overhead_summary.csv",
            datasets["operator"],
            ["variant", "concurrency"],
            [
                "workflow_success_rate",
                "throughput_workflows_s",
                "effective_goodput",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "gateway_cpu_delta_s",
                "gateway_peak_rss_mb",
                "gateway_avg_rss_mb",
                "duplicate_side_effect",
            ],
            "Failure-free overhead only; same-gateway baseline.",
            False,
        ),
        (
            "config_sensitivity",
            "configuration_sensitivity",
            "config_sensitivity_v1",
            "config_sensitivity_summary.csv",
            datasets["config"],
            ["variant", "governance_intensity", "concurrency", "failure_rate"],
            [
                "workflow_success_rate",
                "admitted_success_rate",
                "cascade_failed",
                "wasted_service_ms",
                "wasted_tool_calls",
                "p95_ms",
                "effective_goodput",
                "resume_recovered",
                "react_recovered_success",
                "avoided_replay_steps",
                "duplicate_side_effect",
            ],
            "Robustness evidence, not parameter tuning.",
            False,
        ),
        (
            "recovery_protocol_edge_cases",
            "protocol_usability",
            "recovery_protocol_edge_cases_v1",
            "recovery_protocol_edge_cases_summary.csv",
            datasets["recovery_edges"],
            ["category"],
            [
                "passed_bool",
                "recovered_bool",
                "rejected_bool",
                "avoided_replay_steps",
                "duplicate_side_effect",
                "auto_executed_future_tool_bool",
            ],
            "Protocol harness; not performance evidence.",
            True,
        ),
        (
            "architecture_component_matrix",
            "architecture_mapping",
            "architecture_component_ablation_v1",
            "component_ablation_matrix.csv",
            datasets["component_matrix"],
            ["component_id", "evidence_strength"],
            ["component_present"],
            "Evidence synthesis; not a newly run performance experiment.",
            True,
        ),
        (
            "architecture_component_evidence",
            "architecture_mapping",
            "architecture_component_ablation_v1",
            "component_ablation_evidence.csv",
            datasets["component_evidence"],
            ["component_id"],
            ["evidence_record", "numeric_value_available_bool"],
            "Evidence synthesis; not a newly run performance experiment.",
            True,
        ),
        (
            "adaptive_step0_react_recovery_full",
            "mechanism_boundary",
            "adaptive_step0_react_recovery_full_v1",
            "adaptive_step0_react_recovery_full_summary.csv",
            datasets["adaptive_full"],
            ["layer", "variant", "concurrency", "synthetic_recoverable_failure"],
            [
                "success",
                "cascade_failed",
                "rejected_s0_capacity",
                "react_recovered_success",
                "avoided_replay_steps",
                "duplicate_side_effect",
                "p95_ms",
                "effective_goodput_s",
                "client_timed_out",
            ],
            "Mechanism/boundary artifact; vLLM layer is hybrid agent brain plus local backend.",
            False,
        ),
        (
            "adaptive_react_recovery_mock_smoke",
            "cross_layer_smoke",
            "adaptive_react_recovery_mock_smoke_v1",
            "mock_smoke_summary.csv",
            datasets["mock_smoke"],
            ["variant"],
            [
                "success",
                "cascade_failed",
                "rejected_s0_total",
                "react_recovered_success",
                "avoided_replay_steps",
                "duplicate_side_effect",
                "effective_goodput_s",
                "p95_ms",
                "client_rc",
                "client_timed_out",
            ],
            "Mock smoke evidence only; not headline effect evidence.",
            True,
        ),
        (
            "adaptive_react_recovery_vllm_smoke",
            "cross_layer_smoke",
            "adaptive_react_recovery_vllm_smoke_v1",
            "vllm_smoke_summary.csv",
            datasets["vllm_smoke"],
            ["variant"],
            [
                "success",
                "cascade_failed",
                "rejected_s0_total",
                "react_recovered_success",
                "avoided_replay_steps",
                "duplicate_side_effect",
                "effective_goodput_s",
                "p95_ms",
                "client_rc",
                "client_timed_out",
            ],
            "vLLM smoke evidence only; full vLLM effects are represented separately.",
            True,
        ),
        (
            "adaptive_react_recovery_cloudllm_smoke",
            "cross_layer_smoke",
            "adaptive_react_recovery_cloudllm_smoke_v1",
            "cloudllm_smoke_summary.csv",
            datasets["cloudllm_smoke"],
            ["variant", "provider"],
            [
                "success",
                "cascade_failed",
                "rejected_s0_total",
                "react_recovered_success",
                "avoided_replay_steps",
                "duplicate_side_effect",
                "effective_goodput_s",
                "p95_ms",
                "client_rc",
                "client_timed_out",
            ],
            "Cloud LLM smoke evidence only; not global provider ranking.",
            True,
        ),
        (
            "adaptive_react_recovery_smoke_index",
            "cross_layer_smoke",
            "adaptive_react_recovery_smoke_v1",
            "smoke_index.csv",
            datasets["smoke_index"],
            ["layer"],
            ["row_count", "agg_row_count", "validation_errors"],
            "Cross-layer smoke index; descriptive only.",
            True,
        ),
        (
            "vllm_react_recovery_arm",
            "real_backend_recovery",
            "vllm_react_recovery_arm_v2",
            "summary.csv",
            datasets["vllm_recovery"],
            ["variant", "concurrency"],
            [
                "success",
                "cascade_failed",
                "react_recovered_success",
                "avoided_replay_steps",
                "continued_after_resume",
                "duplicate_side_effect",
                "tool_calls_total",
                "backend_llm_tokens_total",
            ],
            "Targeted vLLM recovery arm; not full natural-recovery matrix.",
            False,
        ),
        (
            "cloud_backend_smoke",
            "provider_boundary",
            "cloud_backend_smoke_v1",
            "cloud_backend_smoke_summary.csv",
            datasets["cloud_backend"],
            ["variant"],
            ["success", "cascade_failed", "p95_ms", "tool_calls", "backend_llm_tokens", "duplicate_side_effect"],
            "Single-repeat provider boundary smoke.",
            True,
        ),
        (
            "cloudlab_random_redis_memory",
            "distributed_state_diagnostic",
            "cloudlab_random_redis_memory_v1",
            "cloudlab_random_redis_memory_summary.csv",
            datasets["cloudlab"],
            ["store"],
            ["cross_node_sessions", "state_miss", "duplicate_admission", "validation_error_count", "validation_passed_bool"],
            "Shared-state correctness diagnostic; not production Redis HA.",
            False,
        ),
        (
            "cloudlab_business_workflow_distributed_summary",
            "distributed_e2e_correctness",
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_summary.csv",
            datasets["cloudlab_business_dist"],
            ["store"],
            [
                "sessions",
                "gateway_switches",
                "cross_gateway_sessions",
                "success",
                "workflow_success_rate",
                "cascade_failed",
                "client_rc",
                "unexpected_errors",
                "duplicate_side_effect",
                "cross_gateway_resume_attempts",
                "cross_gateway_resume_success",
                "cross_gateway_state_miss",
                "memory_state_miss",
                "redis_state_miss",
                "db_final_state_consistent",
                "db_no_duplicate_side_effect_rows",
                "db_no_invalid_final_confirm",
            ],
            "CloudLab distributed smoke summary; random workload rows are descriptive only.",
            True,
        ),
        (
            "cloudlab_business_workflow_distributed_recovery_probe",
            "distributed_e2e_correctness",
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_recovery_probe.csv",
            datasets["cloudlab_business_dist_recovery"],
            ["store"],
            [
                "cross_gateway_bool",
                "side_effect_before_failure_present_bool",
                "resume_attempted_bool",
                "resume_recovered_bool",
                "resume_mode_react_client_cooperative_bool",
                "resume_current_step_gt_zero_bool",
                "resume_requires_client_continuation_bool",
                "resume_continuation_completed_bool",
                "post_resume_success_bool",
                "state_miss_bool",
                "final_state_consistent_bool",
                "failure_safe_state_bool",
                "passed_bool",
                "replayed_completed_side_effects",
                "avoided_replay_steps",
            ],
            "Deterministic forced cross-gateway recovery probe; correctness evidence.",
            False,
        ),
        (
            "cloudlab_business_workflow_distributed_replay_probe",
            "distributed_e2e_correctness",
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_replay_probe.csv",
            datasets["cloudlab_business_dist_replay"],
            ["endpoint"],
            [
                "passed_bool",
                "side_effect_id_same_bool",
                "side_effect_rows_added",
                "idempotency_rows_added",
                "idempotency_replay_hits",
                "duplicate_side_effect",
            ],
            "CloudLab idempotency replay probe; descriptive correctness evidence.",
            True,
        ),
        (
            "cloudlab_business_workflow_distributed_db",
            "distributed_e2e_correctness",
            "cloudlab_business_workflow_distributed_v1",
            "cloudlab_business_workflow_distributed_db_summary.csv",
            datasets["cloudlab_business_dist_db"],
            ["store", "table_name"],
            [
                "row_count",
                "unique_idempotency_keys",
                "duplicate_idempotency_keys",
                "duplicate_side_effect_rows",
            ],
            "CloudLab SQLite DB consistency summary; descriptive correctness evidence.",
            True,
        ),
        (
            "cloudlab_business_workflow_distributed_deterministic_agg",
            "distributed_deterministic_correctness",
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_agg.csv",
            datasets["cloudlab_business_det_agg"],
            ["store"],
            [
                "probe_count",
                "cross_gateway_count",
                "resume_attempted_count",
                "resume_recovered_count",
                "resume_recovered_rate",
                "state_miss_count",
                "state_miss_rate",
                "post_resume_success_count",
                "post_resume_success_rate",
                "replayed_completed_side_effect_count",
                "duplicate_side_effect_count",
                "final_state_consistent_count",
                "failure_safe_state_count",
                "passed_count",
                "passed_rate",
            ],
            "Deterministic CloudLab aggregate; distributed correctness only, not performance.",
            True,
        ),
        (
            "cloudlab_business_workflow_distributed_deterministic_recovery_probe",
            "distributed_deterministic_correctness",
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_recovery_probe.csv",
            datasets["cloudlab_business_det_recovery"],
            ["store"],
            [
                "cross_gateway_bool",
                "side_effect_before_failure_present_bool",
                "resume_attempted_bool",
                "resume_recovered_bool",
                "resume_mode_react_client_cooperative_bool",
                "resume_current_step_gt_zero_bool",
                "resume_requires_client_continuation_bool",
                "resume_continuation_completed_bool",
                "post_resume_success_bool",
                "state_miss_bool",
                "final_state_consistent_bool",
                "failure_safe_state_bool",
                "passed_bool",
                "replayed_completed_side_effects",
                "avoided_replay_steps",
            ],
            "Deterministic CloudLab forced cross-gateway recovery probe; correctness evidence.",
            False,
        ),
        (
            "cloudlab_business_workflow_distributed_deterministic_replay_probe",
            "distributed_deterministic_correctness",
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_replay_probe.csv",
            datasets["cloudlab_business_det_replay"],
            ["endpoint"],
            [
                "passed_bool",
                "side_effect_id_same_bool",
                "side_effect_rows_added",
                "idempotency_rows_added",
                "idempotency_replay_hits",
                "duplicate_side_effect",
            ],
            "Deterministic CloudLab idempotency replay probe; descriptive correctness evidence.",
            True,
        ),
        (
            "cloudlab_business_workflow_distributed_deterministic_db",
            "distributed_deterministic_correctness",
            "cloudlab_business_workflow_distributed_deterministic_v1",
            "cloudlab_business_workflow_distributed_deterministic_db_summary.csv",
            datasets["cloudlab_business_det_db"],
            ["store", "table_name"],
            [
                "row_count",
                "unique_idempotency_keys",
                "duplicate_idempotency_keys",
                "duplicate_side_effect_rows",
            ],
            "Deterministic CloudLab SQLite DB consistency summary; descriptive correctness evidence.",
            True,
        ),
        (
            "mcpbench_smoke",
            "workflow_shape_smoke",
            "mcpbench_smoke_v3",
            "mcpbench_smoke_summary.csv",
            datasets["mcpbench"],
            ["gateway"],
            ["success_rate", "cascade_failed", "effective_goodput_s", "p95_ms"],
            "Metadata-derived workflow-shape smoke; not full MCP-Bench deployment.",
            True,
        ),
        (
            "burstgpt_trace_replay",
            "arrival_realism",
            "burstgpt_trace_replay_v3",
            "burstgpt_trace_replay_summary.csv",
            datasets["burstgpt"],
            ["gateway", "window_id", "scale"],
            ["success_rate", "cascade_failed", "effective_goodput_s", "p95_ms"],
            "Arrival-pattern realism only; not real agent traces.",
            True,
        ),
    ]
    if args.include_request_baseline:
        stat_specs.append(
            (
                "request_level_baseline",
                "request_level_baseline",
                "request_level_baseline_v1",
                "request_level_baseline_summary.csv",
                datasets["request_baseline"],
                ["variant", "concurrency", "failure_rate"],
                [
                    "success",
                    "workflow_success_rate",
                    "admitted_success_rate",
                    "rejected_s0",
                    "request_level_rejected",
                    "cascade_failed",
                    "wasted_service_ms",
                    "wasted_tool_calls",
                    "p95_ms",
                    "effective_goodput",
                    "duplicate_side_effect",
                ],
                "Request-level queue-admission baseline; ignores session progress, recovery, and continuation value.",
                False,
            )
        )
    if args.include_idempotency_baseline:
        stat_specs.append(
            (
                "idempotency_only_retry_baseline",
                "idempotency_only_baseline",
                "idempotency_only_retry_baseline_v1",
                "idempotency_only_retry_baseline_summary.csv",
                datasets["idempotency_baseline"],
                ["variant", "concurrency", "failure_rate"],
                [
                    "success",
                    "workflow_success_rate",
                    "cascade_failed",
                    "partial",
                    "duplicate_side_effect",
                    "resume_recovered",
                    "avoided_replay_steps",
                    "post_resume_success",
                    "restart_attempted",
                    "retry_attempted",
                    "replayed_completed_steps",
                    "http_requests_total",
                    "sqlite_writes_total",
                    "p95_ms",
                ],
                "Idempotency-only retry baseline for HTTP+SQLite workflows; preserves duplicate-side-effect safety but does not restore checkpoint progress.",
                False,
            )
        )

    for experiment, role, artifact, source_csv, rows, group_fields, metrics, boundary, force_desc in stat_specs:
        stat_rows.extend(
            collect_stat_rows(
                experiment=experiment,
                evidence_role=role,
                source_artifact=artifact,
                source_csv=f"artifact_results/{artifact}/{source_csv}",
                rows=rows,
                group_fields=group_fields,
                metrics=metrics,
                claim_boundary=boundary,
                rng=rng,
                bootstrap_iters=args.bootstrap_iters,
                force_descriptive=force_desc,
            )
        )

    effect_rows: list[dict[str, str]] = []

    # Business-workflow core comparisons.
    for concurrency in ["50", "100"]:
        for failure_rate in ["0.0", "0.1", "0.2"]:
            for metric in [
                "workflow_success_rate",
                "admitted_success_rate",
                "cascade_failed",
                "wasted_service_ms",
                "wasted_tool_calls",
                "p95_ms",
                "effective_goodput",
            ]:
                add_effect(
                    effect_rows,
                    experiment="business_workflow_service",
                    evidence_role="core_service_workflow",
                    source_artifact="business_workflow_service_v3",
                    comparison=f"business_adaptive_vs_no_capacity_C{concurrency}_F{failure_rate}",
                    metric=metric,
                    group_a="plangate_adaptive",
                    group_b="plangate_no_capacity_step0",
                    values_a=filter_values(
                        datasets["business"],
                        {"variant": "plangate_adaptive", "concurrency": concurrency, "failure_rate": failure_rate},
                        metric,
                    ),
                    values_b=filter_values(
                        datasets["business"],
                        {"variant": "plangate_no_capacity_step0", "concurrency": concurrency, "failure_rate": failure_rate},
                        metric,
                    ),
                    claim_boundary="Primary governance trade-off; raw workflow success can favor no-capacity in some cells.",
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )
                add_effect(
                    effect_rows,
                    experiment="business_workflow_service",
                    evidence_role="core_service_workflow",
                    source_artifact="business_workflow_service_v3",
                    comparison=f"business_adaptive_vs_strict_C{concurrency}_F{failure_rate}",
                    metric=metric,
                    group_a="plangate_adaptive",
                    group_b="plangate_strict",
                    values_a=filter_values(
                        datasets["business"],
                        {"variant": "plangate_adaptive", "concurrency": concurrency, "failure_rate": failure_rate},
                        metric,
                    ),
                    values_b=filter_values(
                        datasets["business"],
                        {"variant": "plangate_strict", "concurrency": concurrency, "failure_rate": failure_rate},
                        metric,
                    ),
                    claim_boundary="Adaptive-vs-strict boundary; use as trade-off evidence.",
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )

            for metric in [
                "react_recovered_success",
                "avoided_replay_steps",
                "post_resume_success",
                "cascade_failed",
                "wasted_service_ms",
                "duplicate_side_effect",
                "workflow_success_rate",
                "admitted_success_rate",
            ]:
                add_effect(
                    effect_rows,
                    experiment="business_workflow_service",
                    evidence_role="core_recovery",
                    source_artifact="business_workflow_service_v3",
                    comparison=f"business_recovery_vs_adaptive_C{concurrency}_F{failure_rate}",
                    metric=metric,
                    group_a="plangate_adaptive_react_recovery",
                    group_b="plangate_adaptive",
                    values_a=filter_values(
                        datasets["business"],
                        {
                            "variant": "plangate_adaptive_react_recovery",
                            "concurrency": concurrency,
                            "failure_rate": failure_rate,
                        },
                        metric,
                    ),
                    values_b=filter_values(
                        datasets["business"],
                        {"variant": "plangate_adaptive", "concurrency": concurrency, "failure_rate": failure_rate},
                        metric,
                    ),
                    claim_boundary="Recovery effect; no automatic future-tool execution claim.",
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )

    if args.include_request_baseline:
        for concurrency in ["50", "100"]:
            for failure_rate in ["0.0", "0.2"]:
                for comparison, group_b in [
                    ("request_baseline_vs_adaptive", "plangate_adaptive"),
                    ("request_baseline_vs_no_capacity", "plangate_no_capacity_step0"),
                ]:
                    for metric, metric_a, metric_b in [
                        ("success", "success", "success"),
                        ("cascade_failed", "cascade_failed", "cascade_failed"),
                        ("wasted_service_ms", "wasted_service_ms", "wasted_service_ms"),
                        ("rejected_events", "request_level_rejected", "rejected_s0"),
                    ]:
                        add_effect(
                            effect_rows,
                            experiment="request_level_baseline",
                            evidence_role="request_level_baseline",
                            source_artifact="request_level_baseline_v1",
                            comparison=f"{comparison}_C{concurrency}_F{failure_rate}",
                            metric=metric,
                            group_a="request_queue_limit",
                            group_b=group_b,
                            values_a=filter_values(
                                datasets["request_baseline"],
                                {
                                    "variant": "request_queue_limit",
                                    "concurrency": concurrency,
                                    "failure_rate": failure_rate,
                                },
                                metric_a,
                            ),
                            values_b=filter_values(
                                datasets["business"],
                                {"variant": group_b, "concurrency": concurrency, "failure_rate": failure_rate},
                                metric_b,
                            ),
                            claim_boundary="Request-level queue admission baseline; rejection counts compare request-level rejects against PlanGate step-0 rejects.",
                            rng=rng,
                            bootstrap_iters=args.bootstrap_iters,
                        )

    if args.include_idempotency_baseline:
        for concurrency in ["10", "20"]:
            for metric in [
                "resume_recovered",
                "avoided_replay_steps",
                "post_resume_success",
                "duplicate_side_effect",
                "replayed_completed_steps",
                "http_requests_total",
                "sqlite_writes_total",
                "cascade_failed",
                "success",
            ]:
                add_effect(
                    effect_rows,
                    experiment="idempotency_only_retry_baseline",
                    evidence_role="idempotency_only_baseline",
                    source_artifact="idempotency_only_retry_baseline_v1",
                    comparison=f"recovery_vs_idempotency_only_C{concurrency}_F0.1",
                    metric=metric,
                    group_a="plangate_adaptive_react_recovery",
                    group_b="idempotency_only_retry",
                    values_a=filter_values(
                        datasets["idempotency_baseline"],
                        {
                            "variant": "plangate_adaptive_react_recovery",
                            "concurrency": concurrency,
                            "failure_rate": "0.1",
                        },
                        metric,
                    ),
                    values_b=filter_values(
                        datasets["idempotency_baseline"],
                        {
                            "variant": "idempotency_only_retry",
                            "concurrency": concurrency,
                            "failure_rate": "0.1",
                        },
                        metric,
                    ),
                    claim_boundary="Idempotency-only retry baseline; compare duplicate-side-effect safety with checkpoint-progress recovery, not universal raw-success ranking.",
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )

    # Operator overhead.
    # E2E HTTP+SQLite small performance sanity comparisons.
    for concurrency in ["10", "20"]:
        for metric in [
            "workflow_success_rate",
            "cascade_failed",
            "p95_ms",
            "effective_goodput",
            "sqlite_writes_total",
            "duplicate_side_effect",
            "db_final_state_consistent",
        ]:
            add_effect(
                effect_rows,
                experiment="business_workflow_e2e_perf_sanity",
                evidence_role="e2e_perf_sanity",
                source_artifact="business_workflow_e2e_perf_sanity_v1",
                comparison=f"e2e_perf_adaptive_vs_ng_C{concurrency}",
                metric=metric,
                group_a="plangate_adaptive",
                group_b="ng",
                values_a=filter_values(
                    datasets["business_e2e_perf"], {"variant": "plangate_adaptive", "concurrency": concurrency}, metric
                ),
                values_b=filter_values(datasets["business_e2e_perf"], {"variant": "ng", "concurrency": concurrency}, metric),
                claim_boundary="Small real HTTP+SQLite sanity comparison; not a performance-dominance claim.",
                rng=rng,
                bootstrap_iters=args.bootstrap_iters,
            )
            add_effect(
                effect_rows,
                experiment="business_workflow_e2e_perf_sanity",
                evidence_role="e2e_perf_sanity",
                source_artifact="business_workflow_e2e_perf_sanity_v1",
                comparison=f"e2e_perf_recovery_vs_adaptive_C{concurrency}",
                metric=metric,
                group_a="plangate_adaptive_react_recovery",
                group_b="plangate_adaptive",
                values_a=filter_values(
                    datasets["business_e2e_perf"],
                    {"variant": "plangate_adaptive_react_recovery", "concurrency": concurrency},
                    metric,
                ),
                values_b=filter_values(
                    datasets["business_e2e_perf"], {"variant": "plangate_adaptive", "concurrency": concurrency}, metric
                ),
                claim_boundary="Small real HTTP+SQLite sanity comparison; recovery variant is not claimed to dominate raw success.",
                rng=rng,
                bootstrap_iters=args.bootstrap_iters,
            )

    # Operator overhead.
    for concurrency in ["20", "50", "100"]:
        for variant in ["plangate_adaptive", "plangate_adaptive_react_recovery"]:
            for metric in [
                "throughput_workflows_s",
                "effective_goodput",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "gateway_cpu_delta_s",
                "gateway_peak_rss_mb",
                "gateway_avg_rss_mb",
            ]:
                add_effect(
                    effect_rows,
                    experiment="operator_overhead",
                    evidence_role="deployability_overhead",
                    source_artifact="operator_overhead_v1",
                    comparison=f"operator_{variant.replace('plangate_', '')}_vs_baseline_C{concurrency}",
                    metric=metric,
                    group_a=variant,
                    group_b="baseline_gateway",
                    values_a=filter_values(datasets["operator"], {"variant": variant, "concurrency": concurrency}, metric),
                    values_b=filter_values(
                        datasets["operator"], {"variant": "baseline_gateway", "concurrency": concurrency}, metric
                    ),
                    claim_boundary="Failure-free overhead against same-gateway baseline.",
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )

    # Configuration sensitivity around default.
    for base in ["plangate_adaptive", "plangate_adaptive_react_recovery"]:
        for intensity in ["light", "strict"]:
            for concurrency in ["50", "100"]:
                for failure_rate in ["0.0", "0.1", "0.2"]:
                    for metric in [
                        "workflow_success_rate",
                        "admitted_success_rate",
                        "cascade_failed",
                        "wasted_service_ms",
                        "p95_ms",
                        "effective_goodput",
                        "react_recovered_success",
                        "avoided_replay_steps",
                        "duplicate_side_effect",
                    ]:
                        add_effect(
                            effect_rows,
                            experiment="config_sensitivity",
                            evidence_role="configuration_sensitivity",
                            source_artifact="config_sensitivity_v1",
                            comparison=f"config_{base.replace('plangate_', '')}_{intensity}_vs_default_C{concurrency}_F{failure_rate}",
                            metric=metric,
                            group_a=f"{base}_{intensity}",
                            group_b=f"{base}_default",
                            values_a=filter_values(
                                datasets["config"],
                                {
                                    "variant": f"{base}_{intensity}",
                                    "concurrency": concurrency,
                                    "failure_rate": failure_rate,
                                },
                                metric,
                            ),
                            values_b=filter_values(
                                datasets["config"],
                                {
                                    "variant": f"{base}_default",
                                    "concurrency": concurrency,
                                    "failure_rate": failure_rate,
                                },
                                metric,
                            ),
                            claim_boundary="Robustness/sensitivity only, not best-parameter selection.",
                            rng=rng,
                            bootstrap_iters=args.bootstrap_iters,
                        )

    # vLLM targeted recovery arm.
    for concurrency in ["2", "4"]:
        for metric in [
            "success",
            "cascade_failed",
            "react_recovered_success",
            "avoided_replay_steps",
            "continued_after_resume",
            "duplicate_side_effect",
            "tool_calls_total",
            "backend_llm_tokens_total",
        ]:
            add_effect(
                effect_rows,
                experiment="vllm_react_recovery_arm",
                evidence_role="real_backend_recovery",
                source_artifact="vllm_react_recovery_arm_v2",
                comparison=f"vllm_recovery_vs_adaptive_C{concurrency}",
                metric=metric,
                group_a="plangate_adaptive_react_recovery",
                group_b="plangate_adaptive",
                values_a=filter_values(
                    datasets["vllm_recovery"],
                    {"variant": "plangate_adaptive_react_recovery", "concurrency": concurrency},
                    metric,
                ),
                values_b=filter_values(
                    datasets["vllm_recovery"], {"variant": "plangate_adaptive", "concurrency": concurrency}, metric
                ),
                claim_boundary="Targeted local-vLLM recovery arm; not a full natural-recovery matrix.",
                rng=rng,
                bootstrap_iters=args.bootstrap_iters,
            )

    # CloudLab state-store diagnostic.
    for metric in ["state_miss", "duplicate_admission"]:
        add_effect(
            effect_rows,
            experiment="cloudlab_random_redis_memory",
            evidence_role="distributed_state_diagnostic",
            source_artifact="cloudlab_random_redis_memory_v1",
            comparison="cloudlab_redis_vs_memory",
            metric=metric,
            group_a="redis",
            group_b="memory",
            values_a=filter_values(datasets["cloudlab"], {"store": "redis"}, metric),
            values_b=filter_values(datasets["cloudlab"], {"store": "memory"}, metric),
            claim_boundary="Shared-state correctness diagnostic, not production HA.",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )

    # CloudLab distributed business-workflow correctness probe.
    for metric in [
        "resume_recovered_bool",
        "state_miss_bool",
        "passed_bool",
        "resume_continuation_completed_bool",
        "post_resume_success_bool",
        "replayed_completed_side_effects",
    ]:
        add_effect(
            effect_rows,
            experiment="cloudlab_business_workflow_distributed",
            evidence_role="distributed_e2e_correctness",
            source_artifact="cloudlab_business_workflow_distributed_v1",
            comparison="cloudlab_business_redis_vs_memory_recovery_probe",
            metric=metric,
            group_a="redis",
            group_b="memory",
            values_a=filter_values(datasets["cloudlab_business_dist_recovery"], {"store": "redis"}, metric),
            values_b=filter_values(datasets["cloudlab_business_dist_recovery"], {"store": "memory"}, metric),
            claim_boundary="Deterministic cross-gateway recovery correctness probe; not performance benchmark.",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )

    # Clean deterministic CloudLab distributed business-workflow correctness probe.
    for metric in [
        "resume_recovered_bool",
        "state_miss_bool",
        "passed_bool",
        "resume_continuation_completed_bool",
        "post_resume_success_bool",
        "replayed_completed_side_effects",
    ]:
        add_effect(
            effect_rows,
            experiment="cloudlab_business_workflow_distributed_deterministic",
            evidence_role="distributed_deterministic_correctness",
            source_artifact="cloudlab_business_workflow_distributed_deterministic_v1",
            comparison="cloudlab_business_deterministic_redis_vs_memory_recovery_probe",
            metric=metric,
            group_a="redis",
            group_b="memory",
            values_a=filter_values(datasets["cloudlab_business_det_recovery"], {"store": "redis"}, metric),
            values_b=filter_values(datasets["cloudlab_business_det_recovery"], {"store": "memory"}, metric),
            claim_boundary="Deterministic cross-gateway recovery correctness probe; not performance benchmark or production Redis HA.",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )

    # Provider, MCPBench, and BurstGPT are diagnostic/smoke, so keep descriptive.
    for metric in ["success", "cascade_failed", "p95_ms", "backend_llm_tokens"]:
        add_effect(
            effect_rows,
            experiment="cloud_backend_smoke",
            evidence_role="provider_boundary",
            source_artifact="cloud_backend_smoke_v1",
            comparison="cloud_backend_no_capacity_vs_adaptive",
            metric=metric,
            group_a="plangate_no_capacity_step0",
            group_b="plangate_adaptive",
            values_a=filter_values(datasets["cloud_backend"], {"variant": "plangate_no_capacity_step0"}, metric),
            values_b=filter_values(datasets["cloud_backend"], {"variant": "plangate_adaptive"}, metric),
            claim_boundary="Single-repeat cloud-provider boundary smoke.",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
            force_descriptive=True,
        )

    for gateway in ["plangate_relaxed"]:
        for baseline in ["ng", "pp", "rajomon", "static"]:
            for metric in ["success_rate", "cascade_failed", "effective_goodput_s", "p95_ms"]:
                add_effect(
                    effect_rows,
                    experiment="mcpbench_smoke",
                    evidence_role="workflow_shape_smoke",
                    source_artifact="mcpbench_smoke_v3",
                    comparison=f"mcpbench_{gateway}_vs_{baseline}",
                    metric=metric,
                    group_a=gateway,
                    group_b=baseline,
                    values_a=filter_values(datasets["mcpbench"], {"gateway": gateway}, metric),
                    values_b=filter_values(datasets["mcpbench"], {"gateway": baseline}, metric),
                    claim_boundary="Workflow-shape smoke only; do not use for ranking.",
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                    force_descriptive=True,
                )

    for baseline in ["ng", "static", "pp", "rajomon"]:
        for window in ["normal", "burst", "peak_burst"]:
            for scale in ["1.0", "1.5", "2.0"]:
                for metric in ["success_rate", "cascade_failed", "effective_goodput_s", "p95_ms"]:
                    add_effect(
                        effect_rows,
                        experiment="burstgpt_trace_replay",
                        evidence_role="arrival_realism",
                        source_artifact="burstgpt_trace_replay_v3",
                        comparison=f"burst_plangate_relaxed_vs_{baseline}_{window}_s{scale}",
                        metric=metric,
                        group_a="plangate_relaxed",
                        group_b=baseline,
                        values_a=filter_values(
                            datasets["burstgpt"],
                            {"gateway": "plangate_relaxed", "window_id": window, "scale": scale},
                            metric,
                        ),
                        values_b=filter_values(
                            datasets["burstgpt"],
                            {"gateway": baseline, "window_id": window, "scale": scale},
                            metric,
                        ),
                        claim_boundary="Arrival-pattern diagnostic only; not real agent workflow trace.",
                        rng=rng,
                        bootstrap_iters=args.bootstrap_iters,
                        force_descriptive=True,
                    )

    claim_rows = build_claim_rows(effect_rows, datasets, validations)

    stat_fields = [
        "experiment",
        "evidence_role",
        "source_artifact",
        "source_csv",
        "group_key",
        "metric",
        "n",
        "mean",
        "std",
        "ci95_low",
        "ci95_high",
        "descriptive_only",
        "claim_boundary",
    ]
    effect_fields = [
        "experiment",
        "evidence_role",
        "source_artifact",
        "comparison",
        "metric",
        "group_a",
        "group_b",
        "n_a",
        "n_b",
        "mean_a",
        "mean_b",
        "delta",
        "relative_delta_pct",
        "ci95_low_delta",
        "ci95_high_delta",
        "descriptive_only",
        "interpretation",
        "claim_boundary",
    ]
    claim_fields = [
        "claim_id",
        "source_artifact",
        "evidence_role",
        "primary_metrics",
        "result_summary",
        "support_level",
        "caveat",
        "allowed_use",
        "not_allowed",
    ]

    write_csv(out_dir / "statistical_summary.csv", stat_rows, stat_fields)
    write_csv(out_dir / "effect_size_summary.csv", effect_rows, effect_fields)
    write_csv(out_dir / "claim_summary.csv", claim_rows, claim_fields)

    no_nan = True
    for rows in [stat_rows, effect_rows]:
        for row in rows:
            for value in row.values():
                if str(value).lower() in {"nan", "inf", "-inf"}:
                    no_nan = False

    source_artifact_counts = Counter(row["source_artifact"] for row in stat_rows)
    effect_role_counts = Counter(row["evidence_role"] for row in effect_rows)
    claim_role_counts = Counter(row["evidence_role"] for row in claim_rows)

    validation = {
        "artifact": args.out_dir_name,
        "generated_from_artifacts": sorted({artifact for artifact, _ in sources.values()}),
        "jss_core_artifacts_included": {
            "business_workflow_service_v3": "business_workflow_service_v3" in source_artifact_counts,
            "operator_overhead_v1": "operator_overhead_v1" in source_artifact_counts,
            "architecture_component_ablation_v1": "architecture_component_ablation_v1" in source_artifact_counts,
            "config_sensitivity_v1": "config_sensitivity_v1" in source_artifact_counts,
            "recovery_protocol_edge_cases_v1": "recovery_protocol_edge_cases_v1" in source_artifact_counts,
        },
        "jss_correctness_artifacts_included": {
            "business_workflow_e2e_smoke_v1": "business_workflow_e2e_smoke_v1" in source_artifact_counts,
            "business_workflow_e2e_perf_sanity_v1": "business_workflow_e2e_perf_sanity_v1"
            in source_artifact_counts,
            "cloudlab_business_workflow_distributed_v1": "cloudlab_business_workflow_distributed_v1"
            in source_artifact_counts,
            "cloudlab_business_workflow_distributed_deterministic_v1": "cloudlab_business_workflow_distributed_deterministic_v1"
            in source_artifact_counts,
        },
        "supporting_artifacts_included": {
            "adaptive_step0_react_recovery_full_v1": "adaptive_step0_react_recovery_full_v1" in source_artifact_counts,
            "adaptive_react_recovery_mock_smoke_v1": "adaptive_react_recovery_mock_smoke_v1" in source_artifact_counts,
            "adaptive_react_recovery_vllm_smoke_v1": "adaptive_react_recovery_vllm_smoke_v1" in source_artifact_counts,
            "adaptive_react_recovery_cloudllm_smoke_v1": "adaptive_react_recovery_cloudllm_smoke_v1"
            in source_artifact_counts,
            "adaptive_react_recovery_smoke_v1": "adaptive_react_recovery_smoke_v1" in source_artifact_counts,
            "vllm_react_recovery_arm_v2": "vllm_react_recovery_arm_v2" in source_artifact_counts,
            "cloud_backend_smoke_v1": "cloud_backend_smoke_v1" in source_artifact_counts,
            "cloudlab_random_redis_memory_v1": "cloudlab_random_redis_memory_v1" in source_artifact_counts,
            "cloudlab_business_workflow_distributed_v1": "cloudlab_business_workflow_distributed_v1"
            in source_artifact_counts,
            "cloudlab_business_workflow_distributed_deterministic_v1": "cloudlab_business_workflow_distributed_deterministic_v1"
            in source_artifact_counts,
            "business_workflow_e2e_smoke_v1": "business_workflow_e2e_smoke_v1" in source_artifact_counts,
            "business_workflow_e2e_perf_sanity_v1": "business_workflow_e2e_perf_sanity_v1"
            in source_artifact_counts,
            "mcpbench_smoke_v3": "mcpbench_smoke_v3" in source_artifact_counts,
            "burstgpt_trace_replay_v3": "burstgpt_trace_replay_v3" in source_artifact_counts,
            "request_level_baseline_v1": "request_level_baseline_v1" in source_artifact_counts,
            "idempotency_only_retry_baseline_v1": "idempotency_only_retry_baseline_v1" in source_artifact_counts,
        },
        "row_count_statistical_summary": len(stat_rows),
        "row_count_effect_size_summary": len(effect_rows),
        "row_count_claim_summary": len(claim_rows),
        "source_artifact_counts": dict(sorted(source_artifact_counts.items())),
        "effect_role_counts": dict(sorted(effect_role_counts.items())),
        "claim_role_counts": dict(sorted(claim_role_counts.items())),
        "bootstrap_iters": args.bootstrap_iters,
        "seed": args.seed,
        "required_jss_sources_present": True,
        "source_validation_errors_empty": True,
        "no_nan_or_inf": no_nan,
        "errors": [] if no_nan else ["nan_or_inf_detected"],
    }
    (out_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = "\n".join(
        [
            f"# JSS Statistical Summary ({args.out_dir_name})",
            "",
            "This artifact consolidates JSS-oriented PlanGate evidence into one statistical/effect/claim bundle.",
            "It reads existing artifact CSV files only; it does not run new experiments or modify mechanism code.",
            "",
            "## Included JSS Core Artifacts",
            "",
            "- business_workflow_service_v3",
            "- operator_overhead_v1",
            "- architecture_component_ablation_v1",
            "- config_sensitivity_v1",
            "- recovery_protocol_edge_cases_v1",
            "- business_workflow_e2e_smoke_v1",
            "- business_workflow_e2e_perf_sanity_v1",
            "- cloudlab_business_workflow_distributed_v1",
            "- cloudlab_business_workflow_distributed_deterministic_v1",
            *([] if not args.include_request_baseline else ["- request_level_baseline_v1"]),
            *([] if not args.include_idempotency_baseline else ["- idempotency_only_retry_baseline_v1"]),
            "",
            "## Included Supporting/Boundary Artifacts",
            "",
            "- adaptive_step0_react_recovery_full_v1",
            "- adaptive_react_recovery_mock_smoke_v1",
            "- adaptive_react_recovery_vllm_smoke_v1",
            "- adaptive_react_recovery_cloudllm_smoke_v1",
            "- adaptive_react_recovery_smoke_v1",
            "- vllm_react_recovery_arm_v2",
            "- cloud_backend_smoke_v1",
            "- cloudlab_random_redis_memory_v1",
            "- mcpbench_smoke_v3",
            "- burstgpt_trace_replay_v3",
            *([] if not args.include_request_baseline else ["- request_level_baseline_v1 (request-level baseline)"]),
            *([] if not args.include_idempotency_baseline else ["- idempotency_only_retry_baseline_v1 (idempotent retry baseline)"]),
            "",
            "## Claim Boundaries",
            "",
            f"- Default bootstrap iterations: {BOOTSTRAP_ITERS_DEFAULT}. Override with --bootstrap-iters when needed.",
            "- Do not claim raw-success dominance.",
            "- Do not treat MCPBench metadata smoke as full MCP-Bench deployment.",
            "- Do not treat BurstGPT-shaped replay as a real agent-workflow trace.",
            "- Do not use CloudLab Redis evidence as production Redis HA proof.",
            "- Treat business_workflow_e2e_perf_sanity_v1 as sanity/stability evidence, not as real-service performance dominance.",
            "- Prefer cloudlab_business_workflow_distributed_deterministic_v1 for the clean deterministic CloudLab correctness claim.",
            "- Use architecture_component_ablation_v1 as an evidence map, not as a new performance run.",
            "- Treat request_level_baseline_v1 as a request-level governance baseline that ignores session progress and recovery."
            if args.include_request_baseline
            else "",
            "- Treat idempotency_only_retry_baseline_v1 as a side-effect-safety baseline that preserves idempotency but does not restore checkpoint progress."
            if args.include_idempotency_baseline
            else "",
            "",
            "## Outputs",
            "",
            "- statistical_summary.csv",
            "- effect_size_summary.csv",
            "- claim_summary.csv",
            "- validation.json",
            "- README_RESULT.md",
            "",
        ]
    )
    readme = "\n".join(line for line in readme.splitlines() if line != "")
    (out_dir / "README_RESULT.md").write_text(readme + "\n", encoding="utf-8")

    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
