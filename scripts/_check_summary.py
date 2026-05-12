import csv
with open('results/exp_week5_C10/week5_summary.csv') as f:
    rows = list(csv.DictReader(f))
print('plangate_real rows:')
for r in rows:
    if r['gateway'] == 'plangate_real':
        print(f"  run={r['run']} succ={r['success']} elapsed={r['elapsed_s']}")
print(f'Total rows: {len(rows)}')
for r in rows:
    print(f"  gw={r['gateway']} run={r['run']} succ={r['success']}")
