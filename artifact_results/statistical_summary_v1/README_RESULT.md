# Statistical Summary Artifact

This bundle is a statistical summary artifact computed from existing artifact CSV files.
It does not run new experiments and does not modify mechanism/runtime code.

## Method

- Encoding: all CSV inputs are read with utf-8-sig (PowerShell BOM compatible).
- Bootstrap seed: 20260528.
- Bootstrap CI: percentile 95% bootstrap CI on means and pairwise mean deltas.
- Role of CI: uncertainty description only, not strict causal significance proof.

## Evidence Boundary

- Repeats < 3 are marked descriptive_only=true and are not assigned strong CI evidence.
- Provider-backed GLM/DeepSeek evidence (if added later) should be treated as descriptive/boundary,
  not strong statistical claim evidence.
- cloudlab_included=true.
  When true, the bundle includes the lightweight CloudLab Redis-vs-memory random-routing artifact
  as shared-state correctness / diagnostic-control evidence rather than throughput-dominance evidence.

## Outputs

- statistical_summary.csv
- effect_size_summary.csv
- claim_summary.csv
- validation.json
- README_RESULT.md
