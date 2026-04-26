import csv, os

base = r"d:\mcp-governance-main - A"

def avg(v): return sum(v)/len(v) if v else 0
def sd(v):
    if len(v)<2: return 0
    m=avg(v); return (sum((x-m)**2 for x in v)/(len(v)-1))**0.5

print("=== Aggregate bursty data from steps_summary.csv (all runs) ===\n")
for gw in ['ng','plangate_real']:
    bp = os.path.join(base, f'results/exp_bursty_C20_B30/{gw}')
    runs = sorted([d for d in os.listdir(bp) if os.path.isdir(os.path.join(bp, d))])
    
    partials, rej0s, cascades, succs, abds, agents_list = [], [], [], [], [], []
    for rd in runs:
        fp = os.path.join(bp, rd, 'steps_summary.csv')
        if not os.path.exists(fp): continue
        with open(fp) as f:
            rows = list(csv.DictReader(f))
        if not rows: continue
        # steps_summary has multiple rows - first row is the main data
        r = rows[0]
        partial = float(r['partial'])
        rej0 = float(r['all_rejected'])
        cascade = float(r['cascade_wasted_steps'])
        agents = float(r['agents'])
        succ = float(r['success'])
        # ABD = (partial + all_rejected) / agents * 100? No, ABD = failure rate
        # Actually ABD = (partial)/(agents - all_rejected) * 100? Let me check
        # From other tables: ABD = abandon rate 
        # success_rate = success / agents * 100
        succ_pct = succ / agents * 100
        # ABD% might be partial/(agents - all_rejected) * 100 or something else
        # Let me compute: admitted = agents - all_rejected; ABD = partial/admitted
        admitted = agents - rej0
        abd_pct = partial / admitted * 100 if admitted > 0 else 0
        
        partials.append(partial)
        rej0s.append(rej0)
        cascades.append(cascade)
        succs.append(succ_pct)
        abds.append(abd_pct)
        agents_list.append(agents)
        
        print(f"  {gw}/{rd}: agents={agents:.0f} succ={succ:.0f} partial={partial:.0f} rej0={rej0:.0f} cascade={cascade:.0f} succ%={succ_pct:.1f} abd%={abd_pct:.1f}")
    
    lbl = 'PG' if gw == 'plangate_real' else 'NG'
    print(f"\n  {lbl} (N={len(partials)}):")
    print(f"    PARTIAL = {avg(partials):.0f} +- {sd(partials):.0f}")
    print(f"    Rej0    = {avg(rej0s):.0f} +- {sd(rej0s):.0f}")
    print(f"    ABD%    = {avg(abds):.1f} +- {sd(abds):.1f}")
    print(f"    Succ%   = {avg(succs):.1f} +- {sd(succs):.1f}")
    print(f"    Cascade = {avg(cascades):.0f} +- {sd(cascades):.0f}")
    print()

print("\nPaper claims (N=7):")
print("         PARTIAL   Rej0      ABD%          Succ%       Cascade")
print("NG:      91+-11    89+-11    82.3+-3.9     9.8+-2.4    163+-25")
print("PG:      75+-12    109+-12   82.7+-5.3     7.9+-2.4    144+-24")

# Also check pp and rajomon dirs  
print("\n=== pp and rajomon dirs ===")
for gw in ['pp','rajomon']:
    bp = os.path.join(base, f'results/exp_bursty_C20_B30/{gw}')
    if not os.path.isdir(bp): continue
    runs = sorted([d for d in os.listdir(bp) if os.path.isdir(os.path.join(bp, d))])
    print(f"{gw}: {len(runs)} runs: {runs}")
    for rd in runs:
        fp = os.path.join(bp, rd, 'steps_summary.csv')
        if os.path.exists(fp):
            with open(fp) as f:
                rows = list(csv.DictReader(f))
            if rows:
                r = rows[0]
                print(f"  {rd}: agents={r.get('agents','?')} partial={r.get('partial','?')} rej0={r.get('all_rejected','?')} cascade={r.get('cascade_wasted_steps','?')}")
