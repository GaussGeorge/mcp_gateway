package plangate

import "time"

// RecoveryConfig holds the configuration for PlanGate-R checkpoint-based recovery.
//
// Enabled defaults to false. All PlanGate behaviour is identical to the
// pre-Phase-3 baseline when Enabled=false.
//
// Phase 3 supports only Store="inmemory". Redis/BoltDB backends are deferred
// to Phase 4+.
type RecoveryConfig struct {
	Enabled     bool
	TTL         time.Duration
	MaxAttempts int
	Store       string
}

// DefaultRecoveryConfig returns a RecoveryConfig with recovery disabled.
// All fields are safe to use as-is; no checkpoint store is created.
func DefaultRecoveryConfig() RecoveryConfig {
	return RecoveryConfig{
		Enabled:     false,
		TTL:         300 * time.Second,
		MaxAttempts: 3,
		Store:       "inmemory",
	}
}
