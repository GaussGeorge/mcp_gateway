# PlanGate-R: Checkpoint-Aware Recovery — Phase 1 Design

> **Status:** Design only (Phase 1.1 — Design Correction Pass). No runtime changes.  
> **All new mechanisms are disabled by default.**  
> Feature flags required to activate any PlanGate-R behavior.  
> Existing PlanGate, baselines, and experiment reproducibility paths are **not modified**.

---

## 0. Background and Positioning

### 0.1 PlanGate Core Thesis (unchanged)

PlanGate solves the **admitted-but-doomed (ABD)** and **cascading compute waste** problems in
multi-step LLM agent tool governance:

- **Pre-flight Atomic Admission** (§3.2, Eq. 1): admit or reject at step-0 atomically based on
  full DAG cost, eliminating mid-session rejections that waste completed steps.
- **Budget Reservation** (§3.3, Eq. 2): lock prices at admission time, giving admitted sessions
  time-isolated protection from price fluctuations.
- **Dual-Mode Routing** (§3.4, Algorithm 1): P&S and ReAct sessions handled with appropriate
  semantics.
- **Sunk-Cost-Aware Pricing** (§3.4, Eq. 3–4): ongoing ReAct sessions receive progressive
  admission discounts to protect invested compute.

PlanGate's core claim: **admission integrity** — a session that is admitted will not be killed
by the gateway mid-execution due to price drift or overload transients.

### 0.2 What PlanGate-R Adds

PlanGate-R adds **checkpoint-aware recovery** as a complementary extension:

```
PlanGate provides admission integrity and protects admitted sessions.
PlanGate-R adds checkpoint-aware recovery to improve eventual completion
under unavoidable interruptions (e.g., backend worker timeout, transient
429, controlled mock overload after partial progress).
```

PlanGate-R does **not**:
- Change the admission decision for fresh sessions.
- Override or weaken existing sunk-cost pricing logic.
- Claim to recover from non-recoverable failures (invalid DAG, user cancel, etc.).
- Provide exactly-once semantics for arbitrary side-effecting tools.
- Improve raw success rate on workloads where no recoverable failures occur.

PlanGate-R's new claims:
- **Eventual success rate within deadline** (distinct from immediate success rate).
- **Recovered success count** (sessions that failed then succeeded via recovery).
- **Reduced cascade waste** on partial failures by resuming from checkpoint rather than restarting.

---

## 1. Code Audit — Key Files Reviewed

The following files were read in full or in significant part for this design:

| File | Role |
|---|---|
| `plangate/server.go` | `MCPDPServer` struct definition; four core innovations; `sessionCap` semaphore; `protectCommittedSessions` flag |
| `plangate/session_manager.go` | `HTTPSessionReservation` (P&S budget lock + `LockedPrices`); `ReactSessionState` (ReAct sunk-cost step counter); TTL-based cleanup loops |
| `plangate/http_handlers.go` | `ServeHTTP` entry; `handleToolsCall` dual-mode router; `handlePlanAndSolveFirstStep` (Eq.1 + Eq.2); `handleReservedStep`; `handleReActSunkCostStep` |
| `plangate/dual_mode_routing.go` | `handleReservedStep` P&S locked-price path; `executeStepDirect` bypass of MCPGovernor LoadShedding; `handleReActFirstStep`; backend proxy execution |
| `plangate/dag_validation.go` | `HTTPDAGPlan` / `HTTPDAGStep` structs; `validateHTTPDAG` (Kahn cycle detection) |
| `plangate/governance_intensity.go` | `GovernanceIntensityTracker` (mock EMA hysteresis); `IntensityProvider` interface |
| `plangate/external_signal_tracker.go` | `ExternalSignalTracker` (real LLM 3-signal fusion: 429/latency/rateLimit) |
| `plangate/reputation.go` | `ReputationManager`; `ReputationScore`; ban/budget-discount logic |
| `plangate/discount_func.go` | `QuadraticDiscount` and variants for sunk-cost pricing |
| `mcp_governor.go` | `MCPGovernor` struct; dynamic pricing engine; `GetOwnPrice`/`SetOwnPrice`; `GetToolEffectivePrice` |
| `overloadDetection.go` | `latencyCheck`; `queuingCheck`; `proxyOverloadDetector` in `cmd/gateway/main.go` |
| `cmd/gateway/main.go` | All gateway modes; CLI flags; `makeProxyHandler` / `makeProxyHandlerWithSignals`; `setupMCPDPVariant` / `setupMCPDPReal` |
| `scripts/dag_load_generator.py` | `SessionState` enum (PENDING/RUNNING/SUCCESS/CASCADE_FAILED/REJECTED_AT_STEP_0); `SessionPlan`; P&S and ReAct execution loops; CSV output fields |
| `scripts/react_agent_client.py` | Real LLM ReAct agent; `system_prompt` rejection-resistant design; async semaphore pattern |
| `scripts/run_real_llm_bursty.py` | Bursty experiment config; 4 gateway configurations; tuned params |
| `scripts/load_generator.py` | Poisson/Step waveform; `ClientPriceTable` shadow pricing; raw/effective goodput CSV |

### 1.1 Current Admission → Execution Flow (P&S)

```
Client: POST with X-Plan-DAG
  └─ handlePlanAndSolveFirstStep
       ├─ validateHTTPDAG (Kahn)
       ├─ reputationMgr.IsBanned / AdjustBudget
       ├─ calculateDAGTotalCost (Eq.1)
       ├─ sessionCap <- struct{}{}  (semaphore acquire)
       ├─ budgetMgr.Reserve (LockedPrices snapshot, Eq.2)
       └─ executeStepDirect (bypass LoadShedding)

Client: POST with X-Session-ID (step K≥1)
  └─ handleReservedStep
       ├─ budgetMgr.Get (check TTL, get LockedPrices)
       ├─ locked price check (tokens >= lockedPrice[tool])
       ├─ executeStepDirect
       └─ if CurrentStep >= len(Steps): budgetMgr.Release (release semaphore)
```

### 1.2 Current Admission → Execution Flow (ReAct)

```
Client: POST with X-Session-ID (step 0, not yet tracked)
  └─ handleReActFirstStep
       ├─ sessionCap <- struct{}{}  (semaphore)
       ├─ reactSessions.Create
       └─ handleReActMode → MCPGovernor.LoadShedding (Eq.3)

Client: POST with X-Session-ID (step K≥1, tracked)
  └─ handleReActSunkCostStep
       ├─ reactSessions.Get (check TTL)
       ├─ sunk-cost price (Eq.4): P_K = P_eff × I(t) / (1 + K²·α_eff)
       ├─ executeStepDirect or LoadShedding
       └─ reactSessions.Advance
```

---

## 2. PlanGate-R State Machine

### 2.1 State Definitions

| State | Description |
|---|---|
| `NEW` | Session submitted; pre-flight validation not yet attempted. No compute consumed. |
| `RUNNING` | Session admitted at step-0. Actively executing tool steps. Semaphore slot held. `LockedPrices` (P&S) or `ReactSessionState` (ReAct) active. |
| `COMMITTED` | Applied to P&S sessions that have passed pre-flight. Equivalent to RUNNING but explicitly marks that the full budget has been reserved. Conceptually identical to RUNNING in current code; distinguished here for clarity. |
| `CHECKPOINTED` | At least one step completed successfully. Checkpoint record saved. Session is not currently executing (due to interrupt). Semaphore slot released. |
| `RECOVERY_QUEUED` | Session in recovery queue waiting for a slot. Recovery attempts counter incremented. Priority elevated over fresh NEW sessions. |
| `RECOVERING` | Recovery session admitted. Executing remaining steps from last checkpoint. |
| `SUCCEEDED` | All required steps completed. Session result delivered to client. Resources released. |
| `FAILED_TERMINAL` | Non-recoverable failure encountered, or max recovery attempts exceeded, or recovery deadline expired. Session aborted. Counted toward ABD/cascade-waste metrics. |
| `EXPIRED` | TTL elapsed without completion or recovery. Treated as FAILED_TERMINAL for metrics. Differs from FAILED_TERMINAL to distinguish timeout from hard failure. |

### 2.2 State Transition Diagram

```
                  ┌─────────────────────────────────────────────────────────┐
                  │              gateway admission (step-0)                  │
                  └──────────────────────────┬──────────────────────────────┘
                                             │
           NEW ──────────────────────────────┤
            │                               │
            │  validation failure           │  admission success
            │  (invalid DAG /               │  (budget OK + cap slot)
            │   budget fraud / banned)      ▼
            └─────────────────────► RUNNING / COMMITTED
                                        │
                        ┌───────────────┼──────────────────────────────────┐
                        │               │                                  │
              step fails│     after ≥1  │ step succeeds,                   │
              (step 0:  │     completed │ checkpoint saved                 │
              non-recov)│     step then │                                  │
                        │     interrupt │                                  │
                        ▼               ▼                                  │
               FAILED_TERMINAL   CHECKPOINTED                             │ all steps done
                                        │                                  │
                          recoverable   │  TTL expired                    ▼
                          failure       │  (before any                SUCCEEDED
                          detected      │   checkpoint)
                                        ├──────────────────► EXPIRED
                                        │
                                        ▼
                                RECOVERY_QUEUED
                                        │
                          quota / aging │  max attempts or
                          admission     │  deadline exceeded
                                        ├──────────────────► FAILED_TERMINAL
                                        │
                                        ▼
                                   RECOVERING
                                        │
                          step fails    │  step succeeds
                          (recoverable) │  and more remain
                                        ├──── loop back ──► CHECKPOINTED
                                        │
                          non-recoverable failure
                          or all steps done
                                        │
                   ┌────────────────────┴─────────────────────┐
                   ▼                                           ▼
           FAILED_TERMINAL                               SUCCEEDED
```

**Full linear happy-path (P&S):**
```
NEW → RUNNING/COMMITTED → [CHECKPOINTED → RECOVERY_QUEUED → RECOVERING →]* SUCCEEDED
```

**Full linear happy-path (ReAct, no interruption):**
```
NEW → RUNNING → SUCCEEDED
```

**Recovery path:**
```
RUNNING → CHECKPOINTED → RECOVERY_QUEUED → RECOVERING → CHECKPOINTED → RECOVERY_QUEUED → RECOVERING → SUCCEEDED
```

### 2.3 Transition Trigger Classification

| Transition | Where it occurs |
|---|---|
| `NEW → RUNNING` | Gateway, before backend call: pre-flight admission (Eq.1 + cap) |
| `NEW → FAILED_TERMINAL` | Gateway, step-0: invalid DAG / ban / budget fraud |
| `RUNNING → SUCCEEDED` | After backend response: all steps done, session released |
| `RUNNING → FAILED_TERMINAL` | After backend response: non-recoverable error on step-0/1 |
| `RUNNING → CHECKPOINTED` | After backend response: ≥1 step done, recoverable interrupt |
| `CHECKPOINTED → RECOVERY_QUEUED` | Recovery engine: checkpoint valid, attempts < max |
| `CHECKPOINTED → EXPIRED` | cleanup loop: TTL elapsed on checkpoint |
| `RECOVERY_QUEUED → RECOVERING` | Gateway recovery admission: quota allows, slot available |
| `RECOVERY_QUEUED → FAILED_TERMINAL` | attempts > max OR deadline exceeded |
| `RECOVERING → CHECKPOINTED` | After backend response: step done, more remain |
| `RECOVERING → SUCCEEDED` | After backend response: final step done |
| `RECOVERING → FAILED_TERMINAL` | After backend response: non-recoverable error in recovery |

### 2.4 P&S vs ReAct Differences in the State Machine

| Aspect | P&S | ReAct |
|---|---|---|
| Step-0 checkpoint | NOT saved (no completed work) | NOT saved (same principle) |
| Checkpoint content | `completed_steps` + `remaining_plan` (DAG subgraph) | `conversation_trace` + `observation_history` |
| Recovery execution | Deterministic: re-submit remaining DAG steps | Best-effort: replay history as context, LLM continues |
| Locked price in recovery | Re-use valid snapshot; else re-admit remaining cost | ReAct has no locked prices; standard dynamic pricing applies |
| `protectCommittedSessions` | Always applies (step K≥1 never rejected) | Applies via sunk-cost discount (Eq.4) |
| Recovery admission unit | Remaining DAG sub-cost | Single next step (same as fresh ReAct step-0) |
| Deterministic guarantee | Yes (tool outputs deterministic given inputs) | No — explicitly best-effort |

---

## 3. Failure Classification

### 3.1 Recoverable Failures

Recoverable failures MAY result in a checkpoint (if ≥1 step completed) and MAY enter the
recovery queue. Step-0 failures are NOT checkpointed (no completed work to recover from).

| Failure | Error Category | Reason Code | Checkpoint? | Recovery Queue? | ABD / Waste (see §3.3) |
|---|---|---|---|---|---|
| Gateway overload (cap full, no slot) | `GATEWAY_OVERLOAD` | `CAP_FULL` | If step ≥ 1 | Yes | See §3.3 |
| Backend worker timeout | `BACKEND_TIMEOUT` | `WORKER_TIMEOUT` | If step ≥ 1 | Yes | See §3.3 |
| Temporary queue timeout (sessionCapWait) | `QUEUE_TIMEOUT` | `QUEUE_WAIT_EXCEEDED` | If step ≥ 1 | Yes | See §3.3 |
| HTTP 429 / transient external pressure | `EXTERNAL_RATELIMIT` | `UPSTREAM_429` | If step ≥ 1 | Yes | See §3.3 |
| Reservation TTL expiry (checkpoint still valid) | `RESERVATION_EXPIRED` | `TTL_EXPIRED_CHECKPOINT_VALID` | Yes (pre-existing) | Yes | See §3.3 |
| Transient backend unavailable (5xx) | `BACKEND_UNAVAILABLE` | `BACKEND_5XX` | If step ≥ 1 | Yes | See §3.3 |
| Controlled mock overload rejection (step ≥ 1) | `MOCK_OVERLOAD` | `MOCK_REJECTED_PARTIAL` | Yes | Yes | See §3.3 |

> **Note:** Step-0 rejection for any of the above does NOT produce a checkpoint (no completed
> work to recover from). Step-0 rejections are counted as `Rej0`, never as ABD.
> ABD and cascade waste classification for recoverable failures depends on the final
> outcome of the recovery process — see §3.3 for the unified counting rules.

### 3.2 Non-Recoverable Failures

Non-recoverable failures MUST terminate the session immediately. No checkpoint is saved (or
existing checkpoint is invalidated). Session counted toward `FAILED_TERMINAL`.

| Failure | Error Category | Reason Code | Count as ABD? | Count as Cascade Waste? |
|---|---|---|---|---|
| Invalid DAG (cycle, missing dep) | `DAG_INVALID` | `DAG_CYCLE` / `DAG_MISSING_DEP` | No (rejected at validate) | No |
| User cancellation | `CLIENT_CANCEL` | `USER_CANCELLED` | No | No (intentional) |
| Tool semantic error (wrong args, type error) | `TOOL_ERROR` | `TOOL_SEMANTIC_FAIL` | If step ≥ 1 | Yes |
| Authentication / permission failure | `AUTH_FAIL` | `UNAUTHORIZED` / `FORBIDDEN` | No | No |
| Malformed request (JSON parse error) | `REQUEST_INVALID` | `MALFORMED_JSON` | No | No |
| Budget fraud / abuse ban | `SECURITY` | `BANNED` / `BUDGET_FRAUD` | No | No |
| Non-idempotent side-effect tool without idempotency key | `TOOL_POLICY` | `MISSING_IDEMPOTENCY_KEY` | If step ≥ 1 | Possibly |
| Checkpoint expired (TTL elapsed) | `CHECKPOINT_EXPIRED` | `CHECKPOINT_TTL` | No (no slot held) | No |
| Max recovery attempts exceeded | `RECOVERY_LIMIT` | `MAX_ATTEMPTS_EXCEEDED` | Yes | Yes (already partially wasted) |
| Recovery deadline exceeded | `RECOVERY_DEADLINE` | `DEADLINE_EXCEEDED` | Yes | Yes |

### 3.3 Unified ABD and Cascade Waste Counting Rules

These rules apply to all failure modes and must be used consistently in metrics,
CSV logging, and paper reporting. The key insight is that ABD/waste classification
depends on the **final outcome** of the recovery process, not the intermediate failure.

**Rule 1 — Step-0 rejection (Rej0):**
- Count as `Rej0`. Do NOT count as ABD. Do NOT count as cascade waste.
- Rationale: no tool step was executed; nothing was wasted.
- Applies to: any failure (recoverable or not) that occurs before step-0 completion.

**Rule 2 — Step ≥ 1 interruption + eventual recovery success within deadline:**
- Do NOT count as terminal ABD.
- Do NOT count as final cascade waste.
- Count as `interrupted_then_recovered`.
- Count recovery latency and checkpoint overhead as additional cost, but NOT as wasted compute.
- Rationale: the session ultimately succeeded; the interruption was a slowdown, not a doom.

**Rule 3 — Step ≥ 1 interruption + recovery deadline exceeded OR max attempts exceeded:**
- Count as `terminal_abd_after_recovery_failure` (subset of ABD).
- Count ALL executed steps across the original run plus all recovery attempts as `cascade_waste_steps`.
  Each failed recovery attempt's executed steps add to the waste total.
- Count each failed recovery attempt as `abandoned_recovery_attempts`.
- Count as ABD (admitted but ultimately doomed despite recovery effort).

**Rule 4 — Non-recoverable failure after step ≥ 1:**
- Count as ABD.
- Count all executed steps as `cascade_waste_steps`.
- `abandoned_recovery_attempts = 0` (no recovery was attempted).

**Summary table:**

| Scenario | Rej0 | ABD | cascade_waste_steps | interrupted_then_recovered | terminal_abd_after_recovery |
|---|---|---|---|---|---|
| Step-0 rejection (any cause) | ✓ | — | 0 | — | — |
| Step ≥ 1 + recovery → success | — | — | 0 | ✓ | — |
| Step ≥ 1 + recovery → deadline/attempts exceeded | — | ✓ | all steps (all attempts) | — | ✓ |
| Step ≥ 1 + non-recoverable failure | — | ✓ | all steps executed | — | — |

---

## 4. SessionCheckpoint — Semantic Structure

The following is a semantic field specification. A Go struct definition will be created in Phase 2.

```
SessionCheckpoint {
    // ── Identity ──────────────────────────────────────────────
    session_id          string      // P&S + ReAct required
    agent_id            string      // P&S + ReAct required (derived from session_id prefix / API-Key)
    mode                enum        // P&S | ReAct; required

    // ── Status ───────────────────────────────────────────────
    status              enum        // CHECKPOINTED | RECOVERY_QUEUED | RECOVERING (state machine state)
    current_step        int         // index of the next step to execute; P&S + ReAct required
    recovery_attempts   int         // number of times this session has entered recovery; required

    // ── P&S Specific ─────────────────────────────────────────
    completed_steps     []StepID    // P&S required; which DAG steps finished successfully
    remaining_plan      *HTTPDAGPlan // P&S required; DAG subgraph of not-yet-executed steps
    completed_tool_outputs map[StepID]OutputRef // P&S required; references or digests of step outputs
    locked_price_snapshot  map[ToolName]int64   // P&S required if still valid; nil if reservation expired

    // ── ReAct Specific ───────────────────────────────────────
    conversation_trace  []Message   // ReAct required; system + user + assistant + tool messages
    observation_history []Observation // ReAct required; tool call + response pairs in order
    // NOTE: ReAct recovery is BEST-EFFORT CONVERSATIONAL RECOVERY.
    // It is NOT deterministic. The LLM may reason differently from the same history.
    // Clients must be designed to tolerate non-deterministic continuation.

    // ── Budget Snapshot ──────────────────────────────────────
    budget_snapshot     int64       // P&S + ReAct required; budget at time of checkpoint
    token_usage_so_far  int64       // metrics only; LLM tokens consumed up to checkpoint

    // ── Governance Snapshot ──────────────────────────────────
    tool_weight_snapshot  map[ToolName]float64  // P&S required; tool weights at admission time
    governance_intensity_at_checkpoint float64  // metrics only; I(t) at time of checkpoint

    // ── Timing ───────────────────────────────────────────────
    created_at          time.Time   // P&S + ReAct required
    updated_at          time.Time   // P&S + ReAct required
    expires_at          time.Time   // P&S + ReAct required; controlled by --recovery-ttl

    // ── Failure Context ──────────────────────────────────────
    last_failure_reason string      // metrics + recovery routing; e.g., "WORKER_TIMEOUT"
    last_failure_category string    // metrics; e.g., "BACKEND_TIMEOUT"

    // ── Side-Effect Safety ───────────────────────────────────
    idempotency_keys    map[StepID]string  // required for tools with checkpoint_safe=false
    // Steps whose tools have checkpoint_safe=false AND no idempotency_key
    // MUST set this session as non-recoverable immediately.

    // ── Compute Accounting ───────────────────────────────────
    compute_steps_so_far int        // metrics only; number of tool calls executed
    checkpoint_bytes     int        // metrics only; estimated serialized size of this checkpoint

    // ── Privacy / Storage Cost Note ──────────────────────────
    // completed_tool_outputs: store only content hash / reference ID, NOT raw output data.
    //   Raw outputs may contain PII (user data, search results, LLM completions).
    //   P&S recovery only needs to know WHICH steps to skip, not re-replay their content.
    //
    // conversation_trace (ReAct): this DOES need the actual content for LLM context injection.
    //   It should be stored encrypted-at-rest if checkpoint backend is persistent.
    //   For Phase 2 (in-memory only), privacy risk is limited to process memory.
    //   Treat this as a high-sensitivity field; do not log raw content.
    //
    // token_usage_so_far / governance_intensity: metrics-only, not needed for recovery logic.
    //   Can be omitted or summarized if storage is constrained.
}
```

### 4.1 Field Classification Summary

| Field | P&S Required | ReAct Required | Metrics Only | Privacy/Cost Risk |
|---|---|---|---|---|
| `session_id` | ✓ | ✓ | | |
| `agent_id` | ✓ | ✓ | | Low |
| `mode` | ✓ | ✓ | | |
| `status` | ✓ | ✓ | | |
| `current_step` | ✓ | ✓ | | |
| `recovery_attempts` | ✓ | ✓ | | |
| `completed_steps` | ✓ | — | | |
| `remaining_plan` | ✓ | — | | Low |
| `completed_tool_outputs` | ✓ (refs only) | — | | **HIGH** — store hash/ref, not raw |
| `locked_price_snapshot` | ✓ if valid | — | | Low |
| `conversation_trace` | — | ✓ | | **HIGH** — may contain PII |
| `observation_history` | — | ✓ | | **HIGH** — may contain PII |
| `budget_snapshot` | ✓ | ✓ | | |
| `token_usage_so_far` | — | — | ✓ | Low |
| `tool_weight_snapshot` | ✓ | — | | Low |
| `governance_intensity_at_checkpoint` | — | — | ✓ | Low |
| `created_at` / `updated_at` / `expires_at` | ✓ | ✓ | | |
| `last_failure_reason` | ✓ | ✓ | | Low |
| `idempotency_keys` | ✓ (if side-effect) | ✓ (if side-effect) | | Low |
| `compute_steps_so_far` | — | — | ✓ | |
| `checkpoint_bytes` | — | — | ✓ | |

---

## 5. Recovery Priority Policy

### 5.1 Priority Levels

```
P0: RUNNING / COMMITTED sessions (currently executing, semaphore held)
    → Must never be preempted by P1 or P2.
    → Represent already-admitted work. Evicting them would cause cascade waste.

P1: RECOVERY_QUEUED sessions (checkpointed, waiting for re-admission)
    → Have consumed compute on completed steps.
    → Resuming them is more efficient than restarting equivalent fresh sessions.
    → Must be favored over P2, but cannot starve P2 indefinitely.

P2: NEW sessions (fresh, no completed work)
    → Standard admission queue.
    → Must not be completely blocked by P1 traffic.
```

### 5.2 Starvation Control Mechanisms

#### Mechanism A: Recovery Quota (Recommended Default)

Limit the fraction of admission slots allocated to P1 recovery sessions.

```
max_recovery_fraction = 0.4   (default; controlled by --recovery-quota flag)
```

At each admission decision:
- Count current P0 sessions occupying slots.
- Remaining available slots = `cap - P0_count`.
- P1 may use at most `floor(remaining * max_recovery_fraction)` slots.
- P2 gets the rest.

**Rationale:** Simple, predictable, easy to tune. The fraction directly controls how much
of total capacity is "reserved" for recovery. At 0.4, a system with cap=30 and 10 P0 sessions
running allows at most `floor(20 * 0.4) = 8` concurrent recovery sessions, leaving 12 for
fresh sessions. Operators can tune this per deployment without changing code logic.

#### Mechanism B: Interleaved Admission Ratio

For every `N` P1 recovery admissions, force at least 1 P2 fresh admission.

```
recovery_to_fresh_ratio = 2:1   (default; every 2 recovery → 1 forced fresh)
```

**Rationale:** Provides a strict lower bound on fresh session throughput even under heavy
recovery pressure. However, it is less predictable than quota-based control because it
depends on arrival rates. Under low fresh session load, the ratio may force waiting even
when no P2 sessions are queued.

### 5.3 Recommendation

**Adopt Mechanism A (Recovery Quota) as the default.**

Reasons:
1. Quota is capacity-proportional: as P0 grows, available P1 + P2 budget shrinks together, no
   manual rebalancing needed.
2. Quota of 0.4 aligns with the intuition that "recovery is beneficial but not dominant" —
   the majority of capacity (60%+ of non-P0 slots) remains open for fresh sessions.
3. Quota is a single float parameter easy to A/B test in experiments.
4. Interleaved ratio (B) can be layered on top of quota as a secondary guard but is not
   necessary as the primary mechanism.

Suggested default flags:
```
--enable-recovery=false          # PlanGate-R globally disabled unless set
--recovery-quota=0.4             # P1 max fraction of non-P0 slots
--recovery-max-attempts=3        # attempts before FAILED_TERMINAL
--recovery-ttl=300s              # checkpoint TTL (5 minutes)
--recovery-deadline=3x_p95       # eventually determined experimentally
--recovery-priority-ratio=2      # secondary B mechanism (if enabled)
```

---

## 6. P&S Recovery vs ReAct Recovery Semantics

### 6.1 P&S Recovery

```
Checkpoint contains:
  completed_steps = [step_1, step_2]   (already done)
  remaining_plan  = {steps: [step_3, step_4], budget: adjusted, session_id: same}

On recovery admission:
  1. Load checkpoint.
  2. Pass recovery slot admission (quota check):
       The session MUST acquire a capacity slot via the recovery quota mechanism
       before any execution begins. Price commitment can be reused if valid, but
       capacity control is NEVER bypassed.
       Recovery sessions are prioritized over fresh sessions (P1 > P2) but are still
       subject to the `max_recovery_fraction` cap. See §5 for quota details.
  3. Check locked_price_snapshot validity:
       a. If valid (TTL not expired): submit remaining_plan with locked prices.
          Price/budget re-admission is NOT required for the already-locked steps.
          The price commitment from original admission is honored for remaining steps.
       b. If expired: re-calculate cost for remaining_plan steps at current market prices.
          Re-run Eq.(1) on the remaining subgraph.
          If remaining budget (budget_snapshot - cost_of_completed_steps) < remaining_cost → FAILED_TERMINAL.
          Else: create new LockedPrices snapshot for remaining steps only.
  4. Execute remaining steps only. Do NOT re-execute completed_steps.
  5. On each step completion: update checkpoint (advance current_step).
  6. On final step completion: transition to SUCCEEDED.
```

**Key constraint:** P&S recovery is deterministic in the sense that the remaining plan is
structurally defined by the DAG and the set of completed steps. Tool outputs from completed
steps are referenced (not re-executed) to satisfy dependencies if needed.

### 6.2 ReAct Recovery

#### Prerequisite: Client-Cooperative Checkpoint Metadata

> **Critical constraint:** PlanGate-R gateway operates as an HTTP reverse proxy. It cannot
> natively observe the full LLM reasoning trace (system prompts, model completions, tool
> observation sequences). ReAct recovery with semantic fidelity therefore requires
> **client-cooperative checkpoint metadata** — the agent client must actively provide
> conversation context to the gateway.
>
> Without client-provided `conversation_trace` / `observation_history`, PlanGate-R can
> only recover **transport and session metadata** (session_id, step count, tool names,
> response status). It cannot recover semantic LLM state.

**Phase 2/3 (gateway-only checkpoint) — what the gateway can observe:**

Without any client cooperation, the gateway can record:
- `session_id`, `current_step` (from `ReactSessionState.CurrentStep`)
- `tool_name` of each executed step (from parsed `MCPToolCallParams.Name`)
- response status (success / error category)
- gateway-visible response summary (first N bytes of tool result, if non-PII)

This is sufficient for **transport-level resume**: the gateway restores the session slot
and step counter; the agent client is responsible for re-providing LLM context.
Recovery in Phase 2/3 does NOT claim semantic state restoration for ReAct sessions.

**Phase 5 (full ReAct recovery with client cooperation) — additional requirements:**

The agent client must include one of the following in each tool call request:
- A `_meta.trace_summary` field in the JSON-RPC params body (serialized conversation
  summary up to the current step, token-limited to ≤ 2000 chars).
- Or an `X-Trace-Digest` header referencing client-managed trace storage.

Without client cooperation, Phase 5 ReAct recovery degrades to Phase 2/3 transport resume.
This limitation must be stated explicitly in any paper claims about ReAct recovery.

**Recovery execution when trace is available (Phase 5):**

```
Checkpoint contains:
  conversation_trace  = [system_msg, user_msg, assistant_msg_k1, tool_result_1, ...]
  observation_history = [{tool: "calculate", result: "391", step: 1}, ...]
  current_step        = K (next step to execute)

On recovery admission:
  1. Load checkpoint.
  2. Pass recovery slot admission (quota check) — same constraint as P&S, see §5.
  3. Reconstruct LLM context by injecting conversation_trace as the message history.
  4. The LLM sees the full history up to step K-1 and is prompted to continue.
  5. Recovery begins from step K (not step 0).
  6. Standard ReAct sunk-cost pricing (Eq.4) applies, using K as the step count.
     The session inherits its step count — the K-dependent discount is preserved.
  7. If the LLM cannot precisely continue, it reasons from accumulated observations.
     This is the expected best-effort behavior.
```

**Recovery execution when only transport metadata is available (Phase 2/3 default):**

```
Checkpoint contains (gateway-observable only):
  current_step  = K
  last_tool     = "calculate"   (last successfully executed tool name)
  session_id    = original session_id

On recovery admission:
  1. Load checkpoint.
  2. Pass recovery slot admission (quota check).
  3. Return to client: HTTP 202 with recovered session_id and current_step = K.
  4. Client is responsible for re-submitting context and continuing from step K.
  5. This is a transport-level resume: PlanGate-R restores session slot and step
     counter; the agent client restores semantic (LLM) state.
```

**Explicit disclaimer (must appear in paper and code comments):**

> ReAct recovery is best-effort conversational recovery. It is NOT deterministic replay.
> The LLM may select different tools, produce different intermediate outputs, or fail to
> continue coherently from an injected history. PlanGate-R makes no guarantee of
> deterministic or exactly-once execution for ReAct sessions.
>
> In Phase 2/3, without client-provided conversation trace, ReAct recovery is limited to
> transport-level session resume. Full semantic recovery requires Phase 5 client cooperation.
> Paper claims must clearly distinguish between these two recovery modes.

### 6.3 Mode Comparison Table

| Aspect | P&S Recovery | ReAct Recovery |
|---|---|---|
| Determinism | Strong (DAG structure enforces order) | Best-effort (LLM may diverge) |
| Re-execution of completed steps | Never | Never (but LLM observes their outputs) |
| Budget re-calculation needed? | Only if locked prices expired | No (standard ReAct admission per step) |
| Context injection method | Submit remaining DAG as new plan | Inject conversation history as LLM messages |
| Recovery admission gate | Eq.(1) on remaining sub-cost | Standard ReAct step-0 admission |
| Sunk-cost discount in recovery | Via locked prices (step K uses locked price) | Via Eq.(4) with K = resume step index |
| Output references needed | Yes (to skip completed steps) | No (trace contains tool responses; Phase 2/3: N/A) |

### 6.4 Recovery Result Delivery Semantics

PlanGate is an HTTP synchronous reverse proxy. A session executes within a single HTTP
request-response cycle. On a recoverable failure, the original HTTP connection is closed
(an error response is returned). PlanGate-R must therefore define how recovery results
are eventually delivered to the client.

#### Option A: Client-Driven Recovery / Polling

The gateway returns `202 Accepted` on a recoverable failure with a recovery token.
The client polls or re-submits using the token.

```
Recoverable failure response body (HTTP 202):
  {
    "status": "recoverable",
    "recovery_session_id": "<original-session-id>",
    "recovery_token": "<opaque-token>",
    "resume_after_ms": 500,
    "reason": "WORKER_TIMEOUT",
    "current_step": K,
    "message": "Session checkpointed. Resume with X-Recovery-Token header."
  }

Client recovery request:
  POST /mcp
  X-Session-ID: <original-session-id>
  X-Recovery-Token: <opaque-token>
  (+ tool call params for next step, or empty body to poll status)

Gateway behavior:
  - Admitted: execute remaining steps, return 200 with final result.
  - Still queued: return 202 again with updated resume_after_ms.
  - Deadline exceeded / terminal: return 410 Gone with failure reason.
```

Advantages: clean REST semantics, no long-held connections, clients control retry cadence.  
Suitable as the **paper-level protocol description** of PlanGate-R.

#### Option B: Experiment-Only Client Resume (Recommended implementation prototype)

Experiment scripts simulate client-driven recovery by re-submitting sessions after a
recoverable failure. No production async delivery infrastructure is required.

```
Implementation in experiment scripts (dag_load_generator.py, react_agent_client.py):
  - On receiving a recoverable error response (e.g., HTTP 503 + reason=WORKER_TIMEOUT),
    the script records the session as CHECKPOINTED and schedules re-submission.
  - Re-submission includes the same session_id plus an X-Recovery-Mode: resume header.
  - Gateway recognizes X-Recovery-Mode and routes to recovery path.
  - Script tracks recovery_attempts and enforces max_attempts locally.
  - Scripts compute immediate_success_rate and eventual_success_rate independently.
```

This is NOT a production async task system. It is a minimal prototype to:
1. Validate that recovery state machines work end-to-end in controlled experiments.
2. Measure `immediate_success_rate` vs `eventual_success_rate_within_deadline`.
3. Provide experimental data for the paper's eventual success claim.

Advantages: minimal gateway changes; all delivery logic in client scripts (Python).

#### Option C: Gateway-Held Synchronous Recovery (Not recommended)

The gateway holds the original HTTP connection open during recovery queue wait.
The client receives a single response after full completion (including all recovery time).

Disadvantages:
- P95 completion time includes full recovery queue wait, inflating tail latency.
- HTTP server goroutines are held during recovery wait (resource leak under load).
- Cannot cleanly separate execution latency from recovery latency in measurements.
- Not realistic for production deployments.

May serve as a research upper-bound baseline but is not the default.

#### Selected Default

> **For implementation (Phase 2–5): Option B (Experiment-Only Client Resume)**  
> The experiment scripts handle re-submission. Gateway changes are minimal.
>
> **For paper semantics: Option A (Client-Driven Recovery / Polling)**  
> The paper describes PlanGate-R's recovery protocol as client-driven, with the gateway
> returning HTTP 202 + recovery_token on recoverable interruptions.
>
> This split is intentional: Option B validates the core claims (eventual success rate,
> reduced cascade waste) while Option A provides the production-ready protocol description.
> Neither Option A nor B claims to improve success rate on workloads without recoverable
> failures. PlanGate-R provides a **mechanism basis** for eventual success improvement;
> whether and how much it helps is determined by experiments.

---

## 7. Side-Effect Tool Policy

### 7.1 Tool Recovery Classification

```yaml
tools:
  calculate:
    checkpoint_safe: true
    idempotent: true
    recovery_action: reuse_output        # Use checkpointed output, skip re-execution

  web_search:
    checkpoint_safe: true
    idempotent: true                     # Same query → same (or compatible) results
    recovery_action: reuse_output        # Acceptable to reuse for informational tools

  web_fetch:
    checkpoint_safe: true
    idempotent: true
    recovery_action: reuse_output

  embedding:
    checkpoint_safe: true
    idempotent: true                     # Same input → same vector (deterministic model)
    recovery_action: reuse_output

  weather:
    checkpoint_safe: true
    idempotent: false                    # Time-sensitive; re-fetching may give fresher data
    recovery_action: re_execute          # Re-execute but output difference is tolerable

  mock_heavy:
    checkpoint_safe: true
    idempotent: true
    recovery_action: reuse_output

  send_email:
    checkpoint_safe: false
    idempotent: false
    idempotency_required: true
    recovery_action: require_idempotency_key
    # If idempotency_key present: safe to re-call (server deduplicates).
    # If idempotency_key absent: session marked NON_RECOVERABLE immediately.

  payment:
    checkpoint_safe: false
    idempotent: false
    idempotency_required: true
    recovery_action: require_idempotency_key
    # Same as send_email: requires idempotency_key or session is non-recoverable.

  database_write:
    checkpoint_safe: false
    idempotent: false
    idempotency_required: true
    recovery_action: require_idempotency_key
```

### 7.2 Policy Rules

1. **Read-only / deterministic-ish tools** (`checkpoint_safe: true`): completed step outputs
   are stored by reference in the checkpoint. On recovery, these outputs are injected into
   the context without re-executing the tool. This is the "skip completed steps" guarantee.

2. **Side-effecting tools** (`checkpoint_safe: false`): if an `idempotency_key` is provided
   in the tool call arguments, PlanGate-R records it in `checkpoint.idempotency_keys[step_id]`.
   On recovery, the same key is re-submitted to the backend, which must implement
   server-side deduplication. PlanGate-R does not implement deduplication itself.

3. **Missing idempotency key on side-effecting tool**: the step is executed once normally.
   If a recoverable failure occurs AFTER this step completes, the session is marked
   `non_recoverable = true` and will transition to `FAILED_TERMINAL` on failure rather than
   entering the recovery queue. The completed work is counted as cascade waste.

4. **Explicit limitation:**

   > PlanGate-R does NOT provide exactly-once semantics for arbitrary side-effecting tools.
   > It delegates deduplication responsibility to the tool backend via idempotency keys.
   > Sessions that mix side-effecting tools (without idempotency keys) with recoverable
   > failure modes accept the risk of duplicate execution.

### 7.3 Tool Policy Implementation Location (Phase 2+)

The tool policy registry will be implemented as a `map[toolName]ToolRecoveryPolicy` in a new
file `plangate/tool_policy.go`. All new behavior is gated behind `--enable-recovery`. Existing
tool execution paths in `dual_mode_routing.go` are not modified.

---

## 8. Experiment Metrics

### 8.1 Metric Definitions

#### Primary Outcome Metrics

| Metric | Definition |
|---|---|
| `immediate_success_rate` | Fraction of sessions that complete all steps without any interruption or recovery attempt. Identical to current `success_rate` in `dag_load_generator.py`. Used as the baseline comparison for PlanGate-R. |
| `eventual_success_rate_within_deadline` | Fraction of sessions that complete all steps, counting both immediate successes AND recoveries that complete within the recovery deadline. **Deadline is mandatory.** Suggested default: `3× baseline P95 completion time` (to be calibrated per experiment). Sessions that complete after the deadline are counted as FAILED_TERMINAL for this metric. |
| `recovered_success_count` | Absolute count of sessions that transitioned CHECKPOINTED → RECOVERY_QUEUED → ... → SUCCEEDED. |
| `recovery_attempts_per_session` | Mean number of recovery queue entries per recovered session. High values indicate unstable recovery conditions. |

> **Definitional note:**
> `immediate_success` = session completed without any CHECKPOINTED state.
> `eventual_success_within_deadline` = session completed, regardless of recovery, before
> `submission_time + recovery_deadline`.
> The delta (`eventual - immediate`) is PlanGate-R's marginal contribution.

#### Recovery Queue Metrics

| Metric | Definition |
|---|---|
| `recovery_queue_wait_ms` | Time from CHECKPOINTED → RECOVERING (wall clock). Measures recovery queue latency. Report P50 and P95. |
| `P95_completion_time` | P95 session end-to-end completion time, including recovery wait. Compared against baseline P95 to assess tail latency cost. |
| `fresh_session_wait_ms` | Time from NEW → RUNNING for fresh (P2) sessions during periods when recovery quota is active. Measures starvation pressure on fresh sessions. |
| `fresh_session_starvation_rate` | Fraction of fresh sessions that waited > 2× uncongested NEW→RUNNING time. Indicator of P1 crowding out P2. |

#### Waste / Efficiency Metrics

| Metric | Definition |
|---|---|
| `cascade_waste_steps` | Total tool steps executed across sessions that ultimately reached FAILED_TERMINAL. Includes steps from abandoned recovery attempts. Lower is better. |
| `token_waste_per_success` | Total LLM tokens consumed by failed+abandoned sessions divided by total successful sessions. |
| `ABD` | Admitted-But-Doomed rate: fraction of admitted sessions (past step-0) that eventually reach FAILED_TERMINAL. PlanGate's primary target metric. PlanGate-R should not increase ABD. |
| `Rej0` | Step-0 rejection rate (unchanged definition from PlanGate). |

#### Checkpoint Infrastructure Metrics

| Metric | Definition |
|---|---|
| `checkpoint_save_ms` | Time to serialize and store a checkpoint record. Should be < 1ms for in-memory store. |
| `checkpoint_bytes` | Serialized size of checkpoint record. P&S checkpoints are smaller (no conversation history). ReAct checkpoints may be large (full message history). |
| `checkpoint_hit_rate` | Fraction of recovery attempts where the checkpoint was found valid (not expired, not corrupted). Low hit rate suggests TTL is too short or recovery queue wait is too long. |

#### PlanGate-R Specific Outcome Metrics

> **Counting rules:** All metrics below follow the unified rules in §3.3.
> `ABD` should be disaggregated into `terminal_abd_after_recovery_failure` (Rule 3)
> and direct non-recoverable ABD (Rule 4) to make PlanGate-R's marginal contribution clear.

| Metric | Definition |
|---|---|
| `interrupted_then_recovered` | Count of sessions that entered CHECKPOINTED → ... → SUCCEEDED within deadline. These sessions are NOT counted toward ABD or cascade_waste_steps. |
| `terminal_abd_after_recovery_failure` | Count of sessions that were admitted (step ≥ 1), checkpointed, attempted recovery, but ultimately reached FAILED_TERMINAL. Counted toward ABD. |
| `abandoned_recovery_attempts` | Total recovery queue entries across all sessions that ultimately failed. Measures how much recovery capacity was consumed on ultimately unsuccessful sessions. |
| `recovery_waste_steps` | Total tool steps executed (across all recovery attempts) in sessions that ultimately reached FAILED_TERMINAL. Subset of `cascade_waste_steps`. |

### 8.2 Reporting Convention

- All rates reported as fractions in [0, 1] (not percentages).
- Latency metrics: report P50, P95, P99.
- `immediate_success_rate` and `eventual_success_rate_within_deadline` reported in the **same
  table row** to make the marginal contribution of PlanGate-R explicit.
- Deadline value for `eventual_success_rate_within_deadline` must be stated in every table
  and figure caption.

### 8.3 CSV Output Extension (Phase 8)

Add the following columns to the session-level CSV output:

```
session_id, mode, immediate_success, eventual_success, recovery_attempts,
recovery_queue_wait_ms, completion_time_ms, checkpoint_bytes, cascade_waste_steps,
token_usage, last_failure_reason, deadline_ms
```

These columns are additive. Existing columns (`session_id`, `status`, `latency_ms`, etc.)
are not removed or renamed. Rows for sessions with `--enable-recovery=false` will have empty
values for the new PlanGate-R columns.

---

## 9. Implementation Roadmap

### Phase 2: CheckpointStore Interface and In-Memory Implementation

**Scope:** Define the `CheckpointStore` interface and a thread-safe in-memory implementation.
No integration with runtime yet. Create three new files only; touch nothing else.

```go
// plangate/checkpoint_store.go  (new file; standalone, no runtime integration)
type CheckpointStore interface {
    // Save creates or overwrites the checkpoint for the given session.
    Save(ctx context.Context, cp *SessionCheckpoint) error

    // Load returns a deep copy of the checkpoint, or an error if not found/expired.
    // Callers MUST use the returned copy; do not assume shared state.
    Load(ctx context.Context, sessionID string) (*SessionCheckpoint, error)

    // Update applies fn atomically to the stored checkpoint.
    // Used for recovery_attempts increment and status transitions.
    // fn receives a deep copy; must return the modified copy to save, or an error to abort.
    Update(ctx context.Context, sessionID string, fn func(*SessionCheckpoint) (*SessionCheckpoint, error)) error

    // Delete removes the checkpoint (e.g., on SUCCEEDED or FAILED_TERMINAL).
    Delete(ctx context.Context, sessionID string) error

    // ListRecoverable returns up to `limit` checkpoints whose ExpiresAt > now
    // and status is CHECKPOINTED or RECOVERY_QUEUED, ordered by RecoveryAttempts ASC
    // then CreatedAt ASC (fewest attempts, oldest first).
    ListRecoverable(ctx context.Context, limit int, now time.Time) ([]*SessionCheckpoint, error)

    // Expire deletes all checkpoints whose ExpiresAt <= now.
    // Returns the number of records deleted.
    Expire(ctx context.Context, now time.Time) (int, error)
}

// InMemoryCheckpointStore is a thread-safe in-memory implementation.
// Load MUST return a deep copy to prevent callers from mutating the internal map.
// Update MUST hold a per-session lock for atomic read-modify-write.
type InMemoryCheckpointStore struct {
    mu    sync.RWMutex
    store map[string]*SessionCheckpoint // keyed by session_id
}
```

**Phase 2 file manifest:**
- `plangate/checkpoint_types.go` — `SessionCheckpoint` struct, `SessionStatus` enum, `AgentMode` enum, `StepRecord` struct.
- `plangate/checkpoint_store.go` — `CheckpointStore` interface + `InMemoryCheckpointStore`.
- `plangate/checkpoint_store_test.go` — unit tests for `Save`, `Load`, `Update`, `Expire`, `ListRecoverable`, deep-copy isolation, concurrent access.

**Files NOT modified in Phase 2:**
- `plangate/server.go` (no MCPDPServer field changes — deferred to Phase 3)
- `plangate/dual_mode_routing.go`
- `plangate/session_manager.go`
- `cmd/gateway/main.go`
- Any existing experiment script

### Phase 3: Save Checkpoint After Successful Step

**Scope:** In `executeStepDirect` (P&S) and the ReAct step execution path, after a successful
backend response, call `CheckpointStore.Save` with current progress.

**Integration point in existing code:**
- P&S: in `dual_mode_routing.go`, function `executeStepDirect`, after backend response
  processing, before `budgetMgr.Advance`. Add: `if s.recoveryEnabled { s.checkpointStore.Save(...) }`.
- ReAct: in `handleReActSunkCostStep` / the step execution path, after successful response,
  before `reactSessions.Advance`. Add: `if s.recoveryEnabled { s.checkpointStore.Save(...) }`.

**Guard:** all new code paths inside `if s.recoveryEnabled { ... }`. No behavior change when disabled.

### Phase 4: Recovery Queue and Priority Admission

**Scope:** Implement the recovery queue and the quota-based priority admission logic.

```go
// plangate/recovery_queue.go  (new file)
type RecoveryQueue struct {
    queue    []*SessionCheckpoint  // ordered by recovery_attempts ASC, then created_at ASC
    mu       sync.Mutex
    maxFrac  float64               // max_recovery_fraction (from --recovery-quota)
}

func (q *RecoveryQueue) Enqueue(cp *SessionCheckpoint)
func (q *RecoveryQueue) TryAdmit(availableSlots int, p0Count int) (*SessionCheckpoint, bool)
```

**Integration point:** In `handlePlanAndSolveFirstStep` / `handleReActFirstStep`, BEFORE
the normal admission path, check if `recoveryQueue.TryAdmit()` should be called instead.
This is a NEW code path, not a modification of existing admission logic.

### Phase 5: P&S and ReAct Recovery Execution Paths

**Scope:** Implement `handleRecoverySession` which loads a checkpoint and dispatches to
either `executePS_Recovery` or `executeReAct_Recovery`.

- `executePS_Recovery`: constructs a `HTTPDAGPlan` from `remaining_plan`, optionally reuses
  `locked_price_snapshot` if valid, otherwise re-runs Eq.(1) on remaining cost.
- `executeReAct_Recovery`: assembles LLM message history from `conversation_trace`,
  calls LLM with the reconstructed context, proceeds as standard ReAct from step K.

**No changes to existing execution paths in `dual_mode_routing.go`.**

### Phase 6: Side-Effect Tool Policy

**Scope:** Implement `ToolRecoveryPolicy` registry in `plangate/tool_policy.go`.

On step completion, check `policy[toolName].checkpoint_safe`. If false and no `idempotency_key`
provided, set `checkpoint.non_recoverable = true`.

### Phase 7: Experiments — NG / PlanGate / PlanGate-R / Naive Retry

Add a new gateway mode `mcpdp-recovery` to `cmd/gateway/main.go`.
Add a new experiment script `scripts/run_recovery_experiment.py`.

Comparison matrix:
- **NG**: no governance, no recovery.
- **PlanGate**: admission integrity, no recovery.
- **PlanGate-R**: admission integrity + checkpoint recovery.
- **Naive Retry**: no governance, client retries from step 0 on any failure.

Evaluation: immediate_success_rate, eventual_success_rate, cascade_waste_steps,
fresh_session_wait_ms across bursty/mock overload workloads.

### Phase 8: Metrics and CSV Logging

Add PlanGate-R metric columns to session CSV output. Add
`checkpoint_save_ms`, `checkpoint_bytes`, `checkpoint_hit_rate` to infrastructure metrics log.
Extend `scripts/analyze_results.py` to compute `eventual_success_rate_within_deadline`.

### Phase 9: Paper Integration

Update §3 with PlanGate-R as an optional extension subsection (§3.9 or Appendix).
Add Table: immediate vs eventual success rate under bursty workload.
Clarify that PlanGate-R does not change the core ABD + cascade-waste claims of PlanGate.
Position PlanGate-R as a practical extension for deployments with non-zero backend failure rates.

---

## 9a. Step Identity and Checkpoint Trigger

Each time a tool step completes successfully, the checkpoint save is triggered. The
checkpoint store needs the following **step identity fields** at save time.

### 9a.1 Required Fields per Checkpoint Save

| Field | Source in current code | Available in Phase 2/3? |
|---|---|---|
| `session_id` | HTTP header `X-Session-ID`; also in `HTTPSessionReservation.SessionID` / `ReactSessionState.SessionID` | ✓ |
| `mode` | Determined at step-0: P&S if `X-Plan-DAG` present, else ReAct | ✓ |
| `step_index` (current step number) | `HTTPSessionReservation.CurrentStep` (P&S) or `ReactSessionState.CurrentStep` (ReAct) | ✓ |
| `tool_name` (just-executed tool) | `MCPToolCallParams.Name` (parsed from request body in `handleReservedStep` / `handleReActSunkCostStep`) | ✓ |
| `step_id` (P&S DAG step identifier) | `HTTPDAGPlan.Steps[CurrentStep].StepID` via `res.Plan.Steps` in `handleReservedStep` | ✓ (P&S only) |
| `completed_steps` list | Derivable: all steps with index < `CurrentStep` from `res.Plan.Steps` | ✓ (P&S only) |
| `remaining_plan` (DAG subgraph) | All steps with index ≥ `CurrentStep+1` from `res.Plan.Steps` | ✓ (P&S only) |
| `locked_price_snapshot` | `res.LockedPrices` map | ✓ (P&S only) |
| `budget_snapshot` | `res.TotalCost` (original) minus cost of completed steps | ✓ (P&S only) |
| `response_status` (success/error) | Return value / error from backend proxy call in `executeStepDirect` | ✓ |
| `response_summary` (first N bytes) | Response body from backend (available in `executeStepDirect` before returning) | ✓ (truncated) |

### 9a.2 Fields Requiring Code Additions in Phase 3

The following fields are NOT currently accessible at the checkpoint save point without
minor additions. These are Phase 3 tasks, not Phase 2:

| Missing Field | Gap | Phase 3 Addition |
|---|---|---|
| `agent_id` | Current code uses `session_id` as agent proxy. No dedicated agent_id field. | Add `agentID` derivation (e.g., session_id prefix before first `-`) in Phase 3. |
| `governance_intensity_at_checkpoint` | `getGovernanceIntensity()` is callable on `MCPDPServer` but not passed to checkpoint save. | Pass `s.getGovernanceIntensity()` at save time in Phase 3. |
| `tool_weight_snapshot` | Tool weights are implicit in `calculateDAGTotalCost`; not stored per-step. | Cache weight map at admission time in Phase 3. |
| `conversation_trace` / `observation_history` | Gateway cannot see LLM messages (reverse proxy). | Requires client cooperation (Phase 5). Do NOT add in Phase 3. |

### 9a.3 Backend Response Success/Failure Detection

In `executeStepDirect` (in `dual_mode_routing.go`), the backend response
is already parsed into a `*mcpgov.JSONRPCResponse`. A step is considered successful if:
- `resp.Error == nil` AND the `result.content` array is non-empty.

A step is considered failed if:
- `resp.Error != nil` (error code + message available).
- HTTP call itself fails (timeout, connection refused).

Phase 3 will inspect this return value after `executeStepDirect` returns to determine
whether to save (success) or classify failure (and potentially checkpoint existing progress).

---

## 10. Code Adaptation Risk Assessment

### 10.1 Checkpoint Save Insertion Points

| Location | Function | Risk |
|---|---|---|
| `plangate/dual_mode_routing.go` | `executeStepDirect` — after successful backend response, before `budgetMgr.Advance` | **Low.** An `if s.recoveryEnabled { ... }` guard ensures no behavior change when disabled. The checkpoint call is a pure write to an in-memory map. |
| `plangate/dual_mode_routing.go` | ReAct step execution path — after `reactSessions.Advance` | **Low.** Same guard pattern. |
| `plangate/dual_mode_routing.go` | On session completion — before `budgetMgr.Release` / `reactSessions.ReleaseAndDelete` | **Low.** Delete checkpoint on success (checkpoint no longer needed). |

### 10.2 Recovery Admission Insertion Points

| Location | Risk |
|---|---|
| `plangate/dual_mode_routing.go` → `handlePlanAndSolveFirstStep`, before `sessionCap <- struct{}{}` | **Medium.** Must not change existing admission semantics. Recovery TryAdmit must only run if `recoveryEnabled`. Current flow: `sessionCap <- struct{}{}` is the admission gate. Recovery path would use a separate quota-aware check. |
| `plangate/dual_mode_routing.go` → `handleReActFirstStep`, before `reactSessions.Create` | **Medium.** Same reasoning. |

### 10.3 Current Metrics / CSV Support Assessment

**Current CSV fields** (from `dag_load_generator.py` `SessionPlan`):
- `session_id`, `mode`, `status` (SUCCESS/CASCADE_FAILED/REJECTED_AT_STEP_0),
  `raw_goodput`, `effective_goodput`, `latency_ms`, `steps_completed`, `steps_total`,
  `budget`, `start_time`, `end_time`.

**Gap analysis for PlanGate-R:**
- `immediate_success` — derivable from `status == SUCCESS AND recovery_attempts == 0`. New column needed.
- `eventual_success` — new concept, new column.
- `recovery_attempts` — not tracked. New column needed.
- `recovery_queue_wait_ms` — not tracked. New column.
- `checkpoint_bytes` — not tracked. New column (infra metrics, not per-session CSV).
- `deadline_ms` — new column (constant per experiment run, add to header).

**Assessment:** Current CSV infrastructure is additive-extensible. No existing columns need
to be modified. Parser scripts (`analyze_results.py`, `parse_sessions.py`) will need updating
to recognize new columns, but default behavior (ignoring unknown columns) is safe.

### 10.4 Risk of Breaking Existing Experiment Results

| Risk Area | Assessment | Mitigation |
|---|---|---|
| Existing admission logic (P&S, ReAct) | **No risk** if all PlanGate-R code is inside `if s.recoveryEnabled` guards. | Mandatory feature flag for all new code. |
| `HTTPBudgetReservationManager` and `ReactSessionManager` | **No risk** if not modified. Phase 3 only adds a separate `CheckpointStore.Save` call. | Do not modify existing structs or methods. |
| `proxyOverloadDetector` in `cmd/gateway/main.go` | **No risk** — not modified. | Recovery queue runs in the plangate layer, not in the proxy layer. |
| Baseline gateways (`baseline/`) | **No risk** — PlanGate-R is plangate-package only. | Baselines not touched. |
| Experiment scripts (existing) | **No risk** — new mode `mcpdp-recovery` is a new CLI flag value. Existing scripts use `mcpdp`, `ng`, `rajomon`, etc. | New experiment script added; existing scripts unchanged. |
| CSV output parser scripts | **Low risk** — new columns are additive. Most parsers use `csv.DictReader` which handles new columns gracefully. | Update documentation; add column presence checks where needed. |
| `mcp_governor_test.go`, `mcp_transport_test.go`, `plangate/benchmark_test.go` | **No risk** — tests do not reference recovery code. | New tests added in separate `plangate/recovery_test.go` file. |

### 10.5 Required Feature Flags

All the following flags default to their "PlanGate-R disabled" values. The existing gateway
behavior is identical when these flags are absent.

> **Note on Phase 2:** These flags are documented here for future integration but are NOT
> yet wired into `cmd/gateway/main.go` in Phase 2. Phase 2 only creates the store types
> and tests. Flag integration into `main.go` is a Phase 3+ task.

```
--enable-recovery=false
    Master switch. All PlanGate-R code paths are dead code unless this is true.
    Default: false. Must be explicitly set to activate any recovery behavior.

--recovery-ttl=300s
    Checkpoint TTL. After this duration, CHECKPOINTED sessions are expired and
    transitioned to EXPIRED (counted as FAILED_TERMINAL for metrics).
    Default: 300s (5 minutes).

--recovery-max-attempts=3
    Maximum times a session may enter RECOVERY_QUEUED before FAILED_TERMINAL.
    Default: 3.

--recovery-quota=0.4
    max_recovery_fraction: fraction of non-P0 available slots reserved for P1
    recovery sessions. P2 fresh sessions always retain (1 - quota) of available slots.
    Default: 0.4.

--recovery-mode=experiment_resume
    Recovery result delivery mode:
      experiment_resume  : experiment scripts simulate client re-submission (Phase 2–5 default).
      client_driven      : gateway returns HTTP 202 + recovery_token on recoverable failure
                           (production-style Option A; Phase 6+ full implementation).
    Default: experiment_resume.

--recovery-deadline-sec=0
    Deadline (seconds from original submission) for eventual_success_rate_within_deadline.
    0 = must be set explicitly; auto-calibration (3× P95) requires a separate
    calibration run. Experiments MUST set this to a concrete value.
    Default: 0 (no deadline, metric not computed).

--recovery-store=inmemory
    CheckpointStore backend.
      inmemory  : in-process sync.Map (Phase 2–7 default; lost on restart).
      (future)  : redis / boltdb for persistence across restarts.
    Default: inmemory.

--recovery-priority-ratio=2
    For Mechanism B (interleaved admission ratio): allow 1 fresh session for every
    N recovery admissions. Only active if --enable-recovery=true AND
    --recovery-use-ratio=true.
    Default: 2.
```

---

## 11. Summary

### 11.1 State Machine (Quick Reference)

```
NEW ──(admit)──► RUNNING ──(≥1 step done + interrupt)──► CHECKPOINTED
                    │                                          │
                (all done)                        (recoverable, TTL ok)
                    │                                          │
               SUCCEEDED                            RECOVERY_QUEUED
                                                          │
                                                  (quota + slot ok)
                                                          │
                                                     RECOVERING
                                                    /          \
                                           (more steps)    (all done)
                                                /                \
                                        CHECKPOINTED          SUCCEEDED
```

Terminal states: `SUCCEEDED`, `FAILED_TERMINAL`, `EXPIRED`

### 11.2 Recoverable / Non-Recoverable Summary

- **Recoverable:** transient overload, backend timeout, 429, TTL expiry with valid checkpoint,
  controlled mock rejection after partial progress.
- **Non-recoverable:** invalid DAG, user cancel, tool semantic error, auth failure, budget fraud,
  side-effect tool without idempotency key, max attempts exceeded, checkpoint expired.
- **Principle:** step-0 failures (no completed work) never produce a checkpoint.

### 11.3 Checkpoint Fields Summary

- **Core (both modes):** `session_id`, `agent_id`, `mode`, `status`, `current_step`,
  `recovery_attempts`, `budget_snapshot`, `created_at`, `updated_at`, `expires_at`,
  `last_failure_reason`.
- **P&S only:** `completed_steps`, `remaining_plan`, `completed_tool_outputs` (refs),
  `locked_price_snapshot`, `tool_weight_snapshot`.
- **ReAct only:** `conversation_trace`, `observation_history` (best-effort recovery context).
- **Metrics only:** `token_usage_so_far`, `governance_intensity_at_checkpoint`,
  `compute_steps_so_far`, `checkpoint_bytes`.
- **Privacy-sensitive:** `completed_tool_outputs` (store hash/ref only), `conversation_trace`
  (encrypt at rest in production).

### 11.4 Recommended Priority Policy

**Recovery Quota (Mechanism A)** with `max_recovery_fraction = 0.4`.  
P0 sessions are always protected. P1 sessions may use up to 40% of available (non-P0) slots.
P2 always retains at least 60% of available slots.

### 11.5 Phase 2 Minimum Entry Point

Phase 2 creates exactly **three new files** and modifies **nothing else**.

**Create:**
1. `plangate/checkpoint_types.go` — `SessionCheckpoint` struct, `SessionStatus` enum, `AgentMode` enum, `StepRecord` struct.
2. `plangate/checkpoint_store.go` — `CheckpointStore` interface + `InMemoryCheckpointStore` (thread-safe, deep-copy `Load`).
3. `plangate/checkpoint_store_test.go` — unit tests covering: `Save`/`Load` round-trip, deep-copy isolation, `Update` atomicity, `Expire` TTL, `ListRecoverable` ordering and limit.

**Do NOT modify in Phase 2:**
- `plangate/server.go` — no `MCPDPServer` field additions (deferred to Phase 3 to avoid any accidental runtime coupling)
- `plangate/dual_mode_routing.go`
- `plangate/session_manager.go`
- `cmd/gateway/main.go`
- Any existing experiment script or baseline

**Why defer MCPDPServer field additions to Phase 3?**  
Adding fields to a live struct, even with a `nil` guard, creates a coupling point that could
inadvertently be referenced before the recovery gate is checked. Phase 2 validates the store
abstraction in complete isolation via unit tests. Phase 3 integrates it into the runtime
under a strict `if s.recoveryEnabled { ... }` guard after Phase 2 is fully tested.

Phase 2 deliverable: `go test ./plangate/... -run TestCheckpoint` passes on all new tests
with no failures in existing tests.
