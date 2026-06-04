# adaptive_react_recovery_mock_smoke_v1 (SMOKE)

Variants:
- plangate_strict
- plangate_adaptive
- plangate_no_capacity_step0
- plangate_adaptive_react_recovery

Files:
- mock_smoke_summary.csv
- mock_smoke_agg.csv
- validation.json
- README_RESULT.md

Notes:
- Adaptive Step-0 keeps budget/security/duplicate semantics as hard rejection.
- ReAct recovery here is client-cooperative (resume metadata only, no automatic future DAG replay).

## Smoke Disclaimer

This is smoke evidence only.
It checks execution stability and directional behavior.
It is not a statistically powered comparison.
It is not used to replace the current paper headline results.
