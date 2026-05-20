package baseline

import (
	"encoding/json"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

// ==================== Rajomon Gateway Tests ====================

func TestRajomonGateway_AllowsWhenPriceIsZero(t *testing.T) {
	gw := NewRajomonGateway("rj-test", RajomonConfig{
		InitialPrice:    0, // 初始价格为 0
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "test calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 0) // 即使 tokens=0，price=0 也应通过
	code, resp := doPost(gw, body)

	if code != 200 {
		t.Fatalf("expected status 200, got %d", code)
	}
	if resp.Error != nil {
		t.Fatalf("Rajomon should allow when price=0, got error: %v", resp.Error)
	}
}

func TestRajomonGateway_AllowsHighTokens(t *testing.T) {
	gw := NewRajomonGateway("rj-high-test", RajomonConfig{
		InitialPrice:    50,
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 100) // tokens=100 > price=50
	_, resp := doPost(gw, body)

	if resp.Error != nil {
		t.Fatalf("Rajomon should allow when tokens >= price, got error: %v", resp.Error)
	}
}

func TestRajomonGateway_RejectsLowTokens(t *testing.T) {
	gw := NewRajomonGateway("rj-reject-test", RajomonConfig{
		InitialPrice:    100,
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calculator",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 10) // tokens=10 < price=100
	_, resp := doPost(gw, body)

	if resp.Error == nil {
		t.Fatal("Rajomon should reject when tokens < price")
	}
	if resp.Error.Code != -32001 {
		t.Fatalf("expected error code -32001, got %d", resp.Error.Code)
	}

	// 验证 error.data 中包含价格信息
	if dataMap, ok := resp.Error.Data.(map[string]interface{}); ok {
		if price, exists := dataMap["price"]; !exists || price == "" {
			t.Error("error.data should contain 'price'")
		}
	}
	t.Logf("✅ Rajomon correctly rejected low-token request")
}

func TestRajomonGateway_DoesNotDistinguishToolType(t *testing.T) {
	gw := NewRajomonGateway("rj-uniform-test", RajomonConfig{
		InitialPrice:    50,
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name: "calculate", Description: "lightweight", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))
	gw.RegisterTool(mcpgov.MCPTool{
		Name: "mock_heavy", Description: "heavyweight", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 同样的 tokens，轻量和重量工具应该一样被准入（Rajomon 不分工具类型）
	bodyLight := makeToolCallRequest("calculate", 100)
	bodyHeavy := makeToolCallRequest("mock_heavy", 100)

	_, respLight := doPost(gw, bodyLight)
	_, respHeavy := doPost(gw, bodyHeavy)

	if respLight.Error != nil || respHeavy.Error != nil {
		t.Fatal("Rajomon should treat light and heavy tools equally")
	}

	// 同样的低 tokens，两种工具应该一样被拒绝
	bodyLight2 := makeToolCallRequest("calculate", 10)
	bodyHeavy2 := makeToolCallRequest("mock_heavy", 10)

	_, respLight2 := doPost(gw, bodyLight2)
	_, respHeavy2 := doPost(gw, bodyHeavy2)

	if respLight2.Error == nil || respHeavy2.Error == nil {
		t.Fatal("Rajomon should reject both light and heavy tools equally when tokens < price")
	}
}

func TestRajomonGateway_PriceReturnsInMeta(t *testing.T) {
	gw := NewRajomonGateway("rj-meta-test", RajomonConfig{
		InitialPrice:    42,
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name:        "calculate",
		Description: "calc",
		InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	body := makeToolCallRequest("calculate", 100)
	_, resp := doPost(gw, body)

	resultBytes, _ := json.Marshal(resp.Result)
	var result mcpgov.MCPToolCallResult
	json.Unmarshal(resultBytes, &result)

	if result.Meta == nil || result.Meta.Price != "42" {
		t.Fatalf("Rajomon should return price=42 in _meta, got: %v", result.Meta)
	}
	t.Logf("✅ Rajomon price in _meta: %s", result.Meta.Price)
}

func TestRajomonGateway_ConcurrentLoadPartialReject(t *testing.T) {
	// 设置较高初始价格，部分请求给高 tokens 部分给低 tokens
	gw := NewRajomonGateway("rj-conc-test", RajomonConfig{
		InitialPrice:    50,
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name: "calculate", Description: "calc", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	var wg sync.WaitGroup
	var rejected int64

	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			var tokens int64
			if idx%2 == 0 {
				tokens = 100 // 高预算 → 通过
			} else {
				tokens = 10 // 低预算 → 拒绝
			}
			body := makeToolCallRequest("calculate", tokens)
			_, resp := doPost(gw, body)
			if resp.Error != nil {
				atomic.AddInt64(&rejected, 1)
			}
		}(i)
	}
	wg.Wait()

	if rejected != 10 {
		t.Logf("Rajomon concurrent: rejected %d/20 (expected ~10 low-budget)", rejected)
	}
	if rejected == 0 {
		t.Fatal("Rajomon should reject some low-budget requests")
	}
	t.Logf("✅ Rajomon concurrent rejection: %d/20", rejected)
}

func TestRajomonGateway_Initialize(t *testing.T) {
	gw := NewRajomonGateway("rj-init-test", RajomonConfig{InitialPrice: 0})

	reqBody, _ := json.Marshal(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "initialize",
		"params":  map[string]interface{}{},
	})

	code, resp := doPost(gw, reqBody)
	if code != 200 || resp.Error != nil {
		t.Fatalf("initialize should succeed, code=%d, error=%v", code, resp.Error)
	}
}

func TestRajomonGateway_Ping(t *testing.T) {
	gw := NewRajomonGateway("rj-ping-test", RajomonConfig{InitialPrice: 0})

	reqBody, _ := json.Marshal(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "ping",
	})

	code, resp := doPost(gw, reqBody)
	if code != 200 || resp.Error != nil {
		t.Fatalf("ping should succeed, code=%d, error=%v", code, resp.Error)
	}
}

func TestRajomonGateway_Stats(t *testing.T) {
	gw := NewRajomonGateway("rj-stats-test", RajomonConfig{
		InitialPrice:    20,
		PriceUpdateRate: 1 * time.Second,
	})
	gw.RegisterTool(mcpgov.MCPTool{
		Name: "calculate", Description: "calc", InputSchema: map[string]interface{}{"type": "object"},
	}, mockHandler(0))

	// 发 5 个高预算 + 5 个低预算
	for i := 0; i < 5; i++ {
		body := makeToolCallRequest("calculate", 100)
		doPost(gw, body)
	}
	for i := 0; i < 5; i++ {
		body := makeToolCallRequest("calculate", 1)
		doPost(gw, body)
	}

	total, success, rejected, errors := gw.GetStats()
	if total != 10 {
		t.Fatalf("expected total=10, got %d", total)
	}
	if success+rejected != 10 {
		t.Fatalf("success(%d) + rejected(%d) should equal 10", success, rejected)
	}
	if errors != 0 {
		t.Fatalf("expected errors=0, got %d", errors)
	}
	t.Logf("✅ Rajomon stats: total=%d, success=%d, rejected=%d, errors=%d",
		total, success, rejected, errors)
}

func TestRajomonGateway_GetPrice(t *testing.T) {
	gw := NewRajomonGateway("rj-price-test", RajomonConfig{
		InitialPrice:    99,
		PriceUpdateRate: 1 * time.Second,
	})

	if gw.GetPrice() != 99 {
		t.Fatalf("expected initial price=99, got %d", gw.GetPrice())
	}
}
