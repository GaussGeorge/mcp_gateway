package plangate

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// handlePlanAndSolveFirstStep 处理 P&S 首步：预检准入 + 预算锁创建
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文 §3.2 Pre-flight Atomic Admission (Algorithm 1, P&S 分支) │
// │                                                               │
// │ Eq.(1): C_total = Σ P_eff(t_i),  P_eff(t) = P_own × w_t     │
// │   若 B < C_total → step-0 原子拒绝（零级联浪费）              │
// │                                                               │
// │ Eq.(2): LockedPrices[t_i] = P_eff(t_i)|_{admission time}     │
// │   准入成功 → 对每个工具价格拍快照，后续步骤使用锁定价格       │
// └─────────────────────────────────────────────────────────────────┘
func (s *MCPDPServer) handlePlanAndSolveFirstStep(
	ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest,
	dagJSON string, sessionID string,
) *mcpgov.JSONRPCResponse {
	// 1. 解析 DAG
	var plan HTTPDAGPlan
	if err := json.Unmarshal([]byte(dagJSON), &plan); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams,
			"X-Plan-DAG JSON 解析失败", err.Error())
	}
	if plan.SessionID == "" {
		plan.SessionID = sessionID
	}
	if plan.SessionID == "" {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "缺少 session_id", nil)
	}

	// 2. 解析预算（Header 优先于 DAG body 中的 budget 字段）
	// X-Total-Budget header 允许 Agent 在不重新序列化 DAG 的情况下修改预算上限
	if budgetStr := r.Header.Get(HeaderTotalBudget); budgetStr != "" {
		if b, err := strconv.ParseInt(budgetStr, 10, 64); err == nil {
			plan.Budget = b
		}
	}
	if plan.Budget <= 0 {
		// 预算为零或负数时立即拒绝，无法执行任何工具调用
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "budget 必须大于 0", nil)
	}

	// 2b. §3.7 安全机制：信誉校验 + DAG 结构限制
	// 目的：防止对抗性 Agent 通过虚报预算/伪造 DAG 垄断会话槽位
	// 对应 Eq.(7)：T_a < τ_ban(0.3) → 直接拒绝
	if s.reputationMgr != nil && s.reputationMgr.enabled {
		agentID := plan.SessionID // 以 session_id 前缀作为 agent 标识符（简化；生产环境可改用 API-Key）

		// 封禁检查：信誉分 < banThreshold(0.3) 时返回 HTTP 403 等效拒绝
		if s.reputationMgr.IsBanned(agentID) {
			log.Printf("[PlanGate Security] session=%s BANNED (reputation=%.3f)",
				plan.SessionID, s.reputationMgr.GetScore(agentID))
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				"Agent banned due to low reputation",
				map[string]interface{}{"session_id": plan.SessionID, "reputation": s.reputationMgr.GetScore(agentID)})
		}

		// DAG 结构限制：服务端独立校验步数上限(maxDAGSteps=20) + 预算上限(maxBudgetPerReq=10000)
		// 防止 Agent 提交 steps=1000、budget=999999 的巨型假 DAG 抢占全部并发槽位
		if err := s.reputationMgr.ValidateDAGLimits(&plan); err != nil {
			s.reputationMgr.RecordDAGViolation(agentID) // 结构违规 → 快速扣分
			log.Printf("[PlanGate Security] session=%s DAG VIOLATION: %v", plan.SessionID, err)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, err.Error(),
				map[string]interface{}{"session_id": plan.SessionID})
		}

		// 信誉折扣预算：低信誉 Agent 的可用预算被按信誉分比例压缩
		// AdjustBudget(id, B) = B × T_a，等效提高该 Agent 的准入门槛
		plan.Budget = s.reputationMgr.AdjustBudget(agentID, plan.Budget)
	}

	// 3. 拓扑校验：使用 Kahn 算法检测 DAG 是否有环
	// 有环的 DAG 无法线性执行，且可能导致无限循环消耗资源
	if err := validateHTTPDAG(&plan); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, err.Error(), nil)
	}

	// 4. §3.2 创新点 1: Pre-flight Atomic Admission — 在 step-0 一次性计算全链路总价格
	// >>> Eq.(1): C_total = Σ_{i=1}^{n} P_eff(t_i)
	// P_eff(t_i) = P_own × w_{t_i}，其中 w_{t_i} 是该工具的权重系数（重型工具定价更高）
	// 关键：此时使用的是「当前实时市场价格」，后续步骤将锁定该快照（Eq.2）
	totalCost := s.calculateDAGTotalCost(&plan)

	log.Printf("[PlanGate Pre-flight] session=%s budget=%d totalCost=%d steps=%d",
		plan.SessionID, plan.Budget, totalCost, len(plan.Steps))

	if plan.Budget < totalCost {
		// >>> Eq.(1) 准入判定: B < C_total → step-0 原子拒绝
		// 「原子性」的含义：要么全部准入，要么在 step-0 整体拒绝，绝不在 step K>0 拒绝
		// 这是消除「级联计算浪费」的核心机制：相比 Per-Request 定价的 E[W]=O(N²)，
		// Budget Reservation 将浪费降至 E[W]=O(1)（Table 3 最后一行）
		log.Printf("[PlanGate Pre-flight] session=%s REJECTED (budget %d < cost %d)",
			plan.SessionID, plan.Budget, totalCost)
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
			fmt.Sprintf("Pre-flight rejected: budget %d < total cost %d", plan.Budget, totalCost),
			map[string]interface{}{
				"session_id":  plan.SessionID,
				"budget":      plan.Budget,
				"total_cost":  totalCost,
				"mode":        "plan_and_solve",
				"rejected_at": "step_0",
			})
	}

	// 4b. 并发会话容量硬门控（创新点 1 的第二道防线，补充 Eq.(1) 的经济准入）
	// sessionCap 是一个 buffered channel，容量 = maxConcurrentSessions（默认 30）
	// 即使通过了 Eq.(1) 的预算检查，也必须获取并发槽位才能真正准入
	// 【设计意图】双重门控：Eq.(1) 过滤「肯定失败」的请求；sessionCap 限制「同时活跃数」
	if s.sessionCap != nil {
		select {
		case s.sessionCap <- struct{}{}:
			// 获取到槽位，继续准入；会话结束时通过 releaseFn 归还（见下方 Reserve 调用）
		case <-time.After(s.sessionCapWait):
			// 所有槽位已被占用且在 sessionCapWait 时间内无槽位释放 → 拒绝排队超时
			// sessionCapWait=0 时等效「立即拒绝」（最严格），>0 时允许短暂排队等候
			log.Printf("[PlanGate Pre-flight] session=%s REJECTED (session cap full after %v wait)", plan.SessionID, s.sessionCapWait)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				fmt.Sprintf("Pre-flight rejected: concurrent session cap reached"),
				map[string]interface{}{
					"session_id":  plan.SessionID,
					"mode":        "plan_and_solve",
					"rejected_at": "step_0",
				})
		case <-ctx.Done():
			// HTTP 请求已被客户端取消（超时/断连）
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, "context cancelled while waiting for session cap", nil)
		}
	}

	// 5. §3.3 创新点 2: Budget Reservation — 对每个工具价格拍「准入时刻快照」
	// >>> Eq.(2): LockedPrices[t_i] = P_eff(t_i)|_{admission time}
	//
	// 「时间隔离保护」的实现原理：
	//   - 准入时将每个工具的实时市场价格存入 LockedPrices map
	//   - 后续步骤（step 1, 2, ...）使用 LockedPrices 中的快照价格而非实时价格
	//   - 即使后续市场价格上涨，已准入会话也不会因「价格涨过预算」被中途拒绝
	//
	// 消融实验 Exp4 (w/o BL) 通过 disableBudgetLock=true 跳过此步，验证预算锁的独立贡献
	if !s.disableBudgetLock {
		res := s.budgetMgr.Reserve(s.governor, &plan, totalCost)
		// 注册槽位释放函数（幂等 sync.Once 封装），在以下情况触发归还：
		//   a) 会话正常完成最后一步（executeStepDirect 检测 CurrentStep >= len(Steps)）
		//   b) 会话中途被拒绝（handleReservedStep 中 tokens < lockedPrice）
		//   c) 预留 TTL 到期（cleanupLoop 后台清理）
		if s.sessionCap != nil {
			cap := s.sessionCap
			res.releaseFn = func() { <-cap }
		}
		log.Printf("[PlanGate Pre-flight] session=%s ADMITTED, reservation created", plan.SessionID)
	} else {
		// 消融模式（w/o BL）：不创建价格锁，但仍需管理 sessionCap 槽位（否则槽位泄漏）
		if s.sessionCap != nil {
			res := s.budgetMgr.Reserve(s.governor, &plan, totalCost)
			cap := s.sessionCap
			res.releaseFn = func() { <-cap }
		}
		log.Printf("[PlanGate Pre-flight] session=%s ADMITTED (no-lock mode)", plan.SessionID)
	}

	// 6. 执行 DAG 首步工具调用
	if s.disableBudgetLock {
		// 消融模式：首步也走 ReAct 动态定价，测试「仅有预检准入但无预算锁」时的性能
		return s.handleReActMode(ctx, req)
	}
	// 正常模式：已通过预检 + 获取槽位 + 创建价格锁，直接执行（绕过 MCPGovernor LoadShedding）
	// 原因：LoadShedding 会再次检查 ownPrice，但此刻会话已被「承诺」，二次检查是冗余且有害的
	return s.executeStepDirect(ctx, req, plan.SessionID)
}

// handleReservedStep 处理带预算锁的后续步骤
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文 §3.3 Budget Reservation (Algorithm 1, P&S 后续步骤)      │
// │                                                               │
// │ Eq.(2): 使用 LockedPrices[t_i]（准入时快照）而非实时市场价格  │
// │   实现时间隔离保护 → 步骤 K≥1 不受价格波动影响               │
// │   Table 3: Budget reserv. d(k)=0 (k≥1), E[W]=O(1)           │
// └─────────────────────────────────────────────────────────────────┘
func (s *MCPDPServer) handleReservedStep(
	ctx context.Context, req *mcpgov.JSONRPCRequest, res *HTTPSessionReservation,
) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	toolName := params.Name

	// >>> Eq.(2): 查询准入时锁定的价格快照（而非调用 governor.GetToolEffectivePrice 获取实时价）
	// LockedPrices 在 session_manager.go 的 Reserve() 中创建：
	//   locked[t_i] = gov.GetToolEffectivePrice(t_i)  ← 准入时刻的市场价格
	lockedPrice, exists := res.LockedPrices[toolName]
	if !exists {
		// 工具不在原始 DAG 中（Agent 临时调用了 DAG 以外的工具）
		// 此工具不受价格保护 → 优雅降级为 ReAct 动态定价模式
		// 同时释放 P&S 会话槽位，避免死占资源
		res.Release()
		return s.handleReActMode(ctx, req)
	}

	// 使用锁定价格而非实时价格进行 token 余额检查
	// 即使此刻实时市场价已涨至 2× lockedPrice，只要 tokens ≥ lockedPrice 就允许通过
	// 这正是「时间隔离保护」的价值：消除价格波动导致的中途拒绝
	tokens := int64(0)
	if params.Meta != nil {
		tokens = params.Meta.Tokens
	}

	log.Printf("[PlanGate Reserved] session=%s step=%d tool=%s tokens=%d lockedPrice=%d",
		res.SessionID, res.CurrentStep, toolName, tokens, lockedPrice)

	if tokens < lockedPrice {
		// token 余额不足（Agent 自身预算耗尽，非价格波动导致）→ 中途拒绝
		// 这是 Agent 自身问题（预算分配错误），不是 PlanGate 的级联浪费
		res.Release() // 释放并发槽位，让其他等待会话有机会准入
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
			fmt.Sprintf("Tokens %d < locked price %d for %s", tokens, lockedPrice, toolName),
			map[string]interface{}{
				"session_id":   res.SessionID,
				"locked_price": lockedPrice,
				"mode":         "plan_and_solve",
			})
	}

	// token 余额 ≥ 锁定价格 → 直接执行，绕过实时 LoadShedding
	// executeStepDirect 内部会推进 CurrentStep 计数，并在最后一步时自动释放槽位
	return s.executeStepDirect(ctx, req, res.SessionID)
}

// handleReActMode ReAct 模式兜底路径：完全委托给标准 MCPGovernor 进行负载削减判断
//
// 调用场景（三种）：
//   1. P&S 消融模式（disableBudgetLock=true）的首步和后续步：测试「无预算锁」时的基线
//   2. P&S 会话调用了 DAG 以外的工具：handleReservedStep 降级调用
//   3. 无任何会话上下文的散装工具调用（handleToolsCall 最后的 fallback）
//
// MCPGovernor.HandleToolCall 内部执行标准动态定价检查：
//   tokens < ownPrice × toolWeight → 触发 LoadShedding → 返回 429 等效错误
func (s *MCPDPServer) handleReActMode(ctx context.Context, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	handler, ok := s.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 委托给 MCPGovernor：ownPrice 由 overloadDetection.go 后台持续更新（Eq.6）
	// 此处不做任何沉没成本保护 → 适用于「不需要状态跟踪的单次调用」
	return s.governor.HandleToolCall(ctx, req, handler)
}

// handleReActFirstStep 处理 ReAct 会话首步 (Step 0)：宽松准入 + 创建会话跟踪
//
// ┌─────────────────────────────────────────────────────────────────────┐
// │ 论文 §3.4 Sunk-Cost-Aware Dynamic Pricing (Algorithm 1, ReAct-0)  │
// │                                                                   │
// │ Eq.(3): P_step0 = P_base × I(t) × L(t)                           │
// │   P_base = intensityPriceBase (配置参考基价)                      │
// │   I(t) ∈ [0,1] = 治理强度 (§3.5 GovernanceIntensity)             │
// │   L(t) = N_active / M = 网关负载比 (活跃会话数/容量上限)         │
// │                                                                   │
// │ 3D 定价曲面: 只有后端与网关同时承压时 step-0 价格才高             │
// └─────────────────────────────────────────────────────────────────────┘
//
// 优化 3 (Relaxed Step0 Pre-screening):
//   - 零负载时（ownPrice==0）直接放行，不走 MCPGovernor 标准准入
//   - 有负载时仅做轻量级价格对比（使用基础价格，不含工具权重），降低首步拒绝率
//
// 会话容量门控：ReAct 会话接入 sessionCap，与 P&S 共享并发槽位
func (s *MCPDPServer) handleReActFirstStep(
	ctx context.Context, req *mcpgov.JSONRPCRequest, sessionID string,
) *mcpgov.JSONRPCResponse {
	// 1. 解析参数
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "无效的工具调用参数", err.Error())
	}
	handler, ok := s.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	// 1b. 会话容量门控 — 与 P&S 共享同一 sessionCap 容量池
	// 方案 B: 带超时排队，agent 到达时如果 cap 满，等待一段时间而非立即拒绝
	if s.sessionCap != nil {
		select {
		case s.sessionCap <- struct{}{}:
			// 获取到槽位
		case <-time.After(s.sessionCapWait):
			// 排队超时 → 拒绝
			log.Printf("[PlanGate ReAct Step0] session=%s REJECTED (session cap full after %v wait)", sessionID, s.sessionCapWait)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				"ReAct session cap full",
				map[string]interface{}{
					"session_id":  sessionID,
					"rejected_at": "step_0",
				})
		case <-ctx.Done():
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, "context cancelled while waiting for session cap", nil)
		}
	}

	// 构造释放函数（幂等，用于会话结束/超时/失败时归还槽位）
	var capRelease func()
	if s.sessionCap != nil {
		cap := s.sessionCap
		capRelease = func() { <-cap }
	}

	// 2. Step-0 并发限流：使用原子计数器 reactStep0Inflight 防止 step-0 洪峰
	// 动机：ReAct 新会话到达时，同一时刻可能有大量 step-0 请求并发，需要限流防止雪崩
	ownPrice := s.governor.GetOwnPrice()      // 后台 Eq.(6) 实时更新的市场价格
	intensity := s.getGovernanceIntensity()   // 后台 §3.5 实时更新的治理强度 I(t)
	current := atomic.AddInt64(&s.reactStep0Inflight, 1) // 原子递增，记录当前并发 step-0 数

	if current > s.reactStep0Limit {
		// 超过 step-0 并发软上限 → 降级为 MCPGovernor 标准准入（更严格的负载削减）
		// 这是一种「背压」机制：轻压时用宽松的 Eq.(3) 准入，重压时退化为全量定价检查
		log.Printf("[PlanGate ReAct Step0] session=%s GATED (inflight=%d > limit=%d, intensity=%.3f, ownPrice=%d)",
			sessionID, current, s.reactStep0Limit, intensity, ownPrice)
		resp := s.governor.HandleToolCall(ctx, req, handler)
		atomic.AddInt64(&s.reactStep0Inflight, -1) // 无论成败都释放计数
		if resp.Error == nil {
			// 标准准入通过 → 仍需在 reactSessions 中建立状态跟踪，供后续 K≥1 步使用
			s.reactSessions.Create(sessionID, capRelease)
			s.reactSessions.Advance(sessionID) // step 0 完成 → CurrentStep = 1
		} else if capRelease != nil {
			capRelease() // 被标准准入拒绝 → 归还 sessionCap 槽位，不能泄漏
		}
		return resp
	}

	// 2b. §3.4 Intensity × GatewayLoad 联合 Step-0 经济准入
	// >>> Eq.(3): P_step0 = P_base × I(t) × L(t)
	//   P_base = intensityPriceBase（配置项，参考市场情绪基价）
	//   I(t)   = intensity（[0,1]，来自 §3.5 强度跟踪器，反映后端 API 压力）
	//   L(t)   = N_active / M（[0,1]，网关当前活跃会话数 / 最大并发数，反映网关自身压力）
	//
	// 「3D 定价曲面」的含义：只有在 I(t) > 0（后端有压力）AND L(t) > 0（网关接近满载）时，
	// step-0 价格才显著升高。单一维度压力不会过度惩罚新会话。
	if s.intensityPriceBase > 0 && intensity > 0.01 {
		// intensity ≤ 0.01 说明滞回门控未激活，系统处于零负载状态 → 跳过所有经济检查
		activeCount := s.reactSessions.ActiveCount() // N_active：当前活跃的 ReAct 会话数
		gatewayLoad := float64(activeCount) / float64(s.reactStep0Limit) // L(t) = N/M
		if gatewayLoad > 1.0 {
			gatewayLoad = 1.0 // 饱和剪裁：负载比不超过 1.0
		}
		// 计算 step-0 准入门槛价格
		step0Price := int64(s.intensityPriceBase * intensity * gatewayLoad) // Eq.(3)
		step0Tokens := int64(0)
		if params.Meta != nil {
			step0Tokens = params.Meta.Tokens // Agent 在 _meta.tokens 中携带的预算余额
		}
		if step0Tokens < step0Price {
			// step-0 经济拒绝：tokens 不足以支付当前门槛价格
			// 此时「浪费=0」，因为 step-0 尚未消耗任何后端算力
			atomic.AddInt64(&s.reactStep0Inflight, -1)
			if capRelease != nil {
				capRelease() // 归还 sessionCap 槽位
			}
			log.Printf("[PlanGate ReAct Step0] session=%s INTENSITY REJECT (tokens=%d < price=%d, intensity=%.3f, active=%d, load=%.2f)",
				sessionID, step0Tokens, step0Price, intensity, activeCount, gatewayLoad)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				fmt.Sprintf("Step-0 intensity reject: tokens %d < price %d (intensity %.2f)", step0Tokens, step0Price, intensity),
				map[string]interface{}{
					"session_id":  sessionID,
					"rejected_at": "step_0_intensity",
				})
		}
	}

	// 并发限制内 + 经济准入通过（或负载为零）→ 零负载免通行
	// intensity < 0.01 时完全绕过价格检查（等效 §3.4 「零负载免通行」优化）
	log.Printf("[PlanGate ReAct Step0] session=%s FREE PASS (inflight=%d, intensity=%.3f, ownPrice=%d)",
		sessionID, current, intensity, ownPrice)

	// 3. 执行首步工具调用（绕过 MCPGovernor LoadShedding，因为 step-0 已做轻量级经济检查）
	result, err := handler(ctx, params)
	atomic.AddInt64(&s.reactStep0Inflight, -1) // 无论成败都释放飞行中计数
	if err != nil {
		if capRelease != nil {
			capRelease() // 工具执行失败（如后端 timeout）→ 归还 sessionCap 槽位
		}
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, err.Error(), nil)
	}

	// 4. step-0 执行成功 → 在 reactSessions 中创建状态跟踪
	// 后续 K≥1 步到达时，handleToolsCall 会查找此状态并路由到 handleReActSunkCostStep
	s.reactSessions.Create(sessionID, capRelease) // 挂载 capRelease，会话结束时自动归还槽位
	s.reactSessions.Advance(sessionID)            // step 0 完成 → CurrentStep 从 0 推进到 1

	// 5. 在响应 meta 中附加网关标识和当前市场价格（供 Agent 感知负载状态）
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Name = s.serverInfo.Name
	result.Meta.Price = strconv.FormatInt(ownPrice, 10) // 告知 Agent 当前 step-0 时刻的市场价格

	log.Printf("[PlanGate ReAct Step0] session=%s ADMITTED, tracking started", sessionID)
	return mcpgov.NewSuccessResponse(req.ID, result)
}

// handleReActSunkCostStep 处理 ReAct 会话后续步骤 (Step K≥1)：沉没成本递减价格
//
// ┌─────────────────────────────────────────────────────────────────────────┐
// │ 论文 §3.4 Sunk-Cost-Aware Dynamic Pricing (Algorithm 1, ReAct K≥1)    │
// │                                                                       │
// │ Eq.(4): P_K = P_eff × I(t) / (1 + K² · α_eff)                        │
// │         α_eff = α · (2 − I(t))                                        │
// │                                                                       │
// │ 二次折扣核心: K² 使折扣随步数激进递增                                 │
// │   K=3, α=0.4, I=1.0 → δ = 1/(1+9×0.4) = 0.22 (78% 折扣)            │
// │   强度调制: I=0.5 → α_eff=1.5α → 低负载时更强保护                    │
// │                                                                       │
// │ Table 3: Quadratic d(k)=1/(1+k²α), E[W]=O(ln N) vs Per-req O(N²)    │
// │ Claim 1: 浪费比率 E[W_req]/E[W_K²] ≥ Θ(N²/ln N)                     │
// └─────────────────────────────────────────────────────────────────────────┘
//
// 优化 1 (Zero-Load Free Pass): ownPrice==0 时直接放行，不做价格检查
// 优化 2 (Aggressive Sunk-Cost): adjustedPrice = toolPrice / (1 + K² × α)，二次方衰减
// 优化 3 (Committed Sessions): protectCommittedSessions=true 时，step 1+ 永不拒绝，消除级联浪费
func (s *MCPDPServer) handleReActSunkCostStep(
	ctx context.Context, req *mcpgov.JSONRPCRequest, rState *ReactSessionState,
) *mcpgov.JSONRPCResponse {
	// 1. 解析参数
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "无效的工具调用参数", err.Error())
	}
	handler, ok := s.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	tokens := int64(0)
	if params.Meta != nil {
		tokens = params.Meta.Tokens
	}

	K := rState.CurrentStep

	// 2. 「准入即承诺」保障模式（protectCommittedSessions=true 时启用）
	// 论文更激进的变体：step-0 准入后，step 1..N 永远不因价格原因拒绝
	// 代价：即使后端过载，已承诺会话仍强制占用资源 → 更高的完成率，但可能加重过载
	// 适用场景：对级联浪费极度敏感的场景（如真实 LLM 调用成本高昂）
	if s.protectCommittedSessions {
		intensity := s.getGovernanceIntensity()
		log.Printf("[PlanGate ReAct Committed] session=%s step=%d tool=%s tokens=%d intensity=%.3f COMMITTED PASS",
			rState.SessionID, K, params.Name, tokens, intensity)

		result, err := handler(ctx, params)
		if err != nil {
			// 承诺保障下的瞬态错误重试策略：等待 2s 后重试一次
			// 原因：已承诺会话「必须完成」，单次瞬态错误不应导致整个会话废弃
			log.Printf("[PlanGate ReAct Committed] session=%s step=%d tool=%s RETRY after transient error: %v",
				rState.SessionID, K, params.Name, err)
			time.Sleep(2 * time.Second)
			result, err = handler(ctx, params)
		}
		if err != nil {
			// 重试后仍失败 → 返回错误但「不删除会话状态」
			// Agent 下次重试相同步骤时仍能享受承诺保障（会话状态保留）
			log.Printf("[PlanGate ReAct Committed] session=%s step=%d tool=%s TRANSIENT ERROR (session preserved): %v",
				rState.SessionID, K, params.Name, err)
			if s.reputationMgr != nil {
				s.reputationMgr.RecordFailure(rState.SessionID) // 记录失败但不扣信誉（非 Agent 责任）
			}
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, err.Error(), nil)
		}

		s.reactSessions.Advance(rState.SessionID) // 推进 CurrentStep，为下一步准备

		if result.Meta == nil {
			result.Meta = &mcpgov.ResponseMeta{}
		}
		result.Meta.Name = s.serverInfo.Name
		result.Meta.Price = "0" // 承诺会话：不向 Agent 暴露价格压力，避免 Agent 主动放弃

		return mcpgov.NewSuccessResponse(req.ID, result)
	}

	// 3. §3.4 沉没成本感知准入：仅用真实后端负载信号调制价格（不叠加影子定价）
	// 设计原则：sessionCap 已在 step-0 完成「新会话门控」，此处只保护「已准入会话」
	// 已准入会话不应因「其他新会话涌入导致 capRatio 升高」而被拒绝（那是 sessionCap 的职责）
	ownPrice := s.governor.GetOwnPrice()    // Eq.(6) 计算的实时市场价格：反映后端真实排队压力
	intensity := s.getGovernanceIntensity() // §3.5 I(t)：滞回门控后的平滑治理强度

	// 仅用于诊断日志，capRatio 不参与定价（避免已准入会话受新会话竞争影响）
	activeCount := s.reactSessions.ActiveCount()
	capRatio := float64(activeCount) / float64(s.reactStep0Limit)

	if intensity < 0.01 {
		// 滞回门控未激活（I(t) ≈ 0）→ 系统处于空闲状态，无条件放行
		// 等效「零负载免通行」：后端无压力时不做任何经济检查，最大化 ReAct 完成率
		log.Printf("[PlanGate ReAct Sunk-Cost] session=%s step=%d tool=%s FREE PASS (intensity=%.3f, ownPrice=%d, active=%d, capRatio=%.2f)",
			rState.SessionID, K, params.Name, intensity, ownPrice, activeCount, capRatio)
	} else {
		// 后端负载激活（I(t) > 0.01）→ 执行 Eq.(4) 沉没成本折扣定价

		// 基价回退：当 ownPrice=0 但 intensity>0 时，说明 sessionCap 硬门控导致
		// proxyOverloadDetector 看不到排队压力（所有请求在进网关前就被限流了），
		// 此时用 intensityPriceBase 作为兜底基价，避免价格恒为 0 导致折扣无效
		basePrice := float64(ownPrice)
		if basePrice < 1 && s.intensityPriceBase > 0 {
			basePrice = s.intensityPriceBase // 兜底基价（由配置设定，通常 = maxToken × n）
		}

		// >>> Eq.(4): P_K = P_eff × I(t) / (1 + K² · α_eff)
		//
		// 逐步计算：
		//   Step A: effectiveBasePrice = P_own × I(t)
		//     含义：将市场价格按治理强度缩放。I(t)=0.5 时价格减半，轻负载下降低门槛
		effectiveBasePrice := basePrice * intensity

		//   Step B: α_eff = α · (2 − I(t))
		//     含义：强度调制的自适应折扣系数。I(t)低时 α_eff 更大 → 折扣更激进，保护低负载下已走多步的会话
		//     I=1.0 → α_eff=α；I=0.5 → α_eff=1.5α（折扣增强 50%）
		effectiveAlpha := s.sunkCostAlpha * (2.0 - intensity)

		//   Step C: δ(K) = discountFunc(K, α_eff) — 折扣因子，由 discount_func.go 计算
		//     默认二次方折扣：δ(K) = 1/(1 + K²·α_eff)，对应 Table 3 中 E[W]=O(ln N) 行
		//     K=3, α=0.5, I=1.0 → α_eff=0.5, δ=1/(1+4.5)=0.18 (82% 折扣)
		discountFactor := s.discountFunc(float64(K), effectiveAlpha)

		//   Step D: P_K = effectiveBasePrice × δ(K) = P_eff × I(t) × δ(K)
		//     最终准入门槛价格，随步骤 K 递减（沉没成本越高，拒绝代价越大，门槛越低）
		adjustedPrice := int64(effectiveBasePrice * discountFactor)

		log.Printf("[PlanGate ReAct Sunk-Cost] session=%s step=%d tool=%s tokens=%d ownPrice=%d intensity=%.3f capRatio=%.2f adjusted=%d discountFunc=%s",
			rState.SessionID, K, params.Name, tokens, ownPrice, intensity, capRatio, adjustedPrice, s.discountFuncName)

		if tokens < adjustedPrice {
			// 沉没成本折扣后仍不满足价格条件 → 系统极度过载，必须拒绝以保护其他会话
			// 此时释放会话状态 + 归还 sessionCap 槽位，让新会话有机会准入
			s.reactSessions.ReleaseAndDelete(rState.SessionID)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
				fmt.Sprintf("ReAct sunk-cost: tokens %d < adjusted price %d (orig %d, step %d, intensity %.2f)",
					tokens, adjustedPrice, ownPrice, K, intensity),
				map[string]interface{}{
					"session_id":     rState.SessionID,
					"adjusted_price": adjustedPrice,
					"original_price": ownPrice,
					"step":           K,
					"intensity":      intensity,
					"mode":           "react_sunk_cost_intensity",
				})
		}
	}

	// 4. 准入检查通过 → 直接执行工具调用（绕过 MCPGovernor LoadShedding）
	// 原因：沉没成本折扣已经是比 LoadShedding 更宽松的判断，不需要再次检查 ownPrice
	result, err := handler(ctx, params)
	if err != nil {
		// 后端瞬态错误（如工具超时、rate limit 429）→ 保留会话状态，不终止会话
		// Agent 重试相同步骤时，K 值不变，仍享有当前步骤的沉没成本折扣保护
		// 注意：这里区分于「准入拒绝」—— 准入拒绝删除会话，执行错误保留会话
		log.Printf("[PlanGate ReAct Sunk-Cost] session=%s step=%d tool=%s TRANSIENT ERROR (session preserved): %v",
			rState.SessionID, K, params.Name, err)
		if s.reputationMgr != nil {
			s.reputationMgr.RecordFailure(rState.SessionID) // 轻微扣分（非欺诈行为，仅记录）
		}
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, err.Error(), nil)
	}

	// 5. 工具调用成功 → CurrentStep + 1，为下一步 Eq.(4) 计算的 K 值更新
	s.reactSessions.Advance(rState.SessionID)

	// 6. 在响应中附加网关标识和当前市场价格（供 Agent 决策：是否继续下一步）
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Name = s.serverInfo.Name
	result.Meta.Price = strconv.FormatInt(ownPrice, 10) // 透传当前市场价格，Agent 可据此调整策略

	return mcpgov.NewSuccessResponse(req.ID, result)
}

// executeStepDirect P&S 模式专用执行路径：绕过 MCPGovernor LoadShedding 直接调用工具
//
// 「直接执行」的合理性：
//   P&S 会话在 step-0 已通过 Eq.(1) 预检 + 获取并发槽位 + 创建价格锁（Eq.2）
//   后续每步在 handleReservedStep 中已用 lockedPrice 做了 token 余额检查
//   因此调用此函数时，「准入判定」已完成，无需再经过 MCPGovernor 的动态定价流程
func (s *MCPDPServer) executeStepDirect(ctx context.Context, req *mcpgov.JSONRPCRequest, sessionID string) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	handler, ok := s.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
			fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	result, err := handler(ctx, params)
	if err != nil {
		// 工具后端执行失败（网络超时、下游服务不可用等）
		// P&S 模式下执行失败即终止会话：DAG 步骤有依赖关系，中间步骤失败无法跳过
		if res, ok := s.budgetMgr.Get(sessionID); ok {
			res.Release() // 立即释放 sessionCap 槽位，避免槽位长期被失败会话占用
		}
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, err.Error(), nil)
	}

	// 执行成功 → 推进 CurrentStep 计数（用于判断是否到达 DAG 最后一步）
	s.budgetMgr.Advance(sessionID)

	// 在响应 meta 中附加网关标识
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Name = s.serverInfo.Name

	// 使用 Eq.(2) 锁定价格作为响应透传价格（非实时市场价，体现时间隔离保护）
	// 同时检测是否已完成 DAG 所有步骤 —— 若是，则归还 sessionCap 并发槽位
	if res, ok := s.budgetMgr.Get(sessionID); ok {
		if lp, exists := res.LockedPrices[params.Name]; exists {
			result.Meta.Price = strconv.FormatInt(lp, 10) // 告知 Agent「此步实际扣费价格」
		}
		if res.CurrentStep >= len(res.Plan.Steps) {
			// DAG 所有步骤执行完毕 → 「正常完成」路径，释放槽位
			res.Release()
		}
	}

	return mcpgov.NewSuccessResponse(req.ID, result)
}

// calculateDAGTotalCost 计算 DAG 全链路总价格（对应 Eq.(1) 的求和操作）
//
// >>> Eq.(1): C_total = Σ_{i=1}^{n} P_eff(t_i)
// GetToolEffectivePrice(t_i) 内部计算：P_eff(t_i) = P_own × toolWeight[t_i]
//   P_own       = 当前动态定价引擎（Eq.6）计算的实时市场基价
//   toolWeight  = 各工具的权重系数（重型工具定价更高，如 code_execute > web_search）
//
// 注意：此函数使用「调用时刻的实时价格」，返回值后续会被 Eq.(2) 锁定为快照
// 因此 calculateDAGTotalCost 的结果既是「准入门槛」也是「后续价格锁的基础」
func (s *MCPDPServer) calculateDAGTotalCost(plan *HTTPDAGPlan) int64 {
	var total int64
	for _, step := range plan.Steps {
		// 逐步累加：每个 DAG 步骤对应一个工具，取其当前有效价格
		total += s.governor.GetToolEffectivePrice(step.ToolName)
	}
	return total
}
