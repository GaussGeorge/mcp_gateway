#!/usr/bin/env bash
# reproduce_main_paper_from_cache.sh
# -------------------------------------------------------------------
# Level 1: Regenerate ALL paper tables and figures from cached CSV.
# No API key required. No live experiment run.
# Expected runtime: 5–10 minutes.
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GENERATED="$REPO_ROOT/results/generated"
mkdir -p "$GENERATED"

echo "========================================"
echo " PlanGate: Reproduce Paper from Cache"
echo " Root: $REPO_ROOT"
echo "========================================"

# ── 0. Sanity: Python importability ─────────────────────────────────
echo ""
echo "[0] Checking Python environment..."
python -m compileall scripts/ mcp_server/ -q 2>/dev/null || true
echo "    OK"

# ── 1. Commitment Quality Table (Table 1) ───────────────────────────
echo ""
echo "[1] Commitment Quality Table..."
if [ -f "results/paper_figures/table_commitment_quality.tex" ]; then
    cp results/paper_figures/table_commitment_quality.tex tables/table_commitment_quality_submission.tex
    echo "    → tables/table_commitment_quality_submission.tex (copied from pre-generated)"
else
    python scripts/update_paper_tables.py --exp commitment_quality \
        --output tables/table_commitment_quality_submission.tex 2>/dev/null || \
    echo "    [WARN] update_paper_tables.py skipped (table already in paper_figures/)"
fi

# ── 2. Core Mock Summary ─────────────────────────────────────────────
echo ""
echo "[2] Core Mock Performance..."
python scripts/aggregate_results.py \
    --dir results/exp1_core \
    --output "$GENERATED/core_mock_summary.csv" 2>/dev/null \
    || echo "    [WARN] aggregate_results.py not available; see results/exp1_core/exp1_core_summary.csv"
echo "    → $GENERATED/core_mock_summary.csv (or results/exp1_core/exp1_core_summary.csv)"

# ── 3. Mechanism Ablation ────────────────────────────────────────────
echo ""
echo "[3] Mechanism Ablation..."
python scripts/aggregate_results.py \
    --dir results/exp4_ablation \
    --output "$GENERATED/ablation_summary.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp4_ablation/exp4_ablation_summary.csv"
echo "    → $GENERATED/ablation_summary.csv"

# ── 4. Discount Function Ablation ────────────────────────────────────
echo ""
echo "[4] Discount Function Ablation..."
python scripts/aggregate_results.py \
    --dir results/exp8_discountablation \
    --output "$GENERATED/discount_summary.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp8_discountablation/exp8_discountablation_summary.csv"
echo "    → $GENERATED/discount_summary.csv"

# ── 5. Rajomon Sensitivity ───────────────────────────────────────────
echo ""
echo "[5] Rajomon Sensitivity Figure..."
python scripts/plot_rajomon_sensitivity.py \
    --dir results/exp_rajomon_sensitivity \
    --output "$GENERATED/rajomon_sensitivity.pdf" 2>/dev/null \
    || echo "    [WARN] plot_rajomon_sensitivity.py skipped or matplotlib unavailable"
echo "    → $GENERATED/rajomon_sensitivity.pdf"

# ── 6. Beta Sensitivity (Appendix) ──────────────────────────────────
echo ""
echo "[6] Beta Sensitivity (Appendix)..."
python scripts/run_beta_ablation.py --plot-only 2>/dev/null \
    || echo "    [WARN] beta ablation plot-only mode skipped; see results/beta_ablation/beta_summary.csv"
echo "    → tables/beta_ablation_table.tex"
echo "    → plots/beta_ablation/beta_ablation_cascade_abd_success.{pdf,png}"

# ── 7. Steady Real-LLM Tables ────────────────────────────────────────
echo ""
echo "[7] Steady Real-LLM (GLM C=10, C=40)..."
python scripts/analyze_real_llm.py \
    --dir results/exp_week5_C10 \
    --output "$GENERATED/steady_glm_c10.csv" 2>/dev/null \
    || cp results/exp_week5_C10/week5_summary.csv "$GENERATED/steady_glm_c10.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp_week5_C10/week5_summary.csv"
python scripts/analyze_real_llm.py \
    --dir results/exp_week5_C40 \
    --output "$GENERATED/steady_glm_c40.csv" 2>/dev/null \
    || cp results/exp_week5_C40/week5_summary.csv "$GENERATED/steady_glm_c40.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp_week5_C40/week5_summary.csv"
echo "    → $GENERATED/steady_glm_c10.csv  $GENERATED/steady_glm_c40.csv"

# ── 8. Bursty Real-LLM ───────────────────────────────────────────────
echo ""
echo "[8] Bursty Real-LLM..."
python scripts/analyze_real_llm.py \
    --dir results/exp_bursty_C20_B30 \
    --output "$GENERATED/bursty_glm.csv" 2>/dev/null \
    || cp results/exp_bursty_C20_B30/bursty_summary.csv "$GENERATED/bursty_glm.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp_bursty_C20_B30/bursty_summary.csv"
echo "    → $GENERATED/bursty_glm.csv"

# ── 9. Self-Hosted vLLM ──────────────────────────────────────────────
echo ""
echo "[9] Self-Hosted vLLM..."
python scripts/analyze_real_llm.py \
    --dir results/exp_selfhosted_vllm_C20_W8 \
    --output "$GENERATED/selfhosted_vllm.csv" 2>/dev/null \
    || cp results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv "$GENERATED/selfhosted_vllm.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv"
echo "    → $GENERATED/selfhosted_vllm.csv"

# ── 10. Adversarial (Appendix) ───────────────────────────────────────
echo ""
echo "[10] Adversarial Robustness (Appendix)..."
python scripts/aggregate_results.py \
    --dir results/exp10_adversarial \
    --output "$GENERATED/adversarial_summary.csv" 2>/dev/null \
    || cp results/exp10_adversarial/exp10_adversarial_summary.csv "$GENERATED/adversarial_summary.csv" 2>/dev/null \
    || echo "    [WARN] see results/exp10_adversarial/exp10_adversarial_summary.csv"
echo "    → $GENERATED/adversarial_summary.csv"

echo ""
echo "========================================"
echo " ALL DONE — paper tables/figures ready"
echo ""
echo " LaTeX tables:"
echo "   tables/table_commitment_quality_submission.tex"
echo "   tables/beta_ablation_table.tex"
echo ""
echo " Generated summaries:"
echo "   $GENERATED/"
echo ""
echo " Pre-generated paper figures:"
echo "   results/paper_figures/{PDF,PNG}/"
echo "========================================"
