# PlanGate Artifact Scope

## Overview

This artifact accompanies the paper **"PlanGate: Sunk-Cost-Aware Dynamic Pricing
for Multi-Step LLM Agent Tool Governance"**. This document clarifies which
experiments and result directories belong to the **paper reproducibility path**
and which are historical diagnostic/exploratory traces that are archived but
**do not appear in the paper**.

---

## 1. Artifact Contents (Paper-Used Experiments)

> **Note on `results/` directories**: All `results/` paths below are excluded
> from this public repository (`.gitignore`). They exist locally after running
> experiments and are distributed via the conference supplementary artifact.

The following experiments and their cached results are part of the paper:

| # | Paper Item | Results Directory | Notes |
|---|------------|-------------------|-------|
| 1 | Commitment Quality (Table 2) | `results/exp_week4_formal/` | 7 gateways × 5 repeats × 200 sessions; LaTeX at `results/paper_figures/table_commitment_quality.tex` |
| 2 | Core Mock Performance (Table 3) | `results/exp1_core/` | 500 sessions, C=200, 5 repeats |
| 3 | Mechanism Ablation (Table 4) | `results/exp4_ablation/` | PlanGate Full / wo-BudgetLock / wo-SessionCap |
| 4 | Discount Function Ablation | `results/exp8_discountablation/` | quadratic / exponential / linear / logarithmic |
| 5 | Rajomon Sensitivity | `results/exp_rajomon_sensitivity/` | price_step ∈ {5, 10, 20, 50, 100} |
| 6 | Alpha Sensitivity | `results/exp_alpha_sweep/` | α sweep |
| 7 | Beta Sensitivity (Appendix) | `results/beta_ablation/` | β ∈ {0, 0.5, 1, 2, 3}, pure ReAct mock; LaTeX at `tables/beta_ablation_table.tex` |
| 8 | Steady Commercial API (GLM C=10) | `results/exp_week5_C10/` | 4 gateways, 200 sessions, C=10 |
| 9 | Steady Commercial API (GLM C=40) | `results/exp_week5_C40/` | 4 gateways, 200 sessions, C=40 |
| 10 | Bursty Real-LLM | `results/exp_bursty_C20_B30/` | GLM-4-Flash bursty, C=20, burst=30 |
| 11 | Self-Hosted vLLM | `results/exp_selfhosted_vllm_C20_W8/` | vLLM C=20, workers=8 |
| 12 | Adversarial Robustness (Appendix) | `results/exp10_adversarial/` | 10% malicious agents |
| 13 | Pareto Frontier Tradeoff (B-Strengthening) | `artifact_cache/pareto_frontier_selected/` | n=3 selected repeats; see `TABLE_FIGURE_MAPPING.md` for data/script mapping |

Additional mock experiments (Exp2–Exp12 series) are referenced in the paper
for breadth evaluation: `results/exp2_heavyratio/` through `results/exp12_longtail/`.

---

## 2. Archived / Diagnostic Results (NOT in Paper)

The following directories contain exploratory, diagnostic, or superseded results.
They are retained for internal traceability but **do not appear in the paper** and
are **not part of the reproducibility path**:

| Directory | Reason Excluded |
|-----------|-----------------|
| `results/neutral_real_llm/` | Early neutral-prompt diagnostic; zero-step sessions observed; not adopted |
| `results/neutral_multitool_real_llm/` | B2-MT-medium experiment; PlanGate did not outperform PP; not adopted |
| `results/neutral_multitool_real_llm/b2_mt_medium/` | Negative result (NG > PlanGate in success %), reported in internal log only |
| `results/exp_real3_deepseek/` | DeepSeek-V3 steady pilot; superseded by standardized week5 suite |
| `results/exp_deepseek_n3/` | DeepSeek pilot, N=3; superseded |
| `results/exp_pp_smoke/` | Smoke test for Progress Priority gateway; diagnostic only |
| `results/exp_sbac30/` | SBAC sensitivity sweep; not in main paper |
| `results/exp_week2_smoke/` | Early smoke test; superseded by week4/week5 formal runs |
| `results/exp_week5_pilot/` | Pilot sweep to select C; superseded by C10/C40 formal |
| `results/exp_week5_real_llm/` | Duplicate/merged run set; superseded by C10/C40 directories |
| `results/smoke_multitool/` | Multitool smoke tests |
| `results/log/` | Runtime logs from various experiments |

Scripts prefixed with `_` (e.g., `scripts/_audit.py`, `scripts/_bursty_check.py`)
are diagnostic utilities, not reproducibility scripts.

---

## 2A. Validated Lightweight CloudLab Evidence

The repository also includes lightweight evidence bundles under
`artifact_results/` for validated CloudLab runs. These directories are meant
for README/artifact-evaluation citation and quick inspection only. They include
compact CSV/JSON summaries and a short README, but intentionally omit full raw
logs and large per-step traces.

| Directory | Included Files | Purpose |
|-----------|----------------|---------|
| `artifact_results/cloudlab_smoke_c2/` | `summary.csv`, `aggregate.csv`, `validation.json`, `README_RESULT.md` | P0-P2 CloudLab correctness smoke evidence |
| `artifact_results/cloudlab_p3_small_sticky_v2/` | `p3_summary.csv`, `p3_adversarial_summary.csv`, `summary.csv`, `validation.json`, `README_RESULT.md` | P3 CloudLab sticky-routing recovery/amendment evidence |
| `artifact_results/cloudlab_p3_small_random_redis_cp_v2/` | `p3_summary.csv`, `p3_adversarial_summary.csv`, `summary.csv`, `validation.json`, `README_RESULT.md` | P4 CloudLab random-routing recovery/amendment evidence with Redis CheckpointStore |

Important boundary:

- `artifact_results/cloudlab_p3_small_sticky_v2/` validates multi-node P3
  execution with **sticky per-session routing** and remains a simpler baseline.
- `artifact_results/cloudlab_p3_small_random_redis_cp_v2/` validates
  **random cross-gateway recovery** for the small CloudLab profile with Redis
  session state and Redis CheckpointStore.

Random cross-gateway recovery is validated for the small CloudLab profile with
Redis session state and Redis checkpoint store.

These artifact bundles do **not** claim medium/large-profile validation,
multi-region deployment, production-grade Redis HA, or Byzantine/malicious
gateway security.

---

## 3. Default Reproducibility Mode

The minimal reproduction path (no API key required):

- **Level 0 — Unit tests**: `go test ./... -timeout 120s` (< 1 min, no server)
- **Level 1 — Mock re-run**: Re-run core mock experiments from scratch  
  `python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1` (~2 min, no API key)  
  `python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1` (~1 min, no API key)  
  `go test ./plangate/... -run "TestRuntime"` — PlanGate-R recovery smoke (< 2 min, no API)
- **Level 2 — From supplementary cache**: regenerate paper tables/figures using cached CSVs  
  Cached CSVs are distributed in `artifact_cache/`. All verification scripts read from  
  frozen data without requiring a live re-run.
- **Level 3 (optional)** — Live real-LLM rerun (requires `.env` API credentials)

See `Makefile` (Linux/macOS/WSL2) or `scripts/artifact_smoke.ps1` (Windows) for one-click targets.

Full cached traces for real-LLM experiments are provided in `artifact_cache/`.
Mock experiments can be re-run from scratch using commands in [README.md](README.md).

See `README.md` § "Minimal Reproduction" for exact commands.

---

## 4. API Keys and Sensitive Information

- `.env` is excluded from git (see `.gitignore`).
- No API keys appear in any committed script; all scripts read from environment variables.
- Live real-LLM reruns require setting `LLM_API_BASE` and `LLM_API_KEY` in `.env`.
- If the `.env` file is absent, live-rerun scripts print a clear error and exit; they do **not** silently skip.

---

## 5. Experiment Not Included: B2-MT-medium Negative Result

The B2-MT-medium experiment (agents=150, C=10, bursty, 4 gateways × 3 repeats)
was conducted as an internal boundary test. Results showed PP outperformed PlanGate on cascade steps (315 vs 356), and PlanGate's success rate was lower than NG (16.4% vs 22.4%). This is an honest result that reveals a non-trivial tradeoff under medium-pressure bursty overload. It is preserved at:

```
results/neutral_multitool_real_llm/b2_mt_medium/medium_report.md
```

This result is **not part of the main paper claims** and is not cited in the paper tables. It is retained here for scientific completeness and to support potential future analysis.

---

## 6. Reproducing without a GPU / commercial API

The mock experiments (Levels 0–1) require only:
- Go 1.21+
- Python 3.10+
- No GPU, no API key, no internet access

Full cached traces for real-LLM experiments are distributed separately through the
conference supplementary artifact; they are not tracked in this public code repository.
