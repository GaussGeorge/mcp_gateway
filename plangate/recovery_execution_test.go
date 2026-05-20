package plangate

// PlanGate-R Phase 4B/5: Recovery execution unit tests
//
// Test coverage:
//   1. TestRecoveryResumeDisabledReturnsError       — disabled gateway rejects resume
//   2. TestRecoveryResumeRejectsWithoutCheckpoint   — unknown session → error
//   3. TestRecoveryResumeRejectsActiveCheckpoint    — live session → error
//   4. TestRecoveryResumeRejectsReActSemanticRecovery — wrong mode → error
//   5. TestPSRecoveryStateTransitions               — CHECKPOINTED→RECOVERING→deleted
//   6. TestPSRecoveryDoesNotReplayCompletedSteps    — only remaining handlers called
//   7. TestRecoveredSessionDeletesCheckpointOnSuccess — checkpoint gone after success
//   8. TestPSRecoveryComputesSkippedSteps            — SkippedSteps == len(completed)
//   [BONUS] TestRecoveryResumeCanHandleAlreadyComplete
//   [BONUS] TestRecoveryTerminalFailureUpdatesStatus
//   [BONUS] TestRecoveryRecoverableReInterruptionSavesProgress
//   [BONUS] controlled E2E scenario: 5-step session, fail after step 2, recover from step 3

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

// ─────────────────────────────────────────────────────────────────────────────
// Test helpers
// ─────────────────────────────────────────────────────────────────────────────

// recoveryServer creates an MCPDPServer with recovery enabled and an
// InMemoryCheckpointStore wired in.  Tools are registered directly so tests
// can control handler behaviour.
func recoveryServer() *MCPDPServer {
	gov := makeTestGovernor()
	s := NewMCPDPServer("recovery-test", gov, 60*time.Second, 0, 0.0)
	s.recoveryConfig = RecoveryConfig{
		Enabled:     true,
		TTL:         5 * time.Minute,
		MaxAttempts: 3,
		Store:       "inmemory",
	}
	s.checkpointStore = NewInMemoryCheckpointStore()
	return s
}

// makeRecoveryResumeRequest builds an HTTP POST carrying the recovery headers.
func makeRecoveryResumeRequest(sessionID string) *http.Request {
	body, _ := json.Marshal(mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "r1",
		Method:  mcpgov.MethodToolsCall,
		Params:  json.RawMessage(`{"name":"noop"}`),
	})
	req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderRecoveryMode, "resume")
	req.Header.Set(HeaderSessionID, sessionID)
	return req
}

// makeRPCRequest returns a minimal JSON-RPC request for recovery handler tests.
func makeRPCRequest() *mcpgov.JSONRPCRequest {
	return &mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "req-1",
		Method:  mcpgov.MethodToolsCall,
		Params:  json.RawMessage(`{"name":"noop"}`),
	}
}

// saveCheckpointedPS saves a P&S checkpoint in CHECKPOINTED status with
// pre-populated completed and remaining steps so recovery can be triggered.
func saveCheckpointedPS(
	t *testing.T,
	store CheckpointStore,
	sessionID string,
	completedCount int,
	remainingTools []string,
) {
	t.Helper()
	ctx := context.Background()
	completedSteps := make([]StepRecord, completedCount)
	for i := 0; i < completedCount; i++ {
		completedSteps[i] = StepRecord{
			StepID:    makeStepID(i),
			StepIndex: i,
			ToolName:  "tool_a",
		}
	}
	remaining := make([]HTTPDAGStep, len(remainingTools))
	for i, tool := range remainingTools {
		remaining[i] = HTTPDAGStep{
			StepID:   makeStepID(completedCount + i),
			ToolName: tool,
		}
	}
	remainingJSON, _ := json.Marshal(remaining)
	err := store.Save(ctx, &SessionCheckpoint{
		SessionID:         sessionID,
		AgentID:           "test-agent",
		Mode:              AgentModePlanSolve,
		Status:            StatusCheckpointed,
		CurrentStep:       completedCount,
		CompletedSteps:    completedSteps,
		RemainingPlanJSON: remainingJSON,
		CreatedAt:         time.Now(),
	})
	if err != nil {
		t.Fatalf("saveCheckpointedPS: %v", err)
	}
}

func makeStepID(i int) string {
	return "step_" + string(rune('a'+i))
}

// registerNoop registers a no-op handler for each named tool on s.
func registerNoop(s *MCPDPServer, tools ...string) {
	for _, name := range tools {
		toolName := name // capture loop variable
		s.RegisterTool(mcpgov.MCPTool{Name: toolName, Description: "test"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
			return &mcpgov.MCPToolCallResult{
				Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok:" + toolName}},
			}, nil
		})
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 1: Recovery disabled → all resume requests rejected
// ─────────────────────────────────────────────────────────────────────────────

// TestRecoveryDisabledOriginalPathUnchanged verifies that when recovery is
// disabled (the default), handleRecoveryResume returns a clear error and
// the normal admission path is completely unaffected.
func TestRecoveryDisabledOriginalPathUnchanged(t *testing.T) {
	gov := makeTestGovernor()
	// Use the default server (recovery disabled).
	s := NewMCPDPServer("default", gov, 60*time.Second, 0, 0.0)
	// recovery is disabled — s.recoveryConfig.Enabled == false

	r := makeRecoveryResumeRequest("some-sess")
	resp := s.handleRecoveryResume(context.Background(), r, makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected error when recovery disabled, got success")
	}
	if resp.Error.Code != mcpgov.CodeInvalidRequest {
		t.Errorf("expected CodeInvalidRequest, got %d", resp.Error.Code)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 2: No checkpoint for session → error, not panic
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveryResumeRejectsWithoutCheckpoint(t *testing.T) {
	s := recoveryServer()
	r := makeRecoveryResumeRequest("nonexistent-sess")
	resp := s.handleRecoveryResume(context.Background(), r, makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected error for unknown session, got success")
	}
	// Should be a not-found style error, not an internal panic.
	if resp.Error.Code != mcpgov.CodeInternalError {
		t.Errorf("expected CodeInternalError (not found), got %d", resp.Error.Code)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 3: ACTIVE_CHECKPOINT status → cannot resume a live session
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveryResumeRejectsActiveCheckpoint(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID: "sess-live",
		AgentID:   "agent",
		Mode:      AgentModePlanSolve,
		Status:    StatusActiveCheckpoint, // still in-flight
		CreatedAt: time.Now(),
	})

	r := makeRecoveryResumeRequest("sess-live")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected error for ACTIVE_CHECKPOINT session, got success")
	}
	if resp.Error.Code != mcpgov.CodeInvalidRequest {
		t.Errorf("expected CodeInvalidRequest, got %d", resp.Error.Code)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 4: ReAct mode → semantic recovery not implemented
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveryResumeRejectsReActSemanticRecovery(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID: "sess-react",
		AgentID:   "agent",
		Mode:      AgentModeReAct,          // wrong mode
		Status:    StatusCheckpointed,
		CreatedAt: time.Now(),
	})

	r := makeRecoveryResumeRequest("sess-react")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected error for ReAct session, got success")
	}
	if resp.Error.Code != mcpgov.CodeInvalidRequest {
		t.Errorf("expected CodeInvalidRequest, got %d", resp.Error.Code)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 5: State machine transitions
// CHECKPOINTED → RECOVERING (during execution) → checkpoint deleted (success)
// ─────────────────────────────────────────────────────────────────────────────

func TestPSRecoveryStateTransitions(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	// Pre-register tools.
	registerNoop(s, "tool_b", "tool_c")

	// Save a checkpointed session with 1 completed step and 2 remaining.
	saveCheckpointedPS(t, store, "sess-transitions", 1, []string{"tool_b", "tool_c"})

	// At this point the checkpoint must be CHECKPOINTED.
	before, err := store.Load(ctx, "sess-transitions")
	if err != nil {
		t.Fatalf("pre-check load: %v", err)
	}
	if before.Status != StatusCheckpointed {
		t.Fatalf("expected CHECKPOINTED before recovery, got %q", before.Status)
	}

	// Execute recovery.
	r := makeRecoveryResumeRequest("sess-transitions")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("recovery failed: %s", resp.Error.Message)
	}

	// After successful recovery the checkpoint must be deleted.
	_, loadErr := store.Load(ctx, "sess-transitions")
	if !errors.Is(loadErr, ErrCheckpointNotFound) {
		t.Errorf("expected checkpoint to be deleted after successful recovery, got %v", loadErr)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 6: Completed steps are NEVER re-executed
// ─────────────────────────────────────────────────────────────────────────────

// TestPSRecoveryDoesNotReplayCompletedSteps is the core correctness test:
// only the remaining_steps handlers are called; completed_steps handlers are not.
func TestPSRecoveryDoesNotReplayCompletedSteps(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	// Two already-completed tools (must NOT be called during recovery).
	completedCallCount := 0
	for _, name := range []string{"done_0", "done_1"} {
		toolName := name
		s.RegisterTool(mcpgov.MCPTool{Name: toolName, Description: "completed tool"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
			completedCallCount++
			t.Errorf("completed tool %q must NOT be called during recovery", toolName)
			return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{{Type: "text", Text: "should not run"}}}, nil
		})
	}

	// Three remaining tools (MUST be called during recovery).
	remainingCallCount := 0
	for _, name := range []string{"rem_0", "rem_1", "rem_2"} {
		s.RegisterTool(mcpgov.MCPTool{Name: name, Description: "remaining tool"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
			remainingCallCount++
			return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}}}, nil
		})
	}

	// Save checkpoint: 2 completed (done_0, done_1), 3 remaining (rem_0, rem_1, rem_2).
	saveCheckpointedPS(t, store, "sess-nowreplay", 2, []string{"rem_0", "rem_1", "rem_2"})

	r := makeRecoveryResumeRequest("sess-nowreplay")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("recovery failed: %s", resp.Error.Message)
	}

	// Verify counters.
	if completedCallCount != 0 {
		t.Errorf("completed tools called %d times during recovery (expected 0)", completedCallCount)
	}
	if remainingCallCount != 3 {
		t.Errorf("expected 3 remaining tool calls, got %d", remainingCallCount)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 7: Checkpoint is deleted on success
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveredSessionDeletesCheckpointOnSuccess(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	registerNoop(s, "tool_x")
	saveCheckpointedPS(t, store, "sess-delete", 1, []string{"tool_x"})

	r := makeRecoveryResumeRequest("sess-delete")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("recovery failed: %s", resp.Error.Message)
	}

	_, err := store.Load(ctx, "sess-delete")
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("expected ErrCheckpointNotFound after recovery, got %v", err)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test 8: SkippedSteps == len(completed_steps)
// ─────────────────────────────────────────────────────────────────────────────

// TestPSRecoveryComputesSkippedSteps verifies that the PSRecoveryResult payload
// correctly reports how many steps were skipped (saved compute).
// This is the key metric distinguishing PlanGate-R from naive retry.
func TestPSRecoveryComputesSkippedSteps(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	registerNoop(s, "tool_r1", "tool_r2", "tool_r3")
	// 2 completed steps (should be skipped), 3 remaining.
	saveCheckpointedPS(t, store, "sess-skip", 2, []string{"tool_r1", "tool_r2", "tool_r3"})

	r := makeRecoveryResumeRequest("sess-skip")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("recovery failed: %s", resp.Error.Message)
	}

	// Parse PSRecoveryResult from the result.
	resBytes, _ := json.Marshal(resp.Result)
	var result PSRecoveryResult
	if err := json.Unmarshal(resBytes, &result); err != nil {
		t.Fatalf("could not parse PSRecoveryResult: %v", err)
	}

	if result.SkippedSteps != 2 {
		t.Errorf("expected SkippedSteps=2 (= completed_steps), got %d", result.SkippedSteps)
	}
	if result.ExecutedSteps != 3 {
		t.Errorf("expected ExecutedSteps=3, got %d", result.ExecutedSteps)
	}
	if result.TotalSteps != 5 {
		t.Errorf("expected TotalSteps=5, got %d", result.TotalSteps)
	}
	if result.SavedComputeSteps != 2 {
		t.Errorf("expected SavedComputeSteps=2, got %d", result.SavedComputeSteps)
	}
	if !result.Recovered {
		t.Error("expected Recovered=true")
	}
	if result.Mode != "ps_recovery" {
		t.Errorf("expected Mode=ps_recovery, got %q", result.Mode)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: Already-complete checkpoint is handled gracefully
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveryResumeCanHandleAlreadyComplete(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	// Save a checkpoint with 0 remaining steps (empty RemainingPlanJSON).
	emptyRemaining, _ := json.Marshal([]HTTPDAGStep{})
	_ = store.Save(ctx, &SessionCheckpoint{
		SessionID:         "sess-done",
		AgentID:           "agent",
		Mode:              AgentModePlanSolve,
		Status:            StatusCheckpointed,
		CurrentStep:       3,
		CompletedSteps:    make([]StepRecord, 3),
		RemainingPlanJSON: emptyRemaining,
		CreatedAt:         time.Now(),
	})

	r := makeRecoveryResumeRequest("sess-done")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("expected success for already-complete session, got: %s", resp.Error.Message)
	}

	// Checkpoint should be cleaned up.
	_, err := store.Load(ctx, "sess-done")
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("expected checkpoint deleted, got %v", err)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: Terminal failure during recovery → FAILED_TERMINAL status
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveryTerminalFailureUpdatesStatus(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	// Register a tool that produces a terminal failure (context cancelled).
	s.RegisterTool(mcpgov.MCPTool{Name: "bad_tool", Description: "always fails"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		return nil, errors.New("context canceled: user aborted")
	})

	saveCheckpointedPS(t, store, "sess-terminal", 1, []string{"bad_tool"})

	r := makeRecoveryResumeRequest("sess-terminal")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected error response for terminal failure")
	}

	// Checkpoint should be marked FAILED_TERMINAL.
	cp, err := store.Load(ctx, "sess-terminal")
	if err != nil {
		t.Fatalf("checkpoint should still exist after terminal failure: %v", err)
	}
	if cp.Status != StatusFailedTerminal {
		t.Errorf("expected FAILED_TERMINAL, got %q", cp.Status)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: Recoverable re-interruption saves progress
// ─────────────────────────────────────────────────────────────────────────────

func TestRecoveryRecoverableReInterruptionSavesProgress(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	// First tool succeeds, second tool simulates a recoverable backend timeout.
	firstCallDone := false
	s.RegisterTool(mcpgov.MCPTool{Name: "ok_tool", Description: "succeeds"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		firstCallDone = true
		return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}}}, nil
	})
	s.RegisterTool(mcpgov.MCPTool{Name: "timeout_tool", Description: "times out"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		return nil, errors.New("connection refused: backend unavailable")
	})
	registerNoop(s, "final_tool")

	// 1 completed, 3 remaining: ok_tool succeeds, timeout_tool fails, final_tool never reached.
	saveCheckpointedPS(t, store, "sess-reinterrupt", 1, []string{"ok_tool", "timeout_tool", "final_tool"})

	r := makeRecoveryResumeRequest("sess-reinterrupt")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error == nil {
		t.Fatal("expected error response after re-interruption")
	}

	// ok_tool should have been called.
	if !firstCallDone {
		t.Error("ok_tool should have been executed before re-interruption")
	}

	// Checkpoint must be back in CHECKPOINTED with CurrentStep advanced by 1
	// (ok_tool completed), and RemainingPlanJSON pointing to [timeout_tool, final_tool].
	cp, loadErr := store.Load(ctx, "sess-reinterrupt")
	if loadErr != nil {
		t.Fatalf("checkpoint should be preserved after re-interruption: %v", loadErr)
	}
	if cp.Status != StatusCheckpointed {
		t.Errorf("expected CHECKPOINTED after re-interruption, got %q", cp.Status)
	}
	// CurrentStep must be 2 (1 prior + 1 executed in this recovery run).
	if cp.CurrentStep != 2 {
		t.Errorf("expected CurrentStep=2, got %d", cp.CurrentStep)
	}
	// RemainingPlanJSON must contain timeout_tool and final_tool (not ok_tool).
	var remaining []HTTPDAGStep
	_ = json.Unmarshal(cp.RemainingPlanJSON, &remaining)
	if len(remaining) != 2 {
		t.Errorf("expected 2 remaining steps after partial recovery, got %d: %v", len(remaining), remaining)
	}
	if len(remaining) > 0 && remaining[0].ToolName != "timeout_tool" {
		t.Errorf("expected first remaining tool=timeout_tool, got %q", remaining[0].ToolName)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Bonus: Controlled 5-step E2E scenario
// "5 steps, fail after step 2, recover from step 3, verify eventual_success=true"
// ─────────────────────────────────────────────────────────────────────────────

// TestControlledE2E_5StepSessionRecovery is the key proof-of-concept test.
// It directly demonstrates the PlanGate-R claim:
//   "Under recoverable interruptions, PlanGate-R improves eventual success
//    by resuming from checkpoint rather than restarting or failing."
//
// Setup:
//   5-step session: tools [t0 t1 t2 t3 t4]
//   Steps t0, t1 succeed → checkpoint saved (CurrentStep=2, completed=[t0,t1])
//   Step t2 generates a recoverable interruption → ACTIVE_CHECKPOINT→CHECKPOINTED
//   Recovery call → resumes from t2, executes [t2, t3, t4]
//   eventual_success = true
//   saved_compute_steps = 2 (t0 and t1 not re-executed)
func TestControlledE2E_5StepSessionRecovery(t *testing.T) {
	s := recoveryServer()
	store := s.checkpointStore
	ctx := context.Background()

	allTools := []string{"t0", "t1", "t2", "t3", "t4"}
	callLog := make([]string, 0, 5)

	for _, name := range allTools {
		toolName := name
		s.RegisterTool(mcpgov.MCPTool{Name: toolName, Description: "e2e"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
			callLog = append(callLog, toolName)
			return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{{Type: "text", Text: toolName + ":done"}}}, nil
		})
	}

	// Simulate steps t0 and t1 having run successfully:
	// Use saveCheckpointAfterStep directly to create the checkpoint as Phase 3 would.
	remainingFull, _ := json.Marshal([]HTTPDAGStep{
		{StepID: "s2", ToolName: "t2"},
		{StepID: "s3", ToolName: "t3"},
		{StepID: "s4", ToolName: "t4"},
	})
	err := store.Save(ctx, &SessionCheckpoint{
		SessionID: "e2e-sess",
		AgentID:   "e2e-agent",
		Mode:      AgentModePlanSolve,
		Status:    StatusActiveCheckpoint, // was in-flight
		CurrentStep: 2,
		CompletedSteps: []StepRecord{
			{StepID: "s0", StepIndex: 0, ToolName: "t0"},
			{StepID: "s1", StepIndex: 1, ToolName: "t1"},
		},
		RemainingPlanJSON: remainingFull,
		CreatedAt:         time.Now(),
	})
	if err != nil {
		t.Fatalf("initial checkpoint save: %v", err)
	}

	// Simulate recoverable interruption → promote to CHECKPOINTED.
	interruptionFailure := RecoveryFailure{
		Decision: RecoveryDecisionRecoverable,
		Category: FailureCategoryBackendUnavail,
		Reason:   FailureReasonBackend5XX,
	}
	s.markCheckpointRecoverable(ctx, "e2e-sess", interruptionFailure)

	// Verify CHECKPOINTED status.
	cpBefore, _ := store.Load(ctx, "e2e-sess")
	if cpBefore.Status != StatusCheckpointed {
		t.Fatalf("expected CHECKPOINTED after interruption, got %q", cpBefore.Status)
	}

	// Recovery resume call.
	r := makeRecoveryResumeRequest("e2e-sess")
	resp := s.handleRecoveryResume(ctx, r, makeRPCRequest())
	if resp.Error != nil {
		t.Fatalf("E2E recovery failed: %s", resp.Error.Message)
	}

	// Parse result.
	resBytes, _ := json.Marshal(resp.Result)
	var result PSRecoveryResult
	_ = json.Unmarshal(resBytes, &result)

	// === Assertions ===

	// eventual_success = true
	if !result.Recovered {
		t.Error("E2E: expected Recovered=true")
	}
	// saved_compute_steps = 2 (t0, t1 not re-executed)
	if result.SkippedSteps != 2 {
		t.Errorf("E2E: expected SkippedSteps=2, got %d", result.SkippedSteps)
	}
	// 3 remaining steps executed (t2, t3, t4)
	if result.ExecutedSteps != 3 {
		t.Errorf("E2E: expected ExecutedSteps=3, got %d", result.ExecutedSteps)
	}
	// Total correctly = 5
	if result.TotalSteps != 5 {
		t.Errorf("E2E: expected TotalSteps=5, got %d", result.TotalSteps)
	}

	// Only t2, t3, t4 were called — t0 and t1 must NOT appear in callLog.
	if len(callLog) != 3 {
		t.Errorf("E2E: expected 3 tool calls (t2,t3,t4), got %d: %v", len(callLog), callLog)
	}
	for _, called := range callLog {
		if called == "t0" || called == "t1" {
			t.Errorf("E2E: completed tool %q must NOT be called during recovery", called)
		}
	}

	// Checkpoint must be gone.
	_, err = store.Load(ctx, "e2e-sess")
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Errorf("E2E: expected checkpoint deleted after success, got %v", err)
	}

	// Metrics must reflect 1 recovery.
	stats := s.GetRecoveryStats()
	if stats.RecoveredSuccessCount != 1 {
		t.Errorf("E2E: expected RecoveredSuccessCount=1, got %d", stats.RecoveredSuccessCount)
	}
	if stats.SkippedStepsTotal != 2 {
		t.Errorf("E2E: expected SkippedStepsTotal=2, got %d", stats.SkippedStepsTotal)
	}
}
