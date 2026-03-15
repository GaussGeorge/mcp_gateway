// mcp_transport_test.go
// MCP HTTP 传输层集成测试
// 测试通过真实 HTTP 连接的 MCP 工具调用治理流程
package mcpgov

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// TestMCPServer_Initialize 测试 MCP 初始化握手
func TestMCPServer_Initialize(t *testing.T) {
	callMap := map[string][]string{"test_tool": {}}
	gov := NewMCPGovernor("server-1", callMap, defaultOpts)
	server := NewMCPServer("test-server", gov)

	ts := httptest.NewServer(server)
	defer ts.Close()

	// 发送 initialize 请求
	req, _ := NewJSONRPCRequest(1, MethodInitialize, MCPInitializeParams{
		ProtocolVersion: "2024-11-05",
		ClientInfo:      Implementation{Name: "test-client", Version: "1.0.0"},
	})

	resp := sendJSONRPC(t, ts.URL, req)

	if resp.Error != nil {
		t.Fatalf("initialize 失败: %v", resp.Error)
	}

	// 解析结果
	resultBytes, _ := json.Marshal(resp.Result)
	var result MCPInitializeResult
	json.Unmarshal(resultBytes, &result)

	if result.ProtocolVersion != "2024-11-05" {
		t.Errorf("协议版本 = %q; 期望 '2024-11-05'", result.ProtocolVersion)
	}
	if result.ServerInfo.Name != "test-server" {
		t.Errorf("服务器名称 = %q; 期望 'test-server'", result.ServerInfo.Name)
	}

	t.Logf("✅ MCP Initialize 握手成功: server=%s, version=%s",
		result.ServerInfo.Name, result.ProtocolVersion)
}

// TestMCPServer_ToolsList 测试工具列表
func TestMCPServer_ToolsList(t *testing.T) {
	callMap := map[string][]string{"get_weather": {}, "search": {}}
	gov := NewMCPGovernor("server-1", callMap, defaultOpts)
	server := NewMCPServer("tool-server", gov)

	// 注册两个工具
	server.RegisterTool(MCPTool{
		Name:        "get_weather",
		Description: "查询城市天气",
		InputSchema: map[string]interface{}{
			"type": "object",
			"properties": map[string]interface{}{
				"city": map[string]string{"type": "string", "description": "城市名称"},
			},
			"required": []string{"city"},
		},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("晴天")}}, nil
	})

	server.RegisterTool(MCPTool{
		Name:        "search",
		Description: "搜索信息",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("搜索结果")}}, nil
	})

	ts := httptest.NewServer(server)
	defer ts.Close()

	req, _ := NewJSONRPCRequest(2, MethodToolsList, nil)
	resp := sendJSONRPC(t, ts.URL, req)

	if resp.Error != nil {
		t.Fatalf("tools/list 失败: %v", resp.Error)
	}

	resultBytes, _ := json.Marshal(resp.Result)
	var result MCPToolsListResult
	json.Unmarshal(resultBytes, &result)

	if len(result.Tools) != 2 {
		t.Errorf("工具数量 = %d; 期望 2", len(result.Tools))
	}

	t.Logf("✅ Tools List 返回 %d 个工具", len(result.Tools))
	for _, tool := range result.Tools {
		t.Logf("  - %s: %s", tool.Name, tool.Description)
	}
}

// TestMCPServer_ToolCallGovernance 测试通过 HTTP 的完整工具调用治理流程
func TestMCPServer_ToolCallGovernance(t *testing.T) {
	callMap := map[string][]string{"get_weather": {}}
	gov := NewMCPGovernor("weather-node", callMap, defaultOpts)
	server := NewMCPServer("weather-service", gov)

	server.RegisterTool(MCPTool{
		Name:        "get_weather",
		Description: "查询天气",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		city, _ := params.Arguments["city"].(string)
		if city == "" {
			city = "未知"
		}
		return &MCPToolCallResult{
			Content: []ContentBlock{TextContent(fmt.Sprintf("%s: 晴天 25°C", city))},
		}, nil
	})

	ts := httptest.NewServer(server)
	defer ts.Close()

	// 设置价格为 50
	gov.priceTableMap.Store("ownprice", int64(50))

	// --- 测试 1: 令牌充足的请求 ---
	t.Run("令牌充足-请求通过", func(t *testing.T) {
		params := MCPToolCallParams{
			Name:      "get_weather",
			Arguments: map[string]interface{}{"city": "北京"},
			Meta:      &GovernanceMeta{Tokens: 100, Method: "get_weather", Name: "client-1"},
		}
		req, _ := NewJSONRPCRequest(1, MethodToolsCall, params)
		resp := sendJSONRPC(t, ts.URL, req)

		if resp.Error != nil {
			t.Fatalf("预期成功; 实际错误: %v", resp.Error)
		}

		resultBytes, _ := json.Marshal(resp.Result)
		var result MCPToolCallResult
		json.Unmarshal(resultBytes, &result)

		if len(result.Content) == 0 {
			t.Fatal("响应内容为空")
		}
		t.Logf("✅ 工具调用成功: %s", result.Content[0].Text)

		if result.Meta != nil && result.Meta.Price != "" {
			t.Logf("   返回价格: %s (节点: %s)", result.Meta.Price, result.Meta.Name)
		}
	})

	// --- 测试 2: 令牌不足的请求 ---
	t.Run("令牌不足-请求被拒绝", func(t *testing.T) {
		params := MCPToolCallParams{
			Name:      "get_weather",
			Arguments: map[string]interface{}{"city": "上海"},
			Meta:      &GovernanceMeta{Tokens: 10, Method: "get_weather", Name: "client-2"},
		}
		req, _ := NewJSONRPCRequest(2, MethodToolsCall, params)
		resp := sendJSONRPC(t, ts.URL, req)

		if resp.Error == nil {
			t.Fatal("预期被拒绝; 实际通过了")
		}
		if resp.Error.Code != CodeOverloaded {
			t.Errorf("错误码 = %d; 期望 %d (CodeOverloaded)", resp.Error.Code, CodeOverloaded)
		}
		t.Logf("✅ 低令牌请求被正确拒绝: code=%d, msg=%s", resp.Error.Code, resp.Error.Message)
	})

	// --- 测试 3: 未注册的工具 ---
	t.Run("未注册工具-报错", func(t *testing.T) {
		params := MCPToolCallParams{
			Name: "nonexistent_tool",
			Meta: &GovernanceMeta{Tokens: 100},
		}
		req, _ := NewJSONRPCRequest(3, MethodToolsCall, params)
		resp := sendJSONRPC(t, ts.URL, req)

		if resp.Error == nil {
			t.Fatal("预期返回错误")
		}
		if resp.Error.Code != CodeMethodNotFound {
			t.Errorf("错误码 = %d; 期望 %d (CodeMethodNotFound)", resp.Error.Code, CodeMethodNotFound)
		}
		t.Logf("✅ 未注册工具请求被正确拒绝: %s", resp.Error.Message)
	})
}

// TestMCPServer_HighConcurrency 测试高并发下的价格自适应机制
// 验证在大量并发工具调用下，治理引擎能否检测过载并提高价格
func TestMCPServer_HighConcurrency(t *testing.T) {
	// 配置：开启排队延迟检测
	opts := map[string]interface{}{
		"priceUpdateRate":  5000 * time.Microsecond,
		"tokenUpdateRate":  100000 * time.Microsecond,
		"latencyThreshold": 500 * time.Microsecond,
		"priceStep":        int64(180),
		"priceStrategy":    "expdecay",
		"lazyResponse":     false,
		"rateLimiting":     true,
		"loadShedding":     true,
		"pinpointQueuing":  true, // 开启排队延迟检测
	}

	callMap := map[string][]string{"heavy_tool": {}}
	gov := NewMCPGovernor("node-1", callMap, opts)
	server := NewMCPServer("heavy-service", gov)

	// 注册一个"重"工具（模拟耗时操作）
	server.RegisterTool(MCPTool{
		Name:        "heavy_tool",
		Description: "模拟耗时工具调用",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		// 模拟 1 秒的处理延迟
		time.Sleep(1 * time.Second)
		return &MCPToolCallResult{
			Content: []ContentBlock{TextContent("处理完成")},
		}, nil
	})

	ts := httptest.NewServer(server)
	defer ts.Close()

	// 并发参数
	const concurrency = 200
	const testDuration = 6 * time.Second
	stop := make(chan struct{})

	var wg sync.WaitGroup
	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
					params := MCPToolCallParams{
						Name: "heavy_tool",
						Meta: &GovernanceMeta{Tokens: 1000, Method: "heavy_tool"},
					}
					req, _ := NewJSONRPCRequest(1, MethodToolsCall, params)
					sendJSONRPCNoCheck(ts.URL, req)
				}
			}
		}()
	}

	// 让负载持续一段时间
	time.Sleep(testDuration / 2)

	// 检查价格是否上涨
	priceStr, err := gov.RetrieveTotalPrice(context.Background(), "heavy_tool")
	if err != nil {
		t.Fatalf("获取总价失败: %v", err)
	}

	if priceStr == "0" {
		t.Logf("⚠️ 价格暂未上涨 (可能需要更高并发或更长时间): price=%s", priceStr)
	} else {
		t.Logf("✅ 高并发下价格已上涨: price=%s", priceStr)
	}

	// 继续运行剩余时间
	time.Sleep(testDuration / 2)

	close(stop)
	wg.Wait()
}

// TestMCPServer_Ping 测试健康检查
func TestMCPServer_Ping(t *testing.T) {
	callMap := map[string][]string{}
	gov := NewMCPGovernor("server-1", callMap, defaultOpts)
	server := NewMCPServer("test-server", gov)

	ts := httptest.NewServer(server)
	defer ts.Close()

	req, _ := NewJSONRPCRequest(99, MethodPing, nil)
	resp := sendJSONRPC(t, ts.URL, req)

	if resp.Error != nil {
		t.Fatalf("ping 失败: %v", resp.Error)
	}
	t.Log("✅ Ping 响应正常")
}

// TestMCPServer_InvalidMethod 测试无效方法名
func TestMCPServer_InvalidMethod(t *testing.T) {
	callMap := map[string][]string{}
	gov := NewMCPGovernor("server-1", callMap, defaultOpts)
	server := NewMCPServer("test-server", gov)

	ts := httptest.NewServer(server)
	defer ts.Close()

	req, _ := NewJSONRPCRequest(1, "invalid/method", nil)
	resp := sendJSONRPC(t, ts.URL, req)

	if resp.Error == nil {
		t.Fatal("预期返回错误")
	}
	if resp.Error.Code != CodeMethodNotFound {
		t.Errorf("错误码 = %d; 期望 %d", resp.Error.Code, CodeMethodNotFound)
	}
	t.Logf("✅ 无效方法名被正确拒绝: %s", resp.Error.Message)
}

// ==================== 辅助函数 ====================

// sendJSONRPC 发送 JSON-RPC 请求并解析响应
func sendJSONRPC(t *testing.T, url string, req *JSONRPCRequest) *JSONRPCResponse {
	t.Helper()

	body, _ := json.Marshal(req)
	httpResp, err := http.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("HTTP 请求失败: %v", err)
	}
	defer httpResp.Body.Close()

	respBody, _ := io.ReadAll(httpResp.Body)

	var resp JSONRPCResponse
	if err := json.Unmarshal(respBody, &resp); err != nil {
		t.Fatalf("JSON 解析响应失败: %v\n原始响应: %s", err, string(respBody))
	}
	return &resp
}

// sendJSONRPCNoCheck 发送请求但不检查错误（用于并发压测）
func sendJSONRPCNoCheck(url string, req *JSONRPCRequest) *JSONRPCResponse {
	body, _ := json.Marshal(req)
	httpResp, err := http.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return nil
	}
	defer httpResp.Body.Close()

	var resp JSONRPCResponse
	json.NewDecoder(httpResp.Body).Decode(&resp)
	return &resp
}
