package plangate

// PlanGate-R Phase 4B/5: Minimum End-to-End P&S Recovery Loop
//
// This file implements the gateway-side recovery execution for Plan-and-Solve sessions.
// When a P&S session is interrupted by a recoverable failure, the checkpoint store
// records its progress (RemainingPlanJSON, CurrentStep, LockedPriceSnapshot).
// Later, the agent sends a recovery resume request and this handler re-executes
// only the remaining steps from where the session left off.
//
// What this file implements:
//   - isRecoveryResumeRequest: header-based detection
//   - handleRecoveryResume: full P&S recovery loop (state transitions + step execution)
//   - markCheckpointTerminal: helper to record permanent failures
//   - PSRecoveryResult: progress & metrics payload returned on success
//   - RecoveryStatsSnapshot: atomic counters for tests and operators
//
// What is intentionally NOT implemented here:
//   - ReAct semantic recovery (client-cooperative; deferred to Phase 5+)
//   - HTTP 202 / polling (synchronous recovery only for now)
//   - Redis / persistent store (InMemory store is sufficient for testing)
//   - Argument injection for recovered steps (Phase 6: stored arguments)

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// ─────────────────────────────────────────────────────────────────────────────
// Protocol helpers
// ─────────────────────────────────────────────────────────────────────────────

// isRecoveryResumeRequest returns true when the HTTP request carries the
// X-Recovery-Mode: resume header, indicating a PlanGate-R recovery attempt.
// Any other value of the header (or absence) falls through to normal admission.
func isRecoveryResumeRequest(r *http.Request) bool {
	return r.Header.Get(HeaderRecoveryMode) == "resume"
}

// ─────────────────────────────────────────────────────────────────────────────
// Result type
// ─────────────────────────────────────────────────────────────────────────────

// PSRecoveryResult is the JSON-RPC result payload returned when P&S recovery
// completes successfully. It carries the proof-of-recovery metrics that
// distinguish PlanGate-R from naive retry.
type PSRecoveryResult struct {
	SessionID string `json:"session_id"`
	Recovered bool   `json:"recovered"`
	// SkippedSteps: steps that were NOT re-executed because a checkpoint existed.
	// naive retry: SkippedSteps=0. PlanGate-R: SkippedSteps=len(completed_steps).
	SkippedSteps int `json:"skipped_steps"`
	// ExecutedSteps: steps actually executed during this recovery run.
	ExecutedSteps     int    `json:"executed_steps"`
	TotalSteps        int    `json:"total_steps"`         // = SkippedSteps + ExecutedSteps
	SavedComputeSteps int    `json:"saved_compute_steps"` // = SkippedSteps (proof of savings)
	Mode              string `json:"mode"`                // "ps_recovery" or "ps_already_complete"
}

// ─────────────────────────────────────────────────────────────────────────────
// Metrics
// ─────────────────────────────────────────────────────────────────────────────

// RecoveryStatsSnapshot is a point-in-time read of the recovery metrics.
// All values are snapshots; they may advance between calls.
type RecoveryStatsSnapshot struct {
	RecoveredSuccessCount int64 // fully recovered sessions
	SkippedStepsTotal     int64 // total steps saved across all recoveries
	RecoveryAttempts      int64 // total RECOVERING transitions attempted
}

// GetRecoveryStats returns a snapshot of the server's recovery metrics.
// Safe to call from any goroutine.
func (s *MCPDPServer) GetRecoveryStats() RecoveryStatsSnapshot {
	return RecoveryStatsSnapshot{
		RecoveredSuccessCount: atomic.LoadInt64(&s.recoveredSuccessCount),
		SkippedStepsTotal:     atomic.LoadInt64(&s.skippedStepsTotal),
		RecoveryAttempts:      atomic.LoadInt64(&s.recoveryAttempts),
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Core recovery handler
// ─────────────────────────────────────────────────────────────────────────────

// handleRecoveryResume is the PlanGate-R Phase 4B gateway-side recovery loop.
//
// Protocol:
//
//	Header X-Recovery-Mode: resume   (required; detected by isRecoveryResumeRequest)
//	Header X-Session-ID: <session>   (required; identifies the checkpoint)
//
// State machine:
//
//	CHECKPOINTED / RECOVERY_QUEUED
//	  → quota check (canAdmitRecovery)
//	  → sessionCap admission
//	  → RECOVERING  (RecoveryAttempts++)
//	  → for each remaining step in RemainingPlanJSON:
//	       success  → update CurrentStep + RemainingPlanJSON in checkpoint
//	       recoverable failure → Status=CHECKPOINTED + release slot → error response
//	       terminal failure   → Status=FAILED_TERMINAL + release slot → error response
//	  → all steps done → delete checkpoint (recoveredSuccessCount++) → success response
//
// Invariants (proven by tests):
//   - completed_steps from the prior run are NEVER re-executed
//   - the sessionCap slot is always released (defer pattern)
//   - checkpoint save/delete failures are non-fatal (logged only)
//   - Recovery is a complete no-op when s.recoveryConfig.Enabled == false
func (s *MCPDPServer) handleRecoveryResume(
	ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest,
) *mcpgov.JSONRPCResponse {
	return s.handleRecoveryResumeWithWriter(ctx, nil, r, req)
}

func (s *MCPDPServer) handleRecoveryResumeWithWriter(
	ctx context.Context, w http.ResponseWriter, r *http.Request, req *mcpgov.JSONRPCRequest,
) *mcpgov.JSONRPCResponse {
	// 0. Guard: recovery must be enabled.
	if !s.recoveryConfig.Enabled || s.checkpointStore == nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest,
			"recovery not enabled on this gateway", nil)
	}

	// 1. Read the target session ID.
	sessionID := r.Header.Get(HeaderSessionID)
	if sessionID == "" {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
			"X-Session-ID header is required for recovery resume", nil)
	}

	// 2. Load checkpoint.
	cp, err := s.checkpointStore.Load(ctx, sessionID)
	if err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
			fmt.Sprintf("no checkpoint found for session %s: cannot resume", sessionID),
			err.Error())
	}

	// 3. Validate Status.
	switch cp.Status {
	case StatusCheckpointed, StatusRecoveryQueued:
		// eligible for recovery
	case StatusActiveCheckpoint, StatusRunning:
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest,
			"session is still active (ACTIVE_CHECKPOINT); cannot resume a live session",
			map[string]interface{}{"session_id": sessionID, "status": string(cp.Status)})
	case StatusRecovering:
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest,
			"recovery is already in progress for this session",
			map[string]interface{}{"session_id": sessionID, "status": string(cp.Status)})
	default:
		// SUCCEEDED, FAILED_TERMINAL, EXPIRED, NEW, COMMITTED, etc.
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest,
			fmt.Sprintf("session %s is in non-recoverable status %q", sessionID, cp.Status),
			map[string]interface{}{"session_id": sessionID, "status": string(cp.Status)})
	}

	// 4. Only P&S semantic recovery is implemented.
	// ReAct recovery requires full LLM message-trace injection (Phase 5 client cooperation).
	if cp.Mode != AgentModePlanSolve {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest,
			"ReAct semantic recovery is not implemented; gateway preserves transport-level"+
				" checkpoints for ReAct but cannot resume without client-side context injection",
			map[string]interface{}{"session_id": sessionID, "mode": string(cp.Mode)})
	}

	// 5. Non-recoverable flag: a side-effecting tool ran without an idempotency key.
	// Replaying such a session risks double-execution of side effects.
	if cp.NonRecoverable {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest,
			"session is marked non-recoverable: a side-effecting tool executed without an idempotency key",
			map[string]interface{}{"session_id": sessionID})
	}

	// 6. Deserialize remaining steps.
	if len(cp.RemainingPlanJSON) == 0 {
		// The checkpoint has no remaining plan. This means either:
		//   a) All steps were already done before the failure → treat as complete.
		//   b) The checkpoint was saved by an older version that didn't store RemainingPlanJSON.
		// Safe action: clean up and return success with 0 executed steps.
		_ = s.checkpointStore.Delete(ctx, sessionID)
		return mcpgov.NewSuccessResponse(req.ID, PSRecoveryResult{
			SessionID:         sessionID,
			Recovered:         true,
			SkippedSteps:      cp.CurrentStep,
			ExecutedSteps:     0,
			TotalSteps:        cp.CurrentStep,
			SavedComputeSteps: cp.CurrentStep,
			Mode:              "ps_already_complete",
		})
	}

	var remainingSteps []HTTPDAGStep
	if err := json.Unmarshal(cp.RemainingPlanJSON, &remainingSteps); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
			"could not deserialize remaining plan from checkpoint", err.Error())
	}
	if len(remainingSteps) == 0 {
		// Already complete; clean up.
		_ = s.checkpointStore.Delete(ctx, sessionID)
		return mcpgov.NewSuccessResponse(req.ID, PSRecoveryResult{
			SessionID: sessionID, Recovered: true,
			SkippedSteps: cp.CurrentStep, ExecutedSteps: 0,
			TotalSteps: cp.CurrentStep, SavedComputeSteps: cp.CurrentStep,
			Mode: "ps_already_complete",
		})
	}

	amendmentHeader := r.Header.Get(HeaderPlanAmendment)
	if amendmentHeader == "" {
		setAmendmentStatus(w, AmendmentStatusNotApplicable)
	} else {
		policy := s.amendmentPolicy()
		var amendment HTTPPlanAmendment
		if err := json.Unmarshal([]byte(amendmentHeader), &amendment); err != nil {
			setAmendmentFailure(w, AmendmentStatusRejected, "", "malformed amendment json")
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
				"invalid plan amendment",
				map[string]interface{}{"session_id": sessionID, "reason": "malformed amendment json"})
		}
		if w != nil && amendment.AmendmentID != "" {
			w.Header().Set(HeaderAmendmentID, amendment.AmendmentID)
		}
		if policy.Mode == AmendmentModeOff {
			setAmendmentFailure(w, AmendmentStatusDisabled, amendment.AmendmentID, "plan amendment disabled")
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
				"plan amendment disabled",
				map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID})
		}

		var parentClaims *CommitmentTokenClaims
		parentToken := r.Header.Get(HeaderCommitmentToken)
		parentCommitmentHash := ""
		if parentToken == "" {
			if policy.RequireCommitment {
				setCommitmentFailure(w, CommitmentTokenStatusMissing, "missing commitment token")
				setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, "missing commitment token")
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
					"missing commitment token",
					map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID})
			}
		} else {
			if s.commitmentMode() == CommitmentTokenModeOff {
				setCommitmentStatus(w, CommitmentTokenStatusDisabled)
				setAmendmentFailure(w, AmendmentStatusDisabled, amendment.AmendmentID, "commitment tokens disabled")
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
					"commitment tokens disabled for amendment recovery",
					map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID})
			}
			rawParentClaims, _, _ := s.commitmentTokens.parseAndVerify(parentToken)

			priceHash, err := checkpointPriceHash(cp)
			if err != nil {
				setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, err.Error())
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
					"checkpoint missing commitment context",
					map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID, "reason": err.Error()})
			}
			totalCost, err := checkpointTotalCost(cp, remainingSteps)
			if err != nil {
				if rawParentClaims != nil {
					totalCost = rawParentClaims.TotalCost
				}
			}
			currentCheckpointHash, err := hashCheckpointForCommitment(cp)
			if err != nil {
				setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, err.Error())
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
					"checkpoint missing commitment context",
					map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID, "reason": err.Error()})
			}
			parentCommitmentHash = commitmentTokenHash(parentToken)
			parsedClaims, status, reason := s.commitmentTokens.ValidateParentCommitmentForAmendment(
				parentToken,
				AmendedCommitmentValidationContext{
					CommitmentTokenValidationContext: CommitmentTokenValidationContext{
						SessionID:  cp.SessionID,
						PlanHash:   expectedCheckpointPlanHash(cp, rawParentClaims),
						PriceHash:  priceHash,
						TotalCost:  totalCost,
						TotalSteps: checkpointTotalSteps(cp, remainingSteps),
					},
					AmendmentVersion:   cp.AmendmentVersion,
					AmendmentChainHash: cp.AmendmentChainHash,
					CheckpointHash:     currentCheckpointHash,
				},
			)
			if status != CommitmentTokenStatusValidated {
				setCommitmentFailure(w, status, reason)
				setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, reason)
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
					"invalid commitment token",
					map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID, "reason": reason})
			}

			parentClaims = parsedClaims
			setCommitmentStatus(w, CommitmentTokenStatusValidated)
		}

		applied, err := applyAmendmentToCheckpoint(
			cp,
			&amendment,
			policy,
			parentClaims,
			parentCommitmentHash,
			func(toolName string) int64 { return s.governor.GetToolEffectivePrice(toolName) },
			s.handlers,
		)
		if err != nil {
			setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, err.Error())
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
				"invalid plan amendment",
				map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID, "reason": err.Error()})
		}

		cp = applied.Checkpoint
		if err := s.checkpointStore.Save(ctx, cp); err != nil {
			setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, "failed to persist amended checkpoint")
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
				"failed to persist amended checkpoint",
				map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID})
		}
		if err := json.Unmarshal(cp.RemainingPlanJSON, &remainingSteps); err != nil {
			setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, "failed to deserialize amended suffix")
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
				"failed to deserialize amended suffix",
				map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID})
		}

		if s.commitmentMode() != CommitmentTokenModeOff {
			stateStore := "local"
			if _, ok := s.sharedStateStore.(*RedisSessionStateStore); ok {
				stateStore = "redis"
			}
			newToken, err := s.commitmentTokens.IssueAmendedCommitment(CommitmentTokenClaims{
				SessionID:            cp.SessionID,
				PlanHash:             cp.CurrentPlanHash,
				PriceHash:            applied.PriceHash,
				Budget:               cp.BudgetSnapshot,
				TotalCost:            applied.TotalCost,
				TotalSteps:           applied.TotalSteps,
				NodeID:               s.nodeID,
				StateStore:           stateStore,
				RecoveryEnabled:      s.recoveryConfig.Enabled,
				AmendmentVersion:     cp.AmendmentVersion,
				AmendmentID:          amendment.AmendmentID,
				ParentCommitmentHash: applied.ParentCommitmentHash,
				DeltaHash:            applied.DeltaHash,
				AmendmentChainHash:   applied.AmendmentChainHash,
				CheckpointHash:       applied.CheckpointHash,
				BaseStep:             amendment.BaseStep,
			})
			if err != nil {
				setCommitmentFailure(w, CommitmentTokenStatusInvalid, "token issue failed")
				setAmendmentFailure(w, AmendmentStatusRejected, amendment.AmendmentID, "failed to issue amended commitment")
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
					"failed to issue amended commitment",
					map[string]interface{}{"session_id": sessionID, "amendment_id": amendment.AmendmentID})
			}
			if w != nil {
				w.Header().Set(HeaderCommitmentToken, newToken)
			}
			setCommitmentStatus(w, CommitmentTokenStatusIssued)
			log.Printf("[PlanGate Amendment] session=%s amendment=%s accepted parent=%s new_token=%s",
				sessionID, amendment.AmendmentID, commitmentTokenDigest(parentToken), commitmentTokenDigest(newToken))
		}

		setAmendmentStatus(w, AmendmentStatusAccepted)
	}

	// 7. Recovery quota check (pure computation, no side effects).
	// We estimate active P0 sessions via the channel length (items in semaphore).
	cfg := DefaultRecoveryQuotaConfig()
	if s.sessionCap != nil {
		totalSlots := cap(s.sessionCap)
		p0Active := len(s.sessionCap) // currently acquired slots
		if !canAdmitRecovery(totalSlots, p0Active, 0, cfg.MaxRecoveryFraction) {
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				"recovery quota exceeded: too many concurrent P0 sessions, try again later",
				map[string]interface{}{"session_id": sessionID})
		}
	}

	// 8. Acquire session cap slot.
	// Recovery sessions are subject to capacity control and must not bypass it.
	var capRelease func()
	if s.sessionCap != nil {
		waitTime := s.sessionCapWait
		if waitTime == 0 {
			waitTime = time.Millisecond // allow at least one scheduling quantum in tests
		}
		select {
		case s.sessionCap <- struct{}{}:
			ch := s.sessionCap
			capRelease = func() { <-ch }
		case <-time.After(waitTime):
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				"session cap full; cannot admit recovery session right now",
				map[string]interface{}{"session_id": sessionID})
		case <-ctx.Done():
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
				"context cancelled while waiting for session cap", nil)
		}
	}
	// Ensure the cap slot is always returned.
	defer func() {
		if capRelease != nil {
			capRelease()
			capRelease = nil
		}
	}()

	// 9. Transition to RECOVERING (increment RecoveryAttempts in the checkpoint record).
	atomic.AddInt64(&s.recoveryAttempts, 1)
	if updErr := s.checkpointStore.Update(ctx, sessionID, func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
		c.Status = StatusRecovering
		c.RecoveryAttempts++
		c.UpdatedAt = timeNow()
		return c, nil
	}); updErr != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
			"failed to transition checkpoint to RECOVERING state", updErr.Error())
	}

	skippedSteps := cp.CurrentStep
	totalSteps := skippedSteps + len(remainingSteps)
	executedSteps := 0

	log.Printf("[PlanGate-R] recovery started session=%s mode=ps skipped=%d remaining=%d total=%d",
		sessionID, skippedSteps, len(remainingSteps), totalSteps)

	// 10. Execute remaining steps in sequence.
	// This is the key invariant: we start from cp.CurrentStep, not from 0.
	// Completed steps are never replayed.
	for i, step := range remainingSteps {
		handler, ok := s.handlers[step.ToolName]
		if !ok {
			// Terminal: tool no longer registered → cannot proceed.
			s.markCheckpointTerminal(ctx, sessionID, FailureCategoryDagInvalid, FailureReasonDAGMissingDep)
			log.Printf("[PlanGate-R] recovery terminal: tool %q not registered session=%s", step.ToolName, sessionID)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
				fmt.Sprintf("recovery: tool %q is not registered on this gateway", step.ToolName),
				map[string]interface{}{"session_id": sessionID, "step_id": step.StepID})
		}

		// Build minimal params. Arguments are empty in Phase 4B because they are not
		// yet persisted in the checkpoint. Phase 6 will inject stored arguments.
		// Mock/controlled tests use handlers that succeed regardless of arguments.
		params := mcpgov.MCPToolCallParams{
			Name: step.ToolName,
		}

		result, execErr := handler(ctx, params)
		if execErr != nil {
			failure := classifyTransportError(execErr)
			newCurrentStep := cp.CurrentStep + executedSteps

			if failure.Decision == RecoveryDecisionRecoverable {
				// Recoverable interruption during recovery: save progress, return to CHECKPOINTED.
				newRemaining, _ := json.Marshal(remainingSteps[i:])
				_ = s.checkpointStore.Update(ctx, sessionID, func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
					c.Status = StatusCheckpointed
					c.CurrentStep = newCurrentStep
					c.RemainingPlanJSON = newRemaining
					c.LastFailureCategory = failure.Category
					c.LastFailureReason = failure.Reason
					c.UpdatedAt = timeNow()
					return c, nil
				})
				log.Printf("[PlanGate-R] recovery re-interrupted session=%s at_step=%d category=%s",
					sessionID, newCurrentStep, failure.Category)
				if capRelease != nil {
					capRelease()
					capRelease = nil
				}
				return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
					fmt.Sprintf("recovery re-interrupted at step %d (%s): %v", newCurrentStep, step.ToolName, execErr),
					map[string]interface{}{
						"session_id":       sessionID,
						"recovered_so_far": executedSteps,
						"status":           string(StatusCheckpointed),
						"retryable":        true,
					})
			}

			// Terminal failure during recovery.
			s.markCheckpointTerminal(ctx, sessionID, failure.Category, failure.Reason)
			log.Printf("[PlanGate-R] recovery terminal failure session=%s step=%d: %v",
				sessionID, newCurrentStep, execErr)
			if capRelease != nil {
				capRelease()
				capRelease = nil
			}
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError,
				fmt.Sprintf("recovery terminal failure at step %d (%s): %v", newCurrentStep, step.ToolName, execErr),
				map[string]interface{}{
					"session_id": sessionID,
					"status":     string(StatusFailedTerminal),
					"retryable":  false,
				})
		}

		// Step succeeded: update progress checkpoint.
		executedSteps++
		_ = result // result carried for future accumulation (Phase 6: session output)

		newCurrentStep := cp.CurrentStep + executedSteps
		newRemaining, _ := json.Marshal(remainingSteps[i+1:])
		_ = s.checkpointStore.Update(ctx, sessionID, func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
			c.CurrentStep = newCurrentStep
			c.RemainingPlanJSON = newRemaining
			c.CompletedSteps = append(c.CompletedSteps, StepRecord{
				StepID:      step.StepID,
				StepIndex:   newCurrentStep - 1,
				ToolName:    step.ToolName,
				CompletedAt: timeNow(),
			})
			c.UpdatedAt = timeNow()
			return c, nil
		})

		log.Printf("[PlanGate-R] recovery step done session=%s tool=%s step=%d/%d",
			sessionID, step.ToolName, newCurrentStep, totalSteps)
	}

	// 11. All remaining steps complete → delete checkpoint and record success.
	_ = s.checkpointStore.Delete(ctx, sessionID)
	atomic.AddInt64(&s.recoveredSuccessCount, 1)
	atomic.AddInt64(&s.skippedStepsTotal, int64(skippedSteps))

	log.Printf("[PlanGate-R] recovery complete session=%s skipped=%d executed=%d total=%d",
		sessionID, skippedSteps, executedSteps, totalSteps)

	// Prevent double-release in defer.
	capRelease = nil

	return mcpgov.NewSuccessResponse(req.ID, PSRecoveryResult{
		SessionID:         sessionID,
		Recovered:         true,
		SkippedSteps:      skippedSteps,
		ExecutedSteps:     executedSteps,
		TotalSteps:        totalSteps,
		SavedComputeSteps: skippedSteps,
		Mode:              "ps_recovery",
	})
}

// markCheckpointTerminal transitions the checkpoint record to FAILED_TERMINAL.
// This is called on permanent, non-retryable failures during recovery execution.
// Failure to update the checkpoint is non-fatal (logged only).
func (s *MCPDPServer) markCheckpointTerminal(
	ctx context.Context,
	sessionID string,
	category FailureCategory,
	reason FailureReason,
) {
	if s.checkpointStore == nil {
		return
	}
	err := s.checkpointStore.Update(ctx, sessionID, func(c *SessionCheckpoint) (*SessionCheckpoint, error) {
		c.Status = StatusFailedTerminal
		c.LastFailureCategory = category
		c.LastFailureReason = reason
		c.UpdatedAt = timeNow()
		return c, nil
	})
	if err != nil && !isSkipUpdate(err) {
		log.Printf("[PlanGate-R] markCheckpointTerminal failed session=%s: %v", sessionID, err)
	}
}
