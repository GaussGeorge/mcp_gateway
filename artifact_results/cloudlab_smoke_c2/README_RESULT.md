## CloudLab P0-P2 Correctness Smoke

- Date: `2026-05-23`
- Profile: `small`
- Topology: `1 loader, 1 Redis, 2 gateways, 2 backends`
- Routing: `random`
- Sessions: `20`
- Concurrency: `2`
- Failure rate: `0.0`
- Amendment rate: `0.0`
- Validation mode: `correctness`

Validated files in this directory:

- `summary.csv`
- `aggregate.csv`
- `validation.json`

Key results:

- `success_sessions = 20 / 20`
- `success_rate = 1.0`
- `cross_node_sessions = 17`
- `state_miss = 0`
- `duplicate_admission = 0`
- `cascade_failed = 0`
- `commitment_invalid = 0`
- `commitment_mismatch = 0`
- `commitment_expired = 0`
- `validation.json.errors = []`
