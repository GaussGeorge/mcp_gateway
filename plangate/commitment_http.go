package plangate

import (
	"context"
	"log"
	"net/http"

	mcpgov "mcp-governance"
)

func (s *MCPDPServer) commitmentMode() CommitmentTokenMode {
	if s.commitmentTokens == nil {
		return CommitmentTokenModeOff
	}
	return s.commitmentTokens.Mode()
}

func setCommitmentStatus(w http.ResponseWriter, status CommitmentTokenStatus) {
	if w != nil && status != "" {
		w.Header().Set(HeaderCommitmentStatus, string(status))
	}
}

func setCommitmentFailure(w http.ResponseWriter, status CommitmentTokenStatus, reason string) {
	setCommitmentStatus(w, status)
	if w != nil && reason != "" {
		w.Header().Set(HeaderCommitmentError, reason)
	}
}

func (s *MCPDPServer) validateCommitmentForReservedStep(
	w http.ResponseWriter, r *http.Request, req *mcpgov.JSONRPCRequest, res *HTTPSessionReservation,
) *mcpgov.JSONRPCResponse {
	mode := s.commitmentMode()
	if mode == CommitmentTokenModeOff {
		setCommitmentStatus(w, CommitmentTokenStatusDisabled)
		return nil
	}

	token := r.Header.Get(HeaderCommitmentToken)
	if token == "" {
		if mode == CommitmentTokenModeOptional {
			setCommitmentStatus(w, CommitmentTokenStatusLegacy)
			return nil
		}
		setCommitmentFailure(w, CommitmentTokenStatusMissing, "missing commitment token")
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
			"missing commitment token",
			map[string]interface{}{"session_id": res.SessionID, "commitment_status": string(CommitmentTokenStatusMissing)})
	}

	totalSteps := res.TotalSteps
	if totalSteps == 0 && res.Plan != nil {
		totalSteps = len(res.Plan.Steps)
	}
	_, status, reason := s.commitmentTokens.Validate(token, CommitmentTokenValidationContext{
		SessionID:  res.SessionID,
		PlanHash:   res.PlanHash,
		PriceHash:  res.PriceHash,
		TotalCost:  res.TotalCost,
		TotalSteps: totalSteps,
	})
	if status != CommitmentTokenStatusValidated {
		setCommitmentFailure(w, status, reason)
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
			"invalid commitment token",
			map[string]interface{}{"session_id": res.SessionID, "commitment_status": string(status), "reason": reason})
	}
	setCommitmentStatus(w, CommitmentTokenStatusValidated)
	return nil
}

func (s *MCPDPServer) maybeIssueCommitmentToken(
	ctx context.Context, w http.ResponseWriter, res *HTTPSessionReservation, sharedRec *SharedPSRecord,
) {
	if res == nil {
		return
	}
	mode := s.commitmentMode()
	if mode == CommitmentTokenModeOff {
		setCommitmentStatus(w, CommitmentTokenStatusDisabled)
		return
	}
	totalSteps := res.TotalSteps
	if totalSteps == 0 && res.Plan != nil {
		totalSteps = len(res.Plan.Steps)
	}
	if totalSteps <= 1 || res.CurrentStep >= totalSteps {
		setCommitmentStatus(w, CommitmentTokenStatus("complete-no-token"))
		return
	}
	budget := int64(0)
	if res.Plan != nil {
		budget = res.Plan.Budget
	}
	stateStore := "local"
	if _, ok := s.sharedStateStore.(*RedisSessionStateStore); ok {
		stateStore = "redis"
	}
	token, err := s.commitmentTokens.IssueInitialCommitment(CommitmentTokenClaims{
		SessionID:       res.SessionID,
		PlanHash:        res.PlanHash,
		PriceHash:       res.PriceHash,
		Budget:          budget,
		TotalCost:       res.TotalCost,
		TotalSteps:      totalSteps,
		NodeID:          s.nodeID,
		StateStore:      stateStore,
		RecoveryEnabled: s.recoveryConfig.Enabled,
	})
	if err != nil {
		setCommitmentFailure(w, CommitmentTokenStatusInvalid, "token issue failed")
		log.Printf("[PlanGate Commitment] session=%s token issue failed: %v", res.SessionID, err)
		return
	}
	res.CommitmentToken = token
	w.Header().Set(HeaderCommitmentToken, token)
	setCommitmentStatus(w, CommitmentTokenStatusIssued)
	log.Printf("[PlanGate Commitment] session=%s token_issued digest=%s", res.SessionID, commitmentTokenDigest(token))

	if sharedRec != nil && s.sharedStateStore != nil {
		sharedRec.CurrentStep = res.CurrentStep
		sharedRec.PlanHash = res.PlanHash
		sharedRec.PriceHash = res.PriceHash
		sharedRec.CommitmentTokenIssued = true
		if err := s.sharedStateStore.SaveReservation(ctx, sharedRec, s.budgetMgr.maxDuration); err != nil {
			log.Printf("[PlanGate Commitment] session=%s shared-store token metadata save failed: %v", res.SessionID, err)
		}
	}
}
