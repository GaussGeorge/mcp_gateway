# CloudLab Distributed Harness

This directory contains a stdlib-only CloudLab harness for distributed
PlanGate experiments across:

- loader node(s)
- one Redis node
- multiple gateway nodes
- multiple backend nodes

The current harness covers P0-P2:

- inventory-driven SSH orchestration
- setup/build/start/stop/collect/validate
- shared Redis multi-gateway smoke runs
- per-run summary and aggregate CSV generation

It intentionally does not implement failure/amendment workload injection yet.
`--failure-rate` and `--amendment-rate` are accepted for API stability, but any
non-zero value fails fast until the recovery/amendment workload runner lands.

## Files

- `inventory.example.json`: example node inventory
- `setup_node.sh`: install runtime dependencies and Python venv; optional Redis setup
- `build_gateway.sh`: run targeted Go tests and build `gateway_linux`
- `start_backend.sh`: start the Python backend on a backend node
- `start_gateway.sh`: start a Redis-backed gateway with a shared commitment secret
- `stop_all.sh`: stop prior processes and clear `pg:*` Redis keys
- `run_cloudlab_experiment.py`: main orchestrator
- `collect_results.py`: fetch loader outputs, logs, and Redis info
- `validate_results.py`: validate merged `steps.csv` and gateway logs

## Inventory

Create `scripts/cloudlab/inventory.json` from `inventory.example.json` and fill
in the actual CloudLab hostnames plus the repo location on each node.

## Commands

1. Environment and command-plan check:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --dry-run
```

2. Small shared-random smoke:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --sessions 200 \
  --concurrency 40 \
  --repeats 1 \
  --results-dir results/cloudlab_smoke \
  --commitment-secret cloudlab-shared-secret
```

3. Medium-scale baseline matrix:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile medium \
  --sessions 2000 \
  --concurrency 100 200 \
  --repeats 3 \
  --failure-rate 0 \
  --amendment-rate 0 \
  --results-dir results/cloudlab_medium_ar \
  --commitment-secret cloudlab-shared-secret
```

If you pass any non-zero `--failure-rate` or `--amendment-rate`, the harness
fails fast with a clear message. That is deliberate for now: P3 workload
injection is a follow-up step, while this cut is meant to stabilize the
distributed Redis/shared-secret path first.

## What gets validated

For each collected run, `validate_results.py` checks:

- `total_sessions == expected sessions`
- `state_miss == 0`
- `duplicate_admission == 0`
- `cross_node_sessions > 0` for multi-gateway random routing
- `cascade_failed == 0` for no-failure smoke
- commitment invalid/mismatch/expired counts are zero
- all gateway logs advertise the same commitment-token mode and Redis address

The local results root also gets:

- `summary.csv`: one row per run
- `aggregate.csv`: grouped means per concurrency/failure/amendment setting
- raw merged `steps.csv`
- loader logs
- gateway logs
- backend logs
- `redis_info.txt`

## Notes

- Every gateway must share the same `--commitment-secret`.
- The harness drives the existing token-aware `dag_load_generator.py`; it sends
  both `--target` and `--targets` for backward compatibility with the current
  loader CLI.
- Recovery is enabled on the gateways, but distributed recovery/amendment
  workload injection is not part of this P0-P2 harness cut yet.
