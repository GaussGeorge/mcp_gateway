package plangate

import (
	"errors"
	"strings"

	mcpgov "mcp-governance"
)

// ─────────────────────────────────────────────────────────────────────────────
// PlanGate-R Phase 4A: Recoverable Failure Classifier
//
// This file contains pure classification functions that decide whether a failure
// is recoverable (→ checkpoint should be upgraded to CHECKPOINTED status) or
// terminal (→ checkpoint should stay ACTIVE_CHECKPOINT or be discarded).
//
// Rules for extensibility:
//   - Never access the checkpoint store here.
//   - Never change handler or admission logic here.
//   - When in doubt, classify as terminal (fail-safe).
// ─────────────────────────────────────────────────────────────────────────────

// RecoveryDecision is the outcome of failure classification.
type RecoveryDecision string

const (
	// RecoveryDecisionNone means no active session was affected; nothing to do.
	RecoveryDecisionNone RecoveryDecision = "none"
	// RecoveryDecisionRecoverable means the failure is transient; a checkpoint
	// should be promoted to CHECKPOINTED so Phase 4B/5 can retry the session.
	RecoveryDecisionRecoverable RecoveryDecision = "recoverable"
	// RecoveryDecisionTerminal means the failure is permanent; the session
	// cannot be retried. The checkpoint (if any) should remain ACTIVE_CHECKPOINT
	// (or be deleted) and must not enter the recovery queue.
	RecoveryDecisionTerminal RecoveryDecision = "terminal"
)

// RecoveryFailure is the result of classifying a single failure event.
// Category and Reason carry the structured failure metadata that will be
// persisted in the checkpoint's LastFailureCategory / LastFailureReason fields.
type RecoveryFailure struct {
	Decision   RecoveryDecision
	Category   FailureCategory
	Reason     FailureReason
	HTTPStatus int    // 0 if not applicable
	Message    string // human-readable summary (not persisted to checkpoint)
}

// ─────────────────────────────────────────────────────────────────────────────
// classifyTransportError classifies a Go error from tool handler execution.
//
// This is called when handler(ctx, params) returns a non-nil error.
// Most transport errors are recoverable (transient network / backend issues).
// Only context.Canceled is terminal (client explicitly cancelled).
// ─────────────────────────────────────────────────────────────────────────────

func classifyTransportError(err error) RecoveryFailure {
	if err == nil {
		return RecoveryFailure{Decision: RecoveryDecisionNone}
	}

	msg := strings.ToLower(err.Error())

	// Context cancelled explicitly by the client → terminal.
	if errors.Is(err, errContextCanceled) || strings.Contains(msg, "context canceled") {
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryClientCancel,
			Reason:   FailureReasonUserCancelled,
			Message:  err.Error(),
		}
	}

	// Deadline / timeout → recoverable backend timeout.
	if errors.Is(err, errDeadlineExceeded) ||
		strings.Contains(msg, "deadline exceeded") ||
		strings.Contains(msg, "context deadline") ||
		strings.Contains(msg, "timeout") ||
		strings.Contains(msg, "timed out") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryBackendTimeout,
			Reason:   FailureReasonWorkerTimeout,
			Message:  err.Error(),
		}
	}

	// Connection refused / refused / reset / unavailable → recoverable.
	if strings.Contains(msg, "connection refused") ||
		strings.Contains(msg, "connection reset") ||
		strings.Contains(msg, "no such host") ||
		strings.Contains(msg, "i/o timeout") ||
		strings.Contains(msg, "eof") ||
		strings.Contains(msg, "network") ||
		strings.Contains(msg, "unavailable") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryBackendUnavail,
			Reason:   FailureReasonBackend5XX,
			Message:  err.Error(),
		}
	}

	// Rate limit / overload signals in error string → recoverable.
	if strings.Contains(msg, "rate limit") ||
		strings.Contains(msg, "ratelimit") ||
		strings.Contains(msg, "429") ||
		strings.Contains(msg, "too many requests") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryExternalRateLimit,
			Reason:   FailureReasonUpstream429,
			Message:  err.Error(),
		}
	}

	if strings.Contains(msg, "overload") ||
		strings.Contains(msg, "overloaded") ||
		strings.Contains(msg, "capacity") ||
		strings.Contains(msg, "server busy") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryBackendUnavail,
			Reason:   FailureReasonBackend5XX,
			Message:  err.Error(),
		}
	}

	// Default: unknown transport error → terminal (fail-safe).
	return RecoveryFailure{
		Decision: RecoveryDecisionTerminal,
		Category: FailureCategoryToolError,
		Reason:   FailureReasonToolSemanticFail,
		Message:  err.Error(),
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// classifyJSONRPCError classifies a failure from a JSON-RPC error response.
//
// This is called when the tool call returns a *JSONRPCResponse with Error != nil.
// The gateway itself generates these responses for overload / rate-limit events,
// and the backend may also return them.
// ─────────────────────────────────────────────────────────────────────────────

func classifyJSONRPCError(code int, message string) RecoveryFailure {
	msg := strings.ToLower(message)

	switch code {
	// ── Gateway overload / load-shedding ──────────────────────────────────────
	case mcpgov.CodeOverloaded:
		// Step-0 overload on a fresh session: no prior checkpoint → Decision=none
		// (caller decides whether there's a checkpoint to promote).
		// We still return Recoverable so the caller can make the decision.
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryGatewayOverload,
			Reason:   FailureReasonCapFull,
			Message:  message,
		}

	case mcpgov.CodeRateLimited:
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryExternalRateLimit,
			Reason:   FailureReasonUpstream429,
			Message:  message,
		}

	case mcpgov.CodeTokenInsufficient:
		// Token budget exhausted by the agent → terminal (agent's own problem).
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryToolPolicy,
			Reason:   FailureReasonMissingIdempotencyKey,
			Message:  message,
		}

	// ── Standard JSON-RPC errors ─────────────────────────────────────────────
	case mcpgov.CodeParseError, mcpgov.CodeInvalidRequest, mcpgov.CodeInvalidParams:
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryRequestInvalid,
			Reason:   FailureReasonMalformedJSON,
			Message:  message,
		}

	case mcpgov.CodeMethodNotFound:
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryDagInvalid,
			Reason:   FailureReasonDAGMissingDep,
			Message:  message,
		}

	case mcpgov.CodeInternalError:
		// Internal errors may be transient (backend crash) or permanent.
		// Inspect the message string for hints.
		return classifyInternalErrorMessage(msg, message)
	}

	// Unknown code → check message content.
	return classifyInternalErrorMessage(msg, message)
}

// classifyInternalErrorMessage applies keyword heuristics to an error message.
// Used for CodeInternalError and unknown codes.
func classifyInternalErrorMessage(msgLower, original string) RecoveryFailure {
	// Terminal signals.
	if strings.Contains(msgLower, "unauthorized") || strings.Contains(msgLower, "forbidden") ||
		strings.Contains(msgLower, "auth") || strings.Contains(msgLower, "permission") {
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryAuthFail,
			Reason:   FailureReasonUnauthorized,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "invalid dag") || strings.Contains(msgLower, "cycle") {
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryDagInvalid,
			Reason:   FailureReasonDAGCycle,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "malformed") || strings.Contains(msgLower, "parse") ||
		strings.Contains(msgLower, "invalid json") {
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategoryRequestInvalid,
			Reason:   FailureReasonMalformedJSON,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "banned") || strings.Contains(msgLower, "fraud") {
		return RecoveryFailure{
			Decision: RecoveryDecisionTerminal,
			Category: FailureCategorySecurity,
			Reason:   FailureReasonBanned,
			Message:  original,
		}
	}

	// Recoverable signals.
	if strings.Contains(msgLower, "timeout") || strings.Contains(msgLower, "timed out") ||
		strings.Contains(msgLower, "deadline") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryBackendTimeout,
			Reason:   FailureReasonWorkerTimeout,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "overload") || strings.Contains(msgLower, "overloaded") ||
		strings.Contains(msgLower, "capacity") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryGatewayOverload,
			Reason:   FailureReasonCapFull,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "rate limit") || strings.Contains(msgLower, "ratelimit") ||
		strings.Contains(msgLower, "429") || strings.Contains(msgLower, "too many requests") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryExternalRateLimit,
			Reason:   FailureReasonUpstream429,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "unavailable") || strings.Contains(msgLower, "refused") ||
		strings.Contains(msgLower, "network") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryBackendUnavail,
			Reason:   FailureReasonBackend5XX,
			Message:  original,
		}
	}
	if strings.Contains(msgLower, "queue") && strings.Contains(msgLower, "timeout") {
		return RecoveryFailure{
			Decision: RecoveryDecisionRecoverable,
			Category: FailureCategoryQueueTimeout,
			Reason:   FailureReasonQueueWaitExceeded,
			Message:  original,
		}
	}

	// Default: unknown → terminal.
	return RecoveryFailure{
		Decision: RecoveryDecisionTerminal,
		Category: FailureCategoryToolError,
		Reason:   FailureReasonToolSemanticFail,
		Message:  original,
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Sentinel errors used for Is() checks.
// Go's context package errors are checked via errors.Is; provide aliases here
// to make the classifyTransportError function testable without importing context.
// ─────────────────────────────────────────────────────────────────────────────

var (
	errContextCanceled  = contextCanceledErr{}
	errDeadlineExceeded = deadlineExceededErr{}
)

// contextCanceledErr and deadlineExceededErr are sentinels that match
// context.Canceled and context.DeadlineExceeded via errors.Is.
// They are unexported; tests use errors.New("context canceled") string matching.

type contextCanceledErr struct{}

func (contextCanceledErr) Error() string { return "context canceled" }
func (contextCanceledErr) Is(target error) bool {
	return target != nil && target.Error() == "context canceled"
}

type deadlineExceededErr struct{}

func (deadlineExceededErr) Error() string { return "context deadline exceeded" }
func (deadlineExceededErr) Is(target error) bool {
	return target != nil && (target.Error() == "context deadline exceeded" ||
		target.Error() == "deadline exceeded")
}
