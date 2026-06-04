# operator_overhead_v1 (JSS Deployability Evidence)

This artifact is frozen as the JSS-oriented operator/runtime overhead evidence
for PlanGate. It evaluates failure-free steady-state cost, not governance
effectiveness.

## Role in the Paper

- Use this artifact for operator overhead and deployability claims.
- Pair it with `business_workflow_service_v3`, which evaluates failure/overload
  governance benefits.
- Do not use this artifact to claim success-rate improvement, duplicate
  reduction, or recovery effectiveness.

## Experimental Design

- 3 variants x 3 concurrency levels (20/50/100) x 5 repeats.
- 1000 lightweight deterministic workflows per run.
- Failure rate is 0.0 for every run.
- No LLM, cloud provider, or vLLM is used.
- Recovery/checkpoint machinery is enabled in the recovery variant but must stay
  idle because no failure is injected.
- CPU/RSS resource sampling uses 0.2 second intervals.

## Variants

- `baseline_gateway`: same gateway binary/path with `--mode ng`; no governance,
  admission, recovery, or checkpoint machinery.
- `plangate_adaptive`: adaptive admission enabled, recovery disabled.
- `plangate_adaptive_react_recovery`: adaptive admission and ReAct recovery
  machinery enabled, with no resume events expected.

## Baseline Validity

The baseline is not direct-to-backend. All variants send traffic through the
same gateway/proxy path and backend path. The baseline isolates governance
overhead over a no-governance gateway path.

## Files

- `operator_overhead_summary.csv` (45 run-level rows)
- `operator_overhead_agg.csv` (9 aggregate rows)
- `operator_overhead_effects.csv` (9 comparison rows)
- `validation.json`
- `README_RESULT.md`

## Metrics

- `workflow_success_rate = success / total_sessions`
- `admitted_sessions = total_sessions - rejected_s0`
- `admitted_success_rate = success / admitted_sessions`
- `step0_reject_rate = rejected_s0 / total_sessions`
- `throughput_workflows_s = total_sessions / elapsed_s`
- `effective_goodput = success / elapsed_s`

## Interpretation

The artifact supports a bounded-overhead claim: user-visible latency and
throughput stay close to the no-governance gateway baseline under failure-free
lightweight workflows. CPU usage increases moderately in this lightweight setup,
and peak RSS rises by only a small number of megabytes. These costs should be
interpreted as the steady-state cost of session-aware governance, traded against
the cascade and wasted-work reductions shown in `business_workflow_service_v3`.

## State Operation Counters

State operation counters were not instrumented in this artifact. `NA` state-op
fields should not be interpreted as zero internal operations.

## Validation Summary

Validation records `errors=[]`, complete 45-run grid coverage, success rate 1.0,
zero client timeouts, zero recovery triggers, zero duplicate side effects, a
same-gateway-path baseline, and sufficient gateway/backend process samples.
