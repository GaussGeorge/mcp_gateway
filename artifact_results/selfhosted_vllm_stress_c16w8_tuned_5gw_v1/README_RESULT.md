# Self-Hosted vLLM Stress Evidence (Submitted 5-Gateway Subset)

This submission bundle records the self-hosted vLLM stress evidence used by the
main paper display subset.

It contains only:

- `selfhosted_vllm_stress_summary.csv`
- `selfhosted_vllm_stress_agg.csv`
- `validation.json`

## Scope

- backend regime: self-hosted vLLM stress
- protocol: $N{=}3$, 80 agents, $C{=}16$, burst arrivals (12 per burst, 5 s gap), `max_steps=8`, 8 backend/GPU workers, vLLM `max-num-seqs=8`
- submitted gateway set:
  - `ng`
  - `static`
  - `pp`
  - `rajomon`
  - `plangate_relaxed`
- aggregate rows: `5`
- validation result: `errors = []`

## Main-Paper Mapping

The submitted artifact contains the main-paper 5-gateway display subset.
Within paper text/figures, `plangate_relaxed` is shown as `PlanGate (tuned)`.

A conservative diagnostic profile was used during local sensitivity checking but
is not included in the submitted vLLM artifact.

## Boundary

This is a local self-hosted vLLM stress artifact, not a CloudLab multi-node
result and not a claim of universal dominance across all governance policies.
Its purpose is to preserve a compact, submission-ready evidence package aligned
with the main-paper self-hosted vLLM figure/table.
