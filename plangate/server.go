package plangate

import (
	"time"

	mcpgov "mcp-governance"
)

// HTTP Header 名称常量
const (
	HeaderPlanDAG     = "X-Plan-DAG"
	HeaderSessionID   = "X-Session-ID"
	HeaderTotalBudget = "X-Total-Budget"
)

// IntensityProvider 治理强度提供者接口
// GovernanceIntensityTracker (mock) 和 ExternalSignalTracker (real) 均实现此接口
type IntensityProvider interface {
	GetIntensity() float64
	IsActive() bool
}

// MCPDPServer 集成四大创新机制的 MCP HTTP 网关
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文核心架构 — 四大创新机制全集成于此                           │
// │                                                               │
// │ 创新点 1 (§3.2): Pre-flight Atomic Admission [Eq.(1)]        │
// │   → calculateDAGTotalCost + handlePlanAndSolveFirstStep       │
// │                                                               │
// │ 创新点 2 (§3.3): Budget Reservation [Eq.(2)]                 │
// │   → budgetMgr.Reserve (LockedPrices 快照)                    │
// │                                                               │
// │ 创新点 3 (§3.4): Dual-Mode Routing [Algorithm 1]             │
// │   → handleToolsCall 路由分发 (P&S / ReAct 双模态)            │
// │                                                               │
// │ 创新点 4 (§3.4): Sunk-Cost-Aware Pricing [Eq.(3),(4)]        │
// │   → handleReActFirstStep / handleReActSunkCostStep           │
// │                                                               │
// │ 支撑机制:                                                     │
// │   §3.5 I(t) 治理强度: intensityTracker [Eq.(5) 或 mock]     │
// │   §3.6 动态定价引擎: governor [Eq.(6)]                       │
// │   §3.7 信誉安全: reputationMgr [Eq.(7)]                     │
// │   §3.8 理论保证: discountFunc [Table 3, Claim 1]             │
// └─────────────────────────────────────────────────────────────────┘
type MCPDPServer struct {
	governor          *mcpgov.MCPGovernor
	tools             map[string]mcpgov.MCPTool
	handlers          map[string]mcpgov.ToolCallHandler
	serverInfo        mcpgov.Implementation
	budgetMgr         *HTTPBudgetReservationManager
	reactSessions     *ReactSessionManager  // ReAct 会话沉没成本跟踪
	disableBudgetLock bool                  // 消融实验：禁用预算锁（保留预检准入）
	sessionCap        chan struct{}         // 并发会话上限信道（nil 表示不限制）
	sessionCapWait    time.Duration        // Step-0 排队等待超时（0=立即拒绝）
	sunkCostAlpha       float64              // ReAct 沉没成本系数 (0=禁用)
	discountFunc        DiscountFunc         // 沉没成本折扣函数（默认 Quadratic K²）
	discountFuncName    DiscountFuncName     // 折扣函数名称（日志/消融用）
	intensityPriceBase  float64              // intensity 驱动定价的参考基价（ownPrice=0 时的 fallback）
	intensityTracker    IntensityProvider    // 滞回门控治理强度跟踪器（nil=禁用，退化为原始行为）
	reputationMgr       *ReputationManager // 信誉管理器（nil=禁用，等效信任所有 Agent）
	reactStep0Inflight int64              // atomic: 当前在处理中的 ReAct step-0 请求数
	reactStep0Limit    int64              // step-0 并发上限，超过后走标准准入
	protectCommittedSessions bool          // 准入即承诺：step 1+ 永不拒绝，消除级联浪费
}

// getGovernanceIntensity 获取当前治理强度
// 无跟踪器时返回 1.0（完全治理，向后兼容原始行为）
func (s *MCPDPServer) getGovernanceIntensity() float64 {
	if s.intensityTracker == nil {
		return 1.0
	}
	return s.intensityTracker.GetIntensity()
}

// NewMCPDPServer 创建 PlanGate 创新网关
// maxConcurrentSessions <= 0 表示不限制并发会话数
func NewMCPDPServer(name string, gov *mcpgov.MCPGovernor, reservationTTL time.Duration, maxConcurrentSessions int, sunkCostAlpha float64) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	step0Limit := int64(maxConcurrentSessions)
	if step0Limit <= 0 {
		step0Limit = 30
	}
	return &MCPDPServer{
		governor:          gov,
		tools:             make(map[string]mcpgov.MCPTool),
		handlers:          make(map[string]mcpgov.ToolCallHandler),
		serverInfo:        mcpgov.Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:         NewHTTPBudgetReservationManager(reservationTTL),
		reactSessions:     NewReactSessionManager(reservationTTL),
		sessionCap:        cap,
		sessionCapWait:    0, // mock 模式默认立即拒绝
		sunkCostAlpha:     sunkCostAlpha,
		discountFunc:      QuadraticDiscount,
		discountFuncName:  DiscountQuadratic,
		intensityTracker:  NewGovernanceIntensityTracker(gov, 200),
		reactStep0Limit:   step0Limit,
		reputationMgr:     NewReputationManager(DefaultReputationConfig()),
	}
}

// NewMCPDPServerNoLock 创建消融变体网关（保留预检准入，禁用预算锁）
func NewMCPDPServerNoLock(name string, gov *mcpgov.MCPGovernor, reservationTTL time.Duration, maxConcurrentSessions int, sunkCostAlpha float64) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	step0Limit := int64(maxConcurrentSessions)
	if step0Limit <= 0 {
		step0Limit = 30
	}
	return &MCPDPServer{
		governor:          gov,
		tools:             make(map[string]mcpgov.MCPTool),
		handlers:          make(map[string]mcpgov.ToolCallHandler),
		serverInfo:        mcpgov.Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:         NewHTTPBudgetReservationManager(reservationTTL),
		reactSessions:     NewReactSessionManager(reservationTTL),
		disableBudgetLock: true,
		sessionCap:        cap,
		sessionCapWait:    0,
		sunkCostAlpha:     sunkCostAlpha,
		discountFunc:      QuadraticDiscount,
		discountFuncName:  DiscountQuadratic,
		intensityTracker:  NewGovernanceIntensityTracker(gov, 200),
		reactStep0Limit:   step0Limit,
		reputationMgr:     NewReputationManager(DefaultReputationConfig()),
	}
}

// RegisterTool 注册工具及其处理函数
func (s *MCPDPServer) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	s.tools[tool.Name] = tool
	s.handlers[tool.Name] = handler
}

// SetDiscountFunc 设置沉没成本折扣函数（用于消融实验）
// 默认为 QuadraticDiscount (K²)，可切换为 Linear/Exponential/Logarithmic
func (s *MCPDPServer) SetDiscountFunc(name DiscountFuncName) {
	s.discountFunc = GetDiscountFunc(name)
	s.discountFuncName = name
}

// GetDiscountFuncName 获取当前折扣函数名称（用于日志和诊断）
func (s *MCPDPServer) GetDiscountFuncName() DiscountFuncName {
	return s.discountFuncName
}

// NewMCPDPServerWithExternalSignals 创建使用外部信号治理的 PlanGate 网关（真实 LLM 模式）
// 使用 ExternalSignalTracker 替代 GovernanceIntensityTracker，
// 三维信号融合 (429频率 + 延迟P95 + RateLimit-Remaining) 驱动治理
func NewMCPDPServerWithExternalSignals(
	name string, gov *mcpgov.MCPGovernor,
	reservationTTL time.Duration, maxConcurrentSessions int,
	sunkCostAlpha float64, signalTracker *ExternalSignalTracker,
	sessionCapWait time.Duration, intensityPriceBase float64,
) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	step0Limit := int64(maxConcurrentSessions)
	if step0Limit <= 0 {
		step0Limit = 30
	}
	return &MCPDPServer{
		governor:          gov,
		tools:             make(map[string]mcpgov.MCPTool),
		handlers:          make(map[string]mcpgov.ToolCallHandler),
		serverInfo:        mcpgov.Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:         NewHTTPBudgetReservationManager(reservationTTL),
		reactSessions:     NewReactSessionManager(reservationTTL),
		sessionCap:        cap,
		sessionCapWait:    sessionCapWait,
		sunkCostAlpha:       sunkCostAlpha,
		discountFunc:        QuadraticDiscount,
		discountFuncName:    DiscountQuadratic,
		intensityPriceBase:  intensityPriceBase, // intensity 驱动定价参考基价（真实 LLM 模式）
		intensityTracker:    signalTracker,
		reactStep0Limit:     step0Limit,
		reputationMgr:       NewReputationManager(DefaultReputationConfig()),
		protectCommittedSessions: true, // 真实 LLM 模式默认启用准入承诺保障
	}
}

// GetExternalSignalTracker 获取外部信号跟踪器（仅当使用 ExternalSignalTracker 时有效）
// 供 makeProxyHandlerWithSignals 报告 API 响应信号
func (s *MCPDPServer) GetExternalSignalTracker() *ExternalSignalTracker {
	if est, ok := s.intensityTracker.(*ExternalSignalTracker); ok {
		return est
	}
	return nil
}

// GetReputationManager 获取信誉管理器（供外部查询统计）
func (s *MCPDPServer) GetReputationManager() *ReputationManager {
	return s.reputationMgr
}
