// mcp_dp_innovations.go
// MCP-DP 创新网关 — 在 MCPServer 基础上集成三大创新机制
//
// 创新点 1: Pre-flight Atomic Admission (DAG 预检准入)
//   - 客户端通过 X-Plan-DAG Header 提交完整 DAG 执行计划
//   - 网关在第 0 步原子性地计算全链路总价格并准入/拒绝
//   - 实现 "零级联算力浪费"
//
// 创新点 2: Budget Reservation (预算锁/远期价格锁定)
//   - 准入通过后为会话锁定当前价格快照
//   - 即使后续实时价格因拥塞暴涨，已准入会话仍按锁定价格结算
//   - 防止长链路 Agent 任务 "半路夭折"
//
// 创新点 3: Dual-Mode Governance (双模态异构治理)
//   - 有 X-Plan-DAG → Plan-and-Solve 模式 (创新点 1+2)
//   - 无 X-Plan-DAG → ReAct 模式 (标准 MCPGovernor 动态定价)
//
// HTTP 协议扩展:
//   - X-Plan-DAG:     完整 DAG JSON (首步携带)
//   - X-Session-ID:   会话 ID (后续步骤携带，用于查找预算锁)
//   - X-Total-Budget: 总预算 (首步携带，覆盖 DAG.Budget)
package mcpgov

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// HTTP Header 名称常量
const (
	HeaderPlanDAG     = "X-Plan-DAG"
	HeaderSessionID   = "X-Session-ID"
	HeaderTotalBudget = "X-Total-Budget"
)

// ==================== DAG 计划类型 (HTTP 变体) ====================

// HTTPDAGStep DAG 执行计划中的单个步骤
type HTTPDAGStep struct {
	StepID    string   `json:"step_id"`
	ToolName  string   `json:"tool_name"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// HTTPDAGPlan 完整的 DAG 执行计划
type HTTPDAGPlan struct {
	SessionID string        `json:"session_id"`
	Steps     []HTTPDAGStep `json:"steps"`
	Budget    int64         `json:"budget"`
}

// ==================== 预算锁管理器 (HTTP-adapted) ====================

// HTTPSessionReservation 会话级预算预留
type HTTPSessionReservation struct {
	SessionID    string
	Plan         *HTTPDAGPlan
	TotalCost    int64
	LockedPrices map[string]int64 // 每个工具在准入时锁定的价格
	CreatedAt    time.Time
	ExpiresAt    time.Time
	CurrentStep  int
	mu           sync.Mutex
	releaseOnce  sync.Once
	releaseFn    func() // 释放并发槽位（若有）
}

// Release 一次性释放该会话的并发槽位（幂等）
func (r *HTTPSessionReservation) Release() {
	if r.releaseFn != nil {
		r.releaseOnce.Do(r.releaseFn)
	}
}

// HTTPBudgetReservationManager 管理所有活跃会话的预算锁
type HTTPBudgetReservationManager struct {
	reservations sync.Map
	maxDuration  time.Duration
}

// NewHTTPBudgetReservationManager 创建预算锁管理器
func NewHTTPBudgetReservationManager(ttl time.Duration) *HTTPBudgetReservationManager {
	mgr := &HTTPBudgetReservationManager{maxDuration: ttl}
	go mgr.cleanupLoop()
	return mgr
}

// Reserve 为 DAG 会话创建预算预留（锁定价格快照）
func (m *HTTPBudgetReservationManager) Reserve(gov *MCPGovernor, plan *HTTPDAGPlan, totalCost int64) *HTTPSessionReservation {
	locked := make(map[string]int64)
	for _, step := range plan.Steps {
		locked[step.ToolName] = gov.GetToolEffectivePrice(step.ToolName)
	}
	res := &HTTPSessionReservation{
		SessionID:    plan.SessionID,
		Plan:         plan,
		TotalCost:    totalCost,
		LockedPrices: locked,
		CreatedAt:    time.Now(),
		ExpiresAt:    time.Now().Add(m.maxDuration),
	}
	m.reservations.Store(plan.SessionID, res)
	log.Printf("[MCPDP Budget Reserve] session=%s locked totalCost=%d prices=%v",
		plan.SessionID, totalCost, locked)
	return res
}

// Get 获取会话预留（检查过期）
func (m *HTTPBudgetReservationManager) Get(sessionID string) (*HTTPSessionReservation, bool) {
	v, ok := m.reservations.Load(sessionID)
	if !ok {
		return nil, false
	}
	res := v.(*HTTPSessionReservation)
	if time.Now().After(res.ExpiresAt) {
		m.reservations.Delete(sessionID)
		return nil, false
	}
	return res, true
}

// Advance 推进会话执行步骤
func (m *HTTPBudgetReservationManager) Advance(sessionID string) {
	if res, ok := m.Get(sessionID); ok {
		res.mu.Lock()
		res.CurrentStep++
		res.mu.Unlock()
	}
}

// Release 释放会话预留
func (m *HTTPBudgetReservationManager) Release(sessionID string) {
	m.reservations.Delete(sessionID)
}

func (m *HTTPBudgetReservationManager) cleanupLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		now := time.Now()
		m.reservations.Range(func(k, v interface{}) bool {
			r := v.(*HTTPSessionReservation)
			if now.After(r.ExpiresAt) {
				r.Release()
				m.reservations.Delete(k)
			}
			return true
		})
	}
}

// ==================== MCPDPServer 创新网关 ====================

// MCPDPServer 集成三大创新机制的 MCP HTTP 网关
type MCPDPServer struct {
	governor          *MCPGovernor
	tools             map[string]MCPTool
	handlers          map[string]ToolCallHandler
	serverInfo        Implementation
	budgetMgr         *HTTPBudgetReservationManager
	disableBudgetLock bool        // 消融实验：禁用预算锁（保留预检准入）
	sessionCap        chan struct{} // 并发会话上限信道（nil 表示不限制）
}

// NewMCPDPServer 创建 MCPDP 创新网关
// maxConcurrentSessions <= 0 表示不限制并发会话数
func NewMCPDPServer(name string, gov *MCPGovernor, reservationTTL time.Duration, maxConcurrentSessions int) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	return &MCPDPServer{
		governor:   gov,
		tools:      make(map[string]MCPTool),
		handlers:   make(map[string]ToolCallHandler),
		serverInfo: Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:  NewHTTPBudgetReservationManager(reservationTTL),
		sessionCap: cap,
	}
}

// NewMCPDPServerNoLock 创建消融变体网关（保留预检准入，禁用预算锁）
func NewMCPDPServerNoLock(name string, gov *MCPGovernor, reservationTTL time.Duration, maxConcurrentSessions int) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	return &MCPDPServer{
		governor:          gov,
		tools:             make(map[string]MCPTool),
		handlers:          make(map[string]ToolCallHandler),
		serverInfo:        Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:         NewHTTPBudgetReservationManager(reservationTTL),
		disableBudgetLock: true,
		sessionCap:        cap,
	}
}

// RegisterTool 注册工具及其处理函数
func (s *MCPDPServer) RegisterTool(tool MCPTool, handler ToolCallHandler) {
	s.tools[tool.Name] = tool
	s.handlers[tool.Name] = handler
}

// ServeHTTP 实现 http.Handler，处理所有 MCP JSON-RPC 请求
func (s *MCPDPServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, NewErrorResponse(nil, CodeParseError, "读取请求体失败", err.Error()))
		return
	}

	var req JSONRPCRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, NewErrorResponse(nil, CodeParseError, "JSON 解析错误", err.Error()))
		return
	}
	if req.JSONRPC != JSONRPCVersion {
		writeJSON(w, NewErrorResponse(req.ID, CodeInvalidRequest, "jsonrpc 版本必须为 2.0", nil))
		return
	}

	ctx := r.Context()
	var resp *JSONRPCResponse

	switch req.Method {
	case MethodInitialize:
		resp = s.handleInitialize(&req)
	case MethodToolsList:
		resp = s.handleToolsList(&req)
	case MethodToolsCall:
		resp = s.handleToolsCall(ctx, r, &req)
	case MethodPing:
		resp = NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	writeJSON(w, resp)
}

func (s *MCPDPServer) handleInitialize(req *JSONRPCRequest) *JSONRPCResponse {
	return NewSuccessResponse(req.ID, MCPInitializeResult{
		ProtocolVersion: "2024-11-05",
		ServerInfo:      s.serverInfo,
		Capabilities:    ServerCapabilities{Tools: &ToolsCapability{ListChanged: false}},
	})
}

func (s *MCPDPServer) handleToolsList(req *JSONRPCRequest) *JSONRPCResponse {
	tools := make([]MCPTool, 0, len(s.tools))
	for _, t := range s.tools {
		tools = append(tools, t)
	}
	return NewSuccessResponse(req.ID, MCPToolsListResult{Tools: tools})
}

// handleToolsCall 核心：双模态路由 (创新点 3)
func (s *MCPDPServer) handleToolsCall(ctx context.Context, r *http.Request, req *JSONRPCRequest) *JSONRPCResponse {
	dagHeader := r.Header.Get(HeaderPlanDAG)
	sessionID := r.Header.Get(HeaderSessionID)

	// ====== Plan-and-Solve 模式: 首步（带 X-Plan-DAG）======
	if dagHeader != "" {
		return s.handlePlanAndSolveFirstStep(ctx, r, req, dagHeader, sessionID)
	}

	// ====== Plan-and-Solve 模式: 后续步骤（带 X-Session-ID + 预算锁）======
	// 消融模式下跳过预算锁查询，直接走 ReAct
	if sessionID != "" && !s.disableBudgetLock {
		if res, ok := s.budgetMgr.Get(sessionID); ok {
			return s.handleReservedStep(ctx, req, res)
		}
		// 预留不存在或过期 → 降级为 ReAct
	}

	// ====== ReAct 模式: 标准 MCPGovernor 动态定价 ======
	return s.handleReActMode(ctx, req)
}

// handlePlanAndSolveFirstStep 处理 P&S 首步：预检准入 + 预算锁创建
func (s *MCPDPServer) handlePlanAndSolveFirstStep(
	ctx context.Context, r *http.Request, req *JSONRPCRequest,
	dagJSON string, sessionID string,
) *JSONRPCResponse {
	// 1. 解析 DAG
	var plan HTTPDAGPlan
	if err := json.Unmarshal([]byte(dagJSON), &plan); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams,
			"X-Plan-DAG JSON 解析失败", err.Error())
	}
	if plan.SessionID == "" {
		plan.SessionID = sessionID
	}
	if plan.SessionID == "" {
		return NewErrorResponse(req.ID, CodeInvalidParams, "缺少 session_id", nil)
	}

	// 2. 解析预算（Header 优先）
	if budgetStr := r.Header.Get(HeaderTotalBudget); budgetStr != "" {
		if b, err := strconv.ParseInt(budgetStr, 10, 64); err == nil {
			plan.Budget = b
		}
	}
	if plan.Budget <= 0 {
		return NewErrorResponse(req.ID, CodeInvalidParams, "budget 必须大于 0", nil)
	}

	// 3. 验证 DAG 无环
	if err := validateHTTPDAG(&plan); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, err.Error(), nil)
	}

	// 4. 创新点 1: Pre-flight Atomic Admission — 计算全链路总价格
	totalCost := s.calculateDAGTotalCost(&plan)

	log.Printf("[MCPDP Pre-flight] session=%s budget=%d totalCost=%d steps=%d",
		plan.SessionID, plan.Budget, totalCost, len(plan.Steps))

	if plan.Budget < totalCost {
		// 第 0 步拒绝 — 零级联算力浪费
		log.Printf("[MCPDP Pre-flight] session=%s REJECTED (budget %d < cost %d)",
			plan.SessionID, plan.Budget, totalCost)
		return NewErrorResponse(req.ID, CodeOverloaded,
			fmt.Sprintf("Pre-flight rejected: budget %d < total cost %d", plan.Budget, totalCost),
			map[string]interface{}{
				"session_id":  plan.SessionID,
				"budget":      plan.Budget,
				"total_cost":  totalCost,
				"mode":        "plan_and_solve",
				"rejected_at": "step_0",
			})
	}

	// 4b. 并发会话容量检查（创新点 1 的后端保护层）
	// 若并发槽位已满，在第 0 步拒绝 — 仍属零级联
	if s.sessionCap != nil {
		select {
		case s.sessionCap <- struct{}{}:
			// 获取到槽位，继续准入
		default:
			log.Printf("[MCPDP Pre-flight] session=%s REJECTED (session cap full)", plan.SessionID)
			return NewErrorResponse(req.ID, CodeOverloaded,
				fmt.Sprintf("Pre-flight rejected: concurrent session cap reached"),
				map[string]interface{}{
					"session_id":  plan.SessionID,
					"mode":        "plan_and_solve",
					"rejected_at": "step_0",
				})
		}
	}

	// 5. 创新点 2: Budget Reservation — 创建预算锁（锁定价格快照）
	// 消融模式下跳过预算锁创建
	if !s.disableBudgetLock {
		res := s.budgetMgr.Reserve(s.governor, &plan, totalCost)
		// 注册槽位释放函数（幂等，会话完成/失败时调用）
		if s.sessionCap != nil {
			cap := s.sessionCap
			res.releaseFn = func() { <-cap }
		}
		log.Printf("[MCPDP Pre-flight] session=%s ADMITTED, reservation created", plan.SessionID)
	} else {
		// 消融模式下无预算锁，需直接记录释放函数
		if s.sessionCap != nil {
			// 消融模式的槽位通过临时 reservation 管理
			// 这里创建一个仅用于释放槽位的 reservation
			res := s.budgetMgr.Reserve(s.governor, &plan, totalCost)
			cap := s.sessionCap
			res.releaseFn = func() { <-cap }
		}
		log.Printf("[MCPDP Pre-flight] session=%s ADMITTED (no-lock mode)", plan.SessionID)
	}

	// 6. 执行首步
	if s.disableBudgetLock {
		// 消融模式：首步也走 ReAct 动态定价
		return s.handleReActMode(ctx, req)
	}
	// 正常模式：绕过 LoadShedding，因为预检已原子性通过
	return s.executeStepDirect(ctx, req, plan.SessionID)
}

// handleReservedStep 处理带预算锁的后续步骤
func (s *MCPDPServer) handleReservedStep(
	ctx context.Context, req *JSONRPCRequest, res *HTTPSessionReservation,
) *JSONRPCResponse {
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	toolName := params.Name

	// 获取锁定价格
	lockedPrice, exists := res.LockedPrices[toolName]
	if !exists {
		// 工具不在原始 DAG 中 → 降级为 ReAct 模式（释放槽位因为不再追踪此会话）
		res.Release()
		return s.handleReActMode(ctx, req)
	}

	// 使用锁定价格检查 tokens
	tokens := int64(0)
	if params.Meta != nil {
		tokens = params.Meta.Tokens
	}

	log.Printf("[MCPDP Reserved] session=%s step=%d tool=%s tokens=%d lockedPrice=%d",
		res.SessionID, res.CurrentStep, toolName, tokens, lockedPrice)

	if tokens < lockedPrice {
		res.Release() // 会话中途被拒绝（级联失败），释放槽位
		return NewErrorResponse(req.ID, CodeOverloaded,
			fmt.Sprintf("Tokens %d < locked price %d for %s", tokens, lockedPrice, toolName),
			map[string]interface{}{
				"session_id":   res.SessionID,
				"locked_price": lockedPrice,
				"mode":         "plan_and_solve",
			})
	}

	// 执行步骤（使用锁定价格，绕过实时 LoadShedding）
	return s.executeStepDirect(ctx, req, res.SessionID)
}

// handleReActMode ReAct 模式：委托给标准 MCPGovernor
func (s *MCPDPServer) handleReActMode(ctx context.Context, req *JSONRPCRequest) *JSONRPCResponse {
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	handler, ok := s.handlers[params.Name]
	if !ok {
		return NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	return s.governor.HandleToolCall(ctx, req, handler)
}

// executeStepDirect 直接执行工具调用（绕过 LoadShedding）
func (s *MCPDPServer) executeStepDirect(ctx context.Context, req *JSONRPCRequest, sessionID string) *JSONRPCResponse {
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	handler, ok := s.handlers[params.Name]
	if !ok {
		return NewErrorResponse(req.ID, CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	result, err := handler(ctx, params)
	if err != nil {
		// 执行失败（后端超时/上下文取消）→ 释放并发槽位
		if res, ok := s.budgetMgr.Get(sessionID); ok {
			res.Release()
		}
		return NewErrorResponse(req.ID, CodeInternalError, err.Error(), nil)
	}

	// 推进步骤
	s.budgetMgr.Advance(sessionID)

	// 附加元数据
	if result.Meta == nil {
		result.Meta = &ResponseMeta{}
	}
	result.Meta.Name = s.serverInfo.Name

	// 使用锁定价格作为响应价格；若最后一步完成则释放并发槽位
	if res, ok := s.budgetMgr.Get(sessionID); ok {
		if lp, exists := res.LockedPrices[params.Name]; exists {
			result.Meta.Price = strconv.FormatInt(lp, 10)
		}
		// 所有步骤均已完成 → 释放槽位
		if res.CurrentStep >= len(res.Plan.Steps) {
			res.Release()
		}
	}

	return NewSuccessResponse(req.ID, result)
}

// calculateDAGTotalCost 计算 DAG 全链路总价格
func (s *MCPDPServer) calculateDAGTotalCost(plan *HTTPDAGPlan) int64 {
	var total int64
	for _, step := range plan.Steps {
		total += s.governor.GetToolEffectivePrice(step.ToolName)
	}
	return total
}

// validateHTTPDAG 使用 Kahn 算法验证 DAG 无环
func validateHTTPDAG(plan *HTTPDAGPlan) error {
	inDegree := make(map[string]int)
	adj := make(map[string][]string)
	stepSet := make(map[string]bool)

	for _, step := range plan.Steps {
		stepSet[step.StepID] = true
		if _, ok := inDegree[step.StepID]; !ok {
			inDegree[step.StepID] = 0
		}
		for _, dep := range step.DependsOn {
			adj[dep] = append(adj[dep], step.StepID)
			inDegree[step.StepID]++
		}
	}

	for _, step := range plan.Steps {
		for _, dep := range step.DependsOn {
			if !stepSet[dep] {
				return fmt.Errorf("步骤 %s 依赖不存在的步骤 %s", step.StepID, dep)
			}
		}
	}

	queue := []string{}
	for id, deg := range inDegree {
		if deg == 0 {
			queue = append(queue, id)
		}
	}

	visited := 0
	for len(queue) > 0 {
		node := queue[0]
		queue = queue[1:]
		visited++
		for _, next := range adj[node] {
			inDegree[next]--
			if inDegree[next] == 0 {
				queue = append(queue, next)
			}
		}
	}

	if visited != len(plan.Steps) {
		return fmt.Errorf("DAG 计划中存在循环依赖")
	}
	return nil
}
