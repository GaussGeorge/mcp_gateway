#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = REPO_ROOT / "artifact_results"

REQUIRED_INDEX_FILES = [
    ARTIFACT_ROOT / "ARTIFACT_INDEX.md",
    ARTIFACT_ROOT / "JSS_ARTIFACT_MANIFEST.md",
]

REQUIRED_ARTIFACTS = {
    "business_workflow_service_v3": ["validation.json"],
    "operator_overhead_v1": ["validation.json"],
    "architecture_component_ablation_v1": ["validation.json"],
    "config_sensitivity_v1": ["validation.json"],
    "recovery_protocol_edge_cases_v1": ["validation.json"],
    "business_workflow_e2e_smoke_v1": ["validation.json"],
    "business_workflow_e2e_perf_sanity_v1": [
        "validation.json",
        "business_workflow_e2e_perf_sanity_summary.csv",
        "business_workflow_e2e_perf_sanity_agg.csv",
        "business_workflow_e2e_perf_sanity_recovery_probe.csv",
        "business_workflow_e2e_perf_sanity_db_summary.csv",
        "README_RESULT.md",
    ],
    "cloudlab_business_workflow_distributed_v1": ["validation.json"],
    "cloudlab_business_workflow_distributed_deterministic_v1": [
        "validation.json",
        "cloudlab_business_workflow_distributed_deterministic_agg.csv",
        "cloudlab_business_workflow_distributed_deterministic_recovery_probe.csv",
        "cloudlab_business_workflow_distributed_deterministic_replay_probe.csv",
        "cloudlab_business_workflow_distributed_deterministic_db_summary.csv",
        "cloudlab_business_workflow_distributed_deterministic_preflight.json",
        "README_RESULT.md",
    ],
    "request_level_baseline_v1": [
        "validation.json",
        "request_level_baseline_summary.csv",
        "request_level_baseline_agg.csv",
        "request_level_baseline_effects.csv",
        "README_RESULT.md",
    ],
    "idempotency_only_retry_baseline_v1": [
        "validation.json",
        "idempotency_only_retry_baseline_summary.csv",
        "idempotency_only_retry_baseline_agg.csv",
        "idempotency_only_retry_baseline_effects.csv",
        "idempotency_only_retry_baseline_replay_probe.csv",
        "idempotency_only_retry_baseline_recovery_probe.csv",
        "idempotency_only_retry_baseline_db_summary.csv",
        "README_RESULT.md",
    ],
    "adaptive_step0_react_recovery_full_v1": ["validation.json"],
    "adaptive_react_recovery_mock_smoke_v1": ["validation.json"],
    "adaptive_react_recovery_vllm_smoke_v1": ["validation.json"],
    "adaptive_react_recovery_cloudllm_smoke_v1": ["validation.json"],
    "adaptive_react_recovery_smoke_v1": ["validation.json"],
    "vllm_react_recovery_arm_v2": ["validation.json"],
    "cloud_backend_smoke_v1": ["validation.json"],
    "cloudlab_random_redis_memory_v1": ["validation.json"],
    "mcpbench_smoke_v3": ["validation.json"],
    "burstgpt_trace_replay_v3": ["validation.json"],
    "statistical_summary_v2": [
        "validation.json",
        "statistical_summary.csv",
        "effect_size_summary.csv",
        "claim_summary.csv",
        "README_RESULT.md",
    ],
    "statistical_summary_v3": [
        "validation.json",
        "statistical_summary.csv",
        "effect_size_summary.csv",
        "claim_summary.csv",
        "README_RESULT.md",
    ],
    "statistical_summary_v4": [
        "validation.json",
        "statistical_summary.csv",
        "effect_size_summary.csv",
        "claim_summary.csv",
        "README_RESULT.md",
    ],
}


NO_LEAK_PATTERNS = (".log", ".db", ".sqlite", ".sqlite-shm", ".sqlite-wal", ".sqlite-journal", ".tmp")
NO_LEAK_NAME_PARTS = ("gateway", "backend", "_tmp")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def check_no_nan(rows: list[dict[str, str]], label: str, errors: list[str]) -> None:
    for idx, row in enumerate(rows, start=1):
        for key, value in row.items():
            text = str(value).strip().lower()
            if text in {"nan", "inf", "-inf"}:
                errors.append(f"{label}:nan_or_inf:{idx}:{key}")
                continue
            if text == "":
                continue
            try:
                val = float(text)
            except ValueError:
                continue
            if math.isnan(val) or math.isinf(val):
                errors.append(f"{label}:nan_or_inf:{idx}:{key}")


def count_true(rows: list[dict[str, str]], field: str, filters: dict[str, str] | None = None) -> tuple[int, int]:
    filters = filters or {}
    total = 0
    hit = 0
    for row in rows:
        if not all(str(row.get(k, "")) == str(v) for k, v in filters.items()):
            continue
        total += 1
        if truthy(row.get(field, "")):
            hit += 1
    return hit, total


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    for path in REQUIRED_INDEX_FILES:
        if not path.exists():
            errors.append(f"missing_index_file:{path.relative_to(REPO_ROOT)}")

    validations: dict[str, dict[str, Any]] = {}
    for artifact, files in REQUIRED_ARTIFACTS.items():
        artifact_dir = ARTIFACT_ROOT / artifact
        if not artifact_dir.exists():
            errors.append(f"missing_artifact:{artifact}")
            continue
        for rel in files:
            path = artifact_dir / rel
            if not path.exists():
                errors.append(f"missing_file:{artifact}:{rel}")
        validation_path = artifact_dir / "validation.json"
        if validation_path.exists():
            payload = read_json(validation_path)
            validations[artifact] = payload
            if payload.get("errors"):
                errors.append(f"validation_errors:{artifact}:{payload.get('errors')}")

    # New E2E sanity artifact checks.
    e2e = validations.get("business_workflow_e2e_perf_sanity_v1", {})
    if e2e:
        expected = {
            "parameter_grid_complete": True,
            "cell_repeat_counts_complete": True,
            "http_services_started": True,
            "mcp_tools_called_http_services": True,
            "sqlite_side_effects_present": True,
            "duplicate_side_effect_zero": True,
            "db_final_state_consistent": True,
            "latency_metrics_finite": True,
        }
        for key, expected_value in expected.items():
            if e2e.get(key) is not expected_value:
                errors.append(f"e2e_perf_validation:{key}={e2e.get(key)}")
        if int(e2e.get("row_count_summary", 0)) != 18:
            errors.append(f"e2e_perf_row_count_summary:{e2e.get('row_count_summary')}")
        if int(e2e.get("agg_row_count", 0)) != 6:
            errors.append(f"e2e_perf_agg_row_count:{e2e.get('agg_row_count')}")
        if int(e2e.get("deterministic_recovery_probe_pass_count", 0)) < 1:
            errors.append("e2e_perf_recovery_probe_no_pass")
        if int(e2e.get("deterministic_recovery_probe_failed_count", 0)) > 0:
            warnings.append(
                "business_workflow_e2e_perf_sanity_v1 has recovery-probe warning; allowed only as path-availability sanity"
            )

    # Clean deterministic CloudLab checks.
    cloudlab = validations.get("cloudlab_business_workflow_distributed_deterministic_v1", {})
    if cloudlab:
        required_true = [
            "deterministic_only",
            "cloudlab_preflight_passed",
            "using_experiment_network_ips",
            "control_network_not_used_for_experiment_traffic",
            "redis_resume_recovered_all",
            "redis_state_miss_zero",
            "redis_post_resume_success_all",
            "redis_replayed_completed_side_effect_zero",
            "memory_cross_gateway_state_miss_all",
            "memory_resume_recovered_zero",
            "duplicate_side_effect_zero",
            "db_integrity_check_passed",
        ]
        for key in required_true:
            if cloudlab.get(key) is not True:
                errors.append(f"cloudlab_det_validation:{key}={cloudlab.get(key)}")
        if cloudlab.get("random_workload_included") is not False:
            errors.append(f"cloudlab_det_random_workload_included:{cloudlab.get('random_workload_included')}")
        if int(cloudlab.get("deterministic_cross_gateway_probe_count", 0)) != 30:
            errors.append(f"cloudlab_det_probe_count:{cloudlab.get('deterministic_cross_gateway_probe_count')}")

    # Cross-check probe CSVs directly.
    e2e_summary_path = ARTIFACT_ROOT / "business_workflow_e2e_perf_sanity_v1" / "business_workflow_e2e_perf_sanity_summary.csv"
    if e2e_summary_path.exists():
        e2e_rows = read_csv(e2e_summary_path)
        if len(e2e_rows) != 18:
            errors.append(f"e2e_perf_summary_csv_rows:{len(e2e_rows)}")
        check_no_nan(e2e_rows, "e2e_perf_summary", errors)

    cloud_recovery_path = (
        ARTIFACT_ROOT
        / "cloudlab_business_workflow_distributed_deterministic_v1"
        / "cloudlab_business_workflow_distributed_deterministic_recovery_probe.csv"
    )
    if cloud_recovery_path.exists():
        rows = read_csv(cloud_recovery_path)
        if len(rows) != 30:
            errors.append(f"cloudlab_det_recovery_csv_rows:{len(rows)}")
        redis_recovered, redis_total = count_true(rows, "resume_recovered", {"store": "redis"})
        redis_state_miss, _ = count_true(rows, "state_miss", {"store": "redis"})
        memory_state_miss, memory_total = count_true(rows, "state_miss", {"store": "memory"})
        replayed = sum(int(float(row.get("replayed_completed_side_effects") or 0)) for row in rows)
        if redis_recovered != redis_total or redis_total != 15:
            errors.append(f"cloudlab_det_redis_recovered:{redis_recovered}/{redis_total}")
        if redis_state_miss != 0:
            errors.append(f"cloudlab_det_redis_state_miss:{redis_state_miss}")
        if memory_state_miss != memory_total or memory_total != 15:
            errors.append(f"cloudlab_det_memory_state_miss:{memory_state_miss}/{memory_total}")
        if replayed != 0:
            errors.append(f"cloudlab_det_replayed_completed_side_effects:{replayed}")
        check_no_nan(rows, "cloudlab_det_recovery", errors)

    req_val = validations.get("request_level_baseline_v1", {})
    if req_val:
        required_true = [
            "parameter_grid_complete",
            "cell_repeat_counts_complete",
            "request_level_baseline_present",
            "session_progress_unavailable_to_baseline",
            "recovery_disabled_for_baseline",
            "continuation_value_disabled_for_baseline",
            "all_client_rc_zero",
            "all_client_timed_out_zero",
            "all_error_empty_or_zero",
            "duplicate_side_effect_zero",
            "adaptive_comparison_available",
            "no_capacity_comparison_available",
        ]
        for key in required_true:
            if req_val.get(key) is not True:
                errors.append(f"request_baseline_validation:{key}={req_val.get(key)}")
        if int(req_val.get("row_count_summary", 0)) != 20:
            errors.append(f"request_baseline_row_count_summary:{req_val.get('row_count_summary')}")
        if int(req_val.get("agg_row_count", 0)) != 4:
            errors.append(f"request_baseline_agg_row_count:{req_val.get('agg_row_count')}")

    req_summary_path = ARTIFACT_ROOT / "request_level_baseline_v1" / "request_level_baseline_summary.csv"
    if req_summary_path.exists():
        req_rows = read_csv(req_summary_path)
        if len(req_rows) != 20:
            errors.append(f"request_baseline_summary_csv_rows:{len(req_rows)}")
        high_load_req = sum(
            int(float(row.get("request_level_rejected") or 0))
            for row in req_rows
            if int(row.get("concurrency", 0)) == 100 and float(row.get("failure_rate", 0)) == 0.2
        )
        if high_load_req <= 0:
            warnings.append("request_level_baseline high-load request_level_rejected is not positive")
        check_no_nan(req_rows, "request_baseline_summary", errors)

    idem_val = validations.get("idempotency_only_retry_baseline_v1", {})
    if idem_val:
        required_true = [
            "parameter_grid_complete",
            "cell_repeat_counts_complete",
            "http_services_started",
            "mcp_tools_called_http_services",
            "sqlite_side_effects_present",
            "sqlite_wal_enabled",
            "db_integrity_check_passed",
            "all_client_rc_zero",
            "all_client_timed_out_zero",
            "all_unexpected_error_empty_or_zero",
            "duplicate_side_effect_zero",
            "idempotency_only_present",
            "idempotency_only_recovery_disabled",
            "idempotency_only_resume_recovered_zero",
            "idempotency_only_avoided_replay_zero",
            "recovery_variant_present",
            "recovery_resume_recovered_positive",
            "recovery_avoided_replay_steps_positive",
            "recovery_post_resume_success_positive",
            "db_final_state_consistent",
            "db_no_duplicate_idempotency_keys",
            "db_no_duplicate_side_effect_rows",
        ]
        for key in required_true:
            if idem_val.get(key) is not True:
                errors.append(f"idempotency_baseline_validation:{key}={idem_val.get(key)}")
        if int(idem_val.get("row_count_summary", 0)) != 12:
            errors.append(f"idempotency_baseline_row_count_summary:{idem_val.get('row_count_summary')}")
        if int(idem_val.get("agg_row_count", 0)) != 4:
            errors.append(f"idempotency_baseline_agg_row_count:{idem_val.get('agg_row_count')}")

    idem_summary_path = ARTIFACT_ROOT / "idempotency_only_retry_baseline_v1" / "idempotency_only_retry_baseline_summary.csv"
    if idem_summary_path.exists():
        idem_rows = read_csv(idem_summary_path)
        if len(idem_rows) != 12:
            errors.append(f"idempotency_baseline_summary_csv_rows:{len(idem_rows)}")
        check_no_nan(idem_rows, "idempotency_baseline_summary", errors)

    # Statistical summary v2 checks.
    stat_val = validations.get("statistical_summary_v2", {})
    if stat_val:
        generated = set(stat_val.get("generated_from_artifacts", []))
        for artifact in [
            "business_workflow_e2e_perf_sanity_v1",
            "cloudlab_business_workflow_distributed_deterministic_v1",
        ]:
            if artifact not in generated:
                errors.append(f"statistical_summary_missing_generated_artifact:{artifact}")
        if not stat_val.get("source_validation_errors_empty", False):
            errors.append("statistical_summary_source_validation_errors_not_empty")
        if not stat_val.get("no_nan_or_inf", False):
            errors.append("statistical_summary_nan_or_inf")

    stat_v3 = validations.get("statistical_summary_v3", {})
    if stat_v3:
        generated = set(stat_v3.get("generated_from_artifacts", []))
        for artifact in [
            "request_level_baseline_v1",
            "business_workflow_e2e_perf_sanity_v1",
            "cloudlab_business_workflow_distributed_deterministic_v1",
        ]:
            if artifact not in generated:
                errors.append(f"statistical_summary_v3_missing_generated_artifact:{artifact}")
        if not stat_v3.get("source_validation_errors_empty", False):
            errors.append("statistical_summary_v3_source_validation_errors_not_empty")
        if not stat_v3.get("no_nan_or_inf", False):
            errors.append("statistical_summary_v3_nan_or_inf")

    stat_v4 = validations.get("statistical_summary_v4", {})
    if stat_v4:
        generated = set(stat_v4.get("generated_from_artifacts", []))
        for artifact in [
            "request_level_baseline_v1",
            "idempotency_only_retry_baseline_v1",
            "business_workflow_e2e_perf_sanity_v1",
            "cloudlab_business_workflow_distributed_deterministic_v1",
        ]:
            if artifact not in generated:
                errors.append(f"statistical_summary_v4_missing_generated_artifact:{artifact}")
        if not stat_v4.get("source_validation_errors_empty", False):
            errors.append("statistical_summary_v4_source_validation_errors_not_empty")
        if not stat_v4.get("no_nan_or_inf", False):
            errors.append("statistical_summary_v4_nan_or_inf")

    claim_path = ARTIFACT_ROOT / "statistical_summary_v2" / "claim_summary.csv"
    if claim_path.exists():
        claims = read_csv(claim_path)
        claim_ids = {row.get("claim_id") for row in claims}
        claims_by_id = {row.get("claim_id"): row for row in claims}
        for claim_id in ["JSS-C15", "JSS-C16"]:
            if claim_id not in claim_ids:
                errors.append(f"missing_claim:{claim_id}")
        claim_sources = {row.get("source_artifact") for row in claims}
        for artifact in [
            "business_workflow_e2e_perf_sanity_v1",
            "cloudlab_business_workflow_distributed_deterministic_v1",
        ]:
            if artifact not in claim_sources:
                errors.append(f"missing_claim_source:{artifact}")
        c13 = claims_by_id.get("JSS-C13", {})
        if c13 and c13.get("support_level") == "strong_correctness":
            errors.append("claim_C13_not_downgraded_to_supporting")
        c16 = claims_by_id.get("JSS-C16", {})
        if c16:
            if c16.get("source_artifact") != "cloudlab_business_workflow_distributed_deterministic_v1":
                errors.append(f"claim_C16_wrong_source:{c16.get('source_artifact')}")
            if c16.get("support_level") != "strong_correctness":
                errors.append(f"claim_C16_wrong_support:{c16.get('support_level')}")
            if "production Redis HA" not in c16.get("not_allowed", ""):
                errors.append("claim_C16_missing_ha_boundary")

    claim_v3_path = ARTIFACT_ROOT / "statistical_summary_v3" / "claim_summary.csv"
    if claim_v3_path.exists():
        claims = read_csv(claim_v3_path)
        claim_ids = {row.get("claim_id") for row in claims}
        claims_by_id = {row.get("claim_id"): row for row in claims}
        if "JSS-C17" not in claim_ids:
            errors.append("missing_claim:JSS-C17")
        c17 = claims_by_id.get("JSS-C17", {})
        if c17:
            if c17.get("source_artifact") != "request_level_baseline_v1":
                errors.append(f"claim_C17_wrong_source:{c17.get('source_artifact')}")
            if "session prefix waste" not in c17.get("allowed_use", "") and "session-prefix waste" not in c17.get("allowed_use", ""):
                errors.append("claim_C17_missing_allowed_use_boundary")

    claim_v4_path = ARTIFACT_ROOT / "statistical_summary_v4" / "claim_summary.csv"
    if claim_v4_path.exists():
        claims = read_csv(claim_v4_path)
        claim_ids = {row.get("claim_id") for row in claims}
        claims_by_id = {row.get("claim_id"): row for row in claims}
        if "JSS-C18" not in claim_ids:
            errors.append("missing_claim:JSS-C18")
        c18 = claims_by_id.get("JSS-C18", {})
        if c18:
            if c18.get("source_artifact") != "idempotency_only_retry_baseline_v1":
                errors.append(f"claim_C18_wrong_source:{c18.get('source_artifact')}")
            if "idempotent retry" not in c18.get("allowed_use", "").lower() or "checkpoint recovery" not in c18.get("allowed_use", "").lower():
                errors.append("claim_C18_missing_allowed_use_boundary")

    for artifact in ["business_workflow_e2e_perf_sanity_v1", "cloudlab_business_workflow_distributed_deterministic_v1"]:
        artifact_dir = ARTIFACT_ROOT / artifact
        if not artifact_dir.exists():
            continue
        for path in artifact_dir.rglob("*"):
            if not path.is_file():
                continue
            lowered = path.name.lower()
            if lowered == "readme_result.md":
                continue
            if lowered.endswith(NO_LEAK_PATTERNS) or any(part in lowered for part in NO_LEAK_NAME_PARTS):
                errors.append(f"leaked_runtime_file:{artifact}:{path.name}")

    result = {
        "validator": "validate_jss_artifacts",
        "required_artifact_count": len(REQUIRED_ARTIFACTS),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
