"""Compute exact token waste statistics from real-LLM experiment CSVs."""
import csv, os, statistics

def analyze_summary(path, label):
    if not os.path.exists(path):
        print(f"  {label}: FILE NOT FOUND at {path}")
        return
    with open(path) as f:
        rows = list(csv.DictReader(f))
    print(f"\n=== {label} ({len(rows)} rows) ===")
    print(f"  Columns: {list(rows[0].keys())}")
    
    gateways = {}
    for r in rows:
        gw = r.get('gateway', '').strip()
        if gw not in gateways:
            gateways[gw] = []
        gateways[gw].append(r)
    
    for gw, gw_rows in sorted(gateways.items()):
        agent_tok = [float(r.get('agent_llm_tokens', 0) or 0) for r in gw_rows]
        backend_tok = [float(r.get('backend_llm_tokens', 0) or 0) for r in gw_rows]
        # Try multiple column name candidates for success
        success = []
        for r in gw_rows:
            for col in ['success', 'successful', 'succ_count', 'total_success']:
                v = r.get(col, '')
                if v and v != '0':
                    success.append(float(v))
                    break
            else:
                success.append(float(r.get('success', 0) or 0))
        
        cascade_steps = [float(r.get('cascade_wasted_steps', 0) or 0) for r in gw_rows]
        total_tok = [a + b for a, b in zip(agent_tok, backend_tok)]
        tok_per_task = [t / s if s > 0 else 0 for t, s in zip(total_tok, success)]
        
        # Wasted tokens on failed sessions = total_tokens - (tokens_per_task * success_count)
        # Approximate: cascade_wasted_steps * avg_tokens_per_step
        avg_total = statistics.mean(total_tok) if total_tok else 0
        avg_agent = statistics.mean(agent_tok) if agent_tok else 0
        avg_backend = statistics.mean(backend_tok) if backend_tok else 0
        avg_succ = statistics.mean(success) if success else 0
        avg_casc = statistics.mean(cascade_steps) if cascade_steps else 0
        avg_tpt = statistics.mean(tok_per_task) if tok_per_task else 0
        
        print(f"  {gw}: n={len(gw_rows)}")
        print(f"    agent_tokens/run:   {avg_agent:,.0f} +/- {statistics.stdev(agent_tok):,.0f}" if len(agent_tok)>1 else f"    agent_tokens/run: {avg_agent:,.0f}")
        print(f"    backend_tokens/run: {avg_backend:,.0f} +/- {statistics.stdev(backend_tok):,.0f}" if len(backend_tok)>1 else f"    backend_tokens/run: {avg_backend:,.0f}")
        print(f"    total_tokens/run:   {avg_total:,.0f}")
        print(f"    success/run:        {avg_succ:.1f}")
        print(f"    cascade_steps/run:  {avg_casc:.1f}")
        print(f"    tokens/success_task:{avg_tpt:,.0f}")

def analyze_bursty(path, label):
    if not os.path.exists(path):
        print(f"  {label}: FILE NOT FOUND at {path}")
        return
    with open(path) as f:
        rows = list(csv.DictReader(f))
    print(f"\n=== {label} ({len(rows)} rows) ===")
    print(f"  Columns: {list(rows[0].keys())}")
    for r in rows:
        gw = r.get('gateway', '')
        print(f"  {gw}: {dict(r)}")

if __name__ == '__main__':
    analyze_summary('results/exp_real3_glm/summary_all.csv', 'GLM-4-Flash Steady-State')
    analyze_summary('results/exp_real3_deepseek/summary_all.csv', 'DeepSeek-V3 Steady-State')
    analyze_bursty('results/exp_bursty_C20_B30/bursty_summary.csv', 'Bursty C20 B30')
    
    # Also check per-agent level data for wasted token calculation
    print("\n=== Checking per-agent CSVs ===")
    for d in ['results/exp_real3_glm', 'results/exp_real3_deepseek']:
        agents_files = [f for f in os.listdir(d) if '_agents.csv' in f] if os.path.exists(d) else []
        print(f"  {d}: {len(agents_files)} agent files")
        if agents_files:
            path = os.path.join(d, agents_files[0])
            with open(path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            print(f"    Sample columns: {list(rows[0].keys())}")
            # Show failed vs successful agent token usage
            failed = [r for r in rows if r.get('status','') in ('failed','cascade','partial')]
            success = [r for r in rows if r.get('status','') == 'success']
            if not failed and not success:
                # Try other status indicators
                print(f"    Status values: {set(r.get('status','N/A') for r in rows[:20])}")
                print(f"    First 3 rows: {[dict(r) for r in rows[:3]]}")
