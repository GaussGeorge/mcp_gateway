# Business Workflow E2E Performance Sanity

This artifact provides E2E performance sanity/stability evidence for PlanGate
using real HTTP business services backed by SQLite.

## Role

- This is an **E2E performance sanity/stability check**, not a primary performance experiment.
- The primary experiment remains `business_workflow_service_v3`.
- E2E correctness smoke is `business_workflow_e2e_smoke_v1`.
- Recovery correctness is covered by `business_workflow_e2e_smoke_v1`.
- This artifact only verifies that the system is stable under HTTP/SQLite load,
  side-effect safety holds, and DB consistency doesn't break.
- Does **NOT** claim PlanGate raw success dominance.
- Does **NOT** set performance win/loss thresholds (e.g., "p95 < ng").

## Architecture

```
PlanGate Gateway -> MCP tools (business_e2e_mcp_backend.py)
    -> HTTP services (business_http_services.py) -> SQLite
```

## SQLite Configuration

- journal_mode: WAL
- busy_timeout: 5000ms
- synchronous: NORMAL
- foreign_keys: ON
- All writes use short transactions.
- SQLite DB is NOT retained in artifact (only summary + integrity checks).

## Recovery

This artifact is **not the recovery correctness artifact**.
Recovery correctness is covered by `business_workflow_e2e_smoke_v1`.

Here the deterministic recovery probe is a **path-availability check**:
- Validation requires at least one deterministic probe to pass, not all probes.
- Failed probes, if present, are reported as warnings and do not invalidate
  the performance sanity artifact.
- Probe: side-effect step succeeds, next step fails (recoverable),
  X-Recovery-Mode: resume, continuation succeeds, final state consistent.
- `natural_recovery_observed` is recorded but **not required**.

## DB Consistency Rules

- Success workflows must have all required final rows (confirm step exists).
- Failed workflows must not have invalid final confirm rows.
- All workflows: no duplicate idempotency keys, no duplicate side-effect rows.

## Claim Boundary

Allowed:
- HTTP/SQLite E2E harness remains stable under small load.
- DB consistency and side-effect safety hold.
- At least one deterministic recovery path remains executable.
- Idempotency and DB consistency hold under load.

Not allowed:
- Recovery robustness under all loads.
- Recovery performance superiority.
- PlanGate raw success dominance.
- PlanGate performance superiority claims.
- This replaces any primary experiment.
