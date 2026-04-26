// rajomon_session_gateway.go
// Rajomon + Session Bookkeeping (Raj+SB) 基线网关实现
//
// 目的: 证明"在 per-request 定价上加会话跟踪"不够——核心创新在于定价公式和预算预留。
//
// 核心机制:
//   - 定价: 完全复用 Rajomon 的 Token-Price 市场机制 (per-request, 无 session 感知)
//   - 会话跟踪: 解析 X-Session-ID / X-Plan-DAG, 维护 sessions map
//   - 准入: 仍使用 Rajomon 原始 per-request 定价 (tokens < price → reject)
//   - 不区分 step 0 和 step K: 每一步都用同一个价格判定
//
// 与 PlanGate 的关键区别:
//   - 有会话跟踪，但定价不感知步骤进度 (无 continuation value)
//   - 无预算预留 (无 temporal isolation, 价格随时波动)
//   - 无沉没成本折扣 (无 sunk-cost discount)
//   - 无 step-0 原子准入 (无 atomic admission)
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

// RajomonSessionConfig Rajomon+SB 网关配置
type RajomonSessionConfig struct {
	// RajomonConfig 嵌入 Rajomon 定价配置
	RajomonConfig
	// SessionTimeout 会话超时 (防止 slot 泄漏)
	SessionTimeout time.Duration
}

// rajomonSessionInfo 记录一个会话的状态 (仅用于跟踪，不影响定价)
type rajomonSessionInfo struct {
	totalSteps     int
	completedSteps int
	lastActivity   time.Time
}

// RajomonSessionGateway Rajomon + Session Bookkeeping 基线网关
type RajomonSessionGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	// 嵌入 Rajomon 定价核心
	rajomon *RajomonGateway

	// 会话跟踪 (仅记录，不影响定价决策)
	sessions       map[string]*rajomonSessionInfo
	sessionsMu     sync.Mutex
	sessionTimeout time.Duration

	stats RJSBStats
}

// RJSBStats 统计
type RJSBStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64
	ErrorRequests    int64
	startTime        time.Time
}

// NewRajomonSessionGateway 创建 Rajomon+SB 网关
func NewRajomonSessionGateway(nodeName string, config RajomonSessionConfig) *RajomonSessionGateway {
	if config.SessionTimeout <= 0 {
		config.SessionTimeout = 5 * time.Minute
	}

	// 创建内部 Rajomon 实例用于定价逻辑
	rajomon := NewRajomonGateway(nodeName+"-pricing", config.RajomonConfig)

	gw := &RajomonSessionGateway{
		nodeName:       nodeName,
		tools:          make(map[string]mcpgov.MCPTool),
		handlers:       make(map[string]mcpgov.ToolCallHandler),
		serverInfo:     mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		rajomon:        rajomon,
		sessions:       make(map[string]*rajomonSessionInfo),
		sessionTimeout: config.SessionTimeout,
		stats:          RJSBStats{startTime: time.Now()},
	}

	go gw.sessionCleanupLoop()
	return gw
}

// RegisterTool 注册工具 (同时注册到内部 Rajomon 实例)
func (gw *RajomonSessionGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
	gw.rajomon.RegisterTool(tool, handler)
}

// ServeHTTP 实现 http.Handler 接口
func (gw *RajomonSessionGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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

// handleToolsCall Rajomon+SB 工具调用处理
// 定价: 使用 Rajomon 的 per-request Token-Price (不区分 step 0/N)
// 会话: 解析 header 记录会话状态 (不影响定价)
func (gw *RajomonSessionGateway) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
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

	// ── Rajomon per-request 定价准入 ──
	var tokens int64
	if params.Meta != nil {
		tokens = params.Meta.Tokens
	}
	price := gw.rajomon.GetPrice()

	if tokens < price {
		atomic.AddInt64(&gw.stats.RejectedRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32001,
			fmt.Sprintf("Rajomon+SB: tokens 不足 (tokens=%d, price=%d)，请求被 %s 拒绝",
				tokens, price, gw.nodeName),
			map[string]string{
				"price": strconv.FormatInt(price, 10),
				"name":  gw.nodeName,
			})
	}

	// ── Session Bookkeeping (记录但不影响定价) ──
	sessID := ""
	planDAGHeader := r.Header.Get("X-Plan-DAG")
	sessionIDHeader := r.Header.Get("X-Session-ID")

	if planDAGHeader != "" {
		// Step 0: 从 X-Plan-DAG 注册新会话
		var plan struct {
			SessionID string        `json:"session_id"`
			Steps     []interface{} `json:"steps"`
		}
		if err := json.Unmarshal([]byte(planDAGHeader), &plan); err == nil {
			sessID = plan.SessionID
			if sessID == "" {
				sessID = sessionIDHeader
			}
			totalSteps := len(plan.Steps)
			if totalSteps <= 0 {
				totalSteps = 1
			}
			gw.sessionsMu.Lock()
			gw.sessions[sessID] = &rajomonSessionInfo{
				totalSteps:   totalSteps,
				lastActivity: time.Now(),
			}
			gw.sessionsMu.Unlock()
		}
	} else if sessionIDHeader != "" {
		sessID = sessionIDHeader
		gw.sessionsMu.Lock()
		sess, exists := gw.sessions[sessID]
		if exists {
			sess.lastActivity = time.Now()
		} else {
			// ReAct 首步: 注册新会话
			gw.sessions[sessID] = &rajomonSessionInfo{
				totalSteps:   5,
				lastActivity: time.Now(),
			}
		}
		gw.sessionsMu.Unlock()
	}

	// ── 调用工具 ──
	callStart := time.Now()
	result, err := handler(ctx, params)
	callDuration := time.Since(callStart)

	// 追踪响应延迟到内部 Rajomon 实例
	atomic.AddInt64(&gw.rajomon.rttSumNs, int64(callDuration))
	atomic.AddInt64(&gw.rajomon.rttCount, 1)

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
	result.Meta.Price = strconv.FormatInt(price, 10)
	result.Meta.Name = gw.nodeName
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// recordStepDone 记录步骤完成, 超出总步数时清理
func (gw *RajomonSessionGateway) recordStepDone(sessID string) {
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

// sessionCleanupLoop 定期回收超时会话
func (gw *RajomonSessionGateway) sessionCleanupLoop() {
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
