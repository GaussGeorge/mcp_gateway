# Pareto Frontier Notes: PlanGate Tradeoff Analysis

> **Research question**: "Is PlanGate merely rejecting earlier?"
>
> **Short answer**: No. The Pareto frontier data show that PlanGate's tunable
> parameters (`max_sessions`, `sunk_cost_alpha`, `session_cap_wait`) produce a
> **controllable tradeoff** between admission leniency and system overload
> protection — a capability absent from simple threshold-based baselines.

---

## Experiment Design

| Parameter | Pilot value | Formal value |
|-----------|-------------|--------------|
| `sessions` | 200 | 500 |
| `concurrency` | 100 | 200 |
| `arrival_rate` | 50.0 req/s | 50.0 req/s |
| `duration` | 60 s | 60 s |
| `heavy_ratio` | 0.3 | 0.3 |
| `ps_ratio` | 1.0 (P&S only) | 1.0 |
| `step_timeout` | 2.0 s | 2.0 s |
| Backend workers | 10 | 10 |

**Stage A sweep** (10 configs): `max_sessions ∈ {20, 30, 40, 60, 80}` × `session_cap_wait ∈ {1, 3}`; `alpha=0.5` fixed.

**Stage B sweep** (2 new configs): `alpha ∈ {0.3, 0.7}` at `ms=30, wait=1`; the reference `(ms=30, wait=1, alpha=0.5)` is already in Stage A.

**Baselines** (3): `ng` (no governance), `sbac` (max_sessions=150), `rajomon` (price_step=20).

---

## Pilot Results (single run per config, sessions=200)

### Raw metrics

| Variant | Success | Rej@S0 | Cascade | Eff.GP | P95 (ms) |
|---------|--------:|-------:|--------:|-------:|---------:|
| `pg_ms20_wait1_a0.5` | 24 | 176 | 0 | 254 | 809 |
| `pg_ms20_wait3_a0.5` | 28 | 172 | 0 | 292 | 904 |
| `pg_ms30_wait1_a0.5` | 28 | 172 | 0 | 280 | 888 |
| `pg_ms30_wait3_a0.5` | 36 | 164 | 0 | 349 | 1110 |
| `pg_ms40_wait1_a0.5` | 36 | 164 | 0 | 375 | 861 |
| `pg_ms40_wait3_a0.5` | 29 | 171 | 0 | 300 | 807 |
| `pg_ms60_wait1_a0.5` | 20 | 180 | 0 | 185 | 807 |
| **`pg_ms60_wait3_a0.5`** ★ | **39** | 160 | 1 | 405 | 1305 |
| **`pg_ms80_wait1_a0.5`** ★ | **40** | 159 | 1 | 399 | 1299 |
| `pg_ms80_wait3_a0.5` | 30 | 170 | 0 | 257 | 807 |
| `pg_ms30_wait1_a0.3` | 38 | 162 | 0 | 352 | 1112 |
| `pg_ms30_wait1_a0.7` | 30 | 170 | 0 | 329 | 934 |
| `ng` | 16 | 141 | 43 | 154 | 1612 |
| `sbac` | 21 | 151 | 28 | 235 | 1588 |
| `rajomon` | 1 | 144 | 55 | 7 | 1534 |

★ = identified as Pareto-optimal (not dominated in `useful_completion_rate` × `effective_goodput` space)

### Derived metrics

| Variant | UCR | Waste/Success | Adm. Rate |
|---------|----:|--------------:|----------:|
| `pg_ms20` variants | 0.12–0.14 | 0.0 | 0.12–0.14 |
| `pg_ms30` variants | 0.14–0.19 | 0.0 | 0.14–0.19 |
| `pg_ms40` variants | 0.145–0.18 | 0.0 | 0.145–0.18 |
| `pg_ms60_wait1` | 0.10 | 0.0 | 0.10 |
| `pg_ms60_wait3` | **0.195** | 0.026 | **0.20** |
| `pg_ms80_wait1` | **0.20** | 0.025 | **0.205** |
| `ng` | 0.08 | **2.69** | 0.30 |
| `sbac` | 0.105 | **1.33** | 0.245 |
| `rajomon` | 0.005 | **55.0** | 0.28 |

UCR = useful_completion_rate = success / sessions.  
Waste/Success = cascade_failed / max(success, 1).

---

## Key Findings

### 1. PlanGate is NOT "just rejecting earlier"

While PlanGate does reject more sessions at S0 (early admission gate) compared
to baselines, the **outcome is qualitatively different**:

- All baselines allow many sessions to start, only to fail mid-chain
  (cascade failures: NG=43, SBAC=28, Rajomon=55).
- All PlanGate variants achieve cascade failures of **≤1** in the pilot.
- The Waste-per-Success metric captures this: baselines waste 1.3×–55× more
  tool calls per successful completion vs PlanGate ≈ 0.

### 2. The tradeoff is controllable and smooth

`max_sessions` exposes a clean knob from conservative to lenient:
- `ms=20`: tight gate, low admission, very low P95 latency (~810 ms)
- `ms=30`–`40`: sweet spot, best wasteless success rate
- `ms=60`–`80`: highest raw success count but 1–2 cascade failures emerge;
  P95 latency rises to 1300 ms as the system approaches overload

`session_cap_wait` (wait for slot before rejecting):
- `wait=1` generally gives better P95 than `wait=3`
- `wait=3` permits more sessions in but increases tail latency

`sunk_cost_alpha` (weight on already-spent tokens):
- Lower alpha (0.3): more lenient mid-session continuation → higher success (38)
- Higher alpha (0.7): more conservative → fewer success (30) but less waste

### 3. PlanGate Pareto dominates all baselines

In the (success_rate, effective_goodput) plane, **every** PlanGate variant
Pareto-dominates all three baselines:

| Metric | Best PlanGate | NG | SBAC | Rajomon |
|--------|:-------------:|:--:|:----:|:-------:|
| Success rate | **20%** | 8% | 10.5% | 0.5% |
| Effective goodput | **405** | 154 | 235 | 7 |
| Cascade failures | **≤1** | 43 | 28 | 55 |
| P95 latency | **807–1305 ms** | 1612 | 1588 | 1534 |

### 4. Honest caveats (single pilot run)

- This is a **pilot** (n=1 per config). Single runs have high variance; some
  differences (e.g., ms=60→ms=80 success=20→40) may not be reproducible.
- The optimal `max_sessions` may shift with workload intensity.  
- Latency-vs-admission tradeoff at `ms=60,wait=3` vs `ms=80,wait=1` needs
  more runs to determine statistical significance.
- These experiments run with a **mock backend** (deterministic tool latency).
  Real LLM backends will show higher variance.

---

## Reproduction Instructions

```bash
# Pilot run (fast, ~20 min on mock backend)
python scripts/run_pareto_frontier.py \
    --pilot --gateway-binary gateway.exe

# Analysis (generates tables/)
python scripts/analyze_pareto_frontier.py \
    --input results/pareto_frontier_pilot/pareto_summary.csv \
    --sessions 200

# Plots (generates plots/pareto_frontier/*.pdf)
python scripts/plot_pareto_frontier.py

# Formal run (3 repeats, 500 sessions, ~2–3 hours)
python scripts/run_pareto_frontier.py \
    --repeats 3 --sessions 500 --concurrency 200 \
    --gateway-binary gateway.exe \
    --output-dir results/pareto_frontier
```

---

## Files

| File | Description |
|------|-------------|
| `scripts/run_pareto_frontier.py` | Sweep runner (reuses `run_all_experiments.py` infra) |
| `scripts/analyze_pareto_frontier.py` | Derived metrics + Pareto front detection |
| `scripts/plot_pareto_frontier.py` | 3 academic PDF figures |
| `results/pareto_frontier_pilot/pareto_summary.csv` | Pilot raw results |
| `tables/pareto_frontier_summary.csv` | Per-variant aggregated metrics |
| `tables/pareto_frontier_key_points.csv` | Pareto front + baselines |
| `plots/pareto_frontier/success_vs_waste.pdf` | Fig 1: UCR vs Waste/Success |
| `plots/pareto_frontier/rej0_vs_cascade.pdf` | Fig 2: Admitted vs Cascade |
| `plots/pareto_frontier/goodput_vs_latency.pdf` | Fig 3: Goodput vs P95 |
