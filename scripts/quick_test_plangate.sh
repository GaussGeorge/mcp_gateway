#!/bin/bash
# quick_test_plangate.sh — 快速验证 plangate intensity 修复效果
set -e
cd /mnt/d/mcp-governance-main

# Kill any existing backend/gateway
kill $(ss -tlnp "sport = :8080" 2>/dev/null | grep -oP 'pid=\K[0-9]+') 2>/dev/null || true
kill $(ss -tlnp "sport = :9200" 2>/dev/null | grep -oP 'pid=\K[0-9]+') 2>/dev/null || true
sleep 1

# Start backend
taskset -c 8-15 python3 mcp_server/server.py --host 127.0.0.1 --port 8080 --max-workers 10 &
BACKEND_PID=$!
sleep 3

echo "=== Quick Test: PlanGate ps_ratio=0.0, conc=20 ==="

# Start plangate gateway
taskset -c 4-7 ./gateway_linux --mode mcpdp --port 9200 --host 127.0.0.1 \
  --backend http://127.0.0.1:8080 \
  --plangate-price-step 40 --plangate-max-sessions 30 --plangate-sunk-cost-alpha 0.5 &
GW_PID=$!
sleep 3

# Run load generator
taskset -c 0-3 python3 scripts/dag_load_generator.py \
  --target http://127.0.0.1:9200 \
  --sessions 200 --concurrency 20 --ps-ratio 0.0 \
  --heavy-ratio 0.3 --duration 60 --budget 500 \
  --min-steps 3 --max-steps 7 --step-timeout 2.0 \
  --output /tmp/quick_test_plangate.csv

echo ""
echo "=== Results ==="
python3 -c "
import csv
with open('/tmp/quick_test_plangate.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
succ = sum(1 for r in rows if r['status']=='success')
cascade = sum(1 for r in rows if r['status']=='cascade_failure')
total = len(rows)
dur = float(rows[-1]['end_time']) - float(rows[0]['start_time']) if rows else 1
gp = sum(int(r.get('total_steps','0')) for r in rows if r['status']=='success')
print(f'Sessions: {total}, Success: {succ}, Cascade: {cascade}')
print(f'EffGoodput/s: {gp/dur:.1f}')
"

# Cleanup
kill $GW_PID 2>/dev/null || true
kill $BACKEND_PID 2>/dev/null || true
echo "=== Done ==="
