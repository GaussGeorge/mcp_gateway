package plangate

import (
	"context"
	"errors"
	"log"
	"sort"
	"strconv"
	"strings"
	"time"
)

// timeNow is a variable so tests can override it.
var timeNow = time.Now

// errSkipUpdate is a sentinel returned from Update callbacks when the record
// should NOT be modified (e.g., because it is already in a terminal state).
// The Update implementation must propagate this error unchanged so callers can
// distinguish "skip" from a real storage error.
var errSkipUpdate = errors.New("skip update: checkpoint status not eligible for promotion")

// isSkipUpdate returns true if err is the errSkipUpdate sentinel.
func isSkipUpdate(err error) bool { return errors.Is(err, errSkipUpdate) }

// ─────────────────────────────────────────────────────────────────────────────
// PlanGate-R Phase 3: Runtime Checkpoint Integration Helpers
//
// These helpers are called from dual_mode_routing.go after a successful tool
// step. They are all no-ops when recoveryConfig.Enabled is false, preserving
// 100% backward compatibility with the pre-Phase-3 baseline.
//
// What Phase 3 does NOT implement (deferred):
//   - Recovery queue / recovery admission
//   - P&S recovery execution
//   - ReAct semantic recovery (Phase 5: client-cooperative trace metadata)
//   - Experiment script changes
// ─────────────────────────────────────────────────────────────────────────────

// saveCheckpointAfterStep persists a checkpoint after a successful tool step.
//
// The caller is responsible for populating cp with session progress. This helper
// fills in any missing metadata fields (AgentID, timestamps, Status, ExpiresAt)
// and delegates to the configured CheckpointStore.
//
// Failure is non-fatal: errors are logged but never returned to the caller.
// This ensures that a checkpoint store failure cannot affect the tool call result.
func (s *MCPDPServer) saveCheckpointAfterStep(ctx context.Context, cp *SessionCheckpoint) {
	if s == nil || !s.recoveryConfig.Enabled || s.checkpointStore == nil {
		return
	}
	if cp == nil || cp.SessionID == "" {
		return
	}

	now := time.Now()

	// Fill missing identity fields.
	if cp.AgentID == "" {
		cp.AgentID = deriveAgentID(cp.SessionID)
	}

	// Timestamp hygiene.
	if cp.CreatedAt.IsZero() {
		cp.CreatedAt = now
	}
	cp.UpdatedAt = now

	// Apply TTL if not already set by caller.
	if cp.ExpiresAt.IsZero() && s.recoveryConfig.TTL > 0 {
		cp.ExpiresAt = now.Add(s.recoveryConfig.TTL)
	}

	// Default status: ACTIVE_CHECKPOINT signals "live session progress".
	// This intentionally differs from CHECKPOINTED, which means
	// "interrupted and eligible for recovery" (used by ListRecoverable).
	// Phase 4's interruption detector may later update ACTIVE_CHECKPOINT →
	// CHECKPOINTED when it observes a recoverable failure.
	if cp.Status == "" {
		cp.Status = StatusActiveCheckpoint
	}

	if err := s.checkpointStore.Update(ctx, cp.SessionID, func(existing *SessionCheckpoint) (*SessionCheckpoint, error) {
		return mergeCheckpointProgress(existing, cp), nil
	}); err != nil {
		if errors.Is(err, ErrCheckpointNotFound) {
			if saveErr := s.checkpointStore.Save(ctx, cp); saveErr != nil {
				log.Printf("[PlanGate-R] checkpoint save failed session=%s: %v", cp.SessionID, saveErr)
			}
			return
		}
		// Non-fatal: log and continue. The tool call already returned success.
		log.Printf("[PlanGate-R] checkpoint save failed session=%s: %v", cp.SessionID, err)
	}
}

func mergeCheckpointProgress(existing, incoming *SessionCheckpoint) *SessionCheckpoint {
	if existing == nil {
		return incoming.Clone()
	}
	if incoming == nil {
		return existing.Clone()
	}

	merged := existing.Clone()

	if incoming.AgentID != "" {
		merged.AgentID = incoming.AgentID
	}
	if incoming.Mode != "" {
		merged.Mode = incoming.Mode
	}
	if incoming.Status != "" {
		merged.Status = incoming.Status
	}
	if incoming.CurrentStep >= merged.CurrentStep {
		merged.CurrentStep = incoming.CurrentStep
	}
	if incoming.RecoveryAttempts > merged.RecoveryAttempts {
		merged.RecoveryAttempts = incoming.RecoveryAttempts
	}
	merged.NonRecoverable = merged.NonRecoverable || incoming.NonRecoverable
	if len(incoming.CompletedSteps) > 0 {
		merged.CompletedSteps = mergeCompletedStepRecords(merged.CompletedSteps, incoming.CompletedSteps)
	}
	if incoming.RemainingPlanJSON != nil {
		merged.RemainingPlanJSON = append([]byte(nil), incoming.RemainingPlanJSON...)
	}
	if incoming.LockedPriceSnapshot != nil {
		merged.LockedPriceSnapshot = cloneInt64Map(incoming.LockedPriceSnapshot)
	}
	if incoming.ToolWeightSnapshot != nil {
		merged.ToolWeightSnapshot = cloneFloat64Map(incoming.ToolWeightSnapshot)
	}
	if incoming.BudgetSnapshot != 0 {
		merged.BudgetSnapshot = incoming.BudgetSnapshot
	}
	if incoming.OriginalPlanHash != "" {
		merged.OriginalPlanHash = incoming.OriginalPlanHash
	}
	if incoming.CurrentPlanHash != "" {
		merged.CurrentPlanHash = incoming.CurrentPlanHash
	}
	if incoming.AmendmentVersion >= merged.AmendmentVersion {
		merged.AmendmentVersion = incoming.AmendmentVersion
	}
	if incoming.AmendmentChainHash != "" {
		merged.AmendmentChainHash = incoming.AmendmentChainHash
	}
	if incoming.LastAmendmentID != "" {
		merged.LastAmendmentID = incoming.LastAmendmentID
	}
	if incoming.LastAmendmentReason != "" {
		merged.LastAmendmentReason = incoming.LastAmendmentReason
	}
	if incoming.ParentCommitmentHash != "" {
		merged.ParentCommitmentHash = incoming.ParentCommitmentHash
	}
	if incoming.DeltaHash != "" {
		merged.DeltaHash = incoming.DeltaHash
	}
	if len(incoming.ConversationTrace) > 0 {
		merged.ConversationTrace = append([]string(nil), incoming.ConversationTrace...)
	}
	if len(incoming.ObservationHistory) > 0 {
		merged.ObservationHistory = append([]string(nil), incoming.ObservationHistory...)
	}
	if incoming.GovernanceIntensityAtCheckpoint != 0 {
		merged.GovernanceIntensityAtCheckpoint = incoming.GovernanceIntensityAtCheckpoint
	}
	if incoming.TokenUsageSoFar != 0 {
		merged.TokenUsageSoFar = incoming.TokenUsageSoFar
	}
	if incoming.ComputeStepsSoFar != 0 {
		merged.ComputeStepsSoFar = incoming.ComputeStepsSoFar
	}
	if !incoming.CreatedAt.IsZero() && merged.CreatedAt.IsZero() {
		merged.CreatedAt = incoming.CreatedAt
	}
	if !incoming.ExpiresAt.IsZero() {
		merged.ExpiresAt = incoming.ExpiresAt
	}
	if incoming.LastFailureCategory != "" {
		merged.LastFailureCategory = incoming.LastFailureCategory
	}
	if incoming.LastFailureReason != "" {
		merged.LastFailureReason = incoming.LastFailureReason
	}
	if incoming.IdempotencyKeys != nil {
		merged.IdempotencyKeys = cloneStringMap(incoming.IdempotencyKeys)
	}

	return merged
}

func mergeCompletedStepRecords(existing, incoming []StepRecord) []StepRecord {
	merged := make([]StepRecord, 0, len(existing)+len(incoming))
	seen := make(map[string]struct{}, len(existing)+len(incoming))
	for _, step := range existing {
		key := completedStepRecordKey(step)
		seen[key] = struct{}{}
		merged = append(merged, step)
	}
	for _, step := range incoming {
		key := completedStepRecordKey(step)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		merged = append(merged, step)
	}
	sort.SliceStable(merged, func(i, j int) bool {
		if merged[i].StepIndex != merged[j].StepIndex {
			return merged[i].StepIndex < merged[j].StepIndex
		}
		if merged[i].StepID != merged[j].StepID {
			return merged[i].StepID < merged[j].StepID
		}
		return merged[i].ToolName < merged[j].ToolName
	})
	return merged
}

func completedStepRecordKey(step StepRecord) string {
	if step.StepID != "" {
		return step.StepID
	}
	return "idx:" + strconv.Itoa(step.StepIndex)
}

func cloneInt64Map(src map[string]int64) map[string]int64 {
	if src == nil {
		return nil
	}
	out := make(map[string]int64, len(src))
	for k, v := range src {
		out[k] = v
	}
	return out
}

func cloneFloat64Map(src map[string]float64) map[string]float64 {
	if src == nil {
		return nil
	}
	out := make(map[string]float64, len(src))
	for k, v := range src {
		out[k] = v
	}
	return out
}

func cloneStringMap(src map[string]string) map[string]string {
	if src == nil {
		return nil
	}
	out := make(map[string]string, len(src))
	for k, v := range src {
		out[k] = v
	}
	return out
}

// deleteCheckpointOnSuccess removes the checkpoint for a successfully completed
// session. This is called once the gateway detects that the session's last step
// has been executed.
//
// Idempotent: deleting a non-existent checkpoint is not an error.
// Failure is non-fatal: errors are logged but do not affect the success response.
func (s *MCPDPServer) deleteCheckpointOnSuccess(ctx context.Context, sessionID string) {
	if s == nil || !s.recoveryConfig.Enabled || s.checkpointStore == nil {
		return
	}
	if sessionID == "" {
		return
	}
	if err := s.checkpointStore.Delete(ctx, sessionID); err != nil {
		log.Printf("[PlanGate-R] checkpoint delete failed session=%s: %v", sessionID, err)
	}
}

// markCheckpointRecoverable promotes an ACTIVE_CHECKPOINT (or RUNNING) record
// to CHECKPOINTED status after a recoverable interruption is detected.
//
// Only statuses that represent "still running" (ACTIVE_CHECKPOINT, RUNNING) are
// promoted. Terminal or already-succeeded checkpoints are never modified.
//
// Phase 4A contract:
//   - Does NOT change the error response returned to the client.
//   - Does NOT increment RecoveryAttempts (that is Phase 4B's responsibility).
//   - Does NOT enqueue the session for recovery execution.
//   - Failure is non-fatal: errors are only logged.
func (s *MCPDPServer) markCheckpointRecoverable(
	ctx context.Context,
	sessionID string,
	failure RecoveryFailure,
) {
	if s == nil || !s.recoveryConfig.Enabled || s.checkpointStore == nil {
		return
	}
	if sessionID == "" {
		return
	}
	if failure.Decision != RecoveryDecisionRecoverable {
		return
	}

	err := s.checkpointStore.Update(ctx, sessionID, func(cp *SessionCheckpoint) (*SessionCheckpoint, error) {
		// Only promote "in-flight" statuses.
		if cp.Status != StatusActiveCheckpoint && cp.Status != StatusRunning {
			// Already CHECKPOINTED, SUCCEEDED, FAILED_TERMINAL, etc. — do not modify.
			return nil, errSkipUpdate
		}
		cp.Status = StatusCheckpointed
		cp.LastFailureCategory = failure.Category
		cp.LastFailureReason = failure.Reason
		cp.UpdatedAt = timeNow()
		return cp, nil
	})

	if err != nil {
		if !isSkipUpdate(err) {
			log.Printf("[PlanGate-R] markCheckpointRecoverable failed session=%s: %v", sessionID, err)
		}
	} else {
		log.Printf("[PlanGate-R] checkpoint promoted to CHECKPOINTED session=%s category=%s reason=%s",
			sessionID, failure.Category, failure.Reason)
	}
}

// nextPSStepState computes the next-step state for a P&S session after a
// successful tool execution.
//
// currentStep is the 0-based "steps already completed" counter read from
// HTTPSessionReservation.CurrentStep BEFORE budgetMgr.Advance is called.
// nextStep is the index of the step to execute next (= the value that
// CurrentStep will hold after Advance). complete is true when no more steps
// remain — the session should be marked succeeded and its checkpoint deleted.
//
// Example: 3-step plan
//
//	currentStep=0 → nextStep=1, complete=false   (step[0] just ran, step[1] next)
//	currentStep=1 → nextStep=2, complete=false   (step[1] just ran, step[2] next)
//	currentStep=2 → nextStep=3, complete=true    (step[2] just ran, all done)
func nextPSStepState(currentStep, totalSteps int) (nextStep int, complete bool) {
	nextStep = currentStep + 1
	complete = nextStep >= totalSteps
	return
}

// deriveAgentID extracts an agent identifier from a session ID.
//
// Rules:
//   - ""                  → "unknown"
//   - "agent-session456"  → "agent"   (prefix before first "-")
//   - "session456"        → "session456" (no "-" → whole string)
func deriveAgentID(sessionID string) string {
	if sessionID == "" {
		return "unknown"
	}
	if idx := strings.Index(sessionID, "-"); idx >= 0 {
		return sessionID[:idx]
	}
	return sessionID
}
