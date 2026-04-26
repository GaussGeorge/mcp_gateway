#!/bin/bash
# Wrapper: launch mock experiments in background and report
cd /mnt/d/mcp-governance-main
nohup bash scripts/_run_mock_all.sh > /tmp/mock_exp.log 2>&1 &
BGPID=$!
echo "STARTED background PID=$BGPID"
sleep 3
echo "=== LOG HEAD ==="
head -20 /tmp/mock_exp.log 2>/dev/null || echo "(no log yet)"
echo "=== PROCESS CHECK ==="
ps aux | grep run_all_experiments | grep -v grep || echo "(not found yet, may still be starting)"
