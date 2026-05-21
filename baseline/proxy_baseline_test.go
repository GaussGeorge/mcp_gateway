package baseline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

func doPostWithHeaders(handler http.Handler, body []byte, headers map[string]string, remote string) (int, mcpgov.JSONRPCResponse) {
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	if remote != "" {
		req.RemoteAddr = remote
	}
	handler.ServeHTTP(rr, req)

	var resp mcpgov.JSONRPCResponse
	_ = json.Unmarshal(rr.Body.Bytes(), &resp)
	return rr.Code, resp
}

func TestKongApprox_ConsumerKeyPriority(t *testing.T) {
	gw := NewKongApproxGateway("kong-test", KongApproxConfig{
		GlobalQPS:    100,
		GlobalBurst:  100,
		SessionQPS:   0.001,
		SessionBurst: 1,
		SessionTTL:   5 * time.Minute,
	})
	defer gw.Close()

	gw.RegisterTool(mcpgov.MCPTool{Name: "calculate"}, mockHandler(0))

	body := makeToolCallRequest("calculate", 10)
	headers1 := map[string]string{"X-Session-ID": "sess-A"}
	headers2 := map[string]string{"X-Session-ID": "sess-A"}
	headers3 := map[string]string{}

	_, resp1 := doPostWithHeaders(gw, body, headers1, "10.0.0.8:1234")
	if resp1.Error != nil {
		t.Fatalf("first request should pass, got error: %+v", resp1.Error)
	}

	_, resp2 := doPostWithHeaders(gw, body, headers2, "10.0.0.8:1234")
	if resp2.Error == nil {
		t.Fatal("second request with same session should be quota-rejected")
	}
	if resp2.Error.Data == nil {
		t.Fatal("expected rejection reason in error.data")
	}

	// Different consumer key path (falls back to _meta.name/remote) should not be blocked by sess-A bucket.
	_, resp3 := doPostWithHeaders(gw, body, headers3, "10.0.0.9:5678")
	if resp3.Error != nil {
		t.Fatalf("different consumer key should pass, got error: %+v", resp3.Error)
	}
}

func TestEnvoyApprox_ReleaseInFlightOnErrorAndSuccess(t *testing.T) {
	gw := NewEnvoyApproxGateway("envoy-test", EnvoyApproxConfig{
		GlobalQPS:     1000,
		GlobalBurst:   1000,
		GlobalMaxConc: 1,
		RouteQPS:      1000,
		RouteBurst:    1000,
		RouteMaxConc:  1,
	})

	var calls int64
	gw.RegisterTool(mcpgov.MCPTool{Name: "mock_heavy"}, func(ctx context.Context, p mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		time.Sleep(30 * time.Millisecond)
		if atomic.AddInt64(&calls, 1) == 1 {
			return nil, errors.New("boom")
		}
		return &mcpgov.MCPToolCallResult{Content: []mcpgov.ContentBlock{mcpgov.TextContent("ok")}}, nil
	})

	body := makeToolCallRequest("mock_heavy", 10)

	_, _ = doPost(gw, body)
	_, _ = doPost(gw, body)

	g, r := gw.GetInFlight("mock_heavy")
	if g != 0 || r != 0 {
		t.Fatalf("in-flight counters should be fully released, got global=%d route=%d", g, r)
	}
}

func TestProxyApprox_NoDAGReservation_MidSessionCanReject(t *testing.T) {
	gw := NewKongApproxGateway("kong-dag-test", KongApproxConfig{
		GlobalQPS:    100,
		GlobalBurst:  100,
		SessionQPS:   0.001,
		SessionBurst: 1,
		SessionTTL:   5 * time.Minute,
	})
	defer gw.Close()
	gw.RegisterTool(mcpgov.MCPTool{Name: "calculate"}, mockHandler(0))

	body := makeToolCallRequest("calculate", 10)
	headers := map[string]string{
		"X-Plan-DAG": `{"session_id":"sess-dag","steps":[{"id":"s1"},{"id":"s2"},{"id":"s3"},{"id":"s4"}]}`,
	}

	_, resp1 := doPostWithHeaders(gw, body, headers, "127.0.0.1:1234")
	if resp1.Error != nil {
		t.Fatalf("first step should pass, got error: %+v", resp1.Error)
	}

	// Same session can still be rejected later; no future-step reservation is performed.
	_, resp2 := doPostWithHeaders(gw, body, headers, "127.0.0.1:1234")
	if resp2.Error == nil {
		t.Fatal("second step should be rejectable (no reservation guarantee)")
	}
	if resp2.Error.Data == nil {
		t.Fatal("expected reject reason in error.data")
	}
}
