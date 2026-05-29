# PlanGate: Session Commitment for Multi-Step LLM Agent Tool Governance

This artifact supports the paper's tables and figures.

**Quick verification (no API key required):**
```bash
# Set up frozen CSV results from artifact_cache/
python scripts/setup_frozen_results.py

# Verify all paper tables against frozen CSVs
python scripts/_verify_paper_data.py

# Verify bursty N=7 table
python scripts/_compute_bursty_stats.py

# Verify tput-latency table
python scripts/_compute_tput_latency_stats.py --show-crossings

# Run gateway-overhead collection and aggregation
python scripts/run_gateway_overhead_benchmark.py --skip-live
python scripts/_compute_gateway_overhead_stats.py

# Regenerate all paper figures
python scripts/gen_paper_figures.py
python scripts/plot_rajomon_sensitivity.py
```

See [TABLE_FIGURE_MAPPING.md](TABLE_FIGURE_MAPPING.md) for the full
table/figure → data → script mapping.

No API keys are required for cached-result verification (Tier A) or mock
re-runs (Tier B). Live real-LLM re-runs (Tier C) optionally require a
provider key or GPU.

---

PlanGate is an MCP-compatible gateway that reduces **governance-induced cascading compute waste**
in multi-step LLM agent workloads by aligning admission decisions with multi-step session
semantics — enforcing *session commitment*: atomic admission, temporal isolation, and
continuation value.

For plan-accurate P&S sessions, PlanGate prevents price-induced mid-session rejection
within the declared plan scope. For ReAct sessions, it reduces mid-execution abandonment
through sunk-cost-aware continuation pricing.

| Agent type | Commitment | Mechanism |
|---|---|---|
| Plan-and-Solve (P&S) | Budget-reserved | Pre-flight DAG admission + locked price |
| ReAct | Soft continuation incentive | K^2 sunk-cost discount pricing |

Additionally, **PlanGate-R** is a checkpoint-aware recovery extension evaluated
in a controlled mock runtime (P&S only; no real LLM; no ReAct semantic
recovery). It is not a primary contribution.

## Commitment Token Protocol

PlanGate can sign successful Plan-and-Solve step-0 admissions as an externally
verifiable session contract. The token covers the admitted session id, final
plan hash, locked-price hash, total cost, step count, expiry, gateway node,
state-store type, and a reserved `recovery_enabled` claim. It does not provide
stateless continuation: later P&S steps still require the local or shared
reservation to exist. It also does not claim replay prevention.

Headers:

- `X-Commitment-Token`: returned after a successful multi-step P&S step-0
  admission, then sent by token-aware clients on later P&S steps.
- `X-Commitment-Status`: `issued`, `validated`, `legacy`, `missing`,
  `invalid`, `expired`, `mismatch`, or `disabled`.
- `X-Commitment-Error`: short reason when validation fails.

Modes:

- `--commitment-token-mode=off`: no token issuance or validation.
- `--commitment-token-mode=optional` (default): step-0 issues a token; later
  steps with a token must validate; old clients without a token continue over
  the `X-Session-ID` reservation path and are marked `legacy`.
- `--commitment-token-mode=strict`: P&S continuation steps must carry a valid
  token or the gateway returns `CodeInvalidParams`.

Security boundary: the token is HMAC-SHA256 over
`base64url(header).base64url(payload)` using `--commitment-token-secret` or
`PLANGATE_COMMITMENT_SECRET`. In Redis multi-gateway strict mode, all nodes
must share the same explicit secret. In any multi-gateway deployment where
clients replay `X-Commitment-Token`, every gateway must use the same
commitment secret; for legacy artifact runs, use
`--commitment-token-mode=off`, and for optional multi-gateway experiments pass
an explicit shared secret.

Example:

```bash
go test ./plangate/... -run "Commitment|Token|PlanAndSolve|MultiGateway" -count=1

./gateway --mode mcpdp --backend http://127.0.0.1:8080 --port 9200 \
  --commitment-token-mode=optional \
  --commitment-token-secret=dev-shared-secret

./gateway --mode mcpdp --backend http://127.0.0.1:8080 --port 9201 \
  --plangate-state-store=redis --plangate-redis-addr=127.0.0.1:6379 \
  --commitment-token-mode=strict \
  --commitment-token-secret=dev-shared-secret

python scripts/run_multigateway_shared_state.py --modes shared_random \
  --commitment-token-mode optional \
  --commitment-token-secret test-shared-secret
```

## Commitment Token v2-minimal

PlanGate uses two commitment token shapes:

- v1 `ps_commitment`: the initial Plan-and-Solve session commitment issued
  after a successful multi-step step-0 admission.
- v2 `ps_amended_commitment`: the recovery-only amended commitment issued
  after a validated delta-plan amendment is applied to a checkpointed session.

The v2-minimal token extends the recovery path with commitment-chain binding.
An accepted amendment is tied to:

- the full SHA256 base64url hash of the parent commitment token
- the amendment delta hash
- the stable checkpoint hash
- the amendment chain hash

This lets the gateway reject stale amended parents and makes each accepted
recovery amendment externally verifiable as part of a chained commitment
history. It is still a minimal protocol surface: it does not claim replay
prevention, revocation, nonce management, key rotation, or a complete audit
signature pipeline.

Example verification:

```bash
go test ./plangate/... -run "Commitment|Amendment|Recovery|PlanAndSolve" -count=1
```

## CloudLab Validated Artifact Results

Lightweight validated CloudLab evidence is checked into:

- [artifact_results/cloudlab_p3_small_random_redis_cp_v2](artifact_results/cloudlab_p3_small_random_redis_cp_v2)
- [artifact_results/cloudlab_p3_small_sticky_v2](artifact_results/cloudlab_p3_small_sticky_v2)
- [artifact_results/cloudlab_smoke_c2](artifact_results/cloudlab_smoke_c2)

The `cloudlab_p3_small_random_redis_cp_v2` bundle records the validated P4
CloudLab small random-routing run with Redis session state and Redis
CheckpointStore:

- topology: 1 loader, 1 Redis, 2 gateways, 2 backends
- routing: **random cross-gateway**
- recovery store: `redis`
- sessions: 100 per failure rate
- concurrency: 10
- failure rates: 0.1 / 0.2 / 0.3
- amendment rate: 1.0
- policies: `naive_retry`, `plangate_r`, `plangate_ar`
- `cross_node_sessions = 843`
- `v2_commitment_issued = 10 / 20 / 30`
- `state_miss = 0`
- `duplicate_admission = 0`
- `commitment_mismatch = 0`
- `validation errors = []`

This is the stronger distributed recovery evidence: it validates random
cross-gateway recovery with Redis CheckpointStore for the small CloudLab
profile.

The `cloudlab_p3_small_sticky_v2` bundle remains useful as a simpler baseline
and records the validated P3 CloudLab small sticky-stress run from 2026-05-23:

- topology: 1 loader, 1 Redis, 2 gateways, 2 backends
- routing: **sticky per-session**
- sessions: 100
- concurrency: 10
- failure rates: 0.1 / 0.2 / 0.3
- amendment rate: 1.0
- policies: `naive_retry`, `plangate_r`, `plangate_ar`
- `v2_commitment_issued = 10 / 20 / 30`
- `commitment_mismatch = 0`
- `state_miss = 0`
- `duplicate_admission = 0`
- `validation errors = []`

The sticky evidence does **not** by itself claim **random cross-gateway**
recovery. That specific claim is covered by
`cloudlab_p3_small_random_redis_cp_v2`, while the sticky run remains a simpler
gateway-local recovery-affinity baseline.

## Mock Regression Artifact Refresh

Lightweight local mock regression evidence is checked into:

- [artifact_results/mock_regression_p4_refresh_v1](artifact_results/mock_regression_p4_refresh_v1)
- [artifact_results/exp11_newmechanismablation_v1/README_RESULT.md](artifact_results/exp11_newmechanismablation_v1/README_RESULT.md)
- [artifact_results/throughput_latency_summary_v1/README_RESULT.md](artifact_results/throughput_latency_summary_v1/README_RESULT.md)

This bundle is a local mock regression refresh from `2026-05-24`, not a
CloudLab run. It covers `Exp1`, `Exp4`, `Exp8`, and `Exp10` after the
Commitment / Amendment / Recovery / Redis CheckpointStore work:

- `Exp1`: `plangate_full EffGP/s = 54.76`, above the best baseline
  `sbac = 45.36`, with `cascade_failed_mean = 0.20`
- `Exp4`: `wo_budgetlock EffGP/s = 10.95`, `cascade_failed_mean = 8.80`
- `Exp4`: `wo_sessioncap` stays near full throughput, but
  `cascade_failed_mean = 3.60`, so session cap still matters as a safety
  component
- `Exp10`: `plangate_full EffGP/s = 57.28`, above `sbac = 44.70`, with
  `cascade_failed_mean = 0.40`

`Exp8` is included as **diagnostic evidence** only: the regression refresh
completes, but the current run has low success counts and relatively high
cascade counts, so it should not be used as a strong paper claim without a
later configuration review.

The `exp11_newmechanismablation_v1` bundle is a separate post-P3/P4 mock
diagnostic/regression evidence pack for the newly added control mechanisms. It
records `plangate_full`, `wo_commitment`, `wo_amendment`, and `wo_recovery`
under the standard Exp11 load shape, with `20` summary rows and `4` aggregate
rows. It also documents that:

- `plangate_full`: `success_mean=90.6`, `EffGP/s=55.38`
- `wo_commitment`: `success_mean=82.2`, `EffGP/s=50.64`
- `wo_amendment`: `success_mean=87.6`, `EffGP/s=56.75`
- `wo_recovery`: `success_mean=88.0`, `cascade_failed_mean=0.2`, `EffGP/s=55.60`

This bundle should **not** be over-claimed as the strongest recovery evidence;
the failure-specific P3 workload remains the more direct evidence for
Recovery/Amendment behavior. It is instead useful as a lightweight regression
check that the new mechanism toggles remain runnable and summary-compatible.

The `throughput_latency_summary_v1` bundle is a summary-only artifact built
from existing `Exp1`, `Exp5`, `Exp6`, and `Exp10` summary CSVs. It reports
both `raw_goodput_s` and `effective_goodput_s`, alongside `success`,
`rejected_s0`, `cascade_failed`, `p95_ms`, and `e2e_p95_ms`. This helps answer
the natural throughput/latency question without introducing a new experiment:
raw throughput captures admitted/processed work rate, while effective goodput
discounts wasted progress and remains the main governance-facing metric.

## P3 Failure Mechanism Ablation

Lightweight local P3 failure/amendment ablation evidence is checked into:

- [artifact_results/p3_failure_mechanism_ablation_v1/README_RESULT.md](artifact_results/p3_failure_mechanism_ablation_v1/README_RESULT.md)

This bundle is separate from the standard mock regression evidence and from the
CloudLab recovery evidence. It is a controlled local P3 workload that isolates
the post-P3 mechanisms under failure/amendment pressure:

- `plangate_full`
- `wo_commitment`
- `wo_amendment`
- `wo_recovery`

Key signals from the aggregate CSV:

- `plangate_full`: `success_mean=192.33`, `recovery_success_mean=38.0`,
  `amendment_success_mean=7.67`
- `wo_commitment`: `commitment_issued_mean=0.0`, `success_mean=178.67`
- `wo_amendment`: `amendment_success_mean=0.0`
- `wo_recovery`: `recovery_success_mean=0.0`, `cascade_failed_mean=42.0`

This is stronger than the standard mock-load Exp11 ablation for the new
mechanisms, but it remains **local controlled evidence**, not a multi-node
CloudLab result.

## Live GLM Artifact Refresh

Lightweight live GLM real-LLM evidence is checked into:

- [artifact_results/glm_real_llm_c10_refresh_v1](artifact_results/glm_real_llm_c10_refresh_v1)

This bundle is separate from both the mock regression evidence and the CloudLab
recovery evidence. It records a local `glm-4-flash` rerun with `200 agents`,
`concurrency 10`, `3 repeats`, and gateways `ng / rajomon / pp / plangate_real`.
The bundle includes only summary CSVs, flattened `steps_summary_*.csv`, and
validation metadata; it intentionally omits API keys, `.env`, full logs, and
full `steps.csv`.

Key results from `week5_agg.csv`:

- `ng`: `success_rate_mean=97.83`, `ABD=2.17`, `EffGP/s=0.46`
- `rajomon`: `success_rate_mean=96.33`, `ABD=3.50`, `EffGP/s=0.45`
- `pp`: `success_rate_mean=94.50`, `ABD=5.03`, `EffGP/s=0.43`
- `plangate_real`: `success_rate_mean=95.83`, `ABD=3.67`, `EffGP/s=0.43`

This live GLM refresh validates that the post-P4 stack still supports real-LLM
ReAct workloads without client/runtime errors or timeout in this C10 rerun. It
should **not be over-claimed** as PlanGate outperforming every baseline on
every real-LLM metric in this particular run.

## DeepSeek Live Smoke Evidence

Lightweight DeepSeek V4 Flash live-smoke evidence is checked into:

- [artifact_results/deepseek_v4_flash_smoke_v1](artifact_results/deepseek_v4_flash_smoke_v1)

This bundle is post-GLM live-provider evidence for `deepseek-v4-flash` at
`C5`, with `50 agents`, `concurrency 5`, and one run each for
`ng / rajomon / pp / plangate_real`. It is distinct from the mock regression
evidence and the CloudLab P3/P4 recovery evidence, and it intentionally omits
API keys, `.env`, full logs, and full per-step traces.

Key smoke results:

- `ng`: `success_rate=98.0`, `ABD=2.0`, `EffGP/s=0.59`
- `rajomon`: `success_rate=96.0`, `ABD=4.0`, `EffGP/s=0.52`
- `pp`: `success_rate=98.0`, `ABD=2.0`, `EffGP/s=0.56`
- `plangate_real`: `success_rate=98.0`, `ABD=2.0`, `EffGP/s=0.57`

This evidence confirms live DeepSeek V4 Flash connectivity and tool-call
execution through the real-LLM runner, but it should **not be over-claimed** as
PlanGate outperforming every baseline in real-LLM experiments.

## Self-Hosted vLLM Stress Evidence

Lightweight self-hosted vLLM stress evidence is checked into:

- [artifact_results/selfhosted_vllm_stress_c16w8_tuned_5gw_v1/README_RESULT.md](artifact_results/selfhosted_vllm_stress_c16w8_tuned_5gw_v1/README_RESULT.md)

This bundle is a local self-hosted stress artifact, not a CloudLab result and
not a new paper-wide claim surface. It records the submitted 5-gateway
comparison under moderate congestion with:

- `ng`
- `static`
- `pp`
- `rajomon`
- `plangate_relaxed`

Key checks recorded in `validation.json`:

- `row_count = 15`
- `agg_row_count = 5`
- `plangate_real_absent = true`
- `all_error_zero = true`
- `all_client_rc_zero = true`
- `all_client_timed_out_zero = true`
- `plangate_relaxed_present = true`
- `congestion_present = true`

The value of this bundle is comparative and diagnostic: it preserves a concrete
self-hosted vLLM congestion snapshot for the main-paper display subset,
without claiming that this single stress result settles every real-LLM
governance comparison. A conservative diagnostic profile was used during local
sensitivity checking but is not included in the submitted vLLM artifact.

## Self-Hosted vLLM Multi-Intensity Sweep Evidence

Lightweight self-hosted vLLM multi-intensity sweep evidence is checked into:

- [artifact_results/selfhosted_vllm_profile_sweep_v1/README_RESULT.md](artifact_results/selfhosted_vllm_profile_sweep_v1/README_RESULT.md)

This bundle is the profile sweep artifact for backend-congestion boundary
characterization over `C=8/12/16/20` with the submitted gateway set
`ng/static/pp/rajomon/plangate_relaxed` (`PlanGate (tuned)` in paper labels).
It intentionally excludes `plangate_real` and keeps only summary-level files:

- `selfhosted_vllm_profile_sweep_summary.csv`
- `selfhosted_vllm_profile_sweep_agg.csv`
- `validation.json`
- `README_RESULT.md`

Interpretation boundary: low-contention points need not favor PlanGate, while
higher-congestion points (`C16/C20`) are used to characterize completion and
cascade-pressure behavior under saturation.

## CloudLab Random-Routing Redis vs Memory Evidence

Lightweight CloudLab Redis-vs-memory shared-state evidence is checked into:

- [artifact_results/cloudlab_random_redis_memory_v1/README_RESULT.md](artifact_results/cloudlab_random_redis_memory_v1/README_RESULT.md)

This bundle is a narrow shared-state correctness artifact under random routing.
It compares a Redis-backed correctness arm against an in-memory diagnostic
control without copying raw logs or rerunning CloudLab:

- `cloudlab_random_redis_memory_summary.csv`
- `cloudlab_random_redis_memory_agg.csv`
- `validation.json`
- `README_RESULT.md`

Key aggregate facts:

- Redis: `runs=3`, `cross_node_sessions_sum=21877`, `state_miss_sum=0`
- Memory control: `runs=3`, `cross_node_sessions_sum=12398`, `state_miss_sum=9813`
- `validation.json` records `errors=[]`

Interpretation boundary: this supports random-routing shared-state lookup
correctness, not a universal performance claim and not a production Redis HA
claim.

## P3 Failure/Amendment Grid Evidence

Lightweight P3 failure/amendment grid evidence is checked into:

- [artifact_results/p3_failure_amendment_grid_v1/README_RESULT.md](artifact_results/p3_failure_amendment_grid_v1/README_RESULT.md)

This bundle extends the local failure/amendment mechanism evidence from a
single workload point to a six-cell `(failure_rate, amendment_rate)` grid.
Its role is mechanism-consistency boundary checking, not a universal ranking:

- disabling recovery drives `recovery_success_mean=0.0` in every cell
- disabling amendment drives `amendment_success_mean=0.0` in every cell
- Full is favorable in most cells, but not every low-failure boundary cell

## Statistical Summary / CI Evidence

The machine-readable statistical summary artifact is checked into:

- [artifact_results/statistical_summary_v1/README_RESULT.md](artifact_results/statistical_summary_v1/README_RESULT.md)

This bundle aggregates existing artifact CSVs only. It now includes:

- mock regression summaries
- P3 mechanism ablation
- P3 failure/amendment grid
- self-hosted vLLM stress and profile sweep
- throughput/latency summary
- CloudLab Redis-vs-memory shared-state diagnostic

Its validation target is:

- `cloudlab_included = true`
- `errors = []`

## Gateway Processing Overhead

The gateway-overhead benchmark is split into two layers:

1. Go in-process microbenchmarks for DAG validation, price computation, session lookup, P&S admission, and HTTP routing (primary overhead evidence).
2. Live mock/back-end traces that record `X-Gateway-Latency-Us` for successful admitted steps, step-0 rejections, and all handled requests (appendix diagnostic only).

Outputs are written under `results/exp_gateway_overhead/`:

- `go_bench_overhead.txt`
- `go_bench_overhead.csv`
- `gateway_overhead_agg.csv`
- `gateway_overhead_cdf.csv`

Note: `X-Gateway-Latency-Us` is a gateway-observed service-time signal and may include proxied backend/tool execution. Use `go_bench_overhead.txt/csv` for pure gateway processing-overhead claims.

Recommended entry points:

```bash
python scripts/run_gateway_overhead_benchmark.py --skip-live
python scripts/_compute_gateway_overhead_stats.py
```

---

## Repository Structure

```
cmd/gateway/          Go gateway entry point (main.go)
plangate/             Core Go package: admission, pricing, DAG validation,
                      checkpoint/recovery, reputation
baseline/             Baseline gateway implementations (NG, SRL, SBAC,
                      DAGOR, Rajomon, ProgressPriority)
mcp_server/           Python MCP backend + tool implementations
  server.py           ThreadPoolExecutor MCP server
  tools/              calculator, weather, web_fetch, mock_heavy,
                      llm_reasoner, deepseek_llm, ...
scripts/              Experiment runner, analysis, and plotting scripts
  run_all_experiments.py        Main mock experiment driver
  run_gateway_overhead_benchmark.py  Gateway-overhead benchmark driver
  reproduce_mock_core.sh        Reproducibility: mock core experiments
  reproduce_main_paper_from_cache.sh  Re-generate figures from CSV cache
  gen_paper_figures.py         Paper figure generator
docs/                 Artifact documentation
  REPRODUCIBILITY.md  Full reproduction guide with expected runtimes
  TABLE_FIGURE_MAPPING.md  Maps paper items to data files and scripts
```

---

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Go | >= 1.23 | `go version` |
| Python | >= 3.10 | `python --version` |
| OS | Linux / macOS / Windows (WSL2) | Native Windows: PowerShell scripts also provided |

Install Python dependencies:

```bash
# Core only (Tier A verification + Tier B mock re-run, no API key needed)
pip install -r requirements.txt

# Tier C (real-LLM live re-run): also install mcp_server/requirements.txt
pip install -r mcp_server/requirements.txt
```

Build the gateway binary:

```bash
go build -o gateway ./cmd/gateway
# Cross-compile for Linux:
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o gateway_linux ./cmd/gateway
```

---

## Quick Start: Smoke Test (no API keys required)

All unit tests and mock experiments run without any real LLM provider.

```bash
# Run full Go test suite
go test ./... -timeout 120s

# Run plangate package tests only
go test ./plangate/... -timeout 120s -v
```

Expected output: `ok mcp-governance/plangate`, `ok mcp-governance/baseline`,
`ok mcp-governance`.

Minimal end-to-end mock smoke test:

```bash
# 1. Start the Python mock backend
python mcp_server/server.py --host 127.0.0.1 --port 8080 --max-workers 10 &

# 2. Start the PlanGate gateway in mock mode
./gateway --mode mcpdp --backend http://127.0.0.1:8080 --port 9200 \
          --max-sessions 200 --base-price 10 --alpha 0.5 &

# 3. Run Go unit tests (validates core logic; no API key)
go test ./... -timeout 120s
```

---

## Running Controlled Mock Experiments (Tier 1)

```bash
# All 12 mock experiments, 5 repeats each
python scripts/run_all_experiments.py --exp all --repeats 5

# Single experiment
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 5

# With CPU isolation (Linux/WSL2)
python scripts/run_all_experiments.py --exp all --repeats 5 \
    --gateway-binary ./gateway_linux \
    --cpu-backend 8-15 --cpu-gateway 4-7 --cpu-loadgen 0-3
```

Results are written to `results/exp1_core/`, `results/exp2_heavyratio/`, etc.
The `results/` directory is excluded from the public repo (see `.gitignore`).

---

## Reproducing Key Results

The public repository contains source code and no-key minimal reproduction commands.
Full cached traces for real-LLM experiments are not tracked in this public branch;
they are distributed separately through the conference artifact submission mechanism,
not as a public GitHub release.

Mock core experiments can be re-run from scratch:

```bash
# Re-run mock core experiments from scratch (no API key, ~30–45 min)
bash scripts/reproduce_mock_core.sh

# Re-generate paper figures from local CSV results (after re-running)
python scripts/gen_paper_figures.py
```

See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) for full instructions,
expected runtimes, and troubleshooting.
See [TABLE_FIGURE_MAPPING.md](TABLE_FIGURE_MAPPING.md) for a mapping of paper
items to specific data files.

---

## Running Real-LLM Experiments (Tier 2 / 3)

Real-LLM experiments require a provider API key. Copy `.env.example` to `.env`
and fill in `LLM_API_KEY`:

```bash
cp .env.example .env
# Edit .env and set LLM_API_KEY, LLM_API_BASE, LLM_MODEL
```

```bash
# 1) Minimal GLM connectivity check
python scripts/check_glm_connectivity.py

# 2) Small single-gateway smoke
python scripts/run_real_llm_week5.py \
  --repeats 1 \
  --agents 5 \
  --concurrency 1 \
  --max-steps 3 \
  --gateways pp \
  --client-timeout 300 \
  --client-log-live

# 3) Two-gateway smoke
python scripts/run_real_llm_week5.py \
  --repeats 1 \
  --agents 20 \
  --concurrency 2 \
  --max-steps 5 \
  --gateways pp plangate_real \
  --client-timeout 900 \
  --client-log-live

# 4) Only after the smoke passes, consider larger real-LLM runs
python scripts/run_real_llm_week5.py --repeats 5 --agents 200 --concurrency 10

# Tier 3: Bursty real-LLM
python scripts/run_real_llm_bursty.py --repeats 3 --burst-size 30
```

Notes:

- `run_real_llm_week5.py` now runs a GLM preflight by default before it starts
  the backend or gateway. Use `--skip-llm-preflight` only when you have already
  confirmed connectivity.
- The runner writes per-gateway client logs to
  `results/log/real_llm/_client_<gateway>_week5.log`.
- If the provider call fails, the preflight or client log should show whether
  the failure was due to a missing key, auth error, timeout, network issue, or
  SDK/dependency problem.

---

## Gateway Strategies

| Strategy | Mode flag | Description | Source |
|---|---|---|---|
| No-Gov (NG) | `ng` | Pass-through, no governance | `baseline/ng_gateway.go` |
| SRL | `srl` | Static rate limiter (token bucket) | `baseline/srl_gateway.go` |
| SBAC | `sbac` | Session-count-based admission | `baseline/sbac_gateway.go` |
| DAGOR | `dagor` | RTT-threshold overload detection | `baseline/dagor_gateway.go` |
| Rajomon | `rajomon` | Queuing-delay dynamic pricing | `baseline/rajomon_gateway.go` |
| ProgressPriority | `pp` | Step-progress priority | `baseline/progress_priority_gateway.go` |
| **PlanGate** | `mcpdp` | Session commitment (this work) | `plangate/`, `cmd/gateway/` |

---

## PlanGate-R: Checkpoint-Aware Recovery Extension

PlanGate-R handles recoverable transient backend failures within admitted P&S
sessions by checkpointing completed steps and resuming from the last verified
step, rather than restarting from scratch.

**Scope:** Evaluated in a controlled mock runtime only (P&S sessions, mock
handlers, injected recoverable failures, no real LLM). Not a ReAct semantic
recovery experiment. Results do not transfer to real-LLM or production
workloads without further evaluation.

Key result at failure_rate=0.3: PlanGate-R matches naive retry's eventual
success rate (1.000) while reducing waste steps by 66.7% and total tool calls
by 10.1% per run.

---

## Minimal Reproduction

> Mock reproduction does **not** require API keys.
> PlanGate-R reproduction is P&S controlled mock only.
> Exact paper tables require full runs; minimal smoke validates qualitative trends.
> See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) and
> [TABLE_FIGURE_MAPPING.md](TABLE_FIGURE_MAPPING.md) for full details.

**One-click targets (Linux/macOS/WSL2):**
```bash
make smoke               # Go unit tests (< 1 min)
make reproduce-core      # Exp1_Core mock (~2 min)
make reproduce-ablation  # Exp4_Ablation mock (~1 min)
make reproduce-recovery  # PlanGate-R Go tests (< 2 min)
make pareto-dryrun       # Pareto sweep dry-run (instant)
```

**One-click targets (Windows PowerShell):**
```powershell
.\scripts\artifact_smoke.ps1 -Target smoke
.\scripts\artifact_smoke.ps1 -Target reproduce-core
.\scripts\artifact_smoke.ps1 -Target reproduce-ablation
.\scripts\artifact_smoke.ps1 -Target reproduce-recovery
.\scripts\artifact_smoke.ps1 -Target pareto-dryrun
```

**1. Unit tests (no server, no API key, < 1 min):**
```bash
go test ./... -timeout 120s
```

**2. PlanGate core mock smoke — validates PlanGate reduces cascade vs NG/SBAC (no API key, ~1–5 min, verified 1.2 min on Windows):**

```bash
# Linux / macOS / WSL2
go build -o gateway ./cmd/gateway
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1 --gateway-binary ./gateway

# Windows PowerShell
go build -o gateway.exe ./cmd/gateway
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1 --gateway-binary gateway.exe
```
> The gateway binary is built locally and excluded from git. Omit `--gateway-binary` to let the script auto-build.

Sanity check: `plangate_full.cascade_failed == 0`, `plangate_full.effective_goodput` is highest.

**3. PlanGate mechanism ablation smoke — validates budget-lock matters (no API key, ~1–3 min, verified 0.8 min on Windows):**

```bash
# Linux / macOS / WSL2
python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1 --gateway-binary ./gateway

# Windows PowerShell
python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1 --gateway-binary gateway.exe
```
Sanity check: `wo_budgetlock.effective_goodput` ~83% lower than `plangate_full`.

**4. PlanGate-R recovery smoke — validates checkpoint resume / no replay (no API key, < 2 min, Go tests only):**
```bash
go test ./plangate/... -run "TestRuntime" -v -timeout 120s
```

**What is NOT included:**
- `paper/` — unpublished draft, excluded entirely.
- `results/` — large experiment CSVs, excluded (see `.gitignore`).
- Real LLM credentials — not needed for mock or unit tests.
- Large tokenizer asset (`scripts/deepseek_v3_tokenizer/tokenizer.json`) — see
  [`scripts/deepseek_v3_tokenizer/README.md`](scripts/deepseek_v3_tokenizer/README.md).
- Real LLM / vLLM experiments (Tables 6–8) are optional and require external setup.

---

## Artifact Notes

- `paper/` is excluded from this public repository.
- `results/` (large experiment CSVs) is **not committed**; excluded via `.gitignore`.
  Cached paper-result CSVs are distributed via the **conference supplementary artifact** only.
- API keys: copy `.env.example` to `.env` and fill in values.
  Mock experiments and all unit tests do **not** require any API key.
- Large external tokenizer assets are not tracked; see
  [`scripts/deepseek_v3_tokenizer/README.md`](scripts/deepseek_v3_tokenizer/README.md)
  for how to obtain `tokenizer.json` if needed for token-accounting scripts.
- One-click reproduction: `make <target>` (Linux/macOS/WSL2) or
  `.\scripts\artifact_smoke.ps1 -Target <target>` (Windows). Run `make help` for the full target list.
- `figures-from-cache` target requires the conference supplementary artifact unpacked to `artifact_cache/`.

---

## License

This repository is released under the Apache License 2.0. See [LICENSE](LICENSE) for details.

---

## Citation

```bibtex
@inproceedings{plangate2026,
  title     = {PlanGate: Session Commitment for Multi-Step LLM Agent Tool Governance},
  author    = {Anonymous},
  booktitle = {Anonymous Submission},
  year      = {2026}
}
```
