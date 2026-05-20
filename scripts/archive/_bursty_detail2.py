import csv, os

base = r"d:\mcp-governance-main - A"

# Check steps_summary.csv and steps_agents.csv structure
for gw in ['ng','plangate_real']:
    rdp = os.path.join(base, f'results/exp_bursty_C20_B30/{gw}/run1')
    for fname in ['steps_summary.csv','steps_agents.csv']:
        fp = os.path.join(rdp, fname)
        with open(fp) as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames
            rows = list(reader)
            print(f"{gw}/run1/{fname}: cols={cols}, rows={len(rows)}")
            if rows:
                print(f"  first row: {dict(rows[0])}")

# Now also check if there are other bursty exp directories
print("\n=== Looking for other bursty-related directories ===")
results = os.path.join(base, 'results')
for d in sorted(os.listdir(results)):
    if 'bursty' in d.lower() or 'burst' in d.lower():
        fullp = os.path.join(results, d)
        if os.path.isdir(fullp):
            print(f"  {d}/ -> {os.listdir(fullp)[:10]}")
