# Artifact Index

Last updated: 2026-06-04

This file is the top-level artifact index for the PlanGate repository. For the
JSS-oriented revision, the authoritative frozen artifact set is recorded in:

```text
artifact_results/JSS_ARTIFACT_MANIFEST.md
artifact_results/statistical_summary_v2/claim_summary.csv
artifact_results/statistical_summary_v2/validation.json
scripts/validate_jss_artifacts.py
```

Use `JSS_ARTIFACT_MANIFEST.md` to decide which artifact directories belong in
the JSS submission bundle. Do not infer JSS claims from older directories that
are not listed in the manifest.

## JSS Frozen Evidence Stack

The JSS evidence stack is organized by claim IDs in
`artifact_results/statistical_summary_v2/claim_summary.csv`.

| Layer | Primary artifacts | Purpose |
|---|---|---|
| Core service workflow | `business_workflow_service_v3` | Main adaptive admission and ReAct recovery evidence. |
| Deployability | `operator_overhead_v1` | Failure-free gateway overhead over same-proxy baseline. |
| Architecture and configuration | `architecture_component_ablation_v1`, `config_sensitivity_v1` | Component rationale and moderate configuration robustness. |
| Recovery protocol | `recovery_protocol_edge_cases_v1` | Recovery API and safety-boundary checks. |
| E2E service workflow | `business_workflow_e2e_smoke_v1`, `business_workflow_e2e_perf_sanity_v1` | Real HTTP+SQLite correctness and small-load stability/sanity. |
| Distributed correctness | `cloudlab_business_workflow_distributed_deterministic_v1` | Clean CloudLab cross-gateway ReAct recovery with Redis vs memory. |
| Real backend and boundary | `vllm_react_recovery_arm_v2`, `cloud_backend_smoke_v1`, `adaptive_step0_react_recovery_full_v1` | vLLM recovery feasibility, cloud-provider boundary behavior, and mechanism support. |
| Workload realism diagnostics | `mcpbench_smoke_v3`, `burstgpt_trace_replay_v3` | Workflow-shape and arrival-pattern diagnostics. |
| Statistical summary | `statistical_summary_v2` | Bootstrap/descriptive summaries, effect sizes, and claim table. |

## Preferred CloudLab Evidence

For JSS, prefer:

```text
cloudlab_business_workflow_distributed_deterministic_v1
```

This artifact provides deterministic, clean distributed correctness evidence:

- Redis recovers 15/15 forced cross-gateway ReAct checkpoints.
- Redis state misses: 0/15.
- Memory-local diagnostic control state misses: 15/15.
- Replayed completed side effects: 0.
- Validation errors: `[]`.

The older artifact:

```text
cloudlab_business_workflow_distributed_v1
```

is retained as supporting history only. It includes random-workload rows with
expected `ACTIVE_CHECKPOINT` conflicts and should not be used as the clean
CloudLab headline result.

## Claim Boundaries

Do not claim:

- PlanGate wins every raw-success setting.
- Every component improves raw success.
- E2E HTTP+SQLite sanity proves real-service performance dominance.
- CloudLab Redis evidence proves production Redis high availability.
- Cloud-provider smoke establishes a global provider/policy ranking.
- BurstGPT-shaped replay is a real agent-workflow trace.
- MCPBench metadata smoke is a full MCP-Bench deployment.

## Validation

Run:

```powershell
python scripts/build_jss_statistical_summary.py
python scripts/validate_jss_artifacts.py
```

Expected:

```json
{
  "errors": [],
  "warnings": [
    "business_workflow_e2e_perf_sanity_v1 has recovery-probe warning; allowed only as path-availability sanity"
  ]
}
```

The warning is expected and documents the evidence boundary for the E2E
performance sanity artifact.
