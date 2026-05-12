package plangate

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	mcpgov "mcp-governance"
)

// ═══════════════════════════════════════════════════════════════════════
// P0-2: 网关开销基准测试 (System Overhead Benchmark)
//
// 评审要求: 报告 PlanGate 引入的额外延迟（μs级）和资源消耗
// 测量维度: DAG验证、价格计算、会话查找、完整准入链路、HTTP处理
// ═══════════════════════════════════════════════════════════════════════

// ── DAG 验证开销 ──

func BenchmarkDAGValidation_5Steps(b *testing.B) {
	plan := makeDAGPlan(5)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = validateHTTPDAG(plan)
	}
}

func BenchmarkDAGValidation_10Steps(b *testing.B) {
	plan := makeDAGPlan(10)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = validateHTTPDAG(plan)
	}
}

func BenchmarkDAGValidation_20Steps(b *testing.B) {
	plan := makeDAGPlan(20)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = validateHTTPDAG(plan)
	}
}

// ── 价格计算开销 ──

func BenchmarkPriceComputation_Quadratic(b *testing.B) {
	for i := 0; i < b.N; i++ {
		QuadraticDiscount(float64(i%10), 0.5)
	}
}

func BenchmarkPriceComputation_SunkCostFull(b *testing.B) {
	// 完整沉没成本定价计算（含强度调制）
	K := 5.0
	alpha := 0.5
	intensity := 0.7
	basePrice := 40.0
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		effectiveAlpha := alpha * (2.0 - intensity)
		discountFactor := QuadraticDiscount(K, effectiveAlpha)
		_ = int64(basePrice * intensity * discountFactor)
	}
}

// ── 会话查找开销 ──

func BenchmarkSessionLookup_BudgetReservation(b *testing.B) {
	mgr := NewHTTPBudgetReservationManager(5 * time.Minute)
	gov := makeTestGovernor()
	// 预填充 1000 个会话
	for i := 0; i < 1000; i++ {
		plan := &HTTPDAGPlan{
			SessionID: fmt.Sprintf("session-%d", i),
			Steps:     []HTTPDAGStep{{StepID: "s1", ToolName: "calculate"}},
			Budget:    1000,
		}
		mgr.Reserve(gov, plan, 100)
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		mgr.Get(fmt.Sprintf("session-%d", i%1000))
	}
}

func BenchmarkSessionLookup_ReactSession(b *testing.B) {
	mgr := NewReactSessionManager(5 * time.Minute)
	// 预填充 1000 个会话
	for i := 0; i < 1000; i++ {
		mgr.Create(fmt.Sprintf("session-%d", i), nil)
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		mgr.Get(fmt.Sprintf("session-%d", i%1000))
	}
}

// ── 完整准入链路开销（含 JSON 解析、DAG 验证、价格计算） ──

func BenchmarkFullAdmission_PlanAndSolve(b *testing.B) {
	server := makeTestServer(30)
	dagJSON := makeDAGJSON(5, 500)
	rpcReq := makeToolCallRPC("calculate")

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		sessionID := fmt.Sprintf("bench-ps-%d", i)
		req := httptest.NewRequest(http.MethodPost, "/", nil)
		req.Header.Set(HeaderPlanDAG, dagJSON)
		req.Header.Set(HeaderSessionID, sessionID)
		req.Header.Set(HeaderTotalBudget, "500")
		_ = server.handlePlanAndSolveFirstStep(
			context.Background(), req, rpcReq, dagJSON, sessionID,
		)
	}
}

func BenchmarkFullAdmission_ReActFirstStep(b *testing.B) {
	server := makeTestServer(30)
	rpcReq := makeToolCallRPC("calculate")

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		sessionID := fmt.Sprintf("bench-react-%d", i)
		_ = server.handleReActFirstStep(
			context.Background(), rpcReq, sessionID,
		)
	}
}

// ── HTTP 端到端开销（完整 ServeHTTP 链路） ──

func BenchmarkHTTPOverhead_Initialize(b *testing.B) {
	server := makeTestServer(30)
	body := `{"jsonrpc":"2.0","id":"bench","method":"initialize","params":{}}`

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest(http.MethodPost, "/",
			jsonReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		server.ServeHTTP(w, req)
	}
}

func BenchmarkHTTPOverhead_ToolsList(b *testing.B) {
	server := makeTestServer(30)
	body := `{"jsonrpc":"2.0","id":"bench","method":"tools/list","params":{}}`

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest(http.MethodPost, "/",
			jsonReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		server.ServeHTTP(w, req)
	}
}

func BenchmarkHTTPOverhead_ToolsCall_ReAct(b *testing.B) {
	server := makeTestServer(1000) // 大容量避免准入瓶颈
	paramBytes, _ := json.Marshal(map[string]interface{}{
		"name":      "calculate",
		"arguments": map[string]interface{}{"operation": "add", "a": 1, "b": 2},
	})
	rpcBody, _ := json.Marshal(map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      "bench",
		"method":  "tools/call",
		"params":  json.RawMessage(paramBytes),
	})

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest(http.MethodPost, "/",
			bytesReader(rpcBody))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set(HeaderSessionID, fmt.Sprintf("bench-%d", i))
		w := httptest.NewRecorder()
		server.ServeHTTP(w, req)
	}
}

func BenchmarkHTTPOverhead_Ping(b *testing.B) {
	server := makeTestServer(30)
	body := `{"jsonrpc":"2.0","id":"bench","method":"ping"}`

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest(http.MethodPost, "/",
			jsonReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		server.ServeHTTP(w, req)
	}
}

// ── 并发查找性能 ──

func BenchmarkConcurrentSessionLookup(b *testing.B) {
	mgr := NewReactSessionManager(5 * time.Minute)
	for i := 0; i < 10000; i++ {
		mgr.Create(fmt.Sprintf("session-%d", i), nil)
	}
	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		i := 0
		for pb.Next() {
			mgr.Get(fmt.Sprintf("session-%d", i%10000))
			i++
		}
	})
}

func BenchmarkConcurrentBudgetReservationLookup(b *testing.B) {
	mgr := NewHTTPBudgetReservationManager(5 * time.Minute)
	gov := makeTestGovernor()
	for i := 0; i < 10000; i++ {
		plan := &HTTPDAGPlan{
			SessionID: fmt.Sprintf("session-%d", i),
			Steps:     []HTTPDAGStep{{StepID: "s1", ToolName: "calculate"}},
			Budget:    1000,
		}
		mgr.Reserve(gov, plan, 100)
	}
	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		i := 0
		for pb.Next() {
			mgr.Get(fmt.Sprintf("session-%d", i%10000))
			i++
		}
	})
}

// ── 治理强度计算开销 ──

func BenchmarkGovernanceIntensity(b *testing.B) {
	gov := makeTestGovernor()
	tracker := NewGovernanceIntensityTracker(gov, 200)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = tracker.GetIntensity()
	}
}

// ═══════════════════════════ 辅助函数 ═══════════════════════════

func makeTestGovernor() *mcpgov.MCPGovernor {
	callMap := map[string][]string{
		"calculate": {},
		"web_fetch": {},
		"mock_heavy": {},
	}
	opts := map[string]interface{}{
		"initprice":    int64(0),
		"rateLimiting": false,
		"loadShedding": true,
		"maxToken":     int64(20),
		"toolWeights":  map[string]int64{"mock_heavy": 5},
	}
	return mcpgov.NewMCPGovernor("bench-gov", callMap, opts)
}

func makeTestServer(maxSessions int) *MCPDPServer {
	gov := makeTestGovernor()
	server := NewMCPDPServer("bench-server", gov, 60*time.Second, maxSessions, 0.5)
	// 注册 mock handler
	mockHandler := func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		return &mcpgov.MCPToolCallResult{
			Content: []mcpgov.ContentBlock{{Type: "text", Text: "ok"}},
		}, nil
	}
	for _, name := range []string{"calculate", "web_fetch", "mock_heavy"} {
		server.RegisterTool(mcpgov.MCPTool{
			Name:        name,
			Description: "bench tool " + name,
		}, mockHandler)
	}
	return server
}

func makeDAGPlan(steps int) *HTTPDAGPlan {
	plan := &HTTPDAGPlan{
		SessionID: "bench-session",
		Budget:    1000,
	}
	for i := 0; i < steps; i++ {
		step := HTTPDAGStep{
			StepID:   fmt.Sprintf("step_%d", i),
			ToolName: "calculate",
		}
		if i > 0 {
			step.DependsOn = []string{fmt.Sprintf("step_%d", i-1)}
		}
		plan.Steps = append(plan.Steps, step)
	}
	return plan
}

func makeDAGJSON(steps int, budget int64) string {
	plan := makeDAGPlan(steps)
	plan.Budget = budget
	data, _ := json.Marshal(plan)
	return string(data)
}

func makeToolCallRPC(toolName string) *mcpgov.JSONRPCRequest {
	params, _ := json.Marshal(map[string]interface{}{
		"name":      toolName,
		"arguments": map[string]interface{}{"operation": "add", "a": 1, "b": 2},
		"_meta":     map[string]interface{}{"tokens": 500},
	})
	return &mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "bench",
		Method:  "tools/call",
		Params:  params,
	}
}

func jsonReader(s string) *bytesReader_ {
	return &bytesReader_{data: []byte(s), pos: 0}
}

func bytesReader(b []byte) *bytesReader_ {
	return &bytesReader_{data: b, pos: 0}
}

// bytesReader_ 可重置的字节读取器（避免 benchmark 中 strings.Reader 分配）
type bytesReader_ struct {
	data []byte
	pos  int
}

func (r *bytesReader_) Read(p []byte) (int, error) {
	if r.pos >= len(r.data) {
		return 0, fmt.Errorf("EOF")
	}
	n := copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}
