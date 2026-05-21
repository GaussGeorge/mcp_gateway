# Paper-Ready Results: Multi-Gateway Shared State Validation

## Main Research Question

Can PlanGate's session commitment semantics be preserved when multiple gateway instances share session state via distributed storage (Redis)?

**Answer:** Yes. Under random per-step routing with shared state, session continuation semantics are fully restored (state_miss → 0%) while maintaining legitimate cross-node session activity (19.7–22.8%).

---

## Table 1: Shared-State Semantics Preservation (Paper Main Table)

| Routing Mode | Concurrency | Success% | Rej0% | StateMiss (Cnt) | SemanticFail% | Cross-Node% | MaxActive | GP/s | P95 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Single-GW** | 20 | 17.6 | 82.4 | 0 | 0.0 | 0.0 | 17 | 5.96 | 2940.9 |
| (in-memory) | 40 | 20.1 | 79.9 | 0 | 0.0 | 0.0 | 20 | 5.85 | 3301.0 |
| | 60 | 18.6 | 81.4 | 0 | 0.0 | 0.0 | 20 | 5.79 | 3213.9 |
| **2xGW-NoShare** | 20 | 3.1 | 68.5 | 427 | 28.5 | 28.5 | 3 | 1.06 | 2291.7 |
| (local_random) | 40 | 4.8 | 66.1 | 436 | 29.1 | 29.1 | 4 | 1.53 | 2701.4 |
| | 60 | 3.9 | 66.1 | 451 | 30.1 | 30.1 | 3 | 1.25 | 1883.5 |
| **2xGW+Redis** | 20 | 26.1 | 73.8 | 0 | 0.0 | 22.8 | 20 | 6.52 | 4605.8 |
| (shared_random) | 40 | 22.1 | 77.3 | 0 | 0.0 | 19.7 | 31 | 6.51 | 5333.5 |
| | 60 | 22.0 | 77.3 | 0 | 0.0 | 20.1 | 31 | 6.10 | 5934.4 |

**N=5 repeats per configuration; results shown as mean across repeats.**

---

## Interpretation & Key Observations

### 1. Continuation Semantic Restoration

**Hypothesis:** Shared state via Redis preserves session continuation across gateway boundaries.

**Result:** ✓ Confirmed
- `2xGW+Redis` achieves **zero state misses** (StateMiss=0 across all C levels)
- Maintains **19.7–22.8% cross-node session rate**, showing legitimate multi-gateway activity
- Single-gateway baseline (`Single-GW`) achieves success rates in range [17.6%, 20.1%]
- `2xGW+Redis` success rates [22.0%, 26.1%] suggest improved admission capacity with two independent gateways sharing the same cap

### 2. Negative Control: Local-Memory Route Failure

**2xGW-NoShare (local_random)** demonstrates the cost of non-shared session state:
- **High state-miss rate:** 28.5–30.1% of sessions encounter continuation failures
- **Correlated with cross-node routing:** Cross-node% = SemanticFail% ≈ 28.5–30.1%, showing that cross-gateway hops in the absence of shared state break continuation
- **Admission collapse:** Success% drops to 3.1–4.8%, indicating most requests are rejected at step-0 when either gateway's in-memory capacity saturates
- **Throughput degradation:** GP/s drops to ~1.3x vs. single gateway (~5.8x)

**Conclusion:** Random routing without shared state is untenable for multi-gateway deployments.

### 3. Single-Gateway Baseline

`Single-GW` provides a performance floor for a single-process PlanGate instance:
- Success rates: 17.6–20.1%
- Throughput: 5.79–5.96 GP/s
- Cross-node rate: 0% (unavailable)

### 4. Shared-State Admission Capacity Gain

`2xGW+Redis` success rates (22.0–26.1%) exceed `Single-GW` (17.6–20.1%) despite similar per-gateway session caps (30 each). This suggests:
- Individual gateways can independently admit sessions without contention
- Shared state allows interleaved step execution without replay/conflict
- Effective admission throughput improves due to dual-gateway parallelism

---

## Detailed Metric Explanations

### Success%
Percentage of sessions completing all planned steps and claiming success state (across 300 sessions per run).

### Rej0%
Percentage of sessions rejected at step-0 (admission gate), measured as Step-0 rejections / total sessions.  
**Not** the primary semantic failure indicator (see StateMiss below).

### StateMiss (Count)
Total count of sessions across the 300-session run that encountered a state continuation miss event.
- Recorded when a session resumes execution on a different gateway and its prior state is unavailable.
- `2xGW-NoShare`: high counts (427–451) due to in-memory isolation.
- `2xGW+Redis` & `Single-GW`: zero counts (state always available).

### SemanticFail%
StateMiss count as a percentage of total sessions.
- **Key metric** for shared-state preservation claim.
- `2xGW-NoShare`: 28.5–30.1% (semantic break evident).
- `2xGW+Redis`: 0.0% (restored).

### Cross-Node%
Percentage of sessions that executed on multiple gateway instances (detected via gateway URL changes in step log).
- `Single-GW`: 0% (unavailable).
- `2xGW-NoShare`: 28.5–30.1% (random routing, no stickiness).
- `2xGW+Redis`: 19.7–22.8% (lower due to partial stickiness from queue dynamics, but still significant).

### MaxActive
Peak concurrent admitted sessions at any point during the run (across both gateways for dual-GW modes).

### GP/s (Effective Goodput)
Total successful steps / elapsed time.

### P95 (ms)
95th-percentile end-to-end latency for successful sessions.

---

## Callout: Why ABD% Is Not the Primary Failure Signal

**ABD% = (cascade_failed) / (admitted_sessions)** in traditional multi-step systems.

In this experiment, **ABD% is NOT appropriate** as the primary semantic failure indicator because:
1. Many sessions are rejected at Step-0, before admission (Rej0% ≈ 66–82%).
2. These rejections suppress ABD% near 0% even when cross-node routing breaks semantics.
3. **The semantic break manifests as state_miss**, not as admitted-session cascade.

**Correct metric hierarchy:**
1. **State-Miss%** — Primary: detects continuation failures (cross-node semantic break).
2. **Rej0%** — Secondary: reflects per-gateway capacity/pricing, not semantic integrity.
3. **Success%** — Tertiary: overall admission throughput (affected by price dynamics).

---

## Callout: local_sticky (Sticky Routing) Is Not an Equivalent Baseline

`local_sticky` achieves zero state misses and 0% cross-node rate by enforcing session stickiness to a single gateway. However:

1. **Not a shared-state design:** Each gateway maintains independent in-memory session state.
2. **Capacity limitation:** At C=40/60, one gateway becomes hot and the other cold, underutilizing aggregate capacity.
3. **Routing locality assumption:** Stickiness is fragile under failures or load balancing shifts.

**Correct comparison:**
- `local_sticky` proves that **routing locality avoids state misses** but relies on operational discipline.
- `2xGW+Redis` proves that **shared state avoids state misses** without routing constraints.
- Shared state is the more robust design.

---

## Recommended Main-Text Phrasing

### Claim
"PlanGate's session commitment semantics can be preserved beyond a single gateway process."

### Supporting Statement
"We validate this claim by deploying two PlanGate gateway instances sharing session state via Redis. Under per-step random routing without coordinated stickiness, sessions are allowed to migrate between gateways at each step. **Without shared state, 28.5–30.1% of sessions encounter continuation state misses.** With Redis-backed shared session state, state misses drop to **zero while 19.7–22.8% of sessions still cross gateway nodes**, demonstrating that PlanGate's commitment semantics can be preserved beyond a single gateway process."

### Methodological Note
"This validation is conducted with P&S (Plan-and-Solve) workloads. ReAct (step-by-step agents) with shared-state continuation is deferred to future work due to checkpoint/recovery complexity (see Appendix A)."

---

## LaTeX Table (Paper Format)

```latex
\begin{table}[t]
  \caption{Multi-Gateway Shared-State Validation: Semantics Preservation Under Random Routing.
  Single-GW is per-process baseline; 2xGW-NoShare demonstrates semantic break without shared state;
  2xGW+Redis restores semantics. N=5 repeats per configuration; results shown as mean.}
  \label{tab:multigateway-shared-state}
  \small
  \centering
  \begin{tabular}{@{}llrrrrrrrr@{}}
    \toprule
    \textbf{Routing Mode} & \textbf{C} & \textbf{Succ\%} & \textbf{Rej0\%} & \textbf{StateMiss} & \textbf{SemFail\%} & \textbf{Cross\%} & \textbf{MaxActive} & \textbf{GP/s} & \textbf{P95(ms)} \\
    \midrule
    \multirow{3}{*}{Single-GW} & 20 & 17.6 & 82.4 & 0 & 0.0 & 0.0 & 17 & 5.96 & 2940.9 \\
                                   & 40 & 20.1 & 79.9 & 0 & 0.0 & 0.0 & 20 & 5.85 & 3301.0 \\
                                   & 60 & 18.6 & 81.4 & 0 & 0.0 & 0.0 & 20 & 5.79 & 3213.9 \\
    \midrule
    \multirow{3}{*}{2xGW-NoShare} & 20 & 3.1 & 68.5 & 427 & 28.5 & 28.5 & 3 & 1.06 & 2291.7 \\
                                     & 40 & 4.8 & 66.1 & 436 & 29.1 & 29.1 & 4 & 1.53 & 2701.4 \\
                                     & 60 & 3.9 & 66.1 & 451 & 30.1 & 30.1 & 3 & 1.25 & 1883.5 \\
    \midrule
    \multirow{3}{*}{2xGW+Redis} & 20 & 26.1 & 73.8 & 0 & 0.0 & 22.8 & 20 & 6.52 & 4605.8 \\
                                   & 40 & 22.1 & 77.3 & 0 & 0.0 & 19.7 & 31 & 6.51 & 5333.5 \\
                                   & 60 & 22.0 & 77.3 & 0 & 0.0 & 20.1 & 31 & 6.10 & 5934.4 \\
    \bottomrule
  \end{tabular}
\end{table}
```

---

## Quick Reference: Column Definitions

| Column | Unit | Definition |
|--------|------|-----------|
| **Routing Mode** | (categorical) | Single-GW (baseline), 2xGW-NoShare (semantic break), 2xGW+Redis (target) |
| **C** | (concurrency) | Concurrent admitted sessions attempted |
| **Success%** | (percent) | Sessions reaching success state / total sessions |
| **Rej0%** | (percent) | Sessions rejected at step-0 / total sessions |
| **StateMiss (Cnt)** | (count) | Number of sessions with state continuation miss (out of 300) |
| **SemanticFail%** | (percent) | StateMiss count / total sessions |
| **Cross-Node%** | (percent) | Sessions executing on ≥2 gateways / total sessions |
| **MaxActive** | (count) | Peak concurrent admitted sessions at any point |
| **GP/s** | (goodput/sec) | Successful steps / elapsed time |
| **P95(ms)** | (milliseconds) | 95th-percentile end-to-end latency (successful sessions only) |

---

## Appendix: Why This Experiment Does NOT Cover ReAct + Shared State

**Scope:** P&S (Plan-and-Solve) only; ReAct continuation deferred.

**Rationale:**
- ReAct (step-by-step agents) interleave tool selection and execution conditionally.
- Shared-state checkpoint/recovery under ReAct requires consensus on the "last good step," failure detection, and replay semantics.
- This experiment focuses on basic shared-state semantics for Plan-Execute phases, not recovery.

**Future work:** ReAct + checkpoint + cross-gateway recovery (PlanGate-R) is a separate large task.

---

## Files & Reproducibility

**Main data:**
- [results/exp_multigateway_shared_state_full/multigateway_summary_computed.csv](../results/exp_multigateway_shared_state_full/multigateway_summary_computed.csv)

**Raw run data (60 files):**
- `results/exp_multigateway_shared_state_full/{single,local_random,local_sticky,shared_random}/C{20,40,60}/run{1,2,3,4,5}/{steps.csv,steps_sessions.csv}`

**Runner & aggregator:**
- [scripts/run_multigateway_shared_state.py](../scripts/run_multigateway_shared_state.py)
- [scripts/_compute_multigateway_shared_state_stats.py](../scripts/_compute_multigateway_shared_state_stats.py)

**Experiment notes:**
- [docs/multigateway_shared_state_notes.md](../docs/multigateway_shared_state_notes.md)

---

**Document:** 2026-05-21  
**Status:** ✓ Ready for paper publication  
**Claim:** PlanGate shared-state semantics validated; multi-gateway random routing safe with Redis backend.
