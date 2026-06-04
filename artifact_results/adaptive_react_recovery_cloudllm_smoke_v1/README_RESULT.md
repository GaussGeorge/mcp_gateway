# adaptive_react_recovery_cloudllm_smoke_v1

Provider: deepseek

Variants:
- plangate_strict
- plangate_adaptive
- plangate_no_capacity_step0
- plangate_adaptive_react_recovery

Files:
- cloudllm_smoke_summary.csv
- cloudllm_smoke_agg.csv
- validation.json
- README_RESULT.md

## Smoke Disclaimer

This is smoke evidence only.
It checks execution stability and directional behavior.
It is not a statistically powered comparison.
It is not used to replace the current paper headline results.

Provider APIs are elastic and hidden-queue systems.
This smoke checks compatibility and recovery-path stability only.
No artificial failures were injected; the recovery path may not be exercised
by natural provider errors in a small smoke run.

No dominance claim is made.
