# Multi-Gateway Shared-State Experiment: Final Delivery

**Date:** 2026-05-21  
**Status:** ✓ Complete (full N=5 run, results finalized, paper-ready outputs)

---

## Delivery Checklist

### 1. ✓ Experiment Execution
- [x] Full matrix run: 4 routing modes × 3 concurrency levels × 5 repeats = 60 runs
- [x] Results directory isolation: quick (archived) + full (active)
- [x] Artifact completeness: 60 steps.csv + 60 steps_sessions.csv + aggregates
- [x] Redis leak validation: pg:active_sessions=0, no residual pg:admit:*/pg:res:* keys
- [x] Scoped Go test pass: Shared*, SessionState*, Budget*, HTTP* suite ✓

### 2. ✓ Data Aggregation & Metrics
- [x] Runner script supports --results-dir override
- [x] Aggregator adds new metrics: rejected_s0_rate, semantic_failure_pct
- [x] CSV outputs: multigateway_agg.csv + multigateway_summary_computed.csv
- [x] Metric semantics clarified: StateMiss (absolute count) vs. SemanticFail% (rate)

### 3. ✓ Paper-Ready Outputs
- [x] Main table (Table 1): single + local_random + shared_random only
- [x] Markdown + LaTeX formats
- [x] Interpretation & key observations
- [x] Claim statement: "Shared-state semantics preserved beyond single-GW process"
- [x] Recommended main-text phrasing

### 4. ✓ Audit Documentation
- [x] Experiment configuration (P&S-only, sessions=300, C={20,40,60}, repeats=5)
- [x] Results directory layout
- [x] Artifact inventory
- [x] Redis validation protocol & results
- [x] Go test status (scoped pass, full test failure out-of-scope attribution)
- [x] Critical design notes (ABD% vs. StateMiss, local_sticky caveat)

### 5. ✓ Code Attribution
- [x] Runner modifications documented (--results-dir, results_dir parameter flow)
- [x] Aggregator modifications documented (new metric fields & UI)
- [x] No core PlanGate changes required (validation-only scope)

---

## Output Files & Locations

### Paper-Facing Deliverables

| File | Purpose | Format |
|------|---------|--------|
| [results/exp_multigateway_shared_state_full/PAPER_TABLE.md](../results/exp_multigateway_shared_state_full/PAPER_TABLE.md) | **Main table + claim statement + interpretation** | Markdown + LaTeX |
| [docs/multigateway_shared_state_notes.md](../docs/multigateway_shared_state_notes.md) | **Experiment audit & reproducibility guide** | Markdown |

### Raw Data & Computation

| Location | Contents | Format |
|----------|----------|--------|
| [results/exp_multigateway_shared_state_full/multigateway_summary_computed.csv](../results/exp_multigateway_shared_state_full/multigateway_summary_computed.csv) | Aggregated stats (mean ± std per mode/concurrency); **primary data source** | CSV |
| [results/exp_multigateway_shared_state_full/multigateway_agg.csv](../results/exp_multigateway_shared_state_full/multigateway_agg.csv) | Per-run raw statistics (60 rows) | CSV |
| `results/exp_multigateway_shared_state_full/{mode}/C{conc}/run{n}/steps.csv` | Step-level traces; 60 files total of `sessions × steps` | CSV |
| `results/exp_multigateway_shared_state_full/{mode}/C{conc}/run{n}/steps_sessions.csv` | Session aggregates; 60 files total | CSV |

### Code Changes (Minimal Scope)

| File | Changes | Lines |
|------|---------|-------|
| [scripts/run_multigateway_shared_state.py](../scripts/run_multigateway_shared_state.py) | Added --results-dir CLI arg + results_dir parameter flow | 41, 495–499, 551, 662–663, 676–685 |
| [scripts/_compute_multigateway_shared_state_stats.py](../scripts/_compute_multigateway_shared_state_stats.py) | Added rejected_s0_rate + semantic_failure_pct metrics; updated aggregation & table printing | 52–53, 200–201, 230–234, 260, 268, 295, 411–413 |

**No changes to:**
- PlanGate core (gateway.go, plangate.go, commitment engine)
- Redis integration (already in place)
- Backend (server.py)
- Go test suite

---

## Main Claim & Evidence

### Claim
**"PlanGate's session commitment semantics can be preserved beyond a single gateway process."**

### Evidence Table (from PAPER_TABLE.md — Table 1)

```
Routing Mode    C   Succ%   Rej0%   StateMiss   SemFail%   Cross%   GP/s   P95(ms)
──────────────────────────────────────────────────────────────────────────────────
Single-GW       20  17.6%   82.4%   0           0.0%       0.0%     5.96   2940.9
Single-GW       40  20.1%   79.9%   0           0.0%       0.0%     5.85   3301.0
Single-GW       60  18.6%   81.4%   0           0.0%       0.0%     5.79   3213.9
──────────────────────────────────────────────────────────────────────────────────
2xGW-NoShare    20  3.1%    68.5%   427 (28.5%) 28.5%      28.5%    1.06   2291.7
2xGW-NoShare    40  4.8%    66.1%   436 (29.1%) 29.1%      29.1%    1.53   2701.4
2xGW-NoShare    60  3.9%    66.1%   451 (30.1%) 30.1%      30.1%    1.25   1883.5
──────────────────────────────────────────────────────────────────────────────────
2xGW+Redis      20  26.1%   73.8%   0 (0.0%)   0.0%       22.8%    6.52   4605.8
2xGW+Redis      40  22.1%   77.3%   0 (0.0%)   0.0%       19.7%    6.51   5333.5
2xGW+Redis      60  22.0%   77.3%   0 (0.0%)   0.0%       20.1%    6.10   5934.4
```

### Key Evidence Lines

1. **2xGW-NoShare semantic break:** StateMiss=427–451 (28.5–30.1% of sessions) when random routing without shared state.
2. **2xGW+Redis semantic restoration:** StateMiss=0 across all C levels despite 19.7–22.8% cross-node activity.
3. **Single-GW baseline:** StateMiss=0 (provided control for in-process semantics).

### Conclusion Sentence

> "Under per-step random routing without shared state, 28.5–30.1% of sessions encounter continuation state misses. With Redis-backed shared session state, state misses drop to zero while 19.7–22.8% of sessions still cross gateway nodes, showing that PlanGate's commitment semantics can be preserved beyond a single gateway process."

---

## Critical Callouts (Prevent Misinterpretation)

### 1. ABD% Is NOT the Primary Failure Signal

- **ABD%** (cascade_failed / admitted) is near 0% in 2xGW-NoShare due to high step-0 rejections suppressing admitted sessions.
- **StateMiss%** captures the actual semantic break: 28.5–30.1% sessions fail to maintain continuation state.
- **Hierarchy:** State-Miss% >> Rej0% >> Success% for shared-state validation.

### 2. local_sticky (Sticky Routing) Is Not an Equivalent Baseline

- Achieves zero state misses via routing locality, not shared state.
- Each gateway has independent in-memory capacity (cap=30), causing under-utilization at C=40/60.
- Is an **engineering baseline**, not a **semantic comparison**.
- Correct interpretation: "Routing stickiness avoids state misses but lacks shared-state robustness."

### 3. 2xGW+Redis Success Rate May Exceed Single-GW

- Due to dual-gateway independent admission and shared state interleaving, throughput can improve.
- This is a **capacity gain** (two admission queues), not a semantic property; both preserve semantics equally.

### 4. Full Go Test Failures Are Out-of-Scope

- Failures are in PlanGate-R (checkpoint/recovery) tests, not multi-gateway routing.
- Scoped tests (Shared*, SessionState*, Budget*, HTTP*) **pass** ✓.
- Recovery design is a separate task.

---

## Quick-Start: How to Cite This

### For Main Paper Section

**Suggested placement:** Results → Shared-State Semantics Validation subsection.

**Text:**
> We validate PlanGate's multi-gateway deployment using Plan-and-Solve agents under randomized per-step routing. Without shared session state, 28.5–30.1% of sessions encounter state continuation misses (Table 1, 2xGW-NoShare). With Redis-backed state, misses drop to 0.0% while maintaining 19.7–22.8% cross-node routing (Table 1, 2xGW+Redis), confirming that PlanGate semantics can be preserved beyond a single gateway process.

**Table reference:** Table 1 (PAPER_TABLE.md provides Markdown + LaTeX).

### For Appendix/Supplement

**Placement:** Methods → Experiment Configuration.

**Reference:** docs/multigateway_shared_state_notes.md (detailed setup, artifact layout, Redis validation, Go test status).

---

## NOT Done (Out-of-Scope for This Experiment)

- ❌ ReAct (step-by-step) shared-state continuation
- ❌ PlanGate-R checkpoint/recovery
- ❌ vLLM backend integration
- ❌ Full go test suite fix (recovery tests)
- ❌ Checkpoint persistence to disk
- ❌ Failure recovery protocol
- ❌ Session migration / fault tolerance

**Note:** These are separate tasks. This experiment is **validation-only**, not system design.

---

## Reproducibility

### To Re-Run Full Experiment

```bash
cd mcp-governance-main - A
python scripts/run_multigateway_shared_state.py \
  --modes single local_random local_sticky shared_random \
  --concurrency 20 40 60 \
  --repeats 5 \
  --sessions 300 \
  --results-dir results/exp_multigateway_shared_state_full \
  --redis-addr 127.0.0.1:6379
```

### To Re-Generate Summary

```bash
python scripts/_compute_multigateway_shared_state_stats.py \
  --results-dir results/exp_multigateway_shared_state_full \
  --show
```

Output: CSV + printed table.

---

## Sign-Off

| Item | Status |
|------|--------|
| Experiment complete | ✓ |
| Data aggregated | ✓ |
| Paper table generated | ✓ |
| Claim validated | ✓ |
| Audit documentation | ✓ |
| Code changes documented | ✓ |
| Callouts & caveats written | ✓ |
| Ready for paper submission | ✓ |

**Next Step:** Paste [results/exp_multigateway_shared_state_full/PAPER_TABLE.md](../results/exp_multigateway_shared_state_full/PAPER_TABLE.md) content into paper draft (Results section + Table 1).

---

**Prepared by:** Experiment Agent  
**Date:** 2026-05-21  
**Scope:** P&S-only shared-state validation, mock & backends  
**Claim:** ✓ Supported by evidence
