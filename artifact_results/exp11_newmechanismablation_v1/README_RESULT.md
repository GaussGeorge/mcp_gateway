# Exp11 New Mechanism Ablation Evidence

Post-P3/P4 new mechanism ablation mock diagnostic/regression evidence.

This bundle contains:

- `exp11_newmechanismablation_summary.csv`
- `exp11_newmechanismablation_agg.csv`
- `validation.json`

It is a lightweight artifact bundle only. It intentionally omits raw per-run
session traces, logs, `.env`, `.venv`, `.gocache`, and the full `results/`
tree.

## Scope

- Experiment: `Exp11_NewMechanismAblation`
- Variants:
  - `plangate_full`
  - `wo_commitment`
  - `wo_amendment`
  - `wo_recovery`
- Runs per variant: `5`
- Validation result: `errors = []`

## Aggregated Results

- `plangate_full`: `runs=5`, `success_mean=90.6`, `cascade_failed_mean=0.0`,
  `EffGP/s=55.38`, `P95=854.4`, `E2E_P95=3201.84`
- `wo_commitment`: `runs=5`, `success_mean=82.2`, `cascade_failed_mean=0.0`,
  `EffGP/s=50.64`, `P95=806.52`, `E2E_P95=2802.34`
- `wo_amendment`: `runs=5`, `success_mean=87.6`, `cascade_failed_mean=0.0`,
  `EffGP/s=56.75`, `P95=904.34`, `E2E_P95=3498.90`
- `wo_recovery`: `runs=5`, `success_mean=88.0`, `cascade_failed_mean=0.2`,
  `EffGP/s=55.60`, `P95=866.94`, `E2E_P95=3470.46`

## Interpretation

This is a mock diagnostic/regression ablation under the standard Exp11 load
shape. It should not be over-claimed as final evidence for failure-recovery
behavior; P3/failure-specific workloads remain the stronger evidence for
Recovery/Amendment mechanisms.

The value of this bundle is narrower and practical: it shows that after the
P3/P4 mechanism additions, the mock experiment framework can still isolate
single-mechanism toggles for Commitment, Amendment, and Recovery without
breaking the standard summary pipeline.

## Related Dry-Run Checks

CloudLab checkpoint-store dry-run compatibility was also verified in code-path
checks during this refresh:

- `redis` recovery-store dry-run accepted
- `memory` recovery-store was normalized to `inmemory` and accepted
