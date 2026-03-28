// mcp_dp_innovations_test.go
// 第 1 步：端到端冒烟测试 — 验证 3 个核心创新点在网络层面生效
//
// Test 1: TestPreflightRejection  — 低预算 + 长链路 P&S → 第 0 步直接拒绝
// Test 2: TestBudgetLockUnderPriceSpike — 高预算 + 中间疯狂抬价 → 锁定价格通行
// Test 3: TestReActCompatibility  — 不带 X-Plan-DAG Header → 回退到带权重的原生逻辑
package mcpgov

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ==================== 测试辅助 ====================

// mockTool 创建一个简单的 mock 工具处理函数
func mockTool(name string) ToolCallHandler {
	return func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{
			Content: []ContentBlock{TextContent(fmt.Sprintf("[%s] executed", name))},
		}, nil
	}
}

// setupTestServer 创建测试用的 MCPDP 服务器
func setupTestServer(t *testing.T) (*MCPDPServer, *MCPGovernor, *httptest.Server) {
	t.Helper()
	callMap := map[string][]string{
		"calculator": {},
		"web_fetch":  {},
		"mock_heavy": {},
	}
	opts := map[string]interface{}{
		"initprice":             int64(0),
		"loadShedding":          true,
		"priceAggregation":      "maximal",
		"priceStep":             int64(10),
		"priceStrategy":         "step",
		"maxToken":              int64(1000),
		"enableAdaptiveProfile": false,
		"toolWeights": map[string]int64{
			"mock_heavy": 5, // mock_heavy 价格 = ownprice × 5
		},
	}

	gov := NewMCPGovernor("test-mcpdp", callMap, opts)
	dpServer := NewMCPDPServer("test-mcpdp", gov, 60*time.Second, 0)

	tools := []MCPTool{
		{Name: "calculator", Description: "calc"},
		{Name: "web_fetch", Description: "fetch"},
		{Name: "mock_heavy", Description: "heavy compute"},
	}
	for _, tool := range tools {
		dpServer.RegisterTool(tool, mockTool(tool.Name))
	}

	ts := httptest.NewServer(dpServer)
	t.Cleanup(ts.Close)
	return dpServer, gov, ts
}

// makeToolCallPayload 构建 tools/call JSON-RPC 请求体
func makeToolCallPayload(id interface{}, toolName string, tokens int64) []byte {
	payload := map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  "tools/call",
		"params": map[string]interface{}{
			"name":      toolName,
			"arguments": map[string]interface{}{},
			"_meta": map[string]interface{}{
				"tokens": tokens,
				"name":   "smoke-test-client",
			},
		},
	}
	b, _ := json.Marshal(payload)
	return b
}

// decodeRPCResponse 解码 JSON-RPC 响应
func decodeRPCResponse(t *testing.T, resp *http.Response) map[string]interface{} {
	t.Helper()
	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatalf("解码响应失败: %v", err)
	}
	return result
}

// ==================== 冒烟测试 1: 预检拦截 ====================

// TestPreflightRejection 验证创新点 1: Pre-flight Atomic Admission
//
// 场景: 发送一个 5 步 P&S 请求，预算远低于总价格
// 期望: 在第 0 步直接被拒绝，error.data.rejected_at == "step_0"
func TestPreflightRejection(t *testing.T) {
	_, gov, ts := setupTestServer(t)

	// 设置 ownprice = 100
	// 工具价格: calculator=100, web_fetch=100, mock_heavy=500 (weight=5)
	gov.SetOwnPrice(100)

	// 创建 5 步 DAG: c→w→h→c→w
	// 期望总价格 = 100 + 100 + 500 + 100 + 100 = 900 (+ gateway overhead)
	dag := HTTPDAGPlan{
		SessionID: "smoke-reject-001",
		Steps: []HTTPDAGStep{
			{StepID: "s1", ToolName: "calculator"},
			{StepID: "s2", ToolName: "web_fetch", DependsOn: []string{"s1"}},
			{StepID: "s3", ToolName: "mock_heavy", DependsOn: []string{"s2"}},
			{StepID: "s4", ToolName: "calculator", DependsOn: []string{"s3"}},
			{StepID: "s5", ToolName: "web_fetch", DependsOn: []string{"s4"}},
		},
		Budget: 50, // 极低预算
	}
	dagJSON, _ := json.Marshal(dag)

	// 发送请求
	body := makeToolCallPayload(1, "calculator", 50)
	req, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderPlanDAG, string(dagJSON))
	req.Header.Set(HeaderSessionID, "smoke-reject-001")
	req.Header.Set(HeaderTotalBudget, "50")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("请求失败: %v", err)
	}
	defer resp.Body.Close()

	result := decodeRPCResponse(t, resp)

	// 验证被拒绝
	errObj, ok := result["error"].(map[string]interface{})
	if !ok {
		t.Fatal("期望 Pre-flight 拒绝，但收到了成功响应")
	}
	if code := errObj["code"].(float64); int(code) != CodeOverloaded {
		t.Fatalf("期望错误码 %d，实际 %v", CodeOverloaded, code)
	}

	// 验证 rejected_at == "step_0"
	data, ok := errObj["data"].(map[string]interface{})
	if !ok {
		t.Fatal("期望 error.data 包含拒绝信息")
	}
	if data["rejected_at"] != "step_0" {
		t.Fatalf("期望 rejected_at=step_0, 实际=%v", data["rejected_at"])
	}
	if data["mode"] != "plan_and_solve" {
		t.Fatalf("期望 mode=plan_and_solve, 实际=%v", data["mode"])
	}

	t.Logf("✓ 预检拦截测试通过: %s", errObj["message"])
	t.Logf("  budget=%v, total_cost=%v, rejected_at=%v",
		data["budget"], data["total_cost"], data["rejected_at"])
}

// ==================== 冒烟测试 2: 预算锁 ====================

// TestBudgetLockUnderPriceSpike 验证创新点 2: Budget Reservation
//
// 场景:
//  1. 以 ownPrice=10 提交 3 步 P&S, 高预算准入并创建锁
//  2. 将 ownPrice 暴涨到 9999 (模拟突发拥塞)
//  3. 发送后续步骤请求 (使用 X-Session-ID)
//
// 期望: 后续步骤按锁定价格通过，而非被 9999 的实时价格拒绝
func TestBudgetLockUnderPriceSpike(t *testing.T) {
	dpServer, gov, ts := setupTestServer(t)

	// 初始低价
	gov.SetOwnPrice(10)

	// 3 步 DAG: calculator→web_fetch→calculator
	// 价格: 10 + 10 + 10 = 30
	dag := HTTPDAGPlan{
		SessionID: "smoke-lock-001",
		Steps: []HTTPDAGStep{
			{StepID: "s1", ToolName: "calculator"},
			{StepID: "s2", ToolName: "web_fetch", DependsOn: []string{"s1"}},
			{StepID: "s3", ToolName: "calculator", DependsOn: []string{"s2"}},
		},
		Budget: 1000,
	}
	dagJSON, _ := json.Marshal(dag)

	// === Step 1: 提交首步 (预检准入 + 锁定价格) ===
	body := makeToolCallPayload(1, "calculator", 1000)
	req, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderPlanDAG, string(dagJSON))
	req.Header.Set(HeaderSessionID, "smoke-lock-001")
	req.Header.Set(HeaderTotalBudget, "1000")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("首步请求失败: %v", err)
	}
	defer resp.Body.Close()

	result := decodeRPCResponse(t, resp)
	if result["error"] != nil {
		t.Fatalf("首步应该成功，但被拒绝: %v", result["error"])
	}

	// 验证锁定价格
	res, ok := dpServer.budgetMgr.Get("smoke-lock-001")
	if !ok {
		t.Fatal("预算锁创建失败")
	}
	t.Logf("✓ 锁定价格: calculator=%d, web_fetch=%d",
		res.LockedPrices["calculator"], res.LockedPrices["web_fetch"])

	// === Step 2: 疯狂抬价 → ownPrice = 9999 ===
	gov.SetOwnPrice(9999)
	t.Logf("  价格暴涨: ownPrice → 9999")

	// === Step 3: 发送后续步骤 (带 X-Session-ID) ===
	body2 := makeToolCallPayload(2, "web_fetch", 1000)
	req2, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body2))
	req2.Header.Set("Content-Type", "application/json")
	req2.Header.Set(HeaderSessionID, "smoke-lock-001")

	resp2, err := http.DefaultClient.Do(req2)
	if err != nil {
		t.Fatalf("后续步骤请求失败: %v", err)
	}
	defer resp2.Body.Close()

	result2 := decodeRPCResponse(t, resp2)

	// 验证后续步骤成功（使用锁定价格 10，而非实时价格 9999）
	if result2["error"] != nil {
		t.Fatalf("后续步骤应该按锁定价格通过，但被拒绝: %v", result2["error"])
	}

	// 验证响应中的价格是锁定价格
	resultData, ok := result2["result"].(map[string]interface{})
	if ok {
		meta, _ := resultData["_meta"].(map[string]interface{})
		if meta != nil {
			t.Logf("✓ 响应中的价格: %v (应为锁定价格 10，而非实时价格 9999)", meta["price"])
		}
	}

	// === Step 4: 第三步也应该通过 ===
	body3 := makeToolCallPayload(3, "calculator", 1000)
	req3, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body3))
	req3.Header.Set("Content-Type", "application/json")
	req3.Header.Set(HeaderSessionID, "smoke-lock-001")

	resp3, err := http.DefaultClient.Do(req3)
	if err != nil {
		t.Fatalf("第三步请求失败: %v", err)
	}
	defer resp3.Body.Close()

	result3 := decodeRPCResponse(t, resp3)
	if result3["error"] != nil {
		t.Fatalf("第三步应按锁定价格通过，但被拒绝: %v", result3["error"])
	}

	t.Logf("✓ 预算锁测试通过: 3 步均按锁定价格通行，实时价格 9999 未影响已准入会话")
}

// ==================== 冒烟测试 3: ReAct 兼容 ====================

// TestReActCompatibility 验证创新点 3: Dual-Mode Governance
//
// 场景: 不带 X-Plan-DAG Header，直接发送 tools/call 请求
// 期望: 回退到 MCPGovernor 的标准 token-price 机制 (带工具权重)
func TestReActCompatibility(t *testing.T) {
	_, gov, ts := setupTestServer(t)

	// 设置价格 = 10
	gov.SetOwnPrice(10)

	// === 测试 A: 高预算 ReAct 请求 → 通过 ===
	body := makeToolCallPayload(1, "calculator", 100)
	req, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	// 不带 X-Plan-DAG 和 X-Session-ID

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("ReAct 请求失败: %v", err)
	}
	defer resp.Body.Close()

	result := decodeRPCResponse(t, resp)
	if result["error"] != nil {
		t.Fatalf("高预算 ReAct 应该通过: %v", result["error"])
	}
	t.Log("✓ ReAct 高预算请求通过")

	// === 测试 B: 低预算 ReAct 请求 → 被标准 LoadShedding 拒绝 ===
	gov.SetOwnPrice(200) // 抬价到 200

	body2 := makeToolCallPayload(2, "calculator", 5)
	req2, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body2))
	req2.Header.Set("Content-Type", "application/json")

	resp2, err := http.DefaultClient.Do(req2)
	if err != nil {
		t.Fatalf("低预算 ReAct 请求失败: %v", err)
	}
	defer resp2.Body.Close()

	result2 := decodeRPCResponse(t, resp2)
	errObj, ok := result2["error"].(map[string]interface{})
	if !ok {
		t.Fatal("低预算 ReAct 应该被 LoadShedding 拒绝")
	}
	if code := errObj["code"].(float64); int(code) != CodeOverloaded {
		t.Fatalf("期望错误码 %d, 实际 %v", CodeOverloaded, code)
	}
	t.Logf("✓ ReAct 低预算请求被拒绝: %s", errObj["message"])

	// === 测试 C: 重量工具权重生效 (mock_heavy 价格 = ownprice × 5) ===
	gov.SetOwnPrice(10) // ownprice=10, mock_heavy effective=50

	body3 := makeToolCallPayload(3, "mock_heavy", 30)
	req3, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body3))
	req3.Header.Set("Content-Type", "application/json")

	resp3, err := http.DefaultClient.Do(req3)
	if err != nil {
		t.Fatalf("mock_heavy ReAct 请求失败: %v", err)
	}
	defer resp3.Body.Close()

	result3 := decodeRPCResponse(t, resp3)
	// tokens=30, effective_price=50 → 应该被拒绝
	errObj3, ok := result3["error"].(map[string]interface{})
	if !ok {
		t.Fatal("mock_heavy(tokens=30, price=50) 应该被权重机制拒绝")
	}
	t.Logf("✓ mock_heavy 工具权重生效 (tokens=30 < price=50): %s", errObj3["message"])

	// tokens=100 > effective_price=50 → 应该通过
	body4 := makeToolCallPayload(4, "mock_heavy", 100)
	req4, _ := http.NewRequest("POST", ts.URL, bytes.NewReader(body4))
	req4.Header.Set("Content-Type", "application/json")

	resp4, err := http.DefaultClient.Do(req4)
	if err != nil {
		t.Fatalf("mock_heavy 高预算请求失败: %v", err)
	}
	defer resp4.Body.Close()

	result4 := decodeRPCResponse(t, resp4)
	if result4["error"] != nil {
		t.Fatalf("mock_heavy(tokens=100, price=50) 应该通过: %v", result4["error"])
	}
	t.Log("✓ mock_heavy 高预算请求通过 (tokens=100 > price=50)")

	t.Log("✓ ReAct 兼容测试全部通过: 双模态网关正确回退到标准动态定价逻辑")
}
