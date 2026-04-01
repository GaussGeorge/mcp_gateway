// mcp_transport.go
// MCP HTTP 传输层实现
// 提供基于 HTTP POST + JSON-RPC 2.0 的 MCP 服务端
// 支持 initialize、tools/list、tools/call、ping 等 MCP 标准方法
package mcpgov

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// MCPServer 是一个完整的 MCP 协议 HTTP 服务端
// 集成了 MCPGovernor 治理引擎，对所有 tools/call 请求执行过载控制
//
// 使用示例：
//
//	gov := NewMCPGovernor("server-1", callMap, opts)
//	server := NewMCPServer("weather-service", gov)
//	server.RegisterTool(MCPTool{
//	    Name:        "get_weather",
//	    Description: "查询天气",
//	    InputSchema: map[string]interface{}{"type": "object", "properties": map[string]interface{}{"city": map[string]string{"type": "string"}}},
//	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
//	    city := params.Arguments["city"].(string)
//	    return &MCPToolCallResult{Content: []ContentBlock{TextContent(city + ": 晴天 25°C")}}, nil
//	})
//	http.ListenAndServe(":8080", server)
type MCPServer struct {
	governor   *MCPGovernor
	tools      map[string]MCPTool
	handlers   map[string]ToolCallHandler
	serverInfo Implementation
}

// NewMCPServer 创建一个新的 MCP HTTP 服务端
// name: 服务名称，会在 initialize 响应中返回给客户端
// governor: 服务治理引擎实例
func NewMCPServer(name string, governor *MCPGovernor) *MCPServer {
	return &MCPServer{
		governor:   governor,
		tools:      make(map[string]MCPTool),
		handlers:   make(map[string]ToolCallHandler),
		serverInfo: Implementation{Name: name, Version: "1.0.0"},
	}
}

// RegisterTool 注册一个 MCP 工具及其处理函数
// 注册后的工具可通过 tools/list 列出，通过 tools/call 调用
func (s *MCPServer) RegisterTool(tool MCPTool, handler ToolCallHandler) {
	s.tools[tool.Name] = tool
	s.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
// 接收 JSON-RPC 2.0 POST 请求，路由到对应的 MCP 方法处理器
//
// 请求示例 (tools/call):
//
//	POST /mcp HTTP/1.1
//	Content-Type: application/json
//
//	{
//	  "jsonrpc": "2.0",
//	  "id": 1,
//	  "method": "tools/call",
//	  "params": {
//	    "name": "get_weather",
//	    "arguments": {"city": "北京"},
//	    "_meta": {"tokens": 100, "name": "client-1"}
//	  }
//	}
func (s *MCPServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// MCP over HTTP 仅支持 POST 方法
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	// 解析 JSON-RPC 请求
	var req JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		resp := NewErrorResponse(nil, CodeParseError, "JSON 解析错误", err.Error())
		writeJSON(w, resp)
		return
	}

	// 校验 JSON-RPC 版本
	if req.JSONRPC != JSONRPCVersion {
		resp := NewErrorResponse(req.ID, CodeInvalidRequest, "jsonrpc 版本必须为 2.0", nil)
		writeJSON(w, resp)
		return
	}

	// 根据 MCP 方法名路由到对应的处理器
	ctx := r.Context()
	var resp *JSONRPCResponse

	switch req.Method {
	case MethodInitialize:
		resp = s.handleInitialize(ctx, &req)
	case MethodToolsList:
		resp = s.handleToolsList(ctx, &req)
	case MethodToolsCall:
		resp = s.handleToolsCall(ctx, &req)
	case MethodPing:
		resp = NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	writeJSON(w, resp)
}

// handleInitialize 处理 MCP 初始化握手
// 客户端在建立连接后首先发送 initialize 请求
// 服务端返回协议版本和能力声明
func (s *MCPServer) handleInitialize(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	result := MCPInitializeResult{
		ProtocolVersion: "2024-11-05", // MCP 协议版本
		ServerInfo:      s.serverInfo,
		Capabilities: ServerCapabilities{
			Tools: &ToolsCapability{ListChanged: false},
		},
	}
	return NewSuccessResponse(req.ID, result)
}

// handleToolsList 处理 tools/list 请求
// 返回当前服务端注册的所有可用工具列表
func (s *MCPServer) handleToolsList(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	tools := make([]MCPTool, 0, len(s.tools))
	for _, tool := range s.tools {
		tools = append(tools, tool)
	}
	return NewSuccessResponse(req.ID, MCPToolsListResult{Tools: tools})
}

// handleToolsCall 处理 tools/call 请求
// 这是治理中间件的入口点：
// 1. 解析请求中的工具名
// 2. 查找已注册的工具处理器
// 3. 通过 MCPGovernor.HandleToolCall 执行治理逻辑 (令牌检查、负载削减)
// 4. 如果通过治理检查，执行实际的工具逻辑
// 5. 在响应 _meta 中携带当前价格
func (s *MCPServer) handleToolsCall(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	// 先解析参数获取工具名称
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	// 查找工具处理器
	handler, ok := s.handlers[params.Name]
	if !ok {
		return NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 通过治理中间件执行工具调用
	return s.governor.HandleToolCall(ctx, req, handler)
}

// writeJSON 将响应序列化为 JSON 并写入 HTTP response
func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
