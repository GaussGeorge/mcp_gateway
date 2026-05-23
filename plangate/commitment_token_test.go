package plangate

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

func TestCommitmentTokenIssueAndValidateSuccess(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := mgr.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	got, status, reason := mgr.Validate(token, testCommitmentContext(claims))
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("Validate status=%s reason=%q", status, reason)
	}
	if got.SessionID != claims.SessionID {
		t.Fatalf("sid=%q, want %q", got.SessionID, claims.SessionID)
	}
}

func TestCommitmentTokenTamperedPayloadFails(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := mgr.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("unexpected token format")
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		t.Fatalf("decode payload: %v", err)
	}
	payload = bytes.Replace(payload, []byte(`"sid":"session-a"`), []byte(`"sid":"session-b"`), 1)
	parts[1] = base64.RawURLEncoding.EncodeToString(payload)
	tampered := strings.Join(parts, ".")
	_, status, _ := mgr.Validate(tampered, testCommitmentContext(claims))
	if status != CommitmentTokenStatusInvalid {
		t.Fatalf("status=%s, want invalid", status)
	}
}

func TestCommitmentTokenWrongSecretFails(t *testing.T) {
	issuer := newTestCommitmentManager(t, "secret-a", time.Minute)
	validator := newTestCommitmentManager(t, "secret-b", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := issuer.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	_, status, _ := validator.Validate(token, testCommitmentContext(claims))
	if status != CommitmentTokenStatusInvalid {
		t.Fatalf("status=%s, want invalid", status)
	}
}

func TestCommitmentTokenExpiredFails(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	claims.IssuedAt = time.Now().Add(-2 * time.Minute).Unix()
	claims.ExpiresAt = time.Now().Add(-time.Minute).Unix()
	token, err := mgr.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	_, status, _ := mgr.Validate(token, testCommitmentContext(claims))
	if status != CommitmentTokenStatusExpired {
		t.Fatalf("status=%s, want expired", status)
	}
}

func TestCommitmentTokenWrongSessionIDFails(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := mgr.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	ctx := testCommitmentContext(claims)
	ctx.SessionID = "other-session"
	_, status, _ := mgr.Validate(token, ctx)
	if status != CommitmentTokenStatusMismatch {
		t.Fatalf("status=%s, want mismatch", status)
	}
}

func TestCommitmentTokenPriceHashMismatchFails(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := mgr.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	ctx := testCommitmentContext(claims)
	ctx.PriceHash = "wrong-price-hash"
	_, status, _ := mgr.Validate(token, ctx)
	if status != CommitmentTokenStatusMismatch {
		t.Fatalf("status=%s, want mismatch", status)
	}
}

func TestCommitmentTokenPlanHashMismatchFails(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := mgr.Issue(claims)
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	ctx := testCommitmentContext(claims)
	ctx.PlanHash = "wrong-plan-hash"
	_, status, _ := mgr.Validate(token, ctx)
	if status != CommitmentTokenStatusMismatch {
		t.Fatalf("status=%s, want mismatch", status)
	}
}

func TestCommitmentTokenV2AmendedIssueAndValidateSuccess(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testAmendedCommitmentClaims(t)
	token, err := mgr.IssueAmendedCommitment(claims)
	if err != nil {
		t.Fatalf("IssueAmendedCommitment: %v", err)
	}
	got, status, reason := mgr.ValidateAmendedCommitment(token, testAmendedCommitmentContext(claims))
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("ValidateAmendedCommitment status=%s reason=%q", status, reason)
	}
	if got.Version != commitmentTokenVersionV2 || got.Type != commitmentTokenTypeAmendedPS {
		t.Fatalf("unexpected amended claims: %+v", got)
	}
}

func TestCommitmentTokenV2MissingParentHashRejected(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testAmendedCommitmentClaims(t)
	claims.ParentCommitmentHash = ""
	token := signCommitmentClaimsForTest(t, mgr, claims)
	_, status, reason := mgr.ValidateAmendedCommitment(token, testAmendedCommitmentContext(testAmendedCommitmentClaims(t)))
	if status != CommitmentTokenStatusInvalid {
		t.Fatalf("status=%s reason=%q, want invalid", status, reason)
	}
}

func TestCommitmentTokenV2DeltaHashMismatchRejected(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testAmendedCommitmentClaims(t)
	token, err := mgr.IssueAmendedCommitment(claims)
	if err != nil {
		t.Fatalf("IssueAmendedCommitment: %v", err)
	}
	ctx := testAmendedCommitmentContext(claims)
	ctx.DeltaHash = "wrong-delta-hash"
	_, status, _ := mgr.ValidateAmendedCommitment(token, ctx)
	if status != CommitmentTokenStatusMismatch {
		t.Fatalf("status=%s, want mismatch", status)
	}
}

func TestCommitmentTokenV2ChainHashMismatchRejected(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testAmendedCommitmentClaims(t)
	token, err := mgr.IssueAmendedCommitment(claims)
	if err != nil {
		t.Fatalf("IssueAmendedCommitment: %v", err)
	}
	ctx := testAmendedCommitmentContext(claims)
	ctx.AmendmentChainHash = "wrong-chain-hash"
	_, status, _ := mgr.ValidateAmendedCommitment(token, ctx)
	if status != CommitmentTokenStatusMismatch {
		t.Fatalf("status=%s, want mismatch", status)
	}
}

func TestCommitmentTokenV2CheckpointHashMismatchRejected(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testAmendedCommitmentClaims(t)
	token, err := mgr.IssueAmendedCommitment(claims)
	if err != nil {
		t.Fatalf("IssueAmendedCommitment: %v", err)
	}
	ctx := testAmendedCommitmentContext(claims)
	ctx.CheckpointHash = "wrong-checkpoint-hash"
	_, status, _ := mgr.ValidateAmendedCommitment(token, ctx)
	if status != CommitmentTokenStatusMismatch {
		t.Fatalf("status=%s, want mismatch", status)
	}
}

func TestCommitmentTokenV1CannotMasqueradeAsAmended(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	claims := testCommitmentClaims(t)
	token, err := mgr.IssueInitialCommitment(claims)
	if err != nil {
		t.Fatalf("IssueInitialCommitment: %v", err)
	}
	_, status, reason := mgr.ValidateAmendedCommitment(token, testAmendedCommitmentContext(testAmendedCommitmentClaims(t)))
	if status != CommitmentTokenStatusInvalid {
		t.Fatalf("status=%s reason=%q, want invalid", status, reason)
	}
}

func TestCommitmentTokenMalformedV2DoesNotPanic(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	ctx := testAmendedCommitmentContext(testAmendedCommitmentClaims(t))
	for _, token := range []string{"", "v2", "a.b", "a.b.c", "...", "not.base64.payload"} {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("ValidateAmendedCommitment panicked for %q: %v", token, r)
				}
			}()
			_, status, _ := mgr.ValidateAmendedCommitment(token, ctx)
			if status == CommitmentTokenStatusValidated {
				t.Fatalf("malformed v2 token %q validated", token)
			}
		}()
	}
}

func TestCommitmentTokenMalformedDoesNotPanic(t *testing.T) {
	mgr := newTestCommitmentManager(t, "secret-a", time.Minute)
	for _, token := range []string{"", "a", "a.b", "a.b.c", "...", "not.base64.payload"} {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("Validate panicked for %q: %v", token, r)
				}
			}()
			_, status, _ := mgr.Validate(token, testCommitmentContext(testCommitmentClaims(t)))
			if status == CommitmentTokenStatusValidated {
				t.Fatalf("malformed token %q validated", token)
			}
		}()
	}
}

func TestPlanAndSolveStep0IssuesCommitmentToken(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeOptional, "http-secret", nil)
	plan := testHTTPDAGPlan("ps-issue-token")
	w, resp := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	if resp.Error != nil {
		t.Fatalf("step0 error: %+v", resp.Error)
	}
	if token := w.Header().Get(HeaderCommitmentToken); token == "" {
		t.Fatalf("missing %s", HeaderCommitmentToken)
	}
	claims, status, reason := s.commitmentTokens.parseAndVerify(w.Header().Get(HeaderCommitmentToken))
	if status != CommitmentTokenStatusValidated {
		t.Fatalf("parseAndVerify status=%s reason=%q", status, reason)
	}
	if claims.Version != commitmentTokenVersionV1 || claims.Type != commitmentTokenTypePS {
		t.Fatalf("unexpected initial token claims: %+v", claims)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusIssued) {
		t.Fatalf("status=%q, want issued", got)
	}
}

func TestCommitmentTokenOptionalLegacyContinuation(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeOptional, "http-secret", nil)
	plan := testHTTPDAGPlan("ps-optional-legacy")
	sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	w, resp := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[1].ToolName, nil, 1, "")
	if resp.Error != nil {
		t.Fatalf("step1 error: %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusLegacy) {
		t.Fatalf("status=%q, want legacy", got)
	}
}

func TestCommitmentTokenOffDoesNotIssueOrValidate(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeOff, "", nil)
	plan := testHTTPDAGPlan("ps-off")
	w0, resp0 := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	if resp0.Error != nil {
		t.Fatalf("step0 error: %+v", resp0.Error)
	}
	if token := w0.Header().Get(HeaderCommitmentToken); token != "" {
		t.Fatalf("off mode issued token %q", token)
	}
	w1, resp1 := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[1].ToolName, nil, 1, "bad.token.value")
	if resp1.Error != nil {
		t.Fatalf("off mode validated/rejected token: %+v", resp1.Error)
	}
	if got := w1.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusDisabled) {
		t.Fatalf("status=%q, want disabled", got)
	}
}

func TestCommitmentTokenOptionalValidContinuation(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeOptional, "http-secret", nil)
	plan := testHTTPDAGPlan("ps-optional-valid")
	w0, _ := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	token := w0.Header().Get(HeaderCommitmentToken)
	w, resp := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[1].ToolName, nil, 1, token)
	if resp.Error != nil {
		t.Fatalf("step1 error: %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusValidated) {
		t.Fatalf("status=%q, want validated", got)
	}
}

func TestCommitmentTokenOptionalTamperedContinuationRejected(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeOptional, "http-secret", nil)
	plan := testHTTPDAGPlan("ps-optional-tampered")
	w0, _ := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	token := w0.Header().Get(HeaderCommitmentToken)
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("unexpected token format")
	}
	replacement := "A"
	if strings.HasPrefix(parts[2], "A") {
		replacement = "B"
	}
	parts[2] = replacement + parts[2][1:]
	token = strings.Join(parts, ".")
	w, resp := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[1].ToolName, nil, 1, token)
	if resp.Error == nil || resp.Error.Code != mcpgov.CodeInvalidParams {
		t.Fatalf("expected invalid params, got %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusInvalid) {
		t.Fatalf("status=%q, want invalid", got)
	}
}

func TestCommitmentTokenStrictMissingContinuationRejected(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeStrict, "http-secret", nil)
	plan := testHTTPDAGPlan("ps-strict-missing")
	sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	w, resp := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[1].ToolName, nil, 1, "")
	if resp.Error == nil || resp.Error.Code != mcpgov.CodeInvalidParams {
		t.Fatalf("expected invalid params, got %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusMissing) {
		t.Fatalf("status=%q, want missing", got)
	}
}

func TestCommitmentTokenStrictValidContinuation(t *testing.T) {
	s := newCommitmentHTTPTestServer(t, CommitmentTokenModeStrict, "http-secret", nil)
	plan := testHTTPDAGPlan("ps-strict-valid")
	w0, _ := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	token := w0.Header().Get(HeaderCommitmentToken)
	w, resp := sendCommitmentHTTPRequest(t, s, plan.SessionID, plan.Steps[1].ToolName, nil, 1, token)
	if resp.Error != nil {
		t.Fatalf("step1 error: %+v", resp.Error)
	}
	if got := w.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusValidated) {
		t.Fatalf("status=%q, want validated", got)
	}
}

func TestCommitmentTokenMultiGatewaySharedStoreValidates(t *testing.T) {
	store := NewInMemorySessionStateStore(0)
	a := newCommitmentHTTPTestServer(t, CommitmentTokenModeStrict, "shared-secret", store)
	b := newCommitmentHTTPTestServer(t, CommitmentTokenModeStrict, "shared-secret", store)
	a.SetNodeID("gw-a")
	b.SetNodeID("gw-b")

	plan := testHTTPDAGPlan("ps-multigateway-token")
	w0, resp0 := sendCommitmentHTTPRequest(t, a, plan.SessionID, plan.Steps[0].ToolName, plan, 0, "")
	if resp0.Error != nil {
		t.Fatalf("step0 error: %+v", resp0.Error)
	}
	token := w0.Header().Get(HeaderCommitmentToken)
	if token == "" {
		t.Fatalf("missing issued token")
	}
	w1, resp1 := sendCommitmentHTTPRequest(t, b, plan.SessionID, plan.Steps[1].ToolName, nil, 1, token)
	if resp1.Error != nil {
		t.Fatalf("step1 on gateway B error: %+v", resp1.Error)
	}
	if got := w1.Header().Get(HeaderCommitmentStatus); got != string(CommitmentTokenStatusValidated) {
		t.Fatalf("status=%q, want validated", got)
	}
}

func newTestCommitmentManager(t *testing.T, secret string, ttl time.Duration) *CommitmentTokenManager {
	t.Helper()
	mgr, err := NewCommitmentTokenManager(CommitmentTokenConfig{
		Mode:   CommitmentTokenModeOptional,
		Secret: secret,
		TTL:    ttl,
	})
	if err != nil {
		t.Fatalf("NewCommitmentTokenManager: %v", err)
	}
	return mgr
}

func testCommitmentClaims(t *testing.T) CommitmentTokenClaims {
	t.Helper()
	plan := testHTTPDAGPlan("session-a")
	planHash, err := hashHTTPDAGPlan(plan)
	if err != nil {
		t.Fatalf("hash plan: %v", err)
	}
	priceHash, err := hashLockedPrices(map[string]int64{"calculate": 10, "web_fetch": 20})
	if err != nil {
		t.Fatalf("hash prices: %v", err)
	}
	return CommitmentTokenClaims{
		SessionID:       plan.SessionID,
		PlanHash:        planHash,
		PriceHash:       priceHash,
		Budget:          plan.Budget,
		TotalCost:       30,
		TotalSteps:      len(plan.Steps),
		NodeID:          "gw-a",
		StateStore:      "local",
		RecoveryEnabled: false,
	}
}

func testCommitmentContext(claims CommitmentTokenClaims) CommitmentTokenValidationContext {
	return CommitmentTokenValidationContext{
		SessionID:  claims.SessionID,
		PlanHash:   claims.PlanHash,
		PriceHash:  claims.PriceHash,
		TotalCost:  claims.TotalCost,
		TotalSteps: claims.TotalSteps,
	}
}

func testAmendedCommitmentClaims(t *testing.T) CommitmentTokenClaims {
	t.Helper()
	claims := testCommitmentClaims(t)
	claims.Version = commitmentTokenVersionV2
	claims.Type = commitmentTokenTypeAmendedPS
	claims.AmendmentVersion = 1
	claims.AmendmentID = "amend-1"
	claims.ParentCommitmentHash = "parent-commitment-hash"
	claims.DeltaHash = "delta-hash"
	claims.AmendmentChainHash = "amendment-chain-hash"
	claims.CheckpointHash = "checkpoint-hash"
	claims.BaseStep = 2
	return claims
}

func testAmendedCommitmentContext(claims CommitmentTokenClaims) AmendedCommitmentValidationContext {
	return AmendedCommitmentValidationContext{
		CommitmentTokenValidationContext: CommitmentTokenValidationContext{
			SessionID:  claims.SessionID,
			PlanHash:   claims.PlanHash,
			PriceHash:  claims.PriceHash,
			TotalCost:  claims.TotalCost,
			TotalSteps: claims.TotalSteps,
		},
		ParentCommitmentHash: claims.ParentCommitmentHash,
		DeltaHash:            claims.DeltaHash,
		AmendmentChainHash:   claims.AmendmentChainHash,
		CheckpointHash:       claims.CheckpointHash,
		AmendmentVersion:     claims.AmendmentVersion,
		BaseStep:             claims.BaseStep,
	}
}

func signCommitmentClaimsForTest(t *testing.T, mgr *CommitmentTokenManager, claims CommitmentTokenClaims) string {
	t.Helper()
	if claims.IssuedAt == 0 {
		claims.IssuedAt = time.Now().Unix()
	}
	if claims.ExpiresAt == 0 {
		claims.ExpiresAt = time.Now().Add(time.Minute).Unix()
	}
	if claims.PolicyVersion == "" {
		claims.PolicyVersion = defaultPolicyVersion
	}
	headerJSON, err := json.Marshal(commitmentTokenHeader{
		Alg: "HS256",
		Typ: "plangate.commitment",
		V:   claims.Version,
	})
	if err != nil {
		t.Fatalf("marshal header: %v", err)
	}
	payloadJSON, err := json.Marshal(claims)
	if err != nil {
		t.Fatalf("marshal claims: %v", err)
	}
	headerPart := base64.RawURLEncoding.EncodeToString(headerJSON)
	payloadPart := base64.RawURLEncoding.EncodeToString(payloadJSON)
	signedPart := headerPart + "." + payloadPart
	return signedPart + "." + base64.RawURLEncoding.EncodeToString(mgr.sign([]byte(signedPart)))
}

func newCommitmentHTTPTestServer(t *testing.T, mode CommitmentTokenMode, secret string, store SessionStateStore) *MCPDPServer {
	t.Helper()
	gov := makeTestGovernor()
	s := NewMCPDPServer("commitment-http-test", gov, time.Minute, 0, 0.5)
	if err := s.SetCommitmentTokenConfig(CommitmentTokenConfig{
		Mode:   mode,
		Secret: secret,
		TTL:    time.Minute,
	}); err != nil {
		t.Fatalf("SetCommitmentTokenConfig: %v", err)
	}
	if store != nil {
		s.SetSharedStateStore(store)
	}
	handler := func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		return &mcpgov.MCPToolCallResult{
			Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}},
		}, nil
	}
	for _, name := range []string{"calculate", "web_fetch", "mock_heavy"} {
		s.RegisterTool(mcpgov.MCPTool{Name: name, Description: "test tool " + name}, handler)
	}
	return s
}

func testHTTPDAGPlan(sessionID string) *HTTPDAGPlan {
	return &HTTPDAGPlan{
		SessionID: sessionID,
		Budget:    1000,
		Steps: []HTTPDAGStep{
			{StepID: "s1", ToolName: "calculate"},
			{StepID: "s2", ToolName: "web_fetch", DependsOn: []string{"s1"}},
		},
	}
}

func sendCommitmentHTTPRequest(
	t *testing.T, s *MCPDPServer, sessionID string, toolName string, plan *HTTPDAGPlan, step int, token string,
) (*httptest.ResponseRecorder, mcpgov.JSONRPCResponse) {
	t.Helper()
	params, _ := json.Marshal(map[string]interface{}{
		"name":      toolName,
		"arguments": map[string]interface{}{"q": "test"},
		"_meta":     map[string]interface{}{"tokens": 1000},
	})
	body, _ := json.Marshal(mcpgov.JSONRPCRequest{
		JSONRPC: mcpgov.JSONRPCVersion,
		ID:      "req",
		Method:  mcpgov.MethodToolsCall,
		Params:  params,
	})
	req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderSessionID, sessionID)
	req.Header.Set(HeaderSessionStep, strconv.Itoa(step))
	if token != "" {
		req.Header.Set(HeaderCommitmentToken, token)
	}
	if plan != nil {
		dagJSON, _ := json.Marshal(plan)
		req.Header.Set(HeaderPlanDAG, string(dagJSON))
		req.Header.Set(HeaderTotalBudget, "1000")
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, req)
	var resp mcpgov.JSONRPCResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response %q: %v", w.Body.String(), err)
	}
	return w, resp
}
