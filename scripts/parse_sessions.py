import csv, sys, glob, os

pattern = sys.argv[1] if len(sys.argv) > 1 else '/mnt/d/mcp-governance-main/results/Exp3_MixedMode/plangate_full_ps_ratio0.0_run*_sessions.csv'
files = sorted(glob.glob(pattern))

for fpath in files:
    rows = list(csv.DictReader(open(fpath)))
    succ = sum(1 for r in rows if r['state'] == 'SUCCESS')
    casc = sum(1 for r in rows if 'CASCADE' in r['state'])
    rej = sum(1 for r in rows if 'REJECT' in r['state'])
    eg = sum(float(r['effective_goodput']) for r in rows)
    dur = max(float(r['end_time']) for r in rows) - min(float(r['start_time']) for r in rows)
    fname = os.path.basename(fpath)
    print(f'{fname}: {len(rows)} sess, Succ={succ}, Casc={casc}, Rej={rej}, EffGP/s={eg/dur:.1f}')

if len(files) > 1:
    all_rows = []
    for fpath in files:
        all_rows.extend(list(csv.DictReader(open(fpath))))
    succ = sum(1 for r in all_rows if r['state'] == 'SUCCESS')
    casc = sum(1 for r in all_rows if 'CASCADE' in r['state'])
    rej = sum(1 for r in all_rows if 'REJECT' in r['state'])
    print(f'--- TOTAL: {len(all_rows)} sess, Succ={succ}, Casc={casc}, Rej={rej}, AvgCasc={casc/len(files):.1f}')
