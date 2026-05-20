package plangate

import (
	"context"
	"errors"
	"testing"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Mock store used by failure-injection tests
// ─────────────────────────────────────────────────────────────────────────────

type errorCheckpointStore struct{ saveErr error }

func (m *errorCheckpointStore) Save(_ context.Context, _ *SessionCheckpoint) error {
	return m.saveErr
}
func (m *errorCheckpointStore) Load(_ context.Context, _ string) (*SessionCheckpoint, error) {
	return nil, ErrCheckpointNotFound
}
func (m *errorCheckpointStore) Update(_ context.Context, _ string, _ func(*SessionCheckpoint) (*SessionCheckpoint, error)) error {
	return nil
}
func (m *errorCheckpointStore) Delete(_ context.Context, _ string) error { return nil }
func (m *errorCheckpointStore) ListRecoverable(_ context.Context, _ int, _ time.Time) ([]*SessionCheckpoint, error) {
	return nil, nil
}
func (m *errorCheckpointStore) Expire(_ context.Context, _ time.Time) (int, error) {
	return 0, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: saveCheckpointAfterStep helpers
// ─────────────────────────────────────────────────────────────────────────────

// TestSaveCheckpointDisabledNoop verifies that saveCheckpointAfterStep is a no-op
// when recovery is disabled, even when a real store is attached.
func TestSaveCheckpointDisabledNoop(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := &MCPDPServer{
		recoveryConfig:  DefaultRecoveryConfig(), // Enabled = false
		checkpointStore: store,
	}

	cp := &SessionCheckpoint{
		SessionID: "agent1-sess1",
		Mode:      AgentModePlanSolve,
		Status:    StatusCheckpointed,
	}
	s.saveCheckpointAfterStep(context.Background(), cp)

	_, err := store.Load(context.Background(), "agent1-sess1")
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("expected ErrCheckpointNotFound when disabled, got %v", err)
	}
}

// TestSaveCheckpointEnabled verifies that a checkpoint is persisted when recovery
// is enabled. The status must default to StatusActiveCheckpoint (not
// StatusCheckpointed) so that ListRecoverable ignores it.
func TestSaveCheckpointEnabled(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := &MCPDPServer{
		recoveryConfig: RecoveryConfig{
			Enabled:     true,
			TTL:         5 * time.Minute,
			MaxAttempts: 3,
			Store:       "inmemory",
		},
		checkpointStore: store,
	}

	// Status intentionally left empty → helper must default to StatusActiveCheckpoint.
	cp := &SessionCheckpoint{
		SessionID:   "agent2-sess2",
		Mode:        AgentModePlanSolve,
		CurrentStep: 1,
		CompletedSteps: []StepRecord{
			{StepIndex: 0, ToolName: "mock_tool", CompletedAt: time.Now()},
		},
	}
	s.saveCheckpointAfterStep(context.Background(), cp)

	loaded, err := store.Load(context.Background(), "agent2-sess2")
	if err != nil {
		t.Fatalf("expected checkpoint to be found, got error: %v", err)
	}
	if loaded.SessionID != "agent2-sess2" {
		t.Errorf("unexpected SessionID: %s", loaded.SessionID)
	}
	if loaded.CurrentStep != 1 {
		t.Errorf("expected CurrentStep=1, got %d", loaded.CurrentStep)
	}
	if loaded.AgentID != "agent2" {
		t.Errorf("expected deriveAgentID to produce 'agent2', got %q", loaded.AgentID)
	}
	if loaded.ExpiresAt.IsZero() {
		t.Error("expected ExpiresAt to be set by TTL")
	}
	// The default status must be ACTIVE_CHECKPOINT, not CHECKPOINTED.
	// This ensures ListRecoverable does not treat normal progress snapshots
	// as recovery candidates.
	if loaded.Status != StatusActiveCheckpoint {
		t.Errorf("expected Status=%q, got %q", StatusActiveCheckpoint, loaded.Status)
	}
}

// TestSaveCheckpointFailureDoesNotPanic verifies that a save error is logged and
// silently swallowed — it must never panic or propagate.
func TestSaveCheckpointFailureDoesNotPanic(t *testing.T) {
	s := &MCPDPServer{
		recoveryConfig: RecoveryConfig{
			Enabled: true,
			TTL:     5 * time.Minute,
			Store:   "inmemory",
		},
		checkpointStore: &errorCheckpointStore{saveErr: errors.New("storage boom")},
	}

	// Must not panic.
	s.saveCheckpointAfterStep(context.Background(), &SessionCheckpoint{
		SessionID: "agent3-sess3",
		Mode:      AgentModeReAct,
	})
}

// TestDeleteCheckpointOnSuccess verifies that deleting after a successful session
// makes the checkpoint unreachable via Load.
func TestDeleteCheckpointOnSuccess(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := &MCPDPServer{
		recoveryConfig: RecoveryConfig{
			Enabled: true,
			TTL:     5 * time.Minute,
			Store:   "inmemory",
		},
		checkpointStore: store,
	}

	// Save first.
	s.saveCheckpointAfterStep(context.Background(), &SessionCheckpoint{
		SessionID:   "agent4-sess4",
		Mode:        AgentModePlanSolve,
		CurrentStep: 1,
	})

	if _, err := store.Load(context.Background(), "agent4-sess4"); err != nil {
		t.Fatalf("expected checkpoint after save, got: %v", err)
	}

	// Delete on success.
	s.deleteCheckpointOnSuccess(context.Background(), "agent4-sess4")

	_, err := store.Load(context.Background(), "agent4-sess4")
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("expected ErrCheckpointNotFound after delete, got %v", err)
	}
}

// TestDeriveAgentID validates the agentID extraction rules.
func TestDeriveAgentID(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{
		{"", "unknown"},
		{"agent123-session456", "agent123"},
		{"session456", "session456"},
		{"a-b-c", "a"},
		{"-session", ""},
	}
	for _, tc := range cases {
		got := deriveAgentID(tc.input)
		if got != tc.want {
			t.Errorf("deriveAgentID(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: nextPSStepState pure helper (P&S off-by-one guard)
// ─────────────────────────────────────────────────────────────────────────────

// TestPSStepState verifies the P&S off-by-one semantics for a 3-step DAG.
// This test documents the intended behaviour and catches regressions.
//
// Invariant: checkpoint.CurrentStep must always equal "next step to execute"
// (i.e. nextStep), never the index of the step that just ran.
func TestPSStepState(t *testing.T) {
	const totalSteps = 3

	tests := []struct {
		currentStep    int  // HTTPSessionReservation.CurrentStep BEFORE Advance
		wantNextStep   int  // checkpoint.CurrentStep that should be stored
		wantComplete   bool // whether session is finished
		desc           string
	}{
		{0, 1, false, "step[0] just executed; step[1] is next; not complete"},
		{1, 2, false, "step[1] just executed; step[2] is next; not complete"},
		{2, 3, true, "step[2] just executed; no more steps; complete"},
	}

	for _, tc := range tests {
		nextStep, complete := nextPSStepState(tc.currentStep, totalSteps)
		if nextStep != tc.wantNextStep {
			t.Errorf("[%s] nextStep: got %d, want %d", tc.desc, nextStep, tc.wantNextStep)
		}
		if complete != tc.wantComplete {
			t.Errorf("[%s] complete: got %v, want %v", tc.desc, complete, tc.wantComplete)
		}
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: ListRecoverable must not return ACTIVE_CHECKPOINT entries
// ─────────────────────────────────────────────────────────────────────────────

// TestListRecoverableIgnoresActiveCheckpoint ensures that checkpoints saved as
// StatusActiveCheckpoint (normal in-flight progress) are invisible to the
// recovery queue even after Phase 4 adds ListRecoverable scanning.
// Only StatusCheckpointed and StatusRecoveryQueued are eligible for recovery.
func TestListRecoverableIgnoresActiveCheckpoint(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()
	now := time.Now()

	// Save three entries with different statuses.
	entries := []struct {
		id     string
		status SessionStatus
	}{
		{"sess-active", StatusActiveCheckpoint}, // must be INVISIBLE
		{"sess-checkpointed", StatusCheckpointed}, // must be VISIBLE
		{"sess-queued", StatusRecoveryQueued}, // must be VISIBLE
	}
	for _, e := range entries {
		_ = store.Save(ctx, &SessionCheckpoint{
			SessionID: e.id,
			AgentID:   "agent",
			Mode:      AgentModePlanSolve,
			Status:    e.status,
			CreatedAt: now,
			// ExpiresAt zero → never expires
		})
	}

	results, err := store.ListRecoverable(ctx, 100, now)
	if err != nil {
		t.Fatalf("ListRecoverable error: %v", err)
	}

	ids := make(map[string]bool, len(results))
	for _, cp := range results {
		ids[cp.SessionID] = true
	}

	if ids["sess-active"] {
		t.Error("ACTIVE_CHECKPOINT must NOT appear in ListRecoverable")
	}
	if !ids["sess-checkpointed"] {
		t.Error("CHECKPOINTED must appear in ListRecoverable")
	}
	if !ids["sess-queued"] {
		t.Error("RECOVERY_QUEUED must appear in ListRecoverable")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: markCheckpointRecoverable (Phase 4A)
// ─────────────────────────────────────────────────────────────────────────────

// recoverableFailure is a convenience helper that builds a Recoverable failure.
func recoverableFailure() RecoveryFailure {
	return RecoveryFailure{
		Decision: RecoveryDecisionRecoverable,
		Category: FailureCategoryBackendTimeout,
		Reason:   FailureReasonWorkerTimeout,
		Message:  "test timeout",
	}
}

// enabledServer returns an MCPDPServer with recovery enabled and the provided
// store wired in.
func enabledServer(store CheckpointStore) *MCPDPServer {
	return &MCPDPServer{
		recoveryConfig: RecoveryConfig{
			Enabled:     true,
			TTL:         5 * time.Minute,
			MaxAttempts: 3,
			Store:       "inmemory",
		},
		checkpointStore: store,
	}
}

// TestMarkCheckpointRecoverableDisabledNoop verifies that markCheckpointRecoverable
// is a complete no-op when recovery is disabled (Enabled=false).
func TestMarkCheckpointRecoverableDisabledNoop(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	ctx := context.Background()

	// Pre-save an ACTIVE_CHECKPOINT entry.
	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID: "sess-disabled",
		AgentID:   "agent",
		Mode:      AgentModePlanSolve,
		Status:    StatusActiveCheckpoint,
		CreatedAt: time.Now(),
	})

	s := &MCPDPServer{
		recoveryConfig:  DefaultRecoveryConfig(), // Enabled = false
		checkpointStore: store,
	}

	s.markCheckpointRecoverable(ctx, "sess-disabled", recoverableFailure())

	loaded, err := store.Load(ctx, "sess-disabled")
	if err != nil {
		t.Fatalf("expected checkpoint to still exist, got: %v", err)
	}
	if loaded.Status != StatusActiveCheckpoint {
		t.Errorf("disabled: expected status unchanged (%q), got %q",
			StatusActiveCheckpoint, loaded.Status)
	}
}

// TestMarkCheckpointRecoverableNoCheckpointNoop verifies no panic/error when
// there is no prior checkpoint for the session.
func TestMarkCheckpointRecoverableNoCheckpointNoop(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := enabledServer(store)
	ctx := context.Background()

	// Must not panic and must not create a new checkpoint.
	s.markCheckpointRecoverable(ctx, "nonexistent-sess", recoverableFailure())

	_, err := store.Load(ctx, "nonexistent-sess")
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("expected ErrCheckpointNotFound for unknown session, got %v", err)
	}
}

// TestMarkCheckpointRecoverableActiveToCheckpointed verifies that an
// ACTIVE_CHECKPOINT is promoted to CHECKPOINTED when the failure is recoverable,
// and that the LastFailureCategory / LastFailureReason fields are set.
func TestMarkCheckpointRecoverableActiveToCheckpointed(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := enabledServer(store)
	ctx := context.Background()

	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID: "sess-promote",
		AgentID:   "agent",
		Mode:      AgentModePlanSolve,
		Status:    StatusActiveCheckpoint,
		CreatedAt: time.Now(),
	})

	before := time.Now()
	failure := RecoveryFailure{
		Decision: RecoveryDecisionRecoverable,
		Category: FailureCategoryExternalRateLimit,
		Reason:   FailureReasonUpstream429,
	}
	s.markCheckpointRecoverable(ctx, "sess-promote", failure)

	loaded, err := store.Load(ctx, "sess-promote")
	if err != nil {
		t.Fatalf("unexpected error loading after promotion: %v", err)
	}
	if loaded.Status != StatusCheckpointed {
		t.Errorf("expected CHECKPOINTED, got %q", loaded.Status)
	}
	if loaded.LastFailureCategory != FailureCategoryExternalRateLimit {
		t.Errorf("expected LastFailureCategory=%q, got %q",
			FailureCategoryExternalRateLimit, loaded.LastFailureCategory)
	}
	if loaded.LastFailureReason != FailureReasonUpstream429 {
		t.Errorf("expected LastFailureReason=%q, got %q",
			FailureReasonUpstream429, loaded.LastFailureReason)
	}
	// InMemoryCheckpointStore.Update always stamps UpdatedAt = time.Now() on
	// every write; verify it is set to a recent real time rather than the zero value.
	if loaded.UpdatedAt.Before(before) || loaded.UpdatedAt.IsZero() {
		t.Errorf("expected UpdatedAt to be set to a recent time, got %v", loaded.UpdatedAt)
	}
}

// TestMarkCheckpointRecoverableDoesNotModifySucceeded verifies terminal-state
// checkpoints are never modified by markCheckpointRecoverable.
func TestMarkCheckpointRecoverableDoesNotModifySucceeded(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := enabledServer(store)
	ctx := context.Background()

	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID: "sess-succeeded",
		AgentID:   "agent",
		Mode:      AgentModePlanSolve,
		Status:    StatusSucceeded,
		CreatedAt: time.Now(),
	})

	s.markCheckpointRecoverable(ctx, "sess-succeeded", recoverableFailure())

	loaded, _ := store.Load(ctx, "sess-succeeded")
	if loaded.Status != StatusSucceeded {
		t.Errorf("SUCCEEDED status must not be modified, got %q", loaded.Status)
	}
}

// TestMarkCheckpointRecoverableDoesNotIncrementAttempts verifies that
// RecoveryAttempts is NOT touched by Phase 4A (that is Phase 4B's job).
func TestMarkCheckpointRecoverableDoesNotIncrementAttempts(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := enabledServer(store)
	ctx := context.Background()

	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID:        "sess-attempts",
		AgentID:          "agent",
		Mode:             AgentModePlanSolve,
		Status:           StatusActiveCheckpoint,
		RecoveryAttempts: 0,
		CreatedAt:        time.Now(),
	})

	s.markCheckpointRecoverable(ctx, "sess-attempts", recoverableFailure())

	loaded, _ := store.Load(ctx, "sess-attempts")
	if loaded.RecoveryAttempts != 0 {
		t.Errorf("Phase 4A must not touch RecoveryAttempts; got %d", loaded.RecoveryAttempts)
	}
}

// TestListRecoverableAfterMark verifies the full round-trip: after
// markCheckpointRecoverable promotes an ACTIVE_CHECKPOINT, that session
// becomes visible via ListRecoverable (CHECKPOINTED is eligible).
func TestListRecoverableAfterMark(t *testing.T) {
	store := NewInMemoryCheckpointStore()
	s := enabledServer(store)
	ctx := context.Background()
	now := time.Now()

	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID: "sess-roundtrip",
		AgentID:   "agent",
		Mode:      AgentModeReAct,
		Status:    StatusActiveCheckpoint,
		CreatedAt: now,
		// ExpiresAt zero → never expires in ListRecoverable
	})

	// Before promotion: must NOT appear in ListRecoverable.
	before, _ := store.ListRecoverable(ctx, 100, now)
	for _, cp := range before {
		if cp.SessionID == "sess-roundtrip" {
			t.Error("ACTIVE_CHECKPOINT must not appear in ListRecoverable before promotion")
		}
	}

	// Promote.
	s.markCheckpointRecoverable(ctx, "sess-roundtrip", recoverableFailure())

	// After promotion: must appear.
	after, err := store.ListRecoverable(ctx, 100, now)
	if err != nil {
		t.Fatalf("ListRecoverable error: %v", err)
	}
	found := false
	for _, cp := range after {
		if cp.SessionID == "sess-roundtrip" {
			found = true
		}
	}
	if !found {
		t.Error("CHECKPOINTED session must appear in ListRecoverable after promotion")
	}
}

