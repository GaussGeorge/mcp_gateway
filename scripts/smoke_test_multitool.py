#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_test_multitool.py
验证强制多工具 prompt pool 真正触发工具调用。

通过标准：
  avg_steps_per_session >= 3.0
  zero_step_pct        < 20 %
  backend_tokens       > 0
  tools/call           > 0
  工具类别覆盖 ≥ 2 种

用法：
  python scripts/smoke_test_multitool.py              # 默认 10 agents, C=2
  python scripts/smoke_test_multitool.py --agents 20  # 更多 agents
  python scripts/smoke_test_multitool.py --dry-run    # 只打印配置
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
REACT = ROOT / "scripts" / "react_agent_client.py"
SERVER = ROOT / "mcp_server" / "server.py"
LOG_DIR = ROOT / "results" / "log" / "smoke_multitool"
RESULTS_DIR = ROOT / "results" / "smoke_multitool"

PASS_THRESHOLDS = {
    "avg_steps_per_session": 3.0,
    "zero_step_pct_max": 20.0,
    "backend_tokens_min": 1,
    "tool_categories_min": 2,
}


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ:
                    os.environ[k] = v


def build_gateway():
    bin_name = "gateway.exe" if sys.platform == "win32" else "gateway"
    bin_path = ROOT / bin_name
    print(f"  [BUILD] go build -o {bin_name} ./cmd/gateway")
    r = subprocess.run(
        ["go", "build", "-o", str(bin_path), "./cmd/gateway"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Build failed:\n{r.stderr}")
    return str(bin_path)


def start_backend(log_path):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, str(SERVER),
        "--port", "8080",
        "--mode", "real_llm",
        "--max-workers", "4",
    ]
    print(f"  [BACKEND] starting on :8080 (log: {log_path})")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                stdout=lf, stderr=lf)
    time.sleep(4)
    return proc


def start_gateway(binary, port, log_path):
    env = os.environ.copy()
    cmd = [
        binary,
        "--mode", "ng",
        "--port", str(port),
        "--backend", "http://127.0.0.1:8080",
    ]
    print(f"  [GATEWAY] ng on :{port}")
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, env=env, stdout=lf, stderr=lf)
    time.sleep(2)
    return proc


def run_agents(gateway_url, agents, concurrency, out_csv):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Agent LLM from env
    cmd = [
        sys.executable, str(REACT),
        "--gateway", gateway_url,
        "--agents", str(agents),
        "--concurrency", str(concurrency),
        "--max-steps", "12",
        "--budget", "1000",
        "--gateway-mode", "ng",
        "--output", str(out_csv),
    ]
    print(f"\n  [CLIENT] agents={agents} concurrency={concurrency}")
    r = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=600,
                       capture_output=False)
    return r.returncode == 0


def check_backend_tool_calls(log_path):
    if not os.path.exists(log_path):
        return 0
    count = 0
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "tools/call" in line:
                count += 1
    return count


def analyse_results(out_csv_agents, out_csv_steps):
    """Load both CSVs and compute pass/fail metrics."""
    def read_csv(p):
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    agents = read_csv(out_csv_agents)
    steps  = read_csv(out_csv_steps)

    if not agents:
        return None, {"error": "no agents CSV"}

    n = len(agents)
    total_steps = [int(r.get("total_steps", 0)) for r in agents]
    zero_steps  = sum(1 for s in total_steps if s == 0)
    avg_steps   = sum(total_steps) / n
    zero_pct    = 100 * zero_steps / n

    backend_tokens = sum(int(r.get("backend_llm_tokens", 0)) for r in agents)

    # Tool categories from steps
    tool_names = Counter(r.get("tool_name", "?") for r in steps)
    # Bucket into categories
    cats = set()
    for tn in tool_names:
        if tn in ("calculate",):
            cats.add("calculate")
        elif tn == "real_weather":
            cats.add("weather")
        elif tn == "real_web_search":
            cats.add("search")
        elif tn in ("deepseek_llm", "llm_reason"):
            cats.add("llm_tool")
        elif tn == "text_format":
            cats.add("text_format")

    metrics = {
        "n_agents": n,
        "avg_steps_per_session": round(avg_steps, 2),
        "zero_step_pct": round(zero_pct, 1),
        "backend_tokens": backend_tokens,
        "tool_categories": sorted(cats),
        "tool_distribution": dict(tool_names.most_common(8)),
        "total_tool_calls": len(steps),
    }
    return metrics, {}


def check_pass(metrics, backend_log_calls):
    failures = []
    if metrics["avg_steps_per_session"] < PASS_THRESHOLDS["avg_steps_per_session"]:
        failures.append(
            f"avg_steps={metrics['avg_steps_per_session']:.2f} < {PASS_THRESHOLDS['avg_steps_per_session']}"
        )
    if metrics["zero_step_pct"] > PASS_THRESHOLDS["zero_step_pct_max"]:
        failures.append(
            f"zero_step_pct={metrics['zero_step_pct']:.1f}% > {PASS_THRESHOLDS['zero_step_pct_max']}%"
        )
    if metrics["backend_tokens"] < PASS_THRESHOLDS["backend_tokens_min"]:
        failures.append(f"backend_tokens={metrics['backend_tokens']} == 0 (deepseek_llm never called)")
    if len(metrics["tool_categories"]) < PASS_THRESHOLDS["tool_categories_min"]:
        failures.append(
            f"tool_categories={metrics['tool_categories']} < {PASS_THRESHOLDS['tool_categories_min']} distinct"
        )
    if backend_log_calls == 0:
        failures.append("backend log: tools/call = 0 (gateway received no tool requests)")
    return failures


def main():
    load_env()

    parser = argparse.ArgumentParser(description="Smoke test: verify multi-tool prompt pool")
    parser.add_argument("--agents", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--port", type=int, default=9450)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("\n" + "="*65)
    print("  Multi-Tool Smoke Test")
    print(f"  Agents: {args.agents}  Concurrency: {args.concurrency}")
    print(f"  Gateway port: {args.port}")
    print(f"  Thresholds: {PASS_THRESHOLDS}")
    print("="*65)

    if args.dry_run:
        print("\n  [DRY RUN] — 不实际执行")
        print("  Pass thresholds:")
        for k, v in PASS_THRESHOLDS.items():
            print(f"    {k}: {v}")
        return 0

    backend_log = str(LOG_DIR / "backend_smoke.log")
    gw_log      = str(LOG_DIR / "gateway_smoke.log")
    out_agents  = str(RESULTS_DIR / "agents.csv")
    out_steps   = str(RESULTS_DIR / "steps.csv")

    # Use --output with .csv suffix: react_agent_client writes
    # {output} as steps.csv and {output.replace(".csv","_agents.csv")} as agents CSV
    out_base = str(RESULTS_DIR / "smoke.csv")

    backend_proc = None
    gw_proc = None
    try:
        binary = build_gateway()
        backend_proc = start_backend(backend_log)
        gw_proc = start_gateway(binary, args.port, gw_log)

        gateway_url = f"http://127.0.0.1:{args.port}"
        ok = run_agents(gateway_url, args.agents, args.concurrency, out_base)

        if not ok:
            print("\n  [ERROR] react_agent_client returned non-zero exit code")

        # Locate output CSVs
        # react_agent_client saves as {output} (steps) and {output}.replace(.csv, _agents.csv)
        steps_csv  = out_base                                         # smoke.csv
        agents_csv = out_base.replace(".csv", "_agents.csv")          # smoke_agents.csv
        if not os.path.exists(agents_csv):
            # fallback: maybe the file exists without .csv path
            agents_csv = str(RESULTS_DIR / "smoke_agents.csv")

        time.sleep(2)  # let files flush

        backend_calls = check_backend_tool_calls(backend_log)
        print(f"\n  [BACKEND LOG] tools/call entries: {backend_calls}")

        metrics, errs = analyse_results(agents_csv, steps_csv)

        if errs or metrics is None:
            print(f"\n  [FAIL] Cannot parse results: {errs}")
            return 1

        print("\n  [METRICS]")
        for k, v in metrics.items():
            print(f"    {k}: {v}")
        print(f"    backend_log_tool_calls: {backend_calls}")

        failures = check_pass(metrics, backend_calls)

        print("\n" + "="*65)
        if not failures:
            print("  ✓ SMOKE TEST PASSED — multi-tool prompts are working!")
            print("  ✓ Safe to proceed with B2 Bursty and B3 vLLM re-runs.")
            result = 0
        else:
            print("  ✗ SMOKE TEST FAILED")
            for f in failures:
                print(f"    FAIL: {f}")
            print("\n  Do NOT proceed with B2/B3 re-runs until smoke test passes.")
            print("  Suggestions:")
            print("    1. Check that SYSTEM_PROMPT forces tool calls")
            print("    2. Verify prompt wording starts with '使用 X 工具'")
            print("    3. Check backend is running in real_llm mode")
            print("    4. Run with --agents 20 for more data")
            result = 1
        print("="*65)
        return result

    finally:
        for proc in (gw_proc, backend_proc):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
