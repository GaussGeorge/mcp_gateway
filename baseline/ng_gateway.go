// ng_gateway.go
// No Governance (NG) 基线网关实现
// 所有请求直接透传，不做任何准入控制、限流或负载削减。
// 用于证明 "不治理会怎样"，作为实验的下界基线。
//
// 公平性保证：
//   - 使用与 DP 相同的 MCPServer 传输层和 JSON-RPC 2.0 协议
//   - 使用与 DP 相同的 HandleToolCall 接口签名
//   - 响应中携带 _meta（price="0"），保持协议格式一致
//   - 相同的工具注册与路由机制
package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// NGGateway 无治理网关：所有请求直接透传
type NGGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	// 统计指标（用于实验数据采集，不影响请求处理）
	stats NGStats
}

// NGStats 记录 NG 网关的运行统计
type NGStats struct {
	TotalRequests   int64 // 总请求数
	SuccessRequests int64 // 成功请求数
	ErrorRequests   int64 // 后端错误请求数
	mu              sync.Mutex
	startTime       time.Time
}

// NewNGGateway 创建无治理网关实例
func NewNGGateway(nodeName string) *NGGateway {
	return &NGGateway{
		nodeName:   nodeName,
		tools:      make(map[string]mcpgov.MCPTool),
		handlers:   make(map[string]mcpgov.ToolCallHandler),
		serverInfo: mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		stats: NGStats{
			startTime: time.Now(),
		},
	}
}

// RegisterTool 注册工具（与 MCPServer 接口一致）
func (ng *NGGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	ng.tools[tool.Name] = tool
	ng.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
func (ng *NGGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	var req mcpgov.JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		resp := mcpgov.NewErrorResponse(nil, -32700, "JSON 解析错误", err.Error())
		writeJSON(w, resp)
		return
	}

	if req.JSONRPC != "2.0" {
		resp := mcpgov.NewErrorResponse(req.ID, -32600, "jsonrpc 版本必须为 2.0", nil)
		writeJSON(w, resp)
		return
	}

	ctx := r.Context()
	var resp *mcpgov.JSONRPCResponse

	switch req.Method {
	case "initialize":
		resp = ng.handleInitialize(ctx, &req)
	case "tools/list":
		resp = ng.handleToolsList(ctx, &req)
	case "tools/call":
		resp = ng.handleToolsCall(ctx, &req)
	case "ping":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	writeJSON(w, resp)
}

// handleInitialize MCP 初始化握手
func (ng *NGGateway) handleInitialize(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	result := map[string]interface{}{
		"protocolVersion": "2024-11-05",
		"serverInfo":      ng.serverInfo,
		"capabilities": map[string]interface{}{
			"tools": map[string]interface{}{"listChanged": false},
		},
	}
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// handleToolsList 返回可用工具列表
func (ng *NGGateway) handleToolsList(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	tools := make([]mcpgov.MCPTool, 0, len(ng.tools))
	for _, tool := range ng.tools {
		tools = append(tools, tool)
	}
	return mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{"tools": tools})
}

// handleToolsCall 无治理的工具调用处理
// 核心行为：直接透传请求到后端工具，不做任何准入检查
func (ng *NGGateway) handleToolsCall(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	// 1. 解析请求参数
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, -32602, "无效的工具调用参数", err.Error())
	}

	// 2. 查找工具处理器
	handler, ok := ng.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 3. 统计：记录总请求数
	atomic.AddInt64(&ng.stats.TotalRequests, 1)

	// 4. 直接调用工具处理函数 —— 无任何治理逻辑
	result, err := handler(ctx, params)
	if err != nil {
		atomic.AddInt64(&ng.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	// 5. 统计：记录成功请求数
	atomic.AddInt64(&ng.stats.SuccessRequests, 1)

	// 6. 在 _meta 中返回 price="0"，保持协议格式一致，便于统一指标收集
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Price = "0"
	result.Meta.Name = ng.nodeName

	return mcpgov.NewSuccessResponse(req.ID, result)
}

// GetStats 获取当前统计数据
func (ng *NGGateway) GetStats() (total, success, errors int64) {
	return atomic.LoadInt64(&ng.stats.TotalRequests),
		atomic.LoadInt64(&ng.stats.SuccessRequests),
		atomic.LoadInt64(&ng.stats.ErrorRequests)
}

// writeJSON 将响应序列化为 JSON 并写入 HTTP response
func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
