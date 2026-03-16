// server_test.go
// 无治理 MCP 网关 - 单元测试
package nogovernance

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// 创建一个测试用的基线服务器，注册一个简单的 echo 工具
func newTestServer() *MCPBaselineServer {
	server := NewMCPBaselineServer("test-baseline-server")
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
	if initResult.ServerInfo.Name != "test-baseline-server" {
		t.Errorf("服务名应为 test-baseline-server, 实际为 %s", initResult.ServerInfo.Name)
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

// TestNoGovernanceOverhead 验证无治理网关不会因大量并发请求而拒绝任何请求
// 这是与治理版本的核心区别：治理版本会在过载时拒绝请求，而基线版本全部放行
func TestNoGovernanceOverhead(t *testing.T) {
	server := newTestServer()
	const concurrency = 100
	errors := make(chan error, concurrency)

	for i := 0; i < concurrency; i++ {
		go func(id int) {
			resp := sendRequest(t, server, "tools/call", id, map[string]interface{}{
				"name":      "echo",
				"arguments": map[string]interface{}{"message": "concurrent"},
				"_meta":     map[string]interface{}{"tokens": 0}, // 令牌为 0，治理版本会拒绝
			})
			if resp.Error != nil {
				errors <- resp.Error
			} else {
				errors <- nil
			}
		}(i)
	}

	rejected := 0
	for i := 0; i < concurrency; i++ {
		if err := <-errors; err != nil {
			rejected++
		}
	}
	if rejected > 0 {
		t.Errorf("无治理网关不应拒绝任何请求，但拒绝了 %d/%d 个", rejected, concurrency)
	}
}

func TestHandleToolCallDirect(t *testing.T) {
	server := newTestServer()
	params := MCPToolCallParams{
		Name:      "echo",
		Arguments: map[string]interface{}{"message": "direct"},
	}
	handler := server.handlers["echo"]
	result, err := server.HandleToolCallDirect(context.Background(), params, handler)
	if err != nil {
		t.Fatalf("HandleToolCallDirect 应成功，但返回错误: %v", err)
	}
	if len(result.Content) != 1 || result.Content[0].Text != "echo: direct" {
		t.Errorf("返回内容不正确: %+v", result.Content)
	}
}

func TestClientSendAlwaysAllows(t *testing.T) {
	server := newTestServer()
	params := &MCPToolCallParams{
		Name:      "echo",
		Arguments: map[string]interface{}{"message": "test"},
	}
	err := server.ClientSend(context.Background(), params)
	if err != nil {
		t.Errorf("ClientSend 应始终返回 nil，但返回: %v", err)
	}
}
