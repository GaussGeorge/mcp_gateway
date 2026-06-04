# business_workflow_service_v3 (JSS Main Workflow Case Study)

This artifact is frozen as the JSS-oriented main business service workflow case
study for PlanGate. It extends `business_workflow_service_v2`, which is retained
as proof-of-concept evidence, into a concurrency/failure sensitivity sweep.

## Role in the Paper

- Use this artifact for the main service-workflow governance claims.
- Use `operator_overhead_v1` separately for failure-free operator/deployability
  overhead claims.
- Do not interpret raw success alone as the ranking metric. The intended claim
  is a service-governance trade-off across admission, cascade failures, wasted
  service work, recovery, and side-effect safety.

## Experimental Design

- 5 variants x 3 concurrency levels (20/50/100) x 3 failure rates
  (0.0/0.1/0.2) x 5 repeats.
- 200 sessions per run.
- P&S and ReAct sessions are both present.
- Business workflows are mock service compositions, not real payment, order,
  travel, or support production systems.
- ReAct recovery uses real `X-Recovery-Mode: resume` requests followed by
  client-side continuation. No post-hoc recovery estimation is used.
- Bootstrap 95% confidence intervals use 10,000 iterations with seed 20260602.

## Variants

- `ng`
- `plangate_strict`
- `plangate_adaptive`
- `plangate_no_capacity_step0`
- `plangate_adaptive_react_recovery`

## Files

- `business_workflow_service_v3_run_summary.csv`: run-level source for paper
  claims.
- `business_workflow_service_v3_category_summary.csv`: workflow/mode breakdown
  only; it intentionally contains no recovery fields.
- `business_workflow_service_v3_agg.csv`: run-level aggregate table.
- `business_workflow_service_v3_category_agg.csv`: workflow/mode aggregate
  table.
- `business_workflow_service_v3_effects.csv`: pairwise effects with bootstrap
  95% confidence intervals.
- `validation.json`
- `README_RESULT.md`

## Metrics

- `workflow_success_rate = success / total_sessions`
- `admitted_sessions = total_sessions - rejected_s0`
- `admitted_success_rate = success / admitted_sessions`, or 0 if admitted is 0
- `step0_reject_rate = rejected_s0 / total_sessions`
- `effective_goodput = success / elapsed_seconds`
- `wasted_service_ms` and `wasted_tool_calls` quantify mid-session work lost to
  failed workflows.

## Interpretation

This artifact supports the claim that PlanGate's session-aware governance
reduces cascade/wasted-work risk under business service workflows and enables
real client-cooperative ReAct recovery without duplicate side effects.

Do not claim universal raw-success dominance. In some cells,
`plangate_no_capacity_step0` may admit more workflows and show higher raw
success, but it pays with higher cascade and/or wasted service work. The paper
should report both `workflow_success_rate` and `admitted_success_rate`.

The duplicate-side-effect claim is side-effect safety under real recovery and
continuation. It is not a duplicate-reduction claim against a retry-only
baseline.

## Validation Summary

Validation records `errors=[]`, complete 225-run grid coverage, 1350
workflow/mode category rows, no recovery metric duplication, recovery equal to 0
at failure rate 0.0, real recovery activity for failure rates 0.1/0.2, zero
duplicate side effects, no client timeouts, and no non-empty error fields.
