// rajomon_gateway.go
// Rajomon (OSDI 2025) 基线网关实现
// 复现核心 Token-Price 市场机制，适配单机 MCP 场景。
//
// 核心算法：
//   - 每个工具有独立的"价格" (ownPrice)，由排队延迟驱动涨跌
//   - 客户端请求携带 tokens（预算），tokens < price 时拒绝
//   - 使用 maximal 聚合策略：totalPrice = max(ownPrice, downstreamPrice)
//   - 定价为步进策略 (step)：过载涨 priceStep，正常降 1
//
// 与 DP（你的方案）的关键差异：
//   - Rajomon 不做 Regime 自适应档位切换（使用固定参数）
//   - Rajomon 不做工具权重差异化（轻量/重量工具同价）
//   - Rajomon 使用固定的 priceStep 和 decayStep（无自适应）
//   - Rajomon 无积分项（integralThreshold=0）
//   - Rajomon 无平滑窗口（smoothingWindow=1）
//
// 公平性保证：
//   - 使用与 DP 相同的 JSON-RPC 2.0 + MCP 协议栈
//   - 使用与 DP 相同的 RegisterTool / http.Handler 接口
//   - 响应中携带 _meta（price 表示当前价格），保持协议格式一致
//   - 使用与 DP 相同的排队延迟采样方法（Go runtime metrics）
package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"runtime/metrics"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// RajomonConfig Rajomon 网关配置参数
type RajomonConfig struct {
	// InitialPrice 初始价格
	InitialPrice int64
	// PriceStep 过载时每周期涨价步长
	PriceStep int64
	// DecayStep 正常时每周期降价步长
	DecayStep int64
	// DelayThreshold 排队延迟阈值 (微秒)，超过此值涨价
	DelayThreshold time.Duration
	// PriceUpdateRate 价格更新周期
	PriceUpdateRate time.Duration
	// MaxPrice 价格上限（防止价格无限增长）
	MaxPrice int64
}

// RajomonGateway 基于 Token-Price 市场机制的网关 (Rajomon OSDI'25)
type RajomonGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	// 定价核心状态
	ownPrice       int64 // 当前自身价格（原子操作）
	priceStep      int64
	decayStep      int64
	delayThreshold time.Duration
	priceUpdateRate time.Duration
	maxPrice       int64

	// 排队延迟采样
	prevHist *metrics.Float64Histogram
	histMu   sync.Mutex

	// 响应延迟追踪（用于代理架构下的过载检测）
	rttSumNs  int64 // atomic: 当前窗口内请求延迟总和 (ns)
	rttCount  int64 // atomic: 当前窗口内请求个数
	rttThresholdMs float64 // 响应延迟过载阈值 (ms)

	// 统计指标
	stats RJStats
}

// RJStats 记录 Rajomon 网关运行统计
type RJStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64
	ErrorRequests    int64
	startTime        time.Time
}

// NewRajomonGateway 创建 Rajomon 网关实例
func NewRajomonGateway(nodeName string, config RajomonConfig) *RajomonGateway {
	if config.InitialPrice < 0 {
		config.InitialPrice = 0
	}
	if config.PriceStep <= 0 {
		config.PriceStep = 100
	}
	if config.DecayStep <= 0 {
		config.DecayStep = 1
	}
	if config.DelayThreshold <= 0 {
		config.DelayThreshold = 500 * time.Microsecond
	}
	if config.PriceUpdateRate <= 0 {
		config.PriceUpdateRate = 100 * time.Millisecond
	}
	if config.MaxPrice <= 0 {
		config.MaxPrice = 100000
	}

	gw := &RajomonGateway{
		nodeName:        nodeName,
		tools:           make(map[string]mcpgov.MCPTool),
		handlers:        make(map[string]mcpgov.ToolCallHandler),
		serverInfo:      mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		ownPrice:        config.InitialPrice,
		priceStep:       config.PriceStep,
		decayStep:       config.DecayStep,
		delayThreshold:  config.DelayThreshold,
		priceUpdateRate: config.PriceUpdateRate,
		maxPrice:        config.MaxPrice,
		rttThresholdMs:  500.0, // 代理架构下的响应延迟过载阈值
		stats: RJStats{
			startTime: time.Now(),
		},
	}

	// 启动后台价格调整协程
	go gw.priceAdjustLoop()

	return gw
}

// RegisterTool 注册工具（与 MCPServer 接口一致）
func (gw *RajomonGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
func (gw *RajomonGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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
		resp = gw.handleInitialize(ctx, &req)
	case "tools/list":
		resp = gw.handleToolsList(ctx, &req)
	case "tools/call":
		resp = gw.handleToolsCall(ctx, &req)
	case "ping":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	writeJSON(w, resp)
}

func (gw *RajomonGateway) handleInitialize(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	result := map[string]interface{}{
		"protocolVersion": "2024-11-05",
		"serverInfo":      gw.serverInfo,
		"capabilities": map[string]interface{}{
			"tools": map[string]interface{}{"listChanged": false},
		},
	}
	return mcpgov.NewSuccessResponse(req.ID, result)
}

func (gw *RajomonGateway) handleToolsList(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	tools := make([]mcpgov.MCPTool, 0, len(gw.tools))
	for _, tool := range gw.tools {
		tools = append(tools, tool)
	}
	return mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{"tools": tools})
}

// handleToolsCall 基于 Token-Price 的工具调用处理
// 核心行为：
//  1. 读取请求中的 tokens（预算）
//  2. 读取当前 ownPrice
//  3. tokens < price → 拒绝，并在 error.data 中返回当前价格
//  4. tokens ≥ price → 放行，调用工具
//
// 与 DP 的关键差异：
//   - 不区分工具类型（轻量/重量工具同一价格）
//   - 价格调整使用固定步长（无 Regime 自适应）
//   - 价格聚合使用 maximal 策略（无下游依赖时等于 ownPrice）
func (gw *RajomonGateway) handleToolsCall(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, -32602, "无效的工具调用参数", err.Error())
	}

	handler, ok := gw.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	atomic.AddInt64(&gw.stats.TotalRequests, 1)

	// 提取请求中的 tokens
	var tokens int64
	if params.Meta != nil {
		tokens = params.Meta.Tokens
	}

	// 读取当前价格
	price := atomic.LoadInt64(&gw.ownPrice)

	// Token-Price 准入控制
	if tokens < price {
		atomic.AddInt64(&gw.stats.RejectedRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32001,
			fmt.Sprintf("工具 %s 过载，Rajomon 令牌不足 (tokens=%d, price=%d)，请求被 %s 拒绝。",
				params.Name, tokens, price, gw.nodeName),
			map[string]string{
				"price": strconv.FormatInt(price, 10),
				"name":  gw.nodeName,
			})
	}

	// 调用工具并追踪响应延迟
	callStart := time.Now()
	result, err := handler(ctx, params)
	callDuration := time.Since(callStart)

	// 追踪响应延迟（用于过载检测）
	atomic.AddInt64(&gw.rttSumNs, int64(callDuration))
	atomic.AddInt64(&gw.rttCount, 1)

	if err != nil {
		atomic.AddInt64(&gw.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	atomic.AddInt64(&gw.stats.SuccessRequests, 1)

	// 在 _meta 中返回当前价格
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Price = strconv.FormatInt(price, 10)
	result.Meta.Name = gw.nodeName

	return mcpgov.NewSuccessResponse(req.ID, result)
}

// priceAdjustLoop 后台协程，周期性根据排队延迟调整 ownPrice
// 逻辑与 DP 的 queuingCheck 类似，但使用固定步长，无 Regime 切换
func (gw *RajomonGateway) priceAdjustLoop() {
	ticker := time.NewTicker(gw.priceUpdateRate)
	defer ticker.Stop()

	for range ticker.C {
		gapLatency := gw.sampleGapLatency()

		// 采样响应延迟（代理架构下的主要过载信号）
		rttNs := atomic.SwapInt64(&gw.rttSumNs, 0)
		rttCnt := atomic.SwapInt64(&gw.rttCount, 0)
		avgRttMs := 0.0
		if rttCnt > 0 {
			avgRttMs = float64(rttNs) / float64(rttCnt) / 1e6
		}

		// 双信号过载检测：调度延迟 OR 响应延迟超阈值
		// 将 gap latency (ms) 转换为微秒进行比较
		isOverloaded := int64(gapLatency*1000) > gw.delayThreshold.Microseconds() || avgRttMs > gw.rttThresholdMs

		if isOverloaded {
			// 过载：涨价
			for {
				old := atomic.LoadInt64(&gw.ownPrice)
				newPrice := old + gw.priceStep
				if newPrice > gw.maxPrice {
					newPrice = gw.maxPrice
				}
				if atomic.CompareAndSwapInt64(&gw.ownPrice, old, newPrice) {
					break
				}
			}
		} else {
			// 正常：降价
			for {
				old := atomic.LoadInt64(&gw.ownPrice)
				newPrice := old - gw.decayStep
				if newPrice < 0 {
					newPrice = 0
				}
				if atomic.CompareAndSwapInt64(&gw.ownPrice, old, newPrice) {
					break
				}
			}
		}
	}
}

// sampleGapLatency 采样 Go runtime 的排队延迟增量 (ms)
func (gw *RajomonGateway) sampleGapLatency() float64 {
	gw.histMu.Lock()
	defer gw.histMu.Unlock()

	currHist := readRuntimeHistogram()
	if gw.prevHist == nil {
		gw.prevHist = currHist
		return 0
	}

	gapLatency := maximumQueuingDelayMs(gw.prevHist, currHist)
	gw.prevHist = currHist
	return gapLatency
}

// GetStats 获取当前统计数据
func (gw *RajomonGateway) GetStats() (total, success, rejected, errors int64) {
	return atomic.LoadInt64(&gw.stats.TotalRequests),
		atomic.LoadInt64(&gw.stats.SuccessRequests),
		atomic.LoadInt64(&gw.stats.RejectedRequests),
		atomic.LoadInt64(&gw.stats.ErrorRequests)
}

// GetPrice 获取当前价格（用于监控）
func (gw *RajomonGateway) GetPrice() int64 {
	return atomic.LoadInt64(&gw.ownPrice)
}

// readRuntimeHistogram 读取 Go 调度器延迟直方图
func readRuntimeHistogram() *metrics.Float64Histogram {
	const queueingDelay = "/sched/latencies:seconds"
	sample := make([]metrics.Sample, 1)
	sample[0].Name = queueingDelay
	metrics.Read(sample)
	if sample[0].Value.Kind() == metrics.KindBad {
		return &metrics.Float64Histogram{}
	}
	h := sample[0].Value.Float64Histogram()
	return h
}

// maximumQueuingDelayMs 返回两次直方图快照之间出现的最大排队延迟 (ms)
func maximumQueuingDelayMs(earlier, later *metrics.Float64Histogram) float64 {
	if len(earlier.Counts) == 0 || len(later.Counts) == 0 {
		return 0
	}
	for i := len(earlier.Counts) - 1; i >= 0; i-- {
		if i < len(later.Counts) && later.Counts[i] > earlier.Counts[i] {
			return later.Buckets[i] * 1000 // 转毫秒
		}
	}
	return 0
}
