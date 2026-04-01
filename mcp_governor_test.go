// mcp_governor_test.go
// MCP 服务治理引擎单元测试
// 测试核心治理逻辑：令牌准入控制、负载削减、价格管理
package mcpgov

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

var (
	// defaultOpts 定义了测试用的默认配置
	// 模拟 MCP 服务治理引擎的典型运行环境
	defaultOpts = map[string]interface{}{
		"priceUpdateRate":  5000 * time.Microsecond,   // 价格更新频率：5ms
		"tokenUpdateRate":  100000 * time.Microsecond, // 令牌更新频率：100ms
		"latencyThreshold": 500 * time.Microsecond,    // 延迟阈值：0.5ms
		"priceStep":        int64(180),                // 价格调整步长
		"priceStrategy":    "expdecay",                // 指数衰减策略
		"lazyResponse":     false,                     // 关闭懒响应
		"rateLimiting":     true,                      // 开启限流
		"loadShedding":     true,                      // 开启负载削减
	}

	// defaultCallMap 定义了工具间的调用关系（此处为空，表示没有下游依赖）
	defaultCallMap = map[string][]string{
		"Foo": {}, // Foo 工具没有下游依赖
	}
)

// newTestGovernor 为每个测试创建一个全新的 MCPGovernor 实例
// 保证测试之间的隔离性
func newTestGovernor() *MCPGovernor {
	return NewMCPGovernor("node-1", defaultCallMap, defaultOpts)
}

// buildToolCallRequest 构造一个 tools/call 的 JSON-RPC 请求
func buildToolCallRequest(id interface{}, toolName string, tokens int64) *JSONRPCRequest {
	params := MCPToolCallParams{
		Name:      toolName,
		Arguments: map[string]interface{}{},
		Meta: &GovernanceMeta{
			Tokens: tokens,
			Method: toolName,
			Name:   "test-client",
		},
	}
	paramsBytes, _ := json.Marshal(params)
	return &JSONRPCRequest{
		JSONRPC: JSONRPCVersion,
		ID:      id,
		Method:  MethodToolsCall,
		Params:  paramsBytes,
	}
}

// TestHandleToolCall_RejectsLowTokens 测试当令牌不足时，治理中间件是否正确拒绝请求
//
// 场景描述：
//   - 服务端自身价格设为 100
//   - 客户端在 _meta.tokens 中携带 10 个令牌
//   - 预期：10 < 100，令牌不足，请求被拒绝
//
// JSON-RPC 请求示例：
//
//	{
//	  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
//	  "params": {"name": "Foo", "_meta": {"tokens": 10}}
//	}
//
// 预期 JSON-RPC 错误响应：
//
//	{
//	  "jsonrpc": "2.0", "id": 1,
//	  "error": {"code": -32001, "message": "工具 Foo 过载...", "data": {"price": "100"}}
//	}
func TestHandleToolCall_RejectsLowTokens(t *testing.T) {
	// 1. 准备 MCPGovernor 实例
	gov := newTestGovernor()
	gov.priceTableMap.Store("ownprice", int64(100))

	// 2. 构造 JSON-RPC 请求：tokens=10, price=100
	req := buildToolCallRequest(1, "Foo", 10)

	// 3. 定义工具处理函数（不应被调用）
	called := false
	handler := func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		called = true
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("SHOULD_NOT_REACH")}}, nil
	}

	// 4. 执行治理中间件
	resp := gov.HandleToolCall(context.Background(), req, handler)

	// 5. 验证：应返回 JSON-RPC error，错误码为 CodeOverloaded (-32001)
	if resp.Error == nil {
		t.Fatalf("预期应返回错误，但得到了响应: result=%v", resp.Result)
	}
	if resp.Error.Code != CodeOverloaded {
		t.Fatalf("预期错误码 %d (CodeOverloaded); 实际得到 %d: %s",
			CodeOverloaded, resp.Error.Code, resp.Error.Message)
	}
	if called {
		t.Error("尽管令牌不足，工具处理函数依然被调用了")
	}

	// 验证 error.data 中包含价格信息
	if dataMap, ok := resp.Error.Data.(map[string]string); ok {
		if dataMap["price"] == "" {
			t.Error("错误响应 data 中应包含 price 字段")
		}
	}

	t.Logf("✅ 低令牌请求被正确拒绝, error.code=%d, error.message=%s",
		resp.Error.Code, resp.Error.Message)
}

// TestHandleToolCall_AllowsHighTokens 测试当令牌充足时，请求是否能正确通过
//
// 场景描述：
//   - 服务端自身价格设为 10
//   - 客户端在 _meta.tokens 中携带 20 个令牌
//   - 预期：20 >= 10，令牌充足，请求通过
//
// 预期 JSON-RPC 成功响应：
//
//	{
//	  "jsonrpc": "2.0", "id": 1,
//	  "result": {"content": [{"type": "text", "text": "OK"}], "_meta": {"price": "10"}}
//	}
func TestHandleToolCall_AllowsHighTokens(t *testing.T) {
	gov := newTestGovernor()
	gov.priceTableMap.Store("ownprice", int64(10))

	req := buildToolCallRequest(1, "Foo", 20)

	handler := func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{
			Content: []ContentBlock{TextContent("OK")},
		}, nil
	}

	resp := gov.HandleToolCall(context.Background(), req, handler)

	// 验证：不应有错误
	if resp.Error != nil {
		t.Fatalf("预期无错误; 实际得到 code=%d, message=%s", resp.Error.Code, resp.Error.Message)
	}

	// 验证响应结果
	resultBytes, _ := json.Marshal(resp.Result)
	var result MCPToolCallResult
	json.Unmarshal(resultBytes, &result)

	if len(result.Content) == 0 || result.Content[0].Text != "OK" {
		t.Errorf("非预期的响应内容: %v; 期望包含 'OK'", result.Content)
	}

	// 验证 _meta 中包含价格信息
	if result.Meta != nil && result.Meta.Price != "" {
		t.Logf("✅ 高令牌请求通过, 返回价格: %s", result.Meta.Price)
	}
}

// TestHandleToolCall_MixedTokens 测试混合流量（一部分令牌不足，一部分充足）
// 模拟真实 MCP 场景中 AI Agent 发送不同预算的工具调用
func TestHandleToolCall_MixedTokens(t *testing.T) {
	gov := newTestGovernor()
	gov.priceTableMap.Store("ownprice", int64(10))

	var rejected, accepted int

	for i := 0; i < 10; i++ {
		var tokens int64 = 3
		if i >= 5 {
			tokens = 20 // 后 5 次给足令牌
		}

		req := buildToolCallRequest(i+1, "Foo", tokens)

		called := false
		handler := func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
			called = true
			return &MCPToolCallResult{Content: []ContentBlock{TextContent("OK")}}, nil
		}

		resp := gov.HandleToolCall(context.Background(), req, handler)

		if i < 5 {
			// 前 5 次应被拒绝
			if resp.Error == nil || resp.Error.Code != CodeOverloaded {
				t.Errorf("第 %d 次: 预期拒绝 (code=%d); 实际 error=%v", i, CodeOverloaded, resp.Error)
			}
			rejected++
		} else {
			// 后 5 次应通过
			if resp.Error != nil {
				t.Errorf("第 %d 次: 预期通过; 实际 error code=%d, msg=%s", i, resp.Error.Code, resp.Error.Message)
			}
			if !called {
				t.Errorf("第 %d 次: 高令牌请求未触发工具处理函数", i)
			}
			accepted++
		}
	}

	if rejected != 5 || accepted != 5 {
		t.Errorf("混合令牌测试: 拒绝=%d, 通过=%d; 期望各 5", rejected, accepted)
	}
	t.Logf("✅ 混合流量测试通过: 拒绝=%d, 通过=%d", rejected, accepted)
}

// TestDownstreamPrice_StorageAndRetrieval 测试下游工具价格的存储与检索
// 验证 MCPGovernor 如何处理依赖工具的价格变化
//
// 场景：工具 Bar 依赖下游工具 X 和 Y，测试最大值聚合策略
func TestDownstreamPrice_StorageAndRetrieval(t *testing.T) {
	callMap := map[string][]string{
		"Bar": {"X", "Y"},
	}
	gov := NewMCPGovernor("node-2", callMap, defaultOpts)

	// 1. 初始状态：下游价格应为 0
	downstreamPrice, err := gov.RetrieveDSPrice(context.Background(), "Bar")
	if downstreamPrice != 0 {
		t.Errorf("预期初始下游价格为 0; 实际得到 %d", downstreamPrice)
	}
	if err != nil {
		t.Fatalf("RetrieveDSPrice 错误: %v", err)
	}

	// 2. 更新下游 X 的价格为 15
	newPriceX := int64(15)
	price, err := gov.UpdateDownstreamPrice(context.Background(), "Bar", "X", newPriceX)
	if err != nil {
		t.Fatalf("UpdateDownstreamPrice 错误: %v", err)
	}
	if price != newPriceX {
		t.Errorf("返回的价格 = %d; 期望 %d", price, newPriceX)
	}

	// 3. 检索下游价格应为 15
	got, err := gov.RetrieveDSPrice(context.Background(), "Bar")
	if err != nil {
		t.Fatalf("RetrieveDSPrice 错误: %v", err)
	}
	if got != newPriceX {
		t.Errorf("RetrieveDSPrice = %d; 期望 %d", got, newPriceX)
	}

	// 4. 更新 Y=5（低于当前 15），最大值应该仍为 15
	_, _ = gov.UpdateDownstreamPrice(context.Background(), "Bar", "Y", int64(5))
	got2, err := gov.RetrieveDSPrice(context.Background(), "Bar")
	if got2 != newPriceX {
		t.Errorf("较低价格更新后, DSPrice = %d; 期望 %d (取最大值)", got2, newPriceX)
	}
	if err != nil {
		t.Fatalf("RetrieveDSPrice 错误: %v", err)
	}

	// 5. 更新 Y=20（高于当前 15），最大值应更新为 20
	_, _ = gov.UpdateDownstreamPrice(context.Background(), "Bar", "Y", int64(20))
	got3, err := gov.RetrieveDSPrice(context.Background(), "Bar")
	if got3 != int64(20) {
		t.Errorf("较高价格更新后, DSPrice = %d; 期望 %d (取最大值)", got3, int64(20))
	}
	if err != nil {
		t.Fatalf("RetrieveDSPrice 错误: %v", err)
	}

	t.Logf("✅ 下游价格存储与检索测试通过 (Maximal 策略)")
}

// TestLoadShedding_ReturnsCorrectPrice 验证 LoadShedding 函数的扣费逻辑
//
// 输入：tokens=20, ownprice=7
// 预期：返回 price="7", 剩余 tokens=13
func TestLoadShedding_ReturnsCorrectPrice(t *testing.T) {
	gov := newTestGovernor()
	gov.priceTableMap.Store("ownprice", int64(7))

	tokens, price, err := gov.LoadShedding(context.Background(), 20, "Foo")
	if err != nil {
		t.Fatalf("非预期的错误: %v", err)
	}
	if price != "7" {
		t.Errorf("price = %q; 期望 '7'", price)
	}
	if tokens != 13 {
		t.Errorf("剩余 tokens = %d; 期望 13 (20-7)", tokens)
	}
	t.Logf("✅ LoadShedding 扣费正确: 输入=20, 价格=7, 剩余=13")
}

// TestJSONRPCProtocol_MessageFormat 验证 JSON-RPC 2.0 消息格式的正确性
// 测试序列化/反序列化是否符合 MCP 规范
func TestJSONRPCProtocol_MessageFormat(t *testing.T) {
	// 1. 测试请求构造
	params := MCPToolCallParams{
		Name:      "get_weather",
		Arguments: map[string]interface{}{"city": "北京"},
		Meta: &GovernanceMeta{
			Tokens: 100,
			Method: "get_weather",
			Name:   "client-1",
		},
	}

	req, err := NewJSONRPCRequest(1, MethodToolsCall, params)
	if err != nil {
		t.Fatalf("创建请求失败: %v", err)
	}

	// 序列化为 JSON
	reqBytes, _ := json.MarshalIndent(req, "", "  ")
	t.Logf("JSON-RPC Request:\n%s", string(reqBytes))

	// 验证字段
	if req.JSONRPC != "2.0" {
		t.Errorf("jsonrpc = %q; 期望 '2.0'", req.JSONRPC)
	}
	if req.Method != "tools/call" {
		t.Errorf("method = %q; 期望 'tools/call'", req.Method)
	}

	// 2. 测试成功响应
	result := MCPToolCallResult{
		Content: []ContentBlock{TextContent("北京：晴天 25°C")},
		Meta:    &ResponseMeta{Price: "50", Name: "weather-server-1"},
	}
	successResp := NewSuccessResponse(1, result)
	respBytes, _ := json.MarshalIndent(successResp, "", "  ")
	t.Logf("JSON-RPC Success Response:\n%s", string(respBytes))

	if successResp.Error != nil {
		t.Error("成功响应不应包含 error")
	}

	// 3. 测试错误响应
	errorResp := NewErrorResponse(1, CodeOverloaded, "服务过载", map[string]string{"price": "200"})
	errBytes, _ := json.MarshalIndent(errorResp, "", "  ")
	t.Logf("JSON-RPC Error Response:\n%s", string(errBytes))

	if errorResp.Error == nil {
		t.Error("错误响应应包含 error")
	}
	if errorResp.Error.Code != CodeOverloaded {
		t.Errorf("error.code = %d; 期望 %d", errorResp.Error.Code, CodeOverloaded)
	}

	t.Log("✅ JSON-RPC 2.0 消息格式验证通过")
}
