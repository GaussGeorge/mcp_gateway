# Idempotency-Only Retry Baseline v1

This artifact is a request-independent recovery baseline for the HTTP+SQLite E2E workflow harness.

It compares:
- `idempotency_only_retry`
- `plangate_adaptive_react_recovery`

## Intent

This artifact is a baseline for the question:

> Is idempotency-key safety plus retry/restart already sufficient, or is checkpoint recovery still necessary?

The idempotency-only baseline intentionally ignores checkpoint recovery and session-progress restoration. It keeps stable idempotency keys so that duplicate durable writes remain safe, but it does not send `X-Recovery-Mode: resume` and therefore cannot restore safe prefixes.

## Configuration

- sessions: 30
- concurrency: [10, 20]
- failure_rate: 0.1
- repeats: 3

## Allowed claims

- Idempotency-only retry can preserve duplicate-side-effect safety.
- Idempotency-only retry does not recover checkpoint progress.
- PlanGate recovery can avoid replayed completed steps under the same HTTP+SQLite harness.

## Not allowed

- Do not claim idempotency is unnecessary.
- Do not claim recovery universally improves raw workflow success.
- Do not treat this artifact as a main performance benchmark.
