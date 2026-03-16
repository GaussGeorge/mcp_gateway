// server.go
// 静态限流 MCP 网关 - HTTP 传输层实现
// 基于 HTTP POST + JSON-RPC 2.0 的 MCP 服务端
// 使用固定 QPS 阈值 (令牌桶算法) 对 tools/call 请求进行限流
//
// 与无治理版本 (no_governance) 的关键区别：
//   - 使用令牌桶算法实施固定 QPS 限流
//   - 当请求速率超过阈值时，直接返回限流错误 (CodeRateLimited)
//   - 限流参数固定，不根据负载动态调整
//
// 与治理版本 (mcpgov.MCPServer) 的关键区别：
//   - 限流阈值固定，不做动态定价
//   - 无排队延迟检测、吞吐量检测等自适应机制
//   - 无客户端令牌预算管理
//
// 本实现作为"静态限流"对照组，用于评估动态治理策略相对于固定阈值方案的优势
package staticratelimit

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"
)

// ToolCallHandler 工具调用处理函数签名
type ToolCallHandler func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error)

// ==================== 令牌桶限流器 ====================

// tokenBucket 基于令牌桶算法的固定速率限流器
//
// 原理：
//   - 桶中最多容纳 maxTokens 个令牌 (突发容量)
//   - 令牌以 refillRate 个/秒的速率持续补充
//   - 每个请求消耗 1 个令牌，令牌不足时拒绝请求
//   - 通过懒惰补充 (lazy refill) 实现，无需后台 goroutine
type tokenBucket struct {
	mu         sync.Mutex
	tokens     float64   // 当前令牌数
	maxTokens  float64   // 最大令牌数 (突发容量)
	refillRate float64   // 每秒补充的令牌数 (即 QPS 阈值)
	lastRefill time.Time // 上次补充时间
}

// newTokenBucket 创建一个令牌桶，初始令牌数为满桶
func newTokenBucket(maxTokens int, refillRate float64) *tokenBucket {
	return &tokenBucket{
		tokens:     float64(maxTokens),
		maxTokens:  float64(maxTokens),
		refillRate: refillRate,
		lastRefill: time.Now(),
	}
}

// allow 尝试获取一个令牌
// 返回 true 表示允许请求通过，false 表示被限流
func (tb *tokenBucket) allow() bool {
	tb.mu.Lock()
	defer tb.mu.Unlock()

	// 懒惰补充：根据距上次补充的时间差计算应补充的令牌数
	now := time.Now()
	elapsed := now.Sub(tb.lastRefill).Seconds()
	tb.tokens += elapsed * tb.refillRate
	if tb.tokens > tb.maxTokens {
		tb.tokens = tb.maxTokens
	}
	tb.lastRefill = now

	// 尝试消耗一个令牌
	if tb.tokens >= 1.0 {
		tb.tokens -= 1.0
		return true
	}
	return false
}

// ==================== MCP 静态限流服务端 ====================

// MCPStaticRateLimitServer 是一个基于固定 QPS 阈值限流的 MCP 协议 HTTP 服务端
// 使用令牌桶算法对 tools/call 请求进行限流，超过阈值的请求被直接拒绝
//
// 使用示例：
//
//	cfg := staticratelimit.DefaultConfig() // 默认 20 QPS
//	server := NewMCPStaticRateLimitServer("weather-service", cfg)
//	server.RegisterTool(MCPTool{
//	    Name:        "get_weather",
//	    Description: "查询天气",
//	    InputSchema: map[string]interface{}{...},
//	}, func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
//	    city := params.Arguments["city"].(string)
//	    return &MCPToolCallResult{Content: []ContentBlock{TextContent(city + ": 晴天 25°C")}}, nil
//	})
//	http.ListenAndServe(":8080", server)
type MCPStaticRateLimitServer struct {
	tools      map[string]MCPTool
	handlers   map[string]ToolCallHandler
	serverInfo Implementation
	config     *RateLimitConfig
	limiter    *tokenBucket

	// 统计指标
	mu            sync.Mutex
	totalRequests int64 // 总请求数
	rejectedCount int64 // 被限流拒绝的请求数
	acceptedCount int64 // 放行的请求数
}

// NewMCPStaticRateLimitServer 创建一个固定阈值限流的 MCP 服务端
// name: 服务名称
// config: 限流配置 (包含 QPS 阈值和突发容量)
func NewMCPStaticRateLimitServer(name string, config *RateLimitConfig) *MCPStaticRateLimitServer {
	if config == nil {
		config = DefaultConfig()
	}
	return &MCPStaticRateLimitServer{
		tools:      make(map[string]MCPTool),
		handlers:   make(map[string]ToolCallHandler),
		serverInfo: Implementation{Name: name, Version: "1.0.0"},
		config:     config,
		limiter:    newTokenBucket(config.BurstSize, config.MaxQPS),
	}
}

// RegisterTool 注册一个 MCP 工具及其处理函数
func (s *MCPStaticRateLimitServer) RegisterTool(tool MCPTool, handler ToolCallHandler) {
	s.tools[tool.Name] = tool
	s.handlers[tool.Name] = handler
}

// GetStats 获取限流统计指标
func (s *MCPStaticRateLimitServer) GetStats() (total, accepted, rejected int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.totalRequests, s.acceptedCount, s.rejectedCount
}

// ResetStats 重置统计指标
func (s *MCPStaticRateLimitServer) ResetStats() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.totalRequests = 0
	s.acceptedCount = 0
	s.rejectedCount = 0
}

// ServeHTTP 实现 http.Handler 接口
// 接收 JSON-RPC 2.0 POST 请求，对 tools/call 请求执行固定 QPS 限流
func (s *MCPStaticRateLimitServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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
func (s *MCPStaticRateLimitServer) handleInitialize(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
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
func (s *MCPStaticRateLimitServer) handleToolsList(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	tools := make([]MCPTool, 0, len(s.tools))
	for _, tool := range s.tools {
		tools = append(tools, tool)
	}
	return NewSuccessResponse(req.ID, MCPToolsListResult{Tools: tools})
}

// handleToolsCall 处理 tools/call 请求
// 核心区别：在调用工具处理函数之前，先通过令牌桶检查是否超过 QPS 阈值
// 若超过阈值，直接返回限流错误，不执行工具调用
func (s *MCPStaticRateLimitServer) handleToolsCall(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	// 更新总请求计数
	s.mu.Lock()
	s.totalRequests++
	s.mu.Unlock()

	// 令牌桶限流检查
	if !s.limiter.allow() {
		s.mu.Lock()
		s.rejectedCount++
		s.mu.Unlock()
		return NewErrorResponse(req.ID, CodeRateLimited,
			fmt.Sprintf("请求被限流：当前 QPS 超过阈值 %.0f", s.config.MaxQPS),
			map[string]interface{}{
				"max_qps":     s.config.MaxQPS,
				"retry_after": 1.0 / s.config.MaxQPS, // 建议重试间隔 (秒)
			})
	}

	// 通过限流检查，更新放行计数
	s.mu.Lock()
	s.acceptedCount++
	s.mu.Unlock()

	// 解析工具调用参数
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	handler, ok := s.handlers[params.Name]
	if !ok {
		return NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 调用工具处理函数
	result, err := handler(ctx, params)
	if err != nil {
		return NewErrorResponse(req.ID, CodeInternalError, err.Error(), nil)
	}

	return NewSuccessResponse(req.ID, result)
}

// HandleToolCallDirect 直接处理已解析的工具调用（不经过 JSON-RPC 解析）
// 仍会执行限流检查
// 适用于进程内调用或单元测试
func (s *MCPStaticRateLimitServer) HandleToolCallDirect(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error) {
	s.mu.Lock()
	s.totalRequests++
	s.mu.Unlock()

	if !s.limiter.allow() {
		s.mu.Lock()
		s.rejectedCount++
		s.mu.Unlock()
		return nil, fmt.Errorf("请求被限流：当前 QPS 超过阈值 %.0f", s.config.MaxQPS)
	}

	s.mu.Lock()
	s.acceptedCount++
	s.mu.Unlock()

	handler, ok := s.handlers[params.Name]
	if !ok {
		return nil, fmt.Errorf("工具 '%s' 未注册", params.Name)
	}

	return handler(ctx, params)
}

// ClientSend 静态限流客户端中间件
// 与无治理版本不同，此处检查客户端侧限流
// 直接返回 nil 表示允许发送（客户端侧不做限流，限流在服务端侧执行）
func (s *MCPStaticRateLimitServer) ClientSend(ctx context.Context, params *MCPToolCallParams) error {
	return nil
}

// writeJSON 将响应序列化为 JSON 并写入 HTTP response
func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
