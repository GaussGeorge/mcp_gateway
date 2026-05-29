#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = REPO_ROOT / "artifact_results"
OUT_DIR = ARTIFACT_ROOT / "statistical_summary_v1"

SEED_DEFAULT = 20260528
BOOTSTRAP_ITERS_DEFAULT = 10000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build statistical summary artifact from existing artifact CSVs")
    parser.add_argument("--bootstrap-iters", type=int, default=BOOTSTRAP_ITERS_DEFAULT)
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    return parser.parse_args()


def read_csv_utf8sig(path: Path) -> list[dict[str, str]]:
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
    try:
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        val = float(text)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except (TypeError, ValueError):
        return None


def fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def bootstrap_mean_ci(values: list[float], rng: random.Random, iters: int) -> tuple[float | None, float | None]:
    n = len(values)
    if n < 3:
        return None, None
    means: list[float] = []
    for _ in range(iters):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(mean(sample))
    means.sort()
    lo = means[int(0.025 * (iters - 1))]
    hi = means[int(0.975 * (iters - 1))]
    return lo, hi


def bootstrap_delta_ci(
    values_a: list[float], values_b: list[float], rng: random.Random, iters: int
) -> tuple[float | None, float | None]:
    n_a = len(values_a)
    n_b = len(values_b)
    if n_a < 3 or n_b < 3:
        return None, None
    deltas: list[float] = []
    for _ in range(iters):
        sample_a = [values_a[rng.randrange(n_a)] for _ in range(n_a)]
        sample_b = [values_b[rng.randrange(n_b)] for _ in range(n_b)]
        deltas.append(mean(sample_a) - mean(sample_b))
    deltas.sort()
    lo = deltas[int(0.025 * (iters - 1))]
    hi = deltas[int(0.975 * (iters - 1))]
    return lo, hi


def group_key(row: dict[str, str], fields: list[str]) -> str:
    parts = []
    for f in fields:
        parts.append(f"{f}={row.get(f, '')}")
    return "|".join(parts)


def collect_stat_rows(
    *,
    experiment: str,
    rows: list[dict[str, str]],
    group_fields: list[str],
    metrics: list[str],
    source_csv: str,
    rng: random.Random,
    bootstrap_iters: int,
    diagnostic: bool = False,
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(f, "") for f in group_fields)].append(row)

    out: list[dict[str, str]] = []
    for key, bucket in sorted(grouped.items()):
        rep = {group_fields[idx]: key[idx] for idx in range(len(group_fields))}
        gkey = group_key(rep, group_fields)
        for metric in metrics:
            vals: list[float] = []
            for row in bucket:
                v = to_float(row.get(metric))
                if v is not None:
                    vals.append(v)
            n = len(vals)
            descriptive_only = n < 3
            m = mean(vals) if vals else None
            s = stdev(vals) if n >= 2 else 0.0 if n == 1 else None
            ci_low, ci_high = bootstrap_mean_ci(vals, rng, bootstrap_iters)
            if descriptive_only:
                ci_low, ci_high = None, None
            out.append(
                {
                    "experiment": experiment,
                    "group_key": gkey,
                    "metric": metric,
                    "n": str(n),
                    "mean": fmt_num(m),
                    "std": fmt_num(s),
                    "ci95_low": fmt_num(ci_low),
                    "ci95_high": fmt_num(ci_high),
                    "source_csv": source_csv,
                    "descriptive_only": "true" if descriptive_only else "false",
                    "evidence_role": "diagnostic" if diagnostic else "core_or_boundary",
                }
            )
    return out


def ci_crosses_zero(ci_low: float | None, ci_high: float | None) -> bool:
    if ci_low is None or ci_high is None:
        return True
    return ci_low <= 0.0 <= ci_high


def interpret_effect(
    metric: str,
    delta: float,
    ci_low: float | None,
    ci_high: float | None,
    descriptive_only: bool,
) -> str:
    if descriptive_only:
        return "descriptive_only"

    higher_better = {
        "success",
        "success_rate",
        "effective_goodput_s",
        "raw_goodput_s",
        "recovery_success",
        "amendment_success",
        "commitment_issued",
    }

    if ci_crosses_zero(ci_low, ci_high):
        return "mixed_or_boundary"

    if metric in higher_better:
        if delta > 0:
            return "positive_for_plangate"
        if delta < 0:
            return "negative_for_plangate"
        return "mixed_or_boundary"

    # lower is better
    if delta < 0:
        return "positive_for_plangate"
    if delta > 0:
        return "negative_for_plangate"
    return "mixed_or_boundary"


def find_group_values(rows: list[dict[str, str]], filters: dict[str, str], metric: str) -> list[float]:
    vals: list[float] = []
    for row in rows:
        ok = True
        for k, v in filters.items():
            if str(row.get(k, "")) != str(v):
                ok = False
                break
        if not ok:
            continue
        x = to_float(row.get(metric))
        if x is not None:
            vals.append(x)
    return vals


def effect_row(
    *,
    experiment: str,
    comparison: str,
    metric: str,
    group_a: str,
    group_b: str,
    values_a: list[float],
    values_b: list[float],
    rng: random.Random,
    bootstrap_iters: int,
) -> dict[str, str]:
    n_a = len(values_a)
    n_b = len(values_b)
    descriptive_only = n_a < 3 or n_b < 3
    mean_a = mean(values_a) if values_a else None
    mean_b = mean(values_b) if values_b else None
    delta = (mean_a - mean_b) if (mean_a is not None and mean_b is not None) else None

    if descriptive_only:
        ci_low = None
        ci_high = None
    else:
        ci_low, ci_high = bootstrap_delta_ci(values_a, values_b, rng, bootstrap_iters)

    rel = None
    if delta is not None and mean_b is not None and mean_b != 0:
        rel = (delta / mean_b) * 100.0

    interpretation = (
        interpret_effect(metric, delta or 0.0, ci_low, ci_high, descriptive_only)
        if delta is not None
        else "descriptive_only"
    )

    return {
        "experiment": experiment,
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
        "interpretation": interpretation,
    }


def build_claim_rows(effect_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    idx = {(r["experiment"], r["comparison"], r["metric"]): r for r in effect_rows}
    claims: list[dict[str, str]] = []

    def support_of(row: dict[str, str], default: str = "boundary") -> str:
        interp = row["interpretation"]
        if interp == "descriptive_only":
            return "moderate"
        if interp == "mixed_or_boundary":
            return "boundary"
        # CI excludes zero due to interpretation rule
        return "strong"

    key = ("exp1_core", "plangate_full_vs_ng", "effective_goodput_s")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C1",
                "source_artifact": "mock_regression_p4_refresh_v1",
                "metric": "effective_goodput_s",
                "result_text": f"Exp1 core: PlanGate vs NG delta={r['delta']} GP/s, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": support_of(r),
                "caveat": "Mock workload evidence; not a production-trace causal claim.",
            }
        )

    key = ("exp1_core", "plangate_full_vs_ng", "cascade_failed")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C2",
                "source_artifact": "mock_regression_p4_refresh_v1",
                "metric": "cascade_failed",
                "result_text": f"Exp1 core: PlanGate vs NG cascade delta={r['delta']}, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": support_of(r),
                "caveat": "Lower-is-better metric; scoped to controlled mock load.",
            }
        )

    key = ("p3_mechanism", "plangate_full_vs_wo_recovery", "recovery_success")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C3",
                "source_artifact": "p3_failure_mechanism_ablation_v1",
                "metric": "recovery_success",
                "result_text": f"P3 mechanism: Full vs wo_recovery recovery_success delta={r['delta']}, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": support_of(r),
                "caveat": "Failure-specific controlled workload; not real-LLM semantic recovery.",
            }
        )

    key = ("p3_grid", "plangate_full_vs_wo_recovery_f0.3_a0.2", "success")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C4",
                "source_artifact": "p3_failure_amendment_grid_v1",
                "metric": "success",
                "result_text": f"P3 grid (f=0.3,a=0.2): Full vs wo_recovery success delta={r['delta']}, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": support_of(r),
                "caveat": "Grid cell evidence; effect size can vary by failure/amendment regime.",
            }
        )

    key = ("selfhosted_profile_sweep", "plangate_relaxed_vs_ng_c20", "success_rate")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C5",
                "source_artifact": "selfhosted_vllm_profile_sweep_v1",
                "metric": "success_rate",
                "result_text": f"vLLM profile C20: PlanGate(tuned) vs NG success_rate delta={r['delta']}pp, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": support_of(r),
                "caveat": "Boundary evidence under saturated local-vLLM backend.",
            }
        )

    key = ("selfhosted_profile_sweep", "plangate_relaxed_vs_rajomon_c8", "success_rate")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C6",
                "source_artifact": "selfhosted_vllm_profile_sweep_v1",
                "metric": "success_rate",
                "result_text": f"vLLM profile C8: PlanGate(tuned) vs Rajomon success_rate delta={r['delta']}pp, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": "boundary",
                "caveat": "Low-contention boundary where PlanGate is not always best.",
            }
        )

    claims.append(
        {
            "claim_id": "C7",
            "source_artifact": "mock_regression_p4_refresh_v1/exp8_discountablation_summary.csv",
            "metric": "exp8_discount_diagnostic",
            "result_text": "Exp8 discount-family statistics are included as diagnostic only.",
            "support_level": "diagnostic",
            "caveat": "Do not use Exp8 as a standalone strong mechanism claim.",
        }
    )

    key = ("cloudlab_random_redis_memory", "redis_vs_memory", "state_miss")
    if key in idx:
        r = idx[key]
        claims.append(
            {
                "claim_id": "C8",
                "source_artifact": "cloudlab_random_redis_memory_v1",
                "metric": "state_miss",
                "result_text": f"CloudLab random routing: Redis vs memory state_miss delta={r['delta']}, CI[{r['ci95_low_delta']}, {r['ci95_high_delta']}].",
                "support_level": support_of(r),
                "caveat": "Shared-state lookup correctness evidence only; not a production Redis HA claim.",
            }
        )

    return claims


def build_readme(cloudlab_included: bool) -> str:
    return "\n".join(
        [
            "# Statistical Summary Artifact",
            "",
            "This bundle is a statistical summary artifact computed from existing artifact CSV files.",
            "It does not run new experiments and does not modify mechanism/runtime code.",
            "",
            "## Method",
            "",
            "- Encoding: all CSV inputs are read with utf-8-sig (PowerShell BOM compatible).",
            f"- Bootstrap seed: {SEED_DEFAULT}.",
            "- Bootstrap CI: percentile 95% bootstrap CI on means and pairwise mean deltas.",
            "- Role of CI: uncertainty description only, not strict causal significance proof.",
            "",
            "## Evidence Boundary",
            "",
            "- Repeats < 3 are marked descriptive_only=true and are not assigned strong CI evidence.",
            "- Provider-backed GLM/DeepSeek evidence (if added later) should be treated as descriptive/boundary,",
            "  not strong statistical claim evidence.",
            f"- cloudlab_included={str(cloudlab_included).lower()}.",
            "  When true, the bundle includes the lightweight CloudLab Redis-vs-memory random-routing artifact",
            "  as shared-state correctness / diagnostic-control evidence rather than throughput-dominance evidence.",
            "",
            "## Outputs",
            "",
            "- statistical_summary.csv",
            "- effect_size_summary.csv",
            "- claim_summary.csv",
            "- validation.json",
            "- README_RESULT.md",
        ]
    ) + "\n"


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)
    errors: list[str] = []

    source_paths = {
        "exp1_core_summary": ARTIFACT_ROOT / "mock_regression_p4_refresh_v1" / "exp1_core_summary.csv",
        "exp4_ablation_summary": ARTIFACT_ROOT / "mock_regression_p4_refresh_v1" / "exp4_ablation_summary.csv",
        "exp8_discountablation_summary": ARTIFACT_ROOT / "mock_regression_p4_refresh_v1" / "exp8_discountablation_summary.csv",
        "p3_failure_mechanism_summary": ARTIFACT_ROOT
        / "p3_failure_mechanism_ablation_v1"
        / "p3_failure_mechanism_ablation_summary.csv",
        "p3_failure_mechanism_agg": ARTIFACT_ROOT
        / "p3_failure_mechanism_ablation_v1"
        / "p3_failure_mechanism_ablation_agg.csv",
        "p3_failure_amendment_grid_summary": ARTIFACT_ROOT
        / "p3_failure_amendment_grid_v1"
        / "p3_failure_amendment_grid_summary.csv",
        "p3_failure_amendment_grid_agg": ARTIFACT_ROOT
        / "p3_failure_amendment_grid_v1"
        / "p3_failure_amendment_grid_agg.csv",
        "selfhosted_vllm_stress_summary": ARTIFACT_ROOT
        / "selfhosted_vllm_stress_c16w8_tuned_5gw_v1"
        / "selfhosted_vllm_stress_summary.csv",
        "selfhosted_vllm_stress_agg": ARTIFACT_ROOT
        / "selfhosted_vllm_stress_c16w8_tuned_5gw_v1"
        / "selfhosted_vllm_stress_agg.csv",
        "selfhosted_vllm_profile_sweep_summary": ARTIFACT_ROOT
        / "selfhosted_vllm_profile_sweep_v1"
        / "selfhosted_vllm_profile_sweep_summary.csv",
        "selfhosted_vllm_profile_sweep_agg": ARTIFACT_ROOT
        / "selfhosted_vllm_profile_sweep_v1"
        / "selfhosted_vllm_profile_sweep_agg.csv",
        "throughput_latency_summary": ARTIFACT_ROOT
        / "throughput_latency_summary_v1"
        / "throughput_latency_summary.csv",
        "throughput_latency_agg": ARTIFACT_ROOT
        / "throughput_latency_summary_v1"
        / "throughput_latency_agg.csv",
        "cloudlab_random_redis_memory_summary": ARTIFACT_ROOT
        / "cloudlab_random_redis_memory_v1"
        / "cloudlab_random_redis_memory_summary.csv",
        "cloudlab_random_redis_memory_agg": ARTIFACT_ROOT
        / "cloudlab_random_redis_memory_v1"
        / "cloudlab_random_redis_memory_agg.csv",
    }

    cloudlab_candidate = ARTIFACT_ROOT / "cloudlab_random_redis_memory_v1"
    cloudlab_included = cloudlab_candidate.exists()

    required_sources_present = True
    for key, path in source_paths.items():
        if not path.exists():
            required_sources_present = False
            errors.append(f"missing_source:{key}:{path}")

    if not required_sources_present:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        validation = {
            "artifact": "statistical_summary_v1",
            "generated_from_artifacts": [str(path) for path in source_paths.values()],
            "row_count_statistical_summary": 0,
            "row_count_effect_size_summary": 0,
            "row_count_claim_summary": 0,
            "required_sources_present": False,
            "cloudlab_included": cloudlab_included,
            "all_source_files_exist": False,
            "no_nan_or_inf": False,
            "errors": errors,
        }
        (OUT_DIR / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    datasets = {key: read_csv_utf8sig(path) for key, path in source_paths.items()}

    stat_rows: list[dict[str, str]] = []

    stat_rows.extend(
        collect_stat_rows(
            experiment="exp1_core",
            rows=datasets["exp1_core_summary"],
            group_fields=["gateway"],
            metrics=["success", "rejected_s0", "cascade_failed", "effective_goodput_s", "p50_ms", "p95_ms"],
            source_csv="artifact_results/mock_regression_p4_refresh_v1/exp1_core_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="exp4_ablation",
            rows=datasets["exp4_ablation_summary"],
            group_fields=["gateway"],
            metrics=["success", "cascade_failed", "effective_goodput_s", "p95_ms"],
            source_csv="artifact_results/mock_regression_p4_refresh_v1/exp4_ablation_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="exp8_discount_diagnostic",
            rows=datasets["exp8_discountablation_summary"],
            group_fields=["gateway"],
            metrics=["success", "cascade_failed", "effective_goodput_s", "p95_ms"],
            source_csv="artifact_results/mock_regression_p4_refresh_v1/exp8_discountablation_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
            diagnostic=True,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="p3_mechanism",
            rows=datasets["p3_failure_mechanism_summary"],
            group_fields=["gateway"],
            metrics=["success", "cascade_failed", "recovery_success", "amendment_success", "commitment_issued"],
            source_csv="artifact_results/p3_failure_mechanism_ablation_v1/p3_failure_mechanism_ablation_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="p3_grid",
            rows=datasets["p3_failure_amendment_grid_summary"],
            group_fields=["failure_rate", "amendment_rate", "gateway"],
            metrics=["success", "cascade_failed", "recovery_success", "amendment_success", "commitment_issued"],
            source_csv="artifact_results/p3_failure_amendment_grid_v1/p3_failure_amendment_grid_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="selfhosted_vllm_stress_tuned_5gw",
            rows=datasets["selfhosted_vllm_stress_summary"],
            group_fields=["gateway"],
            metrics=["success_rate", "abd_total", "all_rejected", "cascade_agents", "p95_ms"],
            source_csv="artifact_results/selfhosted_vllm_stress_c16w8_tuned_5gw_v1/selfhosted_vllm_stress_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="selfhosted_profile_sweep",
            rows=datasets["selfhosted_vllm_profile_sweep_summary"],
            group_fields=["concurrency", "gateway"],
            metrics=["success_rate", "abd_total", "all_rejected", "cascade_agents", "p95_ms"],
            source_csv="artifact_results/selfhosted_vllm_profile_sweep_v1/selfhosted_vllm_profile_sweep_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="throughput_latency",
            rows=datasets["throughput_latency_summary"],
            # Aggregate by experiment+gateway to keep bootstrap runtime tractable.
            group_fields=["experiment", "gateway"],
            metrics=["effective_goodput_s", "raw_goodput_s", "e2e_p95_ms", "cascade_failed", "rejected_s0"],
            source_csv="artifact_results/throughput_latency_summary_v1/throughput_latency_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    stat_rows.extend(
        collect_stat_rows(
            experiment="cloudlab_random_redis_memory",
            rows=datasets["cloudlab_random_redis_memory_summary"],
            group_fields=["store"],
            metrics=["cross_node_sessions", "state_miss", "duplicate_admission", "validation_error_count"],
            source_csv="artifact_results/cloudlab_random_redis_memory_v1/cloudlab_random_redis_memory_summary.csv",
            rng=rng,
            bootstrap_iters=args.bootstrap_iters,
        )
    )

    effect_rows: list[dict[str, str]] = []

    exp1_rows = datasets["exp1_core_summary"]
    for baseline in ["ng", "sbac", "srl"]:
        for metric in ["success", "cascade_failed", "effective_goodput_s"]:
            a = find_group_values(exp1_rows, {"gateway": "plangate_full"}, metric)
            b = find_group_values(exp1_rows, {"gateway": baseline}, metric)
            effect_rows.append(
                effect_row(
                    experiment="exp1_core",
                    comparison=f"plangate_full_vs_{baseline}",
                    metric=metric,
                    group_a="plangate_full",
                    group_b=baseline,
                    values_a=a,
                    values_b=b,
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )
            )

    p3_rows = datasets["p3_failure_mechanism_summary"]
    for baseline in ["wo_commitment", "wo_amendment", "wo_recovery"]:
        for metric in ["success", "cascade_failed", "recovery_success", "amendment_success", "commitment_issued"]:
            a = find_group_values(p3_rows, {"gateway": "plangate_full"}, metric)
            b = find_group_values(p3_rows, {"gateway": baseline}, metric)
            effect_rows.append(
                effect_row(
                    experiment="p3_mechanism",
                    comparison=f"plangate_full_vs_{baseline}",
                    metric=metric,
                    group_a="plangate_full",
                    group_b=baseline,
                    values_a=a,
                    values_b=b,
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )
            )

    # P3 grid comparison at each (failure_rate, amendment_rate)
    grid_rows = datasets["p3_failure_amendment_grid_summary"]
    fr_vals = sorted({row.get("failure_rate", "") for row in grid_rows})
    ar_vals = sorted({row.get("amendment_rate", "") for row in grid_rows})
    for fr in fr_vals:
        for ar in ar_vals:
            for baseline in ["wo_commitment", "wo_amendment", "wo_recovery"]:
                for metric in ["success", "cascade_failed", "recovery_success", "amendment_success", "commitment_issued"]:
                    a = find_group_values(
                        grid_rows,
                        {"failure_rate": fr, "amendment_rate": ar, "gateway": "plangate_full"},
                        metric,
                    )
                    b = find_group_values(
                        grid_rows,
                        {"failure_rate": fr, "amendment_rate": ar, "gateway": baseline},
                        metric,
                    )
                    effect_rows.append(
                        effect_row(
                            experiment="p3_grid",
                            comparison=f"plangate_full_vs_{baseline}_f{fr}_a{ar}",
                            metric=metric,
                            group_a=f"plangate_full|f={fr}|a={ar}",
                            group_b=f"{baseline}|f={fr}|a={ar}",
                            values_a=a,
                            values_b=b,
                            rng=rng,
                            bootstrap_iters=args.bootstrap_iters,
                        )
                    )

    profile_rows = datasets["selfhosted_vllm_profile_sweep_summary"]
    conc_vals = sorted({row.get("concurrency", "") for row in profile_rows}, key=lambda x: int(x))
    for conc in conc_vals:
        for baseline in ["ng", "static", "pp", "rajomon"]:
            for metric in ["success_rate", "all_rejected", "cascade_agents", "abd_total"]:
                a = find_group_values(
                    profile_rows,
                    {"concurrency": conc, "gateway": "plangate_relaxed"},
                    metric,
                )
                b = find_group_values(
                    profile_rows,
                    {"concurrency": conc, "gateway": baseline},
                    metric,
                )
                effect_rows.append(
                    effect_row(
                        experiment="selfhosted_profile_sweep",
                        comparison=f"plangate_relaxed_vs_{baseline}_c{conc}",
                        metric=metric,
                        group_a=f"plangate_relaxed|c={conc}",
                        group_b=f"{baseline}|c={conc}",
                        values_a=a,
                        values_b=b,
                        rng=rng,
                        bootstrap_iters=args.bootstrap_iters,
                    )
                )

    stress_rows = datasets["selfhosted_vllm_stress_summary"]
    for baseline in ["ng", "static", "pp", "rajomon"]:
        for metric in ["success_rate", "all_rejected", "cascade_agents", "abd_total", "p95_ms"]:
            a = find_group_values(stress_rows, {"gateway": "plangate_relaxed"}, metric)
            b = find_group_values(stress_rows, {"gateway": baseline}, metric)
            effect_rows.append(
                effect_row(
                    experiment="selfhosted_vllm_stress_tuned_5gw",
                    comparison=f"plangate_relaxed_vs_{baseline}",
                    metric=metric,
                    group_a="plangate_relaxed",
                    group_b=baseline,
                    values_a=a,
                    values_b=b,
                    rng=rng,
                    bootstrap_iters=args.bootstrap_iters,
                )
            )

    cloudlab_rows = datasets["cloudlab_random_redis_memory_summary"]
    for metric in ["cross_node_sessions", "state_miss", "duplicate_admission", "validation_error_count"]:
        a = find_group_values(cloudlab_rows, {"store": "redis"}, metric)
        b = find_group_values(cloudlab_rows, {"store": "memory"}, metric)
        effect_rows.append(
            effect_row(
                experiment="cloudlab_random_redis_memory",
                comparison="redis_vs_memory",
                metric=metric,
                group_a="redis",
                group_b="memory",
                values_a=a,
                values_b=b,
                rng=rng,
                bootstrap_iters=args.bootstrap_iters,
            )
        )

    claim_rows = build_claim_rows(effect_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    statistical_fields = [
        "experiment",
        "group_key",
        "metric",
        "n",
        "mean",
        "std",
        "ci95_low",
        "ci95_high",
        "source_csv",
        "descriptive_only",
        "evidence_role",
    ]
    effect_fields = [
        "experiment",
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
        "interpretation",
    ]
    claim_fields = [
        "claim_id",
        "source_artifact",
        "metric",
        "result_text",
        "support_level",
        "caveat",
    ]

    write_csv(OUT_DIR / "statistical_summary.csv", stat_rows, statistical_fields)
    write_csv(OUT_DIR / "effect_size_summary.csv", effect_rows, effect_fields)
    write_csv(OUT_DIR / "claim_summary.csv", claim_rows, claim_fields)
    (OUT_DIR / "README_RESULT.md").write_text(build_readme(cloudlab_included), encoding="utf-8")

    no_nan_or_inf = True
    for rows in (stat_rows, effect_rows):
        for row in rows:
            for key, value in row.items():
                if key.startswith("ci") or key in {"mean", "std", "delta", "relative_delta_pct", "mean_a", "mean_b"}:
                    if str(value).strip() == "":
                        continue
                    try:
                        fv = float(str(value))
                    except ValueError:
                        no_nan_or_inf = False
                        errors.append(f"invalid_numeric:{key}:{value}")
                        continue
                    if math.isnan(fv) or math.isinf(fv):
                        no_nan_or_inf = False
                        errors.append(f"nan_or_inf:{key}:{value}")

    validation = {
        "artifact": "statistical_summary_v1",
        "generated_from_artifacts": [
            "artifact_results/mock_regression_p4_refresh_v1",
            "artifact_results/p3_failure_mechanism_ablation_v1",
            "artifact_results/p3_failure_amendment_grid_v1",
            "artifact_results/cloudlab_random_redis_memory_v1",
            "artifact_results/selfhosted_vllm_stress_c16w8_tuned_5gw_v1",
            "artifact_results/selfhosted_vllm_profile_sweep_v1",
            "artifact_results/throughput_latency_summary_v1",
        ],
        "row_count_statistical_summary": len(stat_rows),
        "row_count_effect_size_summary": len(effect_rows),
        "row_count_claim_summary": len(claim_rows),
        "required_sources_present": required_sources_present,
        "cloudlab_included": cloudlab_included,
        "all_source_files_exist": required_sources_present,
        "no_nan_or_inf": no_nan_or_inf,
        "errors": errors,
    }
    (OUT_DIR / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote statistical summary artifact: {OUT_DIR}")
    print(f"statistical_summary rows: {len(stat_rows)}")
    print(f"effect_size_summary rows: {len(effect_rows)}")
    print(f"claim_summary rows: {len(claim_rows)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
