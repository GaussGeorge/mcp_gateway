#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
RUNNER_PATH = SCRIPT_DIR / "run_cloudlab_experiment.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cloudlab_runner = load_module(RUNNER_PATH, "run_cloudlab_experiment_wrapper")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CloudLab random-routing Redis vs memory state-store comparison wrapper."
    )
    parser.add_argument(
        "--inventory",
        default=str(SCRIPT_DIR / "inventory.m510_6.json"),
    )
    parser.add_argument("--profile", default="small", choices=sorted(cloudlab_runner.PROFILE_COUNTS))
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--failure-rate", nargs="+", type=float, default=[0.1, 0.2, 0.3])
    parser.add_argument("--amendment-rate", type=float, default=0.2)
    parser.add_argument("--results-dir", default=os.path.join("results", "cloudlab_random_redis_memory"))
    parser.add_argument("--artifact-dir", default=os.path.join("artifact_results", "cloudlab_random_redis_memory_v1"))
    parser.add_argument("--commitment-secret", default="")
    parser.add_argument("--skip-setup", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def mode_results_dir(root: str, mode: str) -> str:
    return os.path.join(root, mode)


def artifact_dir(root: str) -> Path:
    return Path(root)


def build_mode_command(args: argparse.Namespace, *, mode: str) -> list[str]:
    if mode == "redis":
        plangate_state_store = "redis"
        recovery_store = "redis"
        validation_mode = "correctness"
    elif mode == "memory":
        plangate_state_store = "inmemory"
        recovery_store = "inmemory"
        validation_mode = "stress"
    else:
        raise ValueError(f"unknown mode={mode!r}")

    cmd = [
        sys.executable,
        str(RUNNER_PATH),
        "--inventory",
        str(args.inventory),
        "--profile",
        str(args.profile),
        "--workload",
        "p3",
        "--routing",
        "random",
        "--plangate-state-store",
        plangate_state_store,
        "--recovery-store",
        recovery_store,
        "--sessions",
        str(args.sessions),
        "--concurrency",
        str(args.concurrency),
        "--repeats",
        str(args.repeats),
        "--failure-rate",
        *[str(rate) for rate in args.failure_rate],
        "--amendment-rate",
        str(args.amendment_rate),
        "--validation-mode",
        validation_mode,
        "--results-dir",
        mode_results_dir(args.results_dir, mode),
    ]
    if args.commitment_secret:
        cmd.extend(["--commitment-secret", args.commitment_secret])
    if args.skip_setup:
        cmd.append("--skip-setup")
    if args.skip_build:
        cmd.append("--skip-build")
    if args.dry_run:
        cmd.append("--dry-run")
    return cmd


def run_mode(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)


def print_dry_run(args: argparse.Namespace) -> int:
    print("[cloudlab-random-state-store-comparison dry-run]")
    print(f"inventory={args.inventory}")
    print(f"profile={args.profile}")
    print("workload=p3")
    print("routing=random")
    print(f"sessions={args.sessions}")
    print(f"concurrency={args.concurrency}")
    print(f"repeats={args.repeats}")
    print(f"failure_rate={args.failure_rate}")
    print(f"amendment_rate={args.amendment_rate}")
    print(f"results_dir={os.path.abspath(args.results_dir)}")
    print(f"artifact_dir={os.path.abspath(args.artifact_dir)}")
    print(f"skip_setup={args.skip_setup}")
    print(f"skip_build={args.skip_build}")
    for mode in ("redis", "memory"):
        print("")
        print(f"=== {mode} ===")
        print("runner_cmd:")
        cmd = build_mode_command(args, mode=mode)
        print("  " + " ".join(cmd))
    return 0


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_mode(results_dir: Path, mode: str) -> dict[str, object]:
    summary_path = results_dir / "summary.csv"
    validation_path = results_dir / "validation.json"
    summary_rows = read_csv_rows(summary_path) if summary_path.exists() else []
    validation = read_json(validation_path) if validation_path.exists() else {}
    return {
        "mode": mode,
        "summary_path": str(summary_path),
        "validation_path": str(validation_path),
        "row_count": len(summary_rows),
        "validation_errors": list(validation.get("errors", [])),
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def materialize_artifact(args: argparse.Namespace) -> None:
    target = artifact_dir(args.artifact_dir)
    target.mkdir(parents=True, exist_ok=True)
    redis_dir = Path(mode_results_dir(args.results_dir, "redis"))
    memory_dir = Path(mode_results_dir(args.results_dir, "memory"))
    for source_name, dest_name in [
        ("summary.csv", "redis_summary.csv"),
        ("validation.json", "redis_validation.json"),
    ]:
        if (redis_dir / source_name).exists():
            shutil.copy2(redis_dir / source_name, target / dest_name)
    for source_name, dest_name in [
        ("summary.csv", "memory_summary.csv"),
        ("validation.json", "memory_validation.json"),
    ]:
        if (memory_dir / source_name).exists():
            shutil.copy2(memory_dir / source_name, target / dest_name)

    agg_rows = []
    for mode in ("redis", "memory"):
        source = Path(mode_results_dir(args.results_dir, mode)) / "summary.csv"
        if not source.exists():
            continue
        for row in read_csv_rows(source):
            agg_rows.append(
                {
                    "mode": mode,
                    **row,
                }
            )
    write_csv(target / "cloudlab_random_redis_memory_agg.csv", agg_rows)

    validation = {
        "artifact": "cloudlab_random_redis_memory_v1",
        "inventory": args.inventory,
        "redis_mode": summarize_mode(Path(mode_results_dir(args.results_dir, "redis")), "redis"),
        "memory_mode": summarize_mode(Path(mode_results_dir(args.results_dir, "memory")), "memory"),
        "errors": [],
    }
    (target / "validation.json").write_text(json.dumps(validation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (target / "README_RESULT.md").write_text(
        "# CloudLab Random Routing Redis vs Memory Comparison\n\n"
        "This artifact compares two CloudLab random-routing P3 configurations:\n\n"
        "- Redis correctness evidence: `--plangate-state-store redis --recovery-store redis`\n"
        "- Memory diagnostic control: `--plangate-state-store inmemory --recovery-store inmemory`\n\n"
        "The Redis run is intended as correctness evidence for shared-state necessity. "
        "The memory run is a no-shared-state diagnostic control; state misses may therefore appear as an expected boundary condition rather than a correctness pass criterion.\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        return print_dry_run(args)

    redis_cmd = build_mode_command(args, mode="redis")
    memory_cmd = build_mode_command(args, mode="memory")
    redis_result = run_mode(redis_cmd)
    if redis_result.returncode != 0:
        sys.stdout.write(redis_result.stdout)
        sys.stderr.write(redis_result.stderr)
        return redis_result.returncode

    memory_result = run_mode(memory_cmd)
    sys.stdout.write(redis_result.stdout)
    sys.stderr.write(redis_result.stderr)
    sys.stdout.write(memory_result.stdout)
    sys.stderr.write(memory_result.stderr)

    materialize_artifact(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
