# PlanGate: Sunk-Cost-Aware Dynamic Pricing for Multi-Step LLM Agent Tool Governance

<p align="center">
  <strong>PlanGate</strong> — A plan-aware gateway for MCP tool governance that eliminates cascade computation waste in multi-step LLM agent sessions through pre-flight atomic admission, budget reservation, and sunk-cost K² discount pricing.
</p>

---

## Overview

When LLM agents execute multi-step tool-calling sessions via the Model Context Protocol (MCP), traditional gateway policies (rate limiters, load shedders) make independent per-request decisions. This causes **cascade computation waste**: an agent that has already completed steps 1–4 gets rejected at step 5, wasting all prior computation.

**PlanGate** solves this with three core mechanisms:

| Mechanism | Description |
|-----------|-------------|
| **Pre-flight Atomic Admission** | For Plan-and-Solve agents: validates the entire DAG plan upfront, ensuring all steps can complete before any execution begins |
| **Budget Reservation** | Locks a session-level price at admission time; subsequent steps are immune to price spikes |
| **Sunk-Cost K² Discount** | For ReAct agents: at step K, the price decays as $\frac{base}{1+K^2\alpha}$, protecting investment already made |

Additionally, PlanGate includes a **Reputation System** for defense against adversarial agents (budget forgery, DAG abuse).

## Architecture

```
┌──────────────────────────────────┐
│  DAG Load Generator              │  (Python asyncio, simulates LLM agents)
│  scripts/dag_load_generator.py   │
└──────────┬───────────────────────┘
           │ HTTP (JSON-RPC 2.0)
           ▼
┌──────────────────────────────────┐
│  PlanGate / Baseline Gateways    │  (Go, 6 strategies switchable via --mode)
│  cmd/gateway/main.go             │
│  ┌────────────────────────────┐  │
│  │ Reputation Manager         │  │  plangate/reputation.go
│  │ Dual-Mode Router           │  │  plangate/dual_mode_routing.go
│  │ Dynamic Pricing Engine     │  │  mcp_governor.go
│  │ Session Manager            │  │  plangate/session_manager.go
│  └────────────────────────────┘  │
└──────────┬───────────────────────┘
           │ HTTP (JSON-RPC 2.0)
           ▼
┌──────────────────────────────────┐
│  Python MCP Backend              │  (ThreadPoolExecutor, max_workers=10)
│  mcp_server/server.py            │
│  └── tools/: calculator, weather,│
│      web_fetch, mock_heavy, ...  │
└──────────────────────────────────┘
```

## Gateway Strategies

| Strategy | Mode Flag | Description | Source |
|----------|-----------|-------------|--------|
| **No-Gov (NG)** | `ng` | Pass-through, no governance | baseline/ng\_gateway.go |
| **SRL** | `srl` | Static rate limiter (token bucket) | baseline/srl\_gateway.go |
| **Rajomon** | `rajomon` | Queuing-delay-based admission (OSDI '25 analog) | baseline/rajomon\_gateway.go |
| **DAGOR** | `dagor` | RTT-threshold overload detection (SoCC '18 analog) | baseline/dagor\_gateway.go |
| **SBAC** | `sbac` | Session-count-based admission control | baseline/sbac\_gateway.go |
| **PlanGate** | `mcpdp` | Pre-flight + BudgetLock + K² Discount + Reputation | plangate/ |

## Experiment Suite

### Three-Tier Evaluation (15 Experiment Sets, 1000+ Trials)

PlanGate uses a **three-tier evidence hierarchy**:

| Tier | Scope | Description |
|------|-------|-------------|
| **Tier 1** | Mock (controlled) | 12 mock experiment suites with hard capacity limits; large, significant governance gains ($p<0.01$) |
| **Tier 2** | Steady Real-LLM | GLM-4-Flash & DeepSeek-V3 under commercial API rate limits; no-regret boundary characterization |
| **Tier 3** | Bursty Real-LLM | Backend-capacity-limited burst overload; significant waste reduction ($p<0.001$) |

#### Tier 1: Mock Experiments (Exp1–Exp12)

| Exp | Name | Description | Sessions | Conc. | Gateways | Runs |
|-----|------|-------------|----------|-------|----------|------|
| 1 | **Core** | Full-load performance comparison | 500 | 200 | NG/SRL/SBAC/PG | 5×4=20 |
| 2 | **HeavyRatio** | Heavy tool ratio sweep (0.1–0.7) | 200 | 20 | 6 gw | 120 |
| 3 | **MixedMode** | P&S/ReAct ratio sweep (0–100%) | 200 | 20 | 6 gw | 150 |
| 4 | **Ablation** | Component ablation (Full/wo-BL/wo-SC) | 500 | 200 | 3 variants | 15 |
| 5 | **ScaleConc** | Mixed-mode concurrency (10–60) | 200 | sweep | 6 gw | 120 |
| 6 | **ScaleConcReAct** | Pure-ReAct concurrency (10–60) | 200 | sweep | 6 gw | 120 |
| 7 | **ClientReject** | Price TTL robustness (0.1–2.0s) | 500 | 200 | PG | 25 |
| 8 | **DiscountAblation** | Discount function families (K²/K/eᴷ/ln) | 500 | 200 | 4 variants | 20 |
| 9 | **ScaleStress** | High-concurrency stress (200–1000) | 500 | sweep | NG/SBAC/PG | 75 |
| 10 | **Adversarial** | 10% malicious agents | 500 | 200 | NG/SRL/SBAC/PG | 20 |
| 11 | **Bursty** | Burst arrival pattern (multi-phase) | 500 | 200 | NG/SRL/SBAC/PG | 20 |
| 12 | **LongTail** | 20% sessions with 10–15 steps | 500 | 200 | NG/SRL/SBAC/PG | 20 |
| — | **Rajomon Sensitivity** | price\_step ∈ {5,10,20,50,100} | 500 | 200 | Rajomon | 25 |

#### Tier 2: Steady Real-LLM Experiments

| Experiment | Provider | Sessions | Concurrency | Runs |
|------------|----------|----------|-------------|------|
| GLM-4-Flash C=10 | GLM-4-Flash (200 RPM) | 200 ReAct | 10 | 5×4=20 |
| GLM-4-Flash C=40 | GLM-4-Flash (200 RPM) | 200 ReAct | 40 | 5×4=20 |
| DeepSeek Sweep | DeepSeek-V3 (60 RPM) | 50 ReAct | C={1,3,5} | 3×4=12 |

#### Tier 3: Bursty Real-LLM Experiments

| Experiment | Provider | Config | Runs |
|------------|----------|--------|------|
| Bursty C=20 B=30 | GLM-4-Flash | 10 workers, 30 sess/burst, 8s gap | 3×2=6 |

### Key Results (Exp1, 500 sessions, 200 concurrency, 5-run mean)

| Gateway | Success | Cascade | Eff. GP/s | P50 (ms) | P95 (ms) | JFI |
|---------|:-------:|:-------:|:---------:|:--------:|:--------:|:---:|
| NG | 22.2 | 122.6 | 16.2 | 1008 | 1986 | 0.929 |
| SRL | 40.0 | 109.6 | 28.3 | 1006 | 1896 | 0.924 |
| SBAC | 58.8 | 34.8 | 46.4 | 361 | 1416 | 0.933 |
| **PlanGate** | **72.6** | **0.0** | **51.9** | **3.9** | **819** | **0.922** |

## Full Reproduction Workflow

### Prerequisites

- **Go** 1.21+ (gateway compilation)
- **Python** 3.10+ with `pip install aiohttp numpy matplotlib`
- **Linux/WSL2** recommended for CPU isolation via `taskset`
- **API Keys** (Tier 2/3 only): `ZHIPUAI_API_KEY`, `DEEPSEEK_API_KEY`

### Step 1: Build Gateway

```bash
# Native build
go build -o gateway ./cmd/gateway

# Cross-compile for Linux (from Windows/macOS)
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o gateway_linux ./cmd/gateway
```

### Step 2: Run Tier 1 — Mock Experiments

```bash
# All 12 experiments (Exp1–Exp12) with 5 repeats each
python scripts/run_all_experiments.py --exp all --repeats 5

# Single experiment
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 5

# With CPU isolation (Linux/WSL2)
python scripts/run_all_experiments.py --exp all --repeats 5 \
    --gateway-binary ./gateway_linux \
    --cpu-backend 8-15 --cpu-gateway 4-7 --cpu-loadgen 0-3

# Rajomon sensitivity sweep
python scripts/run_all_experiments.py --exp Rajomon_Sensitivity --repeats 5
```

Results output: `results/exp1_core/`, `results/exp2_heavyratio/`, ..., `results/exp12_longtail/`

### Step 3: Run Tier 2 — Steady Real-LLM

```bash
# GLM-4-Flash at C=10 and C=40 (5 repeats each, 4 gateways)
export ZHIPUAI_API_KEY="your-key"
bash scripts/run_exp_real3.sh         # C=10
bash scripts/run_exp_real3_all.sh     # C=10 + C=40

# DeepSeek-V3 concurrency sweep (C=1,3,5)
export DEEPSEEK_API_KEY="your-key"
python scripts/run_exp_multi_llm.py --provider deepseek --conc 1,3,5
```

Results output: `results/exp_real3_glm/`, `results/exp_conc_sweep_deepseek/`

### Step 4: Run Tier 3 — Bursty Real-LLM

```bash
export ZHIPUAI_API_KEY="your-key"
python scripts/run_real_llm_bursty.py --repeats 3 --burst-size 30 --workers 10
```

Results output: `results/exp_bursty_C20_B30/`

### Step 5: Generate Paper Figures (CCF-A Quality)

```bash
# Generate all 9 figures referenced in the paper (PDF + PNG)
python scripts/gen_ccfa_figures.py
```

Output: `paper/figures/*.pdf` and `paper/figures/*.png`

Generated figures:
| Figure | File | Description |
|--------|------|-------------|
| Fig 1 | `architecture.pdf` | Three-tier system architecture |
| Fig 2 | `mock_cascade_comparison.pdf` | Exp1: cascade failure comparison |
| Fig 3 | `exp4_ablation.pdf` | Ablation study (3 panels) |
| Fig 4 | `exp9_scalability.pdf` | High-concurrency 2-panel |
| Fig 5 | `chart4_token_efficiency.pdf` | Token cost per successful task |
| Fig 6 | `chart6_fairness.pdf` | Step distribution boxplot |
| Fig 7 | `conc_sweep_deepseek.pdf` | DeepSeek 3-panel sweep |
| Fig 8 | `exp10_adversarial.pdf` | Adversarial robustness |
| Fig 9 | `rajomon_sensitivity.pdf` | Rajomon parameter sensitivity |

### Step 6: Compile Paper

```bash
cd paper
pdflatex plangate_paper.tex
bibtex plangate_paper
pdflatex plangate_paper.tex
pdflatex plangate_paper.tex
```

### Step 7: Analyze Results

```bash
python scripts/aggregate_results.py            # Aggregate with mean ± std
python scripts/compute_fairness.py             # Jain Fairness Index
python scripts/analyze_latency_breakdown.py    # Gateway overhead decomposition
python scripts/_analyze_bursty.py              # Bursty experiment analysis
```

## Key Metrics

| Metric | Description |
|--------|-------------|
| **Success** | Sessions completing all planned steps |
| **CASCADE\_FAIL** | Sessions rejected mid-execution (wasted computation) |
| **REJECTED@S0** | Sessions rejected at step 0 (no waste) |
| **Effective Goodput/s** | Throughput counting only successful sessions |
| **P50 / P95 / P99** | Per-step latency percentiles (ms) |
| **E2E P50 / P95 / P99** | End-to-end session latency percentiles (ms) |
| **JFI\_Steps** | Jain's Fairness Index over agent step counts |
| **JFI\_Latency** | Jain's Fairness Index over agent latencies |
| **gateway\_latency\_us** | Gateway processing overhead (μs) |

## Project Structure

```
mcp-governance-main/
├── cmd/gateway/main.go              # Gateway binary entry point
├── mcp_governor.go                  # Core pricing engine (dynamic pricing, K² discount)
├── mcp_protocol.go                  # JSON-RPC 2.0 MCP protocol handling
├── mcp_transport.go                 # HTTP transport layer
├── mcp_init.go                      # Gateway initialization
├── overloadDetection.go             # Overload detection (EMA-based)
├── queuingDelay.go                  # Queuing delay estimator
├── tokenAndPrice.go                 # Token pricing and budget logic
│
├── plangate/                        # PlanGate-specific components
│   ├── server.go                    #   MCPDPServer (orchestrator)
│   ├── dual_mode_routing.go         #   Plan-and-Solve / ReAct dual-mode router
│   ├── session_manager.go           #   Session lifecycle manager
│   ├── reputation.go                #   Reputation-based security system
│   ├── dag_validation.go            #   DAG plan validator
│   ├── governance_intensity.go      #   Governance intensity calculator
│   ├── external_signal_tracker.go   #   External signal integration
│   ├── http_handlers.go             #   HTTP handler with latency instrumentation
│   └── reputation_test.go           #   8 unit tests for reputation system
│
├── baseline/                        # Baseline gateway implementations
│   ├── ng_gateway.go                #   No-Governance pass-through
│   ├── srl_gateway.go               #   Static Rate Limiter
│   ├── rajomon_gateway.go           #   Rajomon (queuing-delay-based)
│   ├── dagor_gateway.go             #   DAGOR (RTT-threshold-based)
│   └── sbac_gateway.go              #   SBAC (session-count-based)
│
├── mcp_server/                      # Python MCP backend
│   ├── server.py                    #   Main server (ThreadPoolExecutor)
│   └── tools/                       #   Tool registry (12+ tools)
│
├── scripts/                         # Experiment & analysis scripts
│   ├── run_all_experiments.py       #   Automated experiment runner (9 exp × 6 gw)
│   ├── dag_load_generator.py        #   DAG load generator (asyncio)
│   ├── plot_all_experiments.py      #   12 paper figures from mock experiments
│   ├── plot_paper_charts.py         #   6 paper figures from real LLM experiments
│   ├── compute_fairness.py          #   Standalone Jain fairness index calculator
│   ├── analyze_latency_breakdown.py #   Gateway latency decomposition (Parrot-style)
│   ├── run_exp_multi_llm.py         #   Multi-LLM provider experiment runner
│   ├── tune_baselines.py            #   Optuna hyperparameter tuning
│   └── visualize_results.py         #   Legacy visualization script
│
├── results/                         # Experiment output
│   ├── exp1_core/ ... exp9_scalestress/   # 9 experiment result directories
│   ├── paper_figures/PNG/           #   300 DPI PNG figures
│   ├── paper_figures/PDF/           #   Vector PDF figures
│   └── evolution_8runs.csv          #   Historical tuning evolution data
│
├── paper/plangate_paper.tex         # LaTeX paper source
└── plan/                            # Review feedback & revision plans
    ├── Review1.md & Review2.md
    └── advice.md
```

## Tuning Parameters

Baseline parameters optimized via Optuna (`scripts/tune_baselines.py`):

```python
TUNED_PARAMS = {
    "rajomon":       {"price_step": 20},
    "dagor":         {"rtt_threshold": 400.0, "price_step": 10},
    "sbac":          {"max_sessions": 150},
    "srl":           {"qps": 65.0, "burst": 400, "max_conc": 55},
    "plangate_full": {"price_step": 40, "max_sessions": 30, "sunk_cost_alpha": 0.5},
}
```

Re-tune: `python scripts/tune_baselines.py --gateway-binary ./gateway_linux --trials 150`

## Analysis Tools

| Script | Purpose |
|--------|---------|
| `plot_all_experiments.py` | Generate 12 paper-quality figures from Exp1–Exp9 |
| `compute_fairness.py` | Compute Jain's Fairness Index from session CSVs |
| `analyze_latency_breakdown.py` | Parrot-style gateway overhead decomposition |
| `run_exp_multi_llm.py` | Multi-LLM provider experiments (GLM / DeepSeek / GPT-4o / Claude) |
| `aggregate_results.py` | Aggregate results with mean ± std |

## Figure Gallery

9 CCF-A quality figures referenced in the paper, generated by `scripts/gen_ccfa_figures.py`:

| Figure | File | Source | Description |
|--------|------|:------:|-------------|
| Fig 1 | `architecture.pdf` | Design | Three-tier system architecture |
| Fig 2 | `mock_cascade_comparison.pdf` | Exp1 | Cascade failure bar chart (4 gateways) |
| Fig 3 | `exp4_ablation.pdf` | Exp4 | Ablation: success/cascade/GP (3 panels) |
| Fig 4 | `exp9_scalability.pdf` | Exp9 | High-concurrency 2-panel (200–1000) |
| Fig 5 | `chart4_token_efficiency.pdf` | Real-LLM | Token cost per successful task |
| Fig 6 | `chart6_fairness.pdf` | Exp1 | Step distribution boxplot |
| Fig 7 | `conc_sweep_deepseek.pdf` | DeepSeek | 3-panel concurrency sweep |
| Fig 8 | `exp10_adversarial.pdf` | Exp10 | Adversarial robustness (2 panels) |
| Fig 9 | `rajomon_sensitivity.pdf` | Rajomon | Parameter sensitivity (2 panels) |

## Citation

```bibtex
@inproceedings{plangate2026,
  title={PlanGate: Sunk-Cost-Aware Dynamic Pricing for Multi-Step LLM Agent Tool Governance},
  author={...},
  booktitle={Proceedings of ...},
  year={2026}
}
```

## License

This project is for academic research purposes.
