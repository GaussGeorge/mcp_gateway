#!/usr/bin/env python3
"""Extract cascade data from stdout.log files for bursty N=7."""
import os, re, statistics, math

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.path.join(ROOT, "results", "exp_bursty_C20_B30")

def parse_stdout(log_path):
    """Parse a single stdout.log and return metrics dict."""
    if not os.path.exists(log_path):
        return None
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    
    result = {}
    patterns = {
        "cascade_agents": r"级联浪费 Agent:\s*(\d+)",
        "cascade_steps": r"级联浪费步骤:\s*(\d+)",
        "success": r"SUCCESS:\s*(\d+)",
        "partial": r"PARTIAL:\s*(\d+)",
        "all_rejected": r"ALL_REJECTED:\s*(\d+)",
        "http_429": r"429 响应:\s*(\d+)",
        "p50": r"P50:\s*([0-9.]+)ms",
        "p95": r"P95:\s*([0-9.]+)ms",
        "elapsed": r"总耗时:\s*([0-9.]+)s",
        "agent_tokens": r"Agent Brain:\s*([0-9,]+)",
        "backend_tokens": r"Backend LLM:\s*([0-9,]+)",
    }
    
    for key, pat in patterns.items():
        m = re.search(pat, content)
        if m:
            val = m.group(1).replace(",", "")
            result[key] = float(val) if "." in val else int(val)
    return result

# Collect all data
for gw in ["ng", "plangate_real"]:
    print(f"\n=== {gw} ===")
    all_data = []
    gw_dir = os.path.join(BASE, gw)
    
    for run_idx in range(1, 10):
        run_dir = os.path.join(gw_dir, f"run{run_idx}")
        if not os.path.isdir(run_dir):
            continue
        
        # run1 may have multiple stdout files or a single one containing multiple runs
        log_path = os.path.join(run_dir, "stdout.log")
        if os.path.exists(log_path):
            stats = parse_stdout(log_path)
            if stats:
                all_data.append((run_idx, stats))
                print(f"  run{run_idx}: success={stats.get('success','?')}, "
                      f"partial={stats.get('partial','?')}, "
                      f"cascade_steps={stats.get('cascade_steps','?')}, "
                      f"cascade_agents={stats.get('cascade_agents','?')}, "
                      f"429={stats.get('http_429','?')}, "
                      f"p95={stats.get('p95','?')}ms")
        
        # Also check for sub-run logs (run1 may have run1_1, run1_2, run1_3)
        for sub in sorted(os.listdir(run_dir)):
            sub_path = os.path.join(run_dir, sub)
            if sub.endswith(".log") and sub != "stdout.log":
                stats = parse_stdout(sub_path)
                if stats and stats.get("success") is not None:
                    all_data.append((f"{run_idx}_{sub}", stats))
                    print(f"  run{run_idx}/{sub}: success={stats.get('success','?')}, "
                          f"partial={stats.get('partial','?')}, "
                          f"cascade_steps={stats.get('cascade_steps','?')}")
    
    # Summary
    cascade_steps = [d[1].get("cascade_steps", 0) for d in all_data if d[1].get("cascade_steps") is not None]
    cascade_agents = [d[1].get("cascade_agents", 0) for d in all_data if d[1].get("cascade_agents") is not None]
    partials = [d[1].get("partial", 0) for d in all_data if d[1].get("partial") is not None]
    
    print(f"\n  Total data points: {len(all_data)}")
    if cascade_steps:
        print(f"  Cascade steps: {statistics.mean(cascade_steps):.1f} +/- {statistics.stdev(cascade_steps):.1f}" if len(cascade_steps) > 1 else f"  Cascade steps: {cascade_steps}")
    if cascade_agents:
        print(f"  Cascade agents: {statistics.mean(cascade_agents):.1f} +/- {statistics.stdev(cascade_agents):.1f}" if len(cascade_agents) > 1 else f"  Cascade agents: {cascade_agents}")

# Also check run1 directory structure
print("\n=== Run1 directory listing ===")
for gw in ["ng", "plangate_real"]:
    run1_dir = os.path.join(BASE, gw, "run1")
    if os.path.isdir(run1_dir):
        files = sorted(os.listdir(run1_dir))
        print(f"  {gw}/run1/: {files}")
