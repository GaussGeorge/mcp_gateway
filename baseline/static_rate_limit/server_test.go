// server_test.go
// 静态限流 MCP 网关 - 单元测试
package staticratelimit

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// ==================== 测试辅助函数 ====================

// newTestServer 创建一个测试用的限流服务器，默认 20 QPS
func newTestServer() *MCPStaticRateLimitServer {
	return newTestServerWithQPS(20)
}

// newTestServerWithQPS 创建指定 QPS 阈值的测试服务器
func newTestServerWithQPS(qps float64) *MCPStaticRateLimitServer {
	cfg := &RateLimitConfig{MaxQPS: qps, BurstSize: int(qps)}
	server := NewMCPStaticRateLimitServer("test-static-ratelimit-server", cfg)
	server.RegisterTool(MCPTool{
		Name:        "echo",
		Description: "回显输入内容",
		InputSchema: map[string]interface{}{
			"type": "object",
			"properties": map[string]interface{}{
				"message": map[string]string{"type": "string"},
			},
		},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		msg, _ := params.Arguments["message"].(string)
		return &MCPToolCallResult{
			Content: []ContentBlock{TextContent("echo: " + msg)},
		}, nil
	})
	return server
}

// sendRequest 发送 JSON-RPC 请求并返回响应
func sendRequest(t *testing.T, server http.Handler, method string, id interface{}, params interface{}) *JSONRPCResponse {
	t.Helper()
	reqBody := map[string]interface{}{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  method,
	}
	if params != nil {
		reqBody["params"] = params
	}
	body, _ := json.Marshal(reqBody)
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	server.ServeHTTP(rec, req)

	var resp JSONRPCResponse
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("解码响应失败: %v", err)
	}
	return &resp
}

// ==================== 基础功能测试 ====================

func TestInitialize(t *testing.T) {
	server := newTestServer()
	resp := sendRequest(t, server, "initialize", 1, map[string]interface{}{
		"protocolVersion": "2024-11-05",
		"clientInfo":      map[string]string{"name": "test-client", "version": "1.0.0"},
	})
	if resp.Error != nil {
		t.Fatalf("initialize 应成功，但返回错误: %v", resp.Error)
	}
	result, _ := json.Marshal(resp.Result)
	var initResult MCPInitializeResult
	json.Unmarshal(result, &initResult)
	if initResult.ServerInfo.Name != "test-static-ratelimit-server" {
		t.Errorf("服务名应为 test-static-ratelimit-server, 实际为 %s", initResult.ServerInfo.Name)
	}
}

func TestToolsList(t *testing.T) {
	server := newTestServer()
	resp := sendRequest(t, server, "tools/list", 2, nil)
	if resp.Error != nil {
		t.Fatalf("tools/list 应成功，但返回错误: %v", resp.Error)
	}
	result, _ := json.Marshal(resp.Result)
	var listResult MCPToolsListResult
	json.Unmarshal(result, &listResult)
	if len(listResult.Tools) != 1 {
		t.Errorf("应有 1 个工具, 实际有 %d 个", len(listResult.Tools))
	}
	if listResult.Tools[0].Name != "echo" {
		t.Errorf("工具名应为 echo, 实际为 %s", listResult.Tools[0].Name)
	}
}

func TestToolsCall(t *testing.T) {
	server := newTestServer()
	resp := sendRequest(t, server, "tools/call", 3, map[string]interface{}{
		"name":      "echo",
		"arguments": map[string]interface{}{"message": "hello"},
	})
	if resp.Error != nil {
		t.Fatalf("tools/call 应成功，但返回错误: %v", resp.Error)
	}
	result, _ := json.Marshal(resp.Result)
	var callResult MCPToolCallResult
	json.Unmarshal(result, &callResult)
	if len(callResult.Content) != 1 || callResult.Content[0].Text != "echo: hello" {
		t.Errorf("工具返回内容不正确: %+v", callResult.Content)
	}
}

func TestToolsCallNotFound(t *testing.T) {
	server := newTestServer()
	resp := sendRequest(t, server, "tools/call", 4, map[string]interface{}{
		"name":      "nonexistent",
		"arguments": map[string]interface{}{},
	})
	if resp.Error == nil {
		t.Fatal("调用不存在的工具应返回错误")
	}
	if resp.Error.Code != CodeMethodNotFound {
		t.Errorf("错误码应为 %d, 实际为 %d", CodeMethodNotFound, resp.Error.Code)
	}
}

func TestPing(t *testing.T) {
	server := newTestServer()
	resp := sendRequest(t, server, "ping", 5, nil)
	if resp.Error != nil {
		t.Fatalf("ping 应成功，但返回错误: %v", resp.Error)
	}
}

func TestMethodNotAllowed(t *testing.T) {
	server := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	rec := httptest.NewRecorder()
	server.ServeHTTP(rec, req)
	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("GET 请求应返回 405, 实际返回 %d", rec.Code)
	}
}

func TestUnknownMethod(t *testing.T) {
	server := newTestServer()
	resp := sendRequest(t, server, "unknown/method", 6, nil)
	if resp.Error == nil {
		t.Fatal("未知方法应返回错误")
	}
	if resp.Error.Code != CodeMethodNotFound {
		t.Errorf("错误码应为 %d, 实际为 %d", CodeMethodNotFound, resp.Error.Code)
	}
}

// ==================== 限流核心测试 ====================

// TestRateLimitingBasic 验证超过 QPS 阈值的请求会被拒绝
func TestRateLimitingBasic(t *testing.T) {
	// 创建一个 QPS=5、突发容量=5 的服务器
	cfg := &RateLimitConfig{MaxQPS: 5, BurstSize: 5}
	server := NewMCPStaticRateLimitServer("test-rl", cfg)
	server.RegisterTool(MCPTool{
		Name:        "echo",
		Description: "回显",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("ok")}}, nil
	})

	// 快速连续发送 10 个请求（突发容量 5，所以前 5 个应通过，后面应有被拒绝的）
	rejected := 0
	for i := 0; i < 10; i++ {
		resp := sendRequest(t, server, "tools/call", i, map[string]interface{}{
			"name": "echo",
		})
		if resp.Error != nil && resp.Error.Code == CodeRateLimited {
			rejected++
		}
	}

	if rejected == 0 {
		t.Error("应有部分请求被限流拒绝，但全部通过了")
	}
	t.Logf("10 个请求中有 %d 个被限流拒绝 (QPS=5, BurstSize=5)", rejected)

	// 验证统计指标
	total, accepted, rejectedCount := server.GetStats()
	if total != 10 {
		t.Errorf("总请求数应为 10, 实际为 %d", total)
	}
	if accepted+rejectedCount != 10 {
		t.Errorf("accepted(%d) + rejected(%d) 应等于 10", accepted, rejectedCount)
	}
}

// TestRateLimitingRefill 验证令牌桶在等待后会补充令牌
func TestRateLimitingRefill(t *testing.T) {
	cfg := &RateLimitConfig{MaxQPS: 10, BurstSize: 2}
	server := NewMCPStaticRateLimitServer("test-rl-refill", cfg)
	server.RegisterTool(MCPTool{
		Name:        "echo",
		Description: "回显",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("ok")}}, nil
	})

	// 消耗所有令牌（BurstSize=2）
	for i := 0; i < 2; i++ {
		resp := sendRequest(t, server, "tools/call", i, map[string]interface{}{"name": "echo"})
		if resp.Error != nil {
			t.Fatalf("前 %d 个请求不应被拒绝", i+1)
		}
	}

	// 第 3 个请求应该被拒绝
	resp := sendRequest(t, server, "tools/call", 99, map[string]interface{}{"name": "echo"})
	if resp.Error == nil || resp.Error.Code != CodeRateLimited {
		t.Error("令牌耗尽后的请求应被限流拒绝")
	}

	// 等待令牌补充 (10 QPS → 每 100ms 补充 1 个令牌)
	time.Sleep(150 * time.Millisecond)

	// 现在应该有令牌了
	resp = sendRequest(t, server, "tools/call", 100, map[string]interface{}{"name": "echo"})
	if resp.Error != nil {
		t.Errorf("等待令牌补充后应能通过，但返回错误: %v", resp.Error)
	}
}

// TestRateLimitingConcurrent 并发场景下的限流测试
func TestRateLimitingConcurrent(t *testing.T) {
	cfg := &RateLimitConfig{MaxQPS: 10, BurstSize: 10}
	server := NewMCPStaticRateLimitServer("test-rl-concurrent", cfg)
	server.RegisterTool(MCPTool{
		Name:        "echo",
		Description: "回显",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("ok")}}, nil
	})

	const concurrency = 50
	var acceptedCount int64
	var rejectedCount int64
	var wg sync.WaitGroup

	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			resp := sendRequest(t, server, "tools/call", id, map[string]interface{}{
				"name": "echo",
			})
			if resp.Error != nil && resp.Error.Code == CodeRateLimited {
				atomic.AddInt64(&rejectedCount, 1)
			} else if resp.Error == nil {
				atomic.AddInt64(&acceptedCount, 1)
			}
		}(i)
	}
	wg.Wait()

	t.Logf("并发 %d 请求: %d 通过, %d 被限流 (QPS=10, BurstSize=10)",
		concurrency, acceptedCount, rejectedCount)

	if rejectedCount == 0 {
		t.Error("50 个并发请求 (QPS=10) 应有部分被拒绝")
	}
	if acceptedCount == 0 {
		t.Error("应有部分请求被放行")
	}
}

// TestRateLimitingDoesNotAffectOtherMethods 验证限流只作用于 tools/call
func TestRateLimitingDoesNotAffectOtherMethods(t *testing.T) {
	cfg := &RateLimitConfig{MaxQPS: 1, BurstSize: 1}
	server := NewMCPStaticRateLimitServer("test-rl-scope", cfg)
	server.RegisterTool(MCPTool{
		Name:        "echo",
		Description: "回显",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("ok")}}, nil
	})

	// 消耗唯一的令牌
	sendRequest(t, server, "tools/call", 1, map[string]interface{}{"name": "echo"})

	// tools/list 和 ping 不应受限流影响
	resp := sendRequest(t, server, "tools/list", 2, nil)
	if resp.Error != nil {
		t.Error("tools/list 不应受限流影响")
	}

	resp = sendRequest(t, server, "ping", 3, nil)
	if resp.Error != nil {
		t.Error("ping 不应受限流影响")
	}

	resp = sendRequest(t, server, "initialize", 4, map[string]interface{}{
		"protocolVersion": "2024-11-05",
		"clientInfo":      map[string]string{"name": "test", "version": "1.0.0"},
	})
	if resp.Error != nil {
		t.Error("initialize 不应受限流影响")
	}
}

// ==================== 统计指标测试 ====================

func TestStats(t *testing.T) {
	cfg := &RateLimitConfig{MaxQPS: 2, BurstSize: 2}
	server := NewMCPStaticRateLimitServer("test-stats", cfg)
	server.RegisterTool(MCPTool{
		Name:        "echo",
		Description: "回显",
		InputSchema: map[string]interface{}{"type": "object"},
	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
		return &MCPToolCallResult{Content: []ContentBlock{TextContent("ok")}}, nil
	})

	// 发送 5 个请求
	for i := 0; i < 5; i++ {
		sendRequest(t, server, "tools/call", i, map[string]interface{}{"name": "echo"})
	}

	total, accepted, rejected := server.GetStats()
	if total != 5 {
		t.Errorf("总请求数应为 5, 实际为 %d", total)
	}
	if accepted+rejected != 5 {
		t.Errorf("accepted(%d) + rejected(%d) 应等于 5", accepted, rejected)
	}

	// 重置统计
	server.ResetStats()
	total, accepted, rejected = server.GetStats()
	if total != 0 || accepted != 0 || rejected != 0 {
		t.Error("重置后统计应全部为 0")
	}
}

// ==================== HandleToolCallDirect 测试 ====================

func TestHandleToolCallDirect(t *testing.T) {
	server := newTestServer()
	params := MCPToolCallParams{
		Name:      "echo",
		Arguments: map[string]interface{}{"message": "direct"},
	}
	result, err := server.HandleToolCallDirect(context.Background(), params)
	if err != nil {
		t.Fatalf("HandleToolCallDirect 应成功，但返回错误: %v", err)
	}
	if len(result.Content) != 1 || result.Content[0].Text != "echo: direct" {
		t.Errorf("返回内容不正确: %+v", result.Content)
	}
}

// ==================== 配置测试 ====================

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.MaxQPS != 20.0 {
		t.Errorf("默认 QPS 应为 20, 实际为 %f", cfg.MaxQPS)
	}
	if cfg.BurstSize != 20 {
		t.Errorf("默认 BurstSize 应为 20, 实际为 %d", cfg.BurstSize)
	}
}

func TestLoadConfigFromFile(t *testing.T) {
	// 创建临时配置文件
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.json")
	cfgData := `{"max_qps": 50, "burst_size": 100}`
	if err := os.WriteFile(cfgPath, []byte(cfgData), 0644); err != nil {
		t.Fatalf("写入测试配置文件失败: %v", err)
	}

	cfg, err := LoadConfigFromFile(cfgPath)
	if err != nil {
		t.Fatalf("加载配置失败: %v", err)
	}
	if cfg.MaxQPS != 50 {
		t.Errorf("MaxQPS 应为 50, 实际为 %f", cfg.MaxQPS)
	}
	if cfg.BurstSize != 100 {
		t.Errorf("BurstSize 应为 100, 实际为 %d", cfg.BurstSize)
	}
}

func TestLoadConfigInvalidQPS(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.json")
	cfgData := `{"max_qps": -1}`
	os.WriteFile(cfgPath, []byte(cfgData), 0644)

	_, err := LoadConfigFromFile(cfgPath)
	if err == nil {
		t.Error("负数 QPS 应返回错误")
	}
}

func TestLoadConfigOrDefault(t *testing.T) {
	// 文件不存在时应返回默认配置
	cfg := LoadConfigOrDefault("/nonexistent/path/config.json")
	if cfg.MaxQPS != 20.0 {
		t.Errorf("文件不存在时应返回默认配置 (QPS=20), 实际为 %f", cfg.MaxQPS)
	}
}

func TestNilConfigUsesDefault(t *testing.T) {
	server := NewMCPStaticRateLimitServer("test", nil)
	if server.config.MaxQPS != 20.0 {
		t.Errorf("nil 配置应使用默认值 (QPS=20), 实际为 %f", server.config.MaxQPS)
	}
}
