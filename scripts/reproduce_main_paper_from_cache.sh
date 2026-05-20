#!/usr/bin/env bash
# reproduce_main_paper_from_cache.sh
# -------------------------------------------------------------------
# One-click: verify all paper tables and regenerate figures from
# frozen artifact_cache/ CSVs. No API key required. No live experiment.
# Expected runtime: < 5 minutes.
# -------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "========================================"
echo " PlanGate: Reproduce Paper from Cache"
echo " Root: $REPO_ROOT"
echo "========================================"

# ── Step 0: Populate results/ from artifact_cache/ ──────────────────
echo ""
echo "[0] Populating results/ from artifact_cache/ ..."
python scripts/setup_frozen_results.py
echo "    OK"

# ── Step 1: Verify all paper tables ─────────────────────────────────
echo ""
echo "[1] Verifying paper tables (Tables 1-10) ..."
python scripts/_verify_paper_data.py
echo "    OK"

# ── Step 2: Verify bursty N=7 table ─────────────────────────────────
echo ""
echo "[2] Verifying bursty statistics (N=7) ..."
python scripts/_compute_bursty_stats.py
echo "    OK"

# ── Step 3: Verify throughput-latency crossing table ────────────────
echo ""
echo "[3] Verifying throughput-latency crossings ..."
python scripts/_compute_tput_latency_stats.py --show-crossings
echo "    OK"

# ── Step 4: Regenerate all paper figures ────────────────────────────
echo ""
echo "[4] Regenerating paper figures ..."
python scripts/gen_paper_figures.py
echo "    → results/paper_figures/PDF/  results/paper_figures/PNG/"

# ── Step 5: Regenerate Rajomon sensitivity figure ───────────────────
echo ""
echo "[5] Regenerating Rajomon sensitivity figure ..."
python scripts/plot_rajomon_sensitivity.py \
    --dir results/exp_rajomon_sensitivity \
    --output results/paper_figures/PDF/rajomon_sensitivity.pdf 2>/dev/null \
    && echo "    → results/paper_figures/PDF/rajomon_sensitivity.pdf" \
    || echo "    [WARN] plot_rajomon_sensitivity.py skipped (matplotlib unavailable?)"

echo ""
echo "========================================"
echo " ALL DONE"
echo ""
echo " Figures: results/paper_figures/PDF/  results/paper_figures/PNG/"
echo "========================================"

exit 0
