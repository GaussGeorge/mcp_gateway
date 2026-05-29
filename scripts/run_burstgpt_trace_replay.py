#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import os
import random
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DAG_RUNNER_PATH = SCRIPT_DIR / "dag_load_generator.py"
TRACE_DEFAULT = ROOT_DIR / "external" / "BurstGPT" / "data"
ARTIFACT_DIR = ROOT_DIR / "artifact_results" / "burstgpt_trace_replay_v1"
BACKEND_PORT = 8080
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"


SUMMARY_COLUMNS = [
    "gateway",
    "window_id",
    "window_label",
    "scale",
    "repeat",
    "selected_trace_rows",
    "duration_s",
    "success",
    "success_rate",
    "abd",
    "abd_total",
    "cascade_failed",
    "cascade_rate",
    "all_rejected",
    "step0_rejected",
    "effective_goodput_s",
    "p50_ms",
    "p95_ms",
    "client_rc",
    "client_timed_out",
    "error",
    "backend_mode",
    "source_trace_sha256",
]


AGG_COLUMNS = [
    "gateway",
    "window_id",
    "window_label",
    "scale",
    "runs",
    "selected_trace_rows_mean",
    "duration_s_mean",
    "success_mean",
    "success_rate_mean",
    "abd_mean",
    "abd_total_mean",
    "cascade_failed_mean",
    "cascade_rate_mean",
    "all_rejected_mean",
    "step0_rejected_mean",
    "effective_goodput_s_mean",
    "p50_ms_mean",
    "p95_ms_mean",
    "client_rc_mean",
    "client_timed_out_mean",
]


NUMERIC_FOR_AGG = [
    "selected_trace_rows",
    "duration_s",
    "success",
    "success_rate",
    "abd",
    "abd_total",
    "cascade_failed",
    "cascade_rate",
    "all_rejected",
    "step0_rejected",
    "effective_goodput_s",
    "p50_ms",
    "p95_ms",
    "client_rc",
    "client_timed_out",
]


@dataclass(frozen=True)
class GatewayConfig:
    name: str
    mode: str
    extra_args: tuple[str, ...]


@dataclass(frozen=True)
class TraceWindow:
    window_id: str
    window_label: str
    start_row: int
    end_row: int
    start_ts: float
    end_ts: float
    offsets: tuple[float, ...]


GATEWAYS: tuple[GatewayConfig, ...] = (
    GatewayConfig("ng", "ng", ()),
    GatewayConfig("static", "srl", ("--srl-qps", "65", "--srl-burst", "400", "--srl-max-conc", "55")),
    GatewayConfig("pp", "pp", ("--pp-max-sessions", "150")),
    GatewayConfig("rajomon", "rajomon", ("--rajomon-price-step", "5")),
    GatewayConfig(
        "plangate_relaxed",
        "mcpdp-real",
        (
            "--plangate-price-step",
            "30",
            "--plangate-max-sessions",
            "24",
            "--plangate-sunk-cost-alpha",
            "0.7",
            "--plangate-session-cap-wait",
            "6",
            "--real-ratelimit-max",
            "9999",
            "--real-latency-threshold",
            "10000",
        ),
    ),
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dag = load_module(DAG_RUNNER_PATH, "burstgpt_dag_runner")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="BurstGPT-shaped arrival replay for PlanGate templates.")
    parser.add_argument("--trace", type=str, default="")
    parser.add_argument("--window-size", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scales", nargs="+", type=float, default=[1.0, 1.5, 2.0])
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--ps-ratio", type=float, default=0.5)
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--heavy-ratio", type=float, default=0.3)
    parser.add_argument("--min-steps", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=7)
    parser.add_argument("--step-timeout", type=float, default=30.0)
    parser.add_argument(
        "--gateway-binary",
        type=str,
        default="gateway.exe" if sys.platform == "win32" else "gateway",
    )
    parser.add_argument("--base-port", type=int, default=9700)
    parser.add_argument("--backend-mode", type=str, default="mock_sterile_single_machine")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--queue-timeout", type=float, default=2.0)
    parser.add_argument("--congestion-factor", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_trace_csv(path: Path) -> bool:
    if not path.exists() or path.suffix.lower() != ".csv":
        return False
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fields = {str(x).strip().lower() for x in (reader.fieldnames or [])}
    except Exception:
        return False
    has_ts = any(x in fields for x in ["timestamp", "arrival", "time", "ts"])
    has_token = any("token" in x for x in fields)
    return has_ts and has_token


def pick_trace(path_arg: str) -> Path:
    if path_arg:
        p = Path(path_arg)
        if not p.is_absolute():
            p = ROOT_DIR / p
        if not is_trace_csv(p):
            raise SystemExit(f"trace csv invalid or missing required fields: {p}")
        return p

    preferred = TRACE_DEFAULT / "BurstGPT_without_fails_1.csv"
    if is_trace_csv(preferred):
        return preferred

    candidates = sorted(TRACE_DEFAULT.glob("*.csv"), key=lambda p: p.stat().st_size)
    for p in candidates:
        if is_trace_csv(p):
            return p

    raise SystemExit(f"no usable BurstGPT csv found under {TRACE_DEFAULT}")


def detect_col(fieldnames: list[str], choices: list[str]) -> str:
    lower_map = {f.strip().lower(): f for f in fieldnames}
    for c in choices:
        if c in lower_map:
            return lower_map[c]
    for k, v in lower_map.items():
        for c in choices:
            if c in k:
                return v
    raise SystemExit(f"unable to detect column from choices={choices}, fields={fieldnames}")


def load_trace_rows(trace_path: Path) -> list[dict[str, Any]]:
    with trace_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise SystemExit("trace csv has no rows")

    ts_col = detect_col(list(rows[0].keys()), ["timestamp", "arrival", "time", "ts"])
    req_col = detect_col(list(rows[0].keys()), ["request tokens", "prompt tokens", "input tokens", "req tokens"])
    resp_col = detect_col(list(rows[0].keys()), ["response tokens", "completion tokens", "output tokens", "resp tokens"])
    total_col = detect_col(list(rows[0].keys()), ["total tokens", "tokens"])

    clean: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        try:
            ts = float(str(row.get(ts_col, "")).strip())
        except ValueError:
            continue
        def _num(v: Any) -> int:
            try:
                return int(float(str(v).strip() or "0"))
            except ValueError:
                return 0

        req = _num(row.get(req_col, 0))
        resp = _num(row.get(resp_col, 0))
        total = _num(row.get(total_col, 0))
        clean.append(
            {
                "row_index": idx,
                "timestamp": ts,
                "request_tokens": req,
                "response_tokens": resp,
                "total_tokens": total,
            }
        )

    if len(clean) < 10:
        raise SystemExit("trace rows too few after parsing")

    clean.sort(key=lambda r: r["timestamp"])
    return clean


def sliding_mean_gaps(ts: list[float], window_size: int) -> list[tuple[int, float]]:
    scores: list[tuple[int, float]] = []
    for start in range(0, len(ts) - window_size + 1):
        sub = ts[start : start + window_size]
        gaps = [max(0.0, sub[i] - sub[i - 1]) for i in range(1, len(sub))]
        mean_gap = sum(gaps) / max(1, len(gaps))
        scores.append((start, mean_gap))
    return scores


def pick_distinct_starts(scores: list[tuple[int, float]], target: float, used: set[int], min_distance: int) -> int:
    ranked = sorted(scores, key=lambda x: abs(x[1] - target))
    for start, _ in ranked:
        if all(abs(start - u) >= min_distance for u in used):
            return start
    return ranked[0][0]


def build_windows(rows: list[dict[str, Any]], window_size: int) -> list[TraceWindow]:
    ts = [float(r["timestamp"]) for r in rows]
    scores = sliding_mean_gaps(ts, window_size)
    means = [x[1] for x in scores]
    means_sorted = sorted(means)

    def quantile(q: float) -> float:
        idx = min(len(means_sorted) - 1, max(0, int(round(q * (len(means_sorted) - 1)))))
        return means_sorted[idx]

    target_normal = quantile(0.5)
    target_burst = quantile(0.25)
    target_peak = min(means_sorted)

    used: set[int] = set()
    starts: dict[str, int] = {}
    min_distance = max(1, window_size // 2)

    starts["normal"] = pick_distinct_starts(scores, target_normal, used, min_distance)
    used.add(starts["normal"])
    starts["burst"] = pick_distinct_starts(scores, target_burst, used, min_distance)
    used.add(starts["burst"])
    starts["peak_burst"] = pick_distinct_starts(scores, target_peak, used, min_distance)

    windows: list[TraceWindow] = []
    for key, label in [
        ("normal", "normal"),
        ("burst", "burst"),
        ("peak_burst", "peak-burst"),
    ]:
        s = starts[key]
        sub = rows[s : s + window_size]
        base_ts = float(sub[0]["timestamp"])
        offsets = tuple(float(r["timestamp"]) - base_ts for r in sub)
        windows.append(
            TraceWindow(
                window_id=key,
                window_label=label,
                start_row=int(sub[0]["row_index"]),
                end_row=int(sub[-1]["row_index"]),
                start_ts=float(sub[0]["timestamp"]),
                end_ts=float(sub[-1]["timestamp"]),
                offsets=offsets,
            )
        )
    return windows


def ensure_gateway_binary(path: Path) -> Path:
    if path.exists():
        return path
    subprocess.run(
        ["go", "build", "-o", str(path), "./cmd/gateway"],
        cwd=str(ROOT_DIR),
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return path


def start_backend(args) -> subprocess.Popen:
    log_path = ARTIFACT_DIR / "_tmp_backend.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        str(ROOT_DIR / "mcp_server" / "server.py"),
        "--port",
        str(BACKEND_PORT),
        "--mode",
        "sterile",
        "--max-workers",
        str(args.max_workers),
        "--queue-timeout",
        str(args.queue_timeout),
        "--congestion-factor",
        str(args.congestion_factor),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR / "mcp_server"),
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError(f"backend failed to start; see {log_path}")
    return proc


def start_gateway(binary: Path, args, gateway: GatewayConfig, port: int) -> subprocess.Popen:
    log_path = ARTIFACT_DIR / f"_tmp_gateway_{gateway.name}.log"
    cmd = [
        str(binary),
        "--mode",
        gateway.mode,
        "--port",
        str(port),
        "--backend",
        BACKEND_URL,
        "--host",
        "127.0.0.1",
        *gateway.extra_args,
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"gateway {gateway.name} failed to start; see {log_path}")
    return proc


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(s.getsockname()[1])


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = min(len(sorted_vals) - 1, max(0, int(round((len(sorted_vals) - 1) * p))))
    return float(sorted_vals[idx])


async def run_replay_cell(
    gateway_url: str,
    window: TraceWindow,
    scale: float,
    repeat_idx: int,
    args,
) -> dict[str, Any]:
    rng = random.Random(args.seed + repeat_idx + int(scale * 1000) + sum(ord(c) for c in window.window_id))
    offsets = [x / scale for x in window.offsets]
    selected_trace_rows = len(offsets)

    plans = []
    for i in range(selected_trace_rows):
        sid = f"{window.window_id}-r{repeat_idx:02d}-{i:04d}"
        if rng.random() < args.ps_ratio:
            plan = dag.generate_ps_session(
                sid,
                args.budget,
                args.heavy_ratio,
                min_steps=args.min_steps,
                max_steps=args.max_steps,
            )
        else:
            plan = dag.generate_react_session(
                sid,
                args.budget,
                args.heavy_ratio,
                min_steps=1,
                max_steps=args.max_steps,
            )
        plans.append(plan)

    semaphore = asyncio.Semaphore(args.concurrency)
    req_counter = [0]

    connector = aiohttp.TCPConnector(limit=args.concurrency, limit_per_host=args.concurrency)
    start = time.time()

    async with aiohttp.ClientSession(connector=connector) as http_session:
        tasks: list[asyncio.Task[Any]] = []

        async def run_one(plan):
            async with semaphore:
                return await dag.execute_session(http_session, gateway_url, plan, req_counter)

        for plan, target_offset in zip(plans, offsets):
            wait_s = target_offset - (time.time() - start)
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            tasks.append(asyncio.create_task(run_one(plan)))

        completed = await asyncio.gather(*tasks, return_exceptions=False)

    elapsed = time.time() - start
    stats = dag.compute_stats(completed, elapsed)

    total = selected_trace_rows
    success = int(stats.success_sessions)
    cascade_failed = int(stats.cascade_failed)
    step0_rejected = int(stats.rejected_at_step0)
    admitted = success + cascade_failed
    abd_total = (100.0 * cascade_failed / admitted) if admitted > 0 else 0.0
    cascade_rate = (100.0 * cascade_failed / total) if total > 0 else 0.0
    success_rate = (100.0 * success / total) if total > 0 else 0.0
    eff_gps = stats.effective_goodput_total / max(elapsed, 1e-6)
    p50 = percentile(stats.all_latencies, 0.50)
    p95 = percentile(stats.all_latencies, 0.95)

    return {
        "selected_trace_rows": total,
        "duration_s": round(elapsed, 3),
        "success": success,
        "success_rate": round(success_rate, 3),
        "abd": round(abd_total, 3),
        "abd_total": round(abd_total, 3),
        "cascade_failed": cascade_failed,
        "cascade_rate": round(cascade_rate, 3),
        "all_rejected": step0_rejected,
        "step0_rejected": step0_rejected,
        "effective_goodput_s": round(eff_gps, 6),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "client_rc": 0,
        "client_timed_out": 0,
        "error": "",
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def aggregate_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["gateway"]), str(row["window_id"]), str(row["scale"]))
        grouped[key].append(row)

    out: list[dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        bucket = grouped[key]
        gateway, window_id, scale = key
        window_label = str(bucket[0]["window_label"])
        record: dict[str, Any] = {
            "gateway": gateway,
            "window_id": window_id,
            "window_label": window_label,
            "scale": scale,
            "runs": len(bucket),
        }
        for field in NUMERIC_FOR_AGG:
            vals = [float(r[field]) for r in bucket]
            record[f"{field}_mean"] = round(sum(vals) / len(vals), 6)
        out.append(record)
    return out


def build_validation(
    trace_path: Path,
    trace_sha: str,
    windows: list[TraceWindow],
    scales: list[float],
    summary_rows: list[dict[str, Any]],
    agg_rows: list[dict[str, Any]],
    backend_mode: str,
) -> dict[str, Any]:
    errors: list[str] = []

    gateway_counts = Counter(str(r["gateway"]) for r in summary_rows)
    expected_gateways = ["ng", "static", "pp", "rajomon", "plangate_relaxed"]
    actual_gateways = sorted(gateway_counts.keys())

    if sorted(expected_gateways) != actual_gateways:
        errors.append(f"gateway_set_mismatch expected={expected_gateways} actual={actual_gateways}")

    window_scale_gateway_counts: dict[str, int] = {}
    for row in summary_rows:
        key = f"{row['window_id']}|{row['scale']}|{row['gateway']}"
        window_scale_gateway_counts[key] = window_scale_gateway_counts.get(key, 0) + 1

    repeats_ok = all(v == 3 for v in window_scale_gateway_counts.values())
    if not repeats_ok:
        errors.append("not_all_window_scale_gateway_cells_have_3_repeats")

    plangate_real_absent = all(str(r["gateway"]) != "plangate_real" for r in summary_rows)
    if not plangate_real_absent:
        errors.append("plangate_real_present")

    all_client_rc_zero = all(int(float(r.get("client_rc", 0))) == 0 for r in summary_rows)
    all_client_timed_out_zero = all(int(float(r.get("client_timed_out", 0))) == 0 for r in summary_rows)
    all_error_empty_or_zero = all(str(r.get("error", "")).strip() in ("", "0", "0.0") for r in summary_rows)

    selected_windows = [
        {
            "window_id": w.window_id,
            "window_label": w.window_label,
            "start_row": w.start_row,
            "end_row": w.end_row,
            "start_timestamp": w.start_ts,
            "end_timestamp": w.end_ts,
        }
        for w in windows
    ]

    no_raw_trace_in_artifact = True
    allowed_files = {
        "burstgpt_trace_replay_summary.csv",
        "burstgpt_trace_replay_agg.csv",
        "validation.json",
        "README_RESULT.md",
    }
    for p in ARTIFACT_DIR.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith("_tmp_"):
            continue
        if p.name not in allowed_files:
            no_raw_trace_in_artifact = False

    if not no_raw_trace_in_artifact:
        errors.append("unexpected_extra_files_in_artifact")

    return {
        "artifact": "burstgpt_trace_replay_v1",
        "source_trace_path": str(trace_path),
        "source_trace_sha256": trace_sha,
        "selected_windows": selected_windows,
        "scale_factors": scales,
        "row_count": len(summary_rows),
        "agg_row_count": len(agg_rows),
        "gateway_counts": dict(gateway_counts),
        "window_scale_gateway_counts": window_scale_gateway_counts,
        "gateways": expected_gateways,
        "plangate_real_absent": plangate_real_absent,
        "all_client_rc_zero": all_client_rc_zero,
        "all_client_timed_out_zero": all_client_timed_out_zero,
        "all_error_empty_or_zero": all_error_empty_or_zero,
        "no_raw_trace_in_artifact": no_raw_trace_in_artifact,
        "backend_mode": backend_mode,
        "errors": errors,
    }


def build_readme(trace_path: Path, trace_sha: str, backend_mode: str, windows: list[TraceWindow], scales: list[float]) -> str:
    window_lines = "\n".join(
        [
            f"- {w.window_id} ({w.window_label}): rows {w.start_row}-{w.end_row}, ts [{w.start_ts}, {w.end_ts}]"
            for w in windows
        ]
    )
    scale_str = ", ".join(str(s) for s in scales)

    return (
        "# BurstGPT Trace Replay Evidence\n\n"
        "This artifact is BurstGPT-shaped arrival replay evidence for PlanGate.\n"
        "BurstGPT provides arrival timing, burstiness, and token-size signal only.\n"
        "Session DAG/tool-step structure is generated by controlled PlanGate P&S/ReAct templates.\n"
        "This is not a real agent workflow trace experiment.\n"
        "This is not CloudLab/distributed-state evidence.\n"
        "Interpret this artifact strictly as workload-arrival realism evidence.\n\n"
        "## Source Trace\n\n"
        f"- path: {trace_path}\n"
        f"- sha256: {trace_sha}\n"
        f"- backend_mode: {backend_mode}\n\n"
        "## Windows\n\n"
        f"{window_lines}\n\n"
        "## Scale Factors\n\n"
        f"- {scale_str}\n\n"
        "## Claim Boundary\n\n"
        "Allowed claim:\n"
        "- PlanGate behavior is additionally tested under BurstGPT-shaped real LLM-serving arrival burstiness.\n\n"
        "Not allowed:\n"
        "- evaluated on real agent traces\n"
        "- BurstGPT proves real MCP workflow performance\n"
        "- distributed CloudLab evidence\n"
        "- universal dominance under all windows\n"
    )


def clean_artifact_dir() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for p in ARTIFACT_DIR.iterdir():
        if p.is_file():
            try:
                p.unlink()
            except PermissionError:
                # Windows may keep handles briefly; best-effort cleanup.
                pass


def remove_tmp_files() -> None:
    for p in ARTIFACT_DIR.glob("_tmp_*"):
        if p.is_file():
            try:
                p.unlink()
            except PermissionError:
                # Ignore locked temp logs; they are outside formal artifact outputs.
                pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    random.seed(args.seed)

    trace_path = pick_trace(args.trace)
    trace_sha = sha256_file(trace_path)
    rows = load_trace_rows(trace_path)
    windows = build_windows(rows, args.window_size)
    scales = [float(x) for x in args.scales]

    if args.dry_run:
        print(f"trace={trace_path}")
        print(f"trace_sha256={trace_sha}")
        print(f"window_size={args.window_size}")
        print(f"windows={[w.window_id for w in windows]}")
        print(f"scales={scales}")
        print(f"repeats={args.repeats}")
        print("gateways=" + ",".join(g.name for g in GATEWAYS))
        return 0

    clean_artifact_dir()
    binary = ensure_gateway_binary((ROOT_DIR / args.gateway_binary) if not Path(args.gateway_binary).is_absolute() else Path(args.gateway_binary))

    summary_rows: list[dict[str, Any]] = []

    backend_proc = None
    try:
        backend_proc = start_backend(args)

        for gw_idx, gateway in enumerate(GATEWAYS):
            gw_port = find_free_port()
            gw_url = f"http://127.0.0.1:{gw_port}"
            print(f"[burstgpt-replay] gateway={gateway.name} port={gw_port}")
            gw_proc = start_gateway(binary, args, gateway, gw_port)
            try:
                for window in windows:
                    for scale in scales:
                        for repeat in range(1, args.repeats + 1):
                            print(
                                f"  run gateway={gateway.name} window={window.window_id} scale={scale} repeat={repeat}/{args.repeats}"
                            )
                            row = {
                                "gateway": gateway.name,
                                "window_id": window.window_id,
                                "window_label": window.window_label,
                                "scale": str(scale),
                                "repeat": repeat,
                                "backend_mode": args.backend_mode,
                                "source_trace_sha256": trace_sha,
                            }
                            try:
                                metrics = asyncio.run(run_replay_cell(gw_url, window, scale, repeat, args))
                                row.update(metrics)
                            except Exception as exc:
                                row.update(
                                    {
                                        "selected_trace_rows": len(window.offsets),
                                        "duration_s": 0.0,
                                        "success": 0,
                                        "success_rate": 0.0,
                                        "abd": 0.0,
                                        "abd_total": 0.0,
                                        "cascade_failed": 0,
                                        "cascade_rate": 0.0,
                                        "all_rejected": 0,
                                        "step0_rejected": 0,
                                        "effective_goodput_s": 0.0,
                                        "p50_ms": 0.0,
                                        "p95_ms": 0.0,
                                        "client_rc": 1,
                                        "client_timed_out": 0,
                                        "error": str(exc),
                                    }
                                )
                            summary_rows.append(row)
            finally:
                stop_process(gw_proc)
                time.sleep(1)
    finally:
        stop_process(backend_proc)

    agg_rows = aggregate_summary(summary_rows)

    summary_path = ARTIFACT_DIR / "burstgpt_trace_replay_summary.csv"
    agg_path = ARTIFACT_DIR / "burstgpt_trace_replay_agg.csv"
    validation_path = ARTIFACT_DIR / "validation.json"
    readme_path = ARTIFACT_DIR / "README_RESULT.md"

    write_csv(summary_path, SUMMARY_COLUMNS, summary_rows)
    write_csv(agg_path, AGG_COLUMNS, agg_rows)

    # Drop temporary backend/gateway logs before artifact hygiene checks.
    remove_tmp_files()

    validation = build_validation(trace_path, trace_sha, windows, scales, summary_rows, agg_rows, args.backend_mode)
    validation_path.write_text(json.dumps(validation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    readme = build_readme(trace_path, trace_sha, args.backend_mode, windows, scales)
    readme_path.write_text(readme, encoding="utf-8")

    print(json.dumps(validation, indent=2, ensure_ascii=False))
    return 0 if not validation["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
