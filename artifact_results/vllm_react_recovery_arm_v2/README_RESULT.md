# vllm_react_recovery_arm_v2

Expanded vLLM ReAct recovery arm.

## Configuration

- Agent brain: deepseek-v4-flash @ https://api.deepseek.com
- Backend tool LLM: qwen @ http://127.0.0.1:9999/v1 (local vLLM)
- Architecture: hybrid (cloud agent brain + local vLLM backend)
- Backend workers: 2
- Queue timeout: 1.0 s
- Resume backoff: 5 s
- Continuation concurrency: 1

## Protocol

1. All agents execute steps 0 and 1 normally.
2. Backend workers are saturated with blockers; all agents send step 2 and hit a recoverable queue timeout.
3. Recovery variant sends `X-Recovery-Mode: resume`, verifies the payload, waits for bounded backoff, and continues.
4. Adaptive variant treats the step-2 failure as a cascade because no recovery is enabled.

## Frozen Result Interpretation

This artifact validates ReAct recovery under local-vLLM backend load with
bounded continuation. Under lighter recovery load (C=2), the recovery arm
restores checkpointed ReAct sessions, avoids replaying two completed steps,
continues execution, and completes most sessions with zero duplicate side
effects. Under higher recovery load (C=4), recovery still triggers but
completion remains bounded by post-recovery backend headroom.

## Boundary

This is a targeted vLLM recovery arm, not the main vLLM full matrix.
It validates ReAct client-cooperative recovery under local-vLLM backend load.
It does not claim natural recovery triggering in the main vLLM full experiment.
