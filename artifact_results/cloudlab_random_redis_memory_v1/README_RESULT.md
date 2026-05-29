# CloudLab Random Routing Redis vs Memory (Lightweight Artifact)

This is a CloudLab 6-node m510 random-routing Redis vs memory shared-state diagnostic artifact.

- Redis arm is the correctness arm.
- Memory arm is the diagnostic control.

What this artifact supports:

- Under random multi-gateway routing, Redis-backed shared state keeps state_miss=0.
- Under the same setting, memory-local state shows substantial state_miss.
- Redis adversarial rejection checks pass for all adversarial rows in this run set.

Scope boundaries:

- This artifact is for result consolidation and correctness/diagnostic interpretation only.
- It does not claim PlanGate-AR universal performance superiority.
- It does not claim v2 commitment/amendment performance wins.
- It does not claim production Redis HA/fault-tolerance guarantees.

Contents are intentionally lightweight:

- README_RESULT.md
- cloudlab_random_redis_memory_summary.csv
- cloudlab_random_redis_memory_agg.csv
- validation.json

No raw logs, steps.csv, sessions.csv, loaders, backend/gateway logs, or redis_info files are included.
