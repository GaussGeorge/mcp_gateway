# CloudLab Distributed Business Workflow Smoke Evidence

This artifact provides CloudLab distributed service workflow correctness smoke
evidence. It is NOT a performance primary experiment.

## Topology

6-node CloudLab topology:
```
node0: controller / load generator / artifact collector
node1: PlanGate gateway A
node2: PlanGate gateway B
node3: PlanGate gateway C
node4: MCP backend + HTTP business services + SQLite DB
node5: Redis shared state
```

## Network

Services bind experiment-network IPs, not control-network/FQDN addresses.
The preflight file records service health/binding probes before runtime service
startup; those pre-start service checks are not the pass/fail criterion. The
runtime evidence is the observed experiment-network endpoint traffic in the
summary and recovery-probe CSVs.

## Experiment Design

- Redis arm is the **correctness arm**.
- Memory arm is a **diagnostic control**.
- Goal: cross-gateway ReAct recovery and shared-state necessity.
- No production Redis HA claim.
- No raw success dominance claim.
- Main business workflow experiment remains `business_workflow_service_v3`.
- Random workload rows include expected ACTIVE_CHECKPOINT resume conflicts and
  should not be interpreted as performance benchmark results.

## Configuration

- sessions: 20
- concurrency: 5
- failure_rate: 0.1
- repeats: 3
- stores: ['redis', 'memory']
- deterministic_recovery_probes_per_run: 3
