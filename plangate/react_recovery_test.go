package plangate

import (
	"context"
	"encoding/json"
	"sync/atomic"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

func TestReActRecoveryResumeRestoresStep(t *testing.T) {
	s := recoveryServer()
	ctx := context.Background()
	store := s.checkpointStore
	sessionID := "react-resume-step"

	if err := store.Save(ctx, &SessionCheckpoint{
		SessionID:   sessionID,
		AgentID:     "agent",
		Mode:        AgentModeReAct,
		Status:      StatusCheckpointed,
		CurrentStep: 3,
		CompletedSteps: []StepRecord{
			{StepIndex: 0, ToolName: "calculate"},
			{StepIndex: 1, ToolName: "calculate"},
			{StepIndex: 2, ToolName: "calculate"},
		},
		ObservationHistory: []string{"sha256:abc"},
		CreatedAt:          time.Now(),
	}); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}

	resp := s.handleRecoveryResume(ctx, makeRecoveryResumeRequest(sessionID), makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("expected success, got %v", resp.Error)
	}

	rState, ok := s.reactSessions.Get(sessionID)
	if !ok {
		t.Fatal("expected restored react session")
	}
	if rState.CurrentStep != 3 {
		t.Fatalf("expected current step 3, got %d", rState.CurrentStep)
	}
}

func TestReActRecoveryResumeDoesNotLeakSessionCapForExistingSession(t *testing.T) {
	s := recoveryServer()
	ctx := context.Background()
	store := s.checkpointStore
	sessionID := "react-existing-no-cap-leak"
	toolCalls := int64(0)

	s.sessionCap = make(chan struct{}, 2)
	s.disableCapacityStep0 = false
	s.RegisterTool(mcpgov.MCPTool{Name: "noop", Description: "noop"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		atomic.AddInt64(&toolCalls, 1)
		return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}}}, nil
	})

	// Existing live ReAct session already owns one capacity slot.
	s.sessionCap <- struct{}{}
	s.reactSessions.Create(sessionID, func() { <-s.sessionCap })
	s.reactSessions.Advance(sessionID)
	initialCapLen := len(s.sessionCap)
	initialActive := s.reactSessions.ActiveCount()

	if err := store.Save(ctx, &SessionCheckpoint{
		SessionID:   sessionID,
		AgentID:     "agent",
		Mode:        AgentModeReAct,
		Status:      StatusCheckpointed,
		CurrentStep: 3,
		CompletedSteps: []StepRecord{
			{StepIndex: 0, ToolName: "noop"},
			{StepIndex: 1, ToolName: "noop"},
			{StepIndex: 2, ToolName: "noop"},
		},
		CreatedAt: time.Now(),
	}); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}

	resp := s.handleRecoveryResume(ctx, makeRecoveryResumeRequest(sessionID), makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("expected success, got %v", resp.Error)
	}

	if got := s.reactSessions.ActiveCount(); got != initialActive {
		t.Fatalf("expected active session count unchanged (%d), got %d", initialActive, got)
	}
	if got := len(s.sessionCap); got != initialCapLen {
		t.Fatalf("expected sessionCap len unchanged (%d), got %d", initialCapLen, got)
	}
	if got := atomic.LoadInt64(&toolCalls); got != 0 {
		t.Fatalf("expected no tool execution during recovery resume, got %d", got)
	}

	rState, ok := s.reactSessions.Get(sessionID)
	if !ok {
		t.Fatal("expected restored react session to remain tracked")
	}
	if rState.CurrentStep != 3 {
		t.Fatalf("expected CurrentStep=3 after restore, got %d", rState.CurrentStep)
	}
}

func TestReActRecoveryDoesNotAutoExecuteTools(t *testing.T) {
	s := recoveryServer()
	ctx := context.Background()
	store := s.checkpointStore
	sessionID := "react-no-auto-tools"
	calls := int64(0)

	s.RegisterTool(mcpgov.MCPTool{Name: "noop", Description: "noop"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		atomic.AddInt64(&calls, 1)
		return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}}}, nil
	})

	if err := store.Save(ctx, &SessionCheckpoint{
		SessionID:   sessionID,
		AgentID:     "agent",
		Mode:        AgentModeReAct,
		Status:      StatusCheckpointed,
		CurrentStep: 2,
		CreatedAt:   time.Now(),
	}); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}

	resp := s.handleRecoveryResume(ctx, makeRecoveryResumeRequest(sessionID), makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("expected success, got %v", resp.Error)
	}
	if got := atomic.LoadInt64(&calls); got != 0 {
		t.Fatalf("expected no tool execution, got %d", got)
	}
}

func TestReActRecoveryRejectsNonRecoverable(t *testing.T) {
	s := recoveryServer()
	ctx := context.Background()
	store := s.checkpointStore
	sessionID := "react-nonrecoverable"

	if err := store.Save(ctx, &SessionCheckpoint{
		SessionID:      sessionID,
		AgentID:        "agent",
		Mode:           AgentModeReAct,
		Status:         StatusCheckpointed,
		NonRecoverable: true,
		CreatedAt:      time.Now(),
	}); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}

	resp := s.handleRecoveryResume(ctx, makeRecoveryResumeRequest(sessionID), makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected reject for non-recoverable react checkpoint")
	}
	if resp.Error.Code != mcpgov.CodeInvalidRequest {
		t.Fatalf("expected invalid request, got %d", resp.Error.Code)
	}
}

func TestReActRecoveryRejectsLiveCheckpoint(t *testing.T) {
	s := recoveryServer()
	ctx := context.Background()
	store := s.checkpointStore
	sessionID := "react-live"

	if err := store.Save(ctx, &SessionCheckpoint{
		SessionID: sessionID,
		AgentID:   "agent",
		Mode:      AgentModeReAct,
		Status:    StatusActiveCheckpoint,
		CreatedAt: time.Now(),
	}); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}

	resp := s.handleRecoveryResume(ctx, makeRecoveryResumeRequest(sessionID), makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected reject for live checkpoint")
	}
	if resp.Error.Code != mcpgov.CodeInvalidRequest {
		t.Fatalf("expected invalid request, got %d", resp.Error.Code)
	}
}

func TestReActCheckpointMarksSideEffectWithoutIdempotencyNonRecoverable(t *testing.T) {
	s := makeTestServer(4)

	cp := s.buildReActCheckpoint(
		"react-sideeffect-no-idem",
		0,
		"calculate",
		0.2,
		100,
		"tool:calculate->ok",
		"sha256:obs",
		"",
		true,
	)

	if !cp.NonRecoverable {
		t.Fatal("expected non-recoverable=true for side-effecting step without idempotency key")
	}
	if cp.IdempotencyKeys != nil {
		t.Fatalf("expected idempotency keys nil, got %+v", cp.IdempotencyKeys)
	}
	if got := atomic.LoadInt64(&s.duplicateSideEffectCount); got != 1 {
		t.Fatalf("expected duplicate side-effect counter=1, got %d", got)
	}
}

func TestReActCheckpointAllowsSideEffectWithIdempotency(t *testing.T) {
	s := makeTestServer(4)

	cp := s.buildReActCheckpoint(
		"react-sideeffect-idem",
		1,
		"calculate",
		0.2,
		100,
		"tool:calculate->ok",
		"sha256:obs",
		"idem-k",
		true,
	)

	if cp.NonRecoverable {
		t.Fatal("expected non-recoverable=false when idempotency key is present")
	}
	if cp.IdempotencyKeys == nil {
		t.Fatal("expected idempotency key map")
	}
	if got := cp.IdempotencyKeys["1"]; got != "idem-k" {
		t.Fatalf("expected idempotency key recorded, got %q", got)
	}
	if got := atomic.LoadInt64(&s.duplicateSideEffectCount); got != 0 {
		t.Fatalf("expected duplicate side-effect counter=0, got %d", got)
	}
}

func TestReActRecoveryResultPayloadMode(t *testing.T) {
	s := recoveryServer()
	ctx := context.Background()
	store := s.checkpointStore
	sessionID := "react-result-mode"

	if err := store.Save(ctx, &SessionCheckpoint{
		SessionID:   sessionID,
		AgentID:     "agent",
		Mode:        AgentModeReAct,
		Status:      StatusCheckpointed,
		CurrentStep: 1,
		CompletedSteps: []StepRecord{
			{StepIndex: 0, ToolName: "calculate"},
		},
		ConversationTrace: []string{"tool:calculate -> ok"},
		CreatedAt:         time.Now(),
	}); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}

	resp := s.handleRecoveryResume(ctx, makeRecoveryResumeRequest(sessionID), makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("expected success, got %v", resp.Error)
	}

	b, err := json.Marshal(resp.Result)
	if err != nil {
		t.Fatalf("marshal result: %v", err)
	}
	var got ReActRecoveryResult
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal result: %v", err)
	}
	if got.Mode != "react_client_cooperative" {
		t.Fatalf("expected mode react_client_cooperative, got %q", got.Mode)
	}
	if !got.RequiresClientContinuation {
		t.Fatal("expected requires_client_continuation=true")
	}
}
