# adaptive_step0_react_recovery_full_v1

Full Experiment v1 for Adaptive Step-0 + ReAct client-cooperative recovery.

## What This Evaluates

This artifact evaluates adaptive capacity Step-0 admission and ReAct client-cooperative recovery.

Hard Step-0 rejects remain always enabled for:
- budget
- DAG validity
- security/reputation
- duplicate admission

Only capacity/overload Step-0 rejection is adaptive.

ReAct recovery is client-cooperative and does not replay unknown future tools.
It is weaker than P&S suffix recovery.

## Evidence Layers

| Layer | Type | Description |
|-------|------|-------------|
| mock_full | causal controlled | sterile backend, synthetic failures, direct HTTP clients |
| vllm_full | backend realism | hybrid DeepSeek V4-flash agent brain with local Qwen3.5-4B vLLM backend |

The vLLM layer is hybrid, not all-local: DeepSeek V4-flash is used as the
ReAct agent brain for reliable tool calling, while the governed backend tool
LLM is served by local vLLM at `http://127.0.0.1:9999/v1`.

## Variants

- plangate_strict: adaptive off, recovery off, capacity Step-0 = strict
- plangate_adaptive: adaptive on, recovery off, capacity Step-0 = adaptive (green/yellow/red = 200/50/0ms)
- plangate_no_capacity_step0: adaptive off, capacity Step-0 disabled (budget/DAG/security still enforced)
- plangate_adaptive_react_recovery: adaptive on, recovery on, ReAct client-cooperative recovery

## Boundary

This artifact is frozen as mechanism evidence for Adaptive Step-0 and ReAct
client-cooperative recovery. The `mock_full` layer is the recovery mechanism
evidence. The `vllm_full` layer is backend-realism evidence for adaptive
capacity admission under local-vLLM contention; it should not be interpreted
as natural vLLM recovery evidence because recovery is not naturally triggered
in that matrix. No dominance claim or statistical-significance claim is made.
