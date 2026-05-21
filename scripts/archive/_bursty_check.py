import csv, os

def avg(v): return sum(v)/len(v) if v else 0
def sd(v):
    if len(v)<2: return 0
    m=avg(v); return (sum((x-m)**2 for x in v)/(len(v)-1))**0.5

base = r"d:\mcp-governance-main - A"

# Read bursty summary
with open(os.path.join(base, 'results/exp_bursty_C20_B30/bursty_summary.csv')) as f:
    d = list(csv.DictReader(f))
print(f'bursty_summary.csv: {len(d)} rows')
for r in d:
    print(f"  {r['gateway']} run{r['run']}: partial={r['partial']}, rej0={r['all_rejected']}, succ%={r['success_rate']}, abd={r['abd_total']}, cascade={r['cascade_steps']}")

# Check individual run dirs
for gw in ['ng','plangate_real']:
    bp = os.path.join(base, f'results/exp_bursty_C20_B30/{gw}')
    runs = sorted([d2 for d2 in os.listdir(bp) if os.path.isdir(os.path.join(bp, d2))])
    print(f'\n{gw}: {len(runs)} runs: {runs}')
    for rd in runs:
        rdp = os.path.join(bp, rd)
        csvs = [f for f in os.listdir(rdp) if 'summary' in f.lower() and f.endswith('.csv')]
        if csvs:
            with open(os.path.join(rdp, csvs[0])) as f:
                rows = list(csv.DictReader(f))
            if rows:
                r = rows[0]
                print(f"  {rd}: partial={r.get('partial','?')}, rej0={r.get('all_rejected','?')}, succ%={r.get('success_rate','?')}, abd={r.get('abd_total','?')}, casc={r.get('cascade_steps','?')}")
        else:
            allcsv = [f for f in os.listdir(rdp) if f.endswith('.csv')]
            print(f'  {rd}: no summary, files={allcsv[:3]}')

# Also check exp_pp_smoke and rajomon dirs for bursty
for d2 in ['pp','rajomon']:
    dp = os.path.join(base, f'results/exp_bursty_C20_B30/{d2}')
    if os.path.isdir(dp):
        runs = sorted(os.listdir(dp))
        print(f'\n{d2}: entries={runs[:5]}')
