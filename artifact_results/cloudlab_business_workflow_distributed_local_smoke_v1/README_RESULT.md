# CloudLab Distributed Business Workflow — Local Smoke

This artifact is a local single-machine smoke test of the CloudLab distributed
business workflow experiment harness.

## Architecture

```
3 × PlanGate Gateways (127.0.0.1:19001-19003)
  → MCP Backend (127.0.0.1:18080)
  → HTTP Business Services (127.0.0.1:18081)
  → SQLite
```

## Key Properties

- Local smoke test only — NOT the full CloudLab experiment.
- Validates multi-gateway process management and random routing.
- Validates deterministic cross-gateway recovery probes.
- Does NOT claim CloudLab results.

## Configuration

- sessions: 20
- concurrency: 5
- failure_rate: 0.1
- repeats: 1
- stores: ['memory']
