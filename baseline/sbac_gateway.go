// sbac_gateway.go
// SBAC (Session-Based Admission Control) — MCP 多步 DAG 场景的经典会话并发控制基线
//
// 核心机制（工业界/学术界处理有状态多步调用的最经典手段）：
//   - 不限制 QPS，而是严格限制全局最大并发会话数（maxSessions）
//   - Step 0 (X-Plan-DAG)：如果 activeSessions < maxSessions → 准入, activeSessions++
//                         否则直接拒绝（Reject at S0），不截断进行中的会话
//   - Step N (X-Session-ID)：无条件放行，保证已准入的会话不被夭折
//   - 会话结束（全部步骤完成或超时）：activeSessions--
//
// 与 PlanGate 的核心差异（学术价值所在）：
//   - SBAC 是"瞎子"——它不知道不同 Session 的价值（budget）
//   - 没有动态定价：一个 budget=1 的 Session 和 budget=1000 的 Session 待遇完全相同
//   - PlanGate 通过动态定价，在拥塞时筛选高价值任务，Effective Goodput 碾压 SBAC
//   - SBAC 优于 SRL/Rajomon：完全无级联失败（保护进行中会话）
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

// SBACConfig SBAC-MCP 网关配置
type SBACConfig struct {
	// MaxSessions 最大并发会话数（唯一关键参数）
	MaxSessions int64
	// SessionTimeout 会话超时时间（防止 slot 泄露）
	SessionTimeout time.Duration
}

// sbacSession 记录一个 MCP 会话状态
type sbacSession struct {
	totalSteps     int
	completedSteps int
	lastActivity   time.Time
}

// SBACGateway Session-Based Admission Control 网关
type SBACGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	maxSessions    int64 // 最大并发会话数（配置固定值）
	activeSessions int64 // 当前活跃会话数（atomic）

	// Session 注册表
	sessions       map[string]*sbacSession
	sessionsMu     sync.Mutex
	sessionTimeout time.Duration

	stats SBACStats
}

// SBACStats 统计
type SBACStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64
	ErrorRequests    int64
	startTime        time.Time
}

// NewSBACGateway 创建 SBAC-MCP 网关实例
func NewSBACGateway(nodeName string, config SBACConfig) *SBACGateway {
	if config.MaxSessions <= 0 {
		config.MaxSessions = 50
	}
	if config.SessionTimeout <= 0 {
		config.SessionTimeout = 5 * time.Minute
	}

	gw := &SBACGateway{
		nodeName:       nodeName,
		tools:          make(map[string]mcpgov.MCPTool),
		handlers:       make(map[string]mcpgov.ToolCallHandler),
		serverInfo:     mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		maxSessions:    config.MaxSessions,
		activeSessions: 0,
		sessions:       make(map[string]*sbacSession),
		sessionTimeout: config.SessionTimeout,
		stats:          SBACStats{startTime: time.Now()},
	}

	go gw.sessionCleanupLoop()
	return gw
}

// RegisterTool 注册工具
func (gw *SBACGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
func (gw *SBACGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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

// handleToolsCall SBAC 会话准入控制
//
// Step 0 (X-Plan-DAG)：
//   - 原子检查 activeSessions < maxSessions → 准入 (activeSessions++)
//   - 否则拒绝（Reject at S0）—— 不影响已运行的 Session
//
// Step N (X-Session-ID)：
//   - 已注册 Session → 无条件放行（保证零级联失败）
//   - 未知 Session → 拒绝（超时孤儿）
func (gw *SBACGateway) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
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

	if planDAGHeader != "" {
		// ── Step 0：会话准入检查 ──────────────────────────────────────────────
		var plan struct {
			SessionID string        `json:"session_id"`
			Steps     []interface{} `json:"steps"`
		}
		if err := json.Unmarshal([]byte(planDAGHeader), &plan); err != nil {
			return mcpgov.NewErrorResponse(req.ID, -32602, "X-Plan-DAG 解析失败", err.Error())
		}
		sessID := plan.SessionID
		if sessID == "" {
			sessID = sessionIDHeader
		}

		// 原子检查并占用 Session Slot
		for {
			cur := atomic.LoadInt64(&gw.activeSessions)
			if cur >= gw.maxSessions {
				// Slot 耗尽 → 拒绝（Reject at S0）
				atomic.AddInt64(&gw.stats.RejectedRequests, 1)
				return mcpgov.NewErrorResponse(req.ID, -32001,
					fmt.Sprintf("SBAC: 并发会话已满 (active=%d, max=%d)，Session 被 %s 拒绝",
						cur, gw.maxSessions, gw.nodeName),
					map[string]string{
						"price": strconv.FormatInt(gw.maxSessions, 10),
						"name":  gw.nodeName,
					})
			}
			if atomic.CompareAndSwapInt64(&gw.activeSessions, cur, cur+1) {
				break
			}
		}

		// 注册 Session
		totalSteps := len(plan.Steps)
		if totalSteps <= 0 {
			totalSteps = 1
		}
		gw.sessionsMu.Lock()
		gw.sessions[sessID] = &sbacSession{
			totalSteps:   totalSteps,
			lastActivity: time.Now(),
		}
		gw.sessionsMu.Unlock()

		return gw.execTool(ctx, req, handler, sessID)

	} else if sessionIDHeader != "" {
		// ── Step N 或 ReAct 首步：已准入 Session 无条件放行 / 新 Session 动态准入 ──
		gw.sessionsMu.Lock()
		sess, exists := gw.sessions[sessionIDHeader]
		if exists {
			sess.lastActivity = time.Now()
		}
		gw.sessionsMu.Unlock()

		if !exists {
			// ReAct 模式首步：无 X-Plan-DAG 但有 X-Session-ID → 尝试准入新 Session
			for {
				cur := atomic.LoadInt64(&gw.activeSessions)
				if cur >= gw.maxSessions {
					atomic.AddInt64(&gw.stats.RejectedRequests, 1)
					return mcpgov.NewErrorResponse(req.ID, -32001,
						fmt.Sprintf("SBAC: 并发会话已满 (active=%d, max=%d)", cur, gw.maxSessions),
						map[string]string{"price": strconv.FormatInt(gw.maxSessions, 10), "name": gw.nodeName})
				}
				if atomic.CompareAndSwapInt64(&gw.activeSessions, cur, cur+1) {
					break
				}
			}
			// 注册 ReAct Session（步数未知，设默认 5 步防 slot 泄漏）
			gw.sessionsMu.Lock()
			gw.sessions[sessionIDHeader] = &sbacSession{
				totalSteps:   5,
				lastActivity: time.Now(),
			}
			gw.sessionsMu.Unlock()
			return gw.execTool(ctx, req, handler, sessionIDHeader)
		}
		return gw.execTool(ctx, req, handler, sessionIDHeader)

	} else {
		// ── 无 Session 头：按单次请求处理，不消耗 Session slot ──────────────
		return gw.execToolNoSession(ctx, req, handler)
	}
}

// execTool 执行工具并追踪 Session 步骤进度
func (gw *SBACGateway) execTool(ctx context.Context, req *mcpgov.JSONRPCRequest, handler mcpgov.ToolCallHandler, sessID string) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	_ = json.Unmarshal(req.Params, &params)

	result, err := handler(ctx, params)

	// 记录步骤，全部完成时释放 Session slot
	gw.recordStepDone(sessID)

	if err != nil {
		atomic.AddInt64(&gw.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	atomic.AddInt64(&gw.stats.SuccessRequests, 1)
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	active := atomic.LoadInt64(&gw.activeSessions)
	result.Meta.Price = strconv.FormatInt(active, 10)
	result.Meta.Name = gw.nodeName
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// execToolNoSession 无 Session 上下文的工具调用（不消耗 slot）
func (gw *SBACGateway) execToolNoSession(ctx context.Context, req *mcpgov.JSONRPCRequest, handler mcpgov.ToolCallHandler) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	_ = json.Unmarshal(req.Params, &params)

	result, err := handler(ctx, params)
	if err != nil {
		atomic.AddInt64(&gw.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	atomic.AddInt64(&gw.stats.SuccessRequests, 1)
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Price = "0"
	result.Meta.Name = gw.nodeName
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// recordStepDone 记录步骤完成，Session 全部步骤完成时释放 slot
func (gw *SBACGateway) recordStepDone(sessID string) {
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
		atomic.AddInt64(&gw.activeSessions, -1)
	}
}

// sessionCleanupLoop 定期回收超时会话的 Session slot
func (gw *SBACGateway) sessionCleanupLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		now := time.Now()
		gw.sessionsMu.Lock()
		for id, sess := range gw.sessions {
			if now.Sub(sess.lastActivity) > gw.sessionTimeout {
				delete(gw.sessions, id)
				atomic.AddInt64(&gw.activeSessions, -1)
			}
		}
		gw.sessionsMu.Unlock()
	}
}
