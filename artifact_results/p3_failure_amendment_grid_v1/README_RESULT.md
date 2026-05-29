# P3 Failure/Amendment Rate Grid Artifact

This artifact summarizes the formal P3 failure/amendment rate grid.

Parameters
- failure rates: `0.1, 0.2, 0.3`
- amendment rates: `0.1, 0.2`
- sessions: `200`
- concurrency: `50`
- repeats: `3`
- recovery store: `inmemory`

Variants
- `plangate_full`
- `wo_commitment`
- `wo_amendment`
- `wo_recovery`

Interpretation Boundary
- This is mechanism-generalization evidence across a small failure/amendment grid.
- It is not a throughput-dominance benchmark.
- The trend deltas below are descriptive diagnostics and are not used as validation failure conditions.

Trend Deltas
- `full_vs_wo_commitment_success_delta_by_cell`: {'0.1/0.1': -2.6666, '0.1/0.2': 15.6667, '0.2/0.1': 4.6667, '0.2/0.2': 13.3334, '0.3/0.1': 6.0, '0.3/0.2': 12.3333}
- `full_vs_wo_recovery_cascade_delta_by_cell`: {'0.1/0.1': -14.3334, '0.1/0.2': -43.0, '0.2/0.1': -33.0, '0.2/0.2': -54.0, '0.3/0.1': -48.0, '0.3/0.2': -59.6667}
- `full_vs_wo_amendment_amendment_success_delta_by_cell`: {'0.1/0.1': 2.0, '0.1/0.2': 3.6667, '0.2/0.1': 3.6667, '0.2/0.2': 7.0, '0.3/0.1': 4.6667, '0.3/0.2': 12.0}
