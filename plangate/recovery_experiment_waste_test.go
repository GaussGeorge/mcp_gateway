package plangate

// PlanGate-R Phase 6C: Waste Accounting Tests
//
// These tests verify the new cascade-waste metric definitions are computed
// correctly by runControlledRecoveryExperimentV2.
//
// All four tests use deterministic configurations and verify EXACT values
// that can be derived analytically — they serve as both correctness proofs
// and regression guards for the metric semantics.

import (
	"fmt"
	"testing"
)

// ─────────────────────────────────────────────────────────────────────────────
// Test 1 — PlanGate-base: all interrupted sessions → all terminal waste
// ─────────────────────────────────────────────────────────────────────────────

// TestWasteAccountingForPlanGateBase verifies that PlanGate-base correctly
// accounts the K initial steps of every interrupted session as terminal waste.
//
// Setup: sessions=10, N=5, K=2, interrupt_rate=100%.
// Expected (hand-derived):
//   TotalExecutedSteps = 10×2 = 20
//   EventualSuccess    = 0      (no recovery)
//   UsefulSteps        = 0
//   TotalWasteSteps    = 20
//   TerminalWasteSteps = 20     (all waste is terminal)
func TestWasteAccountingForPlanGateBase(t *testing.T) {
	cfg := RecoveryExperimentConfigV2{
		Sessions:              10,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      1.0,
		MaxAttempts:           0, // no recovery
		Seed:                  1,
	}

	res := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateBase)
	t.Logf("PlanGate-base waste: total=%d terminal=%d failedAtt=%d replayOH=%d",
		res.TotalWasteSteps, res.TerminalWasteSteps,
		res.FailedAttemptWasteSteps, res.ReplayOverheadSteps)

	assertEqual(t, "EventualSuccessCount", 0, res.EventualSuccessCount)
	assertEqual(t, "TotalExecutedSteps", 20, res.TotalExecutedSteps)
	assertEqual(t, "UsefulSteps", 0, res.UsefulSteps)
	assertEqual(t, "TotalWasteSteps", 20, res.TotalWasteSteps)
	assertEqual(t, "TerminalWasteSteps", 20, res.TerminalWasteSteps)
	assertEqual(t, "FailedAttemptWasteSteps", 0, res.FailedAttemptWasteSteps)
	assertEqual(t, "ReplayOverheadSteps", 0, res.ReplayOverheadSteps)
	assertEqual(t, "AvoidedReplayStepsTotal", 0, res.AvoidedReplayStepsTotal)
	// Decomposition invariant: TW == Terminal + FailedAttempt + ReplayOverhead
	assertWasteDecomposition(t, res)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 2 — Naive retry (no failures): K first-pass steps = replay overhead
// ─────────────────────────────────────────────────────────────────────────────

// TestWasteAccountingForNaiveRetry verifies the key insight:
// when all retries succeed, the K first-pass steps are replay overhead —
// they were re-done by the full retry, making the original execution redundant.
//
// Setup: sessions=10, N=5, K=2, interrupt_rate=100%, retry_fail=0, max=1.
// Expected (hand-derived):
//   TotalExecutedSteps = 10×(K+N) = 10×7 = 70
//   EventualSuccess    = 10
//   UsefulSteps        = 10×5 = 50      (the 10 successful retries)
//   TotalWasteSteps    = 70-50 = 20
//   ReplayOverheadSteps = K×SuccessfulRetries = 2×10 = 20
//   TerminalWasteSteps = 0
//   FailedAttemptWaste = 0
func TestWasteAccountingForNaiveRetry(t *testing.T) {
	cfg := RecoveryExperimentConfigV2{
		Sessions:              10,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      1.0,
		RetryFailureRate:      0.0, // retries never fail
		MaxAttempts:           1,
		Seed:                  1,
	}

	res := runControlledRecoveryExperimentV2(cfg, PolicyNaiveRetry)
	t.Logf("Naive retry waste: total=%d terminal=%d failedAtt=%d replayOH=%d",
		res.TotalWasteSteps, res.TerminalWasteSteps,
		res.FailedAttemptWasteSteps, res.ReplayOverheadSteps)

	assertEqual(t, "EventualSuccessCount", 10, res.EventualSuccessCount)
	assertEqual(t, "TotalExecutedSteps", 70, res.TotalExecutedSteps)
	assertEqual(t, "UsefulSteps", 50, res.UsefulSteps)
	assertEqual(t, "TotalWasteSteps", 20, res.TotalWasteSteps)
	assertEqual(t, "ReplayOverheadSteps", 20, res.ReplayOverheadSteps)
	assertEqual(t, "TerminalWasteSteps", 0, res.TerminalWasteSteps)
	assertEqual(t, "FailedAttemptWasteSteps", 0, res.FailedAttemptWasteSteps)
	assertWasteDecomposition(t, res)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 3 — PlanGate-R (no failures): zero waste, all steps useful
// ─────────────────────────────────────────────────────────────────────────────

// TestWasteAccountingForPlanGateR verifies the strongest PlanGate-R claim:
// when all recoveries succeed, TotalWasteSteps = 0.
// The first-pass K steps ARE part of the final success path (checkpointed),
// and recovery adds only the remaining N-K steps. Total = N per session.
//
// Setup: sessions=10, N=5, K=2, interrupt_rate=100%, rec_fail=0, max=1.
// Expected:
//   TotalExecutedSteps = 10×5 = 50  (= K + (N-K) per session)
//   UsefulSteps        = 50
//   TotalWasteSteps    = 0
//   ReplayOverheadSteps= 0          (PlanGate-R never replays K prefix)
//   AvoidedReplay      = K×10 = 20  (K steps saved per recovery vs naive retry)
//   SavedComputeOnSucc = K×10 = 20
func TestWasteAccountingForPlanGateR(t *testing.T) {
	cfg := RecoveryExperimentConfigV2{
		Sessions:              10,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      1.0,
		RecoveryFailureRate:   0.0, // recoveries never fail
		MaxAttempts:           1,
		Seed:                  1,
	}

	res := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateR)
	t.Logf("PlanGate-R waste: total=%d terminal=%d failedAtt=%d replayOH=%d  avoidReplay=%d  savedOnSucc=%d",
		res.TotalWasteSteps, res.TerminalWasteSteps,
		res.FailedAttemptWasteSteps, res.ReplayOverheadSteps,
		res.AvoidedReplayStepsTotal, res.SavedComputeStepsOnSuccess)

	assertEqual(t, "EventualSuccessCount", 10, res.EventualSuccessCount)
	assertEqual(t, "RecoveredSuccessCount", 10, res.RecoveredSuccessCount)
	assertEqual(t, "TotalExecutedSteps", 50, res.TotalExecutedSteps)
	assertEqual(t, "UsefulSteps", 50, res.UsefulSteps)
	assertEqual(t, "TotalWasteSteps", 0, res.TotalWasteSteps)
	assertEqual(t, "ReplayOverheadSteps", 0, res.ReplayOverheadSteps)
	assertEqual(t, "TerminalWasteSteps", 0, res.TerminalWasteSteps)
	assertEqual(t, "FailedAttemptWasteSteps", 0, res.FailedAttemptWasteSteps)
	assertEqual(t, "AvoidedReplayStepsTotal", 20, res.AvoidedReplayStepsTotal)
	assertEqual(t, "SavedComputeStepsOnSuccess", 20, res.SavedComputeStepsOnSuccess)
	assertWasteDecomposition(t, res)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 4 — Secondary failures: waste ordering still holds
// ─────────────────────────────────────────────────────────────────────────────

// TestWasteAccountingWithSecondaryFailures verifies that the canonical waste
// definitions remain correct under non-trivial secondary failure probability.
//
// Setup: sessions=100, N=5, K=2, interrupt_rate=50%, fail_rate=30%, max=2.
func TestWasteAccountingWithSecondaryFailures(t *testing.T) {
	cfg := RecoveryExperimentConfigV2{
		Sessions:              100,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      0.5,
		RecoveryFailureRate:   0.3,
		RetryFailureRate:      0.3,
		MaxAttempts:           2,
		Seed:                  42,
	}

	naive := runControlledRecoveryExperimentV2(cfg, PolicyNaiveRetry)
	pg    := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateR)

	// ── Formula integrity: TotalWasteSteps = TotalExecuted - EventualSuccess×N ──
	N := cfg.StepsPerSession
	naiveExpWaste := naive.TotalExecutedSteps - naive.EventualSuccessCount*N
	pgExpWaste    := pg.TotalExecutedSteps - pg.EventualSuccessCount*N
	assertEqual(t, "naive TotalWasteSteps", naiveExpWaste, naive.TotalWasteSteps)
	assertEqual(t, "pg-r TotalWasteSteps", pgExpWaste, pg.TotalWasteSteps)

	// ── PlanGate-R TotalWasteSteps < Naive ──────────────────────────────────
	if pg.TotalWasteSteps >= naive.TotalWasteSteps {
		t.Errorf("pg-r TotalWasteSteps (%d) must be < naive (%d)",
			pg.TotalWasteSteps, naive.TotalWasteSteps)
	}

	// ── AvoidedReplayStepsTotal > 0 ──────────────────────────────────────────
	if pg.AvoidedReplayStepsTotal == 0 {
		t.Error("PlanGate-R AvoidedReplayStepsTotal must be > 0")
	}

	// ── TotalExecutedSteps: pg-r < naive ─────────────────────────────────────
	if pg.TotalExecutedSteps >= naive.TotalExecutedSteps {
		t.Errorf("pg-r TotalExecutedSteps (%d) must be < naive (%d)",
			pg.TotalExecutedSteps, naive.TotalExecutedSteps)
	}

	// ── Decomposition invariants ─────────────────────────────────────────────
	assertWasteDecomposition(t, naive)
	assertWasteDecomposition(t, pg)

	// ── PlanGate-R replay overhead is always 0 ────────────────────────────────
	if pg.ReplayOverheadSteps != 0 {
		t.Errorf("PlanGate-R ReplayOverheadSteps must be 0, got %d", pg.ReplayOverheadSteps)
	}

	t.Logf("  naive: exec=%d  useful=%d  waste=%d  (terminal=%d  failAtt=%d  replay=%d)",
		naive.TotalExecutedSteps, naive.UsefulSteps, naive.TotalWasteSteps,
		naive.TerminalWasteSteps, naive.FailedAttemptWasteSteps, naive.ReplayOverheadSteps)
	t.Logf("  pg-r:  exec=%d  useful=%d  waste=%d  (terminal=%d  failAtt=%d  replay=%d)  avdReplay=%d",
		pg.TotalExecutedSteps, pg.UsefulSteps, pg.TotalWasteSteps,
		pg.TerminalWasteSteps, pg.FailedAttemptWasteSteps, pg.ReplayOverheadSteps,
		pg.AvoidedReplayStepsTotal)
	t.Logf("  waste reduction: -%d steps  (%.1f%%)",
		naive.TotalWasteSteps-pg.TotalWasteSteps,
		100*float64(naive.TotalWasteSteps-pg.TotalWasteSteps)/float64(naive.TotalWasteSteps))
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: Verify decomposition identity across all policies and configurations
// ─────────────────────────────────────────────────────────────────────────────

// TestWasteDecompositionIdentityHoldsUniversally sweeps K values and
// verifies TW == Terminal + FailedAttempt + ReplayOverhead at every point.
func TestWasteDecompositionIdentityHoldsUniversally(t *testing.T) {
	for _, K := range []int{0, 1, 2, 3, 4} {
		for _, rate := range []float64{0.0, 0.3, 0.6, 1.0} {
			cfg := RecoveryExperimentConfigV2{
				Sessions:              50,
				StepsPerSession:       5,
				InterruptionAfterStep: K,
				InterruptionRate:      0.5,
				RecoveryFailureRate:   rate,
				RetryFailureRate:      rate,
				MaxAttempts:           2,
				Seed:                  int64(K*100 + int(rate*10)),
			}
			for _, policy := range []RecoveryPolicy{PolicyPlanGateBase, PolicyNaiveRetry, PolicyPlanGateR} {
				res := runControlledRecoveryExperimentV2(cfg, policy)
				if failed := assertWasteDecompositionSilent(res); failed != "" {
					t.Errorf("K=%d rate=%.1f policy=%s: %s", K, rate, policy, failed)
				}
			}
		}
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

func assertEqual(t *testing.T, name string, want, got int) {
	t.Helper()
	if want != got {
		t.Errorf("%s: want %d, got %d", name, want, got)
	}
}

// assertWasteDecomposition checks TotalWasteSteps == sum of sub-components.
func assertWasteDecomposition(t *testing.T, r RecoveryExperimentResultV2) {
	t.Helper()
	if msg := assertWasteDecompositionSilent(r); msg != "" {
		t.Errorf("policy=%s: %s", r.Policy, msg)
	}
}

func assertWasteDecompositionSilent(r RecoveryExperimentResultV2) string {
	sum := r.TerminalWasteSteps + r.FailedAttemptWasteSteps + r.ReplayOverheadSteps
	if sum != r.TotalWasteSteps {
		return fmt.Sprintf(
			"waste decomposition: Terminal(%d)+FailedAtt(%d)+ReplayOH(%d)=%d ≠ TotalWaste(%d)",
			r.TerminalWasteSteps, r.FailedAttemptWasteSteps, r.ReplayOverheadSteps, sum, r.TotalWasteSteps)
	}
	// Also verify TotalWasteSteps == TotalExecutedSteps - UsefulSteps.
	if r.TotalWasteSteps != r.TotalExecutedSteps-r.UsefulSteps {
		return fmt.Sprintf(
			"TotalWaste(%d) ≠ TotalExec(%d) - Useful(%d)",
			r.TotalWasteSteps, r.TotalExecutedSteps, r.UsefulSteps)
	}
	return ""
}
