#!/usr/bin/env python3
"""CloudLab orchestration for distributed PlanGate experiments."""

from __future__ import annotations

import argparse
import csv
import json
import os
import posixpath
import shlex
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
SCRIPTS_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, SCRIPTS_ROOT)

import collect_results
import compute_p3_recovery_amendment_stats as p3_stats
import validate_results


PROFILE_COUNTS = {
    "small": {"loaders": 1, "gateways": 2, "backends": 2},
    "medium": {"loaders": 1, "gateways": 4, "backends": 4},
    "large": {"loaders": 2, "gateways": 8, "backends": 8},
}

SSH_BASE_ARGS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
DEFAULT_ARRIVAL_RATE = 50.0
DEFAULT_BUDGET = 500
DEFAULT_MIN_STEPS = 3
DEFAULT_MAX_STEPS = 7
DEFAULT_STEP_TIMEOUT = 30
DEFAULT_PS_RATIO = 1.0
DEFAULT_BACKEND_WORKERS = 16


def normalize_store_name(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized == "memory":
        return "inmemory"
    if normalized not in {"redis", "inmemory"}:
        raise SystemExit(f"unsupported store value: {value!r}; expected redis|inmemory|memory")
    return normalized


@dataclass(frozen=True)
class Service:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class Topology:
    user: str
    repo_dir: str
    redis_host: str
    redis_port: int
    loaders: Tuple[str, ...]
    gateways: Tuple[Service, ...]
    backends: Tuple[Service, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run distributed PlanGate CloudLab experiments")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILE_COUNTS))
    parser.add_argument("--workload", choices=["standard", "p3"], default="standard")
    parser.add_argument("--policies", nargs="+", default=["naive_retry", "plangate_r", "plangate_ar"])
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[100])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--failure-rate", nargs="+", type=float, default=[0.0])
    parser.add_argument("--amendment-rate", nargs="+", type=float, default=[0.0])
    parser.add_argument("--results-dir", default=os.path.join("results", "cloudlab_runs"))
    parser.add_argument("--commitment-secret", default="")
    parser.add_argument("--arrival-rate", type=float, default=DEFAULT_ARRIVAL_RATE)
    parser.add_argument("--backend-workers", type=int, default=DEFAULT_BACKEND_WORKERS)
    parser.add_argument("--routing", default="random", choices=["single", "random", "sticky", "round_robin"])
    parser.add_argument("--plangate-state-store", default="redis", choices=["redis", "inmemory", "memory"])
    parser.add_argument("--recovery-store", default="inmemory", choices=["inmemory", "memory", "redis"])
    parser.add_argument("--min-success-rate", type=float, default=0.95)
    parser.add_argument("--validation-mode", choices=["correctness", "stress"], default="correctness")
    parser.add_argument("--skip-setup", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.plangate_state_store = normalize_store_name(args.plangate_state_store)
    args.recovery_store = normalize_store_name(args.recovery_store)
    return args


def load_inventory(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as handle:
        inventory = json.load(handle)
    required = {"user", "repo_dir", "redis", "loaders", "gateways", "backends", "ports"}
    missing = required.difference(inventory)
    if missing:
        raise ValueError(f"inventory missing required keys: {sorted(missing)}")
    return inventory


def build_topology(inventory: Dict[str, object], profile: str) -> Topology:
    counts = PROFILE_COUNTS[profile]
    loaders = list(inventory["loaders"])
    gateways = list(inventory["gateways"])
    backends = list(inventory["backends"])
    ports = inventory["ports"]

    if len(loaders) < counts["loaders"]:
        raise ValueError(f"profile {profile} needs {counts['loaders']} loaders, inventory has {len(loaders)}")
    if len(gateways) < counts["gateways"]:
        raise ValueError(f"profile {profile} needs {counts['gateways']} gateways, inventory has {len(gateways)}")
    if len(backends) < counts["backends"]:
        raise ValueError(f"profile {profile} needs {counts['backends']} backends, inventory has {len(backends)}")

    selected_gateways = tuple(
        Service(host=host, port=int(ports["gateway_base"]) + idx)
        for idx, host in enumerate(gateways[: counts["gateways"]])
    )
    selected_backends = tuple(
        Service(host=host, port=int(ports["backend_base"]) + idx)
        for idx, host in enumerate(backends[: counts["backends"]])
    )

    return Topology(
        user=str(inventory["user"]),
        repo_dir=str(inventory["repo_dir"]),
        redis_host=str(inventory["redis"]),
        redis_port=int(ports["redis"]),
        loaders=tuple(loaders[: counts["loaders"]]),
        gateways=selected_gateways,
        backends=selected_backends,
    )


def ensure_supported_workload(args: argparse.Namespace) -> None:
    if not args.commitment_secret and not args.dry_run:
        raise SystemExit("--commitment-secret is required for non-dry-run multi-gateway CloudLab experiments")
    if args.workload == "standard":
        nonzero_failure = [rate for rate in args.failure_rate if abs(rate) > 1e-9]
        nonzero_amendment = [rate for rate in args.amendment_rate if abs(rate) > 1e-9]
        if nonzero_failure or nonzero_amendment:
            raise SystemExit(
                "failure/amendment workload injection is only supported with --workload p3; "
                "use --workload standard --failure-rate 0 --amendment-rate 0 for P0-P2 runs."
            )


def unique_hosts(topology: Topology) -> List[str]:
    hosts: List[str] = []
    seen = set()
    for host in [topology.redis_host, *topology.loaders, *(svc.host for svc in topology.gateways), *(svc.host for svc in topology.backends)]:
        if host not in seen:
            seen.add(host)
            hosts.append(host)
    return hosts


def run_remote(user: str, host: str, command: str, *, capture_output: bool = False, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    ssh_cmd = ["ssh", *SSH_BASE_ARGS, f"{user}@{host}", f"bash -lc {shlex.quote(command)}"]
    if dry_run:
        print(f"[dry-run] {host}: {' '.join(ssh_cmd)}")
        return subprocess.CompletedProcess(ssh_cmd, 0, stdout="", stderr="")
    return subprocess.run(ssh_cmd, check=False, capture_output=capture_output, text=True)


def run_parallel(label: str, jobs: Sequence[Tuple[str, str]], user: str, *, dry_run: bool = False) -> None:
    if not jobs:
        return
    print(f"[cloudlab] {label}: {len(jobs)} job(s)")
    if dry_run:
        for host, command in jobs:
            run_remote(user, host, command, dry_run=True)
        return
    with ThreadPoolExecutor(max_workers=min(len(jobs), 8)) as pool:
        future_map = {
            pool.submit(run_remote, user, host, command, capture_output=True, dry_run=False): (host, command)
            for host, command in jobs
        }
        for future in as_completed(future_map):
            host, command = future_map[future]
            result = future.result()
            if result.returncode != 0:
                raise RuntimeError(
                    f"{label} failed on {host}: {result.stderr.strip() or result.stdout.strip()}\ncommand={command}"
                )


def check_ssh(topology: Topology, *, dry_run: bool = False) -> None:
    jobs = [(host, "echo cloudlab-ok") for host in unique_hosts(topology)]
    run_parallel("ssh-check", jobs, topology.user, dry_run=dry_run)


def setup_nodes(topology: Topology, *, dry_run: bool = False) -> None:
    jobs: List[Tuple[str, str]] = []
    for host in unique_hosts(topology):
        suffix = " --install-redis" if host == topology.redis_host else ""
        cmd = f"cd {shlex.quote(topology.repo_dir)} && bash scripts/cloudlab/setup_node.sh{suffix}"
        jobs.append((host, cmd))
    run_parallel("setup", jobs, topology.user, dry_run=dry_run)


def build_gateways(topology: Topology, *, dry_run: bool = False) -> None:
    jobs = [
        (svc.host, f"cd {shlex.quote(topology.repo_dir)} && bash scripts/cloudlab/build_gateway.sh")
        for svc in topology.gateways
    ]
    run_parallel("build-gateways", jobs, topology.user, dry_run=dry_run)


def stop_cluster(topology: Topology, *, dry_run: bool = False) -> None:
    jobs: List[Tuple[str, str]] = []
    for host in unique_hosts(topology):
        arg = f" 127.0.0.1:{topology.redis_port}" if host == topology.redis_host else ""
        cmd = f"cd {shlex.quote(topology.repo_dir)} && bash scripts/cloudlab/stop_all.sh{arg}"
        jobs.append((host, cmd))
    run_parallel("stop-clean", jobs, topology.user, dry_run=dry_run)


def start_redis(topology: Topology, *, dry_run: bool = False) -> None:
    command = f"sudo systemctl restart redis-server && redis-cli -h 127.0.0.1 -p {topology.redis_port} ping"
    result = run_remote(topology.user, topology.redis_host, command, capture_output=True, dry_run=dry_run)
    if not dry_run and result.returncode != 0:
        raise RuntimeError(f"redis start failed: {result.stderr.strip() or result.stdout.strip()}")


def start_backends(topology: Topology, backend_workers: int, *, dry_run: bool = False) -> None:
    jobs = [
        (
            svc.host,
            f"cd {shlex.quote(topology.repo_dir)} && bash scripts/cloudlab/start_backend.sh {svc.port} {backend_workers}",
        )
        for svc in topology.backends
    ]
    run_parallel("start-backends", jobs, topology.user, dry_run=dry_run)


def expanded_gateway_command(
    topology: Topology,
    svc: Service,
    backend: Service,
    *,
    secret: str,
    plangate_state_store: str,
    recovery_store: str,
) -> str:
    redis_addr = f"{topology.redis_host}:{topology.redis_port}"
    parts = [
        "./gateway_linux",
        "--mode", "mcpdp",
        "--host", "0.0.0.0",
        "--port", str(svc.port),
        "--backend", backend.url,
        "--node-id", f"{svc.host}:{svc.port}",
        "--plangate-state-store", plangate_state_store,
    ]
    if plangate_state_store == "redis":
        parts.extend(["--plangate-redis-addr", redis_addr])
    parts.extend(
        [
            "--commitment-token-mode", "optional",
            "--commitment-token-secret", secret if secret else "<shared-secret>",
            "--enable-recovery=true",
            "--recovery-store", recovery_store,
            "--plan-amendment-mode", "recovery-only",
            "--plan-amendment-require-commitment=true",
            "--plan-amendment-max-count", "3",
            "--plan-amendment-max-budget-delta", "0",
            "--plangate-max-sessions", "300",
            "--plangate-price-step", "40",
        ]
    )
    return " ".join(shlex.quote(part) for part in parts)


def start_gateways(
    topology: Topology,
    secret: str,
    plangate_state_store: str,
    recovery_store: str,
    *,
    dry_run: bool = False,
) -> None:
    redis_addr = f"{topology.redis_host}:{topology.redis_port}"
    jobs: List[Tuple[str, str]] = []
    for idx, svc in enumerate(topology.gateways):
        backend = topology.backends[idx % len(topology.backends)]
        expanded_gateway_cmd = expanded_gateway_command(
            topology,
            svc,
            backend,
            secret=secret,
            plangate_state_store=plangate_state_store,
            recovery_store=recovery_store,
        )
        if dry_run:
            print(f"[dry-run] gateway-cmd {svc.host}:{svc.port}: {expanded_gateway_cmd}")
        startup_line = (
            f"cloudlab-gateway node-id={svc.host}:{svc.port} port={svc.port} backend={backend.url} "
            f"plangate-state-store={plangate_state_store} "
            f"redis-addr={redis_addr if plangate_state_store == 'redis' else 'disabled'} "
            f"commitment-token-mode=optional plan-amendment-mode=recovery-only recovery-store={recovery_store}"
        )
        log_path = f"results/log/cloudlab/gateway_{svc.host}:{svc.port}_{svc.port}.log"
        cmd = (
            f"cd {shlex.quote(topology.repo_dir)} && mkdir -p results/log/cloudlab && "
            f"printf '%s\\n' {shlex.quote(startup_line)} > {shlex.quote(log_path)} && "
            f"nohup {expanded_gateway_cmd} >> {shlex.quote(log_path)} 2>&1 & "
            f"echo $! > {shlex.quote(f'results/log/cloudlab/gateway_{svc.port}.pid')}"
        )
        jobs.append((svc.host, cmd))
    run_parallel("start-gateways", jobs, topology.user, dry_run=dry_run)


def ping_gateway(url: str, timeout_seconds: float = 2.0) -> bool:
    payload = json.dumps({"jsonrpc": "2.0", "id": "hc", "method": "ping"}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read())
        return body.get("jsonrpc") == "2.0"
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def verify_gateways(topology: Topology, *, dry_run: bool = False) -> None:
    if dry_run:
        for svc in topology.gateways:
            print(f"[dry-run] gateway ping {svc.url}")
        return
    deadline = time.time() + 30
    pending = {svc.url for svc in topology.gateways}
    while pending and time.time() < deadline:
        ready = {url for url in pending if ping_gateway(url)}
        pending.difference_update(ready)
        if pending:
            time.sleep(1)
    if pending:
        raise RuntimeError(f"gateway ping failed: {sorted(pending)}")


def split_integer(total: int, parts: int) -> List[int]:
    base, extra = divmod(total, parts)
    return [base + (1 if idx < extra else 0) for idx in range(parts)]


def format_rate(rate: float) -> str:
    text = f"{rate:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def scenario_run_dirs(results_dir: str, profile: str, concurrency: int, failure_rate: float, amendment_rate: float, repeat: int) -> Tuple[str, str]:
    pieces = [
        f"profile_{profile}",
        f"C{concurrency}",
        f"F{format_rate(failure_rate)}",
        f"A{format_rate(amendment_rate)}",
        f"run{repeat}",
    ]
    local_dir = os.path.abspath(os.path.join(results_dir, *pieces))
    remote_dir = posixpath.join(*pieces)
    return local_dir, remote_dir


def p3_run_dirs(
    results_dir: str,
    profile: str,
    concurrency: int,
    amendment_rate: float,
    repeat: int,
    *,
    collapse_to_root: bool,
) -> Tuple[str, str]:
    if collapse_to_root:
        return os.path.abspath(results_dir), "p3_root"
    pieces = [
        f"profile_{profile}",
        f"C{concurrency}",
        "Fmulti",
        f"A{format_rate(amendment_rate)}",
        f"run{repeat}",
    ]
    local_dir = os.path.abspath(os.path.join(results_dir, *pieces))
    remote_dir = posixpath.join(*pieces)
    return local_dir, remote_dir


def remote_results_root(repo_dir: str, results_dir: str) -> str:
    if posixpath.isabs(results_dir):
        return results_dir
    normalized = results_dir.replace("\\", "/").lstrip("./")
    return posixpath.join(repo_dir, normalized)


def resolve_remote_run_dir(repo_dir: str, results_dir: str, remote_run_dir_suffix: str) -> str:
    remote_root = remote_results_root(repo_dir, results_dir)
    if remote_run_dir_suffix in ("", ".", "./"):
        return remote_root
    return posixpath.join(remote_root, remote_run_dir_suffix)


def run_loaders(
    topology: Topology,
    *,
    results_dir: str,
    remote_run_dir_suffix: str,
    sessions: int,
    concurrency: int,
    arrival_rate: float,
    backend_workers: int,
    routing: str,
    dry_run: bool = False,
) -> List[Tuple[str, str]]:
    del backend_workers  # reserved for future loader-side tuning
    remote_run_dir = resolve_remote_run_dir(topology.repo_dir, results_dir, remote_run_dir_suffix)
    session_slices = split_integer(sessions, len(topology.loaders))
    concurrency_slices = split_integer(concurrency, len(topology.loaders))
    gateway_urls = [svc.url for svc in topology.gateways]
    first_target = gateway_urls[0]
    targets_arg = " ".join(shlex.quote(url) for url in gateway_urls)

    jobs: List[Tuple[str, str]] = []
    outputs: List[Tuple[str, str]] = []
    for idx, host in enumerate(topology.loaders):
        loader_sessions = session_slices[idx]
        loader_concurrency = max(concurrency_slices[idx], 1) if loader_sessions > 0 else 0
        if loader_sessions <= 0 or loader_concurrency <= 0:
            continue
        loader_arrival = arrival_rate / len(topology.loaders)
        remote_csv = posixpath.join(remote_run_dir, "loaders", f"{host}_steps.csv")
        loader_log = posixpath.join(remote_run_dir, "loaders", f"loader_{host}.log")
        command = (
            f"cd {shlex.quote(topology.repo_dir)} && mkdir -p {shlex.quote(posixpath.dirname(remote_csv))} && "
            ". .venv/bin/activate && "
            "python scripts/dag_load_generator.py "
            f"--target {shlex.quote(first_target)} "
            f"--targets {targets_arg} "
            f"--routing {shlex.quote(routing)} "
            f"--sessions {loader_sessions} "
            f"--concurrency {loader_concurrency} "
            f"--ps-ratio {DEFAULT_PS_RATIO} "
            f"--budget {DEFAULT_BUDGET} "
            f"--arrival-rate {loader_arrival} "
            "--duration 0 "
            f"--min-steps {DEFAULT_MIN_STEPS} "
            f"--max-steps {DEFAULT_MAX_STEPS} "
            f"--step-timeout {DEFAULT_STEP_TIMEOUT} "
            f"--output {shlex.quote(remote_csv)} "
            f"> {shlex.quote(loader_log)} 2>&1"
        )
        jobs.append((host, command))
        outputs.append((host, remote_csv))

    run_parallel("run-loaders", jobs, topology.user, dry_run=dry_run)
    return outputs


def run_p3_loaders(
    topology: Topology,
    *,
    results_dir: str,
    remote_run_dir_suffix: str,
    sessions: int,
    concurrency: int,
    failure_rates: Sequence[float],
    amendment_rate: float,
    policies: Sequence[str],
    routing: str,
    commitment_secret: str,
    dry_run: bool = False,
) -> List[Tuple[str, str, str]]:
    remote_run_dir = resolve_remote_run_dir(topology.repo_dir, results_dir, remote_run_dir_suffix)
    session_slices = split_integer(sessions, len(topology.loaders))
    concurrency_slices = split_integer(concurrency, len(topology.loaders))
    gateway_urls = [svc.url for svc in topology.gateways]
    gateway_args = " ".join(shlex.quote(url) for url in gateway_urls)
    policies_arg = " ".join(shlex.quote(policy) for policy in policies)
    failure_arg = " ".join(format_rate(rate) for rate in failure_rates)

    jobs: List[Tuple[str, str]] = []
    outputs: List[Tuple[str, str, str]] = []
    for idx, host in enumerate(topology.loaders):
        loader_sessions = session_slices[idx]
        loader_concurrency = max(concurrency_slices[idx], 1) if loader_sessions > 0 else 0
        if loader_sessions <= 0 or loader_concurrency <= 0:
            continue
        remote_loader_dir = posixpath.join(remote_run_dir, "loaders", host)
        loader_log = posixpath.join(remote_run_dir, "loaders", f"loader_{host}.log")
        command = (
            f"cd {shlex.quote(topology.repo_dir)} && mkdir -p {shlex.quote(remote_loader_dir)} && "
            ". .venv/bin/activate && "
            "python scripts/p3_recovery_amendment_runner.py "
            f"--policies {policies_arg} "
            f"--sessions {loader_sessions} "
            f"--concurrency {loader_concurrency} "
            f"--failure-rate {failure_arg} "
            f"--amendment-rate {format_rate(amendment_rate)} "
            f"--results-dir {shlex.quote(remote_loader_dir)} "
            f"--commitment-secret {shlex.quote(commitment_secret)} "
            f"--gateway-urls {gateway_args} "
            f"--routing {shlex.quote(routing)} "
            "--no-start-services "
            f"> {shlex.quote(loader_log)} 2>&1"
        )
        jobs.append((host, command))
        outputs.append((host, remote_loader_dir, loader_log))

    run_parallel("run-p3-loaders", jobs, topology.user, dry_run=dry_run)
    return outputs


def write_summary_csv(rows: Sequence[Dict[str, object]], output_path: str) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_aggregate_csv(rows: Sequence[Dict[str, object]], output_path: str) -> None:
    grouped: Dict[Tuple[object, object, object], List[Dict[str, object]]] = {}
    for row in rows:
        key = (row["concurrency"], row["failure_rate"], row["amendment_rate"])
        grouped.setdefault(key, []).append(row)

    aggregate_rows: List[Dict[str, object]] = []
    for key, items in sorted(grouped.items()):
        aggregate_rows.append(
            {
                "concurrency": key[0],
                "failure_rate": key[1],
                "amendment_rate": key[2],
                "runs": len(items),
                "success_rate_mean": statistics.mean(float(item["success_rate"]) for item in items),
                "effective_goodput_mean": statistics.mean(float(item["effective_goodput"]) for item in items),
                "gateway_p95_latency_us_mean": statistics.mean(float(item["gateway_p95_latency_us"]) for item in items),
                "cross_node_sessions_mean": statistics.mean(float(item["cross_node_sessions"]) for item in items),
            }
        )
    write_summary_csv(aggregate_rows, output_path)


def print_dry_run(topology: Topology, args: argparse.Namespace) -> None:
    print("[cloudlab] dry-run topology")
    print(json.dumps(
        {
            "profile": args.profile,
            "workload": args.workload,
            "policies": args.policies,
            "redis": {"host": topology.redis_host, "port": topology.redis_port},
            "loaders": list(topology.loaders),
            "gateways": [asdict(svc) for svc in topology.gateways],
            "backends": [asdict(svc) for svc in topology.backends],
            "results_dir": os.path.abspath(args.results_dir),
            "concurrency": args.concurrency,
            "failure_rate": args.failure_rate,
            "amendment_rate": args.amendment_rate,
            "repeats": args.repeats,
            "routing": args.routing,
            "plangate_state_store": args.plangate_state_store,
            "recovery_store": args.recovery_store,
            "min_success_rate": args.min_success_rate,
            "validation_mode": args.validation_mode,
        },
        indent=2,
        sort_keys=True,
    ))


def compute_p3_summaries(local_run_dir: str) -> None:
    rows = p3_stats.collect_policy_rows(Path(local_run_dir))
    main_summary = p3_stats.summarize_main(rows)
    adversarial_summary = p3_stats.summarize_adversarial(rows)
    p3_stats.write_csv(Path(local_run_dir) / "p3_summary.csv", main_summary)
    p3_stats.write_csv(Path(local_run_dir) / "p3_adversarial_summary.csv", adversarial_summary)


def main() -> int:
    args = parse_args()
    ensure_supported_workload(args)
    inventory = load_inventory(args.inventory)
    topology = build_topology(inventory, args.profile)
    single_p3_root = args.workload == "p3" and len(args.concurrency) == 1 and len(args.amendment_rate) == 1 and args.repeats == 1

    if args.dry_run:
        if args.workload == "standard":
            _sample_local_dir, sample_remote_suffix = scenario_run_dirs(
                args.results_dir,
                args.profile,
                args.concurrency[0],
                args.failure_rate[0],
                args.amendment_rate[0],
                1,
            )
        else:
            _sample_local_dir, sample_remote_suffix = p3_run_dirs(
                args.results_dir,
                args.profile,
                args.concurrency[0],
                args.amendment_rate[0],
                1,
                collapse_to_root=single_p3_root,
            )
        print_dry_run(topology, args)
        check_ssh(topology, dry_run=True)
        setup_nodes(topology, dry_run=True)
        build_gateways(topology, dry_run=True)
        stop_cluster(topology, dry_run=True)
        start_redis(topology, dry_run=True)
        start_backends(topology, args.backend_workers, dry_run=True)
        start_gateways(
            topology,
            args.commitment_secret or "<shared-secret>",
            args.plangate_state_store,
            args.recovery_store,
            dry_run=True,
        )
        verify_gateways(topology, dry_run=True)
        if args.workload == "standard":
            run_loaders(
                topology,
                results_dir=args.results_dir,
                remote_run_dir_suffix=sample_remote_suffix,
                sessions=args.sessions,
                concurrency=args.concurrency[0],
                arrival_rate=args.arrival_rate,
                backend_workers=args.backend_workers,
                routing=args.routing,
                dry_run=True,
            )
        else:
            run_p3_loaders(
                topology,
                results_dir=args.results_dir,
                remote_run_dir_suffix=sample_remote_suffix,
                sessions=args.sessions,
                concurrency=args.concurrency[0],
                failure_rates=args.failure_rate,
                amendment_rate=args.amendment_rate[0],
                policies=args.policies,
                routing=args.routing,
                commitment_secret=args.commitment_secret or "<shared-secret>",
                dry_run=True,
            )
        return 0

    check_ssh(topology, dry_run=False)
    if not args.skip_setup:
        setup_nodes(topology, dry_run=False)
    if not args.skip_build:
        build_gateways(topology, dry_run=False)

    summary_rows: List[Dict[str, object]] = []
    validation_failures = 0
    if args.workload == "standard":
        scenarios = list(product(args.concurrency, args.failure_rate, args.amendment_rate, range(1, args.repeats + 1)))
    else:
        scenarios = list(product(args.concurrency, args.amendment_rate, range(1, args.repeats + 1)))

    try:
        for scenario in scenarios:
            if args.workload == "standard":
                concurrency, failure_rate, amendment_rate, repeat = scenario
                local_run_dir, remote_run_suffix = scenario_run_dirs(
                    args.results_dir, args.profile, concurrency, failure_rate, amendment_rate, repeat
                )
                print(
                    f"[cloudlab] run workload=standard profile={args.profile} concurrency={concurrency} "
                    f"failure_rate={failure_rate} amendment_rate={amendment_rate} repeat={repeat}"
                )
            else:
                concurrency, amendment_rate, repeat = scenario
                failure_rate = None
                local_run_dir, remote_run_suffix = p3_run_dirs(
                    args.results_dir,
                    args.profile,
                    concurrency,
                    amendment_rate,
                    repeat,
                    collapse_to_root=single_p3_root,
                )
                print(
                    f"[cloudlab] run workload=p3 profile={args.profile} concurrency={concurrency} "
                    f"failure_rates={args.failure_rate} amendment_rate={amendment_rate} repeat={repeat}"
                )

            stop_cluster(topology, dry_run=False)
            start_redis(topology, dry_run=False)
            start_backends(topology, args.backend_workers, dry_run=False)
            start_gateways(
                topology,
                args.commitment_secret,
                args.plangate_state_store,
                args.recovery_store,
                dry_run=False,
            )
            verify_gateways(topology, dry_run=False)

            if args.workload == "standard":
                loader_outputs = run_loaders(
                    topology,
                    results_dir=args.results_dir,
                    remote_run_dir_suffix=remote_run_suffix,
                    sessions=args.sessions,
                    concurrency=concurrency,
                    arrival_rate=args.arrival_rate,
                    backend_workers=args.backend_workers,
                    routing=args.routing,
                    dry_run=False,
                )

                merged_steps = collect_results.collect_run_artifacts(
                    user=topology.user,
                    repo_dir=topology.repo_dir,
                    redis_host=topology.redis_host,
                    redis_port=topology.redis_port,
                    loader_outputs=loader_outputs,
                    gateway_hosts=[svc.host for svc in topology.gateways],
                    backend_hosts=[svc.host for svc in topology.backends],
                    local_run_dir=local_run_dir,
                )

                summary = validate_results.summarize_steps_csv(merged_steps)
                errors: List[str] = []
                if not args.skip_validate:
                    errors = validate_results.validate_summary(
                        summary,
                        expected_sessions=args.sessions,
                        gateway_count=len(topology.gateways),
                        routing=args.routing,
                        failure_rate=float(failure_rate or 0.0),
                        min_success_rate=args.min_success_rate,
                        validation_mode=args.validation_mode,
                        gateway_log_dir=os.path.join(local_run_dir, "logs", "gateways"),
                        expected_commitment_mode="optional",
                        expected_redis_addr=(
                            f"{topology.redis_host}:{topology.redis_port}"
                            if args.plangate_state_store == "redis"
                            else None
                        ),
                        expected_recovery_store=args.recovery_store,
                    )
                    validate_results.write_summary(
                        os.path.join(local_run_dir, "validation.json"),
                        summary,
                        errors,
                    )
                    if errors:
                        validation_failures += 1
                        print(f"[cloudlab] validation errors for {local_run_dir}:")
                        for error in errors:
                            print(f"  - {error}")

                summary_rows.append(
                    {
                        "workload": args.workload,
                        "profile": args.profile,
                        "concurrency": concurrency,
                        "failure_rate": failure_rate,
                        "amendment_rate": amendment_rate,
                        "repeat": repeat,
                        "plangate_state_store": args.plangate_state_store,
                        "recovery_store": args.recovery_store,
                        "total_sessions": summary.total_sessions,
                        "success_sessions": summary.success_sessions,
                        "success_rate": round(summary.success_rate, 6),
                        "cascade_failed": summary.cascade_failed,
                        "state_miss": summary.state_miss,
                        "duplicate_admission": summary.duplicate_admission,
                        "cross_node_sessions": summary.cross_node_sessions,
                        "gateway_p95_latency_us": round(summary.gateway_p95_latency_us, 3),
                        "effective_goodput": round(summary.effective_goodput, 6),
                        "elapsed_seconds": round(summary.elapsed_seconds, 6),
                        "steps_csv": merged_steps,
                        "validation_mode": args.validation_mode,
                        "validation_passed": not errors,
                    }
                )
                write_summary_csv(summary_rows, os.path.join(os.path.abspath(args.results_dir), "summary.csv"))
                write_aggregate_csv(summary_rows, os.path.join(os.path.abspath(args.results_dir), "aggregate.csv"))
                continue

            loader_outputs = run_p3_loaders(
                topology,
                results_dir=args.results_dir,
                remote_run_dir_suffix=remote_run_suffix,
                sessions=args.sessions,
                concurrency=concurrency,
                failure_rates=args.failure_rate,
                amendment_rate=amendment_rate,
                policies=args.policies,
                routing=args.routing,
                commitment_secret=args.commitment_secret,
                dry_run=False,
            )

            collect_results.collect_p3_run_artifacts(
                user=topology.user,
                repo_dir=topology.repo_dir,
                redis_host=topology.redis_host,
                redis_port=topology.redis_port,
                loader_outputs=loader_outputs,
                gateway_hosts=[svc.host for svc in topology.gateways],
                backend_hosts=[svc.host for svc in topology.backends],
                local_run_dir=local_run_dir,
                policies=args.policies,
            )
            compute_p3_summaries(local_run_dir)

            errors = []
            p3_summary = validate_results.summarize_p3_outputs(local_run_dir)
            if not args.skip_validate:
                errors = validate_results.validate_p3_results(
                    results_dir=local_run_dir,
                    expected_policies=args.policies,
                    expected_failure_rates=args.failure_rate,
                    gateway_count=len(topology.gateways),
                    routing=args.routing,
                    validation_mode=args.validation_mode,
                    min_success_rate=args.min_success_rate,
                    gateway_log_dir=os.path.join(local_run_dir, "logs", "gateways"),
                    expected_commitment_mode="optional",
                    expected_redis_addr=(
                        f"{topology.redis_host}:{topology.redis_port}"
                        if args.plangate_state_store == "redis"
                        else None
                    ),
                    expected_recovery_store=args.recovery_store,
                )
                with open(os.path.join(local_run_dir, "validation.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "summary": validate_results.p3_summary_to_dict(p3_summary),
                            "errors": errors,
                        },
                        handle,
                        indent=2,
                        sort_keys=True,
                    )
                if errors:
                    validation_failures += 1
                    print(f"[cloudlab] validation errors for {local_run_dir}:")
                    for error in errors:
                        print(f"  - {error}")

            summary_rows.append(
                {
                    "workload": args.workload,
                    "profile": args.profile,
                    "concurrency": concurrency,
                    "failure_rates": ",".join(format_rate(rate) for rate in args.failure_rate),
                    "amendment_rate": amendment_rate,
                    "repeat": repeat,
                    "plangate_state_store": args.plangate_state_store,
                    "recovery_store": args.recovery_store,
                    "policies": ",".join(args.policies),
                    "cross_node_sessions": p3_summary.cross_node_sessions,
                    "state_miss": p3_summary.state_miss,
                    "duplicate_admission": p3_summary.duplicate_admission,
                    "commitment_invalid": p3_summary.commitment_invalid,
                    "commitment_mismatch": p3_summary.commitment_mismatch,
                    "commitment_expired": p3_summary.commitment_expired,
                    "p3_summary_csv": os.path.join(local_run_dir, "p3_summary.csv"),
                    "p3_adversarial_summary_csv": os.path.join(local_run_dir, "p3_adversarial_summary.csv"),
                    "validation_mode": args.validation_mode,
                    "validation_passed": not errors,
                }
            )
            write_summary_csv(summary_rows, os.path.join(os.path.abspath(args.results_dir), "summary.csv"))
    finally:
        stop_cluster(topology, dry_run=False)

    return 1 if validation_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
