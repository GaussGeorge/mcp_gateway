# JSS Figure/Table/Algorithm Alignment

This file maps every figure, table, and algorithm in the JSS manuscript to its source artifact, claim ID, evidence role, and interpretation boundary.

| Object | Source artifact / source | Claim ID | Evidence role | Boundary |
|---|---|---|---|---|
| Table `tab:core_metrics` | Manuscript metric definitions | -- | definition | Not empirical evidence; defines reported metrics. |
| Figure `fig:plangate_architecture_lifecycle` | Architecture synthesis from gateway implementation | -- | design overview | Not a measured result. |
| Table `tab:overview_components` | Architecture synthesis from gateway implementation | JSS-C6 | architecture mapping | Design boundary table, not a performance result. |
| Table `tab:adaptive_policy_defaults` | `plangate/adaptive_admission.go` and CLI defaults | -- | policy specification | Documents tested thresholds; not an optimization proof. |
| Algorithm `alg:session_admission` | Gateway design and implementation path | -- | mechanism description | Abstracts control logic; not a benchmark result. |
| Algorithm `alg:react_recovery` | Gateway recovery protocol implementation | JSS-C2, JSS-C5 | mechanism description | Client-cooperative resume only; no server-side future-tool execution. |
| Figure `fig:react_recovery_protocol` | Recovery protocol implementation and deterministic probes | JSS-C2, JSS-C5 | protocol explanation | Illustrative protocol figure, not a throughput plot. |
| Table `tab:experimental_setup_summary` | `statistical_summary_v4` evidence hierarchy + study inventory | JSS-C1..JSS-C18 | evaluation setup | Setup summary only; not a result table. |
| Table `tab:evaluation_map` | `statistical_summary_v4/claim_summary.csv` | JSS-C1..JSS-C18 | evidence map | Maps research questions to evidence; not a new experiment. |
| Figure `fig:jss_core_tradeoff` | `business_workflow_service_v3`, `request_level_baseline_v1` | JSS-C1, JSS-C17 | performance + baseline | Admission trade-off plot; not a raw-success dominance claim. |
| Figure `fig:jss_backend_boundary` | `cloud_backend_smoke_v1`, `adaptive_step0_react_recovery_full_v1` | JSS-C8 | provider/backend boundary | Panels are not a direct performance comparison. |
| Figure `fig:jss_recovery_effectiveness` | `business_workflow_service_v3`, `vllm_react_recovery_arm_v2`, `idempotency_only_retry_baseline_v1` | JSS-C2, JSS-C7, JSS-C18 | recovery effectiveness | Shows recovered progress and avoided replay; not transparent server-side replay. |
| Table `tab:operator_overhead_c100` | `operator_overhead_v1` | JSS-C3 | deployability overhead | Same gateway/proxy path baseline; not direct-to-backend cost. |
| Figure `fig:jss_overhead_sensitivity` | `operator_overhead_v1`, `config_sensitivity_v1` | JSS-C3, JSS-C4 | deployability + robustness | Not a recovery-effectiveness result. |
| Figure `fig:cloudlab_deployment_topology` | `cloudlab_business_workflow_distributed_deterministic_v1` deployment description | JSS-C16 | deployment explanation | Topology only; not a measured result by itself. |
| Figure `fig:jss_distributed_correctness` | `cloudlab_business_workflow_distributed_deterministic_v1` | JSS-C16 | distributed correctness | Checks shared-state necessity; not Redis HA or CloudLab throughput. |
| Table `tab:main_results_summary` | `statistical_summary_v4/claim_summary.csv` | JSS-C1..JSS-C18 | results ledger | Summarizes supported results and scope; not a new artifact. |
| Table `tab:related_positioning` | Literature synthesis | -- | positioning summary | Organizes adjacent work; not empirical evidence. |

## Notes

1. `statistical_summary_v4` is the canonical JSS evidence package.
2. `JSS-C16` is the headline CloudLab correctness result.
3. `JSS-C17` and `JSS-C18` are baseline claims included in the canonical submission package.
4. Earlier artifacts such as `cloudlab_business_workflow_distributed_v1` are retained only as supporting interpretation, not as the canonical numerical package for the paper.
