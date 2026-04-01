package plangate

import (
	"log"
	"sync"
	"time"

	mcpgov "mcp-governance"
)

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
func (m *HTTPBudgetReservationManager) Reserve(gov *mcpgov.MCPGovernor, plan *HTTPDAGPlan, totalCost int64) *HTTPSessionReservation {
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
	log.Printf("[PlanGate Budget Reserve] session=%s locked totalCost=%d prices=%v",
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

// ==================== ReAct Session Tracker (Sunk-Cost-Aware) ====================

// ReactSessionState 跟踪 ReAct 会话的执行进度，用于沉没成本感知准入
type ReactSessionState struct {
	SessionID   string
	CurrentStep int
	CreatedAt   time.Time
	ExpiresAt   time.Time
	mu          sync.Mutex
	releaseOnce sync.Once
	releaseFn   func()
}

// Release 一次性释放该 ReAct 会话的并发槽位（幂等）
func (r *ReactSessionState) Release() {
	if r.releaseFn != nil {
		r.releaseOnce.Do(r.releaseFn)
	}
}

// ReactSessionManager 管理所有活跃 ReAct 会话的沉没成本跟踪
type ReactSessionManager struct {
	sessions    sync.Map
	maxDuration time.Duration
}

// NewReactSessionManager 创建 ReAct 会话管理器
func NewReactSessionManager(ttl time.Duration) *ReactSessionManager {
	mgr := &ReactSessionManager{maxDuration: ttl}
	go mgr.cleanupLoop()
	return mgr
}

// Create 为新 ReAct 会话创建跟踪条目
func (m *ReactSessionManager) Create(sessionID string, releaseFn func()) *ReactSessionState {
	s := &ReactSessionState{
		SessionID:   sessionID,
		CurrentStep: 0,
		CreatedAt:   time.Now(),
		ExpiresAt:   time.Now().Add(m.maxDuration),
		releaseFn:   releaseFn,
	}
	m.sessions.Store(sessionID, s)
	return s
}

// Get 获取 ReAct 会话状态（检查过期）
func (m *ReactSessionManager) Get(sessionID string) (*ReactSessionState, bool) {
	v, ok := m.sessions.Load(sessionID)
	if !ok {
		return nil, false
	}
	s := v.(*ReactSessionState)
	if time.Now().After(s.ExpiresAt) {
		s.Release()
		m.sessions.Delete(sessionID)
		return nil, false
	}
	return s, true
}

// Advance 推进 ReAct 会话步骤计数
func (m *ReactSessionManager) Advance(sessionID string) {
	if s, ok := m.Get(sessionID); ok {
		s.mu.Lock()
		s.CurrentStep++
		s.mu.Unlock()
	}
}

// ReleaseAndDelete 释放并删除 ReAct 会话
func (m *ReactSessionManager) ReleaseAndDelete(sessionID string) {
	if v, ok := m.sessions.Load(sessionID); ok {
		v.(*ReactSessionState).Release()
		m.sessions.Delete(sessionID)
	}
}

func (m *ReactSessionManager) cleanupLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		now := time.Now()
		m.sessions.Range(func(k, v interface{}) bool {
			s := v.(*ReactSessionState)
			if now.After(s.ExpiresAt) {
				s.Release()
				m.sessions.Delete(k)
			}
			return true
		})
	}
}
