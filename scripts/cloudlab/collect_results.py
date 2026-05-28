#!/usr/bin/env python3
"""Collect CloudLab artifacts and merge loader outputs."""

from __future__ import annotations

import argparse
import csv
import os
import posixpath
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


DEFAULT_SSH_KEY = Path("~/.ssh/cloudlab_ed25519").expanduser()


def resolve_ssh_key(ssh_key: str | None = None) -> str | None:
    if ssh_key:
        return str(Path(ssh_key).expanduser())
    env_key = os.environ.get("CLOUDLAB_SSH_KEY", "").strip()
    if env_key:
        return str(Path(env_key).expanduser())
    if DEFAULT_SSH_KEY.exists():
        return str(DEFAULT_SSH_KEY)
    return None


def ssh_base_args(ssh_key: str | None = None) -> List[str]:
    args: List[str] = []
    resolved = resolve_ssh_key(ssh_key)
    if resolved:
        args.extend(["-i", resolved])
    args.extend(
        [
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
        ]
    )
    return args


def remote_spec(user: str, host: str, path: str) -> str:
    return f"{user}@{host}:{path}"


def scp_from_remote(
    user: str,
    host: str,
    remote_path: str,
    local_path: str,
    *,
    ssh_key: str | None = None,
    recursive: bool = False,
    allow_missing: bool = False,
) -> None:
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    cmd = ["scp", *ssh_base_args(ssh_key)]
    if recursive:
        cmd.append("-r")
    cmd.extend([remote_spec(user, host, remote_path), local_path])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if allow_missing and ("No such file" in result.stderr or "not found" in result.stderr.lower()):
            return
        raise RuntimeError(f"scp failed for {host}:{remote_path}: {result.stderr.strip()}")


def fetch_remote_text(user: str, host: str, command: str, *, ssh_key: str | None = None) -> str:
    result = subprocess.run(
        ["ssh", *ssh_base_args(ssh_key), f"{user}@{host}", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh failed for {host}: {result.stderr.strip()}")
    return result.stdout


def merge_csv_files(inputs: Sequence[str], output_path: str) -> None:
    header: List[str] = []
    rows: List[List[str]] = []
    for path in inputs:
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            try:
                current_header = next(reader)
            except StopIteration:
                continue
            if not header:
                header = current_header
            elif current_header != header:
                raise RuntimeError(f"CSV header mismatch in {path}")
            rows.extend(list(reader))
    if not header:
        raise RuntimeError("no CSV rows collected from loaders")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def collect_run_artifacts(
    *,
    user: str,
    repo_dir: str,
    redis_host: str,
    redis_port: int,
    loader_outputs: Sequence[Tuple[str, str]],
    gateway_hosts: Sequence[str],
    backend_hosts: Sequence[str],
    local_run_dir: str,
    ssh_key: str | None = None,
) -> str:
    os.makedirs(local_run_dir, exist_ok=True)
    loader_local_dir = os.path.join(local_run_dir, "loaders")
    gateway_log_dir = os.path.join(local_run_dir, "logs", "gateways")
    backend_log_dir = os.path.join(local_run_dir, "logs", "backends")
    redis_dir = os.path.join(local_run_dir, "redis")
    os.makedirs(loader_local_dir, exist_ok=True)
    os.makedirs(gateway_log_dir, exist_ok=True)
    os.makedirs(backend_log_dir, exist_ok=True)
    os.makedirs(redis_dir, exist_ok=True)

    loader_csvs: List[str] = []
    for host, remote_csv in loader_outputs:
        local_csv = os.path.join(loader_local_dir, f"{host}_steps.csv")
        scp_from_remote(user, host, remote_csv, local_csv, ssh_key=ssh_key)
        loader_csvs.append(local_csv)

        remote_sessions = remote_csv.replace(".csv", "_sessions.csv")
        local_sessions = os.path.join(loader_local_dir, f"{host}_sessions.csv")
        scp_from_remote(user, host, remote_sessions, local_sessions, ssh_key=ssh_key, allow_missing=True)

        remote_loader_log = os.path.join(os.path.dirname(remote_csv), f"loader_{host}.log").replace("\\", "/")
        local_loader_log = os.path.join(loader_local_dir, f"{host}.log")
        scp_from_remote(user, host, remote_loader_log, local_loader_log, ssh_key=ssh_key, allow_missing=True)

    for host in gateway_hosts:
        host_dir = os.path.join(gateway_log_dir, host)
        os.makedirs(host_dir, exist_ok=True)
        scp_from_remote(
            user,
            host,
            f"{repo_dir}/results/log/cloudlab/",
            host_dir,
            ssh_key=ssh_key,
            recursive=True,
            allow_missing=True,
        )

    for host in backend_hosts:
        host_dir = os.path.join(backend_log_dir, host)
        os.makedirs(host_dir, exist_ok=True)
        scp_from_remote(
            user,
            host,
            f"{repo_dir}/results/log/cloudlab/",
            host_dir,
            ssh_key=ssh_key,
            recursive=True,
            allow_missing=True,
        )

    redis_info = fetch_remote_text(
        user,
        redis_host,
        f"redis-cli -h 127.0.0.1 -p {redis_port} INFO",
        ssh_key=ssh_key,
    )
    with open(os.path.join(redis_dir, "redis_info.txt"), "w", encoding="utf-8") as handle:
        handle.write(redis_info)

    merged_steps = os.path.join(local_run_dir, "steps.csv")
    merge_csv_files(loader_csvs, merged_steps)
    return merged_steps


def collect_p3_run_artifacts(
    *,
    user: str,
    repo_dir: str,
    redis_host: str,
    redis_port: int,
    loader_outputs: Sequence[Tuple[str, str, str]],
    gateway_hosts: Sequence[str],
    backend_hosts: Sequence[str],
    local_run_dir: str,
    policies: Sequence[str],
    ssh_key: str | None = None,
) -> Tuple[str, str]:
    os.makedirs(local_run_dir, exist_ok=True)
    loader_local_dir = os.path.join(local_run_dir, "loaders")
    gateway_log_dir = os.path.join(local_run_dir, "logs", "gateways")
    backend_log_dir = os.path.join(local_run_dir, "logs", "backends")
    redis_dir = os.path.join(local_run_dir, "redis")
    os.makedirs(loader_local_dir, exist_ok=True)
    os.makedirs(gateway_log_dir, exist_ok=True)
    os.makedirs(backend_log_dir, exist_ok=True)
    os.makedirs(redis_dir, exist_ok=True)

    policy_session_inputs = {policy: [] for policy in policies}
    policy_step_inputs = {policy: [] for policy in policies}
    all_sessions: List[str] = []
    all_steps: List[str] = []

    for host, remote_results_dir, remote_loader_log in loader_outputs:
        local_results_dir = os.path.join(loader_local_dir, host)
        scp_from_remote(user, host, remote_results_dir, local_results_dir, ssh_key=ssh_key, recursive=True)
        scp_from_remote(
            user,
            host,
            remote_loader_log,
            os.path.join(loader_local_dir, f"{host}.log"),
            ssh_key=ssh_key,
            allow_missing=True,
        )
        for policy in policies:
            local_policy_dir = os.path.join(local_results_dir, policy)
            local_sessions = os.path.join(local_policy_dir, "sessions.csv")
            local_steps = os.path.join(local_policy_dir, "steps.csv")
            if os.path.isfile(local_sessions):
                policy_session_inputs[policy].append(local_sessions)
                all_sessions.append(local_sessions)
            if os.path.isfile(local_steps):
                policy_step_inputs[policy].append(local_steps)
                all_steps.append(local_steps)

    for host in gateway_hosts:
        host_dir = os.path.join(gateway_log_dir, host)
        os.makedirs(host_dir, exist_ok=True)
        scp_from_remote(
            user,
            host,
            f"{repo_dir}/results/log/cloudlab/",
            host_dir,
            ssh_key=ssh_key,
            recursive=True,
            allow_missing=True,
        )

    for host in backend_hosts:
        host_dir = os.path.join(backend_log_dir, host)
        os.makedirs(host_dir, exist_ok=True)
        scp_from_remote(
            user,
            host,
            f"{repo_dir}/results/log/cloudlab/",
            host_dir,
            ssh_key=ssh_key,
            recursive=True,
            allow_missing=True,
        )

    redis_info = fetch_remote_text(
        user,
        redis_host,
        f"redis-cli -h 127.0.0.1 -p {redis_port} INFO",
        ssh_key=ssh_key,
    )
    with open(os.path.join(redis_dir, "redis_info.txt"), "w", encoding="utf-8") as handle:
        handle.write(redis_info)

    for policy in policies:
        local_policy_dir = os.path.join(local_run_dir, policy)
        os.makedirs(local_policy_dir, exist_ok=True)
        if policy_session_inputs[policy]:
            merge_csv_files(policy_session_inputs[policy], os.path.join(local_policy_dir, "sessions.csv"))
        if policy_step_inputs[policy]:
            merge_csv_files(policy_step_inputs[policy], os.path.join(local_policy_dir, "steps.csv"))

    merged_sessions = os.path.join(local_run_dir, "sessions.csv")
    merged_steps = os.path.join(local_run_dir, "steps.csv")
    if all_sessions:
        merge_csv_files(all_sessions, merged_sessions)
    if all_steps:
        merge_csv_files(all_steps, merged_steps)
    return merged_sessions, merged_steps


def parse_host_path(values: Iterable[str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for item in values:
        host, path = item.split("=", 1)
        pairs.append((host, path))
    return pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect CloudLab artifacts")
    parser.add_argument("--user", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--redis-host", required=True)
    parser.add_argument("--redis-port", type=int, required=True)
    parser.add_argument("--ssh-key", default="")
    parser.add_argument("--loader-output", action="append", default=[], help="HOST=/remote/path/to/steps.csv")
    parser.add_argument("--gateway-host", action="append", default=[])
    parser.add_argument("--backend-host", action="append", default=[])
    parser.add_argument("--local-run-dir", required=True)
    args = parser.parse_args()
    args.ssh_key = resolve_ssh_key(args.ssh_key)
    return args


def main() -> int:
    args = parse_args()
    merged_steps = collect_run_artifacts(
        user=args.user,
        repo_dir=args.repo_dir,
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        loader_outputs=parse_host_path(args.loader_output),
        gateway_hosts=args.gateway_host,
        backend_hosts=args.backend_host,
        local_run_dir=args.local_run_dir,
        ssh_key=args.ssh_key,
    )
    print(merged_steps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
