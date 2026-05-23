package plangate

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"
)

const (
	commitmentTokenVersionV1 = 1
	commitmentTokenVersionV2 = 2

	commitmentTokenTypePS        = "ps_commitment"
	commitmentTokenTypeAmendedPS = "ps_amended_commitment"

	defaultPolicyVersion = "plangate-v1"
)

// CommitmentTokenConfig controls PlanGate Commitment Token issuance and checks.
type CommitmentTokenConfig struct {
	Mode   CommitmentTokenMode
	Secret string
	TTL    time.Duration
}

// CommitmentTokenManager issues and validates signed P&S commitment tokens.
type CommitmentTokenManager struct {
	mode   CommitmentTokenMode
	secret []byte
	ttl    time.Duration
}

type commitmentTokenHeader struct {
	Alg string `json:"alg"`
	Typ string `json:"typ"`
	V   int    `json:"v"`
}

// CommitmentTokenClaims is the signed payload for a P&S budget reservation.
type CommitmentTokenClaims struct {
	Version              int    `json:"v"`
	Type                 string `json:"typ"`
	SessionID            string `json:"sid"`
	PlanHash             string `json:"plan_hash"`
	PriceHash            string `json:"price_hash"`
	Budget               int64  `json:"budget"`
	TotalCost            int64  `json:"total_cost"`
	TotalSteps           int    `json:"total_steps"`
	IssuedAt             int64  `json:"iat"`
	ExpiresAt            int64  `json:"exp"`
	PolicyVersion        string `json:"policy_version"`
	NodeID               string `json:"node_id"`
	StateStore           string `json:"state_store"`
	RecoveryEnabled      bool   `json:"recovery_enabled"`
	AmendmentVersion     int    `json:"amendment_version,omitempty"`
	AmendmentID          string `json:"amendment_id,omitempty"`
	ParentCommitmentHash string `json:"parent_commitment_hash,omitempty"`
	DeltaHash            string `json:"delta_hash,omitempty"`
	AmendmentChainHash   string `json:"amendment_chain_hash,omitempty"`
	CheckpointHash       string `json:"checkpoint_hash,omitempty"`
	BaseStep             int    `json:"base_step,omitempty"`
}

// CommitmentTokenValidationContext contains reservation facts that a token must
// match. PlanHash is optional for shared records created before this field
// existed; PriceHash is required whenever a token is presented.
type CommitmentTokenValidationContext struct {
	SessionID  string
	PlanHash   string
	PriceHash  string
	TotalCost  int64
	TotalSteps int
	Now        time.Time
}

// AmendedCommitmentValidationContext extends the common validation context with
// chain-bound fields used by v2 amended commitments.
type AmendedCommitmentValidationContext struct {
	CommitmentTokenValidationContext
	ParentCommitmentHash string
	DeltaHash            string
	AmendmentChainHash   string
	CheckpointHash       string
	AmendmentVersion     int
	BaseStep             int
}

func NewCommitmentTokenManager(cfg CommitmentTokenConfig) (*CommitmentTokenManager, error) {
	mode, err := normalizeCommitmentTokenMode(cfg.Mode)
	if err != nil {
		return nil, err
	}
	if cfg.TTL <= 0 {
		cfg.TTL = 60 * time.Second
	}
	secret := cfg.Secret
	if secret == "" {
		secret = os.Getenv("PLANGATE_COMMITMENT_SECRET")
	}
	if mode != CommitmentTokenModeOff && secret == "" {
		generated := make([]byte, 32)
		if _, err := rand.Read(generated); err != nil {
			return nil, fmt.Errorf("commitment token secret generation failed: %w", err)
		}
		secret = base64.RawURLEncoding.EncodeToString(generated)
	}
	return &CommitmentTokenManager{
		mode:   mode,
		secret: []byte(secret),
		ttl:    cfg.TTL,
	}, nil
}

func normalizeCommitmentTokenMode(mode CommitmentTokenMode) (CommitmentTokenMode, error) {
	if mode == "" {
		return CommitmentTokenModeOptional, nil
	}
	switch mode {
	case CommitmentTokenModeOff, CommitmentTokenModeOptional, CommitmentTokenModeStrict:
		return mode, nil
	default:
		return "", fmt.Errorf("invalid commitment token mode %q", mode)
	}
}

func (m *CommitmentTokenManager) Mode() CommitmentTokenMode {
	if m == nil {
		return CommitmentTokenModeOff
	}
	return m.mode
}

func (m *CommitmentTokenManager) TTL() time.Duration {
	if m == nil {
		return 0
	}
	return m.ttl
}

func (m *CommitmentTokenManager) Enabled() bool {
	return m != nil && m.mode != CommitmentTokenModeOff
}

func (m *CommitmentTokenManager) Issue(claims CommitmentTokenClaims) (string, error) {
	if m == nil || m.mode == CommitmentTokenModeOff {
		return "", errors.New("commitment token manager disabled")
	}
	now := time.Now()
	if err := normalizeCommitmentTokenClaimsForIssue(&claims); err != nil {
		return "", err
	}
	if claims.IssuedAt == 0 {
		claims.IssuedAt = now.Unix()
	}
	if claims.ExpiresAt == 0 {
		claims.ExpiresAt = now.Add(m.ttl).Unix()
	}
	if claims.PolicyVersion == "" {
		claims.PolicyVersion = defaultPolicyVersion
	}

	header := commitmentTokenHeader{Alg: "HS256", Typ: "plangate.commitment", V: claims.Version}
	headerJSON, err := json.Marshal(header)
	if err != nil {
		return "", err
	}
	payloadJSON, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	headerPart := base64.RawURLEncoding.EncodeToString(headerJSON)
	payloadPart := base64.RawURLEncoding.EncodeToString(payloadJSON)
	signedPart := headerPart + "." + payloadPart
	sig := m.sign([]byte(signedPart))
	return signedPart + "." + base64.RawURLEncoding.EncodeToString(sig), nil
}

func (m *CommitmentTokenManager) IssueInitialCommitment(claims CommitmentTokenClaims) (string, error) {
	zeroAmendmentCommitmentFields(&claims)
	claims.Version = commitmentTokenVersionV1
	claims.Type = commitmentTokenTypePS
	return m.Issue(claims)
}

func (m *CommitmentTokenManager) IssueAmendedCommitment(claims CommitmentTokenClaims) (string, error) {
	claims.Version = commitmentTokenVersionV2
	claims.Type = commitmentTokenTypeAmendedPS
	return m.Issue(claims)
}

func normalizeCommitmentTokenClaimsForIssue(claims *CommitmentTokenClaims) error {
	if claims == nil {
		return errors.New("commitment token claims are nil")
	}

	if claims.Version == 0 {
		switch claims.Type {
		case "", commitmentTokenTypePS:
			claims.Version = commitmentTokenVersionV1
		case commitmentTokenTypeAmendedPS:
			claims.Version = commitmentTokenVersionV2
		default:
			return fmt.Errorf("invalid commitment token type %q", claims.Type)
		}
	}
	if claims.Type == "" {
		switch claims.Version {
		case commitmentTokenVersionV1:
			claims.Type = commitmentTokenTypePS
		case commitmentTokenVersionV2:
			claims.Type = commitmentTokenTypeAmendedPS
		default:
			return fmt.Errorf("invalid commitment token version %d", claims.Version)
		}
	}

	switch claims.Version {
	case commitmentTokenVersionV1:
		if claims.Type != commitmentTokenTypePS {
			return fmt.Errorf("v1 commitment token must use type %q", commitmentTokenTypePS)
		}
	case commitmentTokenVersionV2:
		if claims.Type != commitmentTokenTypeAmendedPS {
			return fmt.Errorf("v2 commitment token must use type %q", commitmentTokenTypeAmendedPS)
		}
		if claims.AmendmentVersion <= 0 {
			return errors.New("amended commitment missing amendment_version")
		}
		if claims.AmendmentID == "" {
			return errors.New("amended commitment missing amendment_id")
		}
		if claims.ParentCommitmentHash == "" {
			return errors.New("amended commitment missing parent_commitment_hash")
		}
		if claims.DeltaHash == "" {
			return errors.New("amended commitment missing delta_hash")
		}
		if claims.AmendmentChainHash == "" {
			return errors.New("amended commitment missing amendment_chain_hash")
		}
		if claims.CheckpointHash == "" {
			return errors.New("amended commitment missing checkpoint_hash")
		}
	default:
		return fmt.Errorf("invalid commitment token version %d", claims.Version)
	}

	return nil
}

func zeroAmendmentCommitmentFields(claims *CommitmentTokenClaims) {
	if claims == nil {
		return
	}
	claims.AmendmentVersion = 0
	claims.AmendmentID = ""
	claims.ParentCommitmentHash = ""
	claims.DeltaHash = ""
	claims.AmendmentChainHash = ""
	claims.CheckpointHash = ""
	claims.BaseStep = 0
}

func (m *CommitmentTokenManager) Validate(token string, ctx CommitmentTokenValidationContext) (*CommitmentTokenClaims, CommitmentTokenStatus, string) {
	if m == nil || m.mode == CommitmentTokenModeOff {
		return nil, CommitmentTokenStatusDisabled, ""
	}
	claims, status, reason := m.parseAndVerify(token)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}
	if claims.Version != commitmentTokenVersionV1 || claims.Type != commitmentTokenTypePS {
		return nil, CommitmentTokenStatusInvalid, "invalid token type"
	}
	status, reason = validateCommitmentCoreClaims(claims, ctx)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}
	return claims, CommitmentTokenStatusValidated, ""
}

func (m *CommitmentTokenManager) ValidateParentCommitmentForAmendment(
	token string,
	ctx AmendedCommitmentValidationContext,
) (*CommitmentTokenClaims, CommitmentTokenStatus, string) {
	if m == nil || m.mode == CommitmentTokenModeOff {
		return nil, CommitmentTokenStatusDisabled, ""
	}
	claims, status, reason := m.parseAndVerify(token)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}
	status, reason = validateCommitmentCoreClaims(claims, ctx.CommitmentTokenValidationContext)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}

	switch {
	case claims.Version == commitmentTokenVersionV1 && claims.Type == commitmentTokenTypePS:
		return claims, CommitmentTokenStatusValidated, ""
	case claims.Version == commitmentTokenVersionV2 && claims.Type == commitmentTokenTypeAmendedPS:
		status, reason = validateAmendedCommitmentClaims(claims, ctx)
		if status != CommitmentTokenStatusValidated {
			return nil, status, reason
		}
		if ctx.AmendmentVersion > 0 && claims.AmendmentVersion != ctx.AmendmentVersion {
			return nil, CommitmentTokenStatusMismatch, "amendment version mismatch"
		}
		if ctx.AmendmentChainHash != "" && claims.AmendmentChainHash != ctx.AmendmentChainHash {
			return nil, CommitmentTokenStatusMismatch, "amendment chain hash mismatch"
		}
		if ctx.CheckpointHash != "" && claims.CheckpointHash != ctx.CheckpointHash {
			return nil, CommitmentTokenStatusMismatch, "checkpoint hash mismatch"
		}
		return claims, CommitmentTokenStatusValidated, ""
	default:
		return nil, CommitmentTokenStatusInvalid, "invalid token type"
	}
}

func (m *CommitmentTokenManager) ValidateAmendedCommitment(
	token string,
	ctx AmendedCommitmentValidationContext,
) (*CommitmentTokenClaims, CommitmentTokenStatus, string) {
	if m == nil || m.mode == CommitmentTokenModeOff {
		return nil, CommitmentTokenStatusDisabled, ""
	}
	claims, status, reason := m.parseAndVerify(token)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}
	if claims.Version != commitmentTokenVersionV2 || claims.Type != commitmentTokenTypeAmendedPS {
		return nil, CommitmentTokenStatusInvalid, "invalid token type"
	}
	status, reason = validateCommitmentCoreClaims(claims, ctx.CommitmentTokenValidationContext)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}
	status, reason = validateAmendedCommitmentClaims(claims, ctx)
	if status != CommitmentTokenStatusValidated {
		return nil, status, reason
	}
	return claims, CommitmentTokenStatusValidated, ""
}

func validateCommitmentCoreClaims(
	claims *CommitmentTokenClaims,
	ctx CommitmentTokenValidationContext,
) (CommitmentTokenStatus, string) {
	if claims == nil {
		return CommitmentTokenStatusInvalid, "missing token claims"
	}
	now := ctx.Now
	if now.IsZero() {
		now = time.Now()
	}
	if claims.ExpiresAt <= now.Unix() {
		return CommitmentTokenStatusExpired, "token expired"
	}
	if claims.SessionID != ctx.SessionID {
		return CommitmentTokenStatusMismatch, "session id mismatch"
	}
	if claims.TotalCost != ctx.TotalCost {
		return CommitmentTokenStatusMismatch, "total cost mismatch"
	}
	if claims.TotalSteps != ctx.TotalSteps {
		return CommitmentTokenStatusMismatch, "total steps mismatch"
	}
	if ctx.PriceHash == "" {
		return CommitmentTokenStatusMismatch, "reservation price hash missing"
	}
	if claims.PriceHash != ctx.PriceHash {
		return CommitmentTokenStatusMismatch, "price hash mismatch"
	}
	if ctx.PlanHash != "" && claims.PlanHash != ctx.PlanHash {
		return CommitmentTokenStatusMismatch, "plan hash mismatch"
	}
	return CommitmentTokenStatusValidated, ""
}

func validateAmendedCommitmentClaims(
	claims *CommitmentTokenClaims,
	ctx AmendedCommitmentValidationContext,
) (CommitmentTokenStatus, string) {
	if claims.ParentCommitmentHash == "" {
		return CommitmentTokenStatusInvalid, "missing parent commitment hash"
	}
	if claims.DeltaHash == "" {
		return CommitmentTokenStatusInvalid, "missing delta hash"
	}
	if claims.AmendmentChainHash == "" {
		return CommitmentTokenStatusInvalid, "missing amendment chain hash"
	}
	if claims.CheckpointHash == "" {
		return CommitmentTokenStatusInvalid, "missing checkpoint hash"
	}
	if claims.AmendmentVersion <= 0 {
		return CommitmentTokenStatusInvalid, "missing amendment version"
	}
	if ctx.ParentCommitmentHash != "" && claims.ParentCommitmentHash != ctx.ParentCommitmentHash {
		return CommitmentTokenStatusMismatch, "parent commitment hash mismatch"
	}
	if ctx.DeltaHash != "" && claims.DeltaHash != ctx.DeltaHash {
		return CommitmentTokenStatusMismatch, "delta hash mismatch"
	}
	if ctx.AmendmentChainHash != "" && claims.AmendmentChainHash != ctx.AmendmentChainHash {
		return CommitmentTokenStatusMismatch, "amendment chain hash mismatch"
	}
	if ctx.CheckpointHash != "" && claims.CheckpointHash != ctx.CheckpointHash {
		return CommitmentTokenStatusMismatch, "checkpoint hash mismatch"
	}
	if ctx.AmendmentVersion > 0 && claims.AmendmentVersion != ctx.AmendmentVersion {
		return CommitmentTokenStatusMismatch, "amendment version mismatch"
	}
	if ctx.BaseStep > 0 && claims.BaseStep != ctx.BaseStep {
		return CommitmentTokenStatusMismatch, "base step mismatch"
	}
	return CommitmentTokenStatusValidated, ""
}

func (m *CommitmentTokenManager) parseAndVerify(token string) (*CommitmentTokenClaims, CommitmentTokenStatus, string) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] == "" || parts[1] == "" || parts[2] == "" {
		return nil, CommitmentTokenStatusInvalid, "malformed token"
	}
	signedPart := parts[0] + "." + parts[1]
	gotSig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return nil, CommitmentTokenStatusInvalid, "malformed signature"
	}
	wantSig := m.sign([]byte(signedPart))
	if !hmac.Equal(gotSig, wantSig) {
		return nil, CommitmentTokenStatusInvalid, "invalid signature"
	}
	headerJSON, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, CommitmentTokenStatusInvalid, "malformed header"
	}
	var header commitmentTokenHeader
	if err := json.Unmarshal(headerJSON, &header); err != nil {
		return nil, CommitmentTokenStatusInvalid, "malformed header"
	}
	if header.Alg != "HS256" {
		return nil, CommitmentTokenStatusInvalid, "invalid token algorithm"
	}
	payloadJSON, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, CommitmentTokenStatusInvalid, "malformed payload"
	}
	var claims CommitmentTokenClaims
	if err := json.Unmarshal(payloadJSON, &claims); err != nil {
		return nil, CommitmentTokenStatusInvalid, "malformed payload"
	}
	if header.V != claims.Version {
		return nil, CommitmentTokenStatusInvalid, "header/payload version mismatch"
	}
	return &claims, CommitmentTokenStatusValidated, ""
}

func (m *CommitmentTokenManager) sign(data []byte) []byte {
	mac := hmac.New(sha256.New, m.secret)
	_, _ = mac.Write(data)
	return mac.Sum(nil)
}

func commitmentTokenHash(token string) string {
	sum := sha256.Sum256([]byte(token))
	return base64.RawURLEncoding.EncodeToString(sum[:])
}

func commitmentTokenDigest(token string) string {
	digest := commitmentTokenHash(token)
	if len(digest) <= 8 {
		return digest
	}
	return digest[:8]
}

func hashHTTPDAGPlan(plan *HTTPDAGPlan) (string, error) {
	data, err := json.Marshal(plan)
	if err != nil {
		return "", err
	}
	return sha256Base64URL(data), nil
}

func hashLockedPrices(prices map[string]int64) (string, error) {
	keys := make([]string, 0, len(prices))
	for k := range prices {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	pairs := make([]struct {
		Tool  string `json:"tool"`
		Price int64  `json:"price"`
	}, 0, len(keys))
	for _, k := range keys {
		pairs = append(pairs, struct {
			Tool  string `json:"tool"`
			Price int64  `json:"price"`
		}{Tool: k, Price: prices[k]})
	}
	data, err := json.Marshal(pairs)
	if err != nil {
		return "", err
	}
	return sha256Base64URL(data), nil
}

func sha256Base64URL(data []byte) string {
	sum := sha256.Sum256(data)
	return base64.RawURLEncoding.EncodeToString(sum[:])
}
