package plangate

// ─────────────────────────────────────────────────────────────────────────────
// PlanGate-R Phase 4A: Recovery Queue / Quota Scaffold
//
// This file contains purely functional, stateless helpers for recovery queue
// quota calculation. No runtime admission is performed here.
//
// Phase 4A does NOT implement:
//   - actual scheduling of recovery sessions
//   - sessionCap integration
//   - P0/P1/P2 preemption
//   - HTTP 202 / recovery token
//   - client polling
// ─────────────────────────────────────────────────────────────────────────────

// RecoveryQuotaConfig controls what fraction of available session slots may be
// reserved for recovery sessions. The intent is to avoid starving new P0
// sessions when many stale checkpoints are queued for recovery.
type RecoveryQuotaConfig struct {
	// MaxRecoveryFraction is the fraction of available (non-P0-active) slots
	// that may be used by recovery sessions. Range: [0, 1].
	// 0 disables recovery scheduling entirely.
	// Values > 1 are clamped to 1.
	MaxRecoveryFraction float64
}

// DefaultRecoveryQuotaConfig returns the recommended quota configuration.
// 40% of available slots may be used for recovery, leaving the majority for
// new P0 (fresh) sessions.
func DefaultRecoveryQuotaConfig() RecoveryQuotaConfig {
	return RecoveryQuotaConfig{MaxRecoveryFraction: 0.4}
}

// maxRecoverySlots returns the maximum number of concurrent recovery sessions
// that may be admitted given the current gateway state.
//
// Parameters:
//   - totalSlots:  the total configured session capacity (M).
//   - activeP0:    the number of currently active P0 (non-recovery) sessions.
//   - frac:        MaxRecoveryFraction from RecoveryQuotaConfig (clamped to [0,1]).
//
// Logic:
//
//	available := totalSlots - activeP0
//	available <= 0        → 0  (no headroom for recovery)
//	frac <= 0             → 0  (recovery disabled)
//	frac > 1              → treat as 1
//	result = floor(available * clamp(frac, 0, 1))
//	result is also capped at available (no negative, no overflow).
func maxRecoverySlots(totalSlots int, activeP0 int, frac float64) int {
	available := totalSlots - activeP0
	if available <= 0 {
		return 0
	}
	if frac <= 0 {
		return 0
	}
	if frac > 1.0 {
		frac = 1.0
	}
	slots := int(float64(available) * frac)
	if slots > available {
		slots = available
	}
	return slots
}

// canAdmitRecovery returns true when the current number of active recovery
// sessions is strictly below the computed quota.
//
// Parameters:
//   - totalSlots:     total configured session capacity (M).
//   - activeP0:       currently active P0 (non-recovery) sessions.
//   - activeRecovery: currently active recovery sessions.
//   - frac:           MaxRecoveryFraction from RecoveryQuotaConfig.
//
// This is a pure function. It does NOT modify any sessionCap, semaphore, or
// counter. Actual admission must be gated separately (Phase 4B).
func canAdmitRecovery(totalSlots int, activeP0 int, activeRecovery int, frac float64) bool {
	return activeRecovery < maxRecoverySlots(totalSlots, activeP0, frac)
}
