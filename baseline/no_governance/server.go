// server.go
// 无治理 MCP 网关 - HTTP 传输层实现
// 基于 HTTP POST + JSON-RPC 2.0 的 MCP 服务端
// 直接转发所有请求，不执行任何限流、定价或过载保护逻辑
//
// 与治理版本 (mcpgov.MCPServer) 的关键区别：
//   - 无 MCPGovernor 引擎，不做负载削减 (Load Shedding)
//   - 无动态定价机制，不在请求/响应中处理 tokens、price 等治理元数据
//   - 无客户端限流 (Rate Limiting)、退避 (Backoff) 逻辑
//   - 所有 tools/call 请求直接路由到注册的处理函数，无准入控制
//
// 本实现作为基线对照组，用于评估治理策略带来的性能影响与稳定性收益
package nogovernance

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// ToolCallHandler 工具调用处理函数签名
// 接收上下文和工具调用参数，返回工具调用结果
type ToolCallHandler func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error)

// MCPBaselineServer 是一个无治理逻辑的 MCP 协议 HTTP 服务端
// 所有 tools/call 请求直接转发到注册的工具处理函数，不做任何治理干预
//
// 使用示例：
//
//	server := NewMCPBaselineServer("weather-service")
//	server.RegisterTool(MCPTool{
//	    Name:        "get_weather",
//	    Description: "查询天气",
//	    InputSchema: map[string]interface{}{...},
//	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
//	    city := params.Arguments["city"].(string)
//	    return &MCPToolCallResult{Content: []ContentBlock{TextContent(city + ": 晴天 25°C")}}, nil
//	})
//	http.ListenAndServe(":8080", server)
type MCPBaselineServer struct {
	tools      map[string]MCPTool
	handlers   map[string]ToolCallHandler
	serverInfo Implementation
}

// NewMCPBaselineServer 创建一个无治理逻辑的 MCP 基线服务端
// name: 服务名称，会在 initialize 响应中返回给客户端
func NewMCPBaselineServer(name string) *MCPBaselineServer {
	return &MCPBaselineServer{
		tools:      make(map[string]MCPTool),
		handlers:   make(map[string]ToolCallHandler),
		serverInfo: Implementation{Name: name, Version: "1.0.0"},
	}
}

// RegisterTool 注册一个 MCP 工具及其处理函数
// 注册后的工具可通过 tools/list 列出，通过 tools/call 调用
func (s *MCPBaselineServer) RegisterTool(tool MCPTool, handler ToolCallHandler) {
	s.tools[tool.Name] = tool
	s.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
// 接收 JSON-RPC 2.0 POST 请求，直接路由到对应的 MCP 方法处理器
// 不执行任何治理逻辑
func (s *MCPBaselineServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	var req JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		resp := NewErrorResponse(nil, CodeParseError, "JSON 解析错误", err.Error())
		writeJSON(w, resp)
		return
	}

	if req.JSONRPC != JSONRPCVersion {
		resp := NewErrorResponse(req.ID, CodeInvalidRequest, "jsonrpc 版本必须为 2.0", nil)
		writeJSON(w, resp)
		return
	}

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
func (s *MCPBaselineServer) handleInitialize(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	result := MCPInitializeResult{
		ProtocolVersion: "2024-11-05",
		ServerInfo:      s.serverInfo,
		Capabilities: ServerCapabilities{
			Tools: &ToolsCapability{ListChanged: false},
		},
	}
	return NewSuccessResponse(req.ID, result)
}

// handleToolsList 处理 tools/list 请求，返回所有已注册的工具
func (s *MCPBaselineServer) handleToolsList(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	tools := make([]MCPTool, 0, len(s.tools))
	for _, tool := range s.tools {
		tools = append(tools, tool)
	}
	return NewSuccessResponse(req.ID, MCPToolsListResult{Tools: tools})
}

// handleToolsCall 处理 tools/call 请求
// 与治理版本不同，此处直接调用工具处理函数，无任何准入控制或价格计算
func (s *MCPBaselineServer) handleToolsCall(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	handler, ok := s.handlers[params.Name]
	if !ok {
		return NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 直接调用工具处理函数，不做任何治理检查
	result, err := handler(ctx, params)
	if err != nil {
		return NewErrorResponse(req.ID, CodeInternalError, err.Error(), nil)
	}

	return NewSuccessResponse(req.ID, result)
}

// HandleToolCallDirect 直接处理已解析的工具调用（不经过 JSON-RPC 解析）
// 适用于进程内调用或单元测试
func (s *MCPBaselineServer) HandleToolCallDirect(ctx context.Context, params MCPToolCallParams, handler ToolCallHandler) (*MCPToolCallResult, error) {
	result, err := handler(ctx, params)
	if err != nil {
		return nil, err
	}
	return result, nil
}

// ClientSend 无治理客户端中间件
// 与治理版本不同，不做任何令牌注入、限流检查或退避逻辑
// 直接返回 nil 表示允许发送
func (s *MCPBaselineServer) ClientSend(ctx context.Context, params *MCPToolCallParams) error {
	// 无治理逻辑，直接放行
	return nil
}

// writeJSON 将响应序列化为 JSON 并写入 HTTP response
func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
