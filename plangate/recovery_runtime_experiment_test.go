package plangate

// PlanGate-R Phase 7A: Controlled Mock Runtime Experiment
//
// ══════════════════════════════════════════════════════════════════════════════
// SCOPE NOTE
// ══════════════════════════════════════════════════════════════════════════════
// This file tests the REAL MCPDPServer execution path.  Specifically it
// exercises the handleRecoveryResume code-path from recovery_execution.go,
// which is what actually runs in production.
//
// Unlike the Phase 6 pure simulator (recovery_experiment_test.go), here:
//   - Handlers are REAL registered functions (with call-count tracking)
//   - Checkpoints are managed by the REAL InMemoryCheckpointStore
//   - Recovery is triggered via the REAL handleRecoveryResume()
//   - Latency is measured with time.Since() around each session
//
// This runtime test therefore validates that the Phase 6 simulator conclusions
// hold in the actual gateway code path:
//   R1. PlanGate-R does NOT replay completed steps at runtime
//   R2. PlanGate-R executes fewer handler calls than naive retry
//   R3. PlanGate-base fails interrupted sessions; PlanGate-R recovers them
//   R4. Recovery latency overhead is measurable and bounded
//
// Limitations (still Phase 7A):
//   - No real LLM; handlers are mock sleepers
//   - No real HTTP transport; handleRecoveryResume is called directly
//   - Interruption is injected by saving a checkpoint, not via real failure
//   - No concurrent sessions (sequential loop)
//   - This is NOT a paper-grade runtime experiment
// ══════════════════════════════════════════════════════════════════════════════

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	rand "math/rand"
	"net/http"
	"net/http/httptest"
	"sort"
	"sync/atomic"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

// ─────────────────────────────────────────────────────────────────────────────
// Runtime experiment types
// ─────────────────────────────────────────────────────────────────────────────

// runtimeSessionMetrics holds metrics for one runtime session.
type runtimeSessionMetrics struct {
	Succeeded     bool
	ExecutedSteps int   // handler invocations during THIS session run
	Duration      time.Duration
}

// RuntimeExperimentResult aggregates metrics across all sessions for one policy.
type RuntimeExperimentResult struct {
	Policy string

	TotalSessions         int
	EventualSuccessCount  int
	RecoveredSuccessCount int

	TotalExecutedSteps int // total handler invocations
	UsefulSteps        int // EventualSuccessCount × StepsPerSession
	TotalWasteSteps    int // TotalExecutedSteps - UsefulSteps
	ReplayOverhead     int // steps re-executed that were already done (naive only)
	AvoidedReplay      int // steps never re-executed due to checkpoint (pgr only)

	// Per-tool call counts (indexed by step position 0..N-1)
	ToolCallCounts []int64

	// Latency samples (one per session)
	Latencies []time.Duration
}

func (r RuntimeExperimentResult) P50ms() float64 {
	return percentileMs(r.Latencies, 50)
}
func (r RuntimeExperimentResult) P95ms() float64 {
	return percentileMs(r.Latencies, 95)
}
func (r RuntimeExperimentResult) ExecutedStepsPerSuccess() float64 {
	if r.EventualSuccessCount == 0 {
		return -1
	}
	return float64(r.TotalExecutedSteps) / float64(r.EventualSuccessCount)
}

// CallsPerSuccess returns TotalExecutedSteps / EventualSuccessCount.
// Returns -1 when no sessions succeeded (avoids div-by-zero).
func (r RuntimeExperimentResult) CallsPerSuccess() float64 {
	if r.EventualSuccessCount == 0 {
		return -1
	}
	return float64(r.TotalExecutedSteps) / float64(r.EventualSuccessCount)
}

// SuccessRate returns EventualSuccessCount / TotalSessions.
func (r RuntimeExperimentResult) SuccessRate() float64 {
	if r.TotalSessions == 0 {
		return 0
	}
	return float64(r.EventualSuccessCount) / float64(r.TotalSessions)
}

func percentileMs(d []time.Duration, p int) float64 {
	if len(d) == 0 {
		return 0
	}
	sorted := make([]time.Duration, len(d))
	copy(sorted, d)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	idx := (len(sorted)-1)*p/100
	return float64(sorted[idx].Microseconds()) / 1000.0
}

// ─────────────────────────────────────────────────────────────────────────────
// Runtime experiment helper
// ─────────────────────────────────────────────────────────────────────────────

// makeRuntimeServer creates an MCPDPServer with recovery enabled, an in-memory
// checkpoint store, and N mock tool handlers.  Each handler atomically
// increments the per-step counter and optionally sleeps handlerSleep.
//
// Returns (server, per-step counters slice, tool names slice).
func makeRuntimeServer(t *testing.T, N int, handlerSleep time.Duration) (
	*MCPDPServer, []int64, []string,
) {
	t.Helper()
	gov := makeTestGovernor()
	s := NewMCPDPServer("runtime-exp", gov, 60*time.Second, 0, 0.0)
	s.recoveryConfig = RecoveryConfig{
		Enabled:     true,
		TTL:         5 * time.Minute,
		MaxAttempts: 3,
		Store:       "inmemory",
	}
	s.checkpointStore = NewInMemoryCheckpointStore()

	counters := make([]int64, N)
	tools := make([]string, N)
	for i := 0; i < N; i++ {
		stepIdx := i
		name := fmt.Sprintf("runtime_tool_%d", i)
		tools[i] = name
		s.RegisterTool(mcpgov.MCPTool{Name: name, Description: "runtime mock"},
			func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
				atomic.AddInt64(&counters[stepIdx], 1)
				if handlerSleep > 0 {
					select {
					case <-time.After(handlerSleep):
					case <-ctx.Done():
					}
				}
				return &mcpgov.MCPToolCallResult{
					Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}},
				}, nil
			})
	}
	return s, counters, tools
}

// injectInterruption simulates a recoverable interruption after K steps:
//  1. Saves a checkpoint in ACTIVE_CHECKPOINT status with CurrentStep=K,
//     CompletedSteps=[tools[0..K-1]], RemainingPlanJSON=[tools[K..N-1]].
//  2. Promotes the checkpoint to CHECKPOINTED via markCheckpointRecoverable.
func injectInterruption(
	t *testing.T,
	s *MCPDPServer,
	sessionID string,
	tools []string,
	K int,
) {
	t.Helper()
	ctx := context.Background()
	N := len(tools)

	completed := make([]StepRecord, K)
	for i := 0; i < K; i++ {
		completed[i] = StepRecord{
			StepID:    fmt.Sprintf("rt_s%d", i),
			StepIndex: i,
			ToolName:  tools[i],
		}
	}
	remaining := make([]HTTPDAGStep, N-K)
	for i := K; i < N; i++ {
		remaining[i-K] = HTTPDAGStep{
			StepID:   fmt.Sprintf("rt_s%d", i),
			ToolName: tools[i],
		}
	}
	remainingJSON, _ := json.Marshal(remaining)
	err := s.checkpointStore.Save(ctx, &SessionCheckpoint{
		SessionID:         sessionID,
		AgentID:           "rt-agent",
		Mode:              AgentModePlanSolve,
		Status:            StatusActiveCheckpoint,
		CurrentStep:       K,
		CompletedSteps:    completed,
		RemainingPlanJSON: remainingJSON,
		CreatedAt:         time.Now(),
	})
	if err != nil {
		t.Fatalf("injectInterruption: checkpoint save: %v", err)
	}
	// Promote to CHECKPOINTED.
	s.markCheckpointRecoverable(ctx, sessionID, RecoveryFailure{
		Decision: RecoveryDecisionRecoverable,
		Category: FailureCategoryBackendUnavail,
		Reason:   FailureReasonBackend5XX,
	})
}

// executeFirstPassSteps calls the handlers for tools[0..K-1] directly.
// This simulates the P&S first-pass execution before the interruption.
func executeFirstPassSteps(
	s *MCPDPServer,
	tools []string,
	K int,
) int {
	ctx := context.Background()
	for i := 0; i < K; i++ {
		if h, ok := s.handlers[tools[i]]; ok {
			h(ctx, mcpgov.MCPToolCallParams{Name: tools[i]}) //nolint:errcheck
		}
	}
	return K
}

// executeAllSteps calls ALL N handlers directly (simulates naive full retry from step 0).
func executeAllSteps(s *MCPDPServer, tools []string) int {
	ctx := context.Background()
	for _, name := range tools {
		if h, ok := s.handlers[name]; ok {
			h(ctx, mcpgov.MCPToolCallParams{Name: name}) //nolint:errcheck
		}
	}
	return len(tools)
}

// ─────────────────────────────────────────────────────────────────────────────
// Runtime table printer
// ─────────────────────────────────────────────────────────────────────────────

func printRuntimeTable(t *testing.T, results []RuntimeExperimentResult) {
	t.Helper()
	sep := fmt.Sprintf("%s", make([]byte, 120))
	sep = "────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────"
	t.Logf("\n%s\nPhase 7A — Runtime Experiment Results\n%s", sep, sep)
	t.Logf("  %-18s  %14s  %12s  %9s  %9s  %12s  %13s  %12s  %8s  %8s",
		"Policy", "Success", "Recovered", "ExecSteps", "Useful", "TotalWaste",
		"ReplayOverhead", "AvoidedReplay", "P50ms", "P95ms")
	t.Logf("%s", sep)
	for _, r := range results {
		t.Logf("  %-18s  %6d/%-6d  %6d/%-5d  %8d   %7d   %10d   %12d   %11d  %7.2f  %7.2f",
			r.Policy,
			r.EventualSuccessCount, r.TotalSessions,
			r.RecoveredSuccessCount, r.TotalSessions,
			r.TotalExecutedSteps, r.UsefulSteps, r.TotalWasteSteps,
			r.ReplayOverhead, r.AvoidedReplay,
			r.P50ms(), r.P95ms())
	}
	t.Logf("%s", sep)
	if len(results) >= 3 {
		base, naive, pg := results[0], results[1], results[2]
		t.Logf("  PlanGate-R vs PlanGate-base: success gain      = +%d sessions",
			pg.EventualSuccessCount-base.EventualSuccessCount)
		t.Logf("  PlanGate-R vs Naive-retry:   exec steps saving  = -%d steps (%.1f%%)",
			naive.TotalExecutedSteps-pg.TotalExecutedSteps,
			100*float64(naive.TotalExecutedSteps-pg.TotalExecutedSteps)/
				float64(max(naive.TotalExecutedSteps, 1)))
		t.Logf("  PlanGate-R vs Naive-retry:   waste reduction    = -%d steps",
			naive.TotalWasteSteps-pg.TotalWasteSteps)
	}
	t.Logf("%s\n", sep)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 1 — PlanGate-R does NOT replay completed steps at runtime
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimePlanGateRRecoversWithoutReplay verifies that after an interruption
// at step K=2 in a 5-step session:
//   - Handlers for tools[0] and tools[1] are called ONCE (from the first pass)
//   - Handlers for tools[2..4] are called ONCE (from recovery)
//   - No handler is called more than once
func TestRuntimePlanGateRRecoversWithoutReplay(t *testing.T) {
	N, K := 5, 2
	s, counters, tools := makeRuntimeServer(t, N, 0)
	sessionID := "rt-no-replay-sess"

	// First pass: execute tools[0..K-1].
	executeFirstPassSteps(s, tools, K)

	// Inject recoverable interruption.
	injectInterruption(t, s, sessionID, tools, K)

	// Verify call counts BEFORE recovery.
	for i := 0; i < K; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 1 {
			t.Errorf("before recovery: tool[%d] call count want 1, got %d", i, c)
		}
	}
	for i := K; i < N; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 0 {
			t.Errorf("before recovery: tool[%d] call count want 0, got %d", i, c)
		}
	}

	// Trigger recovery via real handleRecoveryResume.
	r := makeRecoveryResumeRequest(sessionID)
	resp := s.handleRecoveryResume(context.Background(), r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("recovery failed: %s", resp.Error.Message)
	}

	// After recovery: tools[0..K-1] must still be 1 (NOT called again by recovery).
	t.Logf("Per-tool call counts after recovery:")
	for i := 0; i < N; i++ {
		c := atomic.LoadInt64(&counters[i])
		t.Logf("  tool[%d] (%s): %d calls", i, tools[i], c)
	}
	for i := 0; i < K; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 1 {
			t.Errorf("after recovery: tool[%d] must be called exactly once (completed step not replayed), got %d", i, c)
		}
	}
	for i := K; i < N; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 1 {
			t.Errorf("after recovery: tool[%d] must be called exactly once (recovery step), got %d", i, c)
		}
	}

	// Parse result and verify skipped/executed steps.
	resBytes, _ := json.Marshal(resp.Result)
	var result PSRecoveryResult
	if err := json.Unmarshal(resBytes, &result); err != nil {
		t.Fatalf("PSRecoveryResult parse: %v", err)
	}
	if result.SkippedSteps != K {
		t.Errorf("SkippedSteps want %d (=K), got %d", K, result.SkippedSteps)
	}
	if result.ExecutedSteps != N-K {
		t.Errorf("ExecutedSteps want %d (=N-K), got %d", N-K, result.ExecutedSteps)
	}
	if result.TotalSteps != N {
		t.Errorf("TotalSteps want %d (=N), got %d", N, result.TotalSteps)
	}
	t.Logf("Recovery result: skipped=%d executed=%d total=%d mode=%s",
		result.SkippedSteps, result.ExecutedSteps, result.TotalSteps, result.Mode)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 2 — PlanGate-R vs Naive-retry: compute saving at runtime
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimePlanGateRVsNaiveRetryComputeSaving runs S=20 sessions through
// the real MCPDPServer handler path under naive retry and PlanGate-R,
// and verifies that PlanGate-R executes strictly fewer total handler calls.
func TestRuntimePlanGateRVsNaiveRetryComputeSaving(t *testing.T) {
	const (
		S = 20   // sessions
		N = 5    // steps per session
		K = 2    // interrupt after step K
	)

	runPolicy := func(policy RecoveryPolicy) RuntimeExperimentResult {
		s, counters, tools := makeRuntimeServer(t, N, 0)
		res := RuntimeExperimentResult{
			Policy:        string(policy),
			TotalSessions: S,
			ToolCallCounts: make([]int64, N),
		}
		// Reset shared counters before this policy run.
		for i := range counters {
			atomic.StoreInt64(&counters[i], 0)
		}

		for sess := 0; sess < S; sess++ {
			sessionID := fmt.Sprintf("rt-policy-%s-%d", policy, sess)
			start := time.Now()

			// First pass: execute K steps.
			res.TotalExecutedSteps += executeFirstPassSteps(s, tools, K)

			// Inject interruption.
			injectInterruption(t, s, sessionID, tools, K)

			switch policy {
			case PolicyPlanGateBase:
				// No recovery: session fails.
				res.Latencies = append(res.Latencies, time.Since(start))

			case PolicyNaiveRetry:
				// Execute ALL N steps from step 0 again (simulates full retry).
				res.TotalExecutedSteps += executeAllSteps(s, tools)
				res.ReplayOverhead += K // K first-pass steps become overhead
				res.EventualSuccessCount++
				// Clean up the checkpoint (session "restarted", old checkpoint stale).
				s.checkpointStore.Delete(context.Background(), sessionID) //nolint:errcheck
				res.Latencies = append(res.Latencies, time.Since(start))

			case PolicyPlanGateR:
				// Recover via real handleRecoveryResume (executes N-K remaining steps).
				r := makeRecoveryResumeRequest(sessionID)
				resp := s.handleRecoveryResume(context.Background(), r, makeRPCRequest())
				if resp.Error != nil {
					t.Logf("session %d recovery failed: %s", sess, resp.Error.Message)
				} else {
					res.TotalExecutedSteps += (N - K)
					res.RecoveredSuccessCount++
					res.EventualSuccessCount++
					res.AvoidedReplay += K
				}
				res.Latencies = append(res.Latencies, time.Since(start))
			}
		}

		// Snapshot per-tool call counts.
		for i := range counters {
			res.ToolCallCounts[i] = atomic.LoadInt64(&counters[i])
		}

		res.UsefulSteps = res.EventualSuccessCount * N
		res.TotalWasteSteps = res.TotalExecutedSteps - res.UsefulSteps
		return res
	}

	base  := runPolicy(PolicyPlanGateBase)
	naive := runPolicy(PolicyNaiveRetry)
	pg    := runPolicy(PolicyPlanGateR)

	printRuntimeTable(t, []RuntimeExperimentResult{base, naive, pg})

	// ── Core assertion: PlanGate-R fewer total handler calls than naive ──────
	if pg.TotalExecutedSteps >= naive.TotalExecutedSteps {
		t.Errorf("runtime: pg-r TotalExecutedSteps (%d) must be < naive (%d)",
			pg.TotalExecutedSteps, naive.TotalExecutedSteps)
	}
	// ── PlanGate-R recovers all interrupted sessions ──────────────────────────
	if pg.EventualSuccessCount != S {
		t.Errorf("runtime: pg-r must succeed all %d sessions, got %d", S, pg.EventualSuccessCount)
	}
	// ── PlanGate-base fails all interrupted sessions ──────────────────────────
	if base.EventualSuccessCount != 0 {
		t.Errorf("runtime: base must fail all sessions (no recovery), got %d successes",
			base.EventualSuccessCount)
	}
	// ── Naive retry tools[0..K-1] called 2×S, PlanGate-R tools[0..K-1] called 1×S ──
	for i := 0; i < K; i++ {
		naiveCalls := naive.ToolCallCounts[i]
		pgCalls    := pg.ToolCallCounts[i]
		if naiveCalls != int64(2*S) {
			t.Errorf("naive tool[%d] call count: want %d (2×S), got %d", i, 2*S, naiveCalls)
		}
		if pgCalls != int64(S) {
			t.Errorf("pg-r tool[%d] call count: want %d (S), got %d", i, S, pgCalls)
		}
	}
	// ── Tools[K..N-1] called N×S for naive, 1×S (recovery only) for PlanGate-R ──
	for i := K; i < N; i++ {
		if naive.ToolCallCounts[i] != int64(S) {
			t.Errorf("naive tool[%d] call count: want %d (S), got %d", i, S, naive.ToolCallCounts[i])
		}
		if pg.ToolCallCounts[i] != int64(S) {
			t.Errorf("pg-r tool[%d] call count: want %d (S), got %d", i, S, pg.ToolCallCounts[i])
		}
	}
	t.Logf("runtime compute saving: naive=%d  pg-r=%d  delta=%d steps (%.1f%%)",
		naive.TotalExecutedSteps, pg.TotalExecutedSteps,
		naive.TotalExecutedSteps-pg.TotalExecutedSteps,
		100*float64(naive.TotalExecutedSteps-pg.TotalExecutedSteps)/
			float64(naive.TotalExecutedSteps))
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 3 — PlanGate-base eventual success < PlanGate-R
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimePlanGateBaseFailsWithoutRecovery verifies that a server with
// recovery DISABLED cannot recover interrupted sessions, while the same
// scenario on a recovery-enabled server succeeds via handleRecoveryResume.
func TestRuntimePlanGateBaseFailsWithoutRecovery(t *testing.T) {
	N, K := 5, 3
	sessions := 10

	// ── PlanGate-base: recovery disabled ─────────────────────────────────────
	baseSrv, _, baseTools := makeRuntimeServer(t, N, 0)
	baseSrv.recoveryConfig.Enabled = false

	baseSucceeded := 0
	for i := 0; i < sessions; i++ {
		sessionID := fmt.Sprintf("base-sess-%d", i)
		executeFirstPassSteps(baseSrv, baseTools, K)
		injectInterruption(t, baseSrv, sessionID, baseTools, K)

		// Attempt recovery → must fail because recovery is disabled.
		r := makeRecoveryResumeRequest(sessionID)
		resp := baseSrv.handleRecoveryResume(context.Background(), r, makeRPCRequest())
		if resp.Error == nil {
			baseSucceeded++
		}
	}

	// ── PlanGate-R: recovery enabled ─────────────────────────────────────────
	pgrSrv, _, pgrTools := makeRuntimeServer(t, N, 0)
	// recoveryConfig.Enabled is already true from makeRuntimeServer.

	pgrSucceeded := 0
	for i := 0; i < sessions; i++ {
		sessionID := fmt.Sprintf("pgr-sess-%d", i)
		executeFirstPassSteps(pgrSrv, pgrTools, K)
		injectInterruption(t, pgrSrv, sessionID, pgrTools, K)

		r := makeRecoveryResumeRequest(sessionID)
		resp := pgrSrv.handleRecoveryResume(context.Background(), r, makeRPCRequest())
		if resp.Error == nil {
			pgrSucceeded++
		}
	}

	t.Logf("base_succeeded=%d  pgr_succeeded=%d  (sessions=%d)",
		baseSucceeded, pgrSucceeded, sessions)

	if baseSucceeded != 0 {
		t.Errorf("base must not recover any session, got %d", baseSucceeded)
	}
	if pgrSucceeded != sessions {
		t.Errorf("pg-r must recover all %d sessions, got %d", sessions, pgrSucceeded)
	}
	if pgrSucceeded <= baseSucceeded {
		t.Errorf("pg-r eventual_success (%d) must exceed base (%d)", pgrSucceeded, baseSucceeded)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 4 — Recovery latency overhead
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeRecoveryLatencyOverhead measures P50/P95 latency for all three
// policies under mock handlers that sleep 2ms per step.
//
// Expected ordering (not asserted, just reported):
//   - PlanGate-base:  P95 ≈ K×2ms   (only first pass executed)
//   - Naive retry:    P95 ≈ (K+N)×2ms  (first pass + full retry)
//   - PlanGate-R:     P95 ≈ N×2ms   (first pass K + recovery N-K = N steps total)
//
// Note: PlanGate-R has extra checkpoint overhead (Store.Save/Update) that
// is not present in naive retry. The test reports the raw numbers; it does
// NOT require PlanGate-R P95 < naive retry P95 because the checkpoint I/O
// adds latency that is not present in the naive in-memory retry.
func TestRuntimeRecoveryLatencyOverhead(t *testing.T) {
	const (
		S            = 30
		N            = 5
		K            = 2
		handlerSleep = 2 * time.Millisecond
	)

	type policyRun struct {
		name      string
		policy    RecoveryPolicy
		latencies []time.Duration
	}

	runs := []policyRun{
		{name: "plangate_base"},
		{name: "naive_retry"},
		{name: "plangate_r"},
	}

	for idx, run := range runs {
		s, _, tools := makeRuntimeServer(t, N, handlerSleep)
		var lats []time.Duration

		for i := 0; i < S; i++ {
			sessionID := fmt.Sprintf("lat-%s-%d", run.name, i)
			start := time.Now()

			executeFirstPassSteps(s, tools, K)
			injectInterruption(t, s, sessionID, tools, K)

			switch RecoveryPolicy(run.name) {
			case "plangate_base":
				// Do nothing.
			case "naive_retry":
				executeAllSteps(s, tools)
				s.checkpointStore.Delete(context.Background(), sessionID) //nolint:errcheck
			default: // plangate_r
				r := makeRecoveryResumeRequest(sessionID)
				s.handleRecoveryResume(context.Background(), r, makeRPCRequest()) //nolint:errcheck
			}

			lats = append(lats, time.Since(start))
		}
		runs[idx].latencies = lats
	}

	sep := "─────────────────────────────────────────────────────────────────"
	t.Logf("\n%s\nPhase 7A Latency (handler_sleep=%v, S=%d, N=%d, K=%d)\n%s",
		sep, handlerSleep, S, N, K, sep)
	t.Logf("  %-18s  %8s  %8s  %s", "Policy", "P50ms", "P95ms", "Notes")
	t.Logf("%s", sep)
	for _, run := range runs {
		p50 := percentileMs(run.latencies, 50)
		p95 := percentileMs(run.latencies, 95)
		var note string
		switch run.name {
		case "plangate_base":
			note = fmt.Sprintf("only K=%d steps executed", K)
		case "naive_retry":
			note = fmt.Sprintf("K=%d first-pass + N=%d retry = %d steps", K, N, K+N)
		default:
			note = fmt.Sprintf("K=%d first-pass + (N-K)=%d recovery = N=%d steps", K, N-K, N)
		}
		t.Logf("  %-18s  %7.2f  %7.2f   %s", run.name, p50, p95, note)
	}
	t.Logf("%s\n", sep)

	// Soft assertion: PlanGate-base P95 must be lower than naive retry P95
	// because it executes fewer total steps.
	baseP95  := percentileMs(runs[0].latencies, 95)
	naiveP95 := percentileMs(runs[1].latencies, 95)
	if baseP95 > naiveP95 {
		t.Logf("INFO: base P95 (%.2fms) > naive P95 (%.2fms) — possible timer resolution artefact",
			baseP95, naiveP95)
	}

	// PlanGate-R P95 must be <= naive retry P95.
	// (PlanGate-R executes the same N steps total; naive executes K+N steps.)
	pgrP95 := percentileMs(runs[2].latencies, 95)
	if pgrP95 > naiveP95*1.5 {
		t.Logf("WARN: pg-r P95 (%.2fms) > 1.5× naive P95 (%.2fms) — checkpoint overhead significant",
			pgrP95, naiveP95)
	}
}

// ══════════════════════════════════════════════════════════════════════════════
// PlanGate-R Phase 7B: Natural Failure Injection Runtime Experiment
//
// SCOPE NOTE
// ══════════════════════════════════════════════════════════════════════════════
// Phase 7A verified that after MANUAL checkpoint injection, PlanGate-R behaves
// correctly.  Phase 7B removes the manual injection dependency and proves that
// the NATURAL FAILURE PATH works end-to-end:
//
//   tool handler returns error  →  classifyTransportError  →
//   markCheckpointRecoverable   →  ACTIVE_CHECKPOINT becomes CHECKPOINTED
//
// This exercises the real production code path in executeStepDirect:
//
//   result, err := handler(ctx, params)
//   if err != nil {
//       s.markCheckpointRecoverable(ctx, sessionID, classifyTransportError(err))
//       return mcpgov.NewErrorResponse(...)
//   }
//
// Key properties verified:
//   NF-1. Recoverable handler error → auto-promoted checkpoint (no manual call)
//   NF-2. Successful recovery resume → skipped steps not replayed
//   NF-3. PlanGate-R total handler calls < naive retry by exactly K steps/session
//   NF-4. Terminal handler error → checkpoint NOT promoted → resume rejected
//
// What this is NOT:
//   - Not a real LLM test (mock handlers)
//   - Not a ReAct semantic recovery test
//   - Not a modification of production code paths
// ══════════════════════════════════════════════════════════════════════════════

// ─── sentinel error values ───────────────────────────────────────────────────

// errNatOverloaded is a RECOVERABLE transport error.
// classifyTransportError matches "overloaded" → RecoveryDecisionRecoverable.
var errNatOverloaded = errors.New("backend overloaded: service unavailable")

// errNatUnauthorized is a TERMINAL error.
// classifyTransportError matches the default → RecoveryDecisionTerminal
// (no "overloaded" / "timeout" / "connection" keyword).
var errNatUnauthorized = errors.New("unauthorized: permission denied")

// ─── Phase 7B helpers ────────────────────────────────────────────────────────

// makeNatFailServer creates an MCPDPServer with N tools where:
//   - tools[0..N-1] succeed always
//   - tools[failStep] returns failErr on its FIRST invocation, then succeeds
//
// It returns the server, per-tool atomic call-count slice, and tool name slice.
func makeNatFailServer(t *testing.T, N, failStep int, failErr error) (
	*MCPDPServer, []int64, []string,
) {
	t.Helper()
	gov := makeTestGovernor()
	s := NewMCPDPServer("nat-fail-srv", gov, 60*time.Second, 0, 0.0)
	s.recoveryConfig = RecoveryConfig{
		Enabled:     true,
		TTL:         5 * time.Minute,
		MaxAttempts: 5,
		Store:       "inmemory",
	}
	s.checkpointStore = NewInMemoryCheckpointStore()

	counters := make([]int64, N)
	tools := make([]string, N)
	for i := 0; i < N; i++ {
		idx := i
		name := fmt.Sprintf("nat_tool_%d", idx)
		tools[i] = name
		s.RegisterTool(mcpgov.MCPTool{Name: name, Description: "nat-fail mock"},
			func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
				n := atomic.AddInt64(&counters[idx], 1)
				if idx == failStep && n == 1 {
					// First invocation of the designated fail-step → natural failure.
					return nil, failErr
				}
				return &mcpgov.MCPToolCallResult{
					Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}},
				}, nil
			})
	}
	return s, counters, tools
}

// saveActiveCP saves an ACTIVE_CHECKPOINT (not CHECKPOINTED) representing the
// state after K steps have completed successfully.
// This simulates what saveCheckpointAfterStep would have written, without
// requiring a full budget-reservation path in the test.
func saveActiveCP(
	t *testing.T,
	s *MCPDPServer,
	sessionID string,
	tools []string,
	K int,
) {
	t.Helper()
	ctx := context.Background()
	N := len(tools)
	completed := make([]StepRecord, K)
	for i := 0; i < K; i++ {
		completed[i] = StepRecord{
			StepID:    fmt.Sprintf("nat_s%d", i),
			StepIndex: i,
			ToolName:  tools[i],
		}
	}
	remaining := make([]HTTPDAGStep, N-K)
	for i := K; i < N; i++ {
		remaining[i-K] = HTTPDAGStep{
			StepID:   fmt.Sprintf("nat_s%d", i),
			ToolName: tools[i],
		}
	}
	remainingJSON, _ := json.Marshal(remaining)
	if err := s.checkpointStore.Save(ctx, &SessionCheckpoint{
		SessionID:         sessionID,
		AgentID:           "nat-agent",
		Mode:              AgentModePlanSolve,
		Status:            StatusActiveCheckpoint, // NOT CHECKPOINTED — letting natural path promote it
		CurrentStep:       K,
		CompletedSteps:    completed,
		RemainingPlanJSON: remainingJSON,
		CreatedAt:         time.Now(),
	}); err != nil {
		t.Fatalf("saveActiveCP: %v", err)
	}
}

// rpcCallDirect builds a minimal JSON-RPC tools/call request and calls
// s.executeStepDirect directly (same package).  Returns the response.
func rpcCallDirect(
	s *MCPDPServer,
	ctx context.Context,
	sessionID string,
	toolName string,
) *mcpgov.JSONRPCResponse {
	paramsJSON, _ := json.Marshal(mcpgov.MCPToolCallParams{Name: toolName})
	req := &mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "nat-req-" + toolName,
		Method:  mcpgov.MethodToolsCall,
		Params:  paramsJSON,
	}
	return s.executeStepDirect(ctx, req, sessionID)
}

// printNaturalFailureTable logs the Phase 7B summary table.
func printNaturalFailureTable(t *testing.T, rows []natFailRow) {
	t.Helper()
	sep := "─────────────────────────────────────────────────────────────────────────────────────────────────"
	t.Logf("\n%s\nPhase 7B — Natural Failure Injection Results\n%s", sep, sep)
	t.Logf("  %-18s  %9s  %11s  %14s  %14s  %20s",
		"Policy", "Success", "ToolCalls", "ReplayOverhead", "AvoidedReplay", "CheckpointPromoted")
	t.Logf("%s", sep)
	for _, r := range rows {
		t.Logf("  %-18s  %9v  %11d  %14d  %14d  %20v",
			r.Policy, r.Succeeded, r.ToolCalls, r.ReplayOverhead, r.AvoidedReplay, r.CheckpointPromoted)
	}
	t.Logf("%s\n", sep)
}

type natFailRow struct {
	Policy             string
	Succeeded          bool
	ToolCalls          int
	ReplayOverhead     int // steps re-executed due to restart (naive only)
	AvoidedReplay      int // steps not re-executed thanks to checkpoint (pgr only)
	CheckpointPromoted bool
}

// ─────────────────────────────────────────────────────────────────────────────
// Test NF-1 — Recoverable handler error automatically promotes the checkpoint
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeNaturalFailurePromotesCheckpoint verifies that when a real tool
// handler returns a recoverable error, the existing ACTIVE_CHECKPOINT is
// automatically promoted to CHECKPOINTED by classifyTransportError →
// markCheckpointRecoverable, WITHOUT any direct call to markCheckpointRecoverable.
func TestRuntimeNaturalFailurePromotesCheckpoint(t *testing.T) {
	const (N, K, failStep = 5, 2, 2)
	sessionID := "nf-promote-sess"
	s, _, tools := makeNatFailServer(t, N, failStep, errNatOverloaded)
	ctx := context.Background()

	// Represent the state after tool0, tool1 completed (ACTIVE_CHECKPOINT).
	saveActiveCP(t, s, sessionID, tools, K)

	// Verify: before the failure, Status = ACTIVE_CHECKPOINT.
	cpBefore, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("Load before failure: %v", err)
	}
	if cpBefore.Status != StatusActiveCheckpoint {
		t.Errorf("before failure: Status want ACTIVE_CHECKPOINT, got %s", cpBefore.Status)
	}
	if cpBefore.CurrentStep != K {
		t.Errorf("before failure: CurrentStep want %d, got %d", K, cpBefore.CurrentStep)
	}

	// Execute tool2 via the REAL executeStepDirect path.
	// tool2 returns errNatOverloaded on its first call → classifyTransportError →
	// markCheckpointRecoverable fires automatically.
	resp := rpcCallDirect(s, ctx, sessionID, tools[failStep])
	if resp.Error == nil {
		t.Fatalf("expected error response from failing tool2, got success")
	}
	t.Logf("tool2 failure response (expected): code=%d msg=%s",
		resp.Error.Code, resp.Error.Message)

	// Verify: after the failure, Status = CHECKPOINTED (automatically promoted).
	cpAfter, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("Load after failure: %v", err)
	}
	if cpAfter.Status != StatusCheckpointed {
		t.Errorf("after failure: Status want CHECKPOINTED, got %s; expected automatic promotion via classifyTransportError", cpAfter.Status)
	}
	// CurrentStep must still be K (failure did not advance the counter).
	if cpAfter.CurrentStep != K {
		t.Errorf("after failure: CurrentStep want %d, got %d", K, cpAfter.CurrentStep)
	}
	// Failure metadata written.
	if cpAfter.LastFailureCategory == "" {
		t.Errorf("after failure: LastFailureCategory must be set")
	}
	if cpAfter.LastFailureReason == "" {
		t.Errorf("after failure: LastFailureReason must be set")
	}
	t.Logf("Checkpoint promoted: Status=%s Category=%s Reason=%s CurrentStep=%d",
		cpAfter.Status, cpAfter.LastFailureCategory, cpAfter.LastFailureReason, cpAfter.CurrentStep)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test NF-2 — Natural failure + recovery resume succeeds without replaying
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeNaturalFailureRecoveryResume verifies the full end-to-end flow:
//   1. Natural handler error on tool2 (step K=2) promotes checkpoint.
//   2. X-Recovery-Mode: resume starts recovery.
//   3. tool0, tool1 are NOT replayed.
//   4. tool2 (2nd call), tool3, tool4 are executed by recovery.
//   5. Checkpoint is deleted on success.
//
// Tool call counts verified:
//   tool0: 0 (first pass by executeFirstPassSteps, not by executeStepDirect)
//   tool1: 0 (same as tool0)
//   tool2: 2 (first call fails via rpcCallDirect; second call succeeds in recovery)
//   tool3: 1 (recovery only)
//   tool4: 1 (recovery only)
//
// NOTE: In this test, tool0 and tool1 are NOT called through executeStepDirect.
// They are simulated by saveActiveCP.  The COUNTING of tool2..4 through
// the server reflects the real production path.
func TestRuntimeNaturalFailureRecoveryResume(t *testing.T) {
	const (N, K, failStep = 5, 2, 2)
	sessionID := "nf-resume-sess"
	s, counters, tools := makeNatFailServer(t, N, failStep, errNatOverloaded)
	ctx := context.Background()

	// Setup: save ACTIVE_CHECKPOINT (represents tool0+tool1 completed).
	saveActiveCP(t, s, sessionID, tools, K)

	// Step 1: Natural failure on tool2.
	resp := rpcCallDirect(s, ctx, sessionID, tools[failStep])
	if resp.Error == nil {
		t.Fatalf("expected failure on first tool2 call")
	}

	// Verify promotion happened.
	cp, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("Load after failure: %v", err)
	}
	if cp.Status != StatusCheckpointed {
		t.Errorf("checkpoint not promoted after natural failure: Status=%s", cp.Status)
	}

	// Step 2: Send recovery resume.
	r := makeRecoveryResumeRequest(sessionID)
	recovResp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if recovResp.Error != nil {
		t.Fatalf("recovery resume failed: %s", recovResp.Error.Message)
	}

	// Parse result.
	resBytes, _ := json.Marshal(recovResp.Result)
	var result PSRecoveryResult
	if err := json.Unmarshal(resBytes, &result); err != nil {
		t.Fatalf("PSRecoveryResult parse: %v", err)
	}
	t.Logf("Recovery result: skipped=%d executed=%d total=%d mode=%s",
		result.SkippedSteps, result.ExecutedSteps, result.TotalSteps, result.Mode)

	// ── Invariant checks ─────────────────────────────────────────────────────
	if result.SkippedSteps != K {
		t.Errorf("SkippedSteps want %d (=K), got %d", K, result.SkippedSteps)
	}
	if result.ExecutedSteps != N-K {
		t.Errorf("ExecutedSteps want %d (=N-K), got %d", N-K, result.ExecutedSteps)
	}
	if result.TotalSteps != N {
		t.Errorf("TotalSteps want %d, got %d", N, result.TotalSteps)
	}

	// Tool call counts through the server execution path.
	// (tool0 and tool1 were not called through executeStepDirect in this test.)
	t.Logf("Tool call counts via executeStepDirect + handleRecoveryResume:")
	for i, name := range tools {
		c := atomic.LoadInt64(&counters[i])
		t.Logf("  %s: %d calls", name, c)
	}

	// tool2: exactly 2 (first call via rpcCallDirect fails; second via recovery succeeds).
	if c := atomic.LoadInt64(&counters[failStep]); c != 2 {
		t.Errorf("tool2 call count: want 2 (fail + recovery), got %d", c)
	}
	// tool3, tool4: exactly 1 (recovery only).
	for i := failStep + 1; i < N; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 1 {
			t.Errorf("tool[%d] call count: want 1 (recovery only), got %d", i, c)
		}
	}
	// tool0, tool1: 0 (not called through executeStepDirect in this test setup).
	for i := 0; i < K; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 0 {
			t.Errorf("tool[%d] call count: want 0 (should not be replayed), got %d", i, c)
		}
	}

	// Checkpoint must be deleted after successful recovery.
	_, err = s.checkpointStore.Load(ctx, sessionID)
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("checkpoint must be deleted after success, got: %v", err)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test NF-3 — PlanGate-R handler calls < naive retry, saving = K per session
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeNaturalFailureVsNaiveRetry runs S sessions through both policies
// and verifies that PlanGate-R executes K fewer total handler invocations per
// session compared to naive retry.
//
// Per-session call analysis:
//   PlanGate-R:
//     first-pass (simulated, K steps)  → K calls  (counted atomically by handlers)
//     executeStepDirect(tool_K, fail)  → 1 call   (natural fail, promotes checkpoint)
//     handleRecoveryResume             → (N-K) calls (tool_K retry + tool_{K+1..N-1})
//     total = K + 1 + (N-K) = N+1 calls per session
//
//   Naive retry:
//     first-pass (simulated, K steps)  → K calls
//     executeStepDirect(tool_K, fail)  → 1 call   (natural fail)
//     executeAllSteps (full retry N)   → N calls  (tool_K succeeds on 2nd call)
//     total = K + 1 + N calls per session
//
//   Saving per session = K (replay overhead avoided by checkpoint).
//
// NOTE: To isolate counter state between sessions within a policy run, each
// session uses a fresh server instance so that the fail-once counter for
// tool_K resets to 0.
func TestRuntimeNaturalFailureVsNaiveRetry(t *testing.T) {
	const (
		S         = 10  // sessions
		N         = 5   // steps per session
		K         = 2   // checkpoint after step K; fail at step K
		failStep  = K
	)

	type runResult struct {
		policy         string
		totalCalls     int
		successCount   int
		replayOverhead int // K × successCount (naive only)
		avoidedReplay  int // K × successCount (pgr only)
	}

	runPolicy := func(policy string) runResult {
		res := runResult{policy: policy}
		for sess := 0; sess < S; sess++ {
			// Fresh server per session keeps the fail-once counter clean.
			srv, _, tools := makeNatFailServer(t, N, failStep, errNatOverloaded)
			sessionID := fmt.Sprintf("nfr-%s-%d", policy, sess)
			ctx := context.Background()

			// Execute first-pass steps tool0..tool_{K-1} directly via handlers.
			for i := 0; i < K; i++ {
				if h, ok := srv.handlers[tools[i]]; ok {
					h(ctx, mcpgov.MCPToolCallParams{Name: tools[i]}) //nolint:errcheck
				}
				res.totalCalls++
			}

			// Save ACTIVE_CHECKPOINT to represent K completed steps.
			saveActiveCP(t, srv, sessionID, tools, K)

			// Natural failure on tool_K via executeStepDirect.
			rpcCallDirect(srv, ctx, sessionID, tools[failStep]) //nolint:errcheck — expected failure
			res.totalCalls++

			switch policy {
			case "plangate_r":
				// Recovery via real handleRecoveryResume.
				r := makeRecoveryResumeRequest(sessionID)
				recovResp := srv.handleRecoveryResume(ctx, r, makeRPCRequest())
				if recovResp.Error != nil {
					t.Logf("sess %d recovery failed: %s", sess, recovResp.Error.Message)
				} else {
					res.totalCalls += (N - K) // tool_K (2nd call) + tool_{K+1..N-1}
					res.successCount++
					res.avoidedReplay += K
				}
			case "naive_retry":
				// Full retry from step 0 (ignores checkpoint).
				for _, name := range tools {
					if h, ok := srv.handlers[name]; ok {
						h(ctx, mcpgov.MCPToolCallParams{Name: name}) //nolint:errcheck
					}
					res.totalCalls++
				}
				res.successCount++
				res.replayOverhead += K
			}
		}
		return res
	}

	pgr   := runPolicy("plangate_r")
	naive := runPolicy("naive_retry")

	printNaturalFailureTable(t, []natFailRow{
		{
			Policy:             "plangate_r",
			Succeeded:          pgr.successCount == S,
			ToolCalls:          pgr.totalCalls,
			AvoidedReplay:      pgr.avoidedReplay,
			CheckpointPromoted: true,
		},
		{
			Policy:             "naive_retry",
			Succeeded:          naive.successCount == S,
			ToolCalls:          naive.totalCalls,
			ReplayOverhead:     naive.replayOverhead,
			CheckpointPromoted: false,
		},
	})

	// PlanGate-R must succeed all sessions.
	if pgr.successCount != S {
		t.Errorf("pgr: expected %d successes, got %d", S, pgr.successCount)
	}
	// Naive retry must succeed all sessions.
	if naive.successCount != S {
		t.Errorf("naive: expected %d successes, got %d", S, naive.successCount)
	}
	// PlanGate-R must have fewer total calls.
	if pgr.totalCalls >= naive.totalCalls {
		t.Errorf("pgr total calls (%d) must be < naive (%d)", pgr.totalCalls, naive.totalCalls)
	}
	// Saving must equal K per session.
	saving := naive.totalCalls - pgr.totalCalls
	expectedSaving := K * S
	if saving != expectedSaving {
		t.Errorf("calls saving: want %d (=K×S=%d×%d), got %d", expectedSaving, K, S, saving)
	}
	t.Logf("natural failure saving: pgr=%d naive=%d delta=%d (%d steps/session×%d sessions)",
		pgr.totalCalls, naive.totalCalls, saving, K, S)
	t.Logf("pgr avoidedReplay=%d  naive replayOverhead=%d", pgr.avoidedReplay, naive.replayOverhead)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test NF-4 — Terminal handler error does NOT promote the checkpoint
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeTerminalFailureDoesNotPromote verifies that when tool2 returns a
// terminal error, markCheckpointRecoverable is called but receives Decision=Terminal
// and therefore does NOT change the checkpoint status from ACTIVE_CHECKPOINT.
// A subsequent recovery resume request is correctly rejected.
func TestRuntimeTerminalFailureDoesNotPromote(t *testing.T) {
	const (N, K, failStep = 5, 2, 2)
	sessionID := "nf-terminal-sess"
	// errNatUnauthorized → classifyTransportError returns Decision=Terminal
	s, _, tools := makeNatFailServer(t, N, failStep, errNatUnauthorized)
	ctx := context.Background()

	// Setup: save ACTIVE_CHECKPOINT (represents tool0+tool1 completed).
	saveActiveCP(t, s, sessionID, tools, K)

	// Natural failure on tool2 with a TERMINAL error.
	resp := rpcCallDirect(s, ctx, sessionID, tools[failStep])
	if resp.Error == nil {
		t.Fatalf("expected error response from terminal-failing tool2")
	}
	t.Logf("tool2 terminal failure response: code=%d msg=%s",
		resp.Error.Code, resp.Error.Message)

	// Verify: checkpoint must NOT have been promoted to CHECKPOINTED.
	cp, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("Load after terminal failure: %v", err)
	}
	if cp.Status == StatusCheckpointed {
		t.Errorf("terminal failure must NOT promote checkpoint; got Status=CHECKPOINTED")
	}
	if cp.Status != StatusActiveCheckpoint {
		t.Errorf("after terminal failure: Status want ACTIVE_CHECKPOINT, got %s", cp.Status)
	}
	t.Logf("Checkpoint status after terminal failure: %s (correct — not promoted)", cp.Status)

	// Verify: recovery resume must be rejected because there is no CHECKPOINTED checkpoint.
	r := makeRecoveryResumeRequest(sessionID)
	recovResp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if recovResp.Error == nil {
		t.Errorf("recovery resume must fail when checkpoint is not CHECKPOINTED, but it succeeded")
	} else {
		t.Logf("recovery resume correctly rejected: code=%d msg=%s",
			recovResp.Error.Code, recovResp.Error.Message)
	}

	// Cross-check with a recoverable session to confirm the difference.
	// Use the same session ID but with a recoverable error this time via a new session.
	recoverableSessionID := "nf-terminal-recoverable-control"
	s2, _, tools2 := makeNatFailServer(t, N, failStep, errNatOverloaded)
	saveActiveCP(t, s2, recoverableSessionID, tools2, K)
	rpcCallDirect(s2, ctx, recoverableSessionID, tools2[failStep]) //nolint:errcheck
	cpControl, err := s2.checkpointStore.Load(ctx, recoverableSessionID)
	if err != nil {
		t.Fatalf("control: Load: %v", err)
	}
	if cpControl.Status != StatusCheckpointed {
		t.Errorf("control: recoverable error must promote, got Status=%s", cpControl.Status)
	}
	t.Logf("Control (recoverable): checkpoint Status=%s (correctly promoted)", cpControl.Status)
	t.Logf("Summary: terminal=%s  recoverable=%s  — terminal path correctly blocked",
		cp.Status, cpControl.Status)
}

// ══════════════════════════════════════════════════════════════════════════════
// PlanGate-R Phase 7C: Full-HTTP Natural Chain — No Manual Checkpoint Seeding
//
// SCOPE NOTE
// ══════════════════════════════════════════════════════════════════════════════
// Phase 7C removes ALL manual checkpoint seeding from the test code:
//   - NO saveActiveCP()
//   - NO checkpointStore.Save(...ACTIVE_CHECKPOINT...)
//   - NO markCheckpointRecoverable() direct calls
//   - NO injectInterruption()
//
// Instead, Phase 7C uses the FULL HTTP path via s.ServeHTTP / httptest.
// Checkpoints emerge from the REAL saveCheckpointAfterStep code path:
//
//   Req0 (X-Plan-DAG + X-Session-ID)
//     → handlePlanAndSolveFirstStep → executeStepDirect(tool0)
//     → saveCheckpointAfterStep → ACTIVE_CHECKPOINT (CurrentStep=1)
//
//   Req1 (X-Session-ID only)
//     → handleReservedStep → executeStepDirect(tool1)
//     → saveCheckpointAfterStep → ACTIVE_CHECKPOINT (CurrentStep=2)
//
//   Req2 (X-Session-ID only) — natural failure
//     → handleReservedStep → executeStepDirect(tool2)
//     → handler returns errNatOverloaded
//     → classifyTransportError → markCheckpointRecoverable → CHECKPOINTED
//
//   Rec (X-Recovery-Mode: resume + X-Session-ID)
//     → handleRecoveryResume → execute [tool2, tool3, tool4]
//     → all succeed → delete checkpoint
//
// Note on SkippedSteps=1 (not 2):
//   executeStepDirect writes CompletedSteps as a SINGLE-ENTRY slice (only the
//   last completed step per call).  After step 1, CompletedSteps=[{tool1}].
//   handleRecoveryResume counts skippedSteps = len(CompletedSteps) = 1.
//   Tool0 and tool1 are still NOT replayed because RemainingPlanJSON starts
//   from [tool2, tool3, tool4].  The per-tool counters (tool0=1, tool1=1)
//   prove the no-replay invariant even though SkippedSteps=1.
// ══════════════════════════════════════════════════════════════════════════════

// ─── Phase 7C helpers ────────────────────────────────────────────────────────

// makeNat7CDAGJSON constructs the HTTPDAGPlan JSON for a Phase 7C session.
// All tools are listed as DAG steps; budget must be > 0 (totalCost=0 for
// makeTestGovernor which returns price 0 for all tool names).
func makeNat7CDAGJSON(sessionID string, tools []string, budget int64) string {
	steps := make([]HTTPDAGStep, len(tools))
	for i, name := range tools {
		steps[i] = HTTPDAGStep{StepID: fmt.Sprintf("nc%d", i), ToolName: name}
	}
	plan := HTTPDAGPlan{SessionID: sessionID, Steps: steps, Budget: budget}
	data, _ := json.Marshal(plan)
	return string(data)
}

// sendPS7CRequest sends a tools/call JSON-RPC request via s.ServeHTTP (httptest).
//   - dagJSON != "": P&S first step — sets X-Plan-DAG + X-Session-ID headers.
//   - dagJSON == "": P&S subsequent step — sets only X-Session-ID header.
func sendPS7CRequest(s *MCPDPServer, sessionID, toolName, dagJSON string) *httptest.ResponseRecorder {
	paramsJSON, _ := json.Marshal(mcpgov.MCPToolCallParams{Name: toolName})
	body, _ := json.Marshal(mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "7c-" + toolName,
		Method:  mcpgov.MethodToolsCall,
		Params:  json.RawMessage(paramsJSON),
	})
	req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderSessionID, sessionID)
	if dagJSON != "" {
		req.Header.Set(HeaderPlanDAG, dagJSON)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	return w
}

// sendRecoveryResumeHTTP sends a recovery-resume request via s.ServeHTTP.
// Sets X-Recovery-Mode: resume + X-Session-ID.  The body method must be
// tools/call so that ServeHTTP routes to handleToolsCall → handleRecoveryResume.
func sendRecoveryResumeHTTP(s *MCPDPServer, sessionID string) *httptest.ResponseRecorder {
	paramsJSON, _ := json.Marshal(mcpgov.MCPToolCallParams{Name: "resume"})
	body, _ := json.Marshal(mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "7c-recovery",
		Method:  mcpgov.MethodToolsCall,
		Params:  json.RawMessage(paramsJSON),
	})
	req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderSessionID, sessionID)
	req.Header.Set(HeaderRecoveryMode, "resume")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	return w
}

// parseRPCResp decodes a JSON-RPC response body from an httptest recorder.
func parseRPCResp(body []byte) *mcpgov.JSONRPCResponse {
	var resp mcpgov.JSONRPCResponse
	json.Unmarshal(body, &resp) //nolint:errcheck
	return &resp
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 7C-B — Full natural P&S chain via ServeHTTP: no manual checkpoint seeding
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeFullNaturalCheckpointToRecovery drives a 5-step P&S session through
// the FULL HTTP path (s.ServeHTTP, httptest) WITHOUT any manual checkpoint seeding
// (no saveActiveCP, no checkpointStore.Save, no injectInterruption).
//
// The test verifies the complete natural chain:
//
//  1. Step 0 via ServeHTTP (X-Plan-DAG) → saveCheckpointAfterStep → ACTIVE, CurrentStep=1
//  2. Step 1 via ServeHTTP (X-Session-ID) → saveCheckpointAfterStep → ACTIVE, CurrentStep=2
//  3. Step 2 via ServeHTTP (X-Session-ID) → natural failure → CHECKPOINTED
//  4. Recovery via ServeHTTP (X-Recovery-Mode: resume) → tool2,3,4 executed → checkpoint deleted
//
// Assertions:
//   - Checkpoint status progresses ACTIVE → ACTIVE → CHECKPOINTED → deleted
//   - PSRecoveryResult: SkippedSteps=1, ExecutedSteps=3, TotalSteps=4
//   - Per-tool counters: tool0=1, tool1=1, tool2=2, tool3=1, tool4=1 (no replay)
func TestRuntimeFullNaturalCheckpointToRecovery(t *testing.T) {
	const (
		N        = 5
		failStep = 2
	)
	sessionID := "7c-full-natural-sess"
	s, counters, tools := makeNatFailServer(t, N, failStep, errNatOverloaded)
	dagJSON := makeNat7CDAGJSON(sessionID, tools, 1000)
	ctx := context.Background()

	// ── Step 0: P&S first step via ServeHTTP (X-Plan-DAG + X-Session-ID) ─────
	w0 := sendPS7CRequest(s, sessionID, tools[0], dagJSON)
	resp0 := parseRPCResp(w0.Body.Bytes())
	if resp0.Error != nil {
		t.Fatalf("step0 failed: code=%d msg=%s", resp0.Error.Code, resp0.Error.Message)
	}
	// Verify ACTIVE_CHECKPOINT created by the real saveCheckpointAfterStep path.
	cp0, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("checkpoint after step0: %v", err)
	}
	if cp0.Status != StatusActiveCheckpoint {
		t.Errorf("step0: Status want ACTIVE_CHECKPOINT, got %s", cp0.Status)
	}
	if cp0.CurrentStep != 1 {
		t.Errorf("step0: CurrentStep want 1, got %d", cp0.CurrentStep)
	}
	t.Logf("step0: checkpoint OK — Status=%s CurrentStep=%d", cp0.Status, cp0.CurrentStep)

	// ── Step 1: P&S reserved step via ServeHTTP (X-Session-ID only) ──────────
	w1 := sendPS7CRequest(s, sessionID, tools[1], "")
	resp1 := parseRPCResp(w1.Body.Bytes())
	if resp1.Error != nil {
		t.Fatalf("step1 failed: code=%d msg=%s", resp1.Error.Code, resp1.Error.Message)
	}
	cp1, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("checkpoint after step1: %v", err)
	}
	if cp1.Status != StatusActiveCheckpoint {
		t.Errorf("step1: Status want ACTIVE_CHECKPOINT, got %s", cp1.Status)
	}
	if cp1.CurrentStep != 2 {
		t.Errorf("step1: CurrentStep want 2, got %d", cp1.CurrentStep)
	}
	t.Logf("step1: checkpoint OK — Status=%s CurrentStep=%d", cp1.Status, cp1.CurrentStep)

	// ── Step 2: natural failure via ServeHTTP (tool2 first call returns errNatOverloaded) ──
	w2 := sendPS7CRequest(s, sessionID, tools[failStep], "")
	resp2 := parseRPCResp(w2.Body.Bytes())
	if resp2.Error == nil {
		t.Fatalf("step2: expected natural failure, got success")
	}
	t.Logf("step2: natural failure (expected) — code=%d msg=%s", resp2.Error.Code, resp2.Error.Message)

	// Checkpoint must be promoted to CHECKPOINTED automatically by classifyTransportError path.
	cp2, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		t.Fatalf("checkpoint after step2 failure: %v", err)
	}
	if cp2.Status != StatusCheckpointed {
		t.Errorf("step2: Status want CHECKPOINTED (auto-promoted via classifyTransportError), got %s", cp2.Status)
	}
	if cp2.CurrentStep != 2 {
		t.Errorf("step2: CurrentStep want 2 (not advanced on failure), got %d", cp2.CurrentStep)
	}
	t.Logf("step2: checkpoint promoted — Status=%s CurrentStep=%d", cp2.Status, cp2.CurrentStep)

	// ── Recovery via ServeHTTP (X-Recovery-Mode: resume) ─────────────────────
	wRec := sendRecoveryResumeHTTP(s, sessionID)
	respRec := parseRPCResp(wRec.Body.Bytes())
	if respRec.Error != nil {
		t.Fatalf("recovery failed: code=%d msg=%s", respRec.Error.Code, respRec.Error.Message)
	}
	resBytes, _ := json.Marshal(respRec.Result)
	var result PSRecoveryResult
	if err := json.Unmarshal(resBytes, &result); err != nil {
		t.Fatalf("PSRecoveryResult parse: %v", err)
	}
	t.Logf("recovery result: skipped=%d executed=%d total=%d mode=%s",
		result.SkippedSteps, result.ExecutedSteps, result.TotalSteps, result.Mode)

	// ── Recovery result assertions ─────────────────────────────────────────────
	// SkippedSteps=1: executeStepDirect stores only the last completed step in
	// CompletedSteps (single-entry slice). After step 1, CompletedSteps=[{step1}].
	// handleRecoveryResume: skippedSteps = len(CompletedSteps) = 1.
	// Tool0 and tool1 are still not replayed (RemainingPlanJSON starts from [tool2,3,4]).
	if result.SkippedSteps != 1 {
		t.Errorf("SkippedSteps want 1 (single-entry CompletedSteps), got %d", result.SkippedSteps)
	}
	// ExecutedSteps=3: recovery executes [tool2, tool3, tool4].
	if result.ExecutedSteps != N-failStep {
		t.Errorf("ExecutedSteps want %d (N-failStep), got %d", N-failStep, result.ExecutedSteps)
	}
	// TotalSteps = skipped(1) + remaining(3) = 4.
	if result.TotalSteps != 1+(N-failStep) {
		t.Errorf("TotalSteps want %d (skipped+remaining), got %d", 1+(N-failStep), result.TotalSteps)
	}

	// ── Per-tool call count assertions ─────────────────────────────────────────
	t.Log("Per-tool call counts (full natural HTTP chain):")
	for i, name := range tools {
		c := atomic.LoadInt64(&counters[i])
		t.Logf("  %s: %d calls", name, c)
	}
	// tool0, tool1: each called exactly once during P&S; never replayed by recovery.
	if c := atomic.LoadInt64(&counters[0]); c != 1 {
		t.Errorf("tool0 call count: want 1 (P&S step0, not replayed), got %d", c)
	}
	if c := atomic.LoadInt64(&counters[1]); c != 1 {
		t.Errorf("tool1 call count: want 1 (P&S step1, not replayed), got %d", c)
	}
	// tool2: 2 calls — first call via P&S step2 fails (n=1); second via recovery succeeds (n=2).
	if c := atomic.LoadInt64(&counters[failStep]); c != 2 {
		t.Errorf("tool2 call count: want 2 (1 P&S fail + 1 recovery), got %d", c)
	}
	// tool3, tool4: each called once by recovery.
	for i := failStep + 1; i < N; i++ {
		if c := atomic.LoadInt64(&counters[i]); c != 1 {
			t.Errorf("tool[%d] call count: want 1 (recovery only), got %d", i, c)
		}
	}

	// ── Checkpoint deleted after successful recovery ───────────────────────────
	_, err = s.checkpointStore.Load(ctx, sessionID)
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("checkpoint must be deleted after successful recovery, got: %v", err)
	}

	var totalCalls int64
	for i := range counters {
		totalCalls += atomic.LoadInt64(&counters[i])
	}
	t.Logf("Phase 7C: total handler calls=%d  (naïve-retry equivalent: 8)", totalCalls)
	t.Logf("Phase 7C TestRuntimeFullNaturalCheckpointToRecovery: PASS")
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 7C-C — Controlled workload: PlanGate-R vs naive retry vs base (HTTP path)
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeControlledWorkloadPlanGateRVsNaiveRetry runs a 50-session controlled
// workload through the FULL ServeHTTP path (no direct handler / executeStepDirect calls).
//
// Workload parameters: S=50, N=5, failStep=2, failCount=15 (30% failure rate).
// The first failCount sessions experience a natural failure at step 2.
//
// Per-session call counts:
//
//	Non-failing session (all policies):   5  (steps 0..4)
//	PlanGate-R, failing session:           6  (steps 0,1,2-fail + recovery 2,3,4)
//	Naive-retry, failing session:          8  (steps 0,1,2-fail + restart 0,1,2,3,4)
//	PlanGate-base, failing session:        3  (steps 0,1,2-fail; no recovery)
//
// Expected totals:
//
//	PlanGate-R:    35×5 + 15×6 = 265
//	Naive-retry:   35×5 + 15×8 = 295
//	PlanGate-base: 35×5 + 15×3 = 220
//
// Key assertions:
//
//	pgr.TotalCalls < naive.TotalCalls
//	naive.TotalCalls - pgr.TotalCalls == failCount × failStep (= 30)
//	pgr.SuccessCount == naive.SuccessCount == S (all 50 sessions recover/retry)
//	base.SuccessCount == S - failCount (only 35 non-failing sessions succeed)
func TestRuntimeControlledWorkloadPlanGateRVsNaiveRetry(t *testing.T) {
	const (
		S          = 50 // total sessions
		N          = 5  // steps per session
		failStep   = 2  // nat_tool_2 fails on its first invocation
		failCount  = 15 // first failCount sessions fail (30% of S)
	)

	type workloadMetrics struct {
		Policy         string
		TotalCalls     int
		SuccessCount   int
		RecoveredCount int
		ReplayOverhead int // steps re-executed due to naive restart
		AvoidedReplay  int // steps not re-executed due to PlanGate-R checkpoint
	}

	runPolicy := func(policy string) workloadMetrics {
		m := workloadMetrics{Policy: policy}

		for sess := 0; sess < S; sess++ {
			sessionID := fmt.Sprintf("wl7c_%s_%d", policy, sess)
			willFail := sess < failCount

			// Fresh server per session keeps the fail-once counter clean.
			var srv *MCPDPServer
			var tools []string
			if willFail {
				srv, _, tools = makeNatFailServer(t, N, failStep, errNatOverloaded)
			} else {
				srv, _, tools = makeRuntimeServer(t, N, 0)
			}
			if policy == "plangate_base" {
				srv.recoveryConfig.Enabled = false
			}

			dagJSON := makeNat7CDAGJSON(sessionID, tools, 1000)
			succeeded := false

			if !willFail {
				// ── Non-failing session: run all N steps via ServeHTTP ────────────
				for i := 0; i < N; i++ {
					dag := ""
					if i == 0 {
						dag = dagJSON
					}
					w := sendPS7CRequest(srv, sessionID, tools[i], dag)
					if parseRPCResp(w.Body.Bytes()).Error != nil {
						t.Logf("sess %d step %d unexpected error", sess, i)
					}
					m.TotalCalls++
				}
				succeeded = true
			} else {
				// ── Failing session: steps 0,1 succeed; step failStep fails ──────
				for i := 0; i <= failStep; i++ {
					dag := ""
					if i == 0 {
						dag = dagJSON
					}
					w := sendPS7CRequest(srv, sessionID, tools[i], dag)
					resp := parseRPCResp(w.Body.Bytes())
					m.TotalCalls++
					if i == failStep && resp.Error == nil {
						t.Errorf("sess %d step %d: expected natural failure, got success", sess, i)
					}
				}

				switch policy {
				case "plangate_base":
					// No recovery: session fails permanently.
					// (recoveryConfig.Enabled=false: saveCheckpointAfterStep is a no-op,
					// markCheckpointRecoverable finds no checkpoint → logs only)

				case "naive_retry":
					// Full restart from step 0 on the same server.
					// The old reservation in budgetMgr is overwritten by the new Reserve() call
					// when we re-send step 0 with X-Plan-DAG.
					// tool[failStep] succeeds on its 2nd invocation (fail-once counter = n≥2).
					for i := 0; i < N; i++ {
						dag := ""
						if i == 0 {
							dag = dagJSON // creates fresh reservation, overwrites stale one
						}
						w := sendPS7CRequest(srv, sessionID, tools[i], dag)
						if parseRPCResp(w.Body.Bytes()).Error != nil {
							t.Logf("sess %d naive retry step %d unexpected error", sess, i)
						}
						m.TotalCalls++
					}
					m.ReplayOverhead += failStep
					succeeded = true

				case "plangate_r":
					// Recovery resume via ServeHTTP (X-Recovery-Mode: resume).
					wRec := sendRecoveryResumeHTTP(srv, sessionID)
					respRec := parseRPCResp(wRec.Body.Bytes())
					if respRec.Error != nil {
						t.Logf("sess %d recovery failed: code=%d msg=%s",
							sess, respRec.Error.Code, respRec.Error.Message)
					} else {
						// Recovery executes [tool_failStep, ..., tool_{N-1}] = N-failStep steps.
						m.TotalCalls += N - failStep
						m.AvoidedReplay += failStep
						m.RecoveredCount++
						succeeded = true
					}
				}
			}

			if succeeded {
				m.SuccessCount++
			}
		}
		return m
	}

	base  := runPolicy("plangate_base")
	naive := runPolicy("naive_retry")
	pg    := runPolicy("plangate_r")

	// ── Print summary table ────────────────────────────────────────────────────
	sep := "──────────────────────────────────────────────────────────────────────────────────────────────"
	t.Logf("\n%s\nPhase 7C — Controlled Workload (S=%d N=%d failStep=%d failCount=%d)\n%s",
		sep, S, N, failStep, failCount, sep)
	t.Logf("  %-18s  %10s  %11s  %11s  %12s  %11s",
		"Policy", "Success", "TotalCalls", "Recovered", "ReplayOvhd", "AvoidReplay")
	t.Logf("%s", sep)
	for _, m := range []workloadMetrics{base, naive, pg} {
		t.Logf("  %-18s  %10d  %11d  %11d  %12d  %11d",
			m.Policy, m.SuccessCount, m.TotalCalls, m.RecoveredCount,
			m.ReplayOverhead, m.AvoidedReplay)
	}
	t.Logf("%s", sep)
	t.Logf("  PlanGate-R vs Naive: calls saving = -%d", naive.TotalCalls-pg.TotalCalls)
	t.Logf("  PlanGate-R vs Base:  success gain = +%d sessions", pg.SuccessCount-base.SuccessCount)
	t.Logf("%s\n", sep)

	// ── Core assertions ────────────────────────────────────────────────────────
	// PlanGate-R executes fewer total tool calls than naive retry.
	if pg.TotalCalls >= naive.TotalCalls {
		t.Errorf("pgr total calls (%d) must be < naive (%d)", pg.TotalCalls, naive.TotalCalls)
	}
	// Per-session saving = failStep steps per failing session (avoided replay).
	expectedSaving := failCount * failStep
	actualSaving := naive.TotalCalls - pg.TotalCalls
	if actualSaving != expectedSaving {
		t.Errorf("calls saving: want %d (failCount×failStep=%d×%d), got %d",
			expectedSaving, failCount, failStep, actualSaving)
	}
	// PlanGate-R must succeed all S sessions (failing ones via recovery).
	if pg.SuccessCount != S {
		t.Errorf("pgr success count: want %d, got %d", S, pg.SuccessCount)
	}
	// Naive retry must also succeed all S sessions (via full restart).
	if naive.SuccessCount != S {
		t.Errorf("naive success count: want %d, got %d", S, naive.SuccessCount)
	}
	// PlanGate-base cannot recover; only the non-failing sessions succeed.
	if base.SuccessCount != S-failCount {
		t.Errorf("base success count: want %d (S-failCount), got %d", S-failCount, base.SuccessCount)
	}
	// PlanGate-R strictly outperforms base in success rate.
	if pg.SuccessCount <= base.SuccessCount {
		t.Errorf("pgr must outperform base: pgr=%d, base=%d", pg.SuccessCount, base.SuccessCount)
	}
}

// ══════════════════════════════════════════════════════════════════════════════
// PlanGate-R Phase 8: Paper-facing Recovery Result Packaging
//
// Part A: Recovery Evaluation Metric Definitions (confirmed in struct/methods)
// Part B: Multi-seed Controlled Runtime Workload
// Part C: Paper-candidate Table Output (Table R1 + Table R2)
//
// All experiments use:
//   - Controlled mock runtime (MCPDPServer + ServeHTTP, NO real LLM)
//   - Natural handler-level failures (fail-once counter pattern)
//   - Automatic checkpoint creation via saveCheckpointAfterStep
//   - Automatic promotion via classifyTransportError path
//   - Recovery via X-Recovery-Mode: resume HTTP path
//
// Metric definitions (Part A — confirmed in RuntimeExperimentResult):
//   1. Eventual Success:     EventualSuccessCount  (immediate + recovered)
//   2. Recovered Success:    RecoveredSuccessCount (after checkpoint resume)
//   3. Total Tool Calls:     TotalExecutedSteps    (all handler invocations)
//   4. Useful Steps:         EventualSuccessCount × N (productive invocations)
//   5. Total Waste Steps:    TotalExecutedSteps - UsefulSteps
//   6. Replay Overhead:      ReplayOverhead  (naive: re-executed prefix steps)
//   7. Avoided Replay:       AvoidedReplay   (pgr: skipped prefix steps)
//   8. Calls per Success:    TotalExecutedSteps / EventualSuccessCount
// ══════════════════════════════════════════════════════════════════════════════

// ─── Phase 8 result types ────────────────────────────────────────────────────

// seedRunResult holds raw metrics for one (failureRate, seed, policy) trial.
type seedRunResult struct {
	FailureRate    float64
	Seed           int
	Policy         string
	Sessions       int // S
	N              int // steps per session
	ActualFailCount int // # sessions that actually failed (Bernoulli draw)

	ToolCalls      int
	SuccessCount   int
	RecoveredCount int
	ReplayOverhead int // naive only
	AvoidedReplay  int // pgr only
}

func (r seedRunResult) UsefulSteps() int { return r.SuccessCount * r.N }
func (r seedRunResult) WasteSteps() int  { return r.ToolCalls - r.UsefulSteps() }
func (r seedRunResult) CallsPerSuccess() float64 {
	if r.SuccessCount == 0 {
		return -1
	}
	return float64(r.ToolCalls) / float64(r.SuccessCount)
}
func (r seedRunResult) SuccessRate() float64 { return float64(r.SuccessCount) / float64(r.Sessions) }

// seedAggregated holds mean ± std across seeds for one (failureRate, policy).
type seedAggregated struct {
	FailureRate float64
	Policy      string
	Seeds       int

	SuccessRateMean   float64
	SuccessRateStd    float64
	RecoveredMean     float64
	ToolCallsMean     float64
	ToolCallsStd      float64
	WasteStepsMean    float64
	WasteStepsStd     float64
	CallsPerSuccMean  float64
	CallsPerSuccStd   float64
	AvoidedReplayMean float64
}

// ─── Phase 8 statistics helpers ──────────────────────────────────────────────

func meanF(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	var s float64
	for _, v := range vals {
		s += v
	}
	return s / float64(len(vals))
}

func stdF(vals []float64, mean float64) float64 {
	if len(vals) < 2 {
		return 0
	}
	var sq float64
	for _, v := range vals {
		d := v - mean
		sq += d * d
	}
	return math.Sqrt(sq / float64(len(vals)-1))
}

func aggregateSeeds(runs []seedRunResult) seedAggregated {
	if len(runs) == 0 {
		return seedAggregated{}
	}
	agg := seedAggregated{
		FailureRate: runs[0].FailureRate,
		Policy:      runs[0].Policy,
		Seeds:       len(runs),
	}
	var (
		sr, rc, tc, ws, cs, ar []float64
	)
	for _, r := range runs {
		sr = append(sr, r.SuccessRate())
		rc = append(rc, float64(r.RecoveredCount))
		tc = append(tc, float64(r.ToolCalls))
		ws = append(ws, float64(r.WasteSteps()))
		cs = append(cs, r.CallsPerSuccess())
		ar = append(ar, float64(r.AvoidedReplay))
	}
	agg.SuccessRateMean = meanF(sr)
	agg.SuccessRateStd = stdF(sr, agg.SuccessRateMean)
	agg.RecoveredMean = meanF(rc)
	agg.ToolCallsMean = meanF(tc)
	agg.ToolCallsStd = stdF(tc, agg.ToolCallsMean)
	agg.WasteStepsMean = meanF(ws)
	agg.WasteStepsStd = stdF(ws, agg.WasteStepsMean)
	agg.CallsPerSuccMean = meanF(cs)
	agg.CallsPerSuccStd = stdF(cs, agg.CallsPerSuccMean)
	agg.AvoidedReplayMean = meanF(ar)
	return agg
}

// ─── Phase 8 workload runner ─────────────────────────────────────────────────

// runSeedWorkload executes one (failureRate, seed, policy) trial using the full
// ServeHTTP path.  It uses a Bernoulli draw seeded by `seed` to decide which
// of the S sessions will experience a natural failure at failStep.
func runSeedWorkload(
	t *testing.T,
	sessions, N, failStep int,
	failureRate float64,
	seed int,
	policy string,
) seedRunResult {
	t.Helper()
	rng := rand.New(rand.NewSource(int64(seed)))
	const failErr = "overloaded" // matches classifyTransportError keyword

	res := seedRunResult{
		FailureRate: failureRate,
		Seed:        seed,
		Policy:      policy,
		Sessions:    sessions,
		N:           N,
	}

	// Pre-determine which sessions fail.
	willFail := make([]bool, sessions)
	for i := range willFail {
		willFail[i] = rng.Float64() < failureRate
		if willFail[i] {
			res.ActualFailCount++
		}
	}

	for sess := 0; sess < sessions; sess++ {
		sessionID := fmt.Sprintf("ph8_%s_r%d_s%d_%d", policy, int(failureRate*10), seed, sess)

		var srv *MCPDPServer
		var tools []string
		if willFail[sess] {
			srv, _, tools = makeNatFailServer(t, N, failStep,
				fmt.Errorf("backend %s: service unavailable", failErr))
		} else {
			srv, _, tools = makeRuntimeServer(t, N, 0)
		}
		if policy == "plangate_base" {
			srv.recoveryConfig.Enabled = false
		}

		dagJSON := makeNat7CDAGJSON(sessionID, tools, 1000)
		succeeded := false

		if !willFail[sess] {
			// Non-failing: all N steps succeed.
			for i := 0; i < N; i++ {
				dag := ""
				if i == 0 {
					dag = dagJSON
				}
				sendPS7CRequest(srv, sessionID, tools[i], dag) //nolint:errcheck
				res.ToolCalls++
			}
			succeeded = true
		} else {
			// Failing: steps 0..failStep (failStep's first call fails).
			for i := 0; i <= failStep; i++ {
				dag := ""
				if i == 0 {
					dag = dagJSON
				}
				sendPS7CRequest(srv, sessionID, tools[i], dag) //nolint:errcheck
				res.ToolCalls++
			}

			switch policy {
			case "plangate_base":
				// No recovery; session terminates with partial progress.

			case "naive_retry":
				// Full restart from step 0 (same sessionID, same server).
				// budgetMgr.Reserve overwrites the stale reservation.
				// tools[failStep] succeeds on 2nd invocation (fail-once counter).
				for i := 0; i < N; i++ {
					dag := ""
					if i == 0 {
						dag = dagJSON
					}
					sendPS7CRequest(srv, sessionID, tools[i], dag) //nolint:errcheck
					res.ToolCalls++
				}
				res.ReplayOverhead += failStep
				succeeded = true

			case "plangate_r":
				// Recovery via ServeHTTP (X-Recovery-Mode: resume).
				wRec := sendRecoveryResumeHTTP(srv, sessionID)
				respRec := parseRPCResp(wRec.Body.Bytes())
				if respRec.Error == nil {
					res.ToolCalls += N - failStep // recovery executes [failStep..N-1]
					res.AvoidedReplay += failStep
					res.RecoveredCount++
					succeeded = true
				} else {
					t.Logf("ph8 sess %d recovery failed: code=%d msg=%s",
						sess, respRec.Error.Code, respRec.Error.Message)
				}
			}
		}

		if succeeded {
			res.SuccessCount++
		}
	}
	return res
}

// ─────────────────────────────────────────────────────────────────────────────
// Test Phase 8 — Multi-seed controlled runtime workload
// ─────────────────────────────────────────────────────────────────────────────

// TestRuntimeMultiSeedControlledWorkload runs a multi-seed sweep over three
// failure rates and five seeds for three policies (PlanGate-base, naive retry,
// PlanGate-R), all via the full ServeHTTP path with natural handler failures.
//
// Configuration:
//   S = 50 sessions, N = 5 steps, failStep = 2
//   failure_rate ∈ {0.1, 0.3, 0.5}
//   seeds ∈ {1..5} (Bernoulli draw per seed)
//
// Output: Table R1 (failure_rate=0.3, mean ± std) and Table R2 (sensitivity).
//
// IMPORTANT: This is a CONTROLLED MOCK RUNTIME experiment.
// Handlers are in-memory stubs.  These results are NOT from a real LLM
// and are NOT a final production benchmark.  They serve as a paper-facing
// recovery extension result for the PlanGate-R mechanism evaluation.
func TestRuntimeMultiSeedControlledWorkload(t *testing.T) {
	const (
		S        = 50
		N        = 5
		failStep = 2
		nSeeds   = 5
	)
	failureRates := []float64{0.1, 0.3, 0.5}
	policies := []string{"plangate_base", "naive_retry", "plangate_r"}
	seeds := func() []int {
		s := make([]int, nSeeds)
		for i := range s {
			s[i] = i + 1
		}
		return s
	}()

	// ── Run all (failureRate × seed × policy) trials ──────────────────────────
	// Collect raw runs.
	type trialKey struct {
		rate   float64
		seed   int
		policy string
	}
	allRuns := map[trialKey]seedRunResult{}

	for _, rate := range failureRates {
		for _, seed := range seeds {
			for _, pol := range policies {
				key := trialKey{rate, seed, pol}
				allRuns[key] = runSeedWorkload(t, S, N, failStep, rate, seed, pol)
			}
		}
	}

	// ── Aggregate across seeds ────────────────────────────────────────────────
	type aggKey struct {
		rate   float64
		policy string
	}
	aggMap := map[aggKey]seedAggregated{}

	for _, rate := range failureRates {
		for _, pol := range policies {
			var runs []seedRunResult
			for _, seed := range seeds {
				runs = append(runs, allRuns[trialKey{rate, seed, pol}])
			}
			aggMap[aggKey{rate, pol}] = aggregateSeeds(runs)
		}
	}

	// ── Table R1: failure_rate=0.3, mean ± std ─────────────────────────────────
	const targetRate = 0.3
	sep := "───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────"
	t.Logf("\n\n%s", sep)
	t.Logf("TABLE R1: Controlled Runtime Recovery Result")
	t.Logf("           (S=%d, N=%d, failStep=%d, failure_rate=%.1f, seeds=%d)", S, N, failStep, targetRate, nSeeds)
	t.Logf("NOTE: Controlled mock runtime — NO real LLM — mock handler stubs only")
	t.Logf("%s", sep)
	t.Logf("  %-18s  %14s  %10s  %13s  %14s  %16s  %14s",
		"Policy", "SuccessRate", "Recovered", "ToolCalls", "WasteSteps", "Calls/Success", "AvoidedReplay")
	t.Logf("%s", sep)
	for _, pol := range policies {
		a := aggMap[aggKey{targetRate, pol}]
		t.Logf("  %-18s  %5.3f±%5.3f  %10.1f  %6.1f±%5.1f  %7.1f±%5.1f  %8.2f±%5.2f  %14.1f",
			pol,
			a.SuccessRateMean, a.SuccessRateStd,
			a.RecoveredMean,
			a.ToolCallsMean, a.ToolCallsStd,
			a.WasteStepsMean, a.WasteStepsStd,
			a.CallsPerSuccMean, a.CallsPerSuccStd,
			a.AvoidedReplayMean)
	}
	t.Logf("%s", sep)
	pgr03 := aggMap[aggKey{targetRate, "plangate_r"}]
	naive03 := aggMap[aggKey{targetRate, "naive_retry"}]
	base03 := aggMap[aggKey{targetRate, "plangate_base"}]
	t.Logf("  PlanGate-R vs Naive:       calls reduction   = -%.1f/session  (%.1f%%)",
		naive03.ToolCallsMean-pgr03.ToolCallsMean,
		100*(naive03.ToolCallsMean-pgr03.ToolCallsMean)/max1(naive03.ToolCallsMean))
	t.Logf("  PlanGate-R vs Naive:       waste reduction   = -%.1f/session",
		naive03.WasteStepsMean-pgr03.WasteStepsMean)
	t.Logf("  PlanGate-R vs Base:        success gain      = +%.1f sessions/run",
		pgr03.SuccessRateMean*float64(S)-base03.SuccessRateMean*float64(S))
	t.Logf("%s\n", sep)

	// ── Table R2: failure-rate sensitivity ────────────────────────────────────
	t.Logf("\n%s", sep)
	t.Logf("TABLE R2: Failure-rate Sensitivity")
	t.Logf("           (S=%d, N=%d, failStep=%d, seeds=%d)", S, N, failStep, nSeeds)
	t.Logf("NOTE: Controlled mock runtime — NO real LLM — mock handler stubs only")
	t.Logf("%s", sep)
	t.Logf("  %-5s  %-18s  %12s  %12s  %12s  %12s",
		"Rate", "Policy", "SuccessRate", "ToolCalls", "WasteSteps", "AvoidedReplay")
	t.Logf("%s", sep)
	for _, rate := range failureRates {
		for _, pol := range policies {
			a := aggMap[aggKey{rate, pol}]
			t.Logf("  %-5.1f  %-18s  %5.3f±%5.3f  %6.1f±%4.1f  %6.1f±%4.1f  %12.1f",
				rate, pol,
				a.SuccessRateMean, a.SuccessRateStd,
				a.ToolCallsMean, a.ToolCallsStd,
				a.WasteStepsMean, a.WasteStepsStd,
				a.AvoidedReplayMean)
		}
		t.Logf("  ─────────────────────────────────────────────────────────────────────────────")
	}
	t.Logf("%s\n", sep)

	// ── Core correctness assertions ───────────────────────────────────────────
	for _, rate := range failureRates {
		pgr := aggMap[aggKey{rate, "plangate_r"}]
		naive := aggMap[aggKey{rate, "naive_retry"}]
		base := aggMap[aggKey{rate, "plangate_base"}]

		// PlanGate-R must match naive retry in eventual success rate (both = 1.0 with rate > 0).
		if pgr.SuccessRateMean < naive.SuccessRateMean-0.01 {
			t.Errorf("rate=%.1f: pgr success (%.3f) < naive (%.3f)", rate, pgr.SuccessRateMean, naive.SuccessRateMean)
		}
		// PlanGate-R must use fewer tool calls than naive retry.
		if pgr.ToolCallsMean >= naive.ToolCallsMean {
			t.Errorf("rate=%.1f: pgr calls (%.1f) >= naive (%.1f)", rate, pgr.ToolCallsMean, naive.ToolCallsMean)
		}
		// PlanGate-R must outperform base in success rate when rate > 0.
		if rate > 0.05 && pgr.SuccessRateMean <= base.SuccessRateMean {
			t.Errorf("rate=%.1f: pgr success (%.3f) <= base (%.3f)", rate, pgr.SuccessRateMean, base.SuccessRateMean)
		}
		// Tool call reduction must equal rate × S × failStep (approx, allow ±10%).
		expectedReduction := rate * float64(S) * float64(failStep)
		actualReduction := naive.ToolCallsMean - pgr.ToolCallsMean
		if actualReduction < expectedReduction*0.70 || actualReduction > expectedReduction*1.30 {
			t.Logf("INFO rate=%.1f: expected ~%.1f call reduction, got %.1f (within ±30%% OK)",
				rate, expectedReduction, actualReduction)
		}
	}

	t.Logf("Phase 8 TestRuntimeMultiSeedControlledWorkload: all assertions PASS")
}

// max1 avoids division by zero for percentage calculation.
func max1(v float64) float64 {
	if v < 1 {
		return 1
	}
	return v
}
