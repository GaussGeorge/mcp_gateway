# CloudLab Distributed Harness

This directory contains a stdlib-only CloudLab harness for distributed
PlanGate experiments across:

- loader node(s)
- one Redis node
- multiple gateway nodes
- multiple backend nodes

The current harness covers P0-P3:

- inventory-driven SSH orchestration
- setup/build/start/stop/collect/validate
- shared Redis multi-gateway smoke runs
- per-run summary and aggregate CSV generation
- distributed P3 recovery/amendment workload runs through
  `scripts/p3_recovery_amendment_runner.py`

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
- `../p3_recovery_amendment_runner.py`: policy-aware P3 workload runner used by
  `--workload p3`
- `../compute_p3_recovery_amendment_stats.py`: writes `p3_summary.csv` and
  `p3_adversarial_summary.csv`

All CloudLab entrypoints also support:

```bash
--ssh-key ~/.ssh/cloudlab_ed25519
```

Resolution order is:

1. explicit `--ssh-key`
2. `CLOUDLAB_SSH_KEY`
3. `~/.ssh/cloudlab_ed25519` if present
4. otherwise fall back to the local SSH default behavior

Every `ssh` and `scp` invocation uses:

- `-i <key>` when a key path resolves
- `-o IdentitiesOnly=yes`
- `-o BatchMode=yes`
- `-o StrictHostKeyChecking=no`

## Inventory

Create `scripts/cloudlab/inventory.json` from `inventory.example.json` and fill
in the actual CloudLab hostnames plus the repo location on each node.

For the current 6-node `m510` small deployment, you can also use
`scripts/cloudlab/inventory.m510_6.json`, which fixes:

- `node-0`: Redis
- `node-1`: Loader
- `node-2`, `node-3`: Gateways
- `node-4`, `node-5`: Backends

## Commands

1. Environment and command-plan check:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --dry-run
```

2. Correctness smoke:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --sessions 20 \
  --concurrency 2 \
  --repeats 1 \
  --arrival-rate 2 \
  --results-dir results/cloudlab_smoke_c2 \
  --commitment-secret cloudlab-shared-secret
```

3. Stress smoke:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --sessions 200 \
  --concurrency 40 \
  --repeats 1 \
  --arrival-rate 50 \
  --results-dir results/cloudlab_smoke_c40 \
  --commitment-secret cloudlab-shared-secret \
  --validation-mode stress \
  --skip-setup \
  --skip-build
```

4. Medium-scale baseline matrix:

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

5. P3 small stress:

```bash
python scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --workload p3 \
  --policies naive_retry plangate_r plangate_ar \
  --sessions 100 \
  --concurrency 10 \
  --failure-rate 0.1 0.2 0.3 \
  --amendment-rate 1.0 \
  --validation-mode stress \
  --results-dir results/cloudlab_p3_small \
  --commitment-secret cloudlab-shared-secret
```

6. P4 random-routing P3 with Redis checkpoint store:

```bash
python3 scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --workload p3 \
  --policies naive_retry plangate_r plangate_ar \
  --sessions 100 \
  --concurrency 10 \
  --repeats 1 \
  --failure-rate 0.1 0.2 0.3 \
  --amendment-rate 1.0 \
  --validation-mode stress \
  --routing random \
  --recovery-store redis \
  --results-dir results/cloudlab_p3_small_random_redis_cp_v2 \
  --commitment-secret cloudlab-shared-secret \
  --skip-setup \
  --skip-build
```

7. Random-routing Redis vs memory shared-state comparison (6-node `m510` small profile):

```bash
python scripts/cloudlab/run_random_state_store_comparison.py \
  --inventory scripts/cloudlab/inventory.m510_6.json \
  --sessions 1000 \
  --concurrency 100 \
  --repeats 3 \
  --failure-rate 0.1 0.2 0.3 \
  --amendment-rate 0.2 \
  --ssh-key ~/.ssh/cloudlab_ed25519 \
  --results-dir results/cloudlab_random_redis_memory \
  --dry-run
```

This wrapper expands two runs:

- Redis correctness evidence:
  - `--plangate-state-store redis`
  - `--recovery-store redis`
  - `--validation-mode correctness`
- Memory no-shared-state diagnostic control:
  - `--plangate-state-store inmemory`
  - `--recovery-store inmemory`
  - `--validation-mode stress`

The Redis run is the shared-state correctness evidence. The memory run is a
diagnostic control for the no-shared-state boundary; if it shows `state_miss`
or related continuation failures under random routing, that is an expected
boundary signal rather than a contradiction of the Redis result. This is not a
claim about production Redis HA or fault-tolerant distributed control planes.

Validated sticky-routing command:

```bash
python3 scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --workload p3 \
  --policies naive_retry plangate_r plangate_ar \
  --sessions 100 \
  --concurrency 10 \
  --repeats 1 \
  --failure-rate 0.1 0.2 0.3 \
  --amendment-rate 1.0 \
  --validation-mode stress \
  --routing sticky \
  --results-dir results/cloudlab_p3_small_sticky_v2 \
  --commitment-secret cloudlab-shared-secret \
  --skip-setup
```

In CloudLab P3 mode, the harness starts Redis/backends/gateways itself and the
P3 runner only sends workload with:

- `--gateway-urls http://gw1:9601 http://gw2:9602 ...`
- `--routing random`
- `--no-start-services`

When `--routing random` is used for recovery-bearing P3 workloads, pass
`--recovery-store redis` so checkpoints are shared across gateway nodes.
The shared reservation record also carries the admission-time plan snapshot and
budget, so a non-admission gateway can keep `CurrentStep` and
`RemainingPlanJSON` moving forward before a later recovery/amendment resume.

## CloudLab Small Validation

Validated on 6 CloudLab `m510` bare-metal nodes running Ubuntu 22.04:

- 1 loader
- 1 Redis
- 2 gateways
- 2 backends

Correctness smoke result:

- `sessions = 20`
- `concurrency = 2`
- `success = 20/20`
- `cross_node_sessions = 17/20`
- `state_miss = 0`
- `duplicate_admission = 0`
- `cascade_failed = 0`
- `validation_passed = True`

This confirms that the P0-P2 correctness smoke validates distributed shared
state plus commitment-token replay across multiple gateways. The `C=40` run is
intended as a stress smoke, so it should use `--validation-mode stress` rather
than the default 95% correctness threshold.

## Validated Results

Validated artifact evidence is mirrored in:

- [artifact_results/cloudlab_p3_small_random_redis_cp_v2](../../artifact_results/cloudlab_p3_small_random_redis_cp_v2)
- [artifact_results/cloudlab_p3_small_sticky_v2](../../artifact_results/cloudlab_p3_small_sticky_v2)
- [artifact_results/cloudlab_smoke_c2](../../artifact_results/cloudlab_smoke_c2)

## Validated P4 Random Redis Checkpoint Result

Experiment: P4 CloudLab small random Redis checkpoint stress  
Date: 2026-05-24  
Profile: small, 6 nodes  
Topology: 1 loader, 1 Redis, 2 gateways, 2 backends  
Routing: random  
Recovery store: redis  
State store: redis  
Sessions: 100 per failure rate  
Concurrency: 10  
Failure rates: 0.1, 0.2, 0.3  
Amendment rate: 1.0  
Policies: naive_retry, plangate_r, plangate_ar  
Validation mode: stress  
Result directory: `cloudlab_p3_small_random_redis_cp_v2`

Validated command:

```bash
python3 scripts/cloudlab/run_cloudlab_experiment.py \
  --inventory scripts/cloudlab/inventory.json \
  --profile small \
  --workload p3 \
  --policies naive_retry plangate_r plangate_ar \
  --sessions 100 \
  --concurrency 10 \
  --repeats 1 \
  --failure-rate 0.1 0.2 0.3 \
  --amendment-rate 1.0 \
  --validation-mode stress \
  --routing random \
  --recovery-store redis \
  --results-dir results/cloudlab_p3_small_random_redis_cp_v2 \
  --commitment-secret cloudlab-shared-secret \
  --skip-setup \
  --skip-build
```

Sync note: when copying code to CloudLab nodes, use a root-anchored exclude such
as `--exclude '/gateway'`. Do not use bare `--exclude 'gateway'`, because that
pattern can also exclude the `cmd/gateway/` source directory.

Core result snapshot:

- `validation errors = []`
- `cross_node_sessions = 843`
- `state_miss = 0`
- `duplicate_admission = 0`
- `commitment_invalid = 0`
- `commitment_mismatch = 0`
- `commitment_expired = 0`
- `plangate_ar success_rate = 1.0 / 1.0 / 1.0`
- `plangate_ar amendment_accept_rate = 1.0 / 1.0 / 1.0`
- `v2_commitment_issued = 10 / 20 / 30`
- `plangate_ar avg_total_tool_calls = 5.1 / 5.2 / 5.3`
- `naive_retry avg_total_tool_calls = 5.3 / 5.6 / 5.9`
- all adversarial amendment `reject_rate = 1.0`
- `false_accept = 0`
- `executed_after_rejected_amendment = 0`

This is the stronger distributed evidence for **random cross-gateway**
recovery/amendment with Redis session state and Redis CheckpointStore in the
small CloudLab profile.

Experiment: P3 CloudLab small sticky stress  
Date: 2026-05-23  
Profile: small, 6 nodes  
Topology: 1 loader, 1 Redis, 2 gateways, 2 backends  
Routing: sticky  
Sessions: 100  
Concurrency: 10  
Failure rates: 0.1, 0.2, 0.3  
Amendment rate: 1.0  
Policies: naive_retry, plangate_r, plangate_ar  
Validation mode: stress  
Result directory: `cloudlab_p3_small_sticky_v2`

Core result snapshot:

- `AR success_rate = 1.0 / 1.0 / 1.0`
- `AR recovery_success_rate = 1.0 / 1.0 / 1.0`
- `AR amendment_accept_rate = 1.0 / 1.0 / 1.0`
- `v2_commitment_issued = 10 / 20 / 30`
- `AR avg_total_tool_calls = 5.1 / 5.2 / 5.3`
- `naive_retry avg_total_tool_calls = 5.3 / 5.6 / 5.9`
- `invalid amendment reject_rate = 1.0` for all 6 adversarial cases
- `commitment_invalid = 0`
- `commitment_mismatch = 0`
- `commitment_expired = 0`
- `state_miss = 0`
- `duplicate_admission = 0`
- `validation errors = []`

Limitation:

This result uses **sticky per-session routing** because the current recovery
checkpoint store is gateway-local/in-memory. It validates CloudLab multi-node
execution with gateway-local recovery affinity, not **random cross-gateway**
recovery. That stronger claim is covered instead by
`artifact_results/cloudlab_p3_small_random_redis_cp_v2`, which uses
`--routing random` together with `--recovery-store redis`.

## What gets validated

For `--workload standard`, `validate_results.py` checks:

- `total_sessions == expected sessions`
- `state_miss == 0`
- `duplicate_admission == 0`
- `cross_node_sessions > 0` for multi-gateway random routing
- `cascade_failed == 0` for no-failure smoke
- commitment invalid/mismatch/expired counts are zero
- all gateway logs advertise the same commitment-token mode and Redis address
- `success_rate >= 95%` only in `--validation-mode correctness`

In `--validation-mode stress`, standard validation still requires:

- `state_miss == 0`
- `duplicate_admission == 0`
- `cascade_failed == 0`
- commitment invalid/mismatch/expired counts are zero
- `cross_node_sessions > 0` for multi-gateway random routing

The local results root also gets:

- `summary.csv`: one row per run
- `aggregate.csv`: grouped means per concurrency/failure/amendment setting
- raw merged `steps.csv`
- loader logs
- gateway logs
- backend logs
- `redis_info.txt`

For `--workload p3`, each run gets:

- `p3_summary.csv`
- `p3_adversarial_summary.csv`
- policy-level `naive_retry/steps.csv`, `naive_retry/sessions.csv`
- policy-level `plangate_r/steps.csv`, `plangate_r/sessions.csv`
- policy-level `plangate_ar/steps.csv`, `plangate_ar/sessions.csv`
- merged top-level `steps.csv` and `sessions.csv`
- loader logs
- gateway logs
- backend logs
- `redis_info.txt`

P3 validation checks:

- all requested policies are present
- failure-rate rows cover the requested set
- `plangate_ar v2_commitment_issued > 0`
- `plangate_ar false_accept = 0`
- `plangate_ar executed_after_rejected_amendment = 0`
- `plangate_ar avg_total_tool_calls < naive_retry`
- `plangate_ar success_rate >= plangate_r`
- invalid amendment rows reject `unknown_tool`, `dag_cycle`,
  `budget_overflow`, `stale_parent`, `checkpoint_hash_mismatch`, and
  `modify_completed_prefix`
- `state_miss = 0`
- `duplicate_admission = 0`
- commitment invalid/mismatch/expired counts are zero
- `cross_node_sessions > 0` for multi-gateway random routing

## Notes

- Every gateway must share the same `--commitment-secret`.
- `run_cloudlab_experiment.py` now treats `--plangate-state-store` and
  `--recovery-store` as separate knobs and prints both in dry-run output.
- Random-routing P3 recovery across multiple gateways requires
  `--recovery-store redis`; the sticky validated artifact intentionally uses the
  default `--recovery-store inmemory`.
- For the 6-node Redis-vs-memory comparison, the Redis mode uses
  `--plangate-state-store redis --recovery-store redis`, while the diagnostic
  memory control uses
  `--plangate-state-store inmemory --recovery-store inmemory`.
- Random-routing checkpoint progress also relies on the shared reservation
  snapshot (`plan_steps` + `budget`) so any gateway can save the next recovery
  checkpoint after a successful continuation step.
- `start_backend.sh` binds backends to `0.0.0.0` so CloudLab peers can reach them.
- `setup_node.sh` installs Go 1.23.12 from the official tarball path when the
  node does not already provide Go 1.23+.
- The harness drives the existing token-aware `dag_load_generator.py`; it sends
  both `--target` and `--targets` for backward compatibility with the current
  loader CLI.
- In P3 mode, the runner never starts gateway/backend processes on loader
  nodes; that lifecycle stays under the CloudLab harness so ports and logs stay
  well-behaved.
