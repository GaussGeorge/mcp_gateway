# PlanGate Supplementary Artifact Cache — Manifest

**Version**: 2026-05-15  
**Branch**: b-paper-strengthening-pareto  
**Purpose**: Curated selection of CSV summaries and pre-built files needed to
regenerate all paper tables and figures from cached data.  
**Policy**: Only paper-used CSV summaries and required auxiliary files are
included. Raw per-run logs, pilot experiments, failed trials, neutral-prompt
variants, corrupted backups, smoke tests, and intermediate exploratory
experiments are excluded.

> This manifest describes the contents of `artifact_cache/` (the conference
> supplementary artifact). `results/` (local working directory) is NOT committed
> to the public repository and is NOT the same as `artifact_cache/`.

---

## Section 1: Included Files (Whitelist)

| Paper Item | Source CSV (under `results/`) | Destination (under `artifact_cache/`) | Included? | API Key for Live Rerun? | Notes |
|-----------|-------------------------------|---------------------------------------|-----------|------------------------|-------|
| **Table 2**: Commitment Quality (CSV verification) | `exp_week4_formal/week2_smoke_summary.csv` | `exp_week4_formal/week2_smoke_summary.csv` | ✅ YES | No | Verified by `scripts/_verify_paper_data.py` for Commitment Quality metrics |
| **Table 2**: Commitment Quality (pre-built LaTeX reference) | `paper_figures/table_commitment_quality.tex` | `paper_figures/table_commitment_quality.tex` | ✅ YES | No | Pre-built reference LaTeX table from `exp_week4_formal`; retained for reference |
| **Table 3**: Core Mock Performance | `exp1_core/exp1_core_summary.csv` | `exp1_core/exp1_core_summary.csv` | ✅ YES | No | §4.2 / Table 3; mock backend, no API |
| **Figure: Fairness Boxplot** | `exp1_core/{gw}_run{1..5}_sessions.csv` (20 files) | `exp1_core/{gw}_run{1..5}_sessions.csv` | ✅ YES | No | Session-level step data for `fig_fairness_boxplot()` in `gen_paper_figures.py` |
| **Table 4**: Mechanism Ablation | `exp4_ablation/exp4_ablation_summary.csv` | `exp4_ablation/exp4_ablation_summary.csv` | ✅ YES | No | §4.3 mechanism ablation |
| **Table 5**: Discount Function Ablation | `exp8_discountablation/exp8_discountablation_summary.csv` | `exp8_discountablation/exp8_discountablation_summary.csv` | ✅ YES | No | §4.3 discount ablation |
| **Figure: Scalability** | `exp9_scalestress/exp9_scalestress_summary.csv` | `exp9_scalestress/exp9_scalestress_summary.csv` | ✅ YES | No | Referenced by `gen_paper_figures.py fig_exp9_scalability()` |
| **Appendix: Adversarial Robustness** | `exp10_adversarial/exp10_adversarial_summary.csv` | `exp10_adversarial/exp10_adversarial_summary.csv` | ✅ YES | No | §5.5 / Appendix adversarial robustness |
| **Figure: Cross-LLM Comparison (GLM)** | `exp_real3_glm/summary_all.csv` | `exp_real3_glm/summary_all.csv` | ✅ YES | Yes (live rerun) | Used by `gen_paper_figures.py fig_cross_llm()` and `fig_token_efficiency()` |
| **Figure: Cross-LLM Comparison (DeepSeek)** | `exp_real3_deepseek/summary_all.csv` | `exp_real3_deepseek/summary_all.csv` | ✅ YES | Yes (live rerun) | Per-run per-gateway CSVs and `_corrupted_backup/` are excluded |
| **Appendix: Alpha Sensitivity** | `exp_alpha_sweep/alpha_sweep_summary.csv` | `exp_alpha_sweep/alpha_sweep_summary.csv` | ✅ YES | No | Appendix α sensitivity sweep |
| **Appendix: Beta Sensitivity** | `beta_ablation/beta_summary.csv` | `beta_ablation/beta_summary.csv` | ✅ YES | No | Appendix β sensitivity ablation |
| **Figure 2 / Rajomon Sensitivity** | `exp_rajomon_sensitivity/rajomon_sensitivity.csv` | `exp_rajomon_sensitivity/rajomon_sensitivity.csv` | ✅ YES | No | §4.3 / Figure 2 Rajomon price-step sensitivity |
| **Table 6**: Steady Real-LLM C=10 | `exp_week5_C10/week5_summary.csv` | `exp_week5_C10/week5_summary.csv` | ✅ YES | Yes (live rerun) | §4.4 GLM-4-Flash C=10 |
| **Table 6**: Steady Real-LLM C=40 | `exp_week5_C40/week5_summary.csv` | `exp_week5_C40/week5_summary.csv` | ✅ YES | Yes (live rerun) | §4.4 GLM-4-Flash C=40 |
| **Table 7**: Bursty Real-LLM | `exp_bursty_C20_B30/bursty_summary.csv` | `exp_bursty_C20_B30/bursty_summary.csv` | ✅ YES | Yes (live rerun) | §4.5 Bursty GLM C=20 B=30 |
| **Table 8 / Appendix**: Self-Hosted vLLM | `exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv` | `exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv` | ✅ YES | No (GPU) | §4.5 / Appendix vLLM C=20 W=8; GPU required for live rerun |
| **Table 9**: PlanGate-R Recovery | N/A (code only) | N/A | N/A | No | Reproduced by `go test ./plangate/... -run TestRuntime`; no CSV needed |
| **Pareto Frontier Figure** (B-Strengthening) | `pareto_frontier_selected/pareto_summary.csv` | `pareto_frontier_selected/pareto_summary.csv` | ✅ YES | No | Admission-vs-cascade tradeoff; all PlanGate configs: cascade=0 |

**Total included files**: 17 CSVs + 1 pre-built `.tex` + 20 session files = **38 files**

---

## Section 2: Excluded Files (Suspicious / Not-for-Supplement)

| Directory / Pattern | Reason for Exclusion | File Count |
|--------------------|---------------------|-----------|
| `neutral_multitool_real_llm/` | Neutral-prompt experiment — uses generic prompts, not the PlanGate governance prompt; not a paper result | ~400 CSVs |
| `neutral_real_llm/` | Neutral-prompt experiment — same reason | ~100 CSVs |
| `exp_real3_deepseek/_corrupted_backup/` | Corrupted data from failed Deepseek runs; explicitly named backup | ~15 CSVs |
| `log/_diag*`, `log/_diag2*` | Diagnostic/debug output, not experimental results | 2 CSVs |
| `exp_week5_pilot/` | Pilot runs (C=20/30/40, single repeat each) used to calibrate C=10/C=40 final runs | ~17 CSVs |
| `exp_week2_smoke/` | Smoke-test from week 2, not a paper experiment | 1 CSV |
| `exp_week4_formal/week2_smoke_summary.csv` | INCLUDED (paper-used) — verified by `_verify_paper_data.py` for Commitment Quality | 0 CSV excluded |
| `exp_pp_smoke/` | Smoke test for progress-priority, not a paper experiment | ~30 CSVs |
| `exp_week5_real_llm/` | Early 1-repeat pilot run superseded by `exp_week5_C10` (5 repeats) | ~5 CSVs |
| `exp_sbac30/` | Exploratory SBAC-30 config test, not referenced in paper | 1 CSV |
| `exp_conc_sweep_deepseek/` | Exploratory DeepSeek concurrency sweep, not in paper whitelist | ~25 CSVs |
| `exp_deepseek_n3/` | DeepSeek C=3 prototype, not in paper whitelist | ~10 CSVs |
| `exp_selfhosted_vllm_C10_W8/` | C=10 config; paper uses C=20 (`exp_selfhosted_vllm_C20_W8`) | ~7 CSVs |
| `exp2_heavyratio/`, `exp3_mixedmode/`, `exp5_scaleconc/`, `exp6_scaleconcreact/`, `exp7_clientreject/`, `exp11_bursty/`, `exp12_longtail/` | Intermediate/exploratory mock experiments not in final paper whitelist | ~350 CSVs |
| `evolution_8runs.csv` | 8-round tuning evolution log, not a standalone paper table/figure | 1 CSV |

**Total excluded**: ~965 suspicious CSVs (out of 2283 total)

---

## Section 3: Missing / Reported Items

| Paper Item | Expected Path | Exists? | Action |
|-----------|--------------|---------|--------|
| Table 2 pre-built LaTeX | `paper_figures/table_commitment_quality.tex` | ✅ Yes | Include |
|Exp9 scalestress (scalability figure) | `exp9_scalestress/exp9_scalestress_summary.csv` | ✅ Yes | Include — referenced by `gen_paper_figures.py` |
| `exp_week4_formal/` raw steps.csv | `exp_week4_formal/{gw}/*/steps.csv` | ✅ Yes | Included in cache for traceability; primary verifier uses `week2_smoke_summary.csv` |
| `results/paper_figures/PNG/`, `PDF/` | `paper_figures/PNG/*.png`, `PDF/*.pdf` | ✅ Yes | **NOT included** — these are generated outputs, not source data |
| PlanGate-R Recovery CSV | N/A (code only) | N/A | Use `go test ./plangate/... -run TestRuntime` |

---

## Section 4: Directory Structure of artifact_cache/

```
artifact_cache/
├── README.md                                ← instructions for conference artifact recipients
├── manifests/
│   ├── artifact_cache_manifest.md           ← this file
│   └── artifact_cache_manifest.csv          ← machine-readable version
│
├── exp1_core/
│   ├── exp1_core_summary.csv                ← Table 3 (core mock performance)
│   ├── ng_run{1..5}_sessions.csv            ← fairness boxplot (Figure 6)
│   ├── srl_run{1..5}_sessions.csv
│   ├── sbac_run{1..5}_sessions.csv
│   └── plangate_full_run{1..5}_sessions.csv
│
├── exp4_ablation/
│   └── exp4_ablation_summary.csv            ← Table 4 (mechanism ablation)
│
├── exp8_discountablation/
│   └── exp8_discountablation_summary.csv    ← Table 5 (discount function ablation)
│
├── exp9_scalestress/
│   └── exp9_scalestress_summary.csv         ← Scalability figure
│
├── exp10_adversarial/
│   └── exp10_adversarial_summary.csv        ← Appendix: adversarial robustness
│
├── exp_real3_glm/
│   └── summary_all.csv                      ← Cross-LLM + token efficiency figures (GLM)
│
├── exp_real3_deepseek/
│   └── summary_all.csv                      ← Cross-LLM + token efficiency figures (DeepSeek)
│
├── exp_alpha_sweep/
│   └── alpha_sweep_summary.csv              ← Appendix: α sensitivity
│
├── beta_ablation/
│   └── beta_summary.csv                     ← Appendix: β sensitivity
│
├── exp_rajomon_sensitivity/
│   └── rajomon_sensitivity.csv              ← Figure 2 / Rajomon sensitivity
│
├── exp_week5_C10/
│   └── week5_summary.csv                    ← Table 6 (Steady GLM C=10)
│
├── exp_week5_C40/
│   └── week5_summary.csv                    ← Table 6 (Steady GLM C=40)
│
├── exp_bursty_C20_B30/
│   └── bursty_summary.csv                   ← Table 7 (Bursty GLM C=20 B=30)
│
├── exp_selfhosted_vllm_C20_W8/
│   └── selfhosted_c20_summary.csv           ← Table 8 (vLLM C=20 W=8)
│
├── paper_figures/
│   └── table_commitment_quality.tex         ← Table 2 (pre-built LaTeX)
│
└── pareto_frontier_selected/
    └── pareto_summary.csv                   ← Pareto frontier figure (B-strengthening)
```

---

## Section 5: Curation Policy

1. Only CSV summaries and derived aggregates explicitly referenced in the paper or by `scripts/gen_paper_figures.py` are included.
2. Raw per-run per-step logs are excluded unless required by a specific figure function.
3. Pilot, smoke, failed, corrupted, deprecated, and neutral-prompt experiment outputs are excluded.
4. The `_corrupted_backup/` directory inside `exp_real3_deepseek/` is never included.
5. No API keys, `.env` files, or credentials are present in this artifact.
6. The zip is intended for conference supplementary artifact upload only.
7. `artifact_cache/` is **not** committed to the public GitHub repository.
