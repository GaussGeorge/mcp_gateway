# adaptive_react_recovery_smoke_v1

Aggregate smoke artifact for Adaptive Step-0 + ReAct recovery.

## Layers

| Layer | Directory | Status |
|-------|-----------|--------|
| mock | adaptive_react_recovery_mock_smoke_v1 | passed |
| cloudllm | adaptive_react_recovery_cloudllm_smoke_v1 | passed |
| vllm | adaptive_react_recovery_vllm_smoke_v1 | passed |

## Smoke Disclaimer

This is smoke evidence only.
It checks execution stability and directional behavior.
It is not a statistically powered comparison.
It is not used to replace the current paper headline results.

The smoke run validates that the adaptive admission and ReAct recovery paths execute
cleanly across controlled mock, provider-backed LLM (DeepSeek), and self-hosted vLLM (Qwen)
layers under a shared parameter schema. These runs are not statistically powered and are
used only to decide whether a full experiment is warranted.

## Result

All three layers passed all validation checks:
- No client return-code errors
- No client timeouts
- No runtime errors
- No duplicate side effects
- Directional trends consistent (adaptive ABD <= strict ABD; no_capacity_step0 shows expected behavior)

Smoke passed. Execution stable. Directional trends observed.
A full experiment is warranted.
