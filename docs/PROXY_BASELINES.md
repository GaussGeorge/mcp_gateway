# Proxy Approximation Baselines (Envoy/Kong)

## Scope

This codebase adds two request-level proxy-style governance approximations:
- envoy-approx
- kong-approx

These are approximation baselines, not product benchmarks.

## Non-goals and Prohibited Behaviors

The proxy approximations MUST NOT:
- Read the full DAG and make future-step reservation decisions.
- Lock session prices or implement session commitment.
- Perform session-level admission guarantees.

They only use request-local, currently visible fields (headers, session id, route/tool, tokens, remote address).

## Official Primitive Mapping

| Baseline | Implemented request-level primitives | Notes |
|---|---|---|
| envoy-approx | Global token bucket, per-route token bucket, global in-flight cap, per-route in-flight cap | Approximation of local/global rate limit and circuit-breaker style controls |
| kong-approx | Global quota token bucket, per-consumer/session token bucket, TTL cleanup for key state | Consumer key priority: X-Session-ID > _meta.name > remote addr |

## Not Covered vs Real Products

Not covered in this approximation:
- Envoy/Kong full control plane behavior.
- Lua/WASM/plugin runtime semantics.
- Product-specific HCM/plugin internals.
- Distributed shared-state backends (first version is memory store only).

## Result Interpretation

Use these baselines as request-level approximations only. They are useful for comparing admission and rate-control behavior, but they do not provide session commitment or full DAG awareness.

Current paper-facing reading:
- `envoy-approx` and `kong-approx` are reasonable request-local controls, but they still admit substantial cascade waste at higher concurrency.
- `plangate_full` should be described as a plan-aware admission policy, not as a universal throughput winner.
- Higher `success_sessions_per_s` is not the same thing as higher `effective_goodput_s`; keep both metrics separate in the paper and in captions.

Lightweight PlanGate sensitivity at `C=40` supports the tradeoff framing:
- `max_sessions=50` increases effective throughput relative to `max_sessions=30`, but it can also admit a small amount of cascade waste.
- `price_step=40` is materially more conservative than `price_step=20`, and that shows up as lower throughput and fewer accepted sessions.
- The effect is therefore a parameterized admission-vs-waste tradeoff, not an unconditional PlanGate win.

## Paper Placement

Recommended placement is appendix-only unless stronger evidence is added later.

- Main text: keep the core claim focused on session-aware governance and the controlled mock results.
- Appendix: include the proxy baseline comparison, the `C=40` sensitivity check, and the selfhosted-vLLM smoke note.
- Diagnostic note: the selfhosted-vLLM smoke run is diagnostic-only and should not be used as a paper claim.

## Paper Table Artifact

The compact paper-candidate table is now written to:

- `results/exp_proxy_baselines/mock/proxy_baseline_paper_table.csv`

It contains the rows needed for the appendix or a compact results table, without the full experiment matrix.
