#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "results"
OUTPUT_DIR = REPO_ROOT / "artifact_results" / "throughput_latency_summary_v1"

REQUIRED_SOURCE_COLUMNS = [
    "gateway",
    "run_idx",
    "sweep_key",
    "sweep_val",
    "raw_goodput_s",
    "effective_goodput_s",
    "success",
    "rejected_s0",
    "cascade_failed",
    "p95_ms",
    "e2e_p95_ms",
    "error",
]

SUMMARY_COLUMNS = [
    "experiment",
    "gateway",
    "run_idx",
    "sweep_key",
    "sweep_val",
    "raw_goodput_s",
    "effective_goodput_s",
    "success",
    "rejected_s0",
    "cascade_failed",
    "p95_ms",
    "e2e_p95_ms",
    "source_csv",
]

AGG_COLUMNS = [
    "experiment",
    "gateway",
    "sweep_key",
    "sweep_val",
    "runs",
    "raw_goodput_s_mean",
    "effective_goodput_s_mean",
    "success_mean",
    "rejected_s0_mean",
    "cascade_failed_mean",
    "p95_ms_mean",
    "e2e_p95_ms_mean",
]

EXPERIMENT_CANDIDATES = [
    (
        "Exp1_Core",
        [
            RESULTS_ROOT / "exp1_core" / "exp1_core_summary.csv",
            RESULTS_ROOT / "exp1_core" / "*summary*.csv",
        ],
    ),
    (
        "Exp5_ScaleConc",
        [
            RESULTS_ROOT / "exp5_scaleconc" / "exp5_scaleconc_summary.csv",
            RESULTS_ROOT / "exp5_scaleconc" / "*summary*.csv",
        ],
    ),
    (
        "Exp6_ScaleConcReact",
        [
            RESULTS_ROOT / "exp6_scaleconcreact" / "exp6_scaleconcreact_summary.csv",
            RESULTS_ROOT / "exp6_scaleconcreact" / "*summary*.csv",
        ],
    ),
    (
        "Exp10_Adversarial",
        [
            RESULTS_ROOT / "exp10_adversarial" / "exp10_adversarial_summary.csv",
            RESULTS_ROOT / "exp10_adversarial" / "*summary*.csv",
        ],
    ),
]

NUMERIC_COLUMNS = [
    "raw_goodput_s",
    "effective_goodput_s",
    "success",
    "rejected_s0",
    "cascade_failed",
    "p95_ms",
    "e2e_p95_ms",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_source_file(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
        matches = sorted(candidate.parent.glob(candidate.name))
        if matches:
            return matches[-1]
    return None


def parse_float(value: str, field: str, errors: list[str], context: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"non-numeric {field} in {context}: {value!r}")
        return 0.0


def normalize_sweep_key(value: str) -> str:
    value = (value or "").strip()
    if value == "concurrency":
        return "conc"
    return value


def read_rows(experiment: str, source: Path, errors: list[str]) -> list[dict[str, str]]:
    with source.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing = [col for col in REQUIRED_SOURCE_COLUMNS if col not in fieldnames]
        if missing:
            errors.append(
                f"{source.as_posix()} missing required columns: {', '.join(missing)}"
            )
            return []

        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            if not row.get("gateway", "").strip():
                errors.append(f"{source.as_posix()} row {index} has empty gateway")
                continue
            for col in NUMERIC_COLUMNS:
                parse_float(row.get(col, ""), col, errors, f"{source.as_posix()} row {index}")
            rows.append(
                {
                    "experiment": experiment,
                    "gateway": row["gateway"].strip(),
                    "run_idx": (row.get("run_idx") or "").strip(),
                    "sweep_key": normalize_sweep_key(row.get("sweep_key", "")),
                    "sweep_val": (row.get("sweep_val") or "").strip(),
                    "raw_goodput_s": row["raw_goodput_s"].strip(),
                    "effective_goodput_s": row["effective_goodput_s"].strip(),
                    "success": row["success"].strip(),
                    "rejected_s0": row["rejected_s0"].strip(),
                    "cascade_failed": row["cascade_failed"].strip(),
                    "p95_ms": row["p95_ms"].strip(),
                    "e2e_p95_ms": row["e2e_p95_ms"].strip(),
                    "source_csv": source.relative_to(REPO_ROOT).as_posix(),
                    "_source_error": (row.get("error") or "").strip(),
                }
            )
        return rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_float(value: float) -> str:
    text = f"{value:.6f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def build_artifact() -> dict[str, object]:
    errors: list[str] = []
    source_files: list[Path] = []
    summary_rows: list[dict[str, str]] = []
    source_sha256: dict[str, str] = {}
    included_experiments: list[str] = []

    for experiment, candidates in EXPERIMENT_CANDIDATES:
        source = find_source_file(candidates)
        if source is None:
            errors.append(f"missing source for {experiment}")
            continue
        source_files.append(source)
        source_sha256[source.relative_to(REPO_ROOT).as_posix()] = sha256_file(source)
        included_experiments.append(experiment)
        summary_rows.extend(read_rows(experiment, source, errors))

    required_columns_present = not any("missing required columns" in err for err in errors)

    error_rows = [row for row in summary_rows if row["_source_error"]]
    if error_rows:
        errors.append(f"{len(error_rows)} source rows have non-empty error fields")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    clean_summary_rows = []
    for row in summary_rows:
        clean_summary_rows.append({key: row[key] for key in SUMMARY_COLUMNS})
    write_csv(OUTPUT_DIR / "throughput_latency_summary.csv", SUMMARY_COLUMNS, clean_summary_rows)

    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in summary_rows:
        grouped[(row["experiment"], row["gateway"], row["sweep_key"], row["sweep_val"])].append(row)

    agg_rows: list[dict[str, str]] = []
    for key in sorted(grouped.keys()):
        rows = grouped[key]
        experiment, gateway, sweep_key, sweep_val = key
        agg_rows.append(
            {
                "experiment": experiment,
                "gateway": gateway,
                "sweep_key": sweep_key,
                "sweep_val": sweep_val,
                "runs": str(len(rows)),
                "raw_goodput_s_mean": format_float(
                    sum(float(row["raw_goodput_s"]) for row in rows) / len(rows)
                ),
                "effective_goodput_s_mean": format_float(
                    sum(float(row["effective_goodput_s"]) for row in rows) / len(rows)
                ),
                "success_mean": format_float(
                    sum(float(row["success"]) for row in rows) / len(rows)
                ),
                "rejected_s0_mean": format_float(
                    sum(float(row["rejected_s0"]) for row in rows) / len(rows)
                ),
                "cascade_failed_mean": format_float(
                    sum(float(row["cascade_failed"]) for row in rows) / len(rows)
                ),
                "p95_ms_mean": format_float(
                    sum(float(row["p95_ms"]) for row in rows) / len(rows)
                ),
                "e2e_p95_ms_mean": format_float(
                    sum(float(row["e2e_p95_ms"]) for row in rows) / len(rows)
                ),
            }
        )
    write_csv(OUTPUT_DIR / "throughput_latency_agg.csv", AGG_COLUMNS, agg_rows)

    validation = {
        "artifact": "throughput_latency_summary_v1",
        "included_experiments": included_experiments,
        "source_files": [path.relative_to(REPO_ROOT).as_posix() for path in source_files],
        "source_sha256": source_sha256,
        "row_count": len(clean_summary_rows),
        "agg_row_count": len(agg_rows),
        "required_columns_present": required_columns_present,
        "errors": errors,
    }
    with (OUTPUT_DIR / "validation.json").open("w", encoding="utf-8") as f:
        json.dump(validation, f, indent=2)
        f.write("\n")

    readme = """# Throughput and Latency Summary Evidence

This artifact summarizes existing throughput and latency evidence. It does not
introduce new experiments.

This bundle contains:

- `throughput_latency_summary.csv`
- `throughput_latency_agg.csv`
- `validation.json`

It is a lightweight artifact pack built from existing summary CSVs under
`results/`. It intentionally omits full run directories, raw per-step traces,
logs, `.env`, `.venv`, `.gocache`, and the full `results/` tree.

## Included Experiments

- `Exp1_Core`
- `Exp5_ScaleConc`
- `Exp6_ScaleConcReact`
- `Exp10_Adversarial`

## Metric Interpretation

We report both raw throughput and effective goodput. Raw throughput measures
admitted/processed work rate, while effective goodput discounts cascaded
failures and wasted progress. The main governance claim uses effective goodput
because the objective is useful completed work, not merely higher admission
rate.

- `raw_goodput_s`: raw throughput/goodput rate
- `effective_goodput_s`: useful completed work rate after discounting
  cascaded failures or wasted progress

## Scope Boundary

This is a summary-only artifact derived from existing CSV outputs. It does not
rerun Exp1 / Exp5 / Exp6 / Exp10, and it should not be read as a separate new
throughput experiment.
"""
    (OUTPUT_DIR / "README_RESULT.md").write_text(readme, encoding="utf-8")

    return validation


def main() -> int:
    validation = build_artifact()
    print(json.dumps(validation, indent=2))
    return 0 if not validation["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
