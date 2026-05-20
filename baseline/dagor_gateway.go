// dagor_gateway.go
// DAGOR (SoCC'18) — 适配 MCP 多步 DAG 场景的优先级过载控制基线
//
// 核心机制 (参考微信 DAGOR 论文 "Overload Control for Scaling WeChat Microservices"):
//   - 为每个 Session 赋予"业务优先级"（= Session Budget）
//   - 当后端平均 RTT 超过阈值时，激活"优先级脱落 (Priority Shedding)"
//   - 优先级脱落规则：budget < priceThreshold 的请求被丢弃
//   - 重要：检查发生在每一步（Step 0 + 后续所有步骤），而非仅入口步
//
// 与 PlanGate 的核心差异（学术价值所在）：
//   - DAGOR 在中间步骤也会丢弃已运行中的低优先级 Session
//   - 导致"算力浪费"——前几步已消耗的计算无法收回
//   - PlanGate 仅在 Step 0 原子预检，一旦准入则全程保护，无中途截断
//
// 参数：
//   - rttThresholdMs:  RTT 过载检测阈值 (ms)，超过则激活优先级脱落
//   - priceStep:       过载时每轮 priceThreshold 增加量
//   - adjustInterval:  过载/恢复检测周期
package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// DagorConfig DAGOR-MCP 网关配置
type DagorConfig struct {
	// RTTThresholdMs 过载检测 RTT 阈值 (ms)；超过则激活优先级脱落
	RTTThresholdMs float64
	// PriceStep 过载时优先级门槛每轮增加量（= 单位 budget）
	PriceStep int64
	// AdjustInterval 检测与调整周期
	AdjustInterval time.Duration
	// SessionTimeout 会话超时（清理孤儿记录）
	SessionTimeout time.Duration
}

// dagorSession 记录一个 MCP 会话的状态
type dagorSession struct {
	budget         int64
	totalSteps     int
	completedSteps int
	lastActivity   time.Time
}

// DagorGateway DAGOR-MCP 优先级过载控制网关
type DagorGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	// 优先级门槛（动态调整）
	// 请求 budget < priceThreshold 时在过载状态下被丢弃
	priceThreshold int64 // atomic

	// 过载检测（基于响应 RTT）
	rttSumNs       int64   // atomic: 当前窗口请求延迟总和 (ns)
	rttCount       int64   // atomic: 当前窗口请求数
	rttThresholdMs float64 // 过载判定 RTT 阈值 (ms)

	priceStep      int64
	adjustInterval time.Duration
	sessionTimeout time.Duration

	// Session 注册表
	sessions   map[string]*dagorSession
	sessionsMu sync.Mutex

	stats DagorStats
}

// DagorStats 统计
type DagorStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64 // 包含 Step 0 丢弃 + 中途级联丢弃
	ErrorRequests    int64
	startTime        time.Time
}

// NewDagorGateway 创建 DAGOR-MCP 网关实例
func NewDagorGateway(nodeName string, config DagorConfig) *DagorGateway {
	if config.RTTThresholdMs <= 0 {
		config.RTTThresholdMs = 200.0
	}
	if config.PriceStep <= 0 {
		config.PriceStep = 50
	}
	if config.AdjustInterval <= 0 {
		config.AdjustInterval = 50 * time.Millisecond
	}
	if config.SessionTimeout <= 0 {
		config.SessionTimeout = 5 * time.Minute
	}

	gw := &DagorGateway{
		nodeName:       nodeName,
		tools:          make(map[string]mcpgov.MCPTool),
		handlers:       make(map[string]mcpgov.ToolCallHandler),
		serverInfo:     mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		priceThreshold: 0,
		rttThresholdMs: config.RTTThresholdMs,
		priceStep:      config.PriceStep,
		adjustInterval: config.AdjustInterval,
		sessionTimeout: config.SessionTimeout,
		sessions:       make(map[string]*dagorSession),
		stats:          DagorStats{startTime: time.Now()},
	}

	go gw.adjustLoop()
	go gw.sessionCleanupLoop()
	return gw
}

// RegisterTool 注册工具
func (gw *DagorGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
func (gw *DagorGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	var req mcpgov.JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, mcpgov.NewErrorResponse(nil, -32700, "JSON 解析错误", err.Error()))
		return
	}

	if req.JSONRPC != "2.0" {
		writeJSON(w, mcpgov.NewErrorResponse(req.ID, -32600, "jsonrpc 版本必须为 2.0", nil))
		return
	}

	ctx := r.Context()
	var resp *mcpgov.JSONRPCResponse

	switch req.Method {
	case "initialize":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{
			"protocolVersion": "2024-11-05",
			"serverInfo":      gw.serverInfo,
			"capabilities":    map[string]interface{}{"tools": map[string]interface{}{"listChanged": false}},
		})
	case "tools/list":
		tools := make([]mcpgov.MCPTool, 0, len(gw.tools))
		for _, t := range gw.tools {
			tools = append(tools, t)
		}
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{"tools": tools})
	case "tools/call":
		resp = gw.handleToolsCall(ctx, r, &req)
	case "ping":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, -32601,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	writeJSON(w, resp)
}

// handleToolsCall DAGOR 优先级过载控制：每步都检查优先级
//
// 逻辑：
//  1. 读取 budget（从 X-Plan-DAG 或已注册 session）
//  2. 若系统过载 (priceThreshold > 0) 且 budget < priceThreshold → 拒绝（含中间步骤！）
//  3. 否则放行执行
//
// 与 PlanGate 的关键差异：Step 1..N 同样可能被丢弃 → 级联算力浪费
func (gw *DagorGateway) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
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

	planDAGHeader := r.Header.Get("X-Plan-DAG")
	sessionIDHeader := r.Header.Get("X-Session-ID")

	var budget int64
	var sessID string

	if planDAGHeader != "" {
		// Step 0：注册新 Session
		var plan struct {
			SessionID string        `json:"session_id"`
			Steps     []interface{} `json:"steps"`
			Budget    int64         `json:"budget"`
		}
		if err := json.Unmarshal([]byte(planDAGHeader), &plan); err != nil {
			return mcpgov.NewErrorResponse(req.ID, -32602, "X-Plan-DAG 解析失败", err.Error())
		}
		budget = plan.Budget
		sessID = plan.SessionID
		if sessID == "" {
			sessID = sessionIDHeader
		}

		// 注册 Session（无论是否准入）
		totalSteps := len(plan.Steps)
		if totalSteps <= 0 {
			totalSteps = 1
		}
		gw.sessionsMu.Lock()
		gw.sessions[sessID] = &dagorSession{
			budget:       budget,
			totalSteps:   totalSteps,
			lastActivity: time.Now(),
		}
		gw.sessionsMu.Unlock()

	} else if sessionIDHeader != "" {
		// 后续步骤：查找 budget
		gw.sessionsMu.Lock()
		sess, exists := gw.sessions[sessionIDHeader]
		if exists {
			budget = sess.budget
			sessID = sessionIDHeader
			sess.lastActivity = time.Now()
		}
		gw.sessionsMu.Unlock()

		if !exists {
			// 未知 Session：按 budget=0 处理（最低优先级）
			budget = 0
			sessID = sessionIDHeader
		}
	} else {
		// 无 Session 头：无法确定优先级，budget=0
		budget = 0
	}

	// ── 优先级脱落检查（DAGOR 核心）─────────────────────────────────────────
	// 关键：不论是 Step 0 还是后续步骤，只要 budget < priceThreshold 且系统过载，就拒绝
	threshold := atomic.LoadInt64(&gw.priceThreshold)
	if threshold > 0 && budget < threshold {
		atomic.AddInt64(&gw.stats.RejectedRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32001,
			fmt.Sprintf("DAGOR 优先级脱落: budget=%d < threshold=%d，请求被 %s 丢弃（包含中间步骤）",
				budget, threshold, gw.nodeName),
			map[string]string{
				"price": strconv.FormatInt(threshold, 10),
				"name":  gw.nodeName,
			})
	}

	// ── 执行工具 ─────────────────────────────────────────────────────────────
	callStart := time.Now()
	result, err := handler(ctx, params)
	callDuration := time.Since(callStart)

	// 追踪 RTT（用于过载检测）
	atomic.AddInt64(&gw.rttSumNs, int64(callDuration))
	atomic.AddInt64(&gw.rttCount, 1)

	// 记录步骤完成
	if sessID != "" {
		gw.recordStepDone(sessID)
	}

	if err != nil {
		atomic.AddInt64(&gw.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	atomic.AddInt64(&gw.stats.SuccessRequests, 1)
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Price = strconv.FormatInt(threshold, 10)
	result.Meta.Name = gw.nodeName
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// recordStepDone 记录步骤完成，所有步骤完成时清理 Session
func (gw *DagorGateway) recordStepDone(sessID string) {
	gw.sessionsMu.Lock()
	defer gw.sessionsMu.Unlock()
	sess, ok := gw.sessions[sessID]
	if !ok {
		return
	}
	sess.completedSteps++
	sess.lastActivity = time.Now()
	if sess.completedSteps >= sess.totalSteps {
		delete(gw.sessions, sessID)
	}
}

// adjustLoop 周期性根据平均 RTT 调整优先级门槛
// 过载：threshold += priceStep（拒绝更多低优先级请求）
// 正常：threshold -= priceStep（逐步放宽）
func (gw *DagorGateway) adjustLoop() {
	ticker := time.NewTicker(gw.adjustInterval)
	defer ticker.Stop()

	for range ticker.C {
		rttNs := atomic.SwapInt64(&gw.rttSumNs, 0)
		rttCnt := atomic.SwapInt64(&gw.rttCount, 0)
		avgRttMs := 0.0
		if rttCnt > 0 {
			avgRttMs = float64(rttNs) / float64(rttCnt) / 1e6
		}

		if avgRttMs > gw.rttThresholdMs {
			// 过载：提高门槛
			for {
				old := atomic.LoadInt64(&gw.priceThreshold)
				newVal := old + gw.priceStep
				if atomic.CompareAndSwapInt64(&gw.priceThreshold, old, newVal) {
					break
				}
			}
		} else {
			// 正常：逐步降低门槛
			for {
				old := atomic.LoadInt64(&gw.priceThreshold)
				if old <= 0 {
					break
				}
				newVal := old - gw.priceStep
				if newVal < 0 {
					newVal = 0
				}
				if atomic.CompareAndSwapInt64(&gw.priceThreshold, old, newVal) {
					break
				}
			}
		}
	}
}

// sessionCleanupLoop 定期清理超时会话
func (gw *DagorGateway) sessionCleanupLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		now := time.Now()
		gw.sessionsMu.Lock()
		for id, sess := range gw.sessions {
			if now.Sub(sess.lastActivity) > gw.sessionTimeout {
				delete(gw.sessions, id)
			}
		}
		gw.sessionsMu.Unlock()
	}
}
