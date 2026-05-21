# PlanGate Supplementary Artifact Cache

**Version**: 2026-05-15  
**Repository**: https://github.com/GaussGeorge/mcp_gateway  
**Branch**: b-paper-strengthening-pareto

---

## What Is This?

This directory is a **curated supplementary artifact** for the PlanGate paper.
It contains only the CSV summary files and pre-built tables that are needed to
regenerate all paper figures and tables from cached data — without running any
experiments.

**This is NOT the full `results/` working directory.** The local `results/`
directory contains over 2000 CSV files, including pilot experiments, smoke
tests, failed trials, neutral-prompt variants, and historical exploratory runs.
This artifact contains only the **38 whitelisted files** that correspond to
final paper results.

---

## What Is Included

| Item | Path in this archive | Paper Location |
|------|---------------------|----------------|
| Core mock performance | `exp1_core/exp1_core_summary.csv` | §4.2 / Table 3 |
| Fairness boxplot session data | `exp1_core/{gw}_run{1-5}_sessions.csv` | Figure 6 |
| Mechanism ablation | `exp4_ablation/exp4_ablation_summary.csv` | §4.3 / Table 4 |
| Discount function ablation | `exp8_discountablation/exp8_discountablation_summary.csv` | §4.3 / Table 5 |
| Scalability stress test | `exp9_scalestress/exp9_scalestress_summary.csv` | Scalability figure |
| Adversarial robustness | `exp10_adversarial/exp10_adversarial_summary.csv` | Appendix |
| Cross-LLM + token efficiency (GLM) | `exp_real3_glm/summary_all.csv` | Figure 3 |
| Cross-LLM + token efficiency (DeepSeek) | `exp_real3_deepseek/summary_all.csv` | Figure 3 |
| Alpha sensitivity | `exp_alpha_sweep/alpha_sweep_summary.csv` | Appendix |
| Beta sensitivity | `beta_ablation/beta_summary.csv` | Appendix |
| Rajomon sensitivity | `exp_rajomon_sensitivity/rajomon_sensitivity.csv` | §4.3 / Figure 2 |
| Steady real-LLM GLM C=10 | `exp_week5_C10/week5_summary.csv` | §4.4 / Table 6 |
| Steady real-LLM GLM C=40 | `exp_week5_C40/week5_summary.csv` | §4.4 / Table 6 |
| Bursty real-LLM | `exp_bursty_C20_B30/bursty_summary.csv` | §4.5 / Table 7 |
| Self-hosted vLLM | `exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv` | §4.5 / Table 8 |
| Commitment quality table (pre-built) | `paper_figures/table_commitment_quality.tex` | §4.1 / Table 2 |
| Pareto frontier (B-strengthening) | `pareto_frontier_selected/pareto_summary.csv` | Pareto figure |
| Manifest (markdown) | `manifests/artifact_cache_manifest.md` | — |
| Manifest (CSV) | `manifests/artifact_cache_manifest.csv` | — |

---

## What Is Excluded (and Why)

| Excluded | Reason |
|----------|--------|
| `results/neutral_multitool_real_llm/` | Neutral-prompt experiment (not the PlanGate governance prompt) |
| `results/neutral_real_llm/` | Neutral-prompt experiment |
| `results/exp_real3_deepseek/_corrupted_backup/` | Corrupted data from failed runs |
| `results/log/_diag*` | Diagnostic debug output |
| `results/exp_week5_pilot/` | Pilot calibration runs (superseded by C10/C40 with 5 repeats) |
| `results/exp_week2_smoke/`, `exp_pp_smoke/` | Smoke tests |
| `results/exp_week5_real_llm/` | Early 1-repeat pilot (superseded by exp_week5_C10) |
| `results/exp_sbac30/`, `exp_conc_sweep_deepseek/`, `exp_deepseek_n3/` | Exploratory/undocumented experiments |
| `results/exp_selfhosted_vllm_C10_W8/` | C=10 config; paper uses C=20 |
| `results/exp2_*` through `exp7_*`, `exp11_*`, `exp12_*` | Intermediate mock experiments not in final paper |
| `results/evolution_8runs.csv` | Parameter tuning log, not a paper figure |
| All raw per-run per-step logs (`steps.csv`, per-run `.csv` outside whitelist) | Raw logs not needed for figure regeneration |

---

## How to Use

### Step 1: Unpack

If you received this as a zip (`supplementary_artifact_cache.zip`):
```bash
unzip supplementary_artifact_cache.zip
# This creates artifact_cache/ in the repo root
```

On Windows:
```powershell
Expand-Archive supplementary_artifact_cache.zip -DestinationPath .
```

### Step 2: Regenerate Figures (Windows PowerShell)

```powershell
.\scripts\artifact_smoke.ps1 -Target figures-from-cache
```

This calls `python scripts/gen_paper_figures.py --cache-dir artifact_cache`
and outputs figures to `paper/figures/`.

### Step 3: Regenerate Figures (Linux / WSL2 / macOS)

```bash
make figures-from-cache
# or equivalently:
python scripts/gen_paper_figures.py --cache-dir artifact_cache
```

### Step 4: Regenerate Per-Item (no API key required)

```bash
# Core mock
python scripts/aggregate_results.py --dir artifact_cache/exp1_core --output results/generated/core_mock_summary.csv

# Real-LLM steady
python scripts/analyze_real_llm.py --dir artifact_cache/exp_week5_C10 --output results/generated/steady_c10.csv
python scripts/analyze_real_llm.py --dir artifact_cache/exp_week5_C40 --output results/generated/steady_c40.csv

# Bursty
python scripts/analyze_real_llm.py --dir artifact_cache/exp_bursty_C20_B30 --bursty --output results/generated/bursty.csv

# Beta ablation plot only
python scripts/run_beta_ablation.py --plot-only --cache-dir artifact_cache
```

---

## API Key Requirements

| Item | API Key Needed? |
|------|----------------|
| All mock experiments (Table 3, 4, 5, Appendix) | ❌ No |
| PlanGate-R Recovery (Table 9) | ❌ No (go test) |
| Pareto Frontier Figure | ❌ No |
| Generating figures from cached CSVs | ❌ No |
| Live re-run: Steady real-LLM (Tables 6, 7) | ✅ Yes (GLM-4-Flash key) |
| Live re-run: vLLM (Table 8) | ✅ GPU + vLLM endpoint |

---

## Integrity

After unpacking, verify the SHA256 checksum:
```bash
sha256sum supplementary_artifact_cache.zip
# Compare with: artifact_cache/SHA256SUMS.txt (if provided)
```

---

## See Also

- `docs/REPRODUCIBILITY.md` — full reproduction guide
- `docs/RESULT_MAPPING.md` — paper item → data → command mapping
- `docs/artifact_cache_manifest.md` — detailed whitelist and curation policy
- `ARTIFACT_SCOPE.md` — scope declaration (what is/isn't in this artifact)
