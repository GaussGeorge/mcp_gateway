# PlanGate Paper-to-Artifact Mapping

This document maps each significant paper item to the corresponding artifact
components: source data, minimal sanity command, and full reproduction command.

**Disclaimer:**
> Minimal commands reproduce *qualitative sanity trends only*.
> Exact paper numbers require full experimental settings (5 repeats, 500 sessions,
> C=200, tuned parameters) and are reproduced from cached CSVs or via the full
> reproduction scripts. See [REPRODUCIBILITY.md](REPRODUCIBILITY.md) and
> [RESULT_MAPPING.md](RESULT_MAPPING.md) for the complete path.

---

## Quick Reference Table

| Paper Item | What it Demonstrates | Minimal Command | Full Command | API Key? | Sanity Check |
|-----------|---------------------|-----------------|-------------|----------|-------------|
| **Table 2** — Commitment Quality | PlanGate reduces ABD-like failures vs NG/SBAC under P&S overload | `python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1` | `bash scripts/reproduce_mock_core.sh` | No | plangate_full success rate > sbac > ng |
| **Table 3** — Core Mock Performance | PlanGate reduces cascade rate, improves effective goodput | `python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1` | `bash scripts/reproduce_mock_core.sh` | No | cascade rate: plangate_full < sbac < ng |
| **Table 4** — Mechanism Ablation | Budget-lock / reservation mechanism matters | `python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1` | `python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 5` | No | wo_budgetlock more mid-session failures than plangate_full |
| **Table 9** — PlanGate-R Recovery | Checkpoint resume reduces replay waste | `go test ./plangate/... -run "TestRuntime" -v -timeout 120s` | `go test ./plangate/... -timeout 120s` | No | PlanGate-R tool calls < naive retry; completed steps not replayed |
| **Tables 6–8** — Real LLM / vLLM | PlanGate governs commercial + self-hosted LLM sessions | *(optional, not part of minimal artifact)* | See [REPRODUCIBILITY.md](REPRODUCIBILITY.md) Level 3 | **Yes** | N/A for minimal check |

---

## Detail Per Paper Item

### Table 2 — Mode-Stratified Commitment Quality (`exp_week4_formal`)

- **Paper location:** §4.1 / Table 2
- **Claim:** PlanGate reduces ABD-like (abandoned / budget-depleted) sessions
  compared to NG and SBAC under high-concurrency P&S+ReAct mixed workload.
- **Source data:** `results/exp_week4_formal/` (not committed; cached LaTeX at
  `results/paper_figures/table_commitment_quality.tex`)
- **Minimal sanity command (no API key, mock only):**
  ```bash
  python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1
  ```
  Note: Exp1_Core is the pure P&S version of the core experiment. The full
  Table 2 uses a mixed P&S+ReAct run from `exp_week4_formal`. The Exp1_Core
  smoke validates the same qualitative ordering under pure P&S.
- **Full reproduction from cache:**
  ```bash
  bash scripts/reproduce_main_paper_from_cache.sh
  ```
- **Expected sanity:** `plangate_full` success rate > `sbac` > `ng` under P&S overload.

---

### Table 3 — Core Mock Performance (`exp1_core`)

- **Paper location:** §4.2 / Table 3
- **Claim:** PlanGate reduces cascade failure rate and improves effective
  goodput (tool calls per session) compared to NG, SRL, SBAC.
- **Source data:** `results/exp1_core/` (not committed)
- **Minimal sanity command (no API key):**
  ```bash
  python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1
  ```
  Configuration: 500 sessions, C=200, P&S ratio=1.0, 4 gateways (NG, SRL, SBAC, PlanGate-Full).
- **Full reproduction from cache:**
  ```bash
  bash scripts/reproduce_mock_core.sh
  # or to regenerate from scratch (5 repeats, ~30 min):
  python scripts/run_all_experiments.py --exp Exp1_Core --repeats 5
  ```
- **Expected sanity (1 repeat, verified 2026-05-13 on `public-artifact-clean`):**
  | Policy | success | cascade_failed | effective_goodput |
  |--------|---------|----------------|-------------------|
  | ng | 22 | 88 | 248 |
  | srl | 37 | 88 | 417 |
  | sbac | 46 | 27 | 527 |
  | plangate_full | 80 | **0** | **673** |
  - Cascade rate: `plangate_full`=0 < `sbac`=27 < `ng`/`srl`=88
  - Effective goodput: `plangate_full` > `sbac` > `srl` > `ng`
  - No guarantee of exact paper numbers in a single repeat; qualitative ordering is stable.
  - Verified runtime: 1.2 min on Windows 4-core developer machine.

---

### Table 4 — Mechanism Ablation (`exp4_ablation`)

- **Paper location:** §4.3 / Table 4
- **Claim:** The budget-lock mechanism (`wo_budgetlock` variant) is essential;
  removing it significantly increases mid-session failures and cascade rate.
- **Source data:** `results/exp4_ablation/` (not committed)
- **Minimal sanity command (no API key):**
  ```bash
  python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1
  ```
  Policies: `plangate_full` vs `wo_budgetlock` vs `wo_sessioncap`.
- **Full reproduction from cache:**
  ```bash
  python scripts/update_paper_tables.py --exp ablation
  # or re-run:
  python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 5
  ```
- **Expected sanity (1 repeat, verified 2026-05-13 on `public-artifact-clean`):**
  | Policy | success | cascade_failed | effective_goodput |
  |--------|---------|----------------|-------------------|
  | plangate_full | 85 | 1 | **724** |
  | wo_budgetlock | 21 | 9 | **118** |
  | wo_sessioncap | 69 | 0 | 602 |
  - `wo_budgetlock.effective_goodput` (118) is ~83% lower than `plangate_full` (724).
  - Confirms budget-lock is the dominant mechanism for effective goodput.
  - Verified runtime: 0.8 min on Windows 4-core developer machine.

---

### Table 9 (Appendix) — PlanGate-R Recovery Extension

- **Paper location:** Appendix / PlanGate-R section
- **Claim:** PlanGate-R (checkpoint resume) reduces wasted tool calls compared
  to naive retry, while matching naive retry on eventual session success rate.
  Completed steps are not replayed.
- **Mechanism:** P&S controlled mock runtime only. Injected recoverable failures
  (`DeadlineExceeded`-class). No real LLM. No ReAct. Not a primary paper contribution.
- **Minimal sanity command (no API key, no running server, Go tests only):**
  ```bash
  # All PlanGate-R runtime integration tests
  go test ./plangate/... -run "TestRuntime" -v -timeout 120s
  ```
  Key individual tests:
  ```bash
  # No-replay: completed steps called exactly once
  go test ./plangate/... -run "TestRuntimePlanGateRRecoversWithoutReplay" -v

  # Compute saving: PlanGate-R uses fewer total steps than naive retry
  go test ./plangate/... -run "TestRuntimePlanGateRVsNaiveRetryComputeSaving" -v

  # Base vs R: PlanGate-base fails, PlanGate-R succeeds eventually
  go test ./plangate/... -run "TestRuntimePlanGateBaseFailsWithoutRecovery" -v
  ```
  Also covers experiment-level compute-saving stats:
  ```bash
  go test ./plangate/... -run "TestPlanGateRComputeSavingVsNaiveRetry" -v
  ```
- **Full reproduction:** Same Go tests; the full suite is `go test ./plangate/... -timeout 120s`.
  There is no separate "Level 2" script needed — all claims are validated at the Go test level.
- **Expected sanity:**
  - `TestRuntimePlanGateRRecoversWithoutReplay`: each completed tool handler called exactly once (no replay).
  - `TestRuntimePlanGateRVsNaiveRetryComputeSaving`: PlanGate-R saves ≥ 30% compute at failure_rate=0.3.
  - `TestRuntimePlanGateBaseFailsWithoutRecovery`: base fails all S sessions; PlanGate-R succeeds.
- **Scope reminder:** P&S controlled mock only. Results quantify the checkpoint-resume
  mechanism; they do not generalize to real-LLM or production failure modes.

---

### Tables 6–8 — Real LLM / vLLM Experiments (Optional)

- **Paper location:** §4.4–4.5 / Tables 6–8
- **Claim:** PlanGate outperforms baselines under commercial API (GLM-4-Flash) and
  self-hosted vLLM workloads.
- **Status:** **Not part of the minimal artifact.** Requires external LLM API credential
  or GPU-based vLLM instance.
- **Minimal command:** Not applicable (requires real LLM infrastructure).
- **Full reproduction:**
  ```bash
  # From cached data (no API key needed to regenerate tables):
  bash scripts/reproduce_real_llm_from_cache.sh

  # Live re-run (requires LLM_API_KEY in .env):
  bash scripts/reproduce_real_llm_live.sh
  ```
- **API key required:** Yes (for live re-run). No (for table regeneration from cached CSVs).

---

## Coverage Summary

| Paper Item | Minimal Sanity | Needs API Key | Runtime (minimal) |
|-----------|---------------|--------------|-------------------|
| Table 2 (commitment quality) | ✅ Exp1_Core --repeats 1 | No | ~1–5 min (verified 1.2 min) |
| Table 3 (core mock perf) | ✅ Exp1_Core --repeats 1 | No | ~1–5 min (verified 1.2 min) |
| Table 4 (mechanism ablation) | ✅ Exp4_Ablation --repeats 1 | No | ~1–3 min (verified 0.8 min) |
| Table 9 (PlanGate-R recovery) | ✅ go test -run TestRuntime | No | < 2 min |
| Tables 6–8 (real LLM/vLLM) | ❌ optional only | **Yes (live)** | Variable |
