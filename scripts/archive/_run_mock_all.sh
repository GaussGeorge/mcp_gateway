#!/bin/bash
# Run all mock experiments (Exp1-Exp7)
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Starting All Mock Experiments ==="
echo "TIME: $(date)"

python3 scripts/run_all_experiments.py \
    --gateway-binary ./gateway_linux \
    --backend-max-workers 10 \
    --repeats 5

echo ""
echo "=== All Mock Experiments Complete ==="
echo "TIME: $(date)"
