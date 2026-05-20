package plangate

import (
	"context"
	"errors"
	"fmt"
	"testing"

	mcpgov "mcp-governance"
)

// ─────────────────────────────────────────────────────────────────────────────
// Tests: classifyTransportError
// ─────────────────────────────────────────────────────────────────────────────

func TestClassifyTransportError_NilIsNone(t *testing.T) {
	f := classifyTransportError(nil)
	if f.Decision != RecoveryDecisionNone {
		t.Errorf("nil error: expected Decision=none, got %q", f.Decision)
	}
}

func TestClassifyTransportError_ContextCanceled_Terminal(t *testing.T) {
	f := classifyTransportError(context.Canceled)
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("context.Canceled: expected terminal, got %q", f.Decision)
	}
	if f.Category != FailureCategoryClientCancel {
		t.Errorf("context.Canceled: expected category CLIENT_CANCEL, got %q", f.Category)
	}
}

func TestClassifyTransportError_DeadlineExceeded_Recoverable(t *testing.T) {
	f := classifyTransportError(errors.New("deadline exceeded: upstream LLM"))
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("deadline exceeded: expected recoverable, got %q", f.Decision)
	}
	if f.Category != FailureCategoryBackendTimeout {
		t.Errorf("deadline exceeded: expected BACKEND_TIMEOUT, got %q", f.Category)
	}
	if f.Reason != FailureReasonWorkerTimeout {
		t.Errorf("deadline exceeded: expected WORKER_TIMEOUT reason, got %q", f.Reason)
	}
}

func TestClassifyTransportError_Timeout_Recoverable(t *testing.T) {
	f := classifyTransportError(fmt.Errorf("request timed out after 30s"))
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("timed out: expected recoverable, got %q", f.Decision)
	}
	if f.Reason != FailureReasonWorkerTimeout {
		t.Errorf("timed out: expected WORKER_TIMEOUT, got %q", f.Reason)
	}
}

func TestClassifyTransportError_ConnectionRefused_Recoverable(t *testing.T) {
	f := classifyTransportError(errors.New("dial tcp: connection refused"))
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("connection refused: expected recoverable, got %q", f.Decision)
	}
	if f.Category != FailureCategoryBackendUnavail {
		t.Errorf("connection refused: expected BACKEND_UNAVAILABLE, got %q", f.Category)
	}
}

func TestClassifyTransportError_RateLimit429_Recoverable(t *testing.T) {
	f := classifyTransportError(errors.New("HTTP 429: too many requests"))
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("429: expected recoverable, got %q", f.Decision)
	}
	if f.Category != FailureCategoryExternalRateLimit {
		t.Errorf("429: expected EXTERNAL_RATELIMIT, got %q", f.Category)
	}
}

func TestClassifyTransportError_Overloaded_Recoverable(t *testing.T) {
	f := classifyTransportError(errors.New("server is overloaded, retry later"))
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("overloaded: expected recoverable, got %q", f.Decision)
	}
}

func TestClassifyTransportError_Unknown_Terminal(t *testing.T) {
	f := classifyTransportError(errors.New("unexpected tool panic"))
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("unknown error: expected terminal (fail-safe), got %q", f.Decision)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: classifyJSONRPCError
// ─────────────────────────────────────────────────────────────────────────────

func TestClassifyJSONRPCError_CodeOverloaded_Recoverable(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeOverloaded, "gateway capacity full")
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("CodeOverloaded: expected recoverable, got %q", f.Decision)
	}
	if f.Category != FailureCategoryGatewayOverload {
		t.Errorf("CodeOverloaded: expected GATEWAY_OVERLOAD, got %q", f.Category)
	}
}

func TestClassifyJSONRPCError_CodeRateLimited_Recoverable(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeRateLimited, "upstream rate limit")
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("CodeRateLimited: expected recoverable, got %q", f.Decision)
	}
}

func TestClassifyJSONRPCError_CodeTokenInsufficient_Terminal(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeTokenInsufficient, "token budget exhausted")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("CodeTokenInsufficient: expected terminal, got %q", f.Decision)
	}
}

func TestClassifyJSONRPCError_CodeParseError_Terminal(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeParseError, "malformed JSON payload")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("CodeParseError: expected terminal, got %q", f.Decision)
	}
	if f.Category != FailureCategoryRequestInvalid {
		t.Errorf("CodeParseError: expected REQUEST_INVALID, got %q", f.Category)
	}
}

func TestClassifyJSONRPCError_CodeMethodNotFound_Terminal(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeMethodNotFound, "method not found")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("CodeMethodNotFound: expected terminal, got %q", f.Decision)
	}
	if f.Category != FailureCategoryDagInvalid {
		t.Errorf("CodeMethodNotFound: expected DAG_INVALID, got %q", f.Category)
	}
}

func TestClassifyJSONRPCError_CodeInternalError_TimeoutHeuristic_Recoverable(t *testing.T) {
	// CodeInternalError with timeout keyword → recoverable via heuristic.
	f := classifyJSONRPCError(mcpgov.CodeInternalError, "backend call timed out")
	if f.Decision != RecoveryDecisionRecoverable {
		t.Errorf("CodeInternalError+timeout: expected recoverable, got %q", f.Decision)
	}
}

func TestClassifyJSONRPCError_CodeInternalError_AuthHeuristic_Terminal(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeInternalError, "unauthorized: api key invalid")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("CodeInternalError+auth: expected terminal, got %q", f.Decision)
	}
	if f.Category != FailureCategoryAuthFail {
		t.Errorf("CodeInternalError+auth: expected AUTH_FAIL, got %q", f.Category)
	}
}

func TestClassifyJSONRPCError_CodeInternalError_DAGCycle_Terminal(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeInternalError, "invalid dag: cycle detected")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("invalid dag: expected terminal, got %q", f.Decision)
	}
	if f.Reason != FailureReasonDAGCycle {
		t.Errorf("invalid dag: expected DAG_CYCLE reason, got %q", f.Reason)
	}
}

func TestClassifyJSONRPCError_CodeInternalError_Banned_Terminal(t *testing.T) {
	f := classifyJSONRPCError(mcpgov.CodeInternalError, "session banned for fraud")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("banned: expected terminal, got %q", f.Decision)
	}
	if f.Category != FailureCategorySecurity {
		t.Errorf("banned: expected SECURITY, got %q", f.Category)
	}
}

func TestClassifyJSONRPCError_UnknownCode_DefaultTerminal(t *testing.T) {
	// Unknown code and no recognizable keyword → fail-safe terminal.
	f := classifyJSONRPCError(-99999, "something completely unexpected")
	if f.Decision != RecoveryDecisionTerminal {
		t.Errorf("unknown code: expected terminal (fail-safe), got %q", f.Decision)
	}
}
