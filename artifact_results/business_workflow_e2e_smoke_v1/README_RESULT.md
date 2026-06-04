# Business Workflow E2E Smoke Evidence

This artifact provides end-to-end correctness smoke evidence for PlanGate using
real HTTP business services backed by SQLite.

## Architecture

```
PlanGate Gateway -> MCP tools (business_e2e_mcp_backend.py) -> HTTP services (business_http_services.py) -> SQLite
```

## Key Properties

- This is an E2E **correctness smoke**, not a primary performance experiment.
- Real HTTP services with SQLite side-effect persistence (WAL mode, busy_timeout=5000ms).
- MCP tools call HTTP services — tools do NOT write to SQLite directly.
- Recovery uses real `X-Recovery-Mode: resume` header.
- Deterministic recovery probe forces a side-effect step to succeed, then injects a
  recoverable failure on the next step, then resumes and validates continuation.
- Idempotency replay probe proves that replaying the same idempotency_key does not
  produce duplicate side effects.
- Does NOT claim PlanGate raw success dominance.
- The primary experiment remains `business_workflow_service_v3`.

## Configuration

- sessions: 50
- concurrency: 10
- failure_rate: 0.1
- repeats: 3
- variants: ng / plangate_adaptive / plangate_adaptive_react_recovery

## SQLite Configuration

- journal_mode: WAL
- busy_timeout: 5000ms
- synchronous: NORMAL
- foreign_keys: ON
- All writes use short transactions.

## Claim Boundary

Allowed claims:
- MCP tools invoke real HTTP services with SQLite side effects.
- Idempotency prevents duplicate writes under replay.
- ReAct recovery resumes without replaying completed side-effecting steps.
- The E2E harness is functional for business workflows.

Not allowed:
- PlanGate improves performance on real services.
- PlanGate wins raw success.
- This replaces `business_workflow_service_v3` as the primary experiment.
