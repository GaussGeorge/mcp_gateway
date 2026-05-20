package plangate

// PlanGate-R Phase 6: Controlled Recovery Experiment
//
// ══════════════════════════════════════════════════════════════════════════════
// IMPORTANT SCOPE NOTE
// ══════════════════════════════════════════════════════════════════════════════
// This file implements a PURE IN-MEMORY SIMULATOR.
// It does NOT use:
//   - real network connections
//   - real LLM calls
//   - MCPDPServer / HTTP handlers
//   - any scripts/*, baseline/*, or paper CSVs
//
// The purpose is to verify MECHANISM EFFECT:
//   "Under recoverable interruptions, PlanGate-R improves eventual_success_rate
//    and reduces repeated compute compared to naive retry."
//
// This is NOT a paper result. It is a controlled demonstration of the algorithm.
// Real workload experiments come later (Phase 7).
// ══════════════════════════════════════════════════════════════════════════════

import (
	"fmt"
	"math/rand"
	"strings"
	"testing"
)

// ─────────────────────────────────────────────────────────────────────────────
// Simulator types
// ─────────────────────────────────────────────────────────────────────────────

// RecoveryPolicy identifies which recovery strategy is being simulated.
type RecoveryPolicy string

const (
	// PolicyPlanGateBase: checkpoint exists, but no recovery is performed.
	// A recoverable interruption leaves the session permanently failed.
	PolicyPlanGateBase RecoveryPolicy = "plangate_base"

	// PolicyNaiveRetry: upon any interruption, restart the session from step 0.
	// All previously completed steps are wasted (repeated).
	PolicyNaiveRetry RecoveryPolicy = "naive_retry"

	// PolicyPlanGateR: upon interruption, resume from the checkpoint step K.
	// Only the N-K remaining steps are executed; first K steps are saved.
	PolicyPlanGateR RecoveryPolicy = "plangate_r"
)

// RecoveryExperimentConfig parameterises a controlled recovery experiment.
type RecoveryExperimentConfig struct {
	Sessions              int     // total P&S sessions to simulate
	StepsPerSession       int     // N: fixed steps per session (all sessions identical)
	InterruptionAfterStep int     // K: number of steps that complete before an interruption
	InterruptionRate      float64 // probability [0.0, 1.0] that a session is interrupted
	Seed                  int64   // deterministic random seed (same seed = same outcomes across all policies)
}

// RecoveryExperimentResult collects all metrics from one experiment run.
//
// Counter semantics
// ─────────────────
//   TotalExecutedSteps   = sum of all handler invocations across all sessions
//   RepeatedSteps        = steps re-executed that had already succeeded before (naive retry only)
//   SkippedSteps         = steps NOT re-executed because a checkpoint existed (PlanGate-R only)
//   SavedComputeSteps    = same as SkippedSteps (alias for paper reporting)
//   RecoveryAttempts     = number of sessions that entered a recovery / retry path
//   RecoveredSuccessCount= sessions that succeed BECAUSE of the recovery mechanism
//   EventualSuccessCount = ImmediateSuccessCount + RecoveredSuccessCount
type RecoveryExperimentResult struct {
	Policy                string
	TotalSessions         int
	ImmediateSuccessCount int
	EventualSuccessCount  int
	RecoveredSuccessCount int
	FailedTerminalCount   int
	TotalExecutedSteps    int
	RepeatedSteps         int
	SkippedSteps          int
	SavedComputeSteps     int
	RecoveryAttempts      int
}

// ExecutedStepsPerSuccess returns the average execution cost per successful session.
// Returns -1 when EventualSuccessCount == 0 (undefined — no successes to average over).
func (r RecoveryExperimentResult) ExecutedStepsPerSuccess() float64 {
	if r.EventualSuccessCount == 0 {
		return -1
	}
	return float64(r.TotalExecutedSteps) / float64(r.EventualSuccessCount)
}

// WasteStepsPerSuccess returns average wasted (repeated / useless) steps per successful session.
func (r RecoveryExperimentResult) WasteStepsPerSuccess() float64 {
	if r.EventualSuccessCount == 0 {
		return -1
	}
	return float64(r.RepeatedSteps) / float64(r.EventualSuccessCount)
}

// ─────────────────────────────────────────────────────────────────────────────
// Simulator core
// ─────────────────────────────────────────────────────────────────────────────

// runControlledRecoveryExperiment executes a deterministic, pure in-memory
// simulation of cfg.Sessions P&S sessions under the given recovery policy.
//
// Phase 6A simplification: recovery / retry always succeed on the second attempt.
// This is intentional — it lets us isolate the EFFICIENCY difference without
// mixing in secondary failure probability (deferred to Phase 6B).
//
// The same random seed produces the same set of interrupted sessions regardless
// of which policy is evaluated, ensuring a fair cross-policy comparison.
func runControlledRecoveryExperiment(cfg RecoveryExperimentConfig, policy RecoveryPolicy) RecoveryExperimentResult {
	rng := rand.New(rand.NewSource(cfg.Seed)) //nolint:gosec // deterministic seed is intentional

	res := RecoveryExperimentResult{
		Policy:        string(policy),
		TotalSessions: cfg.Sessions,
	}

	K := cfg.InterruptionAfterStep // steps completed before interruption
	N := cfg.StepsPerSession       // total steps per session

	for i := 0; i < cfg.Sessions; i++ {
		interrupted := rng.Float64() < cfg.InterruptionRate

		if !interrupted {
			// ── Happy path ──────────────────────────────────────────────────
			// All N steps execute and the session succeeds immediately.
			res.TotalExecutedSteps += N
			res.ImmediateSuccessCount++
			res.EventualSuccessCount++
			continue
		}

		// ── Interrupted path ─────────────────────────────────────────────
		// First-pass execution: K steps run successfully before the interruption.
		// This cost is incurred by ALL three policies.
		res.TotalExecutedSteps += K

		switch policy {

		case PolicyPlanGateBase:
			// No recovery mechanism exists.
			// The session is permanently lost (FailedTerminal).
			// Even if a checkpoint was saved, nobody acts on it.
			res.FailedTerminalCount++

		case PolicyNaiveRetry:
			// Retry from scratch: all N steps are re-executed.
			// The K steps already completed are wasted (RepeatedSteps).
			res.TotalExecutedSteps += N
			res.RepeatedSteps += K
			res.RecoveryAttempts++
			res.EventualSuccessCount++ // retry succeeds in Phase 6A

		case PolicyPlanGateR:
			// Resume from checkpoint at step K.
			// Only the remaining N-K steps are executed.
			// First K steps are permanently saved (SkippedSteps = SavedComputeSteps).
			res.TotalExecutedSteps += (N - K)
			res.SkippedSteps += K
			res.SavedComputeSteps += K
			res.RecoveryAttempts++
			res.RecoveredSuccessCount++ // this success is BECAUSE of recovery
			res.EventualSuccessCount++
		}
	}

	return res
}

// ─────────────────────────────────────────────────────────────────────────────
// Table printer  (Part D)
// ─────────────────────────────────────────────────────────────────────────────

// printExperimentTable logs a human-readable results table.
// Run tests with -v to see the output.
func printExperimentTable(t *testing.T, cfg RecoveryExperimentConfig, results []RecoveryExperimentResult) {
	t.Helper()
	sep := strings.Repeat("─", 100)
	t.Logf("\n%s", sep)
	t.Logf("Controlled Recovery Experiment  sessions=%d  steps/session=%d  interrupt_after_step=%d  interrupt_rate=%.0f%%  seed=%d",
		cfg.Sessions, cfg.StepsPerSession, cfg.InterruptionAfterStep, cfg.InterruptionRate*100, cfg.Seed)
	t.Logf("%s", sep)
	t.Logf("  %-18s  %18s  %12s  %16s  %13s  %14s",
		"Policy", "Eventual Success", "Recovered", "Executed Steps", "Saved Steps", "Steps/Success")
	t.Logf("%s", sep)
	for _, r := range results {
		sps := r.ExecutedStepsPerSuccess()
		spsStr := "N/A (0 success)"
		if sps >= 0 {
			spsStr = fmt.Sprintf("%.2f", sps)
		}
		t.Logf("  %-18s  %7d / %7d   %6d / %-4d   %14d   %11d   %s",
			r.Policy,
			r.EventualSuccessCount, r.TotalSessions,
			r.RecoveredSuccessCount, r.RecoveryAttempts,
			r.TotalExecutedSteps,
			r.SavedComputeSteps,
			spsStr,
		)
	}
	t.Logf("%s", sep)

	// Cross-policy deltas (only when all three are present)
	if len(results) == 3 {
		base, naive, pg := results[0], results[1], results[2]
		successGain := pg.EventualSuccessCount - base.EventualSuccessCount
		computeSaving := naive.TotalExecutedSteps - pg.TotalExecutedSteps
		t.Logf("  PlanGate-R vs PlanGate-base  │  success gain      = +%d sessions", successGain)
		t.Logf("  PlanGate-R vs Naive-retry    │  compute saving    = -%d steps  (saved %.1f%%)",
			computeSaving, 100*float64(computeSaving)/float64(naive.TotalExecutedSteps))
		t.Logf("  PlanGate-R saved_compute     = %d steps  (= %d sessions × %d skipped_steps_per_recovery)",
			pg.SavedComputeSteps, pg.RecoveryAttempts, cfg.InterruptionAfterStep)
	}
	t.Logf("%s\n", sep)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 1 — Core 3-policy comparison (100% interruption rate)
// ─────────────────────────────────────────────────────────────────────────────

// TestControlledRecoveryExperimentMetrics is the primary cross-policy comparison.
//
// Setup: 10 sessions, N=5, K=2, interrupt_rate=100% (all sessions are interrupted).
// This gives exact, deterministic numbers with no stochastic variance.
//
// Expected values (worked by hand):
//   PlanGate-base : executed=10×2=20,  eventual=0,  failed=10
//   Naive-retry   : executed=10×2+10×5=70, eventual=10, repeated=20
//   PlanGate-R    : executed=10×2+10×3=50, eventual=10, skipped=20, saved=20
func TestControlledRecoveryExperimentMetrics(t *testing.T) {
	cfg := RecoveryExperimentConfig{
		Sessions:              10,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      1.0, // all sessions are interrupted
		Seed:                  42,
	}

	base  := runControlledRecoveryExperiment(cfg, PolicyPlanGateBase)
	naive := runControlledRecoveryExperiment(cfg, PolicyNaiveRetry)
	pg    := runControlledRecoveryExperiment(cfg, PolicyPlanGateR)

	printExperimentTable(t, cfg, []RecoveryExperimentResult{base, naive, pg})

	// ── PlanGate-base ──────────────────────────────────────────────────────
	if base.EventualSuccessCount != 0 {
		t.Errorf("base: EventualSuccessCount want 0, got %d", base.EventualSuccessCount)
	}
	if base.FailedTerminalCount != 10 {
		t.Errorf("base: FailedTerminalCount want 10, got %d", base.FailedTerminalCount)
	}
	if base.TotalExecutedSteps != 20 { // 10 sessions × 2 first-pass steps
		t.Errorf("base: TotalExecutedSteps want 20, got %d", base.TotalExecutedSteps)
	}

	// ── Naive-retry ────────────────────────────────────────────────────────
	if naive.EventualSuccessCount != 10 {
		t.Errorf("naive: EventualSuccessCount want 10, got %d", naive.EventualSuccessCount)
	}
	if naive.TotalExecutedSteps != 70 { // 10×2 first-pass + 10×5 retry
		t.Errorf("naive: TotalExecutedSteps want 70, got %d", naive.TotalExecutedSteps)
	}
	if naive.RepeatedSteps != 20 { // 10 × 2 wasted first-pass steps
		t.Errorf("naive: RepeatedSteps want 20, got %d", naive.RepeatedSteps)
	}

	// ── PlanGate-R ─────────────────────────────────────────────────────────
	if pg.EventualSuccessCount != 10 {
		t.Errorf("pg-r: EventualSuccessCount want 10, got %d", pg.EventualSuccessCount)
	}
	if pg.RecoveredSuccessCount != 10 {
		t.Errorf("pg-r: RecoveredSuccessCount want 10, got %d", pg.RecoveredSuccessCount)
	}
	if pg.TotalExecutedSteps != 50 { // 10×2 first-pass + 10×3 recovery
		t.Errorf("pg-r: TotalExecutedSteps want 50, got %d", pg.TotalExecutedSteps)
	}
	if pg.SkippedSteps != 20 {
		t.Errorf("pg-r: SkippedSteps want 20, got %d", pg.SkippedSteps)
	}
	if pg.SavedComputeSteps != 20 {
		t.Errorf("pg-r: SavedComputeSteps want 20, got %d", pg.SavedComputeSteps)
	}

	// ── Cross-policy invariants ────────────────────────────────────────────
	if pg.TotalExecutedSteps >= naive.TotalExecutedSteps {
		t.Errorf("pg-r must execute fewer steps than naive: got pg=%d, naive=%d",
			pg.TotalExecutedSteps, naive.TotalExecutedSteps)
	}
	if pg.EventualSuccessCount != naive.EventualSuccessCount {
		t.Errorf("pg-r and naive must reach same eventual success: got pg=%d, naive=%d",
			pg.EventualSuccessCount, naive.EventualSuccessCount)
	}
	if pg.EventualSuccessCount <= base.EventualSuccessCount {
		t.Errorf("pg-r must exceed base in eventual success: pg=%d, base=%d",
			pg.EventualSuccessCount, base.EventualSuccessCount)
	}
	// PlanGate-R steps/success MUST be lower than naive retry.
	pgSPS    := pg.ExecutedStepsPerSuccess()
	naiveSPS := naive.ExecutedStepsPerSuccess()
	if pgSPS >= naiveSPS {
		t.Errorf("pg-r steps/success (%.2f) must be < naive (%.2f)", pgSPS, naiveSPS)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 2 — Compute saving is strictly positive and equals SkippedSteps
// ─────────────────────────────────────────────────────────────────────────────

// TestPlanGateRComputeSavingVsNaiveRetry verifies that PlanGate-R executes
// strictly fewer total steps than naive retry whenever interruptions occur,
// and that the saving exactly equals the sum of skipped checkpoint steps.
func TestPlanGateRComputeSavingVsNaiveRetry(t *testing.T) {
	cfg := RecoveryExperimentConfig{
		Sessions:              100,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      0.5,
		Seed:                  77,
	}

	naive := runControlledRecoveryExperiment(cfg, PolicyNaiveRetry)
	pg    := runControlledRecoveryExperiment(cfg, PolicyPlanGateR)

	printExperimentTable(t, cfg, []RecoveryExperimentResult{naive, pg})

	saving := naive.TotalExecutedSteps - pg.TotalExecutedSteps
	t.Logf("Compute saving: naive=%d  pg-r=%d  delta=-%d  pg.SkippedSteps=%d",
		naive.TotalExecutedSteps, pg.TotalExecutedSteps, saving, pg.SkippedSteps)

	if saving <= 0 {
		t.Errorf("expected positive compute saving, got %d", saving)
	}
	// The saving must equal exactly the skipped steps (algebraic identity).
	// proof: naive_total = firstpass + N×interrupted
	//        pgr_total   = firstpass + (N-K)×interrupted
	//        delta       = K×interrupted = SkippedSteps
	if saving != pg.SkippedSteps {
		t.Errorf("compute_saving (%d) must equal SkippedSteps (%d) — algebraic identity violated",
			saving, pg.SkippedSteps)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 3 — RecoveredSuccessCount equals the number of interrupted sessions
// ─────────────────────────────────────────────────────────────────────────────

// TestPlanGateRRecoveredSuccessCount verifies that every interrupted session
// produces exactly one RecoveredSuccess, and that all sessions eventually succeed.
func TestPlanGateRRecoveredSuccessCount(t *testing.T) {
	cfg := RecoveryExperimentConfig{
		Sessions:              50,
		StepsPerSession:       5,
		InterruptionAfterStep: 3,
		InterruptionRate:      0.4,
		Seed:                  13,
	}

	pg := runControlledRecoveryExperiment(cfg, PolicyPlanGateR)

	interruptedCount := pg.RecoveryAttempts // one RecoveryAttempt per interrupted session

	// Every interruption → exactly one RecoveredSuccess.
	if pg.RecoveredSuccessCount != interruptedCount {
		t.Errorf("RecoveredSuccessCount (%d) must equal interrupted sessions (%d)",
			pg.RecoveredSuccessCount, interruptedCount)
	}
	// All sessions eventually succeed (interrupted ones via recovery, rest immediately).
	if pg.EventualSuccessCount != cfg.Sessions {
		t.Errorf("expected all %d sessions to eventually succeed, got %d",
			cfg.Sessions, pg.EventualSuccessCount)
	}
	// immediate + recovered = total.
	if pg.ImmediateSuccessCount+pg.RecoveredSuccessCount != cfg.Sessions {
		t.Errorf("ImmediateSuccess (%d) + RecoveredSuccess (%d) must equal Sessions (%d)",
			pg.ImmediateSuccessCount, pg.RecoveredSuccessCount, cfg.Sessions)
	}

	t.Logf("Sessions=%d  interrupted=%d  immediate=%d  recovered=%d  eventual=%d",
		cfg.Sessions, interruptedCount, pg.ImmediateSuccessCount,
		pg.RecoveredSuccessCount, pg.EventualSuccessCount)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 4 — Partial interruption rate (50%)
// ─────────────────────────────────────────────────────────────────────────────

// TestExperimentWithPartialInterruptions tests realistic conditions where
// only a fraction of sessions are interrupted (interruption_rate=0.5).
//
// The key ordering invariants must still hold:
//   pg.EventualSuccess >= base.EventualSuccess
//   pg.ExecutedStepsPerSuccess < naive.ExecutedStepsPerSuccess
func TestExperimentWithPartialInterruptions(t *testing.T) {
	cfg := RecoveryExperimentConfig{
		Sessions:              100,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      0.5,
		Seed:                  99,
	}

	base  := runControlledRecoveryExperiment(cfg, PolicyPlanGateBase)
	naive := runControlledRecoveryExperiment(cfg, PolicyNaiveRetry)
	pg    := runControlledRecoveryExperiment(cfg, PolicyPlanGateR)

	printExperimentTable(t, cfg, []RecoveryExperimentResult{base, naive, pg})

	// PlanGate-R must match or exceed PlanGate-base in eventual success.
	if pg.EventualSuccessCount < base.EventualSuccessCount {
		t.Errorf("pg-r eventual_success (%d) must be >= base (%d)",
			pg.EventualSuccessCount, base.EventualSuccessCount)
	}
	// PlanGate-R and naive retry reach the same eventual success (both recover all).
	if pg.EventualSuccessCount != naive.EventualSuccessCount {
		t.Errorf("pg-r and naive must reach identical eventual success: pg=%d, naive=%d",
			pg.EventualSuccessCount, naive.EventualSuccessCount)
	}
	// PlanGate-R is more efficient per success than naive retry.
	pgSPS    := pg.ExecutedStepsPerSuccess()
	naiveSPS := naive.ExecutedStepsPerSuccess()
	if pgSPS >= naiveSPS {
		t.Errorf("pg-r steps/success (%.2f) must be < naive steps/success (%.2f)", pgSPS, naiveSPS)
	}

	// Quantify the success gap between PlanGate-R and PlanGate-base.
	successGain := pg.EventualSuccessCount - base.EventualSuccessCount
	if successGain <= 0 {
		t.Errorf("expected PlanGate-R to gain at least 1 success over base, got delta=%d", successGain)
	}
	t.Logf("Success gain (pg-r over base): +%d sessions", successGain)
	t.Logf("Compute saving (pg-r over naive): -%d steps", naive.TotalExecutedSteps-pg.TotalExecutedSteps)
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: sensitivity sweep (interruption_rate × interrupt_step)
// ─────────────────────────────────────────────────────────────────────────────

// TestRecoveryExperimentSensitivitySweep sweeps interruption_rate over
// [0.0, 0.25, 0.5, 0.75, 1.0] and verifies that PlanGate-R dominates
// naive retry in compute efficiency for any positive rate.
//
// The test also logs the full sweep table so you can inspect the trend.
func TestRecoveryExperimentSensitivitySweep(t *testing.T) {
	rates := []float64{0.0, 0.25, 0.5, 0.75, 1.0}
	sep := strings.Repeat("─", 80)
	t.Logf("\n%s\nSensitivity Sweep  (sessions=200, N=5, K=2, seed=31)\n%s", sep, sep)
	t.Logf("  %-8s  %-12s  %-14s  %-14s  %-12s",
		"Rate", "pg-r evsucc", "naive steps", "pg-r steps", "saving")
	t.Logf("%s", sep)

	for _, rate := range rates {
		cfg := RecoveryExperimentConfig{
			Sessions:              200,
			StepsPerSession:       5,
			InterruptionAfterStep: 2,
			InterruptionRate:      rate,
			Seed:                  31,
		}
		naive := runControlledRecoveryExperiment(cfg, PolicyNaiveRetry)
		pg    := runControlledRecoveryExperiment(cfg, PolicyPlanGateR)

		saving := naive.TotalExecutedSteps - pg.TotalExecutedSteps
		t.Logf("  %-8.0f%%  %5d/%-6d   %11d    %11d    %8d",
			rate*100,
			pg.EventualSuccessCount, cfg.Sessions,
			naive.TotalExecutedSteps,
			pg.TotalExecutedSteps,
			saving)

		// Invariant: when rate > 0, PlanGate-R must save compute.
		if rate > 0 && saving <= 0 {
			t.Errorf("rate=%.2f: expected positive compute saving, got %d", rate, saving)
		}
		// Eventual success must be equal.
		if pg.EventualSuccessCount != naive.EventualSuccessCount {
			t.Errorf("rate=%.2f: pg-r and naive must reach same success: pg=%d, naive=%d",
				rate, pg.EventualSuccessCount, naive.EventualSuccessCount)
		}
	}
	t.Logf("%s\n", sep)
}
