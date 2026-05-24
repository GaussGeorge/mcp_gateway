package plangate

import (
	"context"
	"time"
)

// TryAdmitResult is returned by SessionStateStore.TryAdmitSession.
type TryAdmitResult int

const (
	// AdmitNew means the session is genuinely new and a slot was acquired.
	AdmitNew TryAdmitResult = 0
	// AdmitDuplicate means this sessionID was already admitted (another node beat us).
	AdmitDuplicate TryAdmitResult = 1
	// AdmitCapFull means the global slot cap is exhausted; session must be rejected.
	AdmitCapFull TryAdmitResult = 2
)

// SharedPSRecord holds the P&S reservation state that must be shared across
// gateway nodes so continuations can be served by any node and any gateway can
// advance recovery checkpoints with the same plan snapshot.
// It is JSON-serialised for Redis storage.
type SharedPSRecord struct {
	SessionID             string           `json:"session_id"`
	TotalCost             int64            `json:"total_cost"`
	LockedPrices          map[string]int64 `json:"locked_prices"`
	Budget                int64            `json:"budget,omitempty"`
	PlanSteps             []HTTPDAGStep    `json:"plan_steps,omitempty"`
	PlanHash              string           `json:"plan_hash,omitempty"`
	PriceHash             string           `json:"price_hash,omitempty"`
	CommitmentTokenIssued bool             `json:"commitment_token_issued,omitempty"`
	CurrentStep           int              `json:"current_step"`
	TotalSteps            int              `json:"total_steps"`  // P&S DAG overall length
	ExpiresUnix           int64            `json:"expires_unix"` // unix-nano
}

func cloneHTTPDAGSteps(steps []HTTPDAGStep) []HTTPDAGStep {
	if len(steps) == 0 {
		return nil
	}
	out := make([]HTTPDAGStep, len(steps))
	for i, step := range steps {
		out[i] = HTTPDAGStep{
			StepID:   step.StepID,
			ToolName: step.ToolName,
		}
		if len(step.DependsOn) > 0 {
			out[i].DependsOn = append([]string(nil), step.DependsOn...)
		}
	}
	return out
}

// SessionStateStore abstracts P&S reservation storage and global session-slot
// management for multi-node PlanGate deployments.
//
// Contract:
//   - When --plangate-state-store=inmemory (default), this field is nil on
//     MCPDPServer; existing HTTPBudgetReservationManager paths are used unchanged.
//   - When --plangate-state-store=redis, this field is a *RedisSessionStateStore
//     and all P&S admission/release flows go through it.
//
// Implementations MUST be safe for concurrent use.
type SessionStateStore interface {
	// TryAdmitSession atomically checks for duplicate admission and the global
	// session-slot cap, then admits the session when safe to do so.
	// maxSlots <= 0 means unlimited (no global cap, only dedup check).
	// ttl is the lifetime for the admission marker key.
	TryAdmitSession(ctx context.Context, sessionID string, maxSlots int, ttl time.Duration) (TryAdmitResult, error)

	// SaveReservation persists the locked-price snapshot so other nodes can
	// serve P&S continuation steps without re-running admission.
	SaveReservation(ctx context.Context, r *SharedPSRecord, ttl time.Duration) error

	// GetReservation looks up a previously saved P&S reservation.
	// Returns (nil, nil) when not found (not an error).
	GetReservation(ctx context.Context, sessionID string) (*SharedPSRecord, error)

	// AdvanceReservationStep atomically increments current_step for a reservation.
	// Returns the new current step, total steps, and a boolean indicating if the
	// session is now complete (current_step >= total_steps).
	AdvanceReservationStep(ctx context.Context, sessionID string) (newStep int, totalSteps int, complete bool, err error)

	// ReleaseSession removes the admission marker and reservation, and
	// decrements the global slot counter. Idempotent.
	ReleaseSession(ctx context.Context, sessionID string) error

	// GlobalActiveCount returns the cluster-wide count of currently admitted
	// sessions. Returns 0 on error so callers degrade gracefully.
	GlobalActiveCount(ctx context.Context) (int, error)
}
