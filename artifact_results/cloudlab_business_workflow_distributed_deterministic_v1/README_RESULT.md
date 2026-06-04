# CloudLab Distributed Business Workflow — Deterministic Correctness Smoke

This artifact is a deterministic distributed correctness smoke, NOT a throughput
or latency benchmark.

## Mode

CloudLab 6-node distributed — deterministic-only mode.
Intentionally excludes random workload sessions to isolate the cross-gateway
recovery mechanism.

## Architecture

```
3 × PlanGate Gateways
  → MCP Backend
  → HTTP Business Services
  → SQLite side effects
  → Redis shared state (redis arm only)
```

## Experiment Design

- Redis arm is the **correctness arm** — forced cross-gateway ReAct recovery.
- Memory arm is a **diagnostic control** — cross-gateway state miss is expected.
- Memory state misses are expected and are NOT validation failures.
- No completed side-effect step is replayed.
- SQLite persistent side effects remain idempotent and consistent.

## Claim Boundary

Allowed:
- Redis-backed shared state enables forced cross-gateway ReAct recovery.
- Memory-local state cannot recover across gateways, but remains failure-safe.
- Deterministic probes validate idempotency and recovery correctness.

Not allowed:
- Redis improves performance.
- PlanGate wins distributed workload.
- Production Redis HA guarantee.

## Configuration

- stores: ['redis', 'memory']
- repeats: 5
- deterministic_recovery_probes_per_run: 3
- total probes: 30
