#!/usr/bin/env bash
# reproduce_real_llm_live.sh
# -------------------------------------------------------------------
# Level 3 (Optional): Re-run real-LLM experiments from scratch.
# Requires:  .env file with LLM_API_KEY set  (or env var already set)
#            Internet access to GLM/OpenAI endpoint
# NOT required for basic artifact verification (use Level 0/1 instead).
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Load .env if present ──────────────────────────────────────────────
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

# ── Guard: require API key ────────────────────────────────────────────
if [ -z "${LLM_API_KEY:-}" ]; then
    echo ""
    echo "ERROR: LLM_API_KEY is not set."
    echo ""
    echo "  Option A – create a .env file in the repo root:"
    echo "    LLM_API_KEY=<your-key>"
    echo "    LLM_API_BASE=https://open.bigmodel.cn/api/paas/v4"
    echo "    LLM_MODEL=glm-4-flash"
    echo ""
    echo "  Option B – set the env var before calling this script:"
    echo "    export LLM_API_KEY=<your-key>"
    echo "    bash scripts/reproduce_real_llm_live.sh"
    echo ""
    echo "  !! .env must NOT be committed to version control."
    echo ""
    echo "For offline reproduction (no API key), use:"
    echo "    bash scripts/reproduce_real_llm_from_cache.sh"
    exit 1
fi

echo "========================================"
echo " PlanGate: Live Real-LLM Experiments"
echo "========================================"
echo " Endpoint: ${LLM_API_BASE:-https://open.bigmodel.cn/api/paas/v4}"
echo " Model:    ${LLM_MODEL:-glm-4-flash}"
echo ""
echo " WARNING: This will issue real API calls and incur cost."
echo " Estimated total tokens: ~3-8M depending on concurrency."
echo " Press Ctrl-C within 10 seconds to cancel."
sleep 10

# ── Build gateway ─────────────────────────────────────────────────────
echo ""
echo "[0] Building gateway..."
go build -o gateway ./cmd/gateway
echo "    → gateway binary ready"

# ── Experiment: Steady GLM C=10 (exp_week5_C10) ──────────────────────
echo ""
echo "[1] Steady GLM C=10 (3 repeats per gateway)..."
if [ -f "scripts/run_exp_real3.sh" ]; then
    CONCURRENCY=10 REPEATS=3 bash scripts/run_exp_real3.sh
else
    python scripts/run_real_llm_experiments.py \
        --concurrency 10 --repeats 3 \
        --output results/exp_week5_C10
fi
echo "    → results/exp_week5_C10/"

# ── Experiment: Steady GLM C=40 (exp_week5_C40) ──────────────────────
echo ""
echo "[2] Steady GLM C=40 (3 repeats per gateway)..."
if [ -f "scripts/run_exp_real3_all.sh" ]; then
    CONCURRENCY=40 REPEATS=3 bash scripts/run_exp_real3_all.sh
else
    python scripts/run_real_llm_experiments.py \
        --concurrency 40 --repeats 3 \
        --output results/exp_week5_C40
fi
echo "    → results/exp_week5_C40/"

# ── Experiment: Bursty GLM C=20 B=30 ─────────────────────────────────
echo ""
echo "[3] Bursty GLM C=20 B=30 (3 repeats per gateway)..."
if [ -f "scripts/run_real_llm_bursty.py" ]; then
    python scripts/run_real_llm_bursty.py --repeats 3 --burst-size 30
else
    echo "    [SKIP] run_real_llm_bursty.py not found"
fi
echo "    → results/exp_bursty_C20_B30/"

# ── (Optional) Self-hosted vLLM ───────────────────────────────────────
echo ""
echo "[4] Self-hosted vLLM (optional — requires local GPU + vLLM server at port 8000)..."
if [ "${RUN_VLLM:-0}" = "1" ]; then
    python scripts/run_selfhosted_vllm.py \
        --concurrency 20 --workers 8 --repeats 3 \
        --output results/exp_selfhosted_vllm_C20_W8
    echo "    → results/exp_selfhosted_vllm_C20_W8/"
else
    echo "    [SKIP] Set RUN_VLLM=1 to enable this step."
fi

# ── Regenerate figures/tables from fresh data ─────────────────────────
echo ""
echo "[5] Regenerating tables from new data..."
bash scripts/reproduce_real_llm_from_cache.sh

echo ""
echo "========================================"
echo " Live real-LLM reproduction complete."
echo " Figures/tables updated."
echo "========================================"
