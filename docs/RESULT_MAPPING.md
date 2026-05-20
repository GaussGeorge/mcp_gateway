# PlanGate Paper Result Mapping

This document maps every paper table/figure to its source data directory,
generation script, and reproduce command.

All paths are relative to the repository root.

> **Supplementary Cache column**: "✅ Suppl." means the cached CSV is available
> in the conference supplementary artifact (NOT committed to this public
> repository; `results/` is excluded via `.gitignore`). "✅ Re-run" means the
> result can be reproduced from scratch with no API key in ≤5 min.

---

## Paper Items → Data → Commands

| Paper Item | Paper Location | Source Data | Minimal Command | API Key? | Suppl. Cache? | Re-run (no key)? |
|-----------|---------------|-------------|-----------------|----------|--------------|-----------------|
| **Table 2**: Commitment Quality | §4.1 / Table 2 | `exp_week4_formal/` | `run_all_experiments.py --exp Exp1_Core --repeats 1` | No | ✅ Suppl. | ✅ (qualitative trend) |
| **Table 3**: Core Mock Performance | §4.2 | `exp1_core/` | `run_all_experiments.py --exp Exp1_Core --repeats 1` | No | ✅ Suppl. | ✅ (qualitative trend) |
| **Table 4**: Mechanism Ablation | §4.3 / ablation | `exp4_ablation/` | `run_all_experiments.py --exp Exp4_Ablation --repeats 1` | No | ✅ Suppl. | ✅ (qualitative trend) |
| **Table**: Discount Function Ablation | §4.3 / discount | `exp8_discountablation/` | *(full run only)* | No | ✅ Suppl. | ⚠️ Long (~45 min) |
| **Figure/Table**: Rajomon Sensitivity | §4.3 / sensitivity | `exp_rajomon_sensitivity/` | *(full run only)* | No | ✅ Suppl. | ⚠️ Long |
| **Figure**: Alpha Sensitivity | Appendix | `exp_alpha_sweep/` | *(full run only)* | No | ✅ Suppl. | ⚠️ Long |
| **Table**: Beta Sensitivity | Appendix | `beta_ablation/` | *(full run only)* | No | ✅ Suppl. | ⚠️ Long |
| **Table 9**: PlanGate-R Recovery | Appendix | Go test only | `go test ./plangate/... -run TestRuntime -v` | No | N/A (code-only) | ✅ (< 2 min) |
| **Pareto Frontier Figure** | B-Strengthening | `pareto_frontier_selected/` | `run_pareto_frontier.py --selected --dry-run` | No | ✅ Suppl. | ✅ `--selected --repeats 1` |
| **Tables 6–8**: Steady Commercial API (C=10) | §4.4 | `exp_week5_C10/` | *(optional)* | **Yes** | ✅ Suppl. | ❌ Needs API key |
| **Tables 6–8**: Steady Commercial API (C=40) | §4.4 | `exp_week5_C40/` | *(optional)* | **Yes** | ✅ Suppl. | ❌ Needs API key |
| **Tables 6–8**: Bursty Real-LLM | §4.5 | `exp_bursty_C20_B30/` | *(optional)* | **Yes** | ✅ Suppl. | ❌ Needs API key |
| **Tables 6–8**: Self-Hosted vLLM | §4.5 / Appendix | `exp_selfhosted_vllm_C20_W8/` | *(optional)* | **Yes (GPU)** | ✅ Suppl. | ❌ Needs GPU+API |
| **Table**: Adversarial Robustness | Appendix | `exp10_adversarial/` | *(full run only)* | No | ✅ Suppl. | ⚠️ Long |

---

## Detail per Paper Item

### Table 1 — Mode-Stratified Commitment Quality

- **Paper location**: §4.1 (main text), Table 1
- **Experiment**: 7 gateways × 5 repeats × 200 sessions, mixed P&S+ReAct  
  Gateways: NG, Rajomon, Rajomon+SB, SBAC, Prog.Priority, PG-noRes, PlanGate
- **Source CSV**: `results/exp_week4_formal/{ng,rajomon,rajomon_sb,sbac,pp,pg_nores,plangate_full}/`
  - Each gateway subdirectory contains `steps.csv` (per-step) and `steps_sessions.csv`
- **Pre-generated LaTeX**: `results/paper_figures/table_commitment_quality.tex`
- **Reproduce from cache**:
  ```bash
  python scripts/update_paper_tables.py --exp commitment_quality
  ```
- **Live rerun**: Not needed; modify `scripts/run_all_experiments.py --exp Exp_CommitmentQuality`
- **notes**: Data is from `exp_week4_formal`. The 7-gateway version with `rajomon_sb` and `pg_nores` was finalized here.

---

### Core Mock Performance Table

- **Paper location**: §4.2, core performance table
- **Experiment**: 500 sessions, C=200, 5 repeats, 4 gateways (NG/SRL/SBAC/PlanGate)
- **Source CSV**: `results/exp1_core/`
  - Summary: `results/exp1_core/exp1_core_summary.csv`
  - Per-run: `results/exp1_core/ng_run{1..5}.csv`, `plangate_full_run{1..5}.csv`, etc.
- **Reproduce from cache**:
  ```bash
  python scripts/aggregate_results.py --dir results/exp1_core --output results/generated/core_mock_summary.csv
  ```
- **Live rerun**:
  ```bash
  python scripts/run_all_experiments.py --exp Exp1_Core --repeats 5
  ```

---

### Mechanism Ablation Table

- **Paper location**: §4.3, ablation section
- **Experiment**: PlanGate Full / wo-BudgetLock / wo-SessionCap, 500 sessions, C=200, 5 repeats
- **Source CSV**: `results/exp4_ablation/`
  - Summary: `results/exp4_ablation/exp4_ablation_summary.csv`
- **Reproduce from cache**:
  ```bash
  python scripts/aggregate_results.py --dir results/exp4_ablation --output results/generated/ablation_summary.csv
  ```
- **Live rerun**: `python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 5`

---

### Discount Function Ablation Table

- **Paper location**: §4.3, discount section
- **Experiment**: quadratic / exponential / linear / logarithmic / none, 500 sessions, C=200
- **Source CSV**: `results/exp8_discountablation/`
  - Summary: `results/exp8_discountablation/exp8_discountablation_summary.csv`
- **Reproduce from cache**:
  ```bash
  python scripts/aggregate_results.py --dir results/exp8_discountablation --output results/generated/discount_summary.csv
  ```
- **Live rerun**: `python scripts/run_all_experiments.py --exp Exp8_DiscountAblation --repeats 5`

---

### Rajomon Sensitivity Figure / Table

- **Paper location**: §4.3 or Figure 2
- **Experiment**: price_step ∈ {5, 10, 20, 50, 100}, 500 sessions, C=200, Rajomon only
- **Source CSV**: `results/exp_rajomon_sensitivity/{ps5,ps10,ps20,ps50,ps100}/`
- **Reproduce from cache**:
  ```bash
  python scripts/plot_rajomon_sensitivity.py --dir results/exp_rajomon_sensitivity --output results/generated/rajomon_sensitivity.pdf
  ```
- **Live rerun**: `python scripts/rajomon_sensitivity.py --repeats 5`

---

### Alpha Sensitivity (Appendix)

- **Paper location**: Appendix, α sensitivity
- **Source CSV**: `results/exp_alpha_sweep/`
  - Summary: `results/exp_alpha_sweep/alpha_sweep_summary.csv`
- **Reproduce from cache**:
  ```bash
  python scripts/run_alpha_sweep.py --plot-only
  ```
  *(注：`update_paper_figures.py` 已归档至 `scripts/archive/`，请使用上方命令)*

---

### Beta Sensitivity (Appendix)

- **Paper location**: Appendix, β sensitivity ablation
- **Experiment**: β ∈ {0, 0.5, 1, 2, 3}, pure ReAct, 500 sessions, C=200, 5 repeats, mock backend
- **Source CSV**: `results/beta_ablation/beta_summary.csv`
- **LaTeX table**: `tables/beta_ablation_table.tex`
- **Reproduce from cache** (re-plot only):
  ```bash
  python scripts/run_beta_ablation.py --plot-only
  ```
- **Live rerun** (mock only, ~7 min, no API):
  ```bash
  python scripts/run_beta_ablation.py
  ```
- **outputs**: `plots/beta_ablation/`, `tables/beta_ablation_table.tex`, `results/beta_ablation/beta_ablation_report.md`

---

### Steady Commercial API — GLM-4-Flash (C=10 and C=40)

- **Paper location**: §4.4 steady real-LLM table
- **Experiment**: 4 gateways (NG, Rajomon, PP, PlanGate), 200 sessions, 5 repeats
- **Source CSV**:
  - C=10: `results/exp_week5_C10/week5_summary.csv`  
    (per-gateway dirs: `ng/`, `rajomon/`, `pp/`, `plangate_real/`)
  - C=40: `results/exp_week5_C40/week5_summary.csv`  
    (per-gateway dirs: `ng/`, `rajomon/`, `pp/`, `plangate_real/`)
- **Reproduce from cache**:
  ```bash
  python scripts/analyze_real_llm.py --dir results/exp_week5_C10 --output results/generated/steady_glm_c10.csv
  python scripts/analyze_real_llm.py --dir results/exp_week5_C40 --output results/generated/steady_glm_c40.csv
  ```
- **Live rerun** (requires API key, ~60–90 min):
  ```bash
  # Set credentials first:
  # export LLM_API_BASE="https://open.bigmodel.cn/api/paas/v4"
  # export LLM_API_KEY="<your-key>"
  bash scripts/run_exp_real3.sh         # C=10
  bash scripts/run_exp_real3_all.sh     # C=10 + C=40
  ```
- **API required for live rerun**: Yes (GLM-4-Flash / ZhipuAI)

---

### Bursty Real-LLM Table

- **Paper location**: §4.5 bursty table
- **Experiment**: 4 gateways, GLM-4-Flash, C=20, burst_size=30, burst_gap=8s, 3 repeats
- **Source CSV**: `results/exp_bursty_C20_B30/bursty_summary.csv`
  - Per-gateway dirs: `ng/`, `rajomon/`, `pp/`, `plangate_real/`
- **Reproduce from cache**:
  ```bash
  python scripts/analyze_real_llm.py --dir results/exp_bursty_C20_B30 --bursty --output results/generated/bursty_glm.csv
  ```
- **Live rerun** (requires API key):
  ```bash
  python scripts/run_real_llm_bursty.py --repeats 3 --burst-size 30 --workers 10
  ```
- **API required for live rerun**: Yes (GLM-4-Flash)

---

### Self-Hosted vLLM Table (Appendix)

- **Paper location**: §4.5 / Appendix, high-contention vLLM  
- **Experiment**: C=20, workers=8, NG + PlanGate, 3 repeats
- **Source CSV**: `results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv`
- **Reproduce from cache**:
  ```bash
  python scripts/analyze_real_llm.py --dir results/exp_selfhosted_vllm_C20_W8 --output results/generated/selfhosted_vllm.csv
  ```
- **Live rerun**: Requires local vLLM instance; see `scripts/run_selfhosted_vllm.py --help`
- **API required for live rerun**: No (local GPU / vLLM endpoint required)

---

### Adversarial Robustness (Appendix)

- **Paper location**: Appendix, adversarial section
- **Experiment**: 10% malicious agents, 500 sessions, C=200, 5 repeats
- **Source CSV**: `results/exp10_adversarial/`
  - Summary: `results/exp10_adversarial/exp10_adversarial_summary.csv`
- **Reproduce from cache**:
  ```bash
  python scripts/aggregate_results.py --dir results/exp10_adversarial --output results/generated/adversarial_summary.csv
  ```
- **Live rerun**: `python scripts/run_all_experiments.py --exp Exp10_Adversarial --repeats 5`

---

## Output Files Generated by Cached Reproduction

After running `bash scripts/reproduce_main_paper_from_cache.sh`:

```
results/generated/
├── core_mock_summary.csv
├── ablation_summary.csv
├── discount_summary.csv
├── rajomon_sensitivity.pdf
├── alpha_sweep_summary.csv
├── steady_glm_c10.csv
├── steady_glm_c40.csv
├── bursty_glm.csv
├── selfhosted_vllm.csv
└── adversarial_summary.csv
tables/
├── table_commitment_quality.tex   ← from results/paper_figures/
└── beta_ablation_table.tex        ← regenerated by run_beta_ablation.py --plot-only
plots/beta_ablation/
├── beta_ablation_cascade_abd_success.pdf
└── beta_ablation_gp_latency.pdf
```

---

## Mapping: scripts → experiments

| Script | Experiment |
|--------|-----------|
| `scripts/run_all_experiments.py` | Exp1–Exp12 mock suite |
| `scripts/run_beta_ablation.py` | Beta sensitivity (mock) |
| `scripts/rajomon_sensitivity.py` | Rajomon sensitivity |
| `scripts/run_alpha_sweep.py` | Alpha sensitivity |
| `scripts/run_exp_real3.sh` / `run_exp_real3_all.sh` | Steady GLM C=10/C=40 |
| `scripts/run_real_llm_bursty.py` | Bursty GLM C=20 B=30 |
| `scripts/run_selfhosted_vllm.py` | Self-hosted vLLM |
| `scripts/plot_rajomon_sensitivity.py` | Rajomon sensitivity figure |
| `scripts/update_paper_tables.py` | Table regeneration from cached CSV |
| `scripts/gen_paper_figures.py` | Figure regeneration from cached CSV (canonical) |
| `scripts/plot_rajomon_sensitivity.py` | Rajomon sensitivity figure |
| `scripts/analyze_real_llm.py` | Real-LLM summary from cached CSV |
| `scripts/aggregate_results.py` | Mock summary from cached CSV |
