# Reproducibility Guide — PlanGate

This document covers prerequisites, expected runtimes, common issues, and FAQ for reproducing all results in the PlanGate paper.

For a quick mapping of paper items to data files and commands, see [../TABLE_FIGURE_MAPPING.md](../TABLE_FIGURE_MAPPING.md).  
For the scope declaration (what is and isn't in the artifact), see [../ARTIFACT_SCOPE.md](../ARTIFACT_SCOPE.md).

---

## Prerequisites

### Required for All Levels

| Tool | Version | Notes |
|------|---------|-------|
| Go | ≥ 1.21 | `go version` to check |
| Python | ≥ 3.10 | `python --version` to check |
| Git | any | for cloning |

Install Python dependencies:
```bash
pip install -r mcp_server/requirements.txt
# Core deps: aiohttp, numpy, matplotlib, scipy, pandas
```

Build the gateway binary once:
```bash
go build -o gateway ./cmd/gateway                        # Windows / macOS
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o gateway_linux ./cmd/gateway  # Linux cross-compile
```

### Optional: Live Experiment Prerequisites (Not Required for Artifact Validation)

- A `.env` file in the repo root (do NOT commit this file):
  ```
  LLM_API_KEY=<your-key>
  LLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
  LLM_MODEL=glm-4-flash
  ```
- Internet access to the LLM endpoint.
- For the self-hosted vLLM step: a GPU with ≥ 16 GB VRAM and vLLM ≥ 0.4.0 running at `localhost:8000`.

---

## Expected Runtimes

| Level | Script | Time | API needed? |
|-------|--------|------|-------------|
| L0 | `go test ./...` | < 1 min | no |
| L1 | `reproduce_main_paper_from_cache.sh` | 5–10 min | no |

Timings are on a 16-core Linux machine.

---

## Quick Start (Linux / WSL2)

```bash
git clone <repo>
cd mcp-governance-main
pip install -r mcp_server/requirements.txt
go build -o gateway ./cmd/gateway

# Level 0: unit tests only (< 1 min, no API key)
go test ./... -timeout 120s

# Level 1 PlanGate-R recovery
go test ./plangate/... -run "TestRuntime" -v -timeout 120s

# Reproduce paper tables/figures from frozen artifact_cache/ (no API key, < 5 min)
bash scripts/reproduce_main_paper_from_cache.sh
```

> **Note:** `artifact_cache/` is included in this anonymous artifact repository.
> Run `python scripts/setup_frozen_results.py` to populate `results/` from the cache
> before running any verification or figure-generation scripts.

## Quick Start (Windows PowerShell)

For the no-key mock reproduction path on Windows (no WSL2 required):

```powershell
# Run unit tests and PlanGate-R recovery tests
go test ./... -timeout 120s
go build -o gateway.exe ./cmd/gateway
go test ./plangate/... -run "TestRuntime" -v -timeout 120s
```

For the from-cache bash scripts on Windows (requires WSL2 or Git Bash):

```powershell
# Option A: WSL2
wsl bash scripts/reproduce_main_paper_from_cache.sh

# Option B: Git Bash
"C:\Program Files\Git\bin\bash.exe" scripts/reproduce_main_paper_from_cache.sh
```

---

## Cached Data Inventory

The following summary CSVs are included under `artifact_cache/` in this repository.
Run `python scripts/setup_frozen_results.py` once to copy them into `results/`
before running any verification scripts.

All paper tables and figures can be regenerated without re-running any experiments:

| File (under `artifact_cache/`) | Paper Section |
|------|--------------|
| `exp1_core/exp1_core_summary.csv` | §4.2 Core results (Table 3) |
| `exp4_ablation/exp4_ablation_summary.csv` | §4.3 Ablation (Table 4) |
| `exp8_discountablation/exp8_discountablation_summary.csv` | §4.3 Discount ablation |
| `exp_rajomon_sensitivity/` | §4.3 Rajomon sensitivity |
| `exp_alpha_sweep/alpha_sweep_summary.csv` | §4.3 Alpha sensitivity |
| `beta_ablation/beta_summary.csv` | Appendix: Beta sensitivity |
| `exp_week5_C10/week5_summary.csv` | §4.4 Steady GLM C=10 |
| `exp_week5_C40/week5_summary.csv` | §4.4 Steady GLM C=40 |
| `exp_bursty_C20_B30/bursty_summary.csv` | §4.6 Bursty GLM |
| `exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv` | §4.7 vLLM C=20/W=8 |
| `exp10_adversarial/exp10_adversarial_summary.csv` | §4.5 Adversarial |
| `exp_tput_latency/tput_latency_agg.csv` | §4.8 Throughput-latency figure |
| `exp_week4_formal/week2_smoke_summary.csv` | §4.1 Commitment Quality (Table 2) |
| `paper_figures/table_commitment_quality.tex` | §4.1 Table 2 (pre-built LaTeX) |

---

## Troubleshooting

### `go build` fails with missing module

```bash
go mod tidy
go build -o gateway ./cmd/gateway
```

### Python `ModuleNotFoundError`

```bash
pip install -r mcp_server/requirements.txt
# Or: pip install aiohttp numpy matplotlib scipy pandas
```

### Mock backend port already in use (`address already in use :8080`)

```bash
# Linux/macOS
lsof -ti:8080 | xargs kill -9

# Windows PowerShell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8080).OwningProcess -Force
```

### `reproduce_main_paper_from_cache.sh` exits with "CSV not found"

Run `python scripts/setup_frozen_results.py` first to copy `artifact_cache/` data into `results/`.

### Real-LLM script exits with "LLM_API_KEY is not set"

Create a `.env` file in the repo root (see Prerequisites above). Do NOT commit it. The `.gitignore` already excludes `.env`.

### Rate-limit errors from real-LLM provider

The GLM-4-Flash free tier allows ~200 RPM. Reduce concurrency:
```bash
# Live rerun scripts are not included in this anonymous artifact package.
# Use your local live-rerun script wrapper and lower concurrency (e.g., CONCURRENCY=5).
```

### Windows: `bash: set: pipefail: invalid option`

Use WSL2 or Git Bash to run the `.sh` scripts. PowerShell does not support bash syntax directly.

---

## FAQ

**Q: Can I reproduce the paper results without any API key?**  
A: Yes. The artifact validation path (`go test` + `reproduce_main_paper_from_cache.sh`) requires no API key.

**Q: Where are the raw per-request logs?**  
A: Each `results/expN_*/` directory contains per-gateway subdirectories with `*.jsonl` or `*.csv` raw logs alongside the summary CSV.

**Q: Is there a single command to reproduce everything?**  
A: Run `bash scripts/reproduce_main_paper_from_cache.sh` (no API key required, < 5 min). This verifies all paper tables and regenerates all figures from frozen `artifact_cache/` data.

**Q: The beta ablation plots look slightly different from the paper.**  
A: The paper plots were generated with the full 5-repeat run. If you re-run with `run_beta_ablation.py`, variance across repeats may cause minor visual differences. The means and qualitative conclusions are stable.

**Q: How do I add a new gateway baseline?**  
A: Implement `BaselineGateway` interface (see `baseline/ng_gateway.go`) and register the mode flag in `cmd/gateway/main.go`. The live rerun automation scripts are not included in this anonymous artifact package.
