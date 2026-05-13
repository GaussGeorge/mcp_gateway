# Minimal Reproduction Guide — PlanGate

This document describes the minimum set of commands required to sanity-check the
three main PlanGate claims without running the full multi-hour paper experiments.

**Important disclaimer:**
> Minimal commands reproduce *qualitative sanity trends* only.
> Exact paper tables require full experimental settings (5 repeats, 500 sessions,
> C=200, tuned gateway parameters) and are reproduced from cached CSVs or via
> `reproduce_mock_core.sh`. See [REPRODUCIBILITY.md](REPRODUCIBILITY.md) for the
> full reproduction path.

---

## Level 0: Build and Unit Tests

**Prerequisite:** Go ≥ 1.21. No API key. No running server.

```bash
# Full test suite
go test ./... -timeout 120s

# PlanGate package only (includes PlanGate-R tests)
go test ./plangate/... -timeout 120s
```

**Expected output:**
```
ok  mcp-governance          Xs
ok  mcp-governance/baseline Xs
ok  mcp-governance/plangate Xs
```

**What this validates:**
- The codebase compiles correctly.
- All unit tests for PlanGate core logic (discount functions, DAG routing,
  session admission, reputation) pass.
- All unit tests for PlanGate-R (checkpoint store, recovery queue, failure
  classifier, recovery execution logic) pass.
- PlanGate-R runtime integration tests pass (no real LLM, no network).

**What this does NOT validate:**
- End-to-end session throughput or latency under load.
- Comparison between gateway policies.
- Any paper table or figure numbers.

---

## Level 1: PlanGate Core Controlled Mock Smoke

**Prerequisite:** Go ≥ 1.21, Python ≥ 3.10, `pip install -r mcp_server/requirements.txt`.
No API key. Requires running local processes (gateway binary + Python backend).

### Build the gateway binary first

```bash
# Linux / macOS / WSL2
go build -o gateway ./cmd/gateway

# Windows PowerShell
go build -o gateway.exe ./cmd/gateway
```

### Smoke run: Exp1_Core (1 repeat, no API key)

```bash
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1
```

This script automatically:
1. Builds the Go gateway binary if not present.
2. Starts the Python mock backend (`mcp_server/server.py`) on port 8080.
3. Starts each gateway policy (NG, SRL, SBAC, PlanGate-Full) on port 9200.
4. Runs the DAG load generator (500 sessions, C=200, P&S only, `--ps-ratio 1.0`).
5. Collects per-session CSV, computes summary metrics, shuts down processes.

**Output directory:** `results/Exp1_Core/` (created locally, not committed)

**Expected sanity trend (1 repeat, may vary):**
```
Policy         | SuccessRate | CascadeRate | ABD-like | EffGoodput
----------------|-------------|-------------|----------|----------
ng (no-gov)    |   ~0.35     |   ~0.60     |  ~0.28   |  low
sbac           |   ~0.65     |   ~0.25     |  ~0.12   |  medium
plangate_full  |   ~0.92     |   ~0.05     |  ~0.02   |  high
```

Exact values depend on the local machine timing and single-repeat variance;
the qualitative ordering (plangate > sbac > ng) is the key sanity check.

**Runtime:** ~5–10 minutes on a 4-core/8-core machine (single repeat).
Windows may be 1.5–2× slower due to process-spawning overhead.

### Dry-run check (no processes started)

```bash
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1 --dry-run
```

---

## Level 2: PlanGate Mechanism Ablation Smoke

**Same prerequisites as Level 1.** No API key.

```bash
python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1
```

This runs three gateway policies:
- `plangate_full` — full PlanGate with budget lock and session cap.
- `wo_budgetlock` — PlanGate without the budget-lock mechanism (`mcpdp-no-budgetlock`).
- `wo_sessioncap` — PlanGate without the session cap (`mcpdp-no-sessioncap`).

**Output directory:** `results/Exp4_Ablation/`

**Expected sanity trend:**
```
Policy          | SuccessRate | CascadeRate | MidSessionFail
----------------|-------------|-------------|---------------
plangate_full   |   ~0.92     |   ~0.05     |  low
wo_budgetlock   |   ~0.75     |   ~0.18     |  higher
wo_sessioncap   |   ~0.80     |   ~0.12     |  moderate
```

`wo_budgetlock` should show noticeably more mid-session cascades or failures
than `plangate_full` under P&S overload. This is the key sanity check for
the budget-reservation mechanism.

**Runtime:** ~5–10 minutes per policy × 3 policies = ~15–30 minutes total
(single repeat). Windows may be slower.

---

## Level 3: PlanGate-R Recovery Smoke

**Prerequisite:** Go ≥ 1.21 only. No Python. No API key. No running server.
All tests use in-process mock handlers with injected simulated failures.

```bash
# Run all PlanGate-R runtime integration tests
go test ./plangate/... -run "TestRuntime" -v -timeout 120s
```

Or individually:

```bash
# R1: PlanGate-R does not replay completed steps after an interruption
go test ./plangate/... -run "TestRuntimePlanGateRRecoversWithoutReplay" -v -timeout 30s

# R2: PlanGate-R saves compute vs naive retry (fewer total tool calls)
go test ./plangate/... -run "TestRuntimePlanGateRVsNaiveRetryComputeSaving" -v -timeout 30s

# R3: PlanGate base (no recovery) fails; PlanGate-R and naive retry both eventually succeed
go test ./plangate/... -run "TestRuntimePlanGateBaseFailsWithoutRecovery" -v -timeout 30s

# R4: Natural failure → checkpoint promotion → recovery queue
go test ./plangate/... -run "TestRuntimeNaturalFailurePromotesCheckpoint" -v -timeout 30s

# R5: Compute saving also validated at the experiment-level Go test
go test ./plangate/... -run "TestPlanGateRComputeSavingVsNaiveRetry" -v -timeout 30s
```

**Expected output (key assertions):**
- `R1`: Each completed step (tools[0..K-1]) is called **exactly once** — the checkpoint resume skips replaying them.
- `R2`: PlanGate-R total executed steps < naive retry total executed steps; compute saving ≥ 30% at failure_rate=0.3.
- `R3`: PlanGate-base failure count = S (all sessions fail); PlanGate-R eventual success ≈ 1.0.
- `R4`: Session reaches `RECOVERY_QUEUED` state after failure; reverts correctly on resume.

**Scope reminder:**
PlanGate-R tests run in a **P&S controlled mock runtime only**. No real LLM,
no real tools, no ReAct-mode sessions, no semantic recovery. Injected failures
are deterministic (`context.DeadlineExceeded`-class, classified as recoverable).

**Runtime:** < 60 seconds total.

---

## Summary Table

| Level | Command | API Key? | Runtime | Validates |
|-------|---------|----------|---------|-----------|
| L0 | `go test ./...` | No | < 1 min | Compilation + unit tests |
| L0 | `go test ./plangate/...` | No | < 1 min | PlanGate-R unit + integration tests |
| L1 | `python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1` | No | ~5–10 min | PlanGate reduces cascade vs NG/SBAC |
| L2 | `python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1` | No | ~15–30 min | Budget-lock mechanism matters (wo-BudgetLock > plangate_full defects) |
| L3 | `go test ./plangate/... -run "TestRuntime" -v` | No | < 2 min | PlanGate-R no-replay + compute saving |
| Full | `bash scripts/reproduce_mock_core.sh` | No | ~30–45 min | Paper mock tables (from re-run) |
| Full | `bash scripts/reproduce_main_paper_from_cache.sh` | No | ~5–10 min | Paper tables from cached CSVs |
| Real LLM | See `docs/REPRODUCIBILITY.md` Level 3 | **Yes** | Variable | Tables 6–8 (GLM/vLLM real-LLM) |
