// progress_priority_gateway.go
// Progress-Priority (PP) — 基于进度优先级的会话准入控制基线
//
// 核心机制：
//   - 限制全局最大并发会话数（maxSessions）
//   - 满额时：比较新请求与在场会话的进度分数（completedSteps / totalSteps）
//     → 如果新请求来自更高进度的会话 → 驱逐最低进度会话，接纳当前
//     → 否则拒绝当前请求
//   - 未满额时：直接接纳
//
// 与 PlanGate 的核心差异（对照实验价值）：
//   - 无预算预留（no temporal isolation）：价格随时波动，已接纳会话无保护
//   - 无沉没成本折扣（no continuation pricing）：不降低高进度会话的准入门槛
//   - 无 step-0 原子准入（no atomic admission）：首步不做完整性检查
//   - 仅有 progress-based 抢占：偏袒已走得远的会话，但不承诺
//
// 预期实验结果：
//   - cascade_failed > 0：因为被驱逐的低进度会话会级联失败
//   - admitted-but-doomed rate 远高于 PlanGate：进度优先不等于承诺
//   - 证明 "progress favoritism ≠ continuation value"
package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// PPConfig Progress-Priority 网关配置
type PPConfig struct {
	// MaxSessions 最大并发会话数
	MaxSessions int64
	// SessionTimeout 会话超时时间（防止 slot 泄露）
	SessionTimeout time.Duration
}

// ppSession 记录一个 MCP 会话状态
type ppSession struct {
	mu             sync.Mutex
	totalSteps     int
	completedSteps int
	lastActivity   time.Time
	evicted        bool // 标记已被驱逐
}

func (s *ppSession) progressScore() float64 {
	if s.totalSteps <= 0 {
		return 0
	}
	return float64(s.completedSteps) / float64(s.totalSteps)
}

// PPGateway Progress-Priority Admission Control 网关
type PPGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation

	maxSessions    int64 // 最大并发会话数
	activeSessions int64 // 当前活跃会话数（atomic）

	sessions       map[string]*ppSession
	sessionsMu     sync.Mutex
	sessionTimeout time.Duration

	stats PPStats
}

// PPStats 统计
type PPStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64
	ErrorRequests    int64
	EvictedSessions  int64
	startTime        time.Time
}

// NewPPGateway 创建 Progress-Priority 网关实例
func NewPPGateway(nodeName string, config PPConfig) *PPGateway {
	if config.MaxSessions <= 0 {
		config.MaxSessions = 50
	}
	if config.SessionTimeout <= 0 {
		config.SessionTimeout = 5 * time.Minute
	}

	gw := &PPGateway{
		nodeName:       nodeName,
		tools:          make(map[string]mcpgov.MCPTool),
		handlers:       make(map[string]mcpgov.ToolCallHandler),
		serverInfo:     mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		maxSessions:    config.MaxSessions,
		activeSessions: 0,
		sessions:       make(map[string]*ppSession),
		sessionTimeout: config.SessionTimeout,
		stats:          PPStats{startTime: time.Now()},
	}

	go gw.sessionCleanupLoop()
	return gw
}

// RegisterTool 注册工具
func (gw *PPGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler 接口
func (gw *PPGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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

// handleToolsCall PP 准入控制
//
// Step 0 (X-Plan-DAG)：
//   - 如果 activeSessions < maxSessions → 直接接纳
//   - 否则: 尝试驱逐最低进度会话
//
// Step N (X-Session-ID)：
//   - 已注册且未被驱逐的 Session → 放行
//   - 已被驱逐的 Session → 拒绝（级联失败）
//   - 未知 Session → 按 ReAct 首步处理
func (gw *PPGateway) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
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
		// ── Step 0：P&S 会话准入 ──────────────────────────────────────
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

		totalSteps := len(plan.Steps)
		if totalSteps <= 0 {
			totalSteps = 1
		}

		admitted := gw.tryAdmit(sessID, totalSteps, 0)
		if !admitted {
			atomic.AddInt64(&gw.stats.RejectedRequests, 1)
			return mcpgov.NewErrorResponse(req.ID, -32001,
				fmt.Sprintf("PP: 会话准入失败 (active=%d, max=%d)，进度不足以驱逐现有会话",
					atomic.LoadInt64(&gw.activeSessions), gw.maxSessions),
				map[string]string{"price": strconv.FormatInt(gw.maxSessions, 10), "name": gw.nodeName})
		}

		return gw.execTool(ctx, req, handler, sessID)

	} else if sessionIDHeader != "" {
		// ── Step N 或 ReAct 首步 ──────────────────────────────────────
		gw.sessionsMu.Lock()
		sess, exists := gw.sessions[sessionIDHeader]
		if exists {
			sess.mu.Lock()
			evicted := sess.evicted
			sess.lastActivity = time.Now()
			sess.mu.Unlock()
			gw.sessionsMu.Unlock()

			if evicted {
				// 已被驱逐 → 级联失败
				atomic.AddInt64(&gw.stats.RejectedRequests, 1)
				return mcpgov.NewErrorResponse(req.ID, -32001,
					fmt.Sprintf("PP: 会话 %s 已被更高进度会话驱逐", sessionIDHeader),
					map[string]string{"price": strconv.FormatInt(gw.maxSessions, 10), "name": gw.nodeName})
			}
			return gw.execTool(ctx, req, handler, sessionIDHeader)
		}
		gw.sessionsMu.Unlock()

		// ReAct 模式首步：尝试按新会话准入（初始进度 0，默认 5 步）
		admitted := gw.tryAdmit(sessionIDHeader, 5, 0)
		if !admitted {
			atomic.AddInt64(&gw.stats.RejectedRequests, 1)
			return mcpgov.NewErrorResponse(req.ID, -32001,
				fmt.Sprintf("PP: 并发会话已满 (active=%d, max=%d)", atomic.LoadInt64(&gw.activeSessions), gw.maxSessions),
				map[string]string{"price": strconv.FormatInt(gw.maxSessions, 10), "name": gw.nodeName})
		}
		return gw.execTool(ctx, req, handler, sessionIDHeader)

	} else {
		// ── 无 Session 头：按单次请求处理 ─────────────────────────────
		return gw.execToolNoSession(ctx, req, handler)
	}
}

// tryAdmit 尝试准入会话。如果容量满，找最低进度会话比较后决定是否驱逐。
// 返回 true 表示准入成功。
func (gw *PPGateway) tryAdmit(sessID string, totalSteps int, completedSteps int) bool {
	// 快速路径：有空位则直接准入
	for {
		cur := atomic.LoadInt64(&gw.activeSessions)
		if cur >= gw.maxSessions {
			break // 需要走驱逐路径
		}
		if atomic.CompareAndSwapInt64(&gw.activeSessions, cur, cur+1) {
			gw.sessionsMu.Lock()
			gw.sessions[sessID] = &ppSession{
				totalSteps:     totalSteps,
				completedSteps: completedSteps,
				lastActivity:   time.Now(),
			}
			gw.sessionsMu.Unlock()
			return true
		}
	}

	// 满额路径：尝试驱逐最低进度会话
	newProgress := float64(0)
	if totalSteps > 0 {
		newProgress = float64(completedSteps) / float64(totalSteps)
	}

	gw.sessionsMu.Lock()
	defer gw.sessionsMu.Unlock()

	// 找到进度最低的会话
	var lowestID string
	lowestProgress := math.MaxFloat64
	for id, sess := range gw.sessions {
		sess.mu.Lock()
		if sess.evicted {
			sess.mu.Unlock()
			continue
		}
		p := sess.progressScore()
		sess.mu.Unlock()
		if p < lowestProgress {
			lowestProgress = p
			lowestID = id
		}
	}

	if lowestID == "" {
		return false // 没有可驱逐的会话
	}

	// 只有新会话的进度严格高于最低进度时才驱逐
	// 首步（completedSteps=0）的新会话永远无法驱逐已有进度的会话
	if newProgress <= lowestProgress {
		return false
	}

	// 驱逐最低进度会话
	if victim, ok := gw.sessions[lowestID]; ok {
		victim.mu.Lock()
		victim.evicted = true
		victim.mu.Unlock()
		atomic.AddInt64(&gw.stats.EvictedSessions, 1)
		// 不减 activeSessions——驱逐出的 slot 被新会话占用
	}

	// 注册新会话
	gw.sessions[sessID] = &ppSession{
		totalSteps:     totalSteps,
		completedSteps: completedSteps,
		lastActivity:   time.Now(),
	}

	return true
}

// execTool 执行工具并追踪 Session 步骤进度
func (gw *PPGateway) execTool(ctx context.Context, req *mcpgov.JSONRPCRequest, handler mcpgov.ToolCallHandler, sessID string) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	_ = json.Unmarshal(req.Params, &params)

	result, err := handler(ctx, params)

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
func (gw *PPGateway) execToolNoSession(ctx context.Context, req *mcpgov.JSONRPCRequest, handler mcpgov.ToolCallHandler) *mcpgov.JSONRPCResponse {
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
func (gw *PPGateway) recordStepDone(sessID string) {
	gw.sessionsMu.Lock()
	defer gw.sessionsMu.Unlock()

	sess, ok := gw.sessions[sessID]
	if !ok {
		return
	}
	sess.mu.Lock()
	defer sess.mu.Unlock()

	sess.completedSteps++
	sess.lastActivity = time.Now()

	if sess.completedSteps >= sess.totalSteps {
		delete(gw.sessions, sessID)
		atomic.AddInt64(&gw.activeSessions, -1)
	}
}

// sessionCleanupLoop 定期回收超时会话和已驱逐会话的 Session slot
func (gw *PPGateway) sessionCleanupLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		now := time.Now()
		gw.sessionsMu.Lock()
		for id, sess := range gw.sessions {
			sess.mu.Lock()
			expired := now.Sub(sess.lastActivity) > gw.sessionTimeout
			evicted := sess.evicted
			sess.mu.Unlock()

			if expired || evicted {
				delete(gw.sessions, id)
				if !evicted {
					// 只有非驱逐的超时会话才需要减 activeSessions
					// 被驱逐的会话的 slot 已被新会话占用
					atomic.AddInt64(&gw.activeSessions, -1)
				}
			}
		}
		gw.sessionsMu.Unlock()
	}
}
