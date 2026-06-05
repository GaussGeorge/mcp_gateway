# JSS Supplementary Artifact Guide

This guide accompanies the JSS manuscript and points reviewers to the canonical evidence package, the claim ledger, the figure/table alignment file, and the replay workflow.

## 1. Canonical Evidence Package

The canonical JSS statistical package is:

- `artifact_results/statistical_summary_v4/`

This package is the only canonical numerical source for the current JSS revision. Earlier bundles such as `statistical_summary_v3` are retained for version history, but they are not the main package for the submitted manuscript.

The canonical package contains:

- `statistical_summary.csv`
- `effect_size_summary.csv`
- `claim_summary.csv`
- `validation.json`
- `README_RESULT.md`

The current validator expectation is:

```powershell
python scripts/build_jss_statistical_summary.py --out-dir-name statistical_summary_v4 --include-request-baseline --include-idempotency-baseline
python scripts/validate_jss_artifacts.py
```

Expected result:

- `artifact_results/statistical_summary_v4/validation.json` reports `errors=[]`
- `scripts/validate_jss_artifacts.py` reports `errors=[]`

## 2. Claim Ledger

The table below is a condensed ledger derived from `artifact_results/statistical_summary_v4/claim_summary.csv`.

| Claim ID | Primary artifact | Role | Supported result | Allowed use | Not allowed |
|---|---|---|---|---|---|
| JSS-C1 | `business_workflow_service_v3` | core service workflow | Adaptive governance reduces cascade waste under constrained load. | Main JSS waste-reduction result. | Raw-success dominance. |
| JSS-C2 | `business_workflow_service_v3` | core recovery | Recovery restores checkpointed progress and avoids replay. | Main ReAct recovery result. | Transparent server-side replay. |
| JSS-C3 | `operator_overhead_v1` | deployability overhead | Gateway overhead is bounded in the tested path. | Steady-state overhead evidence. | Recovery effectiveness. |
| JSS-C4 | `config_sensitivity_v1` | configuration sensitivity | Recovery strength changes under parameter perturbation. | Robustness/support result. | Best-parameter claim. |
| JSS-C5 | `recovery_protocol_edge_cases_v1` | protocol usability | Recovery protocol edge cases pass deterministically. | API safety and usability evidence. | Throughput or LLM performance. |
| JSS-C6 | `architecture_component_ablation_v1` | architecture mapping | Components map to evidence records. | Architecture rationale. | Every component improves raw success. |
| JSS-C7 | `vllm_react_recovery_arm_v2` | real-backend recovery | Recovery works on a targeted real-backend arm. | Real-backend recovery feasibility. | Natural full-vLLM recovery claim. |
| JSS-C8 | `cloud_backend_smoke_v1` | provider boundary | Provider elasticity can invert admission trade-offs. | Boundary interpretation. | Global policy ranking. |
| JSS-C9 | `cloudlab_random_redis_memory_v1` | distributed diagnostic | Redis removes state misses relative to memory-local state. | Shared-state diagnostic support. | Production Redis HA. |
| JSS-C10 | `mcpbench_smoke_v3` | workflow-shape smoke | MCP-Bench-derived smoke executes cleanly. | Compatibility/workflow-shape diversity. | Full MCP-Bench accuracy claim. |
| JSS-C11 | `burstgpt_trace_replay_v3` | arrival realism | Trace-shaped replay covers normal/burst/peak-burst windows. | Arrival-pattern realism. | Real agent trace claim. |
| JSS-C12 | `business_workflow_e2e_smoke_v1` | E2E correctness smoke | HTTP+SQLite path preserves idempotency and recovery correctness. | End-to-end correctness. | Real-service performance gain. |
| JSS-C13 | `cloudlab_business_workflow_distributed_v1` | supporting CloudLab correctness | Earlier Redis-vs-memory CloudLab support. | Supporting shared-state evidence only. | Headline CloudLab result. |
| JSS-C14 | `adaptive_react_recovery_smoke_v1` | cross-layer smoke | Mock/cloud/vLLM smoke all validate cleanly. | Execution stability across layers. | Headline performance ranking. |
| JSS-C15 | `business_workflow_e2e_perf_sanity_v1` | E2E performance sanity | Real HTTP+SQLite path remains stable with zero duplicate side effects. | Small real-service sanity result. | Dominance claim on real services. |
| JSS-C16 | `cloudlab_business_workflow_distributed_deterministic_v1` | deterministic CloudLab correctness | Redis recovers `15/15` forced cross-gateway resumes; memory misses `15/15`. | Main distributed correctness result. | CloudLab performance dominance or Redis HA. |
| JSS-C17 | `request_level_baseline_v1` | request-level baseline | Request-level admission cannot reason about completed session prefixes. | Baseline for request-vs-session governance. | Universal PlanGate dominance. |
| JSS-C18 | `idempotency_only_retry_baseline_v1` | idempotency-only baseline | Idempotency protects writes but does not restore checkpoint progress. | Distinguish idempotent retry from checkpoint recovery. | Claim that idempotency is unnecessary or that recovery universally improves raw success. |

## 3. Figure and Table Alignment

Use the companion file:

- `paper/JSS/JSS_FIGURE_TABLE_ALIGNMENT.md`

It maps every figure, table, and algorithm in the JSS manuscript to its source artifact, claim ID, evidence role, and interpretation boundary.

## 4. Artifact Inventory

### 4.1 Main claim artifacts

- `business_workflow_service_v3`
- `request_level_baseline_v1`
- `idempotency_only_retry_baseline_v1`
- `vllm_react_recovery_arm_v2`
- `cloudlab_business_workflow_distributed_deterministic_v1`

### 4.2 Correctness and sanity artifacts

- `business_workflow_e2e_smoke_v1`
- `business_workflow_e2e_perf_sanity_v1`
- `recovery_protocol_edge_cases_v1`

### 4.3 Boundary and diagnostic artifacts

- `cloud_backend_smoke_v1`
- `cloudlab_random_redis_memory_v1`
- `mcpbench_smoke_v3`
- `burstgpt_trace_replay_v3`
- `adaptive_react_recovery_smoke_v1`

### 4.4 Support and interpretation artifacts

- `operator_overhead_v1`
- `config_sensitivity_v1`
- `architecture_component_ablation_v1`
- `cloudlab_business_workflow_distributed_v1`

## 5. Baseline Definitions

### 5.1 Request-level baseline

Artifact:

- `request_level_baseline_v1`

Definition:

- controls individual requests
- does not use session progress
- does not use continuation value
- does not use recovery

Purpose:

- answer whether ordinary per-request admission is sufficient for multi-step workflows

Interpretation:

- request-level governance can throttle calls, but it cannot reason about completed session prefixes

### 5.2 Idempotency-only baseline

Artifact:

- `idempotency_only_retry_baseline_v1`

Definition:

- keeps idempotency keys
- disables checkpoint resume
- allows retry/restart instead of resume

Purpose:

- separate durable-write safety from workflow-progress recovery

Interpretation:

- idempotency prevents duplicate durable writes, but it does not preserve workflow progress

## 6. Reproduction Levels

### 6.1 Lightweight replay

Run:

```powershell
python scripts/validate_jss_artifacts.py
```

Checks:

- required files exist
- source validations report `errors=[]`
- claim package is internally consistent
- transient runtime files are absent

### 6.2 Summary rebuild

Run:

```powershell
python scripts/build_jss_statistical_summary.py --out-dir-name statistical_summary_v4 --include-request-baseline --include-idempotency-baseline
```

Checks:

- rebuilds the canonical JSS package
- rebuild preserves `JSS-C17` and `JSS-C18`

### 6.3 Infrastructure-dependent reruns

Some studies require additional setup:

- local vLLM endpoint and model weights
- cloud-provider credentials
- CloudLab multi-node allocation

These studies are reproducible, but not lightweight.

## 7. Boundary Notes

### 7.1 Controlled versus boundary evidence

Controlled workflow experiments carry the causal performance claims. Real-backend, provider, CloudLab, and smoke studies validate narrower feasibility, correctness, and boundary properties.

### 7.2 CloudLab scope

The headline distributed result is `cloudlab_business_workflow_distributed_deterministic_v1`. It checks cross-gateway checkpoint lookup and recovery correctness. It does not claim production Redis HA, replicated control-plane fault tolerance, or a throughput benchmark.

Sticky routing can preserve locality during ordinary traffic, but forced cross-gateway recovery still requires shared checkpoint lookup.

### 7.3 Trust boundary

PlanGate assumes cooperative clients that provide stable workflow metadata. The prototype includes DAG validation, bounded amendment, session-cap limits, commitment/recovery token checks, and idempotency keys, but it does not provide Byzantine security against malicious clients.
