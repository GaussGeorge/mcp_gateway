package plangate

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

// writeJSON 将响应序列化为 JSON 并写入 HTTP response
func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

// buildReservationFromShared reconstructs a lightweight HTTPSessionReservation
// from a SharedPSRecord retrieved from the shared state store.
// The releaseFn releases both the local budgetMgr entry (no-op if absent) and
// the shared store slot, ensuring idempotent cleanup.
func (s *MCPDPServer) buildReservationFromShared(rec *SharedPSRecord) *HTTPSessionReservation {
	res := &HTTPSessionReservation{
		SessionID:     rec.SessionID,
		TotalCost:     rec.TotalCost,
		LockedPrices:  cloneInt64Map(rec.LockedPrices),
		PlanHash:      rec.PlanHash,
		PriceHash:     rec.PriceHash,
		TotalSteps:    rec.TotalSteps,
		CurrentStep:   rec.CurrentStep,
		ExpiresAt:     time.Unix(0, rec.ExpiresUnix),
		sharedBacking: true,
	}
	if len(rec.PlanSteps) > 0 {
		res.Plan = &HTTPDAGPlan{
			SessionID: rec.SessionID,
			Budget:    rec.Budget,
			Steps:     cloneHTTPDAGSteps(rec.PlanSteps),
		}
	}
	// releaseFn: release the shared store slot (deduct global count).
	if s.sharedStateStore != nil {
		store := s.sharedStateStore
		sid := rec.SessionID
		res.releaseFn = func() {
			_ = store.ReleaseSession(context.Background(), sid)
		}
	}
	return res
}

// newStateMissError returns a JSON-RPC error that signals a cross-node state
// miss to the caller. Error code -32010 is PlanGate-specific (not in JSON-RPC
// or MCP standard ranges). The caller (load generator) recognises this code
// and increments state_miss_count.
func newStateMissError(id interface{}, sessionID string) *mcpgov.JSONRPCResponse {
	return mcpgov.NewErrorResponse(id, -32010,
		"PlanGate state miss: reservation not found on this node",
		map[string]interface{}{
			"session_id": sessionID,
			"state_miss": true,
		})
}

// ServeHTTP 实现 http.Handler，处理所有 MCP JSON-RPC 请求
func (s *MCPDPServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, mcpgov.NewErrorResponse(nil, mcpgov.CodeParseError, "读取请求体失败", err.Error()))
		return
	}

	var req mcpgov.JSONRPCRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, mcpgov.NewErrorResponse(nil, mcpgov.CodeParseError, "JSON 解析错误", err.Error()))
		return
	}
	if req.JSONRPC != mcpgov.JSONRPCVersion {
		writeJSON(w, mcpgov.NewErrorResponse(req.ID, mcpgov.CodeInvalidRequest, "jsonrpc 版本必须为 2.0", nil))
		return
	}

	ctx := r.Context()
	var resp *mcpgov.JSONRPCResponse

	gatewayStart := time.Now()

	switch req.Method {
	case mcpgov.MethodInitialize:
		resp = s.handleInitialize(&req)
	case mcpgov.MethodToolsList:
		resp = s.handleToolsList(&req)
	case mcpgov.MethodToolsCall:
		resp = s.handleToolsCall(ctx, w, r, &req)
	case mcpgov.MethodPing:
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}

	// 附加网关处理耗时到响应头（供延迟分解实验使用）
	gatewayElapsed := time.Since(gatewayStart)
	w.Header().Set("X-Gateway-Latency-Us", strconv.FormatInt(gatewayElapsed.Microseconds(), 10))

	// 附加节点 ID（多节点实验路由追踪）
	if s.nodeID != "" {
		w.Header().Set("X-Gateway-Node", s.nodeID)
	}

	writeJSON(w, resp)
}

func (s *MCPDPServer) handleInitialize(req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	return mcpgov.NewSuccessResponse(req.ID, mcpgov.MCPInitializeResult{
		ProtocolVersion: "2024-11-05",
		ServerInfo:      s.serverInfo,
		Capabilities:    mcpgov.ServerCapabilities{Tools: &mcpgov.ToolsCapability{ListChanged: false}},
	})
}

func (s *MCPDPServer) handleToolsList(req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	tools := make([]mcpgov.MCPTool, 0, len(s.tools))
	for _, t := range s.tools {
		tools = append(tools, t)
	}
	return mcpgov.NewSuccessResponse(req.ID, mcpgov.MCPToolsListResult{Tools: tools})
}

// handleToolsCall 核心：双模态路由 (创新点 3) + ReAct 沉没成本准入 (创新点 4)
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ Algorithm 1: PlanGate Admission Control — 路由入口              │
// │                                                               │
// │ 路由逻辑 (双模态 Dual-Mode):                                    │
// │   1. X-Plan-DAG 存在   → P&S 首步 (Eq.1 + Eq.2)               │
// │   2. X-Session-ID 匹配  → P&S 后续 (Eq.2 锁定价格)              │
// │   3. ReAct 已跟踪会话  → 沉没成本折扣 (Eq.4)                  │
// │   4. ReAct 新会话     → Step-0 宽松准入 (Eq.3)                │
// │   5. 无会话上下文    → MCPGovernor 标准准入                  │
// └─────────────────────────────────────────────────────────────────┘
func (s *MCPDPServer) handleToolsCall(ctx context.Context, w http.ResponseWriter, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	dagHeader := r.Header.Get(HeaderPlanDAG)
	sessionID := r.Header.Get(HeaderSessionID)

	// ====== PlanGate-R: Recovery Resume ======
	// X-Recovery-Mode: resume signals a recovery attempt for a CHECKPOINTED P&S session.
	// This check runs BEFORE all normal admission paths so recovery sessions are never
	// accidentally routed through standard step-0 pre-flight admission.
	if isRecoveryResumeRequest(r) {
		return s.handleRecoveryResumeWithWriter(ctx, w, r, req)
	}

	// ====== Plan-and-Solve 模式: 首步（带 X-Plan-DAG）======
	// >>> Algorithm 1, P&S 分支: Eq.(1) C_total + Eq.(2) LockedPrices
	if dagHeader != "" {
		return s.handlePlanAndSolveFirstStep(ctx, w, r, req, dagHeader, sessionID)
	}

	// ====== Plan-and-Solve 模式: 后续步骤（带 X-Session-ID + 预算锁）======
	// >>> Algorithm 1, P&S 后续: 使用 Eq.(2) 锁定价格，绕过 LoadShedding
	if sessionID != "" && !s.disableBudgetLock {
		if s.sharedStateStore != nil {
			rec, err := s.sharedStateStore.GetReservation(ctx, sessionID)
			if err == nil && rec != nil {
				res := s.buildReservationFromShared(rec)
				if resp := s.validateCommitmentForReservedStep(w, r, req, res); resp != nil {
					return resp
				}
				return s.handleReservedStep(ctx, req, res)
			}
		}
		if res, ok := s.budgetMgr.Get(sessionID); ok {
			if resp := s.validateCommitmentForReservedStep(w, r, req, res); resp != nil {
				return resp
			}
			return s.handleReservedStep(ctx, req, res)
		}
		// Local reservation not found — try shared store (multi-node fallback).
		if s.sharedStateStore != nil {
			rec, err := s.sharedStateStore.GetReservation(ctx, sessionID)
			if err == nil && rec != nil {
				// Reconstruct a lightweight reservation from shared state.
				res := s.buildReservationFromShared(rec)
				if resp := s.validateCommitmentForReservedStep(w, r, req, res); resp != nil {
					return resp
				}
				return s.handleReservedStep(ctx, req, res)
			}
		}
		// X-Session-Step > 0 with no reservation found anywhere → genuine state miss.
		stepIdx, _ := strconv.Atoi(r.Header.Get(HeaderSessionStep))
		if stepIdx > 0 {
			atomic.AddInt64(&s.stateMissCount, 1)
			// Return a special error so the caller can detect and count it.
			return newStateMissError(req.ID, sessionID)
		}
	}

	// ====== P&S 消融模式 (disableBudgetLock): 后续步骤走标准动态定价 ======
	if sessionID != "" && s.disableBudgetLock {
		if _, ok := s.budgetMgr.Get(sessionID); ok {
			return s.handleReActMode(ctx, req)
		}
	}

	// ====== ReAct 沉没成本感知准入 (创新点 4) ======
	if sessionID != "" {
		// >>> Algorithm 1, ReAct K≥1: Eq.(4) P_K = P_eff×I/(1+K²α_eff)
		if rState, ok := s.reactSessions.Get(sessionID); ok {
			return s.handleReActSunkCostStep(ctx, req, rState)
		}
		// >>> Algorithm 1, ReAct step-0: Eq.(3) P_step0 = P_base × I(t) × L(t)
		return s.handleReActFirstStep(ctx, req, sessionID)
	}

	// ====== 无会话上下文的 ReAct 回退 ======
	return s.handleReActMode(ctx, req)
}
