# Reproducibility Guide — PlanGate

This document covers prerequisites, expected runtimes, common issues, and FAQ for reproducing all results in the PlanGate paper.

For a quick mapping of paper items to data files and commands, see [RESULT_MAPPING.md](RESULT_MAPPING.md).  
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

### Required for Level 2 (Mock Re-Run Only)

- The mock MCP backend (`mcp_server/server.py`) runs entirely locally; no Internet access needed.
- Linux or WSL2 is recommended for CPU isolation (`taskset`). On Windows, mock experiments still work but may show higher variance.

### Required for Level 3 (Live Real-LLM Only)

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
| L1 | `reproduce_real_llm_from_cache.sh` | < 2 min | no |
| L1 | `reproduce_appendix_from_cache.sh` | < 2 min | no |
| L2a | `reproduce_mock_core.sh` | ~30–45 min | no |
| L2b | `reproduce_sensitivity.sh` | ~30–45 min | no |
| L3 | `reproduce_real_llm_live.sh` | variable | yes (GLM) |

Timings are on a 16-core Linux machine. Windows may be 1.5–2× slower for mock runs due to process spawning overhead.

---

## Quick Start (Linux / WSL2)

```bash
git clone <repo>
cd mcp-governance-main
pip install -r mcp_server/requirements.txt
go build -o gateway ./cmd/gateway

# Level 1: regenerate all tables/figures from cached data
bash scripts/reproduce_main_paper_from_cache.sh
bash scripts/reproduce_real_llm_from_cache.sh
bash scripts/reproduce_appendix_from_cache.sh
```

## Quick Start (Windows PowerShell)

The shell scripts require bash (WSL2 or Git Bash). To use them on Windows:

```powershell
# Option A: WSL2
wsl bash scripts/reproduce_main_paper_from_cache.sh

# Option B: Git Bash
"C:\Program Files\Git\bin\bash.exe" scripts/reproduce_main_paper_from_cache.sh
```

For the Level 1 Python steps specifically, you can run them directly in PowerShell:

```powershell
# Verify key cached CSVs are present
Test-Path results\exp1_core\exp1_core_summary.csv
Test-Path results\exp4_ablation\exp4_ablation_summary.csv
Test-Path results\beta_ablation\beta_summary.csv
Test-Path results\exp_week5_C10\week5_summary.csv
Test-Path results\exp_week5_C40\week5_summary.csv
Test-Path results\exp_bursty_C20_B30\bursty_summary.csv

# Copy commitment quality table (no Python needed)
Copy-Item results\paper_figures\table_commitment_quality.tex `
    tables\table_commitment_quality_submission.tex
```

---

## Cached Data Inventory

The following summary CSVs are pre-committed and sufficient to regenerate all paper tables and figures without re-running any experiments:

| File | Paper Section |
|------|--------------|
| `results/exp1_core/exp1_core_summary.csv` | §5.1 Core results (Table 3) |
| `results/exp4_ablation/exp4_ablation_summary.csv` | §5.2 Ablation (Table 4) |
| `results/exp8_discountablation/exp8_discountablation_summary.csv` | §5.3 Discount ablation |
| `results/exp_rajomon_sensitivity/{ps5..ps100}/` | §5.3 Rajomon sensitivity |
| `results/exp_alpha_sweep/alpha_sweep_summary.csv` | §5.3 Alpha sensitivity |
| `results/beta_ablation/beta_summary.csv` | Appendix: Beta sensitivity |
| `results/exp_week5_C10/week5_summary.csv` | §5.4 Steady GLM C=10 |
| `results/exp_week5_C40/week5_summary.csv` | §5.4 Steady GLM C=40 |
| `results/exp_bursty_C20_B30/bursty_summary.csv` | §5.4 Bursty GLM |
| `results/exp_selfhosted_vllm_C20_W8/selfhosted_c20_summary.csv` | §5.4 vLLM C=20/W=8 |
| `results/exp10_adversarial/exp10_adversarial_summary.csv` | §5.5 Adversarial |
| `results/paper_figures/table_commitment_quality.tex` | §5.1 Table 2 (pre-built LaTeX) |

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

The cached CSV is missing. Either:
1. Re-run the corresponding Level 2 script (`reproduce_mock_core.sh` or `reproduce_sensitivity.sh`)
2. Check `ARTIFACT_SCOPE.md` — some experiments were diagnostic and are not committed

### Real-LLM script exits with "LLM_API_KEY is not set"

Create a `.env` file in the repo root (see Prerequisites above). Do NOT commit it. The `.gitignore` already excludes `.env`.

### Rate-limit errors from real-LLM provider

The GLM-4-Flash free tier allows ~200 RPM. Reduce concurrency:
```bash
CONCURRENCY=5 bash scripts/reproduce_real_llm_live.sh
```

### Windows: `bash: set: pipefail: invalid option`

Use WSL2 or Git Bash to run the `.sh` scripts. PowerShell does not support bash syntax directly.

---

## FAQ

**Q: Can I reproduce the paper results without any API key?**  
A: Yes. Level 0 and Level 1 require no API key. Level 2 also requires no API key (mock backend). Only Level 3 requires real LLM credentials.

**Q: Where are the raw per-request logs?**  
A: Each `results/expN_*/` directory contains per-gateway subdirectories with `*.jsonl` or `*.csv` raw logs alongside the summary CSV.

**Q: Is there a single command to reproduce everything?**  
A: Run `bash scripts/reproduce_main_paper_from_cache.sh` for mock results and `bash scripts/reproduce_real_llm_from_cache.sh` for real-LLM tables. Both complete in under 15 minutes total.

**Q: The beta ablation plots look slightly different from the paper.**  
A: The paper plots were generated with the full 5-repeat run. If you re-run with `run_beta_ablation.py`, variance across repeats may cause minor visual differences. The means and qualitative conclusions are stable.

**Q: How do I add a new gateway baseline?**  
A: Implement `BaselineGateway` interface (see `baseline/ng_gateway.go`), register the mode flag in `cmd/gateway/main.go`, then add the gateway name to the `GATEWAYS` list in `scripts/run_all_experiments.py`.
