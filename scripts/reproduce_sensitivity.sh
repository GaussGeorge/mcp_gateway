#!/usr/bin/env bash
# reproduce_sensitivity.sh
# -------------------------------------------------------------------
# Level 2b: Rerun sensitivity ablation experiments.
#   - Rajomon price_step sensitivity
#   - Discount function ablation
#   - Alpha sensitivity
#   - Beta sensitivity (mock, pure ReAct)
# No API key required. Requires: Go 1.21+, Python 3.10+.
# Expected runtime: ~30–60 minutes total.
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REPEATS="${REPEATS:-5}"

echo "========================================"
echo " PlanGate: Rerun Sensitivity Experiments"
echo " Repeats=$REPEATS"
echo "========================================"

# ── Build ────────────────────────────────────────────────────────────
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

# ── Rajomon Sensitivity ──────────────────────────────────────────────
echo ""
echo "[1] Rajomon sensitivity (price_step ∈ {5,10,20,50,100})..."
python scripts/rajomon_sensitivity.py \
    --repeats "$REPEATS" \
    --gateway-binary "$GATEWAY_BIN" 2>/dev/null \
|| python scripts/run_all_experiments.py \
    --exp Rajomon_Sensitivity \
    --repeats "$REPEATS" \
    --gateway-binary "$GATEWAY_BIN"
echo "    → results/exp_rajomon_sensitivity/"

# ── Discount Ablation ────────────────────────────────────────────────
echo ""
echo "[2] Discount function ablation..."
python scripts/run_all_experiments.py \
    --exp Exp8_DiscountAblation \
    --repeats "$REPEATS" \
    --gateway-binary "$GATEWAY_BIN"
echo "    → results/exp8_discountablation/"

# ── Alpha Sweep ──────────────────────────────────────────────────────
echo ""
echo "[3] Alpha sensitivity sweep..."
python scripts/run_alpha_sweep.py \
    --repeats "$REPEATS" \
    --gateway-binary "$GATEWAY_BIN" 2>/dev/null \
|| echo "    [WARN] run_alpha_sweep.py not found; skip"
echo "    → results/exp_alpha_sweep/"

# ── Beta Ablation (mock, no API) ─────────────────────────────────────
echo ""
echo "[4] Beta sensitivity (pure ReAct, mock backend, 5 repeats)..."
echo "    Expected runtime: ~7–12 minutes"
python scripts/run_beta_ablation.py --repeats "$REPEATS"
echo "    → results/beta_ablation/"
echo "    → tables/beta_ablation_table.tex"
echo "    → plots/beta_ablation/"

echo ""
echo "========================================"
echo " Sensitivity experiments done."
echo " Regenerate figures with:"
echo "   bash scripts/reproduce_main_paper_from_cache.sh"
echo "========================================"
