# Self-Hosted vLLM Multi-Intensity Profile Sweep Evidence

This artifact records the self-hosted vLLM multi-intensity congestion profile sweep used for boundary characterization.

## Protocol

- backend: Qwen-3.5-4B served by vLLM (`max-num-seqs=8`)
- repeats: `N=3`
- agents: `80`
- max workers: `8`
- burst pattern: `12` sessions every `5` seconds
- max steps: `8`
- concurrency levels: `C=8/12/16/20`
- gateways: `ng, static, pp, rajomon, plangate_relaxed`

## Paper Mapping and Boundary Narrative

- Paper label mapping: `plangate_relaxed` is displayed as `PlanGate (tuned)`.
- Low contention boundary: at lower concurrency, PlanGate is not always the best-performing gateway.
- High contention boundary (`C=16/20`): `PlanGate (tuned)` preserves higher completion than multiple baselines while keeping cascade pressure lower than several alternatives.
- This bundle does not include `plangate_real`.

## Included Files

- `selfhosted_vllm_profile_sweep_summary.csv`
- `selfhosted_vllm_profile_sweep_agg.csv`
- `validation.json`

## Exclusions

This lightweight artifact intentionally excludes full runtime directories and environment/build artifacts, including:

- full `results/` trees
- logs
- steps/session traces beyond the two summary CSV files
- `.env`
- `.venv`
- `.gocache`

## Validation Snapshot

- row_count: `60`
- agg_row_count: `20`
- errors: `[]`
