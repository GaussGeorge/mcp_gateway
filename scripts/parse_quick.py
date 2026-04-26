import csv, sys, glob

# Accept a file argument or default to /tmp/quick_test_plangate.csv
if len(sys.argv) > 1:
    files = sys.argv[1:]
else:
    files = ['/tmp/quick_test_plangate.csv']

for fpath in files:
    with open(fpath) as f:
        rows = list(csv.DictReader(f))
    
    sessions = {}
    for r in rows:
        sid = r['session_id']
        if sid not in sessions:
            sessions[sid] = {'eff_gp': 0}
        sessions[sid]['status'] = r['session_state']
        sessions[sid]['eff_gp'] += float(r.get('effective_goodput', 0))
    
    succ = sum(1 for s in sessions.values() if s['status'] == 'SUCCESS')
    cascade = sum(1 for s in sessions.values() if 'CASCADE' in s['status'])
    rej = sum(1 for s in sessions.values() if 'REJECT' in s['status'])
    total_eff_gp = sum(s['eff_gp'] for s in sessions.values())
    dur = float(rows[-1]['timestamp']) - float(rows[0]['timestamp']) if len(rows) > 1 else 1
    fname = fpath.split('/')[-1]
    print(f'{fname}: Sessions={len(sessions)} Success={succ} Cascade={cascade} Reject={rej} EffGP/s={total_eff_gp/dur:.1f}')
