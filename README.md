# PlanGate: Session Commitment for Multi-Step LLM Agent Tool Governance

PlanGate is an MCP-compatible gateway that prevents **cascading compute waste**
in multi-step LLM agent workloads by enforcing *session commitment*: atomic
admission, temporal isolation, and continuation value.

Traditional per-request governance (rate limiters, load shedders) shares a
structural flaw: its *governance unit* is the individual tool call, while the
*workload unit* is the multi-step session. When a session is admitted at step 0
but rejected at step K, all compute from steps 1 to K-1 is irrecoverably lost.
Under 200-concurrent-session load with no governance, 65% of admitted sessions
are doomed to fail mid-execution.

PlanGate addresses this through two complementary policies:

| Agent type | Commitment | Mechanism |
|---|---|---|
| Plan-and-Solve (P&S) | Budget-reserved | Pre-flight DAG admission + locked price |
| ReAct | Soft continuation incentive | K^2 sunk-cost discount pricing |

Additionally, **PlanGate-R** is a checkpoint-aware recovery extension evaluated
in a controlled mock runtime (P&S only; no real LLM; no ReAct semantic
recovery). It is not a primary contribution.

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
  run_exp_real3_all.sh          Real-LLM (GLM/DeepSeek) experiment driver
  reproduce_mock_core.sh        Reproducibility: mock core experiments
  reproduce_main_paper_from_cache.sh  Re-generate figures from CSV cache
  gen_ccfa_figures.py           Paper figure generator
docs/                 Artifact documentation
  REPRODUCIBILITY.md  Full reproduction guide with expected runtimes
  RESULT_MAPPING.md   Maps paper items to data files
  experiment_code_mapping.md
```

---

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Go | >= 1.23 | `go version` |
| Python | >= 3.10 | `python --version` |

Install Python dependencies:

```bash
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

# 3. Smoke test (50 sessions, no API key needed)
python scripts/smoke_test_multitool.py \
    --gateway http://127.0.0.1:9200 --sessions 50
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

## Reproducing Key Results from Cached CSV

Pre-computed CSV traces for the paper results can be re-analyzed and plotted
without running live experiments:

```bash
# Re-generate all paper figures from cached CSVs
bash scripts/reproduce_main_paper_from_cache.sh

# Re-generate appendix figures from cached CSVs
bash scripts/reproduce_appendix_from_cache.sh

# Re-run mock core experiments from scratch
bash scripts/reproduce_mock_core.sh
```

See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) for full instructions,
expected runtimes, and troubleshooting.
See [docs/RESULT_MAPPING.md](docs/RESULT_MAPPING.md) for a mapping of paper
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
# Tier 2: Steady real-LLM (GLM-4-Flash or DeepSeek-V3)
bash scripts/run_exp_real3_all.sh                    # default: GLM-4-Flash
bash scripts/run_exp_real3_all.sh --deepseek         # DeepSeek-V3

# Tier 3: Bursty real-LLM
python scripts/run_real_llm_bursty.py --repeats 3 --burst-size 30
```

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

## Artifact Notes

- `paper/` is excluded from this public repository.
- `results/` (large experiment CSVs) is excluded from this public repository.
- API keys: copy `.env.example` to `.env` and fill in values.
  Mock experiments and all unit tests do **not** require any API key.
- Large external tokenizer assets are not tracked; see
  [`scripts/deepseek_v3_tokenizer/README.md`](scripts/deepseek_v3_tokenizer/README.md)
  for how to obtain `tokenizer.json` if needed for token-accounting scripts.
- Pre-computed cached CSV traces for the main paper results are available in
  the reproducibility release package (see `docs/REPRODUCIBILITY.md`).

---

## License

See [LICENSE](LICENSE) for license terms.
*(If no LICENSE file is present, please add one before making the repository
public.)*

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