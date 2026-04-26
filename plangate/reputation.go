package plangate

import (
	"fmt"
	"log"
	"sync"
	"time"
)

// ReputationScore 单个 Agent 的信誉评分
type ReputationScore struct {
	AgentID          string
	Score            float64 // [0.0, 1.0], 1.0=完全可信
	TotalSessions    int64
	SuccessSessions  int64
	FailedSessions   int64   // 主动终止/超时/DAG 不匹配
	BudgetViolations int64   // 预算申报与实际不符的次数
	DAGViolations    int64   // DAG 结构违规次数（环、超限、虚假节点）
	LastUpdated      time.Time
}

// ReputationManager 基于历史行为的 Agent 信誉管理器
//// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文 §3.7 Reputation-Based Security                              │
// │                                                               │
// │ Eq.(7): T_a^{t+1} = β · T_a^{t} + (1-β) · r^{t}              │
// │   β = 0.98 (等效 rewardRate=0.02，见 RecordSuccess/Failure)  │
// │   r^{t} ∈ {0,1}: 会话结果 (1=成功, 0=失败)                  │
// │   新 Agent 乐观初始化 T_a = 1.0 (完全可信)                    │
// │   违规额外惩罚: T_a -= δ=0.15 (penaltyRate，非 EWMA)          │
// │                                                               │
// │ 两级渐进执行:                                                    │
// │   任意 T_a < 1 → 预算折扣 AdjustBudget = B × T_a              │
// │   T_a < banThreshold(0.3) → 封禁（IsBanned）                   │
// │   ValidateDAGLimits: 服务端校验 DAG 步数+预算上限              │
// └─────────────────────────────────────────────────────────────────┘
//// 两位审稿人均指出 PlanGate 完全信任客户端 Header 的设计缺陷：
//   - Review1 §2.1: "完全假设Agent会诚实申报预算和DAG计划...未考虑恶意Agent虚报预算"
//   - Review2 §3.4: "恶意客户端可以轻易通过伪造极高的Budget或虚假的DAG来绕过准入限制"
//
// 信誉机制设计：
//   1. 每个 Agent（按 session_id 前缀 或 API-Key 识别）维护信誉评分 ∈ [0, 1]
//   2. 成功完成会话 → 信誉上升（慢升）
//   3. 预算欺诈/DAG违规/异常行为 → 信誉下降（快降）
//   4. 信誉影响准入：低信誉 Agent 的 budget 被折扣，等效提高准入门槛
//   5. 信誉极低（<0.3）的 Agent 进入惩罚期，所有请求直接拒绝
type ReputationManager struct {
	mu          sync.RWMutex
	agents      map[string]*ReputationScore
	enabled     bool
	decayAlpha  float64 // 保留字段（未使用）；实际 EWMA 速率由 rewardRate=0.02 决定
	penaltyRate float64 // 违规时单次扣分幅度
	rewardRate  float64 // 成功时单次加分幅度
	banThreshold float64 // 信誉低于此值时拒绝准入
	// DAG 校验限制
	maxDAGSteps     int   // 单次 DAG 最大步数（防止虚假巨型 DAG 抢占资源）
	maxBudgetPerReq int64 // 单次预算上限（防止虚报天价预算）
}

// ReputationConfig 信誉管理器配置
type ReputationConfig struct {
	Enabled         bool
	DecayAlpha      float64 // 默认 0.1
	PenaltyRate     float64 // 默认 0.15（违规一次扣 15%）
	RewardRate      float64 // 默认 0.02（成功一次加 2%）
	BanThreshold    float64 // 默认 0.3
	MaxDAGSteps     int     // 默认 20
	MaxBudgetPerReq int64   // 默认 10000
}

// DefaultReputationConfig 返回默认信誉配置
func DefaultReputationConfig() ReputationConfig {
	return ReputationConfig{
		Enabled:         true,
		DecayAlpha:      0.1,
		PenaltyRate:     0.15,
		RewardRate:      0.02,
		BanThreshold:    0.3,
		MaxDAGSteps:     20,
		MaxBudgetPerReq: 10000,
	}
}

// NewReputationManager 创建信誉管理器
func NewReputationManager(cfg ReputationConfig) *ReputationManager {
	return &ReputationManager{
		agents:          make(map[string]*ReputationScore),
		enabled:         cfg.Enabled,
		decayAlpha:      cfg.DecayAlpha,
		penaltyRate:     cfg.PenaltyRate,
		rewardRate:      cfg.RewardRate,
		banThreshold:    cfg.BanThreshold,
		maxDAGSteps:     cfg.MaxDAGSteps,
		maxBudgetPerReq: cfg.MaxBudgetPerReq,
	}
}

// GetOrCreate 获取或创建 Agent 信誉记录（新 Agent 初始信誉 1.0）
func (rm *ReputationManager) GetOrCreate(agentID string) *ReputationScore {
	rm.mu.Lock()
	defer rm.mu.Unlock()

	if score, ok := rm.agents[agentID]; ok {
		return score
	}

	score := &ReputationScore{
		AgentID:     agentID,
		Score:       1.0,
		LastUpdated: time.Now(),
	}
	rm.agents[agentID] = score
	return score
}

// GetScore 获取 Agent 信誉评分（不存在返回 1.0）
func (rm *ReputationManager) GetScore(agentID string) float64 {
	rm.mu.RLock()
	defer rm.mu.RUnlock()

	if score, ok := rm.agents[agentID]; ok {
		return score.Score
	}
	return 1.0
}

// RecordSuccess 记录会话成功完成
// >>> Eq.(7): T_a^{t+1} = β · T_a^{t} + (1-β) · r^{t}, 此处 r^{t}=1
// 实现: score += rewardRate × (1 - score) → 缓慢上升
func (rm *ReputationManager) RecordSuccess(agentID string) {
	rm.mu.Lock()
	defer rm.mu.Unlock()

	score := rm.getOrCreateLocked(agentID)
	score.TotalSessions++
	score.SuccessSessions++
	// 缓慢上升：score = score + rewardRate × (1 - score)
	score.Score += rm.rewardRate * (1.0 - score.Score)
	if score.Score > 1.0 {
		score.Score = 1.0
	}
	score.LastUpdated = time.Now()
}

// RecordFailure 记录会话失败（级联失败、超时等正常失败）
// >>> Eq.(7): T_a^{t+1} = β · T_a^{t} + (1-β) · r^{t}, 此处 r^{t}=0
// 实现: score -= rewardRate × score → 轻微影响
func (rm *ReputationManager) RecordFailure(agentID string) {
	rm.mu.Lock()
	defer rm.mu.Unlock()

	score := rm.getOrCreateLocked(agentID)
	score.TotalSessions++
	score.FailedSessions++
	// 正常失败只轻微影响信誉
	score.Score -= rm.rewardRate * score.Score
	if score.Score < 0 {
		score.Score = 0
	}
	score.LastUpdated = time.Now()
}

// RecordBudgetViolation 记录预算欺诈（申报预算与实际行为不符）
// >>> §3.7: 违规快降，score -= penaltyRate (0.15)
// 3次连续失败: 1.0×0.85³ ≈ 0.61; 违规后直接扣分可快速触发封禁
func (rm *ReputationManager) RecordBudgetViolation(agentID string) {
	rm.mu.Lock()
	defer rm.mu.Unlock()

	score := rm.getOrCreateLocked(agentID)
	score.BudgetViolations++
	score.Score -= rm.penaltyRate
	if score.Score < 0 {
		score.Score = 0
	}
	score.LastUpdated = time.Now()
	log.Printf("[Reputation] BUDGET VIOLATION: agent=%s score=%.3f violations=%d",
		agentID, score.Score, score.BudgetViolations)
}

// RecordDAGViolation 记录 DAG 结构违规（无效 DAG、超限等）
func (rm *ReputationManager) RecordDAGViolation(agentID string) {
	rm.mu.Lock()
	defer rm.mu.Unlock()

	score := rm.getOrCreateLocked(agentID)
	score.DAGViolations++
	score.Score -= rm.penaltyRate
	if score.Score < 0 {
		score.Score = 0
	}
	score.LastUpdated = time.Now()
	log.Printf("[Reputation] DAG VIOLATION: agent=%s score=%.3f violations=%d",
		agentID, score.Score, score.DAGViolations)
}

// IsBanned 检查 Agent 是否被封禁
// >>> §3.7 三级渐进执行: T_a < banThreshold(0.3) → 临时封禁
func (rm *ReputationManager) IsBanned(agentID string) bool {
	if !rm.enabled {
		return false
	}
	return rm.GetScore(agentID) < rm.banThreshold
}

// AdjustBudget 根据信誉调整 Agent 申报的预算（信誉折扣）
// >>> §3.7: B_adjusted = B_declared × T_a
// 低信誉 Agent 的预算被打折 → 等效提高准入门槛
func (rm *ReputationManager) AdjustBudget(agentID string, declaredBudget int64) int64 {
	if !rm.enabled {
		return declaredBudget
	}
	rep := rm.GetScore(agentID)
	adjusted := int64(float64(declaredBudget) * rep)
	if adjusted < 1 && declaredBudget > 0 {
		adjusted = 1
	}
	return adjusted
}

// ValidateDAGLimits 服务端 DAG 校验（防止恶意巨型 DAG 或天价预算）
// 返回 nil 表示通过，非 nil 为校验错误描述
func (rm *ReputationManager) ValidateDAGLimits(plan *HTTPDAGPlan) error {
	if !rm.enabled {
		return nil
	}

	// 检查步数上限
	if rm.maxDAGSteps > 0 && len(plan.Steps) > rm.maxDAGSteps {
		return fmt.Errorf("DAG steps %d exceeds limit %d", len(plan.Steps), rm.maxDAGSteps)
	}

	// 检查预算上限
	if rm.maxBudgetPerReq > 0 && plan.Budget > rm.maxBudgetPerReq {
		return fmt.Errorf("budget %d exceeds limit %d", plan.Budget, rm.maxBudgetPerReq)
	}

	// 检查工具名称合法性（必须是已注册工具，防止注入攻击）
	// 注：此处仅检查非空，具体工具合法性由上层 RegisterTool 保证
	for _, step := range plan.Steps {
		if step.ToolName == "" {
			return fmt.Errorf("step %s has empty tool_name", step.StepID)
		}
		if step.StepID == "" {
			return fmt.Errorf("step has empty step_id")
		}
	}

	return nil
}

// MaxDAGSteps 返回最大 DAG 步数限制
func (rm *ReputationManager) MaxDAGSteps() int {
	return rm.maxDAGSteps
}

// MaxBudgetPerReq 返回单次预算上限
func (rm *ReputationManager) MaxBudgetPerReq() int64 {
	return rm.maxBudgetPerReq
}

// getOrCreateLocked 内部辅助（调用者必须持有 mu 写锁）
func (rm *ReputationManager) getOrCreateLocked(agentID string) *ReputationScore {
	if score, ok := rm.agents[agentID]; ok {
		return score
	}
	score := &ReputationScore{
		AgentID:     agentID,
		Score:       1.0,
		LastUpdated: time.Now(),
	}
	rm.agents[agentID] = score
	return score
}

// Stats 获取信誉管理器统计信息（调试用）
func (rm *ReputationManager) Stats() map[string]interface{} {
	rm.mu.RLock()
	defer rm.mu.RUnlock()

	totalAgents := len(rm.agents)
	bannedCount := 0
	for _, s := range rm.agents {
		if s.Score < rm.banThreshold {
			bannedCount++
		}
	}

	return map[string]interface{}{
		"total_agents":  totalAgents,
		"banned_agents": bannedCount,
		"enabled":       rm.enabled,
		"ban_threshold": rm.banThreshold,
	}
}
