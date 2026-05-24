package plangate

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

func TestCrossGatewayCheckpointProgressSupportsRandomRoutingAmendment(t *testing.T) {
	sharedState := NewInMemorySessionStateStore(0)
	checkpoints := NewInMemoryCheckpointStore()

	gatewayA := newCrossGatewayCheckpointServer(t, "gw-a", sharedState, checkpoints, true)
	gatewayB := newCrossGatewayCheckpointServer(t, "gw-b", sharedState, checkpoints, false)

	plan := &HTTPDAGPlan{
		SessionID: "cross-gateway-checkpoint",
		Budget:    1000,
		Steps: []HTTPDAGStep{
			{StepID: "s1", ToolName: "calculate"},
			{StepID: "s2", ToolName: "web_fetch", DependsOn: []string{"s1"}},
			{StepID: "s3", ToolName: "mock_heavy", DependsOn: []string{"s2"}},
			{StepID: "s4", ToolName: "calculate", DependsOn: []string{"s3"}},
		},
	}

	w0, resp0 := sendCommitmentHTTPRequest(t, gatewayA, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	if resp0.Error != nil {
		t.Fatalf("step0 error: %+v", resp0.Error)
	}
	parentToken := w0.Header().Get(HeaderCommitmentToken)
	if parentToken == "" {
		t.Fatalf("step0 did not issue %s", HeaderCommitmentToken)
	}

	w1, resp1 := sendCommitmentHTTPRequest(t, gatewayB, plan.SessionID, plan.Steps[1].ToolName, nil, 1, parentToken)
	if resp1.Error != nil {
		t.Fatalf("step1 on gateway B error: %+v", resp1.Error)
	}
	if got := w1.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusValidated) {
		t.Fatalf("step1 commitment status=%q, want validated", got)
	}

	w2, resp2 := sendCommitmentHTTPRequest(t, gatewayA, plan.SessionID, plan.Steps[2].ToolName, nil, 2, parentToken)
	if resp2.Error == nil || resp2.Error.Code != mcpgov.CodeInternalError {
		t.Fatalf("expected recoverable step2 failure, got %+v", resp2.Error)
	}
	if got := w2.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusValidated) {
		t.Fatalf("step2 commitment status=%q, want validated", got)
	}

	cp, err := checkpoints.Load(context.Background(), plan.SessionID)
	if err != nil {
		t.Fatalf("load checkpoint after cross-gateway failure: %v", err)
	}
	if cp.Status != StatusCheckpointed {
		t.Fatalf("checkpoint status=%q, want %q", cp.Status, StatusCheckpointed)
	}
	if cp.CurrentStep != 2 {
		t.Fatalf("checkpoint current_step=%d, want 2", cp.CurrentStep)
	}
	if len(cp.CompletedSteps) != 2 {
		t.Fatalf("completed steps=%d, want 2 (%+v)", len(cp.CompletedSteps), cp.CompletedSteps)
	}
	if cp.CompletedSteps[0].StepID != "s1" || cp.CompletedSteps[1].StepID != "s2" {
		t.Fatalf("completed steps=%+v, want [s1 s2]", cp.CompletedSteps)
	}

	var remaining []HTTPDAGStep
	if err := json.Unmarshal(cp.RemainingPlanJSON, &remaining); err != nil {
		t.Fatalf("unmarshal remaining plan: %v", err)
	}
	if len(remaining) != 2 || remaining[0].StepID != "s3" || remaining[1].StepID != "s4" {
		t.Fatalf("remaining plan=%+v, want [s3 s4]", remaining)
	}

	amendment := &HTTPPlanAmendment{
		SessionID:              cp.SessionID,
		AmendmentID:            "cross-gw-amend",
		BaseStep:               cp.CurrentStep,
		BasePlanHash:           cp.CurrentPlanHash,
		ParentCommitmentDigest: commitmentTokenHash(parentToken),
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s3_retry", ToolName: "calculate", DependsOn: []string{"s2"}},
			{StepID: "s4_retry", ToolName: "web_fetch", DependsOn: []string{"s3_retry"}},
		},
	}

	wRecovery, recoveryResp := sendRecoveryAmendmentRequest(t, gatewayB, cp.SessionID, parentToken, amendment)
	if recoveryResp.Error != nil {
		t.Fatalf("recovery amendment failed: %+v", recoveryResp.Error)
	}
	if got := wRecovery.Header().Get(HeaderAmendmentStatus); got != string(AmendmentStatusAccepted) {
		t.Fatalf("amendment status=%q, want accepted", got)
	}
	if got := wRecovery.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusIssued) {
		t.Fatalf("recovery commitment status=%q, want issued", got)
	}
	amendedToken := wRecovery.Header().Get(HeaderCommitmentToken)
	if amendedToken == "" {
		t.Fatalf("missing amended commitment token")
	}

	claims, status, reason := gatewayB.commitmentTokens.parseAndVerify(amendedToken)
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("amended token status=%s reason=%q", status, reason)
	}
	if claims.Version != commitmentTokenVersionV2 || claims.Type != commitmentTokenTypeAmendedPS {
		t.Fatalf("unexpected amended token claims: %+v", claims)
	}
	if claims.BaseStep != 2 {
		t.Fatalf("amended token base_step=%d, want 2", claims.BaseStep)
	}
	if claims.ParentCommitmentHash != commitmentTokenHash(parentToken) {
		t.Fatalf("parent commitment hash=%q, want %q", claims.ParentCommitmentHash, commitmentTokenHash(parentToken))
	}
}

func newCrossGatewayCheckpointServer(
	t *testing.T,
	nodeID string,
	stateStore SessionStateStore,
	checkpointStore CheckpointStore,
	failMockHeavy bool,
) *MCPDPServer {
	t.Helper()
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeStrict, "cross-gateway-secret", stateStore)
	s.SetNodeID(nodeID)
	if err := s.EnableRecoveryForConfig(RecoveryConfig{
		Enabled:     true,
		TTL:         5 * time.Minute,
		MaxAttempts: 3,
		Store:       "inmemory",
	}, checkpointStore); err != nil {
		t.Fatalf("EnableRecoveryForConfig(%s): %v", nodeID, err)
	}
	if err := s.SetAmendmentPolicy(AmendmentPolicy{
		Mode:              AmendmentModeRecoveryOnly,
		MaxCount:          3,
		MaxBudgetDelta:    0,
		RequireCommitment: true,
	}); err != nil {
		t.Fatalf("SetAmendmentPolicy(%s): %v", nodeID, err)
	}
	if failMockHeavy {
		s.RegisterTool(mcpgov.MCPTool{Name: "mock_heavy", Description: "recoverable failure"}, func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
			return nil, errors.New("connection refused: backend unavailable")
		})
	}
	return s
}
