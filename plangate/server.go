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

// MCPDPServer 集成三大创新机制的 MCP HTTP 网关
type MCPDPServer struct {
	governor          *mcpgov.MCPGovernor
	tools             map[string]mcpgov.MCPTool
	handlers          map[string]mcpgov.ToolCallHandler
	serverInfo        mcpgov.Implementation
	budgetMgr         *HTTPBudgetReservationManager
	disableBudgetLock bool          // 消融实验：禁用预算锁（保留预检准入）
	sessionCap        chan struct{} // 并发会话上限信道（nil 表示不限制）
}

// NewMCPDPServer 创建 PlanGate 创新网关
// maxConcurrentSessions <= 0 表示不限制并发会话数
func NewMCPDPServer(name string, gov *mcpgov.MCPGovernor, reservationTTL time.Duration, maxConcurrentSessions int) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	return &MCPDPServer{
		governor:   gov,
		tools:      make(map[string]mcpgov.MCPTool),
		handlers:   make(map[string]mcpgov.ToolCallHandler),
		serverInfo: mcpgov.Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:  NewHTTPBudgetReservationManager(reservationTTL),
		sessionCap: cap,
	}
}

// NewMCPDPServerNoLock 创建消融变体网关（保留预检准入，禁用预算锁）
func NewMCPDPServerNoLock(name string, gov *mcpgov.MCPGovernor, reservationTTL time.Duration, maxConcurrentSessions int) *MCPDPServer {
	var cap chan struct{}
	if maxConcurrentSessions > 0 {
		cap = make(chan struct{}, maxConcurrentSessions)
	}
	return &MCPDPServer{
		governor:          gov,
		tools:             make(map[string]mcpgov.MCPTool),
		handlers:          make(map[string]mcpgov.ToolCallHandler),
		serverInfo:        mcpgov.Implementation{Name: name, Version: "2.0.0"},
		budgetMgr:         NewHTTPBudgetReservationManager(reservationTTL),
		disableBudgetLock: true,
		sessionCap:        cap,
	}
}

// RegisterTool 注册工具及其处理函数
func (s *MCPDPServer) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	s.tools[tool.Name] = tool
	s.handlers[tool.Name] = handler
}
