package plangate

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

func TestRecoveryAmendmentAcceptedExecutesAmendedSuffix(t *testing.T) {
	s := newRecoveryAmendmentServer(t)
	cp, remainingSteps := recoveryAmendmentCheckpoint(t, "recover-amend-ok")
	callLog := make([]string, 0, 2)
	s.RegisterTool(mcpgov.MCPTool{Name: "orig_b"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		t.Fatalf("original suffix tool orig_b should not execute after amendment")
		return nil, nil
	})
	s.RegisterTool(mcpgov.MCPTool{Name: "orig_c"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		t.Fatalf("original suffix tool orig_c should not execute after amendment")
		return nil, nil
	})
	registerRecoveryAmendmentHandler(s, "retry_b", &callLog, nil)
	registerRecoveryAmendmentHandler(s, "finish_c", &callLog, nil)

	saveRecoveryCheckpoint(t, s.checkpointStore, cp)
	parentToken := issueRecoveryCheckpointCommitment(t, s, cp, remainingSteps)
	parentClaims, status, reason := s.commitmentTokens.parseAndVerify(parentToken)
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("parent token status=%s reason=%q", status, reason)
	}
	if parentClaims.Version != commitmentTokenVersionV1 || parentClaims.Type != commitmentTokenTypePS {
		t.Fatalf("unexpected parent token claims: %+v", parentClaims)
	}
	amendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-ok",
		BaseStep:               cp.CurrentStep,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(parentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_retry", ToolName: "retry_b", DependsOn: []string{"s0"}},
			{StepID: "s2_new", ToolName: "finish_c", DependsOn: []string{"s1_retry"}},
		},
	}

	w, resp := sendRecoveryAmendmentRequest(t, s, cp.SessionID, parentToken, amendment)
	if resp.Error != nil {
		t.Fatalf("recovery amendment failed: %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderAmendmentStatus); got != string(AmendmentStatusAccepted) {
		t.Fatalf("amendment status=%q, want accepted", got)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusIssued) {
		t.Fatalf("commitment status=%q, want issued", got)
	}
	newToken := w.Header().Get(HeaderCommitmentToken)
	if newToken == "" {
		t.Fatalf("missing amended commitment token")
	}
	if !reflect.DeepEqual(callLog, []string{"retry_b", "finish_c"}) {
		t.Fatalf("call log=%v, want amended suffix only", callLog)
	}
	if _, err := s.checkpointStore.Load(context.Background(), cp.SessionID); !errors.Is(err, ErrCheckpointNotFound) {
		t.Fatalf("expected checkpoint deleted after successful recovery, got %v", err)
	}

	claims, status, reason := s.commitmentTokens.parseAndVerify(newToken)
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("amended token status=%s reason=%q", status, reason)
	}
	if claims.AmendmentID != "amend-ok" || claims.BaseStep != 1 {
		t.Fatalf("unexpected amended claims: %+v", claims)
	}
	if claims.Version != commitmentTokenVersionV2 || claims.Type != commitmentTokenTypeAmendedPS {
		t.Fatalf("expected v2 amended commitment, got %+v", claims)
	}
	if claims.ParentCommitmentHash != commitmentTokenHash(parentToken) {
		t.Fatalf("unexpected parent commitment hash=%q", claims.ParentCommitmentHash)
	}
	if claims.CheckpointHash == "" || claims.AmendmentChainHash == "" || claims.DeltaHash == "" {
		t.Fatalf("expected v2 chain fields, got %+v", claims)
	}
}

func TestRecoveryAmendmentSecondUsesV2ParentAndIncrementsVersion(t *testing.T) {
	s := newRecoveryAmendmentServer(t)
	cp, remainingSteps := recoveryAmendmentCheckpoint(t, "recover-amend-second")
	baseParentToken := issueRecoveryCheckpointCommitment(t, s, cp, remainingSteps)
	registerRecoveryAmendmentHandler(s, "retry_b", nil, nil)
	registerRecoveryAmendmentHandler(s, "finish_c", nil, nil)
	firstAmendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-1",
		BaseStep:               cp.CurrentStep,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(baseParentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_retry", ToolName: "retry_b", DependsOn: []string{"s0"}},
			{StepID: "s2_new", ToolName: "finish_c", DependsOn: []string{"s1_retry"}},
		},
	}
	applied, err := applyAmendmentToCheckpoint(
		cp,
		firstAmendment,
		s.amendmentPolicy(),
		&CommitmentTokenClaims{PlanHash: cp.CurrentPlanHash},
		commitmentTokenHash(baseParentToken),
		priceForRecoveryTestTool(cp.LockedPriceSnapshot),
		s.handlers,
	)
	if err != nil {
		t.Fatalf("apply first amendment: %v", err)
	}
	applied.Checkpoint.Status = StatusCheckpointed
	saveRecoveryCheckpoint(t, s.checkpointStore, applied.Checkpoint)
	parentTokenV2 := issueRecoveryAmendedCheckpointCommitment(t, s, applied)

	callLog := make([]string, 0, 2)
	registerRecoveryAmendmentHandler(s, "second_retry", &callLog, nil)
	registerRecoveryAmendmentHandler(s, "second_finish", &callLog, nil)
	secondAmendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-2",
		BaseStep:               applied.Checkpoint.CurrentStep,
		BasePlanHash:           applied.Checkpoint.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(parentTokenV2),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_second", ToolName: "second_retry", DependsOn: []string{"s0"}},
			{StepID: "s2_second", ToolName: "second_finish", DependsOn: []string{"s1_second"}},
		},
	}

	w, resp := sendRecoveryAmendmentRequest(t, s, cp.SessionID, parentTokenV2, secondAmendment)
	if resp.Error != nil {
		t.Fatalf("second amendment recovery failed: %+v", resp.Error)
	}
	if !reflect.DeepEqual(callLog, []string{"second_retry", "second_finish"}) {
		t.Fatalf("call log=%v, want second amended suffix only", callLog)
	}
	newToken := w.Header().Get(HeaderCommitmentToken)
	claims, status, reason := s.commitmentTokens.parseAndVerify(newToken)
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("new amended token status=%s reason=%q", status, reason)
	}
	if claims.AmendmentVersion != 2 {
		t.Fatalf("amendment version=%d, want 2", claims.AmendmentVersion)
	}
	if claims.ParentCommitmentHash != commitmentTokenHash(parentTokenV2) {
		t.Fatalf("parent commitment hash=%q, want latest v2 parent", claims.ParentCommitmentHash)
	}
}

func TestRecoveryAmendmentInvalidLeavesCheckpointUnchanged(t *testing.T) {
	s := newRecoveryAmendmentServer(t)
	cp, remainingSteps := recoveryAmendmentCheckpoint(t, "recover-amend-invalid")
	callLog := make([]string, 0, 1)
	registerRecoveryAmendmentHandler(s, "retry_b", &callLog, nil)
	saveRecoveryCheckpoint(t, s.checkpointStore, cp)
	before, err := s.checkpointStore.Load(context.Background(), cp.SessionID)
	if err != nil {
		t.Fatalf("load before: %v", err)
	}
	parentToken := issueRecoveryCheckpointCommitment(t, s, cp, remainingSteps)
	amendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-bad",
		BaseStep:               cp.CurrentStep + 1,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(parentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_retry", ToolName: "retry_b", DependsOn: []string{"s0"}},
		},
	}

	w, resp := sendRecoveryAmendmentRequest(t, s, cp.SessionID, parentToken, amendment)
	if resp.Error == nil || resp.Error.Code != mcpgov.CodeInvalidParams {
		t.Fatalf("expected invalid params, got %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderAmendmentStatus); got != string(AmendmentStatusRejected) {
		t.Fatalf("amendment status=%q, want rejected", got)
	}
	if len(callLog) != 0 {
		t.Fatalf("no recovery step should execute on invalid amendment, got %v", callLog)
	}
	after, err := s.checkpointStore.Load(context.Background(), cp.SessionID)
	if err != nil {
		t.Fatalf("load after: %v", err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("checkpoint changed after invalid amendment\nbefore=%+v\nafter=%+v", before, after)
	}
}

func TestRecoveryAmendmentStaleV2ParentRejected(t *testing.T) {
	s := newRecoveryAmendmentServer(t)
	cp, remainingSteps := recoveryAmendmentCheckpoint(t, "recover-amend-stale")
	baseParentToken := issueRecoveryCheckpointCommitment(t, s, cp, remainingSteps)
	registerRecoveryAmendmentHandler(s, "retry_b", nil, nil)
	registerRecoveryAmendmentHandler(s, "finish_c", nil, nil)
	firstAmendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-stale-parent",
		BaseStep:               cp.CurrentStep,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(baseParentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_retry", ToolName: "retry_b", DependsOn: []string{"s0"}},
			{StepID: "s2_new", ToolName: "finish_c", DependsOn: []string{"s1_retry"}},
		},
	}
	applied, err := applyAmendmentToCheckpoint(
		cp,
		firstAmendment,
		s.amendmentPolicy(),
		&CommitmentTokenClaims{PlanHash: cp.CurrentPlanHash},
		commitmentTokenHash(baseParentToken),
		priceForRecoveryTestTool(cp.LockedPriceSnapshot),
		s.handlers,
	)
	if err != nil {
		t.Fatalf("apply first amendment: %v", err)
	}
	staleParentToken := issueRecoveryAmendedCheckpointCommitment(t, s, applied)
	mutated := applied.Checkpoint.Clone()
	mutated.Status = StatusCheckpointed
	mutated.CurrentStep = 2
	mutated.CompletedSteps = append(mutated.CompletedSteps, StepRecord{StepID: "s1_retry", StepIndex: 1, ToolName: "retry_b"})
	remainingJSON, err := json.Marshal([]HTTPDAGStep{{StepID: "s2_new", ToolName: "finish_c", DependsOn: []string{"s1_retry"}}})
	if err != nil {
		t.Fatalf("marshal mutated remaining: %v", err)
	}
	mutated.RemainingPlanJSON = remainingJSON
	saveRecoveryCheckpoint(t, s.checkpointStore, mutated)

	callLog := make([]string, 0, 1)
	registerRecoveryAmendmentHandler(s, "late_fix", &callLog, nil)
	amendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-stale-rejected",
		BaseStep:               mutated.CurrentStep,
		BasePlanHash:           mutated.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(staleParentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s2_latest", ToolName: "late_fix", DependsOn: []string{"s1_retry"}},
		},
	}

	w, resp := sendRecoveryAmendmentRequest(t, s, cp.SessionID, staleParentToken, amendment)
	if resp.Error == nil || resp.Error.Code != mcpgov.CodeInvalidParams {
		t.Fatalf("expected invalid params for stale v2 parent, got %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentError); got != "checkpoint hash mismatch" {
		t.Fatalf("commitment error=%q, want checkpoint hash mismatch", got)
	}
	if len(callLog) != 0 {
		t.Fatalf("stale v2 parent should not execute any recovery step, got %v", callLog)
	}
}

func TestRecoveryAmendmentCheckpointHashMismatchRejected(t *testing.T) {
	s := newRecoveryAmendmentServer(t)
	cp, remainingSteps := recoveryAmendmentCheckpoint(t, "recover-amend-checkpoint-mismatch")
	baseParentToken := issueRecoveryCheckpointCommitment(t, s, cp, remainingSteps)
	registerRecoveryAmendmentHandler(s, "retry_b", nil, nil)
	registerRecoveryAmendmentHandler(s, "finish_c", nil, nil)
	firstAmendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-checkpoint-parent",
		BaseStep:               cp.CurrentStep,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(baseParentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_retry", ToolName: "retry_b", DependsOn: []string{"s0"}},
			{StepID: "s2_new", ToolName: "finish_c", DependsOn: []string{"s1_retry"}},
		},
	}
	applied, err := applyAmendmentToCheckpoint(
		cp,
		firstAmendment,
		s.amendmentPolicy(),
		&CommitmentTokenClaims{PlanHash: cp.CurrentPlanHash},
		commitmentTokenHash(baseParentToken),
		priceForRecoveryTestTool(cp.LockedPriceSnapshot),
		s.handlers,
	)
	if err != nil {
		t.Fatalf("apply first amendment: %v", err)
	}
	applied.Checkpoint.Status = StatusCheckpointed
	saveRecoveryCheckpoint(t, s.checkpointStore, applied.Checkpoint)
	validClaims := issueRecoveryAmendedCheckpointClaims(t, applied)
	validClaims.CheckpointHash = "wrong-checkpoint-hash"
	parentTokenV2 := signCommitmentClaimsForTest(t, s.commitmentTokens, validClaims)

	callLog := make([]string, 0, 1)
	registerRecoveryAmendmentHandler(s, "late_fix", &callLog, nil)
	amendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-checkpoint-rejected",
		BaseStep:               applied.Checkpoint.CurrentStep,
		BasePlanHash:           applied.Checkpoint.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(parentTokenV2),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s2_latest", ToolName: "late_fix", DependsOn: []string{"s0"}},
		},
	}

	w, resp := sendRecoveryAmendmentRequest(t, s, cp.SessionID, parentTokenV2, amendment)
	if resp.Error == nil || resp.Error.Code != mcpgov.CodeInvalidParams {
		t.Fatalf("expected invalid params for checkpoint hash mismatch, got %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentError); got != "checkpoint hash mismatch" {
		t.Fatalf("commitment error=%q, want checkpoint hash mismatch", got)
	}
	if len(callLog) != 0 {
		t.Fatalf("checkpoint hash mismatch should not execute any recovery step, got %v", callLog)
	}
}

func TestRecoveryAmendmentReinterruptPreservesAmendedRemainingSuffix(t *testing.T) {
	s := newRecoveryAmendmentServer(t)
	cp, remainingSteps := recoveryAmendmentCheckpoint(t, "recover-amend-retry")
	callLog := make([]string, 0, 2)
	registerRecoveryAmendmentHandler(s, "retry_b", &callLog, nil)
	registerRecoveryAmendmentHandler(s, "finish_c", &callLog, errors.New("connection refused: backend unavailable"))
	saveRecoveryCheckpoint(t, s.checkpointStore, cp)
	parentToken := issueRecoveryCheckpointCommitment(t, s, cp, remainingSteps)
	amendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "amend-retry",
		BaseStep:               cp.CurrentStep,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(parentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s1_retry", ToolName: "retry_b", DependsOn: []string{"s0"}},
			{StepID: "s2_new", ToolName: "finish_c", DependsOn: []string{"s1_retry"}},
		},
	}

	w, resp := sendRecoveryAmendmentRequest(t, s, cp.SessionID, parentToken, amendment)
	if resp.Error == nil {
		t.Fatalf("expected recoverable re-interruption")
	}
	if got := w.Header().Get(HeaderAmendmentStatus); got != string(AmendmentStatusAccepted) {
		t.Fatalf("amendment status=%q, want accepted", got)
	}
	if !reflect.DeepEqual(callLog, []string{"retry_b", "finish_c"}) {
		t.Fatalf("call log=%v, want amended suffix execution before failure", callLog)
	}

	after, err := s.checkpointStore.Load(context.Background(), cp.SessionID)
	if err != nil {
		t.Fatalf("load checkpoint after re-interruption: %v", err)
	}
	if after.Status != StatusCheckpointed {
		t.Fatalf("status=%s, want CHECKPOINTED", after.Status)
	}
	if after.CurrentStep != 2 {
		t.Fatalf("current step=%d, want 2", after.CurrentStep)
	}
	if after.LastAmendmentID != "amend-retry" {
		t.Fatalf("last amendment id=%q", after.LastAmendmentID)
	}
	if len(after.CompletedSteps) != 2 || after.CompletedSteps[1].StepID != "s1_retry" {
		t.Fatalf("completed steps=%+v, want completed amended prefix", after.CompletedSteps)
	}
	var remaining []HTTPDAGStep
	if err := json.Unmarshal(after.RemainingPlanJSON, &remaining); err != nil {
		t.Fatalf("unmarshal amended remaining suffix: %v", err)
	}
	if len(remaining) != 1 || remaining[0].StepID != "s2_new" {
		t.Fatalf("remaining suffix=%+v, want only failed amended step", remaining)
	}
}

func newRecoveryAmendmentServer(t *testing.T) *MCPDPServer {
	t.Helper()
	s := recoveryServer()
	if err := s.SetCommitmentTokenConfig(CommitmentTokenConfig{
		Mode:   CommitmentTokenModeStrict,
		Secret: "recovery-amendment-secret",
		TTL:    time.Minute,
	}); err != nil {
		t.Fatalf("SetCommitmentTokenConfig: %v", err)
	}
	if err := s.SetAmendmentPolicy(AmendmentPolicy{
		Mode:              AmendmentModeRecoveryOnly,
		MaxCount:          3,
		MaxBudgetDelta:    0,
		RequireCommitment: true,
	}); err != nil {
		t.Fatalf("SetAmendmentPolicy: %v", err)
	}
	return s
}

func recoveryAmendmentCheckpoint(t *testing.T, sessionID string) (*SessionCheckpoint, []HTTPDAGStep) {
	t.Helper()
	remaining := []HTTPDAGStep{
		{StepID: "s1", ToolName: "orig_b", DependsOn: []string{"s0"}},
		{StepID: "s2", ToolName: "orig_c", DependsOn: []string{"s1"}},
	}
	remainingJSON, err := json.Marshal(remaining)
	if err != nil {
		t.Fatalf("marshal remaining: %v", err)
	}
	return &SessionCheckpoint{
		SessionID:   sessionID,
		AgentID:     "agent",
		Mode:        AgentModePlanSolve,
		Status:      StatusCheckpointed,
		CurrentStep: 1,
		CompletedSteps: []StepRecord{
			{StepID: "s0", StepIndex: 0, ToolName: "done_a"},
		},
		RemainingPlanJSON: remainingJSON,
		LockedPriceSnapshot: map[string]int64{
			"done_a": 0,
			"orig_b": 0,
			"orig_c": 0,
		},
		BudgetSnapshot:   100,
		OriginalPlanHash: "recover-base-plan-hash",
		CurrentPlanHash:  "recover-base-plan-hash",
		CreatedAt:        time.Now(),
	}, remaining
}

func saveRecoveryCheckpoint(t *testing.T, store CheckpointStore, cp *SessionCheckpoint) {
	t.Helper()
	if err := store.Save(context.Background(), cp); err != nil {
		t.Fatalf("save checkpoint: %v", err)
	}
}

func issueRecoveryCheckpointCommitment(
	t *testing.T, s *MCPDPServer, cp *SessionCheckpoint, remainingSteps []HTTPDAGStep,
) string {
	t.Helper()
	priceHash, err := checkpointPriceHash(cp)
	if err != nil {
		t.Fatalf("checkpointPriceHash: %v", err)
	}
	totalCost, err := checkpointTotalCost(cp, remainingSteps)
	if err != nil {
		t.Fatalf("checkpointTotalCost: %v", err)
	}
	token, err := s.commitmentTokens.IssueInitialCommitment(CommitmentTokenClaims{
		SessionID:       cp.SessionID,
		PlanHash:        cp.CurrentPlanHash,
		PriceHash:       priceHash,
		Budget:          cp.BudgetSnapshot,
		TotalCost:       totalCost,
		TotalSteps:      checkpointTotalSteps(cp, remainingSteps),
		RecoveryEnabled: true,
	})
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	return token
}

func issueRecoveryAmendedCheckpointClaims(t *testing.T, applied *AppliedPlanAmendment) CommitmentTokenClaims {
	t.Helper()
	return CommitmentTokenClaims{
		Version:              commitmentTokenVersionV2,
		Type:                 commitmentTokenTypeAmendedPS,
		SessionID:            applied.Checkpoint.SessionID,
		PlanHash:             applied.Checkpoint.CurrentPlanHash,
		PriceHash:            applied.PriceHash,
		Budget:               applied.Checkpoint.BudgetSnapshot,
		TotalCost:            applied.TotalCost,
		TotalSteps:           applied.TotalSteps,
		RecoveryEnabled:      true,
		AmendmentVersion:     applied.AmendmentVersion,
		AmendmentID:          applied.Checkpoint.LastAmendmentID,
		ParentCommitmentHash: applied.ParentCommitmentHash,
		DeltaHash:            applied.DeltaHash,
		AmendmentChainHash:   applied.AmendmentChainHash,
		CheckpointHash:       applied.CheckpointHash,
		BaseStep:             applied.Checkpoint.CurrentStep,
	}
}

func issueRecoveryAmendedCheckpointCommitment(
	t *testing.T, s *MCPDPServer, applied *AppliedPlanAmendment,
) string {
	t.Helper()
	token, err := s.commitmentTokens.IssueAmendedCommitment(issueRecoveryAmendedCheckpointClaims(t, applied))
	if err != nil {
		t.Fatalf("IssueAmendedCommitment: %v", err)
	}
	return token
}

func sendRecoveryAmendmentRequest(
	t *testing.T, s *MCPDPServer, sessionID, token string, amendment *HTTPPlanAmendment,
) (*httptest.ResponseRecorder, *mcpgov.JSONRPCResponse) {
	t.Helper()
	req := makeRecoveryResumeRequest(sessionID)
	req.Header.Set(HeaderCommitmentToken, token)
	amendmentJSON, err := json.Marshal(amendment)
	if err != nil {
		t.Fatalf("marshal amendment: %v", err)
	}
	req.Header.Set(HeaderPlanAmendment, string(amendmentJSON))
	w := httptest.NewRecorder()
	resp := s.handleRecoveryResumeWithWriter(context.Background(), w, req, makeRPCRequest())
	return w, resp
}

func registerRecoveryAmendmentHandler(s *MCPDPServer, name string, callLog *[]string, err error) {
	s.RegisterTool(mcpgov.MCPTool{Name: name}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		if callLog != nil {
			*callLog = append(*callLog, name)
		}
		if err != nil {
			return nil, err
		}
		return &mcpgov.MCPToolCallResult{
			Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok:" + name}},
		}, nil
	})
}

func priceForRecoveryTestTool(prices map[string]int64) func(string) int64 {
	return func(toolName string) int64 {
		if prices == nil {
			return 0
		}
		return prices[toolName]
	}
}
