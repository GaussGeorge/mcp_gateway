#!/usr/bin/env bash
# reproduce_appendix_from_cache.sh
# -------------------------------------------------------------------
# Level 1 (Appendix): Regenerate appendix tables and figures
# from cached CSV traces. No API key required.
# Expected runtime: < 5 minutes.
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GENERATED="$REPO_ROOT/results/generated"
mkdir -p "$GENERATED"

echo "========================================"
echo " PlanGate: Appendix Tables from Cache"
echo "========================================"

# ── Beta Sensitivity Table ────────────────────────────────────────────
echo ""
echo "[1] Beta sensitivity table (Appendix)..."
if [ -f "results/beta_ablation/beta_summary.csv" ]; then
    python scripts/run_beta_ablation.py --plot-only 2>/dev/null \
        && echo "    → tables/beta_ablation_table.tex" \
        && echo "    → plots/beta_ablation/" \
        || echo "    [WARN] plot-only failed; table at tables/beta_ablation_table.tex already exists"
else
    echo "    [WARN] No beta_summary.csv — run 'python scripts/run_beta_ablation.py' first (mock, no API, ~7 min)"
fi

# ── Adversarial Robustness Table ───────────────────────────────────────
echo ""
echo "[2] Adversarial robustness (Appendix)..."
SRC="results/exp10_adversarial/exp10_adversarial_summary.csv"
if [ -f "$SRC" ]; then
    python scripts/aggregate_results.py \
        --dir results/exp10_adversarial \
        --output "$GENERATED/adversarial_summary.csv" 2>/dev/null \
        || cp "$SRC" "$GENERATED/adversarial_summary.csv"
    echo "    → $GENERATED/adversarial_summary.csv"
else
    echo "    [WARN] $SRC not found; skipping"
fi

# ── Alpha Sensitivity ─────────────────────────────────────────────────
echo ""
echo "[3] Alpha sensitivity (Appendix)..."
SRC="results/exp_alpha_sweep/alpha_sweep_summary.csv"
if [ -f "$SRC" ]; then
    python scripts/update_paper_figures.py --exp alpha_sweep 2>/dev/null \
        || cp "$SRC" "$GENERATED/alpha_sweep_summary.csv"
    echo "    → plots/alpha_sweep/ (or $GENERATED/alpha_sweep_summary.csv)"
else
    echo "    [WARN] $SRC not found; skipping"
fi

# ── Self-Hosted vLLM High Contention ─────────────────────────────────
echo ""
echo "[4] Self-hosted vLLM high-contention (Appendix)..."
SRC="results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv"
if [ -f "$SRC" ]; then
    cp "$SRC" "$GENERATED/selfhosted_vllm_appendix.csv"
    echo "    → $GENERATED/selfhosted_vllm_appendix.csv"
else
    echo "    [WARN] $SRC not found; skipping"
fi

echo ""
echo "========================================"
echo " Appendix reproduction done."
echo ""
echo " Key appendix outputs:"
echo "   tables/beta_ablation_table.tex"
echo "   plots/beta_ablation/*.pdf"
echo "   $GENERATED/adversarial_summary.csv"
echo "========================================"
