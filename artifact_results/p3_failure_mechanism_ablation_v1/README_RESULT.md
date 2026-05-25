# P3 Failure Mechanism Ablation Evidence

P3 failure/amendment-specific mechanism ablation evidence for Commitment,
Amendment, and Recovery.

This artifact bundle contains only:

- `p3_failure_mechanism_ablation_summary.csv`
- `p3_failure_mechanism_ablation_agg.csv`
- `validation.json`

It intentionally omits full run directories, raw `steps.csv`, raw `sessions.csv`,
logs, `.env`, `.venv`, `.gocache`, and the full `results/` tree.

## Scope

- workload: `p3 failure/amendment`
- sessions: `200`
- concurrency: `50`
- failure_rate: `0.2`
- amendment_rate: `0.2`
- repeats: `3`
- variants:
  - `plangate_full`
  - `wo_commitment`
  - `wo_amendment`
  - `wo_recovery`
- validation: `errors = []`

## Key Results

- `plangate_full`:
  - `success_mean = 192.33`
  - `cascade_failed_mean = 7.67`
  - `recovery_success_mean = 38.0`
  - `amendment_success_mean = 7.67`
  - `commitment_issued_mean = 194.67`
- `wo_commitment`:
  - `success_mean = 178.67`
  - `cascade_failed_mean = 21.33`
  - `recovery_success_mean = 28.33`
  - `amendment_success_mean = 0.0`
  - `commitment_issued_mean = 0.0`
- `wo_amendment`:
  - `success_mean = 183.0`
  - `cascade_failed_mean = 17.0`
  - `recovery_success_mean = 29.67`
  - `amendment_success_mean = 0.0`
  - `commitment_issued_mean = 193.33`
- `wo_recovery`:
  - `success_mean = 158.0`
  - `cascade_failed_mean = 42.0`
  - `recovery_success_mean = 0.0`
  - `amendment_success_mean = 0.0`
  - `commitment_issued_mean = 196.67`

## Interpretation

Full preserves the highest success and nonzero recovery/amendment success.
Disabling commitment removes commitment issuance and lowers success.
Disabling amendment makes `amendment_success` drop to zero.
Disabling recovery makes `recovery_success` drop to zero and increases cascade
failures.

## Caveat

This is a controlled local P3 failure/amendment workload, not a CloudLab
multi-node result. It is stronger than standard mock-load ablation for the new
mechanisms, but should still be reported as local controlled evidence.
