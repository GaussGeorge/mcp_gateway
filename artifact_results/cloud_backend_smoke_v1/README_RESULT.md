# cloud_backend_smoke_v1

Cloud-backend smoke for Adaptive Step-0 + ReAct recovery.

This artifact is frozen as provider-backend compatibility and boundary
evidence. It should not be used as headline causal evidence for Adaptive
Step-0 or ReAct recovery, and no cloud-backend full experiment is planned for
this artifact.

## Configuration

- Agent brain: deepseek-v4-flash @ https://api.deepseek.com
- Backend tool LLM: deepseek-v4-flash @ https://api.deepseek.com (cloud provider)
- Provider: deepseek
- Agents / concurrency / max steps: 20 / 8 / 5
- Repeats: 1

## Variants

- plangate_strict
- plangate_adaptive
- plangate_no_capacity_step0
- plangate_adaptive_react_recovery

## Frozen Result Summary

| Variant | Success | Cascade | ABD% | Tool calls | Backend tokens | Duplicate side effects |
|---|---:|---:|---:|---:|---:|---:|
| plangate_strict | 7 | 13 | 63.2 | 77 | 3,054 | 0 |
| plangate_adaptive | 10 | 10 | 47.4 | 76 | 5,232 | 0 |
| plangate_no_capacity_step0 | 17 | 3 | 15.0 | 84 | 4,362 | 0 |
| plangate_adaptive_react_recovery | 10 | 10 | 47.4 | 84 | 5,739 | 0 |

The smoke has `errors=[]`, positive tool calls, positive backend token usage,
zero fake successes, zero client/runtime errors, and zero duplicate side
effects.

## Interpretation

The result shows a provider-elasticity boundary. In this small DeepSeek-backed
cloud smoke, `plangate_no_capacity_step0` performs best because the provider
API absorbs the backend load and local capacity Step-0 admission becomes an
extra source of gateway-level friction. This is expected when the gateway does
not control the actual provider queue or capacity.

Follow-up diagnostics found zero provider-level failures across all variants:
no 429 rate-limit errors, no 401 authentication failures, and no 5xx provider
errors. The observed failures are tool/session-level outcomes rather than
provider outages.

| Variant | Total tool steps | Provider 2xx | Tool-step success | Gateway rejects | Tool/session errors |
|---|---:|---:|---:|---:|---:|
| plangate_strict | 77 | 68 | 58 | 8 | 11 |
| plangate_adaptive | 76 | 71 | 63 | 6 | 7 |
| plangate_no_capacity_step0 | 84 | 83 | 83 | 0 | 1 |
| plangate_adaptive_react_recovery | 84 | 76 | 71 | 4 | 9 |

Step-latency diagnostics also point to local admission friction rather than
provider congestion as the dominant effect in this smoke:

| Variant | Step P50 ms | Step P95 ms | Step mean ms | Reject P50 ms | Provider-tool P95 ms | Session P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| plangate_strict | 13,434 | 35,215 | 15,421 | 20,602 | 39,994 | 136,334 |
| plangate_adaptive | 14,202 | 27,109 | 14,980 | 13,432 | 29,596 | 127,189 |
| plangate_no_capacity_step0 | 13,255 | 24,605 | 12,081 | N/A | 29,282 | 113,612 |
| plangate_adaptive_react_recovery | 12,990 | 28,774 | 13,493 | 9,939 | 30,761 | 131,058 |

The practical reading is that gateway-level admission rejections can confuse
the ReAct agent brain in provider-backed deployments: after a rejected tool
call, the agent must re-plan, which adds provider LLM calls, elongates the
session, and can increase tool/argument errors. With an elastic cloud backend,
disabling capacity Step-0 avoids this local admission friction and lets the
provider-side scheduler absorb the workload.

The observed boundary does not contradict the mock and local-vLLM results.
Adaptive capacity admission is most useful when the gateway controls or
accurately observes a constrained backend, such as the controlled mock backend
or the local vLLM backend. Provider-backed deployments require provider-aware
admission signals such as 429 rate, provider latency, retry-after headers, and
backend timeout rate.

## Boundary

This is cloud-backend smoke evidence only.
It evaluates provider-backend compatibility and boundary behavior.
It is not self-hosted vLLM evidence.
It is not a dominance claim.
Provider hidden queues/rate limits may weaken governance signal.

Cloud-backend smoke validates compatibility and boundary behavior under
provider-hosted backend tools. It is not used as the main causal evidence
for Adaptive Step-0 or ReAct recovery.

No cloud-backend full run is planned from this artifact; the current smoke is
sufficient to document the provider-backed boundary. Running a larger
cloud-backend full experiment would mainly amplify provider-specific hidden
queueing and billing cost rather than strengthen the paper's causal governance
claim.
