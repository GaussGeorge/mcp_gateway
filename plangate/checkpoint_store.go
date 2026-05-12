package plangate

import (
	"context"
	"errors"
	"sort"
	"sync"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// PlanGate-R: CheckpointStore Interface and In-Memory Implementation (Phase 2)
//
// IMPORTANT: This file has NO runtime integration.
// MCPDPServer is not imported or referenced here.
// All runtime coupling is deferred to Phase 3.
// ─────────────────────────────────────────────────────────────────────────────

// Sentinel errors returned by CheckpointStore operations.
var (
	// ErrCheckpointNotFound is returned by Load and Update when the session ID
	// does not exist in the store.
	ErrCheckpointNotFound = errors.New("plangate-r: checkpoint not found")

	// ErrCheckpointExpired is returned by Load when the checkpoint exists but
	// its ExpiresAt is non-zero and is <= now.
	ErrCheckpointExpired = errors.New("plangate-r: checkpoint expired")

	// ErrInvalidCheckpoint is returned when a nil or structurally invalid
	// checkpoint is passed to Save or returned by an Update function.
	ErrInvalidCheckpoint = errors.New("plangate-r: invalid checkpoint (nil or missing session_id)")
)

// CheckpointStore defines the persistence contract for PlanGate-R session checkpoints.
//
// Implementations MUST:
//   - Be safe for concurrent use by multiple goroutines.
//   - Return deep copies on Load and ListRecoverable so callers cannot mutate
//     the store's internal state through the returned pointer.
//   - Apply Update atomically under a write lock so that concurrent updates to
//     the same session_id do not interleave.
type CheckpointStore interface {
	// Save creates or overwrites the checkpoint for cp.SessionID.
	// Returns ErrInvalidCheckpoint if cp is nil or cp.SessionID is empty.
	// Stores a deep copy; the caller may modify cp after Save returns.
	Save(ctx context.Context, cp *SessionCheckpoint) error

	// Load returns a deep copy of the stored checkpoint for sessionID.
	// Returns ErrCheckpointNotFound if not present.
	// Returns ErrCheckpointExpired if ExpiresAt is non-zero and <= now.
	Load(ctx context.Context, sessionID string) (*SessionCheckpoint, error)

	// Update atomically reads, modifies, and saves the checkpoint for sessionID.
	// fn receives a deep copy of the current checkpoint; it must return either
	// the modified checkpoint to save, or an error to abort (no mutation occurs).
	// Returns ErrCheckpointNotFound if sessionID is not present.
	// Returns ErrInvalidCheckpoint if fn returns a nil checkpoint without an error.
	Update(ctx context.Context, sessionID string, fn func(*SessionCheckpoint) (*SessionCheckpoint, error)) error

	// Delete removes the checkpoint for sessionID. Idempotent: deleting a
	// non-existent session does not return an error.
	Delete(ctx context.Context, sessionID string) error

	// ListRecoverable returns up to limit deep-copied checkpoints whose Status
	// is CHECKPOINTED or RECOVERY_QUEUED AND whose ExpiresAt is either zero or
	// strictly greater than now. Results are ordered by RecoveryAttempts ASC,
	// then CreatedAt ASC. limit <= 0 means return all qualifying records.
	ListRecoverable(ctx context.Context, limit int, now time.Time) ([]*SessionCheckpoint, error)

	// Expire deletes all checkpoints whose ExpiresAt is non-zero and <= now.
	// Returns the number of records deleted.
	Expire(ctx context.Context, now time.Time) (int, error)
}

// ─────────────────────────────────────────────────────────────────────────────
// InMemoryCheckpointStore
// ─────────────────────────────────────────────────────────────────────────────

// InMemoryCheckpointStore is a thread-safe, in-process implementation of
// CheckpointStore backed by a plain Go map.
//
// It is suitable for Phase 2–7 experimentation. Checkpoints are lost on process
// restart. For persistent recovery across restarts, replace with a disk-backed
// implementation in a future phase.
type InMemoryCheckpointStore struct {
	mu    sync.RWMutex
	store map[string]*SessionCheckpoint
}

// NewInMemoryCheckpointStore constructs an empty, ready-to-use store.
func NewInMemoryCheckpointStore() *InMemoryCheckpointStore {
	return &InMemoryCheckpointStore{
		store: make(map[string]*SessionCheckpoint),
	}
}

// Save implements CheckpointStore.
func (s *InMemoryCheckpointStore) Save(ctx context.Context, cp *SessionCheckpoint) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if cp == nil || cp.SessionID == "" {
		return ErrInvalidCheckpoint
	}

	now := time.Now()
	clone := cp.Clone()

	// Auto-set CreatedAt on first save.
	if clone.CreatedAt.IsZero() {
		clone.CreatedAt = now
	}
	clone.UpdatedAt = now
	// Note: ExpiresAt is intentionally NOT defaulted here.
	// The TTL is a runtime concern set by Phase 3+ integration.

	s.mu.Lock()
	s.store[clone.SessionID] = clone
	s.mu.Unlock()
	return nil
}

// Load implements CheckpointStore.
func (s *InMemoryCheckpointStore) Load(ctx context.Context, sessionID string) (*SessionCheckpoint, error) {
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}

	s.mu.RLock()
	cp, ok := s.store[sessionID]
	s.mu.RUnlock()

	if !ok {
		return nil, ErrCheckpointNotFound
	}
	if !cp.ExpiresAt.IsZero() && !time.Now().Before(cp.ExpiresAt) {
		return nil, ErrCheckpointExpired
	}
	return cp.Clone(), nil
}

// Update implements CheckpointStore.
// The entire read-modify-write is performed under a write lock to prevent
// concurrent updates to the same session from interleaving.
func (s *InMemoryCheckpointStore) Update(
	ctx context.Context,
	sessionID string,
	fn func(*SessionCheckpoint) (*SessionCheckpoint, error),
) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	current, ok := s.store[sessionID]
	if !ok {
		return ErrCheckpointNotFound
	}

	// Pass a deep copy to the function so it cannot mutate the stored value
	// directly, even if fn returns an error.
	modified, err := fn(current.Clone())
	if err != nil {
		return err
	}
	if modified == nil {
		return ErrInvalidCheckpoint
	}

	modified.UpdatedAt = time.Now()
	s.store[sessionID] = modified.Clone()
	return nil
}

// Delete implements CheckpointStore.
func (s *InMemoryCheckpointStore) Delete(ctx context.Context, sessionID string) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	s.mu.Lock()
	delete(s.store, sessionID)
	s.mu.Unlock()
	return nil
}

// ListRecoverable implements CheckpointStore.
func (s *InMemoryCheckpointStore) ListRecoverable(ctx context.Context, limit int, now time.Time) ([]*SessionCheckpoint, error) {
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}

	s.mu.RLock()
	candidates := make([]*SessionCheckpoint, 0, len(s.store))
	for _, cp := range s.store {
		if cp.Status != StatusCheckpointed && cp.Status != StatusRecoveryQueued {
			continue
		}
		// ExpiresAt zero → never expires; otherwise must be strictly after now.
		if !cp.ExpiresAt.IsZero() && !now.Before(cp.ExpiresAt) {
			continue
		}
		candidates = append(candidates, cp)
	}
	s.mu.RUnlock()

	// Sort: RecoveryAttempts ASC, then CreatedAt ASC.
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].RecoveryAttempts != candidates[j].RecoveryAttempts {
			return candidates[i].RecoveryAttempts < candidates[j].RecoveryAttempts
		}
		return candidates[i].CreatedAt.Before(candidates[j].CreatedAt)
	})

	if limit > 0 && len(candidates) > limit {
		candidates = candidates[:limit]
	}

	// Return deep copies.
	result := make([]*SessionCheckpoint, len(candidates))
	for i, cp := range candidates {
		result[i] = cp.Clone()
	}
	return result, nil
}

// Expire implements CheckpointStore.
func (s *InMemoryCheckpointStore) Expire(ctx context.Context, now time.Time) (int, error) {
	if ctx.Err() != nil {
		return 0, ctx.Err()
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	deleted := 0
	for id, cp := range s.store {
		if !cp.ExpiresAt.IsZero() && !now.Before(cp.ExpiresAt) {
			delete(s.store, id)
			deleted++
		}
	}
	return deleted, nil
}
