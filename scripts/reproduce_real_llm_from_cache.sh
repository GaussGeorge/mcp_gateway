#!/usr/bin/env bash
# reproduce_real_llm_from_cache.sh
# -------------------------------------------------------------------
# Level 1 (Real-LLM part): Regenerate real-LLM paper tables and
# figures from CACHED CSV traces only. No API call is made.
# Expected runtime: < 2 minutes.
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GENERATED="$REPO_ROOT/results/generated"
mkdir -p "$GENERATED"

echo "========================================"
echo " PlanGate: Real-LLM Tables from Cache"
echo " (No API call — cached CSV only)"
echo "========================================"

# ── Steady GLM C=10 ──────────────────────────────────────────────────
echo ""
echo "[1] Steady API — GLM-4-Flash C=10..."
SRC="results/exp_week5_C10/week5_summary.csv"
if [ ! -f "$SRC" ]; then
    echo "    [ERROR] Missing cached data: $SRC"; exit 1
fi
python scripts/analyze_real_llm.py \
    --dir results/exp_week5_C10 \
    --output "$GENERATED/steady_glm_c10.csv" 2>/dev/null \
    || cp "$SRC" "$GENERATED/steady_glm_c10.csv"
echo "    → $GENERATED/steady_glm_c10.csv"

# ── Steady GLM C=40 ──────────────────────────────────────────────────
echo ""
echo "[2] Steady API — GLM-4-Flash C=40..."
SRC="results/exp_week5_C40/week5_summary.csv"
if [ ! -f "$SRC" ]; then
    echo "    [ERROR] Missing cached data: $SRC"; exit 1
fi
python scripts/analyze_real_llm.py \
    --dir results/exp_week5_C40 \
    --output "$GENERATED/steady_glm_c40.csv" 2>/dev/null \
    || cp "$SRC" "$GENERATED/steady_glm_c40.csv"
echo "    → $GENERATED/steady_glm_c40.csv"

# ── Bursty GLM ───────────────────────────────────────────────────────
echo ""
echo "[3] Bursty — GLM-4-Flash C=20 B=30..."
SRC="results/exp_bursty_C20_B30/bursty_summary.csv"
if [ ! -f "$SRC" ]; then
    echo "    [ERROR] Missing cached data: $SRC"; exit 1
fi
python scripts/analyze_real_llm.py \
    --dir results/exp_bursty_C20_B30 \
    --output "$GENERATED/bursty_glm.csv" 2>/dev/null \
    || cp "$SRC" "$GENERATED/bursty_glm.csv"
echo "    → $GENERATED/bursty_glm.csv"

# ── Self-Hosted vLLM ─────────────────────────────────────────────────
echo ""
echo "[4] Self-hosted vLLM C=20 W=8..."
SRC="results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv"
if [ ! -f "$SRC" ]; then
    echo "    [WARN] Missing $SRC; skipping"
else
    python scripts/analyze_real_llm.py \
        --dir results/exp_selfhosted_vllm_C20_W8 \
        --output "$GENERATED/selfhosted_vllm.csv" 2>/dev/null \
        || cp "$SRC" "$GENERATED/selfhosted_vllm.csv"
    echo "    → $GENERATED/selfhosted_vllm.csv"
fi

echo ""
echo "========================================"
echo " Real-LLM cache reproduction done."
echo ""
echo " By default, paper figures use these cached CSV traces"
echo " to avoid API cost and variance."
echo ""
echo " For live rerun (requires .env credentials), see:"
echo "   bash scripts/reproduce_real_llm_live.sh"
echo "========================================"
