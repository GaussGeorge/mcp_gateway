# Table and Figure Mapping — PlanGate Artifact v7-submission

This document maps every paper table and figure to its frozen data source,
generation script, and exact output location.

**Frozen data** lives in `artifact_cache/` (committed to this repository).  
Running `python scripts/setup_frozen_results.py` copies it to `results/` so
all scripts can find it without requiring a live re-run.

**No API keys are required** for Tier A (mock) or Tier B (cached real-LLM)
verification. Tier C (live re-run) is optional.

---

## Quick verification commands

```bash
# Step 0: set up frozen results (one-time, no API key)
python scripts/setup_frozen_results.py

# Step 1: verify all paper tables against frozen CSVs
python scripts/_verify_paper_data.py

# Step 2: verify bursty N=7 table
python scripts/_compute_bursty_stats.py

# Step 3: verify tput-latency table
python scripts/_compute_tput_latency_stats.py --show-crossings

# Step 4: regenerate all paper figures
python scripts/gen_paper_figures.py
python scripts/plot_rajomon_sensitivity.py
```

---

## Tables

| Paper Table | Section | Frozen Data | Verification Script | Expected Output |
|-------------|---------|-------------|---------------------|-----------------|
| Table: Commitment Quality (tab:commitment-quality) | §4.1 | `artifact_cache/manifests/` + go test | `go test ./plangate/... -run TestRuntime -v` | pass/fail per mode |
| Table: Core Mock (tab:exp1) | §4.2 | `artifact_cache/exp1_core/exp1_core_summary.csv` | `scripts/_verify_paper_data.py` | `OK` lines for NG/SRL/SBAC/PlanGate |
| Table: Mechanism Ablation (tab:ablation) | §4.3 | `artifact_cache/exp4_ablation/exp4_ablation_summary.csv` | `scripts/_verify_paper_data.py` | `OK` lines for Full/wo-BL/wo-SC |
| Table: Discount Function Ablation (tab:discount) | §4.3 | `artifact_cache/exp8_discountablation/exp8_discountablation_summary.csv` | `scripts/_verify_paper_data.py` | `OK` for quadratic/linear/exp/log |
| Table: Rajomon Sensitivity (tab:rajomon-sens) | §4.3 | `artifact_cache/exp_rajomon_sensitivity/rajomon_sensitivity.csv` | `scripts/_verify_paper_data.py` | `OK` for price_step ∈ {5,10,20,50,100} |
| Table: Real-LLM C=10 (tab:reallm) | §4.4 | `artifact_cache/exp_week5_C10/week5_summary.csv` | `scripts/_verify_paper_data.py` | `OK` for NG/SRL/PlanGate C=10 |
| Table: Real-LLM C=40 (tab:reallm) | §4.4 | `artifact_cache/exp_week5_C40/week5_summary.csv` | `scripts/_verify_paper_data.py` | `OK` for NG/SRL/PlanGate C=40 |
| Table: Scalability (tab:scalestress) | §4.5 | `artifact_cache/exp9_scalestress/exp9_scalestress_summary.csv` | `scripts/_verify_paper_data.py` | `OK` for C ∈ {200,400,600,800,1000} |
| Table: Adversarial (tab:adversarial) | §4.5 | `artifact_cache/exp10_adversarial/exp10_adversarial_summary.csv` | `scripts/_verify_paper_data.py` | `OK` for NG/SBAC/PlanGate |
| Table: Bursty Real-LLM (tab:bursty) | §4.6 | `artifact_cache/exp_bursty_C20_B30/bursty_summary.csv` | `scripts/_compute_bursty_stats.py` | N=7 means ± std printed |
| Table: Self-Hosted vLLM (tab:selfhosted) | §4.7 | `artifact_cache/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv` | `scripts/_verify_paper_data.py` | `OK` for NG/PlanGate |
| Table: Tput-Latency Sweep (tab:tput_latency) | §4.8 | `artifact_cache/exp_tput_latency/tput_latency_summary.csv` | `scripts/_compute_tput_latency_stats.py` | values matching paper per C ∈ {10,20,30,40,60,80} |

---

## Figures

| Paper Figure | Section | Frozen Data | Generation Script | Output File |
|--------------|---------|-------------|-------------------|-------------|
| Fig. architecture (fig:arch) | §3 | N/A (hand-drawn) | N/A | `paper/figures/architecture.png` |
| Fig. Rajomon sensitivity (fig:rajomon_sens) | §4.3 | `artifact_cache/exp_rajomon_sensitivity/rajomon_sensitivity.csv` | `scripts/plot_rajomon_sensitivity.py` | `paper/figures/rajomon_sensitivity.pdf` |
| Fig. Token efficiency (fig:token_efficiency) | §4.6 | `artifact_cache/exp_real3_glm/summary_all.csv` + `artifact_cache/exp_real3_deepseek/summary_all.csv` | `scripts/gen_paper_figures.py` → `fig_token_efficiency()` | `paper/figures/chart4_token_efficiency.pdf` |
| Fig. Fairness boxplot (fig:fairness) | §4.6 | `artifact_cache/exp1_core/*_run*_sessions.csv` | `scripts/gen_paper_figures.py` → `fig_fairness_boxplot()` | `paper/figures/chart6_fairness.pdf` |
| Fig. Tput-latency sweep (fig:tput_latency) | §4.8 | `artifact_cache/exp_tput_latency/tput_latency_summary.csv` | `scripts/gen_paper_figures.py` → `fig_tput_latency()` | `paper/figures/tput_latency_sweep.pdf` |
| Fig. Cross-LLM comparison (fig:cross_llm) | §4.4 | `artifact_cache/exp_real3_glm/summary_all.csv` + deepseek | `scripts/gen_paper_figures.py` → `fig_cross_llm()` | `paper/figures/cross_llm_comparison.pdf` |
| Fig. Mock cascade (fig:mock_cascade) | §4.2 | `artifact_cache/exp1_core/exp1_core_summary.csv` | `scripts/gen_paper_figures.py` → `fig_exp1_cascade()` | `paper/figures/mock_cascade_comparison.pdf` |
| Fig. Exp4 ablation (fig:exp4) | §4.3 | `artifact_cache/exp4_ablation/exp4_ablation_summary.csv` | `scripts/gen_paper_figures.py` → `fig_exp4_ablation()` | `paper/figures/exp4_ablation.pdf` |
| Fig. Exp8 discount ablation (fig:exp8) | §4.3 | `artifact_cache/exp8_discountablation/exp8_discountablation_summary.csv` | `scripts/gen_paper_figures.py` → `fig_exp8_discount()` | `paper/figures/exp8_discount_ablation.pdf` |
| Fig. Exp9 scalability (fig:exp9) | §4.5 | `artifact_cache/exp9_scalestress/exp9_scalestress_summary.csv` | `scripts/gen_paper_figures.py` → `fig_exp9_scalability()` | `paper/figures/exp9_scalability.pdf` |
| Fig. Exp10 adversarial (fig:exp10) | §4.5 | `artifact_cache/exp10_adversarial/exp10_adversarial_summary.csv` | `scripts/gen_paper_figures.py` → `fig_exp10_adversarial()` | `paper/figures/exp10_adversarial.pdf` |

---

## Reproduction tiers

| Tier | Description | API key needed? | Time estimate |
|------|-------------|-----------------|---------------|
| **A — Frozen verification** | Run `_verify_paper_data.py` + `gen_paper_figures.py` against cached CSVs | No | < 5 min |
| **B — Mock re-run** | Re-run all mock experiments from scratch via `run_all_experiments.py` | No | 30–45 min |
| **C — Live real-LLM** | Re-run GLM / DeepSeek / vLLM experiments (requires provider key or GPU) | Yes / GPU | Hours |

Tier A is the recommended starting point for artifact reviewers.
