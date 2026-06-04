# architecture_component_ablation_v1

Architecture evidence synthesis artifact. This is not a new experiment.

## Purpose

This artifact maps PlanGate architecture components to expected failure modes
and existing artifact evidence. It supports software architecture contribution
claims for JSS by organizing controlled experiments, system sweeps, targeted
probes, boundary checks, and deployability evidence into a structured evidence
matrix.

## What It Does Not Claim

- It does not claim raw-success dominance for any component.
- It does not claim production Redis HA.
- It does not claim cloud-provider dominance.
- It does not claim duplicate reduction without a retry-only baseline.
- It does not run any new experiments.

## Evidence Strength Definitions

| Strength | Meaning |
|---|---|
| `strong_controlled` | Ablation with active disabled/control variant in a controlled workload |
| `strong_system` | Multi-cell system sweep with real recovery/continuation or workflow evidence |
| `targeted_probe` | Focused experiment targeting one mechanism |
| `diagnostic_boundary` | Smoke-level evidence defining a boundary or limitation |
| `supporting` | Corroborating evidence from another experiment |
| `deployability` | Steady-state overhead and operational-cost measurement |

## Components

9 components across 6 types:

| Type | Count |
|---|---|
| `functional_component` | 2 |
| `recovery_component` | 2 |
| `protocol_integrity_component` | 2 |
| `distributed_state_component` | 1 |
| `boundary_evidence` | 1 |
| `deployability_evidence` | 1 |

## Files

- `component_ablation_matrix.csv`: 9 components, one per row.
- `component_ablation_evidence.csv`: 18 evidence records, two per component.
- `validation.json`: structural and reference integrity checks.
- `README_RESULT.md`: this file.

## Interpretation

Each component protects a distinct software-system property:

- `session_commitment`: bounds mid-session waste.
- `adaptive_capacity_step0`: controls the admission-versus-waste trade-off.
- `react_checkpoint_manager`: preserves completed ReAct progress.
- `recovery_resume_protocol`: enables client-cooperative continuation.
- `commitment_token_integrity`: supports explicit committed-session semantics.
- `idempotency_side_effect_safety`: preserves zero duplicate side effects under
  tested recovery/resume workflows.
- `redis_shared_state`: prevents tested cross-node state misses.
- `provider_aware_boundary`: explains why elastic cloud backends require
  conservative interpretation.
- `operator_overhead_deployability`: quantifies bounded steady-state cost.

## Paper Usage

Use this artifact as an architecture evidence matrix rather than as another
performance experiment. It supports the claim that PlanGate is a componentized
software architecture whose parts protect distinct governance properties. It
should be cited alongside the quantitative artifacts it references.

Acceptable wording:

> Each PlanGate component protects a distinct software-system property: session
> commitment bounds mid-session waste, adaptive admission controls the
> admission-versus-waste trade-off, ReAct checkpointing and resume avoid replay
> after recoverable interruptions, idempotency preserves side-effect safety
> under tested recovery/resume workflows, Redis-backed state prevents tested
> multi-gateway state misses, and provider-boundary evidence explains why
> elastic cloud backends require conservative interpretation.

Avoid wording that implies universal raw-success dominance, production Redis HA,
cloud-provider dominance, or duplicate reduction without a retry-only baseline.
