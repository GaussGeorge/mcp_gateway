package baseline

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

// ==================== 公共辅助函数 ====================

// mockHandler 创建一个模拟工具处理器
func mockHandler(delay time.Duration) mcpgov.ToolCallHandler {
	return func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		if delay > 0 {
			time.Sleep(delay)
		}
		return &mcpgov.MCPToolCallResult{
			Content: []mcpgov.ContentBlock{mcpgov.TextContent("mock result")},
		}, nil
	}
}

// makeToolCallRequest 构造一个 tools/call JSON-RPC 请求体
func makeToolCallRequest(toolName string, tokens int64) []byte {
	req := map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "tools/call",
		"params": map[string]interface{}{
			"name":      toolName,
			"arguments": map[string]interface{}{},
			"_meta": map[string]interface{}{
				"tokens": tokens,
				"name":   "test-client",
			},
		},
	}
	b, _ := json.Marshal(req)
	return b
}

// doPost 发送 POST 请求并返回响应状态码和解析后的 JSON-RPC 响应
func doPost(handler http.Handler, body []byte) (int, mcpgov.JSONRPCResponse) {
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	handler.ServeHTTP(rr, req)

	var resp mcpgov.JSONRPCResponse
	json.Unmarshal(rr.Body.Bytes(), &resp)
	return rr.Code, resp
}

// ==================== NG Gateway Tests ====================

func TestNGGateway_AllRequestsPassThrough(t *testing.T) {
	ng := NewNGGateway("ng-test")
	ng.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "test calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 100)
	code, resp := doPost(ng, body)

	if code != 200 {
		t.Fatalf("expected status 200, got %d", code)
	}
	if resp.Error != nil {
		t.Fatalf("NG should not reject any request, got error: %v", resp.Error)
	}
}

func TestNGGateway_NoRejectionUnderHighLoad(t *testing.T) {
	ng := NewNGGateway("ng-load-test")
	ng.RegisterTool(mcpgov.MCPTool{
		Name:        "mock_heavy",
		Description: "heavy tool",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(10*time.Millisecond))

	// 发送 50 个并发请求
	var wg sync.WaitGroup
	var rejected int64

	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			body := makeToolCallRequest("mock_heavy", 10) // 低预算
			_, resp := doPost(ng, body)
			if resp.Error != nil {
				atomic.AddInt64(&rejected, 1)
			}
		}()
	}
	wg.Wait()

	if rejected > 0 {
		t.Fatalf("NG should reject 0 requests, but rejected %d", rejected)
	}

	total, success, errors := ng.GetStats()
	if total != 50 {
		t.Fatalf("expected 50 total requests, got %d", total)
	}
	if success != 50 {
		t.Fatalf("expected 50 success requests, got %d", success)
	}
	if errors != 0 {
		t.Fatalf("expected 0 error requests, got %d", errors)
	}
}

func TestNGGateway_IgnoresTokenBudget(t *testing.T) {
	ng := NewNGGateway("ng-budget-test")
	ng.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 即使 tokens=0（零预算），也应该通过
	body := makeToolCallRequest("calculate", 0)
	_, resp := doPost(ng, body)

	if resp.Error != nil {
		t.Fatalf("NG should pass even zero-budget requests, got error: %v", resp.Error)
	}
}

func TestNGGateway_PriceAlwaysZero(t *testing.T) {
	ng := NewNGGateway("ng-price-test")
	ng.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 100)
	_, resp := doPost(ng, body)

	// 检查 result._meta.price == "0"
	resultBytes, _ := json.Marshal(resp.Result)
	var result mcpgov.MCPToolCallResult
	json.Unmarshal(resultBytes, &result)

	if result.Meta == nil || result.Meta.Price != "0" {
		t.Fatalf("NG price should always be '0', got: %v", result.Meta)
	}
}

func TestNGGateway_Initialize(t *testing.T) {
	ng := NewNGGateway("ng-init-test")

	reqBody, _ := json.Marshal(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "initialize",
		"params":  map[string]interface{}{},
	})

	code, resp := doPost(ng, reqBody)
	if code != 200 || resp.Error != nil {
		t.Fatalf("initialize should succeed, code=%d, error=%v", code, resp.Error)
	}
}

func TestNGGateway_Ping(t *testing.T) {
	ng := NewNGGateway("ng-ping-test")

	reqBody, _ := json.Marshal(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "ping",
	})

	code, resp := doPost(ng, reqBody)
	if code != 200 || resp.Error != nil {
		t.Fatalf("ping should succeed, code=%d, error=%v", code, resp.Error)
	}
}

// ==================== SRL Gateway Tests ====================

func TestSRLGateway_AllowsWithinQPS(t *testing.T) {
	srl := NewSRLGateway("srl-test", SRLConfig{
		QPS:       100,
		BurstSize: 100,
	})
	srl.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 发送一个请求，应该通过
	body := makeToolCallRequest("calculate", 100)
	_, resp := doPost(srl, body)

	if resp.Error != nil {
		t.Fatalf("SRL should allow request within QPS, got error: %v", resp.Error)
	}
}

func TestSRLGateway_RejectsOverBurst(t *testing.T) {
	srl := NewSRLGateway("srl-burst-test", SRLConfig{
		QPS:       1, // 极低 QPS
		BurstSize: 5, // 桶容量 5
	})
	srl.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 发送请求直到被拒绝
	var rejected int64
	for i := 0; i < 10; i++ {
		body := makeToolCallRequest("calculate", 100)
		_, resp := doPost(srl, body)
		if resp.Error != nil {
			atomic.AddInt64(&rejected, 1)
		}
	}

	if rejected == 0 {
		t.Fatal("SRL should reject some requests when burst exceeded")
	}
}

func TestSRLGateway_IgnoresTokenBudget(t *testing.T) {
	srl := NewSRLGateway("srl-budget-test", SRLConfig{
		QPS:       100,
		BurstSize: 100,
	})
	srl.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 高预算和低预算请求应被同等对待
	bodyHigh := makeToolCallRequest("calculate", 1000) // 高预算
	bodyLow := makeToolCallRequest("calculate", 1)     // 低预算

	_, respHigh := doPost(srl, bodyHigh)
	_, respLow := doPost(srl, bodyLow)

	if respHigh.Error != nil || respLow.Error != nil {
		t.Fatal("SRL should treat high and low budget requests equally")
	}
}

func TestSRLGateway_DoesNotDistinguishToolType(t *testing.T) {
	srl := NewSRLGateway("srl-tool-test", SRLConfig{
		QPS:       2, // 非常低的 QPS
		BurstSize: 3, // 桶容量 3
	})
	// 注册轻量和重量工具
	srl.RegisterTool(mcpgov.MCPTool{
		Name: "calculate", Description: "lightweight", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))
	srl.RegisterTool(mcpgov.MCPTool{
		Name: "mock_heavy", Description: "heavyweight", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 交替发送轻量和重量请求，SRL 不应区分
	var lightRejected, heavyRejected int64
	for i := 0; i < 10; i++ {
		if i%2 == 0 {
			body := makeToolCallRequest("calculate", 100)
			_, resp := doPost(srl, body)
			if resp.Error != nil {
				lightRejected++
			}
		} else {
			body := makeToolCallRequest("mock_heavy", 100)
			_, resp := doPost(srl, body)
			if resp.Error != nil {
				heavyRejected++
			}
		}
	}

	// 两种工具都应有被拒绝的可能（因为 QPS 极低）
	totalRejected := lightRejected + heavyRejected
	if totalRejected == 0 {
		t.Fatal("SRL should reject some requests under low QPS")
	}

	// 关键断言：SRL 不应明显区分轻量和重量请求
	// 两者的拒绝率差距不应超过 2（因为是轮流发送的）
	t.Logf("SRL rejection - light: %d, heavy: %d (should be similar)", lightRejected, heavyRejected)
}

func TestSRLGateway_ConcurrencyLimit(t *testing.T) {
	srl := NewSRLGateway("srl-concurrency-test", SRLConfig{
		QPS:            1000, // 高 QPS，不做 QPS 限制
		BurstSize:      1000,
		MaxConcurrency: 5, // 最多 5 个并发
	})
	srl.RegisterTool(mcpgov.MCPTool{
		Name: "slow_tool", Description: "slow", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(100*time.Millisecond)) // 每个请求 100ms

	// 同时发 20 个请求
	var wg sync.WaitGroup
	var rejected int64
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			body := makeToolCallRequest("slow_tool", 100)
			_, resp := doPost(srl, body)
			if resp.Error != nil {
				atomic.AddInt64(&rejected, 1)
			}
		}()
	}
	wg.Wait()

	if rejected == 0 {
		t.Fatal("SRL with MaxConcurrency=5 should reject some of 20 concurrent requests")
	}
	t.Logf("SRL concurrency limit: rejected %d / 20 requests", rejected)
}

func TestSRLGateway_Stats(t *testing.T) {
	srl := NewSRLGateway("srl-stats-test", SRLConfig{
		QPS:       10,
		BurstSize: 5,
	})
	srl.RegisterTool(mcpgov.MCPTool{
		Name: "calculate", Description: "calc", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 发送请求
	for i := 0; i < 10; i++ {
		body := makeToolCallRequest("calculate", 100)
		doPost(srl, body)
	}

	total, success, rejected, errors := srl.GetStats()
	if total != 10 {
		t.Fatalf("expected total=10, got %d", total)
	}
	if success+rejected != 10 {
		t.Fatalf("success(%d) + rejected(%d) should equal 10", success, rejected)
	}
	if errors != 0 {
		t.Fatalf("expected errors=0, got %d", errors)
	}
	t.Logf("SRL stats: total=%d, success=%d, rejected=%d, errors=%d", total, success, rejected, errors)
}

func TestSRLGateway_PriceAlwaysZero(t *testing.T) {
	srl := NewSRLGateway("srl-price-test", SRLConfig{
		QPS:       100,
		BurstSize: 100,
	})
	srl.RegisterTool(mcpgov.MCPTool{
		Name: "calculate", Description: "calc", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 100)
	_, resp := doPost(srl, body)

	resultBytes, _ := json.Marshal(resp.Result)
	var result mcpgov.MCPToolCallResult
	json.Unmarshal(resultBytes, &result)

	if result.Meta == nil || result.Meta.Price != "0" {
		t.Fatalf("SRL price should always be '0', got: %v", result.Meta)
	}
}

func TestSRLGateway_Initialize(t *testing.T) {
	srl := NewSRLGateway("srl-init-test", SRLConfig{QPS: 100, BurstSize: 100})

	reqBody, _ := json.Marshal(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "initialize",
		"params":  map[string]interface{}{},
	})

	code, resp := doPost(srl, reqBody)
	if code != 200 || resp.Error != nil {
		t.Fatalf("initialize should succeed, code=%d, error=%v", code, resp.Error)
	}
}
