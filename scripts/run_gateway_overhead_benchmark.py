#!/usr/bin/env python3
"""Run the gateway-overhead benchmark suite.

This script collects two layers of evidence:
  1. Go in-process benchmarks for DAG validation, lookup, admission, and HTTP paths.
  2. Live gateway traces collected via scripts/dag_load_generator.py, which emit
     gateway_latency_us in the per-step CSV output.

Outputs (default):
  results/exp_gateway_overhead/go_bench_overhead.txt
  results/exp_gateway_overhead/go_bench_overhead.csv          (written by the compute script)
  results/exp_gateway_overhead/live/<target>/C<conc>/runN.csv  (raw step traces)
  results/exp_gateway_overhead/gateway_overhead_agg.csv       (written by the compute script)

Usage examples:
  python scripts/run_gateway_overhead_benchmark.py \
      --targets ng=http://127.0.0.1:9201 srl=http://127.0.0.1:9202 \
      --targets sbac=http://127.0.0.1:9203 plangate_full=http://127.0.0.1:9204 \
      --concurrency 10,40,80 --repeats 5

  python scripts/run_gateway_overhead_benchmark.py --skip-go-bench --skip-live
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DEFAULT_RESULTS_DIR = ROOT_DIR / "results" / "exp_gateway_overhead"
DEFAULT_GO_BENCH_PATTERN = (
    "Benchmark(DAGValidation|PriceComputation|SessionLookup|FullAdmission|"
    "PSAdmission|HTTPOverhead|Concurrent|GovernanceIntensity)"
)


def parse_key_value_pairs(items: Sequence[str]) -> List[Tuple[str, str]]:
    parsed: List[Tuple[str, str]] = []
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid target mapping: {item!r}; expected label=url")
        label, url = item.split("=", 1)
        label = label.strip()
        url = url.strip()
        if not label or not url:
            raise ValueError(f"invalid target mapping: {item!r}; expected label=url")
        parsed.append((label, url))
    return parsed


def run_command(command: List[str], output_path: Path, cwd: Path | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output_path.write_text(completed.stdout, encoding="utf-8")
    print(f"[run] {' '.join(command)}")
    print(f"[run] wrote {output_path}")
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def run_go_bench(results_dir: Path, bench_pattern: str) -> Path:
    bench_txt = results_dir / "go_bench_overhead.txt"
    command = [
        "go",
        "test",
        "./plangate",
        "-run",
        "^$",
        "-bench",
        bench_pattern,
        "-benchmem",
        "-count=1",
    ]
    run_command(command, bench_txt, cwd=ROOT_DIR)
    return bench_txt


def run_live_gateway_benchmark(
    results_dir: Path,
    targets: Sequence[Tuple[str, str]],
    concurrency_levels: Sequence[int],
    repeats: int,
    sessions: int,
    ps_ratio: float,
    budget: int,
    heavy_ratio: float,
    min_steps: int,
    max_steps: int,
    arrival_rate: float,
    cpu_affinity: str,
    step_timeout: float,
    heavy_burn_ms: int,
    price_ttl: float,
    routing: str,
) -> None:
    dag_loader = SCRIPT_DIR / "dag_load_generator.py"
    for label, url in targets:
        for conc in concurrency_levels:
            for repeat in range(1, repeats + 1):
                out_path = results_dir / "live" / label / f"C{conc}" / f"run{repeat}.csv"
                log_path = results_dir / "live" / label / f"C{conc}" / f"run{repeat}.log"
                command = [
                    sys.executable,
                    str(dag_loader),
                    "--target",
                    url,
                    "--sessions",
                    str(sessions),
                    "--ps-ratio",
                    str(ps_ratio),
                    "--budget",
                    str(budget),
                    "--heavy-ratio",
                    str(heavy_ratio),
                    "--min-steps",
                    str(min_steps),
                    "--max-steps",
                    str(max_steps),
                    "--step-timeout",
                    str(step_timeout),
                    "--concurrency",
                    str(conc),
                    "--arrival-rate",
                    str(arrival_rate),
                    "--output",
                    str(out_path),
                    "--cpu-affinity",
                    cpu_affinity,
                    "--heavy-burn-ms",
                    str(heavy_burn_ms),
                    "--price-ttl",
                    str(price_ttl),
                    "--routing",
                    routing,
                ]
                # Keep CSV output owned by dag_load_generator; capture runner logs separately.
                run_command(command, log_path, cwd=ROOT_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run gateway overhead benchmark suite.")
    parser.add_argument(
        "--results-dir",
        default=str(DEFAULT_RESULTS_DIR),
        help=f"Output directory (default: {DEFAULT_RESULTS_DIR})",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        action="append",
        default=[],
        help="One or more target mappings label=url; repeat the flag for multiple groups.",
    )
    parser.add_argument(
        "--concurrency",
        default="10,40,80",
        help="Comma-separated concurrency levels for the live benchmark.",
    )
    parser.add_argument("--repeats", type=int, default=5, help="Number of repeats per target/concurrency.")
    parser.add_argument("--sessions", type=int, default=300, help="Sessions per live run.")
    parser.add_argument("--ps-ratio", type=float, default=1.0, help="P&S ratio for live runs.")
    parser.add_argument("--budget", type=int, default=500, help="Per-session budget for live runs.")
    parser.add_argument("--heavy-ratio", type=float, default=0.2, help="Heavy-tool ratio for live runs.")
    parser.add_argument("--min-steps", type=int, default=5, help="Minimum steps per P&S session.")
    parser.add_argument("--max-steps", type=int, default=5, help="Maximum steps per P&S session.")
    parser.add_argument("--arrival-rate", type=float, default=20.0, help="Arrival rate (sessions/s) for live runs.")
    parser.add_argument("--cpu-affinity", default="0,1", help="CPU affinity passed to dag_load_generator.py.")
    parser.add_argument("--step-timeout", type=float, default=120.0, help="Per-step timeout in seconds.")
    parser.add_argument("--heavy-burn-ms", type=int, default=800, help="CPU burn duration for heavy mock tools.")
    parser.add_argument("--price-ttl", type=float, default=1.0, help="Client price cache TTL in seconds.")
    parser.add_argument("--routing", default="single", choices=["single", "random", "sticky", "round_robin"], help="Routing strategy for live runs.")
    parser.add_argument("--skip-go-bench", action="store_true", help="Skip the Go in-process benchmark run.")
    parser.add_argument("--skip-live", action="store_true", help="Skip live gateway trace collection.")
    parser.add_argument(
        "--go-bench-pattern",
        default=DEFAULT_GO_BENCH_PATTERN,
        help=f"Go benchmark regex (default: {DEFAULT_GO_BENCH_PATTERN})",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_go_bench:
        run_go_bench(results_dir, args.go_bench_pattern)

    if not args.skip_live:
        flat_targets: List[Tuple[str, str]] = []
        for group in args.targets:
            flat_targets.extend(parse_key_value_pairs(group))
        if not flat_targets:
            raise SystemExit("No live targets provided. Use --targets label=url label=url ...")
        concurrency_levels = [int(item.strip()) for item in args.concurrency.split(",") if item.strip()]
        run_live_gateway_benchmark(
            results_dir=results_dir,
            targets=flat_targets,
            concurrency_levels=concurrency_levels,
            repeats=args.repeats,
            sessions=args.sessions,
            ps_ratio=args.ps_ratio,
            budget=args.budget,
            heavy_ratio=args.heavy_ratio,
            min_steps=args.min_steps,
            max_steps=args.max_steps,
            arrival_rate=args.arrival_rate,
            cpu_affinity=args.cpu_affinity,
            step_timeout=args.step_timeout,
            heavy_burn_ms=args.heavy_burn_ms,
            price_ttl=args.price_ttl,
            routing=args.routing,
        )

    print(f"Completed gateway overhead collection under {results_dir}")


if __name__ == "__main__":
    main()
