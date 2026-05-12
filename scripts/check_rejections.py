#!/usr/bin/env python3
"""Analyze rejected sessions from quick test."""
import csv
import sys

csv_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/quick_test_plangate_sessions.csv"

with open(csv_path) as f:
    rows = list(csv.DictReader(f))

rej = [r for r in rows if "REJECT" in r.get("state", "")]
casc = [r for r in rows if "CASCADE" in r.get("state", "")]
succ = [r for r in rows if r.get("state") == "SUCCESS"]

print(f"Total={len(rows)}, Success={len(succ)}, Cascade={len(casc)}, Reject={len(rej)}")
if rej:
    print("Rejected sessions (first 10):")
    for r in rej[:10]:
        print(f"  {r['session_id']}: steps={r.get('n_steps','?')}, state={r['state']}")
