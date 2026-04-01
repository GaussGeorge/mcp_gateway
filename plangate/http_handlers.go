package plangate

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	mcpgov "mcp-governance"
)

// writeJSON 将响应序列化为 JSON 并写入 HTTP response
func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
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

	switch req.Method {
	case mcpgov.MethodInitialize:
		resp = s.handleInitialize(&req)
	case mcpgov.MethodToolsList:
		resp = s.handleToolsList(&req)
	case mcpgov.MethodToolsCall:
		resp = s.handleToolsCall(ctx, r, &req)
	case mcpgov.MethodPing:
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, mcpgov.CodeMethodNotFound,
			fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
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
func (s *MCPDPServer) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	dagHeader := r.Header.Get(HeaderPlanDAG)
	sessionID := r.Header.Get(HeaderSessionID)

	// ====== Plan-and-Solve 模式: 首步（带 X-Plan-DAG）======
	if dagHeader != "" {
		return s.handlePlanAndSolveFirstStep(ctx, r, req, dagHeader, sessionID)
	}

	// ====== Plan-and-Solve 模式: 后续步骤（带 X-Session-ID + 预算锁）======
	if sessionID != "" && !s.disableBudgetLock {
		if res, ok := s.budgetMgr.Get(sessionID); ok {
			return s.handleReservedStep(ctx, req, res)
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
		// 已跟踪的 ReAct 会话 → 沉没成本递减价格
		if rState, ok := s.reactSessions.Get(sessionID); ok {
			return s.handleReActSunkCostStep(ctx, req, rState)
		}
		// 新 ReAct 会话首步 → 准入 + 创建跟踪
		return s.handleReActFirstStep(ctx, req, sessionID)
	}

	// ====== 无会话上下文的 ReAct 回退 ======
	return s.handleReActMode(ctx, req)
}
