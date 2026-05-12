"""
Token waste analysis for bursty real-LLM experiment.
Paper claims: PlanGate 6120 tokens/task vs NG 6788, waste 28.8% -> 21.0%
"""
import csv, statistics, os

base = 'results/exp_bursty_C20_B30'

def compute_token_stats(gw, max_rows=3 if True else 1):
    """Compute token stats from agent CSV files for all 9 data points."""
    all_tokens_per_task = []
    all_waste_fracs = []
    
    # For run1, we have 3 data points (3 rows in steps_summary)
    # For run2-7, we have 1 data point each
    for run_num in range(1, 8):
        summary_path = os.path.join(base, gw, f'run{run_num}', 'steps_summary.csv')
        agents_path = os.path.join(base, gw, f'run{run_num}', 'steps_agents.csv')
        
        if not os.path.exists(agents_path):
            continue
        
        with open(summary_path) as f:
            summary_rows = list(csv.DictReader(f))
        
        # For run1 that has 3 rows, we can't easily separate per-row token data
        # from the single agents file, so compute aggregate stats
        with open(agents_path) as f:
            agent_rows = list(csv.DictReader(f))
        
        # Total tokens
        total_tokens = sum(float(r['agent_llm_tokens']) for r in agent_rows)
        
        # Tokens for successful agents
        success_tokens = sum(float(r['agent_llm_tokens']) for r in agent_rows if r['state'] == 'SUCCESS')
        n_success = sum(1 for r in agent_rows if r['state'] == 'SUCCESS')
        
        # Tokens for partial/failed agents (waste)
        partial_tokens = sum(float(r['agent_llm_tokens']) for r in agent_rows if r['state'] in ('PARTIAL', 'CASCADE_FAILED', 'ABANDONED'))
        
        waste_frac = partial_tokens / total_tokens * 100 if total_tokens > 0 else 0
        tokens_per_task = success_tokens / n_success if n_success > 0 else float('nan')
        
        n_rows = len(summary_rows)  # Number of data points in this run file
        print(f'  {gw}/run{run_num} ({n_rows} dp): n_agents={len(agent_rows)}, succ={n_success}, tokens/task={tokens_per_task:.0f}, waste%={waste_frac:.1f}%')
        
        # Distribute evenly across rows (approximate)
        for _ in range(n_rows):
            all_tokens_per_task.append(tokens_per_task)
            all_waste_fracs.append(waste_frac)
    
    return all_tokens_per_task, all_waste_fracs

print("=== Token waste analysis ===")
print("Paper: PlanGate 6120 tokens/task vs NG 6788, waste 28.8%(NG) -> 21.0%(PG)")
print()

ng_tpt, ng_wf = compute_token_stats('ng')
pg_tpt, pg_wf = compute_token_stats('plangate_real')

def avg(lst): return sum(lst)/len(lst) if lst else float('nan')
def std(lst): return statistics.stdev(lst) if len(lst) > 1 else 0.0

print()
print(f"NG:       tokens/task = {avg(ng_tpt):.0f}, waste% = {avg(ng_wf):.1f}%")
print(f"PlanGate: tokens/task = {avg(pg_tpt):.0f}, waste% = {avg(pg_wf):.1f}%")
print()

# Check unique agent states
print("=== Agent state check (run1) ===")
for gw in ['ng', 'plangate_real']:
    agents_path = os.path.join(base, gw, 'run1', 'steps_agents.csv')
    with open(agents_path) as f:
        rows = list(csv.DictReader(f))
    states = {}
    for r in rows:
        s = r['state']
        states[s] = states.get(s, 0) + 1
    print(f"  {gw}: states = {states}")
