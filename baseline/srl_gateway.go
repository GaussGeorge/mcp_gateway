// srl_gateway.go
// Static Rate Limit (SRL) 基线网关实现
// 使用固定 QPS 令牌桶 + 最大并发数限制进行限流，超出直接拒绝。
// 不区分请求类型（轻量/重量）和预算（高/低），对所有请求一刀切。
// 代表现有 MCP 生态中最常见的限流方式。
//
// 公平性保证：
//   - 使用与 DP 相同的 MCPServer 传输层和 JSON-RPC 2.0 协议
//   - 使用与 DP 相同的 HandleToolCall 接口签名
//   - 响应中携带 _meta（price 表示当前令牌桶状态），保持协议格式一致
//   - SRL 的 QPS 参数可调，实验中选择与 DP 通过率接近的值以确保公平
//   - 令牌桶算法是工业界标准限流方案（Nginx, Envoy, Istio 均采用）
package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// SRLGateway 静态限流网关
type SRLGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	// 令牌桶参数
	bucket *TokenBucket

	// 最大并发数限制（可选，模拟 max_connections 类限制）
	maxConcurrency int64
	activeCalls    int64

	// 统计指标
	stats SRLStats
}

// SRLConfig SRL 网关配置参数
type SRLConfig struct {
	// QPS 令牌桶每秒补充的令牌数（即稳态允许的 QPS）
	QPS float64
	// BurstSize 令牌桶最大容量（允许的突发请求数）
	BurstSize int64
	// MaxConcurrency 最大并发请求数（0 表示不限制）
	MaxConcurrency int64
}

// TokenBucket 标准令牌桶实现
// 算法：每秒以 rate 速率补充令牌，桶容量上限为 burst。
// 每个请求消耗 1 个令牌，令牌不足则拒绝。
type TokenBucket struct {
	mu        sync.Mutex
	tokens    float64   // 当前桶内令牌数
	rate      float64   // 每秒补充速率
	burst     int64     // 桶容量上限
	lastRefil time.Time // 上次令牌补充时间
}

// NewTokenBucket 创建令牌桶
func NewTokenBucket(rate float64, burst int64) *TokenBucket {
	return &TokenBucket{
		tokens:    float64(burst), // 初始满桶
		rate:      rate,
		burst:     burst,
		lastRefil: time.Now(),
	}
}

// Allow 尝试获取一个令牌，返回是否允许通过
func (tb *TokenBucket) Allow() bool {
	tb.mu.Lock()
	defer tb.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(tb.lastRefil).Seconds()
	tb.lastRefil = now

	// 按时间补充令牌
	tb.tokens += elapsed * tb.rate
	if tb.tokens > float64(tb.burst) {
		tb.tokens = float64(tb.burst)
	}

	// 尝试消耗一个令牌
	if tb.tokens >= 1.0 {
		tb.tokens -= 1.0
		return true
	}
	return false
}

// Available 返回当前可用令牌数（用于统计）
func (tb *TokenBucket) Available() float64 {
	tb.mu.Lock()
	defer tb.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(tb.lastRefil).Seconds()
	tokens := tb.tokens + elapsed*tb.rate
	return math.Min(tokens, float64(tb.burst))
}

// SRLStats 记录 SRL 网关的运行统计
type SRLStats struct {
	TotalRequests    int64 // 总请求数
	SuccessRequests  int64 // 成功处理的请求数
	RejectedRequests int64 // 被限流拒绝的请求数
	ErrorRequests    int64 // 后端错误的请求数
	startTime        time.Time
}

// NewSRLGateway 创建静态限流网关实例
//
// 参数说明：
//   - config.QPS: 建议设置为 DP 在同等负载下的平均通过 QPS
//     例如：如果 DP 在 Poisson heavy_ratio=0.3 下平均通过 50 req/s，SRL 也设 50
//   - config.BurstSize: 建议设为 QPS 的 2-3 倍，允许适度突发
//   - config.MaxConcurrency: 建议设为 CPU 核数 × 2-4
func NewSRLGateway(nodeName string, config SRLConfig) *SRLGateway {
	if config.QPS <= 0 {
		config.QPS = 50 // 默认 50 QPS
	}
	if config.BurstSize <= 0 {
		config.BurstSize = int64(config.QPS) * 2 // 默认 burst = 2×QPS
	}

	return &SRLGateway{
		nodeName:   nodeName,
		tools:      make(map[string]mcpgov.MCPTool),
		handlers:   make(map[string]mcpgov.ToolCallHandler),
		serverInfo: mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		bucket:     NewTokenBucket(config.QPS, config.BurstSize),
		maxConcurrency: config.MaxConcurrency,
		stats: SRLStats{
			startTime: time.Now(),
		},
	}
}

// RegisterTool 注册工具（与 MCPServer 接口一致）
func (srl *SRLGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	srl.tools[tool.Name] = tool
	srl.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
func (srl *SRLGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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
		resp = srl.handleInitialize(ctx, &req)
	case "tools/list":
		resp = srl.handleToolsList(ctx, &req)
	case "tools/call":
		resp = srl.handleToolsCall(ctx, &req)
	case "ping":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	writeJSON(w, resp)
}

// handleInitialize MCP 初始化握手
func (srl *SRLGateway) handleInitialize(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	result := map[string]interface{}{
		"protocolVersion": "2024-11-05",
		"serverInfo":      srl.serverInfo,
		"capabilities": map[string]interface{}{
			"tools": map[string]interface{}{"listChanged": false},
		},
	}
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// handleToolsList 返回可用工具列表
func (srl *SRLGateway) handleToolsList(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	tools := make([]mcpgov.MCPTool, 0, len(srl.tools))
	for _, tool := range srl.tools {
		tools = append(tools, tool)
	}
	return mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{"tools": tools})
}

// handleToolsCall 静态限流的工具调用处理
// 核心行为：
//  1. 令牌桶检查（QPS 限流）—— 不区分请求类型或预算
//  2. 并发数检查（可选）—— 不区分轻量/重量请求
//  3. 通过检查后直接调用后端工具
//
// 与 DP 的关键差异：
//   - SRL 对轻量和重量请求使用相同的限流阈值（一刀切）
//   - SRL 不感知请求的 budget/tokens（忽略 _meta.tokens）
//   - SRL 的拒绝率与请求类型无关（随机公平，但非经济公平）
func (srl *SRLGateway) handleToolsCall(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	// 1. 解析请求参数
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, -32602, "无效的工具调用参数", err.Error())
	}

	// 2. 查找工具处理器
	handler, ok := srl.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 3. 统计：记录总请求数
	atomic.AddInt64(&srl.stats.TotalRequests, 1)

	// 4. 令牌桶限流检查 —— 核心逻辑
	// 注意：不区分 params.Name（轻量/重量），不区分 params.Meta.Tokens（预算高低）
	if !srl.bucket.Allow() {
		atomic.AddInt64(&srl.stats.RejectedRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32002,
			fmt.Sprintf("工具 %s 被静态限流拒绝，当前 QPS 超过上限。", params.Name),
			map[string]string{"price": "rate_limited", "name": srl.nodeName})
	}

	// 5. 并发数限流检查（可选）
	if srl.maxConcurrency > 0 {
		current := atomic.AddInt64(&srl.activeCalls, 1)
		defer atomic.AddInt64(&srl.activeCalls, -1)

		if current > srl.maxConcurrency {
			atomic.AddInt64(&srl.stats.RejectedRequests, 1)
			return mcpgov.NewErrorResponse(req.ID, -32002,
				fmt.Sprintf("工具 %s 被并发限流拒绝，当前并发数 %d 超过上限 %d。",
					params.Name, current, srl.maxConcurrency),
				map[string]string{"price": "concurrency_limited", "name": srl.nodeName})
		}
	}

	// 6. 调用实际的工具处理函数
	result, err := handler(ctx, params)
	if err != nil {
		atomic.AddInt64(&srl.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	// 7. 统计：记录成功请求数
	atomic.AddInt64(&srl.stats.SuccessRequests, 1)

	// 8. 在 _meta 中返回限流状态信息，保持协议格式一致
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Price = "0" // SRL 无动态定价，价格恒为 0
	result.Meta.Name = srl.nodeName

	return mcpgov.NewSuccessResponse(req.ID, result)
}

// GetStats 获取当前统计数据
func (srl *SRLGateway) GetStats() (total, success, rejected, errors int64) {
	return atomic.LoadInt64(&srl.stats.TotalRequests),
		atomic.LoadInt64(&srl.stats.SuccessRequests),
		atomic.LoadInt64(&srl.stats.RejectedRequests),
		atomic.LoadInt64(&srl.stats.ErrorRequests)
}

// GetBucketAvailable 返回令牌桶当前可用令牌数（用于监控）
func (srl *SRLGateway) GetBucketAvailable() float64 {
	return srl.bucket.Available()
}
