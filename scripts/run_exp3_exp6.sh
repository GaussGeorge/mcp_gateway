#!/bin/bash
# run_exp3_exp6.sh — Re-run Exp3 and Exp6 experiments after Hysteresis Intensity update
set -e

cd /mnt/d/mcp-governance-main

# Kill any existing backend
kill $(ss -tlnp "sport = :8080" 2>/dev/null | grep -oP 'pid=\K[0-9]+') 2>/dev/null || true
sleep 1

echo "=== Running Exp3_MixedMode + Exp6_ScaleConcReact ==="
python3 scripts/run_all_experiments.py \
  --exp-list Exp3_MixedMode Exp6_ScaleConcReact \
  --repeats 5 \
  --gateway-binary ./gateway_linux \
  --backend-max-workers 10 \
  --cpu-backend 8-15 \
  --cpu-gateway 4-7 \
  --cpu-loadgen 0-3

echo "=== Experiments complete ==="
