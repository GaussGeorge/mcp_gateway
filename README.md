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
  _verify_paper_data.py         Primary verification entry point
  _compute_bursty_stats.py      Bursty table verifier
  _compute_tput_latency_stats.py  Tput-latency crossing verifier
  gen_paper_figures.py          Paper figure generator
  plot_rajomon_sensitivity.py   Rajomon sensitivity figure
  reproduce_main_paper_from_cache.sh  Regenerate figures/tables from CSV cache
  optional_live/                (Optional) Live re-run scripts requiring API/GPU
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
python scripts/optional_live/run_all_experiments.py --exp all --repeats 5

# Single experiment
python scripts/optional_live/run_all_experiments.py --exp Exp1_Core --repeats 5

# With CPU isolation (Linux/WSL2)
python scripts/optional_live/run_all_experiments.py --exp all --repeats 5 \
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
bash scripts/optional_live/reproduce_mock_core.sh

# Re-generate paper figures from local CSV results (after re-running)
python scripts/gen_paper_figures.py
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
python scripts/optional_live/run_real_llm_bursty.py --repeats 3 --burst-size 30
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

## Minimal Reproduction

> Mock reproduction does **not** require API keys.
> PlanGate-R reproduction is P&S controlled mock only.
> Exact paper tables require full runs; minimal smoke validates qualitative trends.
> See [docs/minimal_reproduction.md](docs/minimal_reproduction.md) and
> [docs/paper_mapping.md](docs/paper_mapping.md) for full details.

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
python scripts/optional_live/run_all_experiments.py --exp Exp1_Core --repeats 1 --gateway-binary ./gateway

# Windows PowerShell
go build -o gateway.exe ./cmd/gateway
python scripts/optional_live/run_all_experiments.py --exp Exp1_Core --repeats 1 --gateway-binary gateway.exe
```
> The gateway binary is built locally and excluded from git. Omit `--gateway-binary` to let the script auto-build.

Sanity check: `plangate_full.cascade_failed == 0`, `plangate_full.effective_goodput` is highest.

**3. PlanGate mechanism ablation smoke — validates budget-lock matters (no API key, ~1–3 min, verified 0.8 min on Windows):**

```bash
# Linux / macOS / WSL2
python scripts/optional_live/run_all_experiments.py --exp Exp4_Ablation --repeats 1 --gateway-binary ./gateway

# Windows PowerShell
python scripts/optional_live/run_all_experiments.py --exp Exp4_Ablation --repeats 1 --gateway-binary gateway.exe
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