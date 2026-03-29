package plangate

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"

	mcpgov "mcp-governance"
)

// handlePlanAndSolveFirstStep 处理 P&S 首步：预检准入 + 预算锁创建
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

	// 2. 解析预算（Header 优先）
	if budgetStr := r.Header.Get(HeaderTotalBudget); budgetStr != "" {
		if b, err := strconv.ParseInt(budgetStr, 10, 64); err == nil {
			plan.Budget = b
		}
	}
	if plan.Budget <= 0 {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "budget 必须大于 0", nil)
	}

	// 3. 验证 DAG 无环
	if err := validateHTTPDAG(&plan); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, err.Error(), nil)
	}

	// 4. 创新点 1: Pre-flight Atomic Admission — 计算全链路总价格
	totalCost := s.calculateDAGTotalCost(&plan)

	log.Printf("[PlanGate Pre-flight] session=%s budget=%d totalCost=%d steps=%d",
		plan.SessionID, plan.Budget, totalCost, len(plan.Steps))

	if plan.Budget < totalCost {
		// 第 0 步拒绝 — 零级联算力浪费
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

	// 4b. 并发会话容量检查（创新点 1 的后端保护层）
	// 若并发槽位已满，在第 0 步拒绝 — 仍属零级联
	if s.sessionCap != nil {
		select {
		case s.sessionCap <- struct{}{}:
			// 获取到槽位，继续准入
		default:
			log.Printf("[PlanGate Pre-flight] session=%s REJECTED (session cap full)", plan.SessionID)
			return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
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
		log.Printf("[PlanGate Pre-flight] session=%s ADMITTED, reservation created", plan.SessionID)
	} else {
		// 消融模式下无预算锁，需直接记录释放函数
		if s.sessionCap != nil {
			res := s.budgetMgr.Reserve(s.governor, &plan, totalCost)
			cap := s.sessionCap
			res.releaseFn = func() { <-cap }
		}
		log.Printf("[PlanGate Pre-flight] session=%s ADMITTED (no-lock mode)", plan.SessionID)
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
	ctx context.Context, req *mcpgov.JSONRPCRequest, res *HTTPSessionReservation,
) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	toolName := params.Name

	// 获取锁定价格
	lockedPrice, exists := res.LockedPrices[toolName]
	if !exists {
		// 工具不在原始 DAG 中 → 降级为 ReAct 模式
		res.Release()
		return s.handleReActMode(ctx, req)
	}

	// 使用锁定价格检查 tokens
	tokens := int64(0)
	if params.Meta != nil {
		tokens = params.Meta.Tokens
	}

	log.Printf("[PlanGate Reserved] session=%s step=%d tool=%s tokens=%d lockedPrice=%d",
		res.SessionID, res.CurrentStep, toolName, tokens, lockedPrice)

	if tokens < lockedPrice {
		res.Release() // 会话中途被拒绝（级联失败），释放槽位
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeOverloaded,
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

	return s.governor.HandleToolCall(ctx, req, handler)
}

// executeStepDirect 直接执行工具调用（绕过 LoadShedding）
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
		if res, ok := s.budgetMgr.Get(sessionID); ok {
			res.Release()
		}
		return mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInternalError, err.Error(), nil)
	}

	// 推进步骤
	s.budgetMgr.Advance(sessionID)

	// 附加元数据
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Name = s.serverInfo.Name

	// 使用锁定价格作为响应价格；若最后一步完成则释放并发槽位
	if res, ok := s.budgetMgr.Get(sessionID); ok {
		if lp, exists := res.LockedPrices[params.Name]; exists {
			result.Meta.Price = strconv.FormatInt(lp, 10)
		}
		if res.CurrentStep >= len(res.Plan.Steps) {
			res.Release()
		}
	}

	return mcpgov.NewSuccessResponse(req.ID, result)
}

// calculateDAGTotalCost 计算 DAG 全链路总价格
func (s *MCPDPServer) calculateDAGTotalCost(plan *HTTPDAGPlan) int64 {
	var total int64
	for _, step := range plan.Steps {
		total += s.governor.GetToolEffectivePrice(step.ToolName)
	}
	return total
}
