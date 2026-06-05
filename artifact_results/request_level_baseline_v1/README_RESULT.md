# request_level_baseline_v1

This artifact is a request-level governance baseline.
It intentionally ignores session progress, continuation value, and recovery.
It is not a PlanGate mechanism variant.
It tests whether ordinary per-request admission is sufficient for multi-step workflows.

## Design
- Baseline variant: `request_queue_limit`
- request_limit = 30
- request_queue_wait_ms = 50
- concurrency = [50, 100]
- failure_rate = [0.0, 0.2]
- repeats = 5
- sessions_per_run = 200
- workflow mix = same as `business_workflow_service_v3`

## Scope
- Each tool request is admitted or rejected independently.
- The baseline does not inspect session step index or completed prefix.
- The baseline does not use checkpoint recovery, continuation value, or declared-plan commitment.
- Idempotency keys are still forwarded so side-effect safety remains a fair comparison.

## Files
- request_level_baseline_summary.csv
- request_level_baseline_agg.csv
- request_level_baseline_effects.csv
- validation.json
- README_RESULT.md

## Interpretation boundary
- Use this artifact to compare request-level and session-level governance under the same business workflow harness.
- Do not use this artifact to claim universal PlanGate dominance.
- Effect rows compare baseline `request_level_rejected` against PlanGate `rejected_s0` under the common metric name `rejected_events`.

## Effect comparisons
- 32 effect rows against `plangate_adaptive` and `plangate_no_capacity_step0`
