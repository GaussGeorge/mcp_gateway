package baseline

import (
	"encoding/json"
	"net"
	"net/http"
	"strings"

	mcpgov "mcp-governance"
)

func remoteHost(remoteAddr string) string {
	host, _, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		return remoteAddr
	}
	return host
}

// extractSessionIDFromPlanDAG only reads session_id; it does not inspect steps.
func extractSessionIDFromPlanDAG(raw string) string {
	if strings.TrimSpace(raw) == "" {
		return ""
	}
	var v struct {
		SessionID string `json:"session_id"`
	}
	if err := json.Unmarshal([]byte(raw), &v); err != nil {
		return ""
	}
	return v.SessionID
}

func buildReqDesc(r *http.Request, params *mcpgov.MCPToolCallParams) ReqDesc {
	sessionID := r.Header.Get("X-Session-ID")
	if sessionID == "" {
		sessionID = extractSessionIDFromPlanDAG(r.Header.Get("X-Plan-DAG"))
	}

	consumerKey := sessionID
	if consumerKey == "" && params != nil && params.Meta != nil && params.Meta.Name != "" {
		consumerKey = params.Meta.Name
	}
	if consumerKey == "" {
		consumerKey = remoteHost(r.RemoteAddr)
	}

	tokens := int64(0)
	route := ""
	if params != nil {
		route = params.Name
		if params.Meta != nil {
			tokens = params.Meta.Tokens
		}
	}

	headers := map[string]string{
		"x-session-id": r.Header.Get("X-Session-ID"),
		"x-plan-dag":   r.Header.Get("X-Plan-DAG"),
	}

	return ReqDesc{
		SessionID:   sessionID,
		ConsumerKey: consumerKey,
		Route:       route,
		Method:      r.Method,
		RemoteAddr:  remoteHost(r.RemoteAddr),
		Tokens:      tokens,
		Headers:     headers,
	}
}
