package plangate

// PlanGate-R Phase 6B: Controlled Recovery Experiment — Secondary Failure Probability
//
// ══════════════════════════════════════════════════════════════════════════════
// SCOPE NOTE  (same as Phase 6A)
// ══════════════════════════════════════════════════════════════════════════════
// Pure in-memory simulator. No real network, no real LLM, no MCPDPServer, no
// scripts/*, no baseline/*, no paper CSVs.
//
// Phase 6A proved: "under ideal recovery (always succeeds), PlanGate-R reaches
// the same eventual success as naive retry while saving K steps per recovery."
//
// Phase 6B removes that assumption. We add:
//   · RecoveryFailureRate  – prob that a PlanGate-R attempt fails
//   · RetryFailureRate     – prob that a naive-retry attempt fails
//   · MaxAttempts          – upper bound on recovery/retry attempts
//
// Key questions answered here:
//   1. Does PlanGate-R still outperform PlanGate-base when recovery can fail?
//   2. Does PlanGate-R still save compute vs naive retry when recovery can fail?
//   3. How does max_attempts trade eventual_success against total executed steps?
//   4. What is the recommended default max_attempts?
//
// Phase 6B simplification: a failed attempt still executes its steps
// (N-K for PlanGate-R, N for naive retry) before returning failure. This models
// the realistic case where the backend times out after partial execution.
// ══════════════════════════════════════════════════════════════════════════════

import (
	"fmt"
	"math/rand" //nolint:gosec // deterministic seed is intentional for reproducibility
	"strings"
	"testing"
)

// ─────────────────────────────────────────────────────────────────────────────
// V2 Config & Result types
// ─────────────────────────────────────────────────────────────────────────────

// RecoveryExperimentConfigV2 extends the Phase 6A config with secondary-failure
// parameters and a per-policy attempt cap.
type RecoveryExperimentConfigV2 struct {
	Sessions              int
	StepsPerSession       int     // N: total steps per session
	InterruptionAfterStep int     // K: steps that complete before the interruption
	InterruptionRate      float64 // probability [0,1] a session is interrupted
	RecoveryFailureRate   float64 // probability each PlanGate-R attempt fails
	RetryFailureRate      float64 // probability each naive-retry attempt fails
	MaxAttempts           int     // max recovery/retry attempts (0 = no recovery)
	Seed                  int64   // deterministic seed
}

// RecoveryExperimentResultV2 collects all metrics for a Phase 6B/6C run.
//
// ── Phase 6C Waste Accounting (canonical cascade-waste definitions) ────────
//
// Fairness guarantee: for tests where RetryFailureRate == RecoveryFailureRate,
// the per-session attempt outcomes are IDENTICAL between naive retry and
// PlanGate-R (same per-session RNG seed). The only difference is the number of
// steps executed per attempt (N vs N-K), not the success/failure outcome.
type RecoveryExperimentResultV2 struct {
	Policy      string
	MaxAttempts int

	TotalSessions         int
	ImmediateSuccessCount int
	EventualSuccessCount  int
	RecoveredSuccessCount int // PlanGate-R specific: successes via checkpoint resume
	FailedTerminalCount   int

	TotalExecutedSteps int

	// ── Phase 6C: Refined waste accounting ──────────────────────────────────

	// UsefulSteps: steps that form part of a final successful execution path.
	// = EventualSuccessCount × StepsPerSession (derived, stored for convenience)
	UsefulSteps int

	// TotalWasteSteps: steps that did NOT contribute to any eventual success.
	// = TotalExecutedSteps - UsefulSteps
	// Decomposes as: TerminalWasteSteps + FailedAttemptWasteSteps + ReplayOverheadSteps.
	//
	// NOTE: the old 'WasteSteps' field (= failed-attempt steps only) is DEPRECATED
	// in favour of TotalWasteSteps, which includes terminal-session waste.
	TotalWasteSteps int

	// TerminalWasteSteps: all steps executed in sessions that ULTIMATELY FAIL
	// (after exhausting max_attempts). Includes K first-pass steps + all
	// subsequent retry/recovery attempt steps for those sessions.
	// PlanGate-base: K × (all interrupted sessions).
	// Naive retry / PlanGate-R: K + attempts×cost per terminal session.
	TerminalWasteSteps int

	// FailedAttemptWasteSteps: steps from attempts that FAIL but belong to
	// sessions that EVENTUALLY succeed on a later attempt.
	// For naive retry:  N  × (failed attempts before eventual success)
	// For PlanGate-R:  (N-K) × (failed recoveries before eventual success)
	FailedAttemptWasteSteps int

	// ReplayOverheadSteps: K steps replayed per SUCCESSFUL naive-retry attempt.
	// The original first-pass K steps become overhead once the full retry
	// re-executes them from step 0.
	// PlanGate-R: always 0 (recovery continues from K, never replays prefix).
	// = K × SuccessfulRetries
	ReplayOverheadSteps int

	// ─────────────────────────────────────────────────────────────────────────

	// SkippedSteps: K steps NOT re-executed per SUCCESSFUL PlanGate-R recovery.
	// = K × SuccessfulRecoveries  (= SavedComputeStepsOnSuccess)
	SkippedSteps int

	// SavedComputeStepsOnSuccess: K steps saved per successful recovery.
	// The first-pass K steps ARE part of the final execution path (checkpointed),
	// so comparing to naive retry: this many steps are saved from repetition.
	// = K × SuccessfulRecoveries
	SavedComputeStepsOnSuccess int

	// AvoidedReplayStepsTotal: K steps avoided per PlanGate-R attempt vs naive.
	// Applies UNCONDITIONALLY even to failed attempts.
	// = K × AttemptedRecoveries
	AvoidedReplayStepsTotal int

	// Attempt counters
	AttemptedRecoveries  int
	SuccessfulRecoveries int
	FailedRecoveries     int
	AttemptedRetries     int
	SuccessfulRetries    int
	FailedRetries        int

	// Sessions permanently lost after exhausting max attempts
	TerminalAfterMaxAttempts int
}

// EventualSuccessRate = EventualSuccessCount / TotalSessions.
func (r RecoveryExperimentResultV2) EventualSuccessRate() float64 {
	if r.TotalSessions == 0 {
		return 0
	}
	return float64(r.EventualSuccessCount) / float64(r.TotalSessions)
}

// ExecutedStepsPerSuccess = TotalExecutedSteps / EventualSuccessCount.
// Returns -1 when no sessions succeed (undefined).
func (r RecoveryExperimentResultV2) ExecutedStepsPerSuccess() float64 {
	if r.EventualSuccessCount == 0 {
		return -1
	}
	return float64(r.TotalExecutedSteps) / float64(r.EventualSuccessCount)
}

// ─────────────────────────────────────────────────────────────────────────────
// Simulator core
// ─────────────────────────────────────────────────────────────────────────────

// runControlledRecoveryExperimentV2 runs a deterministic simulation under
// secondary-failure conditions.
//
// RNG design:
//   mainRng(cfg.Seed)          → determines which sessions are interrupted
//   sessionRng(seed, i)        → determines attempt outcomes for session i
//
// Both naive-retry and PlanGate-R use the SAME sessionRng formula so that, when
// RetryFailureRate == RecoveryFailureRate, the per-attempt outcomes are
// identical. The policies differ only in how many steps each attempt executes.
func runControlledRecoveryExperimentV2(cfg RecoveryExperimentConfigV2, policy RecoveryPolicy) RecoveryExperimentResultV2 {
	N := cfg.StepsPerSession
	K := cfg.InterruptionAfterStep

	// ── Phase 1: determine interruption map (consistent across all policies) ──
	mainRng := rand.New(rand.NewSource(cfg.Seed)) //nolint:gosec
	interrupted := make([]bool, cfg.Sessions)
	for i := 0; i < cfg.Sessions; i++ {
		interrupted[i] = mainRng.Float64() < cfg.InterruptionRate
	}

	res := RecoveryExperimentResultV2{
		Policy:        string(policy),
		MaxAttempts:   cfg.MaxAttempts,
		TotalSessions: cfg.Sessions,
	}

	// ── Phase 2: simulate each session ────────────────────────────────────────
	for i := 0; i < cfg.Sessions; i++ {
		if !interrupted[i] {
			// Happy path: all N steps complete, immediate success.
			res.TotalExecutedSteps += N
			res.ImmediateSuccessCount++
			res.EventualSuccessCount++
			continue
		}

		// First-pass: K steps run, then recoverable interruption occurs.
		// This cost is the same for ALL three policies.
		res.TotalExecutedSteps += K

		switch policy {

		case PolicyPlanGateBase:
			// No recovery mechanism. The session is permanently lost.
			res.FailedTerminalCount++
			res.TerminalAfterMaxAttempts++
			// The K first-pass steps are all terminal waste.
			res.TerminalWasteSteps += K

		case PolicyNaiveRetry:
			// Each retry executes N steps from scratch.
			// The K first-pass steps become replay overhead once a retry succeeds.
			sessionRng := rand.New(rand.NewSource(cfg.Seed + int64(i+1)*997)) //nolint:gosec
			succeeded := false
			failedAttemptsBefore := 0
			totalAttemptsMade := 0
			for attempt := 0; attempt < cfg.MaxAttempts; attempt++ {
				totalAttemptsMade++
				res.AttemptedRetries++
				res.TotalExecutedSteps += N
				if sessionRng.Float64() >= cfg.RetryFailureRate {
					// Retry succeeded.
					res.SuccessfulRetries++
					res.EventualSuccessCount++
					succeeded = true
					break
				}
				// Retry failed.
				res.FailedRetries++
				failedAttemptsBefore++
			}
			if succeeded {
				// The K first-pass steps are now replay overhead: the successful
				// retry re-executed them from step 0, making the first-pass useless.
				res.ReplayOverheadSteps += K
				// Steps spent in failed attempts before eventual success.
				res.FailedAttemptWasteSteps += N * failedAttemptsBefore
			} else {
				res.FailedTerminalCount++
				res.TerminalAfterMaxAttempts++
				// All steps from this terminal session are wasted:
				// K (first pass) + N × totalAttemptsMade (all failed retries).
				res.TerminalWasteSteps += K + N*totalAttemptsMade
			}

		case PolicyPlanGateR:
			// Each recovery executes only the N-K remaining steps from checkpoint K.
			// The K completed first-pass steps are NEVER re-executed; they form
			// part of the successful execution path when recovery succeeds.
			sessionRng := rand.New(rand.NewSource(cfg.Seed + int64(i+1)*997)) //nolint:gosec
			succeeded := false
			failedAttemptsBefore := 0
			totalAttemptsMade := 0
			for attempt := 0; attempt < cfg.MaxAttempts; attempt++ {
				totalAttemptsMade++
				res.AttemptedRecoveries++
				res.AvoidedReplayStepsTotal += K // K steps avoided vs naive retry, per attempt
				res.TotalExecutedSteps += (N - K)
				if sessionRng.Float64() >= cfg.RecoveryFailureRate {
					// Recovery succeeded.
					res.SuccessfulRecoveries++
					res.RecoveredSuccessCount++
					res.SkippedSteps += K
					res.SavedComputeStepsOnSuccess += K
					res.EventualSuccessCount++
					succeeded = true
					break
				}
				// Recovery failed.
				res.FailedRecoveries++
				failedAttemptsBefore++
			}
			if succeeded {
				// ReplayOverheadSteps stays 0: K first-pass steps ARE useful
				// (checkpointed, part of the final success path).
				// Steps from failed attempts before eventual success.
				res.FailedAttemptWasteSteps += (N - K) * failedAttemptsBefore
			} else {
				res.FailedTerminalCount++
				res.TerminalAfterMaxAttempts++
				// All steps from this terminal session are wasted:
				// K (first pass) + (N-K) × totalAttemptsMade (all failed recoveries).
				res.TerminalWasteSteps += K + (N-K)*totalAttemptsMade
			}
		}
	}

	// ── Phase 6C: derived waste metrics (canonical cascade-waste accounting) ──
	res.UsefulSteps = res.EventualSuccessCount * cfg.StepsPerSession
	res.TotalWasteSteps = res.TotalExecutedSteps - res.UsefulSteps

	return res
}

// ─────────────────────────────────────────────────────────────────────────────
// Table printers
// ─────────────────────────────────────────────────────────────────────────────

// printV2ExperimentTable prints Table 1: 3-policy secondary-failure comparison.
// Phase 6C: updated columns to include full cascade-waste breakdown.
func printV2ExperimentTable(t *testing.T, cfg RecoveryExperimentConfigV2, results []RecoveryExperimentResultV2) {
	t.Helper()
	sep := strings.Repeat("─", 140)
	t.Logf("\n%s", sep)
	t.Logf("Phase 6C  sessions=%d  N=%d  K=%d  interrupt=%.0f%%  rec_fail=%.0f%%  retry_fail=%.0f%%  max_att=%d  seed=%d",
		cfg.Sessions, cfg.StepsPerSession, cfg.InterruptionAfterStep,
		cfg.InterruptionRate*100, cfg.RecoveryFailureRate*100, cfg.RetryFailureRate*100,
		cfg.MaxAttempts, cfg.Seed)
	t.Logf("%s", sep)
	t.Logf("  %-18s  %14s  %9s  %9s  %12s  %13s  %18s  %14s  %13s  %12s",
		"Policy", "Eventual Succ%", "Exec", "Useful", "TotalWaste",
		"TerminalWaste", "FailedAttemptWaste", "ReplayOverhead", "AvoidedReplay", "Steps/Succ")
	t.Logf("%s", sep)
	for _, r := range results {
		sps := r.ExecutedStepsPerSuccess()
		spsStr := "N/A"
		if sps >= 0 {
			spsStr = fmt.Sprintf("%.2f", sps)
		}
		t.Logf("  %-18s  %5d/%-4d%5.1f%%  %8d   %8d   %11d   %12d   %16d   %13d   %12d   %s",
			r.Policy,
			r.EventualSuccessCount, r.TotalSessions, r.EventualSuccessRate()*100,
			r.TotalExecutedSteps,
			r.UsefulSteps,
			r.TotalWasteSteps,
			r.TerminalWasteSteps,
			r.FailedAttemptWasteSteps,
			r.ReplayOverheadSteps,
			r.AvoidedReplayStepsTotal,
			spsStr,
		)
	}
	t.Logf("%s", sep)
	if len(results) == 3 {
		base, naive, pg := results[0], results[1], results[2]
		successGain := pg.EventualSuccessCount - base.EventualSuccessCount
		stepsSaving := naive.TotalExecutedSteps - pg.TotalExecutedSteps
		wasteSaving := naive.TotalWasteSteps - pg.TotalWasteSteps
		t.Logf("  PlanGate-R vs PlanGate-base  success gain          = +%d sessions (+%.1f%%)",
			successGain, 100*float64(successGain)/float64(cfg.Sessions))
		t.Logf("  PlanGate-R vs Naive-retry    exec steps saving      = -%d steps  (%.1f%%)",
			stepsSaving, 100*float64(stepsSaving)/float64(naive.TotalExecutedSteps))
		t.Logf("  PlanGate-R vs Naive-retry    total waste reduction  = -%d steps  (%.1f%%)",
			wasteSaving, 100*float64(wasteSaving)/float64(max(naive.TotalWasteSteps, 1)))
		t.Logf("  PlanGate-R avoided_replay    = K(%d) × %d attempts = %d (identity=%v)",
			cfg.InterruptionAfterStep, pg.AttemptedRecoveries, pg.AvoidedReplayStepsTotal,
			stepsSaving == pg.AvoidedReplayStepsTotal)
	}
	t.Logf("%s\n", sep)
}

// printMaxAttemptsTable prints Table 2: trade-off sweep over max_attempts.
func printMaxAttemptsTable(t *testing.T, sweepResults [][2]RecoveryExperimentResultV2) {
	t.Helper()
	sep := strings.Repeat("─", 112)
	t.Logf("\n%s\nMax-Attempts Tradeoff Sweep\n%s", sep, sep)
	t.Logf("  %-4s  %-18s  %15s  %13s  %11s  %13s  %10s",
		"Att", "Policy", "Eventual Succ%", "Exec Steps", "Waste", "Steps/Succ", "Terminal")
	t.Logf("%s", sep)
	for _, pair := range sweepResults {
		for _, r := range pair {
			sps := r.ExecutedStepsPerSuccess()
			spsStr := "N/A"
			if sps >= 0 {
				spsStr = fmt.Sprintf("%.2f", sps)
			}
			t.Logf("  %-4d  %-18s  %5d/%-4d %5.1f%%  %11d   %11d  %11s   %8d",
				r.MaxAttempts,
				r.Policy,
				r.EventualSuccessCount, r.TotalSessions, r.EventualSuccessRate()*100,
				r.TotalExecutedSteps,
				r.TotalWasteSteps,
				spsStr,
				r.TerminalAfterMaxAttempts,
			)
		}
		t.Logf("  %s", strings.Repeat("·", 100))
	}
	t.Logf("%s\n", sep)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 1 — 3-policy comparison with secondary failures
// ─────────────────────────────────────────────────────────────────────────────

// TestRecoveryExperimentWithSecondaryFailures compares all three policies
// when both recovery and retry can fail with probability 0.3.
//
// Fixed setup: sessions=100, N=5, K=2, interrupt_rate=50%, fail_rate=30%,
// max_attempts=2, seed=42.
//
// Provable invariants (deterministic with fixed seed):
//   a) pg.EventualSuccess > base.EventualSuccess (recovery saves interrupted sessions)
//   b) naive.EventualSuccess == pg.EventualSuccess  (same sessionRng, same rates)
//   c) pg.TotalExecutedSteps < naive.TotalExecutedSteps  (N-K < N per attempt)
//   d) pg.AvoidedReplayStepsTotal == K × pg.AttemptedRecoveries  (construction)
//   e) naive.TotalExecutedSteps - pg.TotalExecutedSteps == pg.AvoidedReplayStepsTotal (identity)
func TestRecoveryExperimentWithSecondaryFailures(t *testing.T) {
	cfg := RecoveryExperimentConfigV2{
		Sessions:              100,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      0.5,
		RecoveryFailureRate:   0.3,
		RetryFailureRate:      0.3, // equal for fair outcome comparison
		MaxAttempts:           2,
		Seed:                  42,
	}

	base  := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateBase)
	naive := runControlledRecoveryExperimentV2(cfg, PolicyNaiveRetry)
	pg    := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateR)

	printV2ExperimentTable(t, cfg, []RecoveryExperimentResultV2{base, naive, pg})

	// ── (a) PlanGate-R recovers interrupted sessions → success > base ──────
	if pg.EventualSuccessCount <= base.EventualSuccessCount {
		t.Errorf("pg-r EventualSuccess (%d) must be > base (%d)",
			pg.EventualSuccessCount, base.EventualSuccessCount)
	}

	// ── (b) Same sessionRng + same rates → exactly identical outcomes ──────
	if pg.EventualSuccessCount != naive.EventualSuccessCount {
		t.Errorf("pg-r and naive must reach identical EventualSuccess (same sessionRng): pg=%d, naive=%d",
			pg.EventualSuccessCount, naive.EventualSuccessCount)
	}
	if pg.FailedTerminalCount != naive.FailedTerminalCount {
		t.Errorf("terminal counts must match: pg=%d, naive=%d",
			pg.FailedTerminalCount, naive.FailedTerminalCount)
	}

	// ── (c) PlanGate-R uses fewer total steps (N-K < N per attempt) ────────
	if pg.TotalExecutedSteps >= naive.TotalExecutedSteps {
		t.Errorf("pg-r.TotalExecutedSteps (%d) must be < naive (%d)",
			pg.TotalExecutedSteps, naive.TotalExecutedSteps)
	}

	// ── (d) AvoidedReplayStepsTotal algebraic definition ───────────────────
	K := cfg.InterruptionAfterStep
	expectedAvoided := K * pg.AttemptedRecoveries
	if pg.AvoidedReplayStepsTotal != expectedAvoided {
		t.Errorf("AvoidedReplay (%d) must equal K(%d) × attempts(%d) = %d",
			pg.AvoidedReplayStepsTotal, K, pg.AttemptedRecoveries, expectedAvoided)
	}

	// ── (e) Compute saving == avoided replay (algebraic identity) ──────────
	computeSaving := naive.TotalExecutedSteps - pg.TotalExecutedSteps
	if computeSaving != pg.AvoidedReplayStepsTotal {
		t.Errorf("compute_saving(%d) must equal AvoidedReplay(%d): identity violated",
			computeSaving, pg.AvoidedReplayStepsTotal)
	}

	// ── PlanGate-R must have > 0 avoided replay (some sessions interrupted+attempted) ──
	if pg.AvoidedReplayStepsTotal == 0 {
		t.Error("PlanGate-R must have AvoidedReplayStepsTotal > 0 with interrupt_rate=0.5")
	}

	// ── steps/success: pg-r strictly better than naive ──────────────────────
	pgSPS    := pg.ExecutedStepsPerSuccess()
	naiveSPS := naive.ExecutedStepsPerSuccess()
	if pgSPS >= naiveSPS {
		t.Errorf("pg-r steps/success (%.2f) must be < naive (%.2f)", pgSPS, naiveSPS)
	}
	t.Logf("steps/success  naive=%.3f  pg-r=%.3f  improvement=%.1f%%",
		naiveSPS, pgSPS, 100*(naiveSPS-pgSPS)/naiveSPS)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 2 — max_attempts tradeoff sweep
// ─────────────────────────────────────────────────────────────────────────────

// TestMaxAttemptsTradeoff sweeps max_attempts over {0,1,2,3} for both
// PolicyNaiveRetry and PolicyPlanGateR, verifying the monotone invariants:
//
//   1. EventualSuccessCount is non-decreasing as max_attempts increases.
//      (Mathematical guarantee: more chances can only help.)
//
//   2. TotalExecutedSteps and WasteSteps are non-decreasing.
//      (More attempts = more steps executed, regardless of outcome.)
//
//   3. TerminalAfterMaxAttempts is non-increasing.
//      (Fewer sessions permanently lost as we allow more recovery.)
//
//   4. PlanGate-R WasteSteps < NaiveRetry WasteSteps at each level > 0.
//      (N-K waste per attempt < N waste per attempt.)
func TestMaxAttemptsTradeoff(t *testing.T) {
	baseCfg := RecoveryExperimentConfigV2{
		Sessions:              200,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      0.5,
		RecoveryFailureRate:   0.4,
		RetryFailureRate:      0.4,
		Seed:                  31,
	}

	sweepAttempts := []int{0, 1, 2, 3}
	var sweep [][2]RecoveryExperimentResultV2
	prevNaive := RecoveryExperimentResultV2{}
	prevPg    := RecoveryExperimentResultV2{}
	first     := true

	for _, maxAtt := range sweepAttempts {
		cfg := baseCfg
		cfg.MaxAttempts = maxAtt
		naiveRes := runControlledRecoveryExperimentV2(cfg, PolicyNaiveRetry)
		pgRes    := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateR)
		sweep    = append(sweep, [2]RecoveryExperimentResultV2{naiveRes, pgRes})

		if !first {
			// Monotone success: more attempts → at least as many successes.
			if naiveRes.EventualSuccessCount < prevNaive.EventualSuccessCount {
				t.Errorf("naive: EventualSuccess must be non-decreasing; maxAtt=%d got %d < prev %d",
					maxAtt, naiveRes.EventualSuccessCount, prevNaive.EventualSuccessCount)
			}
			if pgRes.EventualSuccessCount < prevPg.EventualSuccessCount {
				t.Errorf("pg-r: EventualSuccess must be non-decreasing; maxAtt=%d got %d < prev %d",
					maxAtt, pgRes.EventualSuccessCount, prevPg.EventualSuccessCount)
			}

			// Monotone cost: more attempts → at least as many total steps.
			if naiveRes.TotalExecutedSteps < prevNaive.TotalExecutedSteps {
				t.Errorf("naive: TotalExecutedSteps must be non-decreasing; maxAtt=%d got %d < prev %d",
					maxAtt, naiveRes.TotalExecutedSteps, prevNaive.TotalExecutedSteps)
			}
			if pgRes.TotalExecutedSteps < prevPg.TotalExecutedSteps {
				t.Errorf("pg-r: TotalExecutedSteps must be non-decreasing; maxAtt=%d got %d < prev %d",
					maxAtt, pgRes.TotalExecutedSteps, prevPg.TotalExecutedSteps)
			}

			// Monotone terminal: more attempts → fewer permanently-lost sessions.
			if naiveRes.TerminalAfterMaxAttempts > prevNaive.TerminalAfterMaxAttempts {
				t.Errorf("naive: TerminalAfterMaxAttempts must be non-increasing; maxAtt=%d got %d > prev %d",
					maxAtt, naiveRes.TerminalAfterMaxAttempts, prevNaive.TerminalAfterMaxAttempts)
			}
			if pgRes.TerminalAfterMaxAttempts > prevPg.TerminalAfterMaxAttempts {
				t.Errorf("pg-r: TerminalAfterMaxAttempts must be non-increasing; maxAtt=%d got %d > prev %d",
					maxAtt, pgRes.TerminalAfterMaxAttempts, prevPg.TerminalAfterMaxAttempts)
			}
		}

		// PlanGate-R wastes fewer steps per failed attempt than naive retry.
		// (N-K waste vs N waste. Only testable when maxAtt > 0.)
		if maxAtt > 0 && pgRes.TotalWasteSteps > naiveRes.TotalWasteSteps {
			t.Errorf("pg-r TotalWasteSteps (%d) must be <= naive (%d) at maxAtt=%d",
				pgRes.TotalWasteSteps, naiveRes.TotalWasteSteps, maxAtt)
		}

		// PlanGate-R executes fewer total steps than naive at each level > 0.
		if maxAtt > 0 && pgRes.TotalExecutedSteps >= naiveRes.TotalExecutedSteps {
			t.Errorf("pg-r TotalExecuted (%d) must be < naive (%d) at maxAtt=%d",
				pgRes.TotalExecutedSteps, naiveRes.TotalExecutedSteps, maxAtt)
		}

		prevNaive, prevPg = naiveRes, pgRes
		first = false
	}

	printMaxAttemptsTable(t, sweep)

	// Commentary on recommended default.
	t.Logf("Recommendation analysis: comparing max_attempts={1,2,3}")
	for _, pair := range sweep[1:] { // skip maxAtt=0
		pg := pair[1]
		t.Logf("  maxAtt=%d  success=%.1f%%  terminal=%d  steps/succ=%.2f  totalWaste=%d  replayOH=%d  failedAtt=%d",
			pg.MaxAttempts, pg.EventualSuccessRate()*100,
			pg.TerminalAfterMaxAttempts,
			pg.ExecutedStepsPerSuccess(),
			pg.TotalWasteSteps, pg.ReplayOverheadSteps, pg.FailedAttemptWasteSteps)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 3 — high failure rate still shows compute savings
// ─────────────────────────────────────────────────────────────────────────────

// TestPlanGateRStillSavesComputeUnderFailure verifies two guarantees that hold
// even under severe failure rates (50%) with up to 3 attempts:
//
//   G1. AvoidedReplayStepsTotal > 0
//       (Every recovery attempt saves K steps vs naive retry; even failed
//        attempts contribute to avoided replay. This is unconditional.)
//
//   G2. ExecutedStepsPerSuccess(pg-r) < ExecutedStepsPerSuccess(naive)
//       (With same failure outcomes and max_attempts, both reach the same
//        EventualSuccessCount. PlanGate-R always executes fewer steps per
//        attempt. Therefore: same denominator, smaller numerator → better ratio.)
//
//   G3. Algebraic identity:
//       naive.TotalExecutedSteps - pg.TotalExecutedSteps == pg.AvoidedReplayStepsTotal
//       (Because per-attempt difference is always K, summed over all attempts.)
func TestPlanGateRStillSavesComputeUnderFailure(t *testing.T) {
	cfg := RecoveryExperimentConfigV2{
		Sessions:              100,
		StepsPerSession:       5,
		InterruptionAfterStep: 2,
		InterruptionRate:      0.5,
		RecoveryFailureRate:   0.5,
		RetryFailureRate:      0.5,
		MaxAttempts:           3,
		Seed:                  55,
	}

	naive := runControlledRecoveryExperimentV2(cfg, PolicyNaiveRetry)
	pg    := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateR)

	printV2ExperimentTable(t, cfg, []RecoveryExperimentResultV2{naive, pg})

	// ── G1: AvoidedReplayStepsTotal > 0 ────────────────────────────────────
	if pg.AvoidedReplayStepsTotal == 0 {
		t.Error("G1 violated: AvoidedReplayStepsTotal must be > 0 (some sessions interrupted)")
	}
	t.Logf("G1: AvoidedReplayStepsTotal = %d  (K=%d × %d attempts)",
		pg.AvoidedReplayStepsTotal, cfg.InterruptionAfterStep, pg.AttemptedRecoveries)

	// ── G2: steps/success pg-r < naive (same success count, fewer steps) ────
	// Precondition: both must have successes to compute the ratio.
	if pg.EventualSuccessCount == 0 {
		t.Skip("no successes with these parameters; skipping steps/success comparison")
	}
	if pg.EventualSuccessCount != naive.EventualSuccessCount {
		t.Errorf("G2 precondition: same sessionRng → same success count: pg=%d, naive=%d",
			pg.EventualSuccessCount, naive.EventualSuccessCount)
	}
	pgSPS    := pg.ExecutedStepsPerSuccess()
	naiveSPS := naive.ExecutedStepsPerSuccess()
	if pgSPS >= naiveSPS {
		t.Errorf("G2 violated: pg-r steps/success (%.3f) must be < naive (%.3f)", pgSPS, naiveSPS)
	}
	t.Logf("G2: steps/succ  naive=%.3f  pg-r=%.3f  improvement=%.1f%%",
		naiveSPS, pgSPS, 100*(naiveSPS-pgSPS)/naiveSPS)

	// ── G3: algebraic identity ──────────────────────────────────────────────
	computeSaving := naive.TotalExecutedSteps - pg.TotalExecutedSteps
	if computeSaving != pg.AvoidedReplayStepsTotal {
		t.Errorf("G3 violated: compute_saving(%d) ≠ AvoidedReplay(%d)",
			computeSaving, pg.AvoidedReplayStepsTotal)
	}
	t.Logf("G3: identity  naive-steps(%d) - pgr-steps(%d) = %d == AvoidedReplay(%d): %v",
		naive.TotalExecutedSteps, pg.TotalExecutedSteps,
		computeSaving, pg.AvoidedReplayStepsTotal,
		computeSaving == pg.AvoidedReplayStepsTotal)

	// Bonus: PlanGate-R TotalWasteSteps must be < naive retry.
	if pg.TotalWasteSteps >= naive.TotalWasteSteps && naive.TotalWasteSteps > 0 {
		t.Errorf("pg-r TotalWasteSteps (%d) should be < naive (%d)", pg.TotalWasteSteps, naive.TotalWasteSteps)
	}
	t.Logf("TotalWasteSteps  naive=%d  pg-r=%d  reduction=%.1f%%",
		naive.TotalWasteSteps, pg.TotalWasteSteps,
		100*float64(naive.TotalWasteSteps-pg.TotalWasteSteps)/float64(naive.TotalWasteSteps))
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: sensitivity sweep over recovery failure rate
// ─────────────────────────────────────────────────────────────────────────────

// TestPhase6BFailureRateSweep sweeps RecoveryFailureRate over [0.0, 0.2, 0.4, 0.6, 0.8]
// at max_attempts=2 and verifies the saving identity holds at every point.
// This demonstrates that PlanGate-R maintains its compute advantage
// regardless of how often recovery itself fails.
func TestPhase6BFailureRateSweep(t *testing.T) {
	rates := []float64{0.0, 0.2, 0.4, 0.6, 0.8}
	sep := strings.Repeat("─", 95)
	t.Logf("\n%s\nFailure Rate Sweep  (sessions=200, N=5, K=2, max_att=2, interrupt=50%%, seed=13)\n%s",
		sep, sep)
	t.Logf("  %-6s  %-12s  %-14s  %-12s  %-12s  %-12s  %-10s",
		"FailR", "pg-r evsucc%", "naive steps", "pg-r steps", "saving", "AvdReplay", "identity?")
	t.Logf("%s", sep)

	for _, rate := range rates {
		cfg := RecoveryExperimentConfigV2{
			Sessions:              200,
			StepsPerSession:       5,
			InterruptionAfterStep: 2,
			InterruptionRate:      0.5,
			RecoveryFailureRate:   rate,
			RetryFailureRate:      rate,
			MaxAttempts:           2,
			Seed:                  13,
		}
		naive := runControlledRecoveryExperimentV2(cfg, PolicyNaiveRetry)
		pg    := runControlledRecoveryExperimentV2(cfg, PolicyPlanGateR)

		saving  := naive.TotalExecutedSteps - pg.TotalExecutedSteps
		identOK := saving == pg.AvoidedReplayStepsTotal
		t.Logf("  %-6.0f%%  %4d/%-4d %5.1f%%  %11d    %10d  %10d  %10d   %v",
			rate*100,
			pg.EventualSuccessCount, cfg.Sessions, pg.EventualSuccessRate()*100,
			naive.TotalExecutedSteps,
			pg.TotalExecutedSteps,
			saving,
			pg.AvoidedReplayStepsTotal,
			identOK,
		)

		// Identity must hold at every failure rate.
		if !identOK {
			t.Errorf("rate=%.1f: algebraic identity violated: saving=%d, AvoidedReplay=%d",
				rate, saving, pg.AvoidedReplayStepsTotal)
		}
		// PlanGate-R must always execute less than naive (when any attempts are made).
		if pg.AttemptedRecoveries > 0 && pg.TotalExecutedSteps >= naive.TotalExecutedSteps {
			t.Errorf("rate=%.1f: pg-r total (%d) must be < naive total (%d)",
				rate, pg.TotalExecutedSteps, naive.TotalExecutedSteps)
		}
	}
	t.Logf("%s\n", sep)
}
