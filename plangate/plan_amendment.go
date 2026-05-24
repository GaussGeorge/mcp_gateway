package plangate

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sort"

	mcpgov "mcp-governance"
)

type AmendmentMode string

const (
	AmendmentModeOff          AmendmentMode = "off"
	AmendmentModeRecoveryOnly AmendmentMode = "recovery-only"
)

type AmendmentStatus string

const (
	AmendmentStatusAccepted      AmendmentStatus = "accepted"
	AmendmentStatusRejected      AmendmentStatus = "rejected"
	AmendmentStatusDisabled      AmendmentStatus = "disabled"
	AmendmentStatusNotApplicable AmendmentStatus = "not_applicable"
)

type AmendmentReason string

const (
	AmendmentReasonToolFailure AmendmentReason = "tool_failure"
)

type AmendmentPolicy struct {
	Mode              AmendmentMode
	MaxCount          int
	MaxBudgetDelta    int64
	RequireCommitment bool
}

type HTTPPlanAmendment struct {
	SessionID              string          `json:"session_id"`
	AmendmentID            string          `json:"amendment_id"`
	BaseStep               int             `json:"base_step"`
	BasePlanHash           string          `json:"base_plan_hash"`
	ParentCommitmentDigest string          `json:"parent_commitment_digest"`
	Reason                 AmendmentReason `json:"reason"`
	BudgetDelta            int64           `json:"budget_delta"`
	ReplacementSuffix      []HTTPDAGStep   `json:"replacement_suffix"`
}

type AppliedPlanAmendment struct {
	Checkpoint           *SessionCheckpoint
	TotalCost            int64
	TotalSteps           int
	PriceHash            string
	CheckpointHash       string
	ParentCommitmentHash string
	DeltaHash            string
	AmendmentChainHash   string
	AmendmentVersion     int
}

func expectedCheckpointPlanHash(cp *SessionCheckpoint, parentClaims *CommitmentTokenClaims) string {
	if cp == nil {
		return ""
	}
	if cp.CurrentPlanHash != "" {
		return cp.CurrentPlanHash
	}
	if cp.OriginalPlanHash != "" {
		return cp.OriginalPlanHash
	}
	if parentClaims != nil {
		return parentClaims.PlanHash
	}
	return ""
}

func checkpointPriceHash(cp *SessionCheckpoint) (string, error) {
	if cp == nil || len(cp.LockedPriceSnapshot) == 0 {
		return "", fmt.Errorf("checkpoint missing locked price snapshot")
	}
	return hashLockedPrices(cp.LockedPriceSnapshot)
}

func checkpointTotalSteps(cp *SessionCheckpoint, remainingSteps []HTTPDAGStep) int {
	if cp == nil {
		return len(remainingSteps)
	}
	return cp.CurrentStep + len(remainingSteps)
}

func checkpointTotalCost(cp *SessionCheckpoint, remainingSteps []HTTPDAGStep) (int64, error) {
	if cp == nil {
		return 0, fmt.Errorf("checkpoint is nil")
	}
	if len(cp.LockedPriceSnapshot) == 0 {
		return 0, fmt.Errorf("checkpoint missing locked price snapshot")
	}
	total := int64(0)
	for _, step := range cp.CompletedSteps {
		price, ok := cp.LockedPriceSnapshot[step.ToolName]
		if !ok {
			return 0, fmt.Errorf("completed step %q missing locked price", step.ToolName)
		}
		total += price
	}
	for _, step := range remainingSteps {
		price, ok := cp.LockedPriceSnapshot[step.ToolName]
		if !ok {
			return 0, fmt.Errorf("remaining step %q missing locked price", step.ToolName)
		}
		total += price
	}
	return total, nil
}

func (s *MCPDPServer) amendmentPolicy() AmendmentPolicy {
	if s == nil {
		return defaultAmendmentPolicy()
	}
	return AmendmentPolicy{
		Mode:              s.amendmentMode,
		MaxCount:          s.amendmentMaxCount,
		MaxBudgetDelta:    s.amendmentMaxBudgetDelta,
		RequireCommitment: s.amendmentRequireCommitment,
	}
}

func defaultAmendmentPolicy() AmendmentPolicy {
	return AmendmentPolicy{
		Mode:              AmendmentModeRecoveryOnly,
		MaxCount:          3,
		MaxBudgetDelta:    0,
		RequireCommitment: true,
	}
}

func setAmendmentStatus(w http.ResponseWriter, status AmendmentStatus) {
	if w != nil && status != "" {
		w.Header().Set(HeaderAmendmentStatus, string(status))
	}
}

func setAmendmentFailure(w http.ResponseWriter, status AmendmentStatus, amendmentID, reason string) {
	setAmendmentStatus(w, status)
	if w == nil {
		return
	}
	if amendmentID != "" {
		w.Header().Set(HeaderAmendmentID, amendmentID)
	}
	if reason != "" {
		w.Header().Set(HeaderAmendmentError, reason)
	}
}

func normalizeAmendmentMode(mode AmendmentMode) (AmendmentMode, error) {
	if mode == "" {
		return AmendmentModeRecoveryOnly, nil
	}
	switch mode {
	case AmendmentModeOff, AmendmentModeRecoveryOnly:
		return mode, nil
	default:
		return "", fmt.Errorf("invalid amendment mode %q", mode)
	}
}

func hashPlanAmendment(amendment *HTTPPlanAmendment) (string, error) {
	if amendment == nil {
		return "", fmt.Errorf("amendment is nil")
	}
	data, err := json.Marshal(amendment)
	if err != nil {
		return "", err
	}
	return sha256Base64URL(data), nil
}

func hashAmendedPlanContract(
	sessionID string,
	completedSteps []StepRecord,
	replacementSuffix []HTTPDAGStep,
	budget int64,
) (string, error) {
	completed := make([]struct {
		StepID    string `json:"step_id,omitempty"`
		StepIndex int    `json:"step_index"`
		ToolName  string `json:"tool_name"`
	}, len(completedSteps))
	for i, step := range completedSteps {
		completed[i] = struct {
			StepID    string `json:"step_id,omitempty"`
			StepIndex int    `json:"step_index"`
			ToolName  string `json:"tool_name"`
		}{
			StepID:    step.StepID,
			StepIndex: step.StepIndex,
			ToolName:  step.ToolName,
		}
	}
	sort.SliceStable(completed, func(i, j int) bool {
		if completed[i].StepIndex != completed[j].StepIndex {
			return completed[i].StepIndex < completed[j].StepIndex
		}
		if completed[i].StepID != completed[j].StepID {
			return completed[i].StepID < completed[j].StepID
		}
		return completed[i].ToolName < completed[j].ToolName
	})
	material := struct {
		SessionID         string        `json:"session_id"`
		Budget            int64         `json:"budget"`
		CompletedSteps    interface{}   `json:"completed_steps"`
		ReplacementSuffix []HTTPDAGStep `json:"replacement_suffix"`
	}{
		SessionID:         sessionID,
		Budget:            budget,
		CompletedSteps:    completed,
		ReplacementSuffix: replacementSuffix,
	}
	data, err := json.Marshal(material)
	if err != nil {
		return "", err
	}
	return sha256Base64URL(data), nil
}

func computeAmendmentChainHash(
	previousChain, parentHash, deltaHash string,
	version int,
	baseStep int,
	newPlanHash string,
) string {
	material := struct {
		PreviousChain string `json:"previous_chain,omitempty"`
		ParentHash    string `json:"parent_hash"`
		DeltaHash     string `json:"delta_hash"`
		Version       int    `json:"version"`
		BaseStep      int    `json:"base_step"`
		NewPlanHash   string `json:"new_plan_hash"`
	}{
		PreviousChain: previousChain,
		ParentHash:    parentHash,
		DeltaHash:     deltaHash,
		Version:       version,
		BaseStep:      baseStep,
		NewPlanHash:   newPlanHash,
	}
	data, _ := json.Marshal(material)
	return sha256Base64URL(data)
}

func validateReplacementSuffix(
	suffix []HTTPDAGStep,
	completedSteps []StepRecord,
	handlers map[string]mcpgov.ToolCallHandler,
) error {
	completedSet := make(map[string]struct{}, len(completedSteps))
	for _, step := range completedSteps {
		if step.StepID != "" {
			completedSet[step.StepID] = struct{}{}
		}
	}

	suffixSet := make(map[string]HTTPDAGStep, len(suffix))
	for _, step := range suffix {
		if step.StepID == "" {
			return fmt.Errorf("replacement suffix contains empty step_id")
		}
		if step.ToolName == "" {
			return fmt.Errorf("replacement suffix step %q missing tool_name", step.StepID)
		}
		if _, exists := suffixSet[step.StepID]; exists {
			return fmt.Errorf("replacement suffix duplicates step_id %q", step.StepID)
		}
		if _, exists := completedSet[step.StepID]; exists {
			return fmt.Errorf("replacement suffix modifies completed step %q", step.StepID)
		}
		if handlers != nil {
			if _, ok := handlers[step.ToolName]; !ok {
				return fmt.Errorf("replacement suffix references unknown tool %q", step.ToolName)
			}
		}
		suffixSet[step.StepID] = step
	}

	inDegree := make(map[string]int, len(suffixSet))
	adj := make(map[string][]string, len(suffixSet))
	for _, step := range suffix {
		inDegree[step.StepID] = 0
	}

	for _, step := range suffix {
		for _, dep := range step.DependsOn {
			if _, ok := suffixSet[dep]; ok {
				inDegree[step.StepID]++
				adj[dep] = append(adj[dep], step.StepID)
				continue
			}
			if _, ok := completedSet[dep]; ok {
				continue
			}
			return fmt.Errorf("replacement suffix step %q depends on unknown step %q", step.StepID, dep)
		}
	}

	queue := make([]string, 0, len(inDegree))
	for stepID, degree := range inDegree {
		if degree == 0 {
			queue = append(queue, stepID)
		}
	}
	sort.Strings(queue)

	visited := 0
	for len(queue) > 0 {
		stepID := queue[0]
		queue = queue[1:]
		visited++
		for _, next := range adj[stepID] {
			inDegree[next]--
			if inDegree[next] == 0 {
				queue = append(queue, next)
				sort.Strings(queue)
			}
		}
	}

	if visited != len(suffix) {
		return fmt.Errorf("replacement suffix contains a dependency cycle")
	}
	return nil
}

func applyAmendmentToCheckpoint(
	cp *SessionCheckpoint,
	amendment *HTTPPlanAmendment,
	policy AmendmentPolicy,
	parentClaims *CommitmentTokenClaims,
	parentCommitmentHash string,
	priceForTool func(string) int64,
	handlers map[string]mcpgov.ToolCallHandler,
) (*AppliedPlanAmendment, error) {
	if cp == nil {
		return nil, fmt.Errorf("checkpoint is nil")
	}
	if amendment == nil {
		return nil, fmt.Errorf("amendment is nil")
	}
	if amendment.SessionID == "" || amendment.SessionID != cp.SessionID {
		return nil, fmt.Errorf("amendment session_id mismatch")
	}
	if amendment.AmendmentID == "" {
		return nil, fmt.Errorf("amendment_id is required")
	}
	if amendment.Reason == "" {
		return nil, fmt.Errorf("amendment reason is required")
	}
	if amendment.BaseStep != cp.CurrentStep {
		return nil, fmt.Errorf("amendment base_step mismatch: amendment=%d checkpoint=%d", amendment.BaseStep, cp.CurrentStep)
	}

	expectedPlanHash := cp.CurrentPlanHash
	if expectedPlanHash == "" {
		expectedPlanHash = cp.OriginalPlanHash
	}
	if expectedPlanHash == "" && parentClaims != nil {
		expectedPlanHash = parentClaims.PlanHash
	}
	if expectedPlanHash == "" {
		return nil, fmt.Errorf("checkpoint missing base plan hash")
	}
	if amendment.BasePlanHash != expectedPlanHash {
		return nil, fmt.Errorf("amendment base_plan_hash mismatch")
	}

	if parentCommitmentHash != "" && amendment.ParentCommitmentDigest != parentCommitmentHash {
		return nil, fmt.Errorf("amendment parent commitment digest mismatch")
	}

	if policy.MaxCount > 0 && cp.AmendmentVersion >= policy.MaxCount {
		return nil, fmt.Errorf("amendment count exceeded")
	}
	if amendment.BudgetDelta < 0 {
		return nil, fmt.Errorf("amendment budget_delta must be non-negative")
	}
	if amendment.BudgetDelta > policy.MaxBudgetDelta {
		return nil, fmt.Errorf("amendment budget_delta exceeds policy limit")
	}
	if cp.CurrentStep > 0 && len(cp.CompletedSteps) < cp.CurrentStep {
		return nil, fmt.Errorf("checkpoint missing completed prefix ledger")
	}
	if err := validateReplacementSuffix(amendment.ReplacementSuffix, cp.CompletedSteps, handlers); err != nil {
		return nil, err
	}

	lockedPrices := make(map[string]int64)
	for _, step := range cp.CompletedSteps {
		if _, ok := lockedPrices[step.ToolName]; ok {
			continue
		}
		if cp.LockedPriceSnapshot != nil {
			if price, ok := cp.LockedPriceSnapshot[step.ToolName]; ok {
				lockedPrices[step.ToolName] = price
				continue
			}
		}
		if priceForTool == nil {
			return nil, fmt.Errorf("missing price snapshot for completed tool %q", step.ToolName)
		}
		lockedPrices[step.ToolName] = priceForTool(step.ToolName)
	}
	for _, step := range amendment.ReplacementSuffix {
		if _, ok := lockedPrices[step.ToolName]; ok {
			continue
		}
		if cp.LockedPriceSnapshot != nil {
			if price, ok := cp.LockedPriceSnapshot[step.ToolName]; ok {
				lockedPrices[step.ToolName] = price
				continue
			}
		}
		if priceForTool == nil {
			return nil, fmt.Errorf("missing price for amendment tool %q", step.ToolName)
		}
		lockedPrices[step.ToolName] = priceForTool(step.ToolName)
	}

	executedCost := int64(0)
	for _, step := range cp.CompletedSteps {
		price, ok := lockedPrices[step.ToolName]
		if !ok {
			return nil, fmt.Errorf("completed step %q missing locked price", step.ToolName)
		}
		executedCost += price
	}

	remainingCost := int64(0)
	for _, step := range amendment.ReplacementSuffix {
		price, ok := lockedPrices[step.ToolName]
		if !ok {
			return nil, fmt.Errorf("replacement step %q missing locked price", step.ToolName)
		}
		remainingCost += price
	}

	baseBudget := cp.BudgetSnapshot
	allowedBudget := baseBudget + amendment.BudgetDelta
	newTotalCost := executedCost + remainingCost
	if newTotalCost > allowedBudget {
		return nil, fmt.Errorf("amended plan exceeds budget")
	}

	remainingJSON, err := json.Marshal(amendment.ReplacementSuffix)
	if err != nil {
		return nil, err
	}
	deltaHash, err := hashPlanAmendment(amendment)
	if err != nil {
		return nil, err
	}
	newPlanHash, err := hashAmendedPlanContract(
		cp.SessionID,
		cp.CompletedSteps,
		amendment.ReplacementSuffix,
		allowedBudget,
	)
	if err != nil {
		return nil, err
	}
	previousChainHash := cp.AmendmentChainHash
	if previousChainHash == "" {
		previousChainHash = expectedPlanHash
	}
	nextAmendmentVersion := cp.AmendmentVersion + 1
	newChainHash := computeAmendmentChainHash(
		previousChainHash,
		parentCommitmentHash,
		deltaHash,
		nextAmendmentVersion,
		amendment.BaseStep,
		newPlanHash,
	)
	priceHash, err := hashLockedPrices(lockedPrices)
	if err != nil {
		return nil, err
	}

	updated := cp.Clone()
	updated.RemainingPlanJSON = remainingJSON
	updated.LockedPriceSnapshot = lockedPrices
	updated.BudgetSnapshot = allowedBudget
	if updated.OriginalPlanHash == "" {
		updated.OriginalPlanHash = expectedPlanHash
	}
	updated.CurrentPlanHash = newPlanHash
	updated.AmendmentVersion = nextAmendmentVersion
	updated.AmendmentChainHash = newChainHash
	updated.LastAmendmentID = amendment.AmendmentID
	updated.LastAmendmentReason = string(amendment.Reason)
	updated.ParentCommitmentHash = parentCommitmentHash
	updated.DeltaHash = deltaHash
	checkpointHash, err := hashCheckpointForCommitment(updated)
	if err != nil {
		return nil, err
	}

	return &AppliedPlanAmendment{
		Checkpoint:           updated,
		TotalCost:            newTotalCost,
		TotalSteps:           updated.CurrentStep + len(amendment.ReplacementSuffix),
		PriceHash:            priceHash,
		CheckpointHash:       checkpointHash,
		ParentCommitmentHash: parentCommitmentHash,
		DeltaHash:            deltaHash,
		AmendmentChainHash:   newChainHash,
		AmendmentVersion:     nextAmendmentVersion,
	}, nil
}
