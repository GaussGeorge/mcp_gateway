#!/usr/bin/env bash
# reproduce_mock_core.sh
# -------------------------------------------------------------------
# Level 2a: Rerun controlled mock experiments (Exp1 Core + Ablation).
# No API key required. Requires: Go 1.21+, Python 3.10+.
# Expected runtime: ~20–40 minutes total.
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REPEATS="${REPEATS:-5}"
GENERATED="$REPO_ROOT/results/generated"
mkdir -p "$GENERATED"

echo "========================================"
echo " PlanGate: Rerun Core Mock Experiments"
echo " Repeats=$REPEATS"
echo "========================================"

# ── Build gateway ────────────────────────────────────────────────────
echo ""
echo "[build] Compiling gateway..."
if [ "$(uname -s 2>/dev/null)" = "Linux" ]; then
    go build -o gateway_linux ./cmd/gateway
    GATEWAY_BIN="./gateway_linux"
else
    go build -o gateway.exe ./cmd/gateway
    GATEWAY_BIN="./gateway.exe"
fi
echo "    → $GATEWAY_BIN"

# ── Exp1: Core ───────────────────────────────────────────────────────
echo ""
echo "[Exp1] Core performance (500 sessions, C=200, $REPEATS repeats)..."
python scripts/run_all_experiments.py \
    --exp Exp1_Core \
    --repeats "$REPEATS" \
    --gateway-binary "$GATEWAY_BIN"
echo "    → results/exp1_core/"

# ── Exp4: Mechanism Ablation ─────────────────────────────────────────
echo ""
echo "[Exp4] Mechanism ablation ($REPEATS repeats)..."
python scripts/run_all_experiments.py \
    --exp Exp4_Ablation \
    --repeats "$REPEATS" \
    --gateway-binary "$GATEWAY_BIN"
echo "    → results/exp4_ablation/"

echo ""
echo "========================================"
echo " Core mock experiments done."
echo " Regenerate tables with:"
echo "   bash scripts/reproduce_main_paper_from_cache.sh"
echo "========================================"
