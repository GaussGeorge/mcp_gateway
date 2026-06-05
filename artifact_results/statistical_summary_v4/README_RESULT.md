# JSS Statistical Summary (statistical_summary_v4)
This artifact consolidates JSS-oriented PlanGate evidence into one statistical/effect/claim bundle.
It reads existing artifact CSV files only; it does not run new experiments or modify mechanism code.
## Included JSS Core Artifacts
- business_workflow_service_v3
- operator_overhead_v1
- architecture_component_ablation_v1
- config_sensitivity_v1
- recovery_protocol_edge_cases_v1
- business_workflow_e2e_smoke_v1
- business_workflow_e2e_perf_sanity_v1
- cloudlab_business_workflow_distributed_v1
- cloudlab_business_workflow_distributed_deterministic_v1
- request_level_baseline_v1
- idempotency_only_retry_baseline_v1
## Included Supporting/Boundary Artifacts
- adaptive_step0_react_recovery_full_v1
- adaptive_react_recovery_mock_smoke_v1
- adaptive_react_recovery_vllm_smoke_v1
- adaptive_react_recovery_cloudllm_smoke_v1
- adaptive_react_recovery_smoke_v1
- vllm_react_recovery_arm_v2
- cloud_backend_smoke_v1
- cloudlab_random_redis_memory_v1
- mcpbench_smoke_v3
- burstgpt_trace_replay_v3
- request_level_baseline_v1 (request-level baseline)
- idempotency_only_retry_baseline_v1 (idempotent retry baseline)
## Claim Boundaries
- Default bootstrap iterations: 3000. Override with --bootstrap-iters when needed.
- Do not claim raw-success dominance.
- Do not treat MCPBench metadata smoke as full MCP-Bench deployment.
- Do not treat BurstGPT-shaped replay as a real agent-workflow trace.
- Do not use CloudLab Redis evidence as production Redis HA proof.
- Treat business_workflow_e2e_perf_sanity_v1 as sanity/stability evidence, not as real-service performance dominance.
- Prefer cloudlab_business_workflow_distributed_deterministic_v1 for the clean deterministic CloudLab correctness claim.
- Use architecture_component_ablation_v1 as an evidence map, not as a new performance run.
- Treat request_level_baseline_v1 as a request-level governance baseline that ignores session progress and recovery.
- Treat idempotency_only_retry_baseline_v1 as a side-effect-safety baseline that preserves idempotency but does not restore checkpoint progress.
## Outputs
- statistical_summary.csv
- effect_size_summary.csv
- claim_summary.csv
- validation.json
- README_RESULT.md
