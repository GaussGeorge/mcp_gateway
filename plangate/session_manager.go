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
