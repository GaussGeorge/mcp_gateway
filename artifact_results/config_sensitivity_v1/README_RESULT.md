# config_sensitivity_v1 (FULL)

This artifact evaluates configuration robustness, not parameter tuning.
The default intensity matches business_workflow_service_v3.
The light/default/strict labels represent moderate changes to session capacity
and adaptive wait parameters.
The workload reuses business workflow service semantics.
The experiment does not claim raw-success dominance.
The goal is to test whether cascade/waste/recovery/side-effect safety signals
remain stable under moderate configuration changes.
business_workflow_service_v3 remains the main case study; this artifact is
supporting robustness evidence.

## Design
- 6 configurable variant-intensity combinations × 2 concurrency (50/100) × 3 failure_rate (0.0/0.1/0.2) × 3 repeats
- Plus control variant (plangate_no_capacity_step0)
- 200 sessions per run, P&S and ReAct
- ReAct recovery: real X-Recovery-Mode: resume + continuation
- Bootstrap 95% CI with 10000 iterations (seed=20260602)

## Intensity definitions

Default matches business_workflow_service_v3 actual defaults confirmed from:
scripts/run_business_workflow_service_v3.py:119,129 (green=200,yellow=50,red=0); cmd/gateway/main.go:152-156 (Go flag defaults); plangate/adaptive_admission.go:50-52 (DefaultAdaptiveAdmissionConfig); start_gateway() line 310 (session_cap=30)

Light: less conservative (higher cap, shorter waits)
Default: v3 defaults
Strict: more conservative (lower cap, longer green wait)

## Variants
- plangate_adaptive_light (intensity=light, cap=48, green=100, yellow=25, red=0)
- plangate_adaptive_react_recovery_light (intensity=light, cap=48, green=100, yellow=25, red=0)
- plangate_adaptive_default (intensity=default, cap=30, green=200, yellow=50, red=0)
- plangate_adaptive_react_recovery_default (intensity=default, cap=30, green=200, yellow=50, red=0)
- plangate_adaptive_strict (intensity=strict, cap=16, green=300, yellow=50, red=0)
- plangate_adaptive_react_recovery_strict (intensity=strict, cap=16, green=300, yellow=50, red=0)
- plangate_no_capacity_step0 (intensity=control_no_capacity, cap=30, green=200, yellow=50, red=0)

## Files
- config_sensitivity_summary.csv (126 rows full)
- config_sensitivity_category_summary.csv (workflow/mode breakdown, no recovery fields)
- config_sensitivity_agg.csv
- config_sensitivity_effects.csv (42 rows, bootstrap 95% CI)
- validation.json
- README_RESULT.md

## Metrics
- workflow_success_rate = success / total_sessions
- admitted_sessions = total_sessions - rejected_s0
- admitted_success_rate = success / admitted_sessions (0 if admitted=0)
- step0_reject_rate = rejected_s0 / total_sessions
- effective_goodput = success / elapsed_seconds

## Notes
- This artifact does NOT select optimal parameters.
- The default intensity is the business_workflow_service_v3 configuration.
- The experiment verifies robustness, not parameter tuning.
- recovery metrics at fr=0.0 must be 0.
- duplicate_side_effect must be 0.
- category_summary contains no recovery fields.
