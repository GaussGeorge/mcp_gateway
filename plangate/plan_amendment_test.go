package plangate

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	mcpgov "mcp-governance"
)

func TestPlanAmendmentValidSuffixAccepted(t *testing.T) {
	cp := testPlanAmendmentCheckpoint(t)
	applied, err := applyAmendmentToCheckpoint(
		cp,
		testValidPlanAmendment(),
		testAmendmentPolicy(),
		&CommitmentTokenClaims{PlanHash: cp.CurrentPlanHash},
		"parent-digest",
		nil,
		testAmendmentHandlers(),
	)
	if err != nil {
		t.Fatalf("applyAmendmentToCheckpoint: %v", err)
	}
	if applied == nil || applied.Checkpoint == nil {
		t.Fatalf("expected applied checkpoint")
	}
	if applied.Checkpoint.CurrentPlanHash == cp.CurrentPlanHash {
		t.Fatalf("expected amended plan hash to change")
	}
	if applied.Checkpoint.AmendmentVersion != 1 {
		t.Fatalf("amendment version=%d, want 1", applied.Checkpoint.AmendmentVersion)
	}
	if applied.Checkpoint.LastAmendmentID != "amend-1" {
		t.Fatalf("last amendment id=%q", applied.Checkpoint.LastAmendmentID)
	}
	if applied.Checkpoint.ParentCommitmentHash != "parent-digest" {
		t.Fatalf("parent commitment hash=%q", applied.Checkpoint.ParentCommitmentHash)
	}
	if applied.Checkpoint.DeltaHash == "" || applied.Checkpoint.AmendmentChainHash == "" {
		t.Fatalf("expected amendment hashes to be populated")
	}
	if applied.CheckpointHash == "" || applied.ParentCommitmentHash == "" || applied.DeltaHash == "" || applied.AmendmentChainHash == "" {
		t.Fatalf("expected applied amendment metadata, got %+v", applied)
	}
	if applied.AmendmentVersion != 1 {
		t.Fatalf("applied amendment version=%d, want 1", applied.AmendmentVersion)
	}
	if applied.TotalSteps != 4 {
		t.Fatalf("total steps=%d, want 4", applied.TotalSteps)
	}
	if applied.TotalCost != 60 {
		t.Fatalf("total cost=%d, want 60", applied.TotalCost)
	}
	if applied.PriceHash == "" {
		t.Fatalf("expected price hash")
	}
	var remaining []HTTPDAGStep
	if err := json.Unmarshal(applied.Checkpoint.RemainingPlanJSON, &remaining); err != nil {
		t.Fatalf("unmarshal amended suffix: %v", err)
	}
	if len(remaining) != 2 || remaining[0].StepID != "s2_retry" || remaining[1].StepID != "s3_new" {
		t.Fatalf("unexpected amended suffix: %+v", remaining)
	}
}

func TestPlanAmendmentModifyingCompletedPrefixRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.ReplacementSuffix[0].StepID = "s1"
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "modifies completed step")
}

func TestPlanAmendmentBaseStepMismatchRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.BaseStep = 1
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "base_step mismatch")
}

func TestPlanAmendmentParentPlanHashMismatchRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.BasePlanHash = "other-plan-hash"
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "base_plan_hash mismatch")
}

func TestPlanAmendmentParentCommitmentDigestMismatchRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.ParentCommitmentDigest = "other-digest"
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "parent commitment digest mismatch")
}

func TestPlanAmendmentDAGCycleRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.ReplacementSuffix = []HTTPDAGStep{
		{StepID: "s2_retry", ToolName: "mock_light", DependsOn: []string{"s3_new"}},
		{StepID: "s3_new", ToolName: "mock_heavy", DependsOn: []string{"s2_retry"}},
	}
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "dependency cycle")
}

func TestPlanAmendmentMissingDependencyRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.ReplacementSuffix[0].DependsOn = []string{"missing-step"}
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "depends on unknown step")
}

func TestPlanAmendmentUnknownToolRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.ReplacementSuffix[0].ToolName = "ghost_tool"
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "unknown tool")
}

func TestPlanAmendmentBudgetOverflowRejected(t *testing.T) {
	amendment := testValidPlanAmendment()
	amendment.ReplacementSuffix = []HTTPDAGStep{
		{StepID: "s2_retry", ToolName: "mock_heavy", DependsOn: []string{"s1"}},
		{StepID: "s3_retry", ToolName: "mock_heavy", DependsOn: []string{"s2_retry"}},
		{StepID: "s4_retry", ToolName: "mock_heavy", DependsOn: []string{"s3_retry"}},
	}
	assertPlanAmendmentErrorContains(t, testPlanAmendmentCheckpoint(t), amendment, "exceeds budget")
}

func TestPlanAmendmentMaxCountRejected(t *testing.T) {
	cp := testPlanAmendmentCheckpoint(t)
	cp.AmendmentVersion = 3
	assertPlanAmendmentErrorContains(t, cp, testValidPlanAmendment(), "count exceeded")
}

func TestPlanAmendmentInvalidLeavesCheckpointUnchanged(t *testing.T) {
	cp := testPlanAmendmentCheckpoint(t)
	original := cp.Clone()
	amendment := testValidPlanAmendment()
	amendment.BaseStep = 99
	_, err := applyAmendmentToCheckpoint(
		cp,
		amendment,
		testAmendmentPolicy(),
		&CommitmentTokenClaims{PlanHash: cp.CurrentPlanHash},
		"parent-digest",
		nil,
		testAmendmentHandlers(),
	)
	if err == nil {
		t.Fatalf("expected amendment error")
	}
	if !reflect.DeepEqual(cp, original) {
		t.Fatalf("checkpoint mutated on invalid amendment")
	}
}

func assertPlanAmendmentErrorContains(
	t *testing.T, cp *SessionCheckpoint, amendment *HTTPPlanAmendment, want string,
) {
	t.Helper()
	_, err := applyAmendmentToCheckpoint(
		cp,
		amendment,
		testAmendmentPolicy(),
		&CommitmentTokenClaims{PlanHash: cp.CurrentPlanHash},
		"parent-digest",
		nil,
		testAmendmentHandlers(),
	)
	if err == nil {
		t.Fatalf("expected amendment error containing %q", want)
	}
	if !strings.Contains(err.Error(), want) {
		t.Fatalf("error=%q, want substring %q", err.Error(), want)
	}
}

func testPlanAmendmentCheckpoint(t *testing.T) *SessionCheckpoint {
	t.Helper()
	remainingJSON, err := json.Marshal([]HTTPDAGStep{
		{StepID: "s2", ToolName: "mock_light", DependsOn: []string{"s1"}},
		{StepID: "s3", ToolName: "mock_heavy", DependsOn: []string{"s2"}},
	})
	if err != nil {
		t.Fatalf("marshal remaining plan: %v", err)
	}
	return &SessionCheckpoint{
		SessionID:   "sess-1",
		Mode:        AgentModePlanSolve,
		Status:      StatusCheckpointed,
		CurrentStep: 2,
		CompletedSteps: []StepRecord{
			{StepID: "s0", StepIndex: 0, ToolName: "mock_light"},
			{StepID: "s1", StepIndex: 1, ToolName: "mock_light"},
		},
		RemainingPlanJSON: remainingJSON,
		LockedPriceSnapshot: map[string]int64{
			"mock_light": 10,
			"mock_heavy": 30,
		},
		BudgetSnapshot:   100,
		OriginalPlanHash: "base-plan-hash",
		CurrentPlanHash:  "base-plan-hash",
	}
}

func testValidPlanAmendment() *HTTPPlanAmendment {
	return &HTTPPlanAmendment{
		SessionID:              "sess-1",
		AmendmentID:            "amend-1",
		BaseStep:               2,
		BasePlanHash:           "base-plan-hash",
		ParentCommitmentDigest: "parent-digest",
		Reason:                 AmendmentReasonToolFailure,
		ReplacementSuffix: []HTTPDAGStep{
			{StepID: "s2_retry", ToolName: "mock_light", DependsOn: []string{"s1"}},
			{StepID: "s3_new", ToolName: "mock_heavy", DependsOn: []string{"s2_retry"}},
		},
	}
}

func testAmendmentPolicy() AmendmentPolicy {
	return AmendmentPolicy{
		Mode:              AmendmentModeRecoveryOnly,
		MaxCount:          3,
		MaxBudgetDelta:    0,
		RequireCommitment: true,
	}
}

func testAmendmentHandlers() map[string]mcpgov.ToolCallHandler {
	noop := func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		return &mcpgov.MCPToolCallResult{
			Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}},
		}, nil
	}
	return map[string]mcpgov.ToolCallHandler{
		"mock_light": noop,
		"mock_heavy": noop,
	}
}
