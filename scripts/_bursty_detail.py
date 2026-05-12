import csv, os, glob

def avg(v): return sum(v)/len(v) if v else 0
def sd(v):
    if len(v)<2: return 0
    m=avg(v); return (sum((x-m)**2 for x in v)/(len(v)-1))**0.5

base = r"d:\mcp-governance-main - A"

print("=== BURSTY: Computing from per-run summaries (all available runs) ===")
for gw in ['ng','plangate_real']:
    bp = os.path.join(base, f'results/exp_bursty_C20_B30/{gw}')
    runs = sorted([d2 for d2 in os.listdir(bp) if os.path.isdir(os.path.join(bp, d2))])
    partials = []
    rej0s = []
    cascades = []
    succs = []
    for rd in runs:
        rdp = os.path.join(bp, rd)
        csvs = [f for f in os.listdir(rdp) if 'summary' in f.lower() and f.endswith('.csv')]
        if csvs:
            with open(os.path.join(rdp, csvs[0])) as f:
                rows = list(csv.DictReader(f))
            if rows:
                r = rows[0]
                partials.append(float(r['partial']))
                rej0s.append(float(r['all_rejected']))
                # Try to find cascade_steps
                cs = r.get('cascade_steps', None)
                if cs: cascades.append(float(cs))
                sr = r.get('success_rate', None) or r.get('success', None)
                if sr: succs.append(float(sr))
                # Also check cascade_agents field
                ca = r.get('cascade_agents', None)
                
    lbl = 'PG' if gw == 'plangate_real' else 'NG'
    print(f"{lbl}(n={len(partials)}): PARTIAL={avg(partials):.0f}+-{sd(partials):.0f}, Rej0={avg(rej0s):.0f}+-{sd(rej0s):.0f}, Cascade(n={len(cascades)})={avg(cascades):.0f}+-{sd(cascades):.0f}")

print()
print("Paper claims (N=7):")
print("NG:  PARTIAL=91+-11, Rej0=89+-11, Cascade=163+-25")  
print("PG:  PARTIAL=75+-12, Rej0=109+-12, Cascade=144+-24")
print()
print("Note: Only 5 runs found per gateway. Paper claims N=7 (3 initial + 4 additional)")
print("Possible explanation: 2 additional runs may be in a different directory or were lost")

# Also look for the per-run CSVs for cascade steps
print("\n=== Checking individual run CSV for cascade data ===")
for gw in ['ng','plangate_real']:
    bp = os.path.join(base, f'results/exp_bursty_C20_B30/{gw}')
    runs = sorted([d2 for d2 in os.listdir(bp) if os.path.isdir(os.path.join(bp, d2))])
    for rd in runs[:1]:  # just first run
        rdp = os.path.join(bp, rd)
        csvs = [f for f in os.listdir(rdp) if f.endswith('.csv')]
        print(f"\n{gw}/{rd} files: {csvs}")
        for c in csvs:
            with open(os.path.join(rdp, c)) as f:
                reader = csv.DictReader(f)
                cols = reader.fieldnames
                print(f"  {c} columns: {cols}")
                break
