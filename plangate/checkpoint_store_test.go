package plangate

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

// newTestCheckpoint returns a minimal valid checkpoint for the given sessionID.
func newTestCheckpoint(sessionID string) *SessionCheckpoint {
	return &SessionCheckpoint{
		SessionID:        sessionID,
		Mode:             AgentModePlanSolve,
		Status:           StatusCheckpointed,
		CompletedSteps:   []StepRecord{},
		RecoveryAttempts: 0,
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 1: Basic round-trip
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointSaveLoadRoundTrip(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	cp := newTestCheckpoint("sess-001")
	cp.CurrentStep = 2
	cp.RecoveryAttempts = 1
	cp.LastFailureReason = FailureReasonWorkerTimeout

	if err := store.Save(ctx, cp); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := store.Load(ctx, "sess-001")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.SessionID != cp.SessionID {
		t.Errorf("SessionID mismatch: got %q, want %q", loaded.SessionID, cp.SessionID)
	}
	if loaded.CurrentStep != cp.CurrentStep {
		t.Errorf("CurrentStep mismatch: got %d, want %d", loaded.CurrentStep, cp.CurrentStep)
	}
	if loaded.RecoveryAttempts != cp.RecoveryAttempts {
		t.Errorf("RecoveryAttempts mismatch: got %d, want %d", loaded.RecoveryAttempts, cp.RecoveryAttempts)
	}
	if loaded.LastFailureReason != cp.LastFailureReason {
		t.Errorf("LastFailureReason mismatch: got %q, want %q", loaded.LastFailureReason, cp.LastFailureReason)
	}
	if loaded.CurrentStep != cp.CurrentStep {
		t.Errorf("CurrentStep mismatch (re-check): got %d, want %d", loaded.CurrentStep, cp.CurrentStep)
	}
	if loaded.CreatedAt.IsZero() {
		t.Error("CreatedAt should be auto-populated by Save")
	}
	if loaded.UpdatedAt.IsZero() {
		t.Error("UpdatedAt should be auto-populated by Save")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 2: Mutating the value returned by Load must not affect the store
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointLoadDeepCopyIsolation(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	cp := newTestCheckpoint("sess-002")
	cp.CompletedSteps = []StepRecord{{StepID: "s1", ToolName: "tool_a"}}
	_ = store.Save(ctx, cp)

	loaded, _ := store.Load(ctx, "sess-002")
	// Mutate the returned copy.
	loaded.CompletedSteps[0].ToolName = "MUTATED"
	loaded.RecoveryAttempts = 99

	// Reload from the store: values must be unchanged.
	reloaded, err := store.Load(ctx, "sess-002")
	if err != nil {
		t.Fatalf("second Load: %v", err)
	}
	if reloaded.CompletedSteps[0].ToolName == "MUTATED" {
		t.Error("mutation of loaded copy leaked into store (ToolName)")
	}
	if reloaded.RecoveryAttempts == 99 {
		t.Error("mutation of loaded copy leaked into store (RecoveryAttempts)")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 3: Mutating the original after Save must not affect the store
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointSaveDeepCopyIsolation(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	cp := newTestCheckpoint("sess-003")
	cp.CompletedSteps = []StepRecord{{StepID: "s1", ToolName: "original"}}
	_ = store.Save(ctx, cp)

	// Mutate the original after saving.
	cp.CompletedSteps[0].ToolName = "MUTATED_AFTER_SAVE"
	cp.RecoveryAttempts = 77

	loaded, err := store.Load(ctx, "sess-003")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.CompletedSteps[0].ToolName != "original" {
		t.Errorf("post-Save mutation leaked into store: got %q, want %q",
			loaded.CompletedSteps[0].ToolName, "original")
	}
	if loaded.RecoveryAttempts != 0 {
		t.Errorf("post-Save mutation leaked into store: RecoveryAttempts = %d, want 0",
			loaded.RecoveryAttempts)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 4: Update is atomic and reflects the mutation
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointUpdateAtomic(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	cp := newTestCheckpoint("sess-004")
	cp.RecoveryAttempts = 0
	_ = store.Save(ctx, cp)

	err := store.Update(ctx, "sess-004", func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
		c.RecoveryAttempts++
		c.Status = StatusRecoveryQueued
		return c, nil
	})
	if err != nil {
		t.Fatalf("Update: %v", err)
	}

	loaded, _ := store.Load(ctx, "sess-004")
	if loaded.RecoveryAttempts != 1 {
		t.Errorf("RecoveryAttempts: got %d, want 1", loaded.RecoveryAttempts)
	}
	if loaded.Status != StatusRecoveryQueued {
		t.Errorf("Status: got %q, want %q", loaded.Status, StatusRecoveryQueued)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 5: Update does not mutate the store when fn returns an error
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointUpdateErrorDoesNotMutate(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	cp := newTestCheckpoint("sess-005")
	cp.RecoveryAttempts = 3
	cp.Status = StatusCheckpointed
	_ = store.Save(ctx, cp)

	fnErr := errors.New("deliberate fn error")
	err := store.Update(ctx, "sess-005", func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
		c.RecoveryAttempts = 999 // this mutation must be rolled back
		return nil, fnErr
	})
	if err == nil {
		t.Fatal("Update should have returned fn's error")
	}
	if !errors.Is(err, fnErr) {
		t.Errorf("unexpected error: %v", err)
	}

	loaded, _ := store.Load(ctx, "sess-005")
	if loaded.RecoveryAttempts != 3 {
		t.Errorf("Update mutated store despite fn error: RecoveryAttempts = %d", loaded.RecoveryAttempts)
	}
	if loaded.Status != StatusCheckpointed {
		t.Errorf("Update mutated store despite fn error: Status = %q", loaded.Status)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 6: Expire deletes only expired records; zero TTL records are kept
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointExpire(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()
	now := time.Now()

	// neverExpires: ExpiresAt is zero → must never be deleted by Expire.
	neverExpires := newTestCheckpoint("never")
	_ = store.Save(ctx, neverExpires)

	// alreadyExpired: ExpiresAt is in the past.
	expired := newTestCheckpoint("expired")
	expired.ExpiresAt = now.Add(-1 * time.Hour)
	_ = store.Save(ctx, expired)

	// futureExpiry: ExpiresAt is in the future → must NOT be deleted.
	future := newTestCheckpoint("future")
	future.ExpiresAt = now.Add(1 * time.Hour)
	_ = store.Save(ctx, future)

	count, err := store.Expire(ctx, now)
	if err != nil {
		t.Fatalf("Expire: %v", err)
	}
	if count != 1 {
		t.Errorf("Expire count: got %d, want 1", count)
	}

	// "never" and "future" must still be loadable.
	if _, err := store.Load(ctx, "never"); err != nil {
		t.Errorf("Load 'never' after Expire: %v", err)
	}
	if _, err := store.Load(ctx, "future"); err != nil {
		t.Errorf("Load 'future' after Expire: %v", err)
	}

	// "expired" must be gone.
	_, loadErr := store.Load(ctx, "expired")
	if !errors.Is(loadErr, ErrCheckpointNotFound) {
		t.Errorf("expected ErrCheckpointNotFound for expired session, got: %v", loadErr)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 7: ListRecoverable ordering and limit
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointListRecoverableOrderingAndLimit(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()
	now := time.Now()
	base := now.Add(-10 * time.Minute)

	// Four checkpoints eligible for recovery, ordered by RecoveryAttempts then CreatedAt.
	records := []*SessionCheckpoint{
		{SessionID: "ccc", Mode: AgentModeReAct, Status: StatusCheckpointed, RecoveryAttempts: 2, CreatedAt: base.Add(1 * time.Minute)},
		{SessionID: "aaa", Mode: AgentModePlanSolve, Status: StatusCheckpointed, RecoveryAttempts: 0, CreatedAt: base.Add(3 * time.Minute)},
		{SessionID: "bbb", Mode: AgentModePlanSolve, Status: StatusRecoveryQueued, RecoveryAttempts: 0, CreatedAt: base.Add(1 * time.Minute)},
		{SessionID: "ddd", Mode: AgentModeReAct, Status: StatusRecoveryQueued, RecoveryAttempts: 1, CreatedAt: base.Add(2 * time.Minute)},
	}
	// Ineligible: wrong status.
	ineligible1 := &SessionCheckpoint{SessionID: "zzz1", Mode: AgentModePlanSolve, Status: StatusSucceeded, RecoveryAttempts: 0, CreatedAt: base}
	// Ineligible: expired.
	ineligible2 := &SessionCheckpoint{SessionID: "zzz2", Mode: AgentModePlanSolve, Status: StatusCheckpointed, RecoveryAttempts: 0, CreatedAt: base, ExpiresAt: now.Add(-1 * time.Minute)}

	for _, r := range records {
		if err := store.Save(ctx, r); err != nil {
			t.Fatalf("Save %s: %v", r.SessionID, err)
		}
	}
	_ = store.Save(ctx, ineligible1)
	_ = store.Save(ctx, ineligible2)

	// Expected order: bbb (0, T+1m), aaa (0, T+3m), ddd (1, T+2m), ccc (2, T+1m)
	all, err := store.ListRecoverable(ctx, 0, now)
	if err != nil {
		t.Fatalf("ListRecoverable: %v", err)
	}
	if len(all) != 4 {
		t.Fatalf("expected 4 results, got %d", len(all))
	}
	wantOrder := []string{"bbb", "aaa", "ddd", "ccc"}
	for i, cp := range all {
		if cp.SessionID != wantOrder[i] {
			t.Errorf("position %d: got %q, want %q", i, cp.SessionID, wantOrder[i])
		}
	}

	// With limit=2 we should get only the first two.
	limited, err := store.ListRecoverable(ctx, 2, now)
	if err != nil {
		t.Fatalf("ListRecoverable limit=2: %v", err)
	}
	if len(limited) != 2 {
		t.Fatalf("expected 2 results with limit=2, got %d", len(limited))
	}
	for i, cp := range limited {
		if cp.SessionID != wantOrder[i] {
			t.Errorf("limited position %d: got %q, want %q", i, cp.SessionID, wantOrder[i])
		}
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 8: Context cancellation propagates through all six methods
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointContextCancellation(t *testing.T) {
	store := NewInMemoryCheckpointStore()

	// Seed one record so Load/Update/Delete don't hit not-found first.
	bgCtx := context.Background()
	seed := newTestCheckpoint("ctx-sess")
	_ = store.Save(bgCtx, seed)

	cancelledCtx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	t.Run("Save", func(t *testing.T) {
		cp := newTestCheckpoint("ctx-new")
		if err := store.Save(cancelledCtx, cp); !errors.Is(err, context.Canceled) {
			t.Errorf("Save: expected context.Canceled, got %v", err)
		}
	})
	t.Run("Load", func(t *testing.T) {
		if _, err := store.Load(cancelledCtx, "ctx-sess"); !errors.Is(err, context.Canceled) {
			t.Errorf("Load: expected context.Canceled, got %v", err)
		}
	})
	t.Run("Update", func(t *testing.T) {
		err := store.Update(cancelledCtx, "ctx-sess", func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
			return c, nil
		})
		if !errors.Is(err, context.Canceled) {
			t.Errorf("Update: expected context.Canceled, got %v", err)
		}
	})
	t.Run("Delete", func(t *testing.T) {
		if err := store.Delete(cancelledCtx, "ctx-sess"); !errors.Is(err, context.Canceled) {
			t.Errorf("Delete: expected context.Canceled, got %v", err)
		}
	})
	t.Run("ListRecoverable", func(t *testing.T) {
		if _, err := store.ListRecoverable(cancelledCtx, 10, time.Now()); !errors.Is(err, context.Canceled) {
			t.Errorf("ListRecoverable: expected context.Canceled, got %v", err)
		}
	})
	t.Run("Expire", func(t *testing.T) {
		if _, err := store.Expire(cancelledCtx, time.Now()); !errors.Is(err, context.Canceled) {
			t.Errorf("Expire: expected context.Canceled, got %v", err)
		}
	})
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 9: Concurrent access — run with -race to detect data races
// ─────────────────────────────────────────────────────────────────────────────

func TestCheckpointConcurrentAccess(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	const goroutines = 20
	const sessionID = "concurrent-sess"

	seed := newTestCheckpoint(sessionID)
	_ = store.Save(ctx, seed)

	var wg sync.WaitGroup
	wg.Add(goroutines * 4)

	// Concurrent Saves.
	for i := 0; i < goroutines; i++ {
		go func(i int) {
			defer wg.Done()
			cp := newTestCheckpoint(sessionID)
			cp.RecoveryAttempts = i
			_ = store.Save(ctx, cp)
		}(i)
	}

	// Concurrent Loads.
	for i := 0; i < goroutines; i++ {
		go func() {
			defer wg.Done()
			_, _ = store.Load(ctx, sessionID)
		}()
	}

	// Concurrent Updates.
	for i := 0; i < goroutines; i++ {
		go func() {
			defer wg.Done()
			_ = store.Update(ctx, sessionID, func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
				c.RecoveryAttempts++
				return c, nil
			})
		}()
	}

	// Concurrent ListRecoverable.
	for i := 0; i < goroutines; i++ {
		go func() {
			defer wg.Done()
			_, _ = store.ListRecoverable(ctx, 10, time.Now())
		}()
	}

	wg.Wait()
}
