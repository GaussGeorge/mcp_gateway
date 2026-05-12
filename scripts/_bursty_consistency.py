"""
Bursty real-LLM consistency check.
"""
import csv, statistics, os

def avg(lst): return sum(lst)/len(lst) if lst else float('nan')
def std(lst): return statistics.stdev(lst) if len(lst)>1 else 0.0

base = 'results/exp_bursty_C20_B30'
gateways = ['ng', 'plangate_real']

print("=== Bursty real-LLM (exp_bursty_C20_B30) ===")
print("Paper Table: NG PARTIAL=95+/-12, Rej0=84+/-13, ABD=82.1%, Succ=10.3%, Cascade=174+/-33")
print("             PlanGate PARTIAL=75+/-10, Rej0=108+/-10, ABD=81.6%, Succ=8.4%, Cascade=143+/-22")
print()

for gw in gateways:
    partials=[]; rej0s=[]; cascades=[]; succs=[]; abds=[]
    for run_num in range(1, 8):
        fpath = os.path.join(base, gw, f'run{run_num}', 'steps_summary.csv')
        if not os.path.exists(fpath): continue
        with open(fpath) as f:
            rows = list(csv.DictReader(f))
        if not rows: continue
        r = rows[0]
        partial = float(r.get('partial', 0))
        rej0 = float(r.get('all_rejected', 0))
        succ = float(r.get('success', 0))
        agents = float(r.get('agents', 200))
        cascade = 0.0
        for col in ['cascade_wasted_steps', 'cascade_steps']:
            if col in r:
                cascade = float(r[col])
                break
        admitted = agents - rej0
        abd = partial / admitted * 100 if admitted > 0 else 0
        succ_pct = succ / agents * 100
        print(f'  {gw}/run{run_num}: agents={agents:.0f}, succ={succ:.0f}({succ_pct:.1f}%), partial={partial:.0f}, rej0={rej0:.0f}, cascade={cascade:.0f}, ABD={abd:.1f}%')
        partials.append(partial); rej0s.append(rej0); cascades.append(cascade); succs.append(succ_pct); abds.append(abd)
    N = len(partials)
    print(f'  {gw} SUMMARY (N={N}):')
    print(f'    PARTIAL = {avg(partials):.1f} +/- {std(partials):.1f}')
    print(f'    Rej0    = {avg(rej0s):.1f} +/- {std(rej0s):.1f}')
    print(f'    Cascade = {avg(cascades):.1f} +/- {std(cascades):.1f}')
    print(f'    Succ%   = {avg(succs):.1f} +/- {std(succs):.1f}%')
    print(f'    ABD%    = {avg(abds):.1f} +/- {std(abds):.1f}%')
    print()

# Check token data
print("=== Token waste analysis ===")
print("Paper: PlanGate 6120 tokens/task vs NG 6788, waste 28.8% -> 21.0%")
print()
for gw in gateways:
    all_tokens=[]; all_succ_cnt=[]; all_waste_fracs=[]
    for run_num in range(1, 8):
        fpath = os.path.join(base, gw, f'run{run_num}', 'steps_summary.csv')
        if not os.path.exists(fpath): continue
        with open(fpath) as f:
            rows = list(csv.DictReader(f))
        if not rows: continue
        r = rows[0]
        # Token data
        agent_tokens = float(r.get('agent_llm_tokens', 0))
        succ = float(r.get('success', 0))
        partial = float(r.get('partial', 0))
        agents = float(r.get('agents', 200))
        # Per-task tokens
        if succ > 0:
            tokens_per_task = agent_tokens / succ
        else:
            tokens_per_task = float('nan')
        # Waste fraction: tokens on partial sessions / total tokens
        # We can estimate based on the summary data
        all_tokens.append(tokens_per_task)
        all_succ_cnt.append(succ)
    print(f'  {gw}: avg tokens/task = {avg(all_tokens):.0f}')

# Also look for agent-level CSV for token waste fraction
print()
print("  (Looking for agent token files...)")
for gw in ['ng', 'plangate_real']:
    for run_num in range(1, 4):
        agents_file = os.path.join(base, gw, f'run{run_num}', 'steps_agents.csv')
        if os.path.exists(agents_file):
            with open(agents_file) as f:
                agent_rows = list(csv.DictReader(f))
            if agent_rows:
                print(f'  {gw}/run{run_num} agents.csv fields: {list(agent_rows[0].keys())}')
                print(f'  First row: {agent_rows[0]}')
            break
