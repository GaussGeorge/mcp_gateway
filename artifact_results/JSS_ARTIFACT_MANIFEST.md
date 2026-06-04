# JSS Artifact Manifest

Last updated: 2026-06-04

This manifest is the frozen artifact map for the JSS-oriented PlanGate
revision. It separates artifacts used for JSS claims from legacy or
diagnostic outputs that remain in the repository for traceability.

Use this manifest together with:

- `artifact_results/statistical_summary_v2/claim_summary.csv`
- `artifact_results/statistical_summary_v2/validation.json`
- `scripts/validate_jss_artifacts.py`

The paper should cite claim IDs from `claim_summary.csv` rather than
hand-picking numbers from older artifact directories.

## Primary JSS Claim Artifacts

| Claim IDs | Artifact | Use in paper | Boundary |
|---|---|---|---|
| `JSS-C1`, `JSS-C2` | `business_workflow_service_v3` | Main service-workflow governance and ReAct recovery evidence. | Do not claim raw-success dominance; report cascade/waste/recovery together. |
| `JSS-C3` | `operator_overhead_v1` | Failure-free deployability and overhead evidence. | Same-gateway baseline only; not recovery evidence. |
| `JSS-C4` | `config_sensitivity_v1` | Robustness under light/default/strict governance-intensity perturbations. | Not parameter selection or best-setting evidence. |
| `JSS-C5` | `recovery_protocol_edge_cases_v1` | Recovery API usability and safety-boundary evidence. | Protocol harness, not throughput evidence. |
| `JSS-C6` | `architecture_component_ablation_v1` | Architecture component-to-failure-mode mapping. | Evidence synthesis, not a new performance experiment. |
| `JSS-C7` | `vllm_react_recovery_arm_v2` | Targeted local-vLLM backend recovery feasibility. | Hybrid DeepSeek agent brain plus local Qwen backend; not full natural-recovery matrix. |
| `JSS-C12` | `business_workflow_e2e_smoke_v1` | Real HTTP+SQLite E2E correctness smoke. | Correctness/external-validity only; not performance improvement. |
| `JSS-C15` | `business_workflow_e2e_perf_sanity_v1` | Real HTTP+SQLite small performance/stability sanity and side-effect safety. | Sanity/stability evidence only; not PlanGate real-service performance dominance. |
| `JSS-C16` | `cloudlab_business_workflow_distributed_deterministic_v1` | Clean six-node CloudLab deterministic cross-gateway ReAct recovery correctness. | Deterministic correctness only; not CloudLab performance dominance or production Redis HA. |

## Supporting And Boundary Artifacts

| Claim IDs | Artifact | Use in paper | Boundary |
|---|---|---|---|
| `JSS-C8` | `cloud_backend_smoke_v1` | Cloud-provider boundary interpretation. | Single-repeat smoke; provider elasticity can invert admission trade-offs. |
| `JSS-C9` | `cloudlab_random_redis_memory_v1` | Shared-state diagnostic: Redis vs memory under random routing. | State-consistency diagnostic only; not production Redis HA. |
| `JSS-C10` | `mcpbench_smoke_v3` | Workflow-shape/compatibility smoke. | Metadata-derived smoke; not full MCP-Bench deployment or model accuracy. |
| `JSS-C11` | `burstgpt_trace_replay_v3` | Arrival-pattern realism diagnostic. | Uses real LLM-serving arrivals only; not a real agent trace. |
| `JSS-C13` | `cloudlab_business_workflow_distributed_v1` | Earlier distributed E2E smoke with deterministic probes and random-workload diagnostics. | Supporting only; the clean CloudLab correctness claim should use `JSS-C16`. |
| `JSS-C14` | `adaptive_react_recovery_smoke_v1` | Cross-layer execution-stability smoke index. | Smoke only; not headline performance ranking. |
| Supporting | `adaptive_step0_react_recovery_full_v1` | Mock-full and vLLM-full mechanism/boundary support. | vLLM layer is hybrid; no natural vLLM recovery claim. |
| Supporting | `adaptive_react_recovery_mock_smoke_v1` | Mock smoke sanity. | Not headline effect evidence. |
| Supporting | `adaptive_react_recovery_vllm_smoke_v1` | vLLM smoke sanity. | Not headline ranking. |
| Supporting | `adaptive_react_recovery_cloudllm_smoke_v1` | Cloud LLM smoke sanity. | Not global provider ranking. |

## Frozen Bundle Whitelist

For a clean JSS artifact bundle, include only the summary/aggregate/effect/probe
CSV files, `README_RESULT.md`, and `validation.json` files under these paths.
Do not include raw per-step traces unless a reviewer explicitly requests them.

```text
artifact_results/JSS_ARTIFACT_MANIFEST.md
artifact_results/ARTIFACT_INDEX.md
artifact_results/statistical_summary_v2/
artifact_results/business_workflow_service_v3/
artifact_results/operator_overhead_v1/
artifact_results/architecture_component_ablation_v1/
artifact_results/config_sensitivity_v1/
artifact_results/recovery_protocol_edge_cases_v1/
artifact_results/business_workflow_e2e_smoke_v1/
artifact_results/business_workflow_e2e_perf_sanity_v1/
artifact_results/cloudlab_business_workflow_distributed_deterministic_v1/
artifact_results/adaptive_step0_react_recovery_full_v1/
artifact_results/vllm_react_recovery_arm_v2/
artifact_results/cloud_backend_smoke_v1/
artifact_results/cloudlab_random_redis_memory_v1/
artifact_results/mcpbench_smoke_v3/
artifact_results/burstgpt_trace_replay_v3/
artifact_results/cloudlab_business_workflow_distributed_v1/
artifact_results/adaptive_react_recovery_mock_smoke_v1/
artifact_results/adaptive_react_recovery_vllm_smoke_v1/
artifact_results/adaptive_react_recovery_cloudllm_smoke_v1/
artifact_results/adaptive_react_recovery_smoke_v1/
scripts/build_jss_statistical_summary.py
scripts/validate_jss_artifacts.py
```

## Legacy Or Superseded Artifacts

These artifacts may remain in the development repository, but the JSS paper
should not cite them as primary evidence:

```text
business_workflow_service_v1/
business_workflow_service_v2/
burstgpt_trace_replay_v1/
burstgpt_trace_replay_v2/
mcpbench_smoke_v1/
mcpbench_smoke_v2/
vllm_react_recovery_arm_v1/
cloudlab_business_workflow_distributed_deterministic_local_smoke_v1/
cloudlab_business_workflow_distributed_local_smoke_v1/
cloudlab_p3_small_random_redis_cp_v2/
cloudlab_p3_small_sticky_v2/
cloudlab_smoke_c2/
deepseek_v4_flash_smoke_v1/
mock_regression_p4_refresh_v1/
statistical_summary_v1/
selfhosted_vllm_stress_c16w8_relaxed_6gw_v1/
```

Do not infer JSS claims from these directories unless they are explicitly
reintroduced into `statistical_summary_v2` and `claim_summary.csv`.

## Files Excluded From Artifact Bundles

Do not include:

- raw logs (`*.log`)
- temporary files (`*.tmp`, `*_tmp/`)
- runtime databases (`*.db`, `*.sqlite`)
- SQLite sidecars (`*.sqlite-shm`, `*.sqlite-wal`, `*.sqlite-journal`)
- local gateway/backend binaries or process output
- raw per-step traces (`steps.csv`, `steps_agents.csv`, `steps_summary.csv`)
- generated paper PDFs and LaTeX intermediate files
- old figure directories not referenced by the JSS paper

## Validation Commands

```powershell
python scripts/build_jss_statistical_summary.py
python scripts/validate_jss_artifacts.py
```

Expected validator result:

```json
{
  "errors": [],
  "warnings": [
    "business_workflow_e2e_perf_sanity_v1 has recovery-probe warning; allowed only as path-availability sanity"
  ]
}
```

The warning is intentional: `business_workflow_e2e_perf_sanity_v1` is a
small performance/stability sanity artifact, and its recovery probe is only
used to verify path availability.
