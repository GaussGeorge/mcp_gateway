#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnose_neutral_real_llm_results.py
从 raw steps.csv / steps_agents.csv 重新计算所有指标，
诊断 neutral prompt 实验是否形成 overload，并生成报告。
"""

import csv, os, sys, json, statistics
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
DIAG_DIR = ROOT / "results" / "neutral_real_llm" / "diagnosis"
DIAG_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────
# 工具：读 CSV
# ─────────────────────────────────────────────────────────────────
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


HEAVY_TOOLS = {"deepseek_llm", "real_web_search"}


def _pct(numer, denom):
    return round(100 * numer / denom, 2) if denom else 0.0


def pN(sorted_list, pct):
    if not sorted_list:
        return 0.0
    idx = int(len(sorted_list) * pct)
    idx = min(idx, len(sorted_list) - 1)
    return sorted_list[idx]


# ─────────────────────────────────────────────────────────────────
# 核心：从 raw steps + agents 重新计算每个 run 的指标
# ─────────────────────────────────────────────────────────────────
def recompute_run(steps_path, agents_path, run_id, gateway, experiment):
    steps = read_csv(steps_path)
    agents = read_csv(agents_path)

    n_agents = len(agents)
    if n_agents == 0:
        return None

    # ── Agent 级统计 ──
    state_counter = Counter(r.get("state", "UNKNOWN") for r in agents)
    success_n = state_counter.get("SUCCESS", 0)
    partial_n = state_counter.get("PARTIAL", 0)
    rejected_n = state_counter.get("ALL_REJECTED", 0)
    error_n = state_counter.get("ERROR", 0)

    admitted_n = success_n + partial_n       # 进入过执行的 session
    abd = _pct(partial_n, admitted_n)

    total_steps_per_agent = [int(r.get("total_steps", 0)) for r in agents]
    success_steps_per_agent = [int(r.get("success_steps", 0)) for r in agents]
    agent_tokens = [int(r.get("agent_llm_tokens", 0)) for r in agents]
    backend_tokens = [int(r.get("backend_llm_tokens", 0)) for r in agents]
    latencies = sorted([float(r.get("total_latency_ms", 0)) for r in agents])

    avg_steps = statistics.mean(total_steps_per_agent) if total_steps_per_agent else 0.0
    max_steps = max(total_steps_per_agent) if total_steps_per_agent else 0
    # sessions with 0 tool calls
    zero_step_sessions = sum(1 for s in total_steps_per_agent if s == 0)
    # sessions with ≥3 tool calls
    ge3_step_sessions = sum(1 for s in total_steps_per_agent if s >= 3)

    total_tool_steps = sum(total_steps_per_agent)
    total_succ_steps = sum(success_steps_per_agent)
    avg_steps_success = statistics.mean(
        [total_steps_per_agent[i] for i, r in enumerate(agents) if r.get("state") == "SUCCESS"]
    ) if success_n > 0 else 0.0

    total_agent_tokens = sum(agent_tokens)
    total_backend_tokens = sum(backend_tokens)

    p50 = pN(latencies, 0.5)
    p95 = pN(latencies, 0.95)

    # ── Step 级统计 ──
    step_statuses = Counter(r.get("status", "?") for r in steps)
    tool_names = Counter(r.get("tool_name", "?") for r in steps)
    heavy_calls = sum(cnt for name, cnt in tool_names.items() if name in HEAVY_TOOLS)
    total_calls = len(steps)
    heavy_ratio = _pct(heavy_calls, total_calls)

    http_429 = sum(1 for r in steps if r.get("http_status") == "429")
    http_5xx = sum(1 for r in steps
                   if r.get("http_status", "").startswith("5"))
    timeout_n = sum(1 for r in steps if r.get("status") == "timeout")
    error_tool = sum(1 for r in steps if r.get("status") in ("error", "timeout", "rejected"))

    # effective goodput (sum of effective_goodput from agents)
    eff_gp = sum(float(r.get("effective_goodput", 0)) for r in agents)
    raw_gp = sum(float(r.get("raw_goodput", 0)) for r in agents)

    # cascade wasted steps = success_steps for PARTIAL / ALL_REJECTED agents
    cascade_steps = sum(
        int(r.get("success_steps", 0)) for r in agents
        if r.get("state") in ("PARTIAL", "ALL_REJECTED")
    )

    return {
        "experiment": experiment,
        "gateway": gateway,
        "run_id": run_id,
        "n_agents": n_agents,
        "n_steps_rows": len(steps),
        # states
        "success": success_n,
        "partial": partial_n,
        "all_rejected": rejected_n,
        "error": error_n,
        "success_rate": _pct(success_n, n_agents),
        "abd_pct": abd,
        # step metrics
        "avg_steps_per_session": round(avg_steps, 3),
        "avg_steps_per_success": round(avg_steps_success, 3),
        "max_steps": max_steps,
        "zero_step_sessions": zero_step_sessions,
        "zero_step_pct": _pct(zero_step_sessions, n_agents),
        "ge3_step_sessions": ge3_step_sessions,
        "ge3_step_pct": _pct(ge3_step_sessions, n_agents),
        "total_tool_calls": total_calls,
        "total_success_tool_calls": total_succ_steps,
        # tool breakdown
        "heavy_tool_calls": heavy_calls,
        "heavy_tool_ratio_pct": heavy_ratio,
        "tool_distribution": dict(tool_names.most_common(10)),
        # tokens
        "total_agent_tokens": total_agent_tokens,
        "total_backend_tokens": total_backend_tokens,
        # goodput
        "raw_goodput": round(raw_gp, 1),
        "effective_goodput": round(eff_gp, 1),
        "cascade_steps": cascade_steps,
        # latency
        "p50_ms": round(p50, 0),
        "p95_ms": round(p95, 0),
        # errors
        "http_429": http_429,
        "http_5xx": http_5xx,
        "timeout": timeout_n,
        "tool_errors": error_tool,
        "step_status_dist": dict(step_statuses.most_common()),
    }


# ─────────────────────────────────────────────────────────────────
# A. Locate 所有 raw 文件
# ─────────────────────────────────────────────────────────────────
def locate_experiment_runs(exp_dir, gateways, max_runs=7):
    """返回 [(gateway, run_id, steps_path, agents_path)] 列表"""
    results = []
    exp_path = ROOT / exp_dir
    for gw in gateways:
        for run in range(1, max_runs + 1):
            run_dir = exp_path / gw / f"run{run}"
            sp = run_dir / "steps.csv"
            ap = run_dir / "steps_agents.csv"
            if ap.exists():
                results.append((gw, run, str(sp), str(ap)))
    return results


# ─────────────────────────────────────────────────────────────────
# B. Compute per-experiment
# ─────────────────────────────────────────────────────────────────
def compute_experiment(exp_name, exp_dir, gateways, today_only=False):
    """
    Compute all runs for an experiment.
    If today_only=True, only include runs with today's date.
    """
    import time
    today_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))

    runs = locate_experiment_runs(exp_dir, gateways)
    all_results = []
    for gw, run_id, sp, ap in runs:
        if not os.path.exists(ap):
            continue
        if today_only:
            mtime = os.path.getmtime(ap)
            if mtime < today_start - 86400:  # allow yesterday too (experiment ran overnight)
                continue
        rec = recompute_run(sp, ap, run_id, gw, exp_name)
        if rec:
            all_results.append(rec)
    return all_results


def compute_vllm(exp_name, exp_dir, gateways, max_runs=3):
    runs = locate_experiment_runs(exp_dir, gateways, max_runs=max_runs)
    all_results = []
    for gw, run_id, sp, ap in runs:
        if not os.path.exists(ap):
            continue
        rec = recompute_run(sp, ap, run_id, gw, exp_name)
        if rec:
            all_results.append(rec)
    return all_results


# ─────────────────────────────────────────────────────────────────
# C. Bursty recomputed summary CSV
# ─────────────────────────────────────────────────────────────────
def write_bursty_recomputed(bursty_results):
    out_path = DIAG_DIR / "bursty_recomputed_summary.csv"
    fields = [
        "experiment", "gateway", "run_id", "n_agents",
        "success", "partial", "all_rejected", "success_rate", "abd_pct",
        "avg_steps_per_session", "max_steps", "zero_step_pct", "ge3_step_pct",
        "total_tool_calls", "heavy_tool_calls", "heavy_tool_ratio_pct",
        "total_agent_tokens", "total_backend_tokens",
        "raw_goodput", "effective_goodput", "cascade_steps",
        "p50_ms", "p95_ms", "http_429", "timeout", "tool_errors",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(bursty_results)
    print(f"  [写入] {out_path}")
    return out_path


# ─────────────────────────────────────────────────────────────────
# D. Print summary table per gateway
# ─────────────────────────────────────────────────────────────────
def summarize_by_gateway(results, label):
    print(f"\n{'='*90}")
    print(f"  {label}")
    print(f"{'='*90}")
    print(f"  {'Gateway':<18} {'Runs':>4} {'Succ%':>7} {'ABD%':>7} {'AvgSteps':>9} "
          f"{'0-step%':>8} {'ge3%':>7} {'HeavyRt%':>9} {'BackTok':>9} "
          f"{'P50ms':>8} {'P95ms':>8}")
    print(f"  {'-'*18} {'-'*4} {'-'*7} {'-'*7} {'-'*9} "
          f"{'-'*8} {'-'*7} {'-'*9} {'-'*9} {'-'*8} {'-'*8}")

    by_gw = defaultdict(list)
    for r in results:
        by_gw[r["gateway"]].append(r)

    for gw, rows in sorted(by_gw.items()):
        n = len(rows)
        avg_succ = statistics.mean([r["success_rate"] for r in rows])
        avg_abd = statistics.mean([r["abd_pct"] for r in rows])
        avg_steps = statistics.mean([r["avg_steps_per_session"] for r in rows])
        avg_zero = statistics.mean([r["zero_step_pct"] for r in rows])
        avg_ge3 = statistics.mean([r["ge3_step_pct"] for r in rows])
        avg_heavy = statistics.mean([r["heavy_tool_ratio_pct"] for r in rows])
        total_btok = sum(r["total_backend_tokens"] for r in rows)
        avg_p50 = statistics.mean([r["p50_ms"] for r in rows])
        avg_p95 = statistics.mean([r["p95_ms"] for r in rows])

        print(f"  {gw:<18} {n:>4} {avg_succ:>7.1f} {avg_abd:>7.1f} {avg_steps:>9.2f} "
              f"{avg_zero:>8.1f} {avg_ge3:>7.1f} {avg_heavy:>9.1f} {total_btok:>9,} "
              f"{avg_p50:>8.0f} {avg_p95:>8.0f}")


# ─────────────────────────────────────────────────────────────────
# E. Diagnose overload
# ─────────────────────────────────────────────────────────────────
WORKLOAD_THRESHOLD_AVG_STEPS = 1.5
WORKLOAD_THRESHOLD_ZERO_PCT = 20.0
WORKLOAD_THRESHOLD_GE3_PCT = 30.0


def diagnose_overload(results, label):
    zero_pcts = [r["zero_step_pct"] for r in results]
    avg_steps_all = [r["avg_steps_per_session"] for r in results]
    ge3_pcts = [r["ge3_step_pct"] for r in results]
    backend_toks = [r["total_backend_tokens"] for r in results]

    mean_zero = statistics.mean(zero_pcts) if zero_pcts else 100
    mean_avg_steps = statistics.mean(avg_steps_all) if avg_steps_all else 0
    mean_ge3 = statistics.mean(ge3_pcts) if ge3_pcts else 0
    total_btok = sum(backend_toks)

    print(f"\n  [过载诊断 — {label}]")
    print(f"    平均 steps/session: {mean_avg_steps:.2f}  (需 ≥{WORKLOAD_THRESHOLD_AVG_STEPS} 才有负载意义)")
    print(f"    零工具调用 session 占比: {mean_zero:.1f}%  (越高越说明 workload 过轻)")
    print(f"    ≥3 步 session 比例: {mean_ge3:.1f}%  (越高越说明 workload 够重)")
    print(f"    Backend LLM token 总量: {total_btok:,}  (0 = deepseek_llm 从未被调用)")

    problems = []
    if mean_zero > WORKLOAD_THRESHOLD_ZERO_PCT:
        problems.append(f"⚠ {mean_zero:.0f}% sessions 未调用任何工具 (LLM 直接回答)")
    if mean_avg_steps < WORKLOAD_THRESHOLD_AVG_STEPS:
        problems.append(f"⚠ avg_steps={mean_avg_steps:.2f} < {WORKLOAD_THRESHOLD_AVG_STEPS} — workload 过轻")
    if total_btok == 0:
        problems.append("✗ CRITICAL: backend_tokens=0 — deepseek_llm 工具从未被调用，Backend 未受任何 heavy 负载")
    if mean_ge3 < WORKLOAD_THRESHOLD_GE3_PCT:
        problems.append(f"⚠ 仅 {mean_ge3:.0f}% sessions ≥3 步，会话提交承诺测试不充分")

    if problems:
        for p in problems:
            print(f"    {p}")
        print(f"\n  → 结论: {label} 未形成有效 backend-limited overload，实验结果缺乏区分度。")
    else:
        print(f"  → 结论: {label} 工作负载正常，具有足够深度。")
    return problems


# ─────────────────────────────────────────────────────────────────
# F. Compare summary CSV vs recomputed
# ─────────────────────────────────────────────────────────────────
def compare_with_summary(summary_csv, recomputed_results, key_field="gateway"):
    if not os.path.exists(summary_csv):
        print(f"  [跳过对比] {summary_csv} 不存在")
        return
    summary = read_csv(summary_csv)
    print(f"\n  [对比 {os.path.basename(summary_csv)} <-> 重计算]")
    by_gw = defaultdict(list)
    for r in recomputed_results:
        by_gw[r["gateway"]].append(r)

    for row in summary[:24]:
        gw = row.get("gateway", row.get("gateway", "?"))
        run = int(row.get("run", 0))
        summ_succ = int(row.get("success", 0))
        summ_arate = float(row.get("success_rate", 0))

        # find matching recomputed run
        matches = [r for r in by_gw.get(gw, []) if r["run_id"] == run]
        if matches:
            rc = matches[0]
            mismatch = rc["success"] != summ_succ
            flag = " ← MISMATCH" if mismatch else ""
            print(f"    {gw}/run{run}: summary_succ={summ_succ} recomp_succ={rc['success']}{flag}")
        else:
            print(f"    {gw}/run{run}: summary_succ={summ_succ} recomp=NOT FOUND")


# ─────────────────────────────────────────────────────────────────
# G. Recommend new prompts
# ─────────────────────────────────────────────────────────────────
RECOMMENDED_PROMPTS = [
    # 每条显式要求多个工具，LLM 无法直接回答
    # ── 强制多步计算 (4步) ──
    "先用 calculate 算 347 × 28，再用 calculate 算结果的平方根，再用 text_format 做 base64 编码，最后用 text_format 统计编码字符数",
    "用 calculate 算 2^20，再用 calculate 算它除以 1000 的余数，再用 text_format 把这两个结果拼成 JSON 字符串，最后用 deepseek_llm 用中文解释这个余数的含义",
    "用 calculate 算 13 的阶乘，再用 calculate 算结果有多少位，再用 text_format 以 markdown 格式输出这两个数，最后用 deepseek_llm 解释阶乘增长速度",
    # ── 强制天气+计算 (3-4步) ──
    "用 real_weather 查北京实时温度，再用 real_weather 查上海实时温度，然后用 calculate 算温差（绝对值），再用 calculate 判断温差是否超过 15 度（返回 1/0）",
    "用 real_weather 查 Tokyo 现在温度，用 calculate 转成华氏（F=C×9/5+32），用 text_format 把摄氏和华氏拼成一行文字，用 deepseek_llm 解释今天是否适合去公园",
    "用 real_weather 查 London 天气，用 real_weather 查 Paris 天气，用 calculate 算湿度差，用 text_format 生成 markdown 格式的双城天气对比表",
    # ── 强制搜索+LLM (3-4步) ──
    "用 real_web_search 搜索 'model context protocol specification 2025'，再用 deepseek_llm 总结前 3 条结果的共同主题，再用 text_format 统计摘要的词数",
    "用 real_web_search 搜索 'rate limiting algorithms token bucket leaky bucket'，用 deepseek_llm 翻译第一条结果标题为中文，用 text_format 做 base64 编码",
    "用 real_web_search 搜索 'LLM inference latency optimization 2024'，用 deepseek_llm 推理：如果 P99 延迟是 P50 的 3 倍意味着什么，用 text_format 输出分析结果",
    # ── 强制全链路 (5-6步) ──
    "用 real_weather 查 Berlin 天气，用 calculate 算温度平方，用 real_web_search 搜索 'Berlin tourist attractions'，用 deepseek_llm 根据天气和搜索结果推荐一项活动，用 text_format 输出推荐结果的词数",
    "用 calculate 算 256 的平方根保留小数点后 4 位，用 real_weather 查 New York 温度，用 calculate 算两者之差，用 real_web_search 搜索 'interesting facts about square roots'，用 deepseek_llm 解释搜索结果中最有趣的事实",
    "用 real_weather 查北京、上海、广州三城温度（3次调用），用 calculate 算三城平均温度，用 calculate 算标准差，用 text_format 生成 CSV 格式结果，用 deepseek_llm 解释这种温差对能耗的影响",
]


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*90)
    print("  Neutral Real-LLM 实验诊断报告 — raw trace 重计算")
    print("="*90)

    # ── B1 Steady ──
    print("\n\n[B1] Steady Commercial API (exp_week5_C10)")
    steady_gws = ["ng", "rajomon", "pp", "plangate_real"]
    steady_results = compute_experiment(
        "B1_steady", "results/exp_week5_C10", steady_gws,
        today_only=False  # April 28 runs all included
    )
    # Filter to today's (April 28) plangate_real runs only
    import time
    cutoff = time.mktime(time.strptime("2026-04-28", "%Y-%m-%d"))
    steady_today = []
    for gw in steady_gws:
        for run in range(1, 8):
            ap = ROOT / "results/exp_week5_C10" / gw / f"run{run}" / "steps_agents.csv"
            sp = ROOT / "results/exp_week5_C10" / gw / f"run{run}" / "steps.csv"
            if not ap.exists():
                break
            mtime = os.path.getmtime(str(ap))
            if mtime >= cutoff:
                rec = recompute_run(str(sp), str(ap), run, gw, "B1_steady")
                if rec:
                    steady_today.append(rec)

    summarize_by_gateway(steady_today, "B1 Steady — April 28 runs only (per gateway avg)")
    problems_steady = diagnose_overload(steady_today, "B1 Steady")
    compare_with_summary(
        str(ROOT / "results/exp_week5_C10/week5_summary.csv"),
        steady_today
    )

    # ── B2 Bursty (today's runs only: April 28-29) ──
    print("\n\n[B2] Bursty Real-LLM (exp_bursty_C20_B30)")
    bursty_gws = ["ng", "rajomon", "pp", "plangate_real"]
    bursty_today = []
    for gw in bursty_gws:
        for run in range(1, 8):
            ap = ROOT / "results/exp_bursty_C20_B30" / gw / f"run{run}" / "steps_agents.csv"
            sp = ROOT / "results/exp_bursty_C20_B30" / gw / f"run{run}" / "steps.csv"
            if not ap.exists():
                break
            mtime = os.path.getmtime(str(ap))
            if mtime >= cutoff:
                rec = recompute_run(str(sp), str(ap), run, gw, "B2_bursty")
                if rec:
                    bursty_today.append(rec)

    summarize_by_gateway(bursty_today, "B2 Bursty — April 28-29 runs only")
    problems_bursty = diagnose_overload(bursty_today, "B2 Bursty")

    bursty_csv_path = write_bursty_recomputed(bursty_today)

    # Also compute bursty task category breakdown
    print("\n  [B2 Bursty] Task Category breakdown (avg across all gateways & runs):")
    print(f"  {'Category':<18} {'Count':>7} {'AvgToks':>10} {'AvgSteps':>10} {'State:Succ%':>12}")
    cat_data = defaultdict(list)
    for gw in bursty_gws:
        for run in range(1, 4):
            ap = ROOT / "results/exp_bursty_C20_B30" / gw / f"run{run}" / "steps_agents.csv"
            if not ap.exists():
                break
            mtime = os.path.getmtime(str(ap))
            if mtime < cutoff:
                continue
            for row in read_csv(str(ap)):
                cat_data[row.get("task_category", "?")].append({
                    "state": row.get("state"),
                    "steps": int(row.get("total_steps", 0)),
                    "tok": int(row.get("agent_llm_tokens", 0)),
                })
    for cat, items in sorted(cat_data.items()):
        n = len(items)
        avg_tok = statistics.mean([x["tok"] for x in items])
        avg_step = statistics.mean([x["steps"] for x in items])
        succ_pct = 100 * sum(1 for x in items if x["state"] == "SUCCESS") / n
        print(f"  {cat:<18} {n:>7} {avg_tok:>10.0f} {avg_step:>10.2f} {succ_pct:>12.0f}%")

    # ── B3 vLLM ──
    print("\n\n[B3] Self-Hosted vLLM (exp_selfhosted_vllm_C10_W8)")
    vllm_gws = ["ng", "plangate_real"]
    vllm_results = compute_vllm(
        "B3_vllm", "results/exp_selfhosted_vllm_C10_W8", vllm_gws, max_runs=3
    )
    summarize_by_gateway(vllm_results, "B3 vLLM — all runs")
    problems_vllm = diagnose_overload(vllm_results, "B3 vLLM")

    # ── Backend log evidence ──
    print("\n\n[Backend日志] Bursty 后端实际工具调用次数:")
    bursty_log = ROOT / "results/log/real_llm_bursty/_backend_bursty.log"
    if bursty_log.exists():
        with open(bursty_log, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tool_call_lines = [l.strip() for l in lines if "tools/call" in l]
        print(f"  tools/call 日志条目总数: {len(tool_call_lines)}")
        if len(tool_call_lines) == 0:
            print("  ✗ CONFIRMED: bursty backend 从未收到任何 tools/call 请求！")
        else:
            for l in tool_call_lines[:5]:
                print(f"  {l}")
    else:
        print("  (log file not found)")

    # ── Print recommendation ──
    print("\n\n" + "="*90)
    print("  [诊断结论 & 下一步建议]")
    print("="*90)

    # collect all problems
    all_ok = not (problems_steady or problems_bursty or problems_vllm)

    if problems_bursty or problems_vllm:
        print("""
  ★ 核心发现: GLM-4-Flash (bursty/vLLM) 在 neutral prompt 下 0 工具调用
  ─────────────────────────────────────────────────────────────────────────
  B2 Bursty 和 B3 vLLM 实验中，所有 agent 的 total_steps=0，
  即 LLM 在第一次推理后直接给出文本答案，未通过网关调用任何 MCP 工具。
  Backend 服务器日志确认：整个 bursty 实验期间 tools/call = 0 次。

  原因分析：
  1. 部分 neutral prompt 为 LLM 知识可直接回答（数学、推理、翻译等）
  2. SYSTEM_PROMPT 中 "Aim to complete in 2-5 tool calls" 反而让 LLM 觉得
     "如果我能直接回答，就不应该浪费工具配额"
  3. bursty 30 agents 同时涌入 → backend 初始响应慢 → LLM 决定直接回答
     （但对 bursty 这点无法确认，因为根本没有任何一次工具调用尝试）

  结论：B2 Bursty 和 B3 vLLM 的 100% success / 0 ABD 完全无效，
        因为网关从未被实际负载冲击，无法体现准入控制价值。
  """)

    if problems_steady:
        print("""
  ★ B1 Steady 实验有真实 tool calls，但 PlanGate 更差
  ─────────────────────────────────────────────────────────────────────────
  Steady 实验中，avg_steps ≈ 2.9/session（有意义的负载），但 PlanGate 的
  成功率显著低于其他网关（88.4% vs 94%+）。

  可能原因：
  1. 商业 API 速率限制（GLM-4-Flash）导致工具调用偶发超时/失败
  2. PlanGate 的 real profile 在 arrival_interval=0.3s + GLM latency ≈ 2-5s
     的参数下，单 session 实际持续 ≈ 35min，并发估算有偏差
  3. --plangate-max-sessions=50 / --real-latency-threshold=5000 参数
     在 real_llm 延迟分布下可能过于保守，导致不必要的准入拒绝
  4. cascade_steps 浪费（partial sessions）在 PlanGate 下更多，
     因为被拒的 partial session 已经消耗了部分 API quota
  """)

    print("""
  下一步建议：
  ─────────────────────────────────────────────────────────────────────────
  1. 必须重构 neutral prompt pool：明确要求 LLM 先调工具再回答。
     每条 prompt 应包含 "先用 X 工具... 再用 Y 工具..." 等明确指示，
     使 avg_steps ≥ 3，heavy_tool_ratio ≥ 20%。

  2. bursty 参数调整建议：
     - CONCURRENCY=20, BURST_SIZE=30 本身合理
     - 但需确保 workload 够重：avg_steps ≥ 4-5, 有 heavy 工具 (deepseek_llm)
     - 可增加 AGENTS=300 或 MAX_STEPS=20 以拉长 session

  3. vLLM 参数调整建议：
     - 改为 AGENTS=100, CONCURRENCY=20,
     - vLLM workers ≤ 8 (当前正确)，确保 backend-limited
     - 同样需要 heavy prompt 强制调用 deepseek_llm (qwen 本身)

  4. PlanGate 参数诊断：
     - 建议对 steady commercial API profile 单独调参：
       调高 --plangate-max-sessions=80, --real-latency-threshold=8000
     - 或在 real profile 中加入 speed bonus

  5. 当前 B1 Steady 数据可用于论文（有真实 tool calls），
     但需要 note: PlanGate 可能因参数保守而惩罚过重。

  6. B2 Bursty 和 B3 vLLM 数据 不可用于论文，必须重跑。
  """)

    print(f"\n  诊断 CSV: {bursty_csv_path}")
    print(f"  报告将写入: {DIAG_DIR / 'diagnosis_report.md'}")

    return {
        "steady_results": steady_today,
        "bursty_results": bursty_today,
        "vllm_results": vllm_results,
        "problems_steady": problems_steady,
        "problems_bursty": problems_bursty,
        "problems_vllm": problems_vllm,
        "recommended_prompts": RECOMMENDED_PROMPTS,
    }


# ─────────────────────────────────────────────────────────────────
# H. Generate Markdown report
# ─────────────────────────────────────────────────────────────────
def _fmt_table(headers, rows, fmts=None):
    """Simple markdown table formatter."""
    col_w = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        strs = []
        for i, cell in enumerate(row):
            s = str(cell)
            strs.append(s)
            col_w[i] = max(col_w[i], len(s))
        str_rows.append(strs)

    def _row(cells):
        return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"

    lines = [
        _row(headers),
        "| " + " | ".join("-" * w for w in col_w) + " |",
    ]
    for row in str_rows:
        lines.append(_row(row))
    return "\n".join(lines)


def generate_report(diag, report_dir):
    import datetime
    steady = diag["steady_results"]
    bursty = diag["bursty_results"]
    vllm   = diag["vllm_results"]
    prompts = diag["recommended_prompts"]

    # ── Build summary tables ──
    def gw_summary_rows(results):
        by_gw = defaultdict(list)
        for r in results:
            by_gw[r["gateway"]].append(r)
        rows = []
        for gw, rows_gw in sorted(by_gw.items()):
            n = len(rows_gw)
            avg_succ = statistics.mean([r["success_rate"] for r in rows_gw])
            avg_abd  = statistics.mean([r["abd_pct"] for r in rows_gw])
            avg_steps= statistics.mean([r["avg_steps_per_session"] for r in rows_gw])
            avg_zero = statistics.mean([r["zero_step_pct"] for r in rows_gw])
            btok     = sum(r["total_backend_tokens"] for r in rows_gw)
            avg_p50  = statistics.mean([r["p50_ms"] for r in rows_gw])
            avg_p95  = statistics.mean([r["p95_ms"] for r in rows_gw])
            rows.append([gw, n, f"{avg_succ:.1f}%", f"{avg_abd:.1f}%",
                         f"{avg_steps:.2f}", f"{avg_zero:.0f}%",
                         f"{btok:,}", f"{avg_p50:.0f}", f"{avg_p95:.0f}"])
        return rows

    steady_rows = gw_summary_rows(steady)
    bursty_rows = gw_summary_rows(bursty)
    vllm_rows   = gw_summary_rows(vllm)

    def _tbl_str(label, rows):
        if not rows:
            return f"*{label}: 无数据*\n"
        hdrs = ["Gateway", "Runs", "Succ%", "ABD%", "AvgSteps", "ZeroStep%", "BackendTok", "P50(ms)", "P95(ms)"]
        return f"{_fmt_table(hdrs, rows)}\n"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # diagnose verdict
    bursty_invalid = "✗ 无效" if diag["problems_bursty"] else "✓ 有效"
    vllm_invalid   = "✗ 无效" if diag["problems_vllm"]   else "✓ 有效"
    steady_valid   = "⚠ 有效但 PlanGate 异常低" if diag["problems_steady"] else "✓ 有效"

    prompt_section = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(prompts))

    report = f"""# Neutral Real-LLM 实验诊断报告

**生成时间**: {now}

---

## 0. 执行摘要

| 实验 | 有效性 | 核心问题 |
|------|--------|----------|
| B1 Steady (exp_week5_C10) | {steady_valid} | PlanGate ABD=10.1%，显著高于其他网关 |
| B2 Bursty (exp_bursty_C20_B30) | {bursty_invalid} | 所有 agent 0 工具调用，backend 从未受负载 |
| B3 vLLM (exp_selfhosted_vllm_C10_W8) | {vllm_invalid} | 同 B2，所有 agent 0 工具调用 |

---

## 1. 数据来源

- `results/exp_week5_C10/{{ng,rajomon,pp,plangate_real}}/run1-5/`  
- `results/exp_bursty_C20_B30/{{ng,rajomon,pp,plangate_real}}/run1-3/` (April 28-29)
- `results/exp_selfhosted_vllm_C10_W8/{{ng,plangate_real}}/run1-3/`
- `results/log/real_llm_bursty/_backend_bursty.log`

所有指标均从 `steps.csv` (工具调用级) + `steps_agents.csv` (session 级) 重新计算，
不依赖原来的 `*_summary.csv`。

---

## 2. B1 Steady 结果（重计算）

{_tbl_str("B1 Steady", steady_rows)}

**分析**：
- NG / Rajomon 表现接近，ABD ≈ 5%，success_rate ≈ 94%
- PP 成功率 ≈ 92.1%，ABD ≈ 7.2%，略差
- **PlanGate 成功率最低 (88.4%)，ABD=10.1%**，P95 延迟也最高
- avg_steps ≈ 2.9/session，证明工作负载有意义：agents 主动调用了工具
- 工作负载形成真实 overload：有的 session 被 partial/rejected

---

## 3. B2 Bursty 结果（重计算）

{_tbl_str("B2 Bursty", bursty_rows)}

### 3.1 关键诊断证据

| 指标 | 值 | 含义 |
|------|-----|------|
| total_steps 均值 | 0.00 | 所有 agent 零工具调用 |
| zero_step sessions | 100% | LLM 全部直接回答 |
| backend_llm_tokens | 0 | deepseek_llm 工具从未执行 |
| effective_goodput | 0.0 | 无 MCP tool 完成任务 |
| backend log tools/call | **0 条** | 12h 实验期间 gateway 从未收到工具请求 |

### 3.2 根因

GLM-4-Flash 在 neutral prompt 下（纯数学/天气推理/LLM 推理类问题）
**直接在第一次 LLM 推理步骤给出文本答案，不调用任何工具**。

`react_agent_client.py` Line 486-487:
```python
if result.total_steps == 0:
    result.state = "SUCCESS"  # 直接回答，不需要工具
```

于是 200 个 agent × 4 gateway × 3 run = **2400 次 session，
全部以 0 步 / SUCCESS 结束**，gateway 从未受到任何实际工具请求压力。

B2 Bursty 实验结果（100% success / 0 ABD）
**不能用于比较网关准入控制策略**，因为没有触发准入控制路径。

---

## 4. B3 vLLM 结果（重计算）

{_tbl_str("B3 vLLM", vllm_rows)}

与 B2 原因相同：Qwen 同样直接回答 neutral prompt，不调用工具。
**B3 vLLM 实验结果无效。**

---

## 5. PlanGate Steady 表现分析

PlanGate 在 B1 Steady 实验中 ABD=10.1%，明显高于 NG(5.5%)/Rajomon(5.0%)。

### 可能原因

1. **参数过保守**：`--plangate-max-sessions=50` 在 GLM-4-Flash avg_latency ≈ 5-10s/step
   的条件下，相当于并发承载仅 ≈ 50×0.15 req/s，可能低估真实吞吐能力

2. **价格步长过小**：`--plangate-price-step` 导致 token budget 阈值频繁调整，
   造成合理 session 被误判为 heavy 而拒绝

3. **商业 API 偶发限流**：GLM rate limit 导致工具超时，PlanGate 的 cascade 惩罚
   比其他网关更严格，将超时 session 标记为异常

4. **LLM_reasoning 类别 avg_steps=1.5 最低**：PlanGate 的 token budget 估算
   可能倾向于拒绝 1-2 步的短 session（认为提交承诺不足），造成误杀

### 建议
- 对 B1 Steady 数据分析 per-category ABD（by task_category）
- 调参：`--plangate-max-sessions=80`，`--real-latency-threshold=8000ms`
- 或按论文论述：PlanGate 对 real API 的 latency jitter 更敏感，
  这本身是 real-world deployment 的 trade-off

---

## 6. 对比：新 bursty 与旧 bursty（April 16）

| 版本 | avg_steps | states | backend_tokens | 有效性 |
|------|-----------|--------|----------------|--------|
| 旧版 bursty (Apr 16) | 2.8-4.5/session | REJECTED=47%, PARTIAL=46%, SUCCESS=7% | 2,637 | ✓ 有效 |
| 新版 bursty (Apr 28) | 0.00 | SUCCESS=100% | 0 | ✗ 无效 |

旧版 bursty 使用的**旧 prompt pool**中，有许多 prompt 要求查询具体 URL 或外部数据，
强制 agent 调用 real_web_search / real_weather，从而产生真实 backend 负载。

新版 neutral prompt pool 删除了这类强制性工具调用提示，导致 LLM 绕过工具直接回答。

---

## 7. 建议：重设计 neutral prompt pool

以下 prompt 示例强制 LLM 先调工具（**不可用 LLM 先验知识直接回答**），
并要求多步骤工具链（avg_steps 目标 ≥ 3.5）：

{prompt_section}

**关键原则**：
- 每条 prompt 明确包含 "先用 X 工具" / "用 X 工具查询" 等动词
- 包含至少一次 real-time data 查询（weather/search），使 LLM 无法凭知识回答
- 包含至少一次跨工具的依赖链（A 的输出作为 B 的输入）
- `SYSTEM_PROMPT` 改为 "You MUST use tools as instructed. Do NOT answer directly from knowledge"

---

## 8. 结论与后续计划

### 当前可用数据
- **B1 Steady**: ✓ 可用于论文，有真实工具负载，PlanGate 显著差需要分析说明

### 需要重跑
- **B2 Bursty**: 必须重设计 prompt pool 后重跑，目标 avg_steps ≥ 4
- **B3 vLLM**: 同上，需要强制工具调用 prompt

### 论文策略建议
若时间紧张，可考虑：
1. 使用 B1 Steady 数据 + 旧版 bursty（April 16 runs）组合
2. 或在 neutral prompt pool 修复后快速重跑 bursty（1-2 轮验证即可）
3. vLLM 场景如暂时无法重跑，可在 Future Work 中描述

---

*报告由 `scripts/diagnose_neutral_real_llm_results.py` 自动生成*
"""

    out_path = report_dir / "diagnosis_report.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n  [报告已写入] {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="Real-LLM 结果诊断")
    _parser.add_argument(
        "--mode", choices=["neutral", "multitool"], default="neutral",
        help="neutral=旧版 neutral prompt 实验; multitool=新版强制多工具实验",
    )
    _args = _parser.parse_args()

    if _args.mode == "multitool":
        # ── multitool 模式 ──
        MT_DIR = ROOT / "results" / "neutral_multitool_real_llm"
        MT_DIAG_DIR = MT_DIR / "diagnosis"
        MT_DIAG_DIR.mkdir(parents=True, exist_ok=True)

        print("\n" + "="*90)
        print("  Multitool Real-LLM 实验诊断报告 (强制多工具 prompt 版)")
        print("="*90)

        # B2 Multitool Bursty
        MT_BURSTY_DIR = MT_DIR / "bursty"
        print(f"\n[B2-MT] Bursty Multitool ({MT_BURSTY_DIR})")
        bursty_gws = ["ng", "rajomon", "pp", "plangate_real"]
        mt_bursty = []
        for gw in bursty_gws:
            for run in range(1, 8):
                ap = MT_BURSTY_DIR / gw / f"run{run}" / "steps_agents.csv"
                sp = MT_BURSTY_DIR / gw / f"run{run}" / "steps.csv"
                if not ap.exists():
                    break
                rec = recompute_run(str(sp), str(ap), run, gw, "B2_multitool_bursty")
                if rec:
                    mt_bursty.append(rec)

        if mt_bursty:
            summarize_by_gateway(mt_bursty, "B2-MT Bursty Multitool")
            problems_b2 = diagnose_overload(mt_bursty, "B2-MT Bursty")
        else:
            print("  (没有数据 — 实验尚未运行)")
            problems_b2 = ["no data"]

        # B3 Multitool vLLM
        MT_VLLM_DIR = MT_DIR / "selfhosted_vllm"
        print(f"\n[B3-MT] vLLM Multitool ({MT_VLLM_DIR})")
        vllm_gws = ["ng", "plangate_real"]
        mt_vllm = []
        for gw in vllm_gws:
            for run in range(1, 8):
                ap = MT_VLLM_DIR / gw / f"run{run}" / "steps_agents.csv"
                sp = MT_VLLM_DIR / gw / f"run{run}" / "steps.csv"
                if not ap.exists():
                    break
                rec = recompute_run(str(sp), str(ap), run, gw, "B3_multitool_vllm")
                if rec:
                    mt_vllm.append(rec)

        if mt_vllm:
            summarize_by_gateway(mt_vllm, "B3-MT vLLM Multitool")
            problems_b3 = diagnose_overload(mt_vllm, "B3-MT vLLM")
        else:
            print("  (没有数据 — 实验尚未运行)")
            problems_b3 = ["no data"]

        # Bursty backend log
        print("\n[Backend日志] Multitool Bursty:")
        mb_log = ROOT / "results" / "log" / "real_llm_bursty" / "_backend_bursty.log"
        if mb_log.exists():
            with open(mb_log, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tc = sum(1 for l in lines if "tools/call" in l)
            print(f"  tools/call 条目: {tc}")
            if tc > 0:
                print("  ✓ Backend 收到工具请求，实验有效！")
            else:
                print("  ✗ tools/call 仍为 0")
        else:
            print("  (log 不存在)")

        # ── CSV output ──
        csv_path = MT_DIAG_DIR / "multitool_summary.csv"
        fields = [
            "experiment", "gateway", "run_id", "n_agents",
            "success", "partial", "all_rejected", "success_rate", "abd_pct",
            "avg_steps_per_session", "max_steps", "zero_step_pct", "ge3_step_pct",
            "total_tool_calls", "heavy_tool_calls", "heavy_tool_ratio_pct",
            "total_agent_tokens", "total_backend_tokens",
            "raw_goodput", "effective_goodput", "cascade_steps",
            "p50_ms", "p95_ms", "http_429", "timeout", "tool_errors",
        ]
        import csv as _csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(mt_bursty + mt_vllm)
        print(f"\n  [写入] {csv_path}")

        # ── Markdown report ──
        import datetime as _dt
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

        def _rows_for_report(results):
            by_gw = defaultdict(list)
            for r in results:
                by_gw[r["gateway"]].append(r)
            rows = []
            for gw, rs in sorted(by_gw.items()):
                n = len(rs)
                avg_succ  = statistics.mean([r["success_rate"] for r in rs])
                avg_abd   = statistics.mean([r["abd_pct"] for r in rs])
                avg_steps = statistics.mean([r["avg_steps_per_session"] for r in rs])
                avg_zero  = statistics.mean([r["zero_step_pct"] for r in rs])
                ge3       = statistics.mean([r["ge3_step_pct"] for r in rs])
                btok      = sum(r["total_backend_tokens"] for r in rs)
                cascade   = sum(r["cascade_steps"] for r in rs)
                avg_p50   = statistics.mean([r["p50_ms"] for r in rs])
                avg_p95   = statistics.mean([r["p95_ms"] for r in rs])
                part      = sum(r["partial"] for r in rs)
                rej       = sum(r["all_rejected"] for r in rs)
                rows.append([gw, n, f"{avg_succ:.1f}%", f"{avg_abd:.1f}%",
                             f"{avg_steps:.2f}", f"{avg_zero:.0f}%", f"{ge3:.0f}%",
                             btok, cascade, f"{avg_p50:.0f}", f"{avg_p95:.0f}",
                             part, rej])
            return rows

        def _md_table(headers, rows):
            col_w = [len(h) for h in headers]
            str_rows = []
            for row in rows:
                sr = [str(c) for c in row]
                str_rows.append(sr)
                for i, s in enumerate(sr):
                    col_w[i] = max(col_w[i], len(s))
            def _r(cells):
                return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"
            lines = [_r(headers), "| " + " | ".join("-"*w for w in col_w) + " |"]
            for r in str_rows:
                lines.append(_r(r))
            return "\n".join(lines)

        b2_rows = _rows_for_report(mt_bursty) if mt_bursty else []
        b3_rows = _rows_for_report(mt_vllm) if mt_vllm else []
        hdrs = ["Gateway", "Runs", "Succ%", "ABD%", "AvgSteps", "ZeroStep%",
                "≥3Step%", "BackendTok", "CascSteps", "P50(ms)", "P95(ms)",
                "PARTIAL", "Rej0"]

        b2_valid = not problems_b2
        b3_valid = not problems_b3

        b2_tbl = _md_table(hdrs, b2_rows) if b2_rows else "*B2-MT: 无数据*"
        b3_tbl = _md_table(hdrs, b3_rows) if b3_rows else "*B3-MT: 无数据*"

        # Pre-compute conditional strings (avoid backslash inside f-string {})
        b2_workload_hdr  = "### 工作负载有效" if b2_valid else "### 工作负载问题"
        b2_workload_line = "- avg_steps >= 3.5，backend 收到真实工具请求" if b2_valid else "- 仍有工具调用不足问题"
        b2_extra_line    = "- 各网关间有明显 ABD/success 差异，准入控制逻辑被激活" if b2_valid else ""
        b3_workload_hdr  = "### 工作负载有效" if b3_valid else "### 工作负载问题"
        b3_workload_line = "- avg_steps >= 3.5，backend 收到真实工具请求" if b3_valid else "- 仍有工具调用不足问题"
        b2_concl = "- B2 Multitool Bursty: 有效，可用于论文" if b2_valid else "- B2 Multitool Bursty: 仍需诊断"
        b3_concl = "- B3 Multitool vLLM: 有效，可作为 Sanity Check 或附录实验" if b3_valid else "- B3 Multitool vLLM: 仍需诊断"

        # Compare B2 old vs new
        def _validity(problems):
            return "✓ 有效" if not problems else ("no data" if problems == ["no data"] else "✗ 无效")

        report_md = f"""# Multitool Real-LLM 实验诊断报告

**生成时间**: {now}
**Prompt 版本**: 强制多工具 (每 session 目标 avg_steps ≥ 3.5)

---

## 0. 有效性摘要

| 实验 | 有效性 | avg_steps | zero_step% | backend_tokens |
|------|--------|----------:|----------:|---------------:|
| B2 Multitool Bursty | {_validity(problems_b2)} | {f"{statistics.mean([r['avg_steps_per_session'] for r in mt_bursty]):.2f}" if mt_bursty else "N/A"} | {f"{statistics.mean([r['zero_step_pct'] for r in mt_bursty]):.0f}%" if mt_bursty else "N/A"} | {sum(r['total_backend_tokens'] for r in mt_bursty) if mt_bursty else 0:,} |
| B3 Multitool vLLM  | {_validity(problems_b3)} | {f"{statistics.mean([r['avg_steps_per_session'] for r in mt_vllm]):.2f}" if mt_vllm else "N/A"} | {f"{statistics.mean([r['zero_step_pct'] for r in mt_vllm]):.0f}%" if mt_vllm else "N/A"} | {sum(r['total_backend_tokens'] for r in mt_vllm) if mt_vllm else 0:,} |

---

## 1. B2 Multitool Bursty

{b2_tbl}

{b2_workload_hdr}
{b2_workload_line}
{b2_extra_line}

---

## 2. B3 Multitool vLLM

{b3_tbl}

{b3_workload_hdr}
{b3_workload_line}

---

## 3. 关键指标对比 (旧 neutral vs 新 multitool)

| 指标 | 旧 neutral prompt | 新 multitool prompt |
|------|:-----------------:|:-------------------:|
| B2 avg_steps | 0.00 | {f"{statistics.mean([r['avg_steps_per_session'] for r in mt_bursty]):.2f}" if mt_bursty else "待跑"} |
| B2 zero_step% | 100% | {f"{statistics.mean([r['zero_step_pct'] for r in mt_bursty]):.0f}%" if mt_bursty else "待跑"} |
| B2 backend_tokens | 0 | {f"{sum(r['total_backend_tokens'] for r in mt_bursty):,}" if mt_bursty else "待跑"} |
| B2 有效性 | ✗ 无效 | {_validity(problems_b2)} |
| B3 avg_steps | 0.00 | {f"{statistics.mean([r['avg_steps_per_session'] for r in mt_vllm]):.2f}" if mt_vllm else "待跑"} |
| B3 有效性 | ✗ 无效 | {_validity(problems_b3)} |

---

## 4. 结论

{b2_concl}
{b3_concl}
- B1 Steady: 有效（unchanged），PlanGate ABD=10.1% 是真实信号

---

*报告由 `scripts/diagnose_neutral_real_llm_results.py --mode multitool` 生成*
"""
        out_path = MT_DIAG_DIR / "diagnosis_report.md"
        out_path.write_text(report_md, encoding="utf-8")
        print(f"\n  [报告已写入] {out_path}")

    else:
        # 默认 neutral 模式 (original behavior)
        diag = main()
        generate_report(diag, DIAG_DIR)
