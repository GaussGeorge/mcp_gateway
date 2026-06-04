# adaptive_react_recovery_vllm_smoke_v1

Self-hosted vLLM smoke for Adaptive Step-0 + ReAct client-cooperative recovery.

## Environment

- agent_brain: deepseek-v4-flash @ https://api.deepseek.com
- backend_tool_llm: qwen @ http://127.0.0.1:9999/v1
- architecture: hybrid, not all-local vLLM
- backend_workers: 8

## Smoke Parameters

- agents: 40
- concurrency: 8
- max_steps: 8
- burst_size: 12
- burst_gap_s: 5.0
- session_cap: 16
- react_step0_limit: 16
- adaptive waits: 200/50/0 ms

## Variants

- plangate_strict
- plangate_adaptive
- plangate_no_capacity_step0
- plangate_adaptive_react_recovery

## Smoke Disclaimer

This is smoke evidence only.
This run checks execution stability of Adaptive Step-0 and ReAct
client-cooperative recovery wiring with a DeepSeek V4-flash agent brain and a
local self-hosted vLLM backend.
It is not statistically powered.
It does not replace selfhosted_vllm_profile_sweep_v1 or selfhosted_vllm_stress_c16w8_tuned_5gw_v1.
No dominance claim is made.

Recovery path was enabled but natural recoverable failures may not occur in smoke.
No synthetic failures were injected.
The smoke validates that all variants produce real tool calls and backend
tokens; it does not validate recovery completion under injected failures.
