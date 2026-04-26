#!/bin/bash
cd /mnt/d/mcp-governance-main
LOG=results/log/real_llm/exp_real3_run.log
AGENTS=results/exp_real3_glm/mcpdp-real_20260405_184028_agents.csv

echo "=== Agent-level results ==="
echo "SUCCESS: $(grep -c SUCCESS $AGENTS)"
echo "PARTIAL: $(grep -c PARTIAL $AGENTS)"  
echo "ALL_REJECTED: $(grep -c ALL_REJECTED $AGENTS)"

echo ""
echo "=== Session cap full total rejections ==="
grep -c "session cap full" $LOG

echo ""
echo "=== Unique agents ever rejected at Step0 ==="
grep "session cap full" $LOG | sed 's/.*session=agent-//' | cut -d' ' -f1 | sort -u | wc -l

echo ""
echo "=== Agents admitted (got FREE PASS at Step0) ==="
grep "FREE PASS" $LOG | grep "Step0" | sed 's/.*session=agent-//' | cut -d' ' -f1 | sort -u | wc -l

echo ""
echo "=== Agent retry attempts (same agent rejected multiple times) ==="
grep "session cap full" $LOG | sed 's/.*session=agent-//' | cut -d' ' -f1 | sort | uniq -c | sort -rn | head -10

echo ""
echo "=== Average session duration (time from first FREE PASS to last Sunk-Cost) ==="
echo "(Approx from log timestamps)"
