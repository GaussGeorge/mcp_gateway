package plangate

import (
	"encoding/json"
	"sort"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// PlanGate-R: Checkpoint Types (Phase 2)
//
// IMPORTANT: These types are intentionally decoupled from runtime structs
// (MCPDPServer, HTTPSessionReservation, ReactSessionState).
// Do NOT import or reference live runtime state here.
// All recovery fields use JSON / primitive / map types for future persistence.
// ─────────────────────────────────────────────────────────────────────────────

// AgentMode identifies the session execution mode.
type AgentMode string

const (
	// AgentModePlanSolve is the Plan-and-Solve mode (DAG-based, deterministic step order).
	AgentModePlanSolve AgentMode = "ps"
	// AgentModeReAct is the ReAct mode (LLM-driven, best-effort conversational recovery).
	AgentModeReAct AgentMode = "react"
)

// SessionStatus represents the state-machine status of a checkpoint record.
// Only CHECKPOINTED and RECOVERY_QUEUED checkpoints are eligible for recovery.
type SessionStatus string

const (
	StatusNew       SessionStatus = "NEW"
	StatusRunning   SessionStatus = "RUNNING"
	StatusCommitted SessionStatus = "COMMITTED"
	// StatusActiveCheckpoint marks a mid-session progress snapshot saved after a
	// successful tool step. The session is still in-flight; this record is NOT
	// eligible for recovery and will NOT be returned by ListRecoverable.
	// Phase 4 may transition this to StatusCheckpointed when an interruption is
	// detected (e.g. gateway restart, budget exhaustion).
	StatusActiveCheckpoint SessionStatus = "ACTIVE_CHECKPOINT"
	StatusCheckpointed     SessionStatus = "CHECKPOINTED"
	StatusRecoveryQueued   SessionStatus = "RECOVERY_QUEUED"
	StatusRecovering       SessionStatus = "RECOVERING"
	StatusSucceeded        SessionStatus = "SUCCEEDED"
	StatusFailedTerminal   SessionStatus = "FAILED_TERMINAL"
	StatusExpired          SessionStatus = "EXPIRED"
)

// FailureCategory is the high-level class of a failure event.
// Used for recovery routing decisions and metrics aggregation.
type FailureCategory string

const (
	FailureCategoryGatewayOverload   FailureCategory = "GATEWAY_OVERLOAD"
	FailureCategoryBackendTimeout    FailureCategory = "BACKEND_TIMEOUT"
	FailureCategoryQueueTimeout      FailureCategory = "QUEUE_TIMEOUT"
	FailureCategoryExternalRateLimit FailureCategory = "EXTERNAL_RATELIMIT"
	FailureCategoryBackendUnavail    FailureCategory = "BACKEND_UNAVAILABLE"
	FailureCategoryRecoveryLimit     FailureCategory = "RECOVERY_LIMIT"
	FailureCategoryRecoveryDeadline  FailureCategory = "RECOVERY_DEADLINE"
	FailureCategoryCheckpointExpired FailureCategory = "CHECKPOINT_EXPIRED"
	FailureCategoryToolPolicy        FailureCategory = "TOOL_POLICY"
	FailureCategoryDagInvalid        FailureCategory = "DAG_INVALID"
	FailureCategoryClientCancel      FailureCategory = "CLIENT_CANCEL"
	FailureCategoryToolError         FailureCategory = "TOOL_ERROR"
	FailureCategoryAuthFail          FailureCategory = "AUTH_FAIL"
	FailureCategoryRequestInvalid    FailureCategory = "REQUEST_INVALID"
	FailureCategorySecurity          FailureCategory = "SECURITY"
)

// FailureReason is the specific reason code within a FailureCategory.
type FailureReason string

const (
	FailureReasonCapFull               FailureReason = "CAP_FULL"
	FailureReasonWorkerTimeout         FailureReason = "WORKER_TIMEOUT"
	FailureReasonQueueWaitExceeded     FailureReason = "QUEUE_WAIT_EXCEEDED"
	FailureReasonUpstream429           FailureReason = "UPSTREAM_429"
	FailureReasonBackend5XX            FailureReason = "BACKEND_5XX"
	FailureReasonMaxAttemptsExceeded   FailureReason = "MAX_ATTEMPTS_EXCEEDED"
	FailureReasonDeadlineExceeded      FailureReason = "DEADLINE_EXCEEDED"
	FailureReasonCheckpointTTL         FailureReason = "CHECKPOINT_TTL"
	FailureReasonMissingIdempotencyKey FailureReason = "MISSING_IDEMPOTENCY_KEY"
	FailureReasonDAGCycle              FailureReason = "DAG_CYCLE"
	FailureReasonDAGMissingDep         FailureReason = "DAG_MISSING_DEP"
	FailureReasonUserCancelled         FailureReason = "USER_CANCELLED"
	FailureReasonToolSemanticFail      FailureReason = "TOOL_SEMANTIC_FAIL"
	FailureReasonUnauthorized          FailureReason = "UNAUTHORIZED"
	FailureReasonForbidden             FailureReason = "FORBIDDEN"
	FailureReasonMalformedJSON         FailureReason = "MALFORMED_JSON"
	FailureReasonBanned                FailureReason = "BANNED"
	FailureReasonBudgetFraud           FailureReason = "BUDGET_FRAUD"
	FailureReasonMockRejectedPartial   FailureReason = "MOCK_REJECTED_PARTIAL"
)

// StepRecord captures per-step execution metadata for a completed tool step.
// P&S sessions use StepID; ReAct sessions may leave StepID empty.
// OutputRef holds an opaque reference or content-address to the step result
// (NOT the raw output, to avoid storing PII in the checkpoint).
type StepRecord struct {
	StepID         string    `json:"step_id,omitempty"`
	StepIndex      int       `json:"step_index"`
	ToolName       string    `json:"tool_name"`
	OutputRef      string    `json:"output_ref,omitempty"`    // opaque content-address / reference ID
	OutputDigest   string    `json:"output_digest,omitempty"` // sha256 hex of raw output, for integrity
	IdempotencyKey string    `json:"idempotency_key,omitempty"`
	CompletedAt    time.Time `json:"completed_at"`
}

// clone returns a shallow copy of the StepRecord (all fields are value types).
func (s StepRecord) clone() StepRecord { return s }

// SessionCheckpoint is the serializable snapshot of a session's execution progress.
//
// Design notes (Phase 2):
//   - RemainingPlanJSON is an opaque []byte holding the serialized HTTPDAGPlan subgraph.
//     Phase 3 will populate this from the live HTTPSessionReservation. Using []byte here
//     keeps Phase 2 decoupled from runtime types.
//   - ConversationTrace / ObservationHistory are gateway-visible summaries only in Phase 2/3.
//     Full LLM context injection requires client cooperation (Phase 5).
//   - NonRecoverable=true marks sessions that must go directly to FAILED_TERMINAL on
//     the next failure (e.g., side-effect tool executed without idempotency key).
type SessionCheckpoint struct {
	// ── Identity ─────────────────────────────────────────────────────────────
	SessionID string        `json:"session_id"`
	AgentID   string        `json:"agent_id,omitempty"`
	Mode      AgentMode     `json:"mode"`
	Status    SessionStatus `json:"status"`

	// ── Progress ──────────────────────────────────────────────────────────────
	CurrentStep      int  `json:"current_step"`
	RecoveryAttempts int  `json:"recovery_attempts"`
	NonRecoverable   bool `json:"non_recoverable,omitempty"`

	// ── Completed step records ────────────────────────────────────────────────
	CompletedSteps []StepRecord `json:"completed_steps,omitempty"`

	// ── P&S specific ──────────────────────────────────────────────────────────
	// RemainingPlanJSON: serialized remaining HTTPDAGPlan (subgraph of unexecuted steps).
	// Populated by Phase 3 runtime integration; nil in Phase 2 tests.
	RemainingPlanJSON    []byte             `json:"remaining_plan_json,omitempty"`
	LockedPriceSnapshot  map[string]int64   `json:"locked_price_snapshot,omitempty"`
	ToolWeightSnapshot   map[string]float64 `json:"tool_weight_snapshot,omitempty"`
	BudgetSnapshot       int64              `json:"budget_snapshot,omitempty"`
	OriginalPlanHash     string             `json:"original_plan_hash,omitempty"`
	CurrentPlanHash      string             `json:"current_plan_hash,omitempty"`
	AmendmentVersion     int                `json:"amendment_version,omitempty"`
	AmendmentChainHash   string             `json:"amendment_chain_hash,omitempty"`
	LastAmendmentID      string             `json:"last_amendment_id,omitempty"`
	LastAmendmentReason  string             `json:"last_amendment_reason,omitempty"`
	ParentCommitmentHash string             `json:"parent_commitment_hash,omitempty"`
	DeltaHash            string             `json:"delta_hash,omitempty"`

	// ── ReAct specific ────────────────────────────────────────────────────────
	// Phase 2/3: gateway-visible summaries only (tool names, response status lines).
	// Phase 5: full LLM message trace injected by client via _meta.trace_summary.
	// Do NOT log raw content; may contain PII.
	ConversationTrace  []string `json:"conversation_trace,omitempty"`
	ObservationHistory []string `json:"observation_history,omitempty"`

	// ── Governance / metrics ─────────────────────────────────────────────────
	GovernanceIntensityAtCheckpoint float64 `json:"governance_intensity,omitempty"`
	TokenUsageSoFar                 int64   `json:"token_usage_so_far,omitempty"`
	ComputeStepsSoFar               int     `json:"compute_steps_so_far,omitempty"`
	CheckpointBytes                 int     `json:"checkpoint_bytes,omitempty"` // set after serialization

	// ── Timing ───────────────────────────────────────────────────────────────
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
	// ExpiresAt zero means "no TTL set". Expire() leaves zero-ExpiresAt records alone.
	ExpiresAt time.Time `json:"expires_at,omitempty"`

	// ── Failure context ───────────────────────────────────────────────────────
	LastFailureCategory FailureCategory `json:"last_failure_category,omitempty"`
	LastFailureReason   FailureReason   `json:"last_failure_reason,omitempty"`

	// ── Side-effect safety ────────────────────────────────────────────────────
	// IdempotencyKeys maps step_id → idempotency_key for side-effecting tools.
	// If a side-effecting tool is executed without an entry here, NonRecoverable
	// is set to true by the runtime (Phase 6).
	IdempotencyKeys map[string]string `json:"idempotency_keys,omitempty"`
}

// Clone returns a deep copy of the SessionCheckpoint.
// All slice, map, and []byte fields are independently copied so that mutations
// to the clone do not affect the original (and vice versa).
func (c *SessionCheckpoint) Clone() *SessionCheckpoint {
	if c == nil {
		return nil
	}
	out := *c // copy all value fields

	// Deep copy CompletedSteps
	if c.CompletedSteps != nil {
		out.CompletedSteps = make([]StepRecord, len(c.CompletedSteps))
		for i, s := range c.CompletedSteps {
			out.CompletedSteps[i] = s.clone()
		}
	}

	// Deep copy RemainingPlanJSON
	if c.RemainingPlanJSON != nil {
		out.RemainingPlanJSON = make([]byte, len(c.RemainingPlanJSON))
		copy(out.RemainingPlanJSON, c.RemainingPlanJSON)
	}

	// Deep copy LockedPriceSnapshot
	if c.LockedPriceSnapshot != nil {
		out.LockedPriceSnapshot = make(map[string]int64, len(c.LockedPriceSnapshot))
		for k, v := range c.LockedPriceSnapshot {
			out.LockedPriceSnapshot[k] = v
		}
	}

	// Deep copy ToolWeightSnapshot
	if c.ToolWeightSnapshot != nil {
		out.ToolWeightSnapshot = make(map[string]float64, len(c.ToolWeightSnapshot))
		for k, v := range c.ToolWeightSnapshot {
			out.ToolWeightSnapshot[k] = v
		}
	}

	// Deep copy ConversationTrace
	if c.ConversationTrace != nil {
		out.ConversationTrace = make([]string, len(c.ConversationTrace))
		copy(out.ConversationTrace, c.ConversationTrace)
	}

	// Deep copy ObservationHistory
	if c.ObservationHistory != nil {
		out.ObservationHistory = make([]string, len(c.ObservationHistory))
		copy(out.ObservationHistory, c.ObservationHistory)
	}

	// Deep copy IdempotencyKeys
	if c.IdempotencyKeys != nil {
		out.IdempotencyKeys = make(map[string]string, len(c.IdempotencyKeys))
		for k, v := range c.IdempotencyKeys {
			out.IdempotencyKeys[k] = v
		}
	}

	return &out
}

// MarshalJSON computes CheckpointBytes as a side-effect during serialization.
// This allows callers to get a size estimate without a separate Sizeof call.
func (c *SessionCheckpoint) MarshalJSON() ([]byte, error) {
	// Use a local alias to avoid infinite recursion.
	type Alias SessionCheckpoint
	tmp := (*Alias)(c)
	b, err := json.Marshal(tmp)
	if err != nil {
		return nil, err
	}
	// Update CheckpointBytes on the receiver so the next Load reflects the real size.
	c.CheckpointBytes = len(b)
	return b, nil
}

func hashCheckpointForCommitment(cp *SessionCheckpoint) (string, error) {
	if cp == nil {
		return "", nil
	}

	remainingPlanHash := ""
	if len(cp.RemainingPlanJSON) > 0 {
		remainingPlanHash = sha256Base64URL(cp.RemainingPlanJSON)
	}
	priceHash := ""
	if len(cp.LockedPriceSnapshot) > 0 {
		var err error
		priceHash, err = hashLockedPrices(cp.LockedPriceSnapshot)
		if err != nil {
			return "", err
		}
	}

	completed := make([]struct {
		StepID       string `json:"step_id,omitempty"`
		StepIndex    int    `json:"step_index"`
		ToolName     string `json:"tool_name"`
		OutputDigest string `json:"output_digest,omitempty"`
	}, len(cp.CompletedSteps))
	for i, step := range cp.CompletedSteps {
		completed[i] = struct {
			StepID       string `json:"step_id,omitempty"`
			StepIndex    int    `json:"step_index"`
			ToolName     string `json:"tool_name"`
			OutputDigest string `json:"output_digest,omitempty"`
		}{
			StepID:       step.StepID,
			StepIndex:    step.StepIndex,
			ToolName:     step.ToolName,
			OutputDigest: step.OutputDigest,
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
		SessionID          string      `json:"session_id"`
		CurrentStep        int         `json:"current_step"`
		CompletedSteps     interface{} `json:"completed_steps"`
		RemainingPlanHash  string      `json:"remaining_plan_hash,omitempty"`
		CurrentPlanHash    string      `json:"current_plan_hash,omitempty"`
		AmendmentVersion   int         `json:"amendment_version,omitempty"`
		AmendmentChainHash string      `json:"amendment_chain_hash,omitempty"`
		LockedPriceHash    string      `json:"locked_price_hash,omitempty"`
		BudgetSnapshot     int64       `json:"budget_snapshot,omitempty"`
	}{
		SessionID:          cp.SessionID,
		CurrentStep:        cp.CurrentStep,
		CompletedSteps:     completed,
		RemainingPlanHash:  remainingPlanHash,
		CurrentPlanHash:    cp.CurrentPlanHash,
		AmendmentVersion:   cp.AmendmentVersion,
		AmendmentChainHash: cp.AmendmentChainHash,
		LockedPriceHash:    priceHash,
		BudgetSnapshot:     cp.BudgetSnapshot,
	}
	data, err := json.Marshal(material)
	if err != nil {
		return "", err
	}
	return sha256Base64URL(data), nil
}
