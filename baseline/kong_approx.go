package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
)

type KongApproxConfig struct {
	GlobalQPS    float64
	GlobalBurst  int64
	SessionQPS   float64
	SessionBurst int64
	SessionTTL   time.Duration
}

type kongApproxHook struct {
	cfg   KongApproxConfig
	store *MemoryStateStore
}

func (h *kongApproxHook) Before(_ context.Context, req ReqDesc) (Decision, HookState) {
	if !h.store.AllowToken("kong:quota:global", h.cfg.GlobalQPS, h.cfg.GlobalBurst) {
		return Decision{Allow: false, Reason: "kong_global_quota_exceeded"}, nil
	}

	consumerKey := req.ConsumerKey
	if consumerKey == "" {
		consumerKey = "unknown"
	}
	if !h.store.AllowToken("kong:quota:consumer:"+consumerKey, h.cfg.SessionQPS, h.cfg.SessionBurst) {
		return Decision{Allow: false, Reason: "kong_consumer_quota_exceeded"}, consumerKey
	}
	return Decision{Allow: true}, consumerKey
}

func (h *kongApproxHook) After(_ context.Context, _ ReqDesc, _ HookState, _ error) {}

type KongApproxGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation
	hook       ProxyHook
	store      *MemoryStateStore
	stopCh     chan struct{}

	stats KongApproxStats
}

type KongApproxStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64
	ErrorRequests    int64
}

func NewKongApproxGateway(nodeName string, cfg KongApproxConfig) *KongApproxGateway {
	if cfg.GlobalQPS <= 0 {
		cfg.GlobalQPS = 65
	}
	if cfg.GlobalBurst <= 0 {
		cfg.GlobalBurst = 400
	}
	if cfg.SessionQPS <= 0 {
		cfg.SessionQPS = 2
	}
	if cfg.SessionBurst <= 0 {
		cfg.SessionBurst = 5
	}
	if cfg.SessionTTL <= 0 {
		cfg.SessionTTL = 300 * time.Second
	}

	store := NewMemoryStateStore()
	gw := &KongApproxGateway{
		nodeName:   nodeName,
		tools:      make(map[string]mcpgov.MCPTool),
		handlers:   make(map[string]mcpgov.ToolCallHandler),
		serverInfo: mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		hook:       &kongApproxHook{cfg: cfg, store: store},
		store:      store,
		stopCh:     make(chan struct{}),
	}
	go store.StartCleanup(30*time.Second, cfg.SessionTTL, gw.stopCh)
	return gw
}

func (gw *KongApproxGateway) Close() {
	select {
	case <-gw.stopCh:
		return
	default:
		close(gw.stopCh)
	}
}

func (gw *KongApproxGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
}

func (gw *KongApproxGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	var req mcpgov.JSONRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, mcpgov.NewErrorResponse(nil, -32700, "JSON 解析错误", err.Error()))
		return
	}
	if req.JSONRPC != "2.0" {
		writeJSON(w, mcpgov.NewErrorResponse(req.ID, -32600, "jsonrpc 版本必须为 2.0", nil))
		return
	}

	ctx := r.Context()
	var resp *mcpgov.JSONRPCResponse
	switch req.Method {
	case "initialize":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{
			"protocolVersion": "2024-11-05",
			"serverInfo":      gw.serverInfo,
			"capabilities":    map[string]interface{}{"tools": map[string]interface{}{"listChanged": false}},
		})
	case "tools/list":
		tools := make([]mcpgov.MCPTool, 0, len(gw.tools))
		for _, t := range gw.tools {
			tools = append(tools, t)
		}
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{"tools": tools})
	case "tools/call":
		resp = gw.handleToolsCall(ctx, r, &req)
	case "ping":
		resp = mcpgov.NewSuccessResponse(req.ID, map[string]interface{}{})
	default:
		resp = mcpgov.NewErrorResponse(req.ID, -32601, fmt.Sprintf("MCP 方法 '%s' 未找到", req.Method), nil)
	}
	writeJSON(w, resp)
}

func (gw *KongApproxGateway) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
	var params mcpgov.MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return mcpgov.NewErrorResponse(req.ID, -32602, "无效的工具调用参数", err.Error())
	}
	handler, ok := gw.handlers[params.Name]
	if !ok {
		return mcpgov.NewErrorResponse(req.ID, -32601, fmt.Sprintf("工具 '%s' 未注册", params.Name), nil)
	}

	atomic.AddInt64(&gw.stats.TotalRequests, 1)
	desc := buildReqDesc(r, &params)
	decision, hookState := gw.hook.Before(ctx, desc)
	if !decision.Allow {
		atomic.AddInt64(&gw.stats.RejectedRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32002,
			fmt.Sprintf("Kong approx rejected request: %s", decision.Reason),
			map[string]string{"reason": decision.Reason, "name": gw.nodeName})
	}

	result, err := handler(ctx, params)
	gw.hook.After(ctx, desc, hookState, err)
	if err != nil {
		atomic.AddInt64(&gw.stats.ErrorRequests, 1)
		return mcpgov.NewErrorResponse(req.ID, -32603, err.Error(), nil)
	}

	atomic.AddInt64(&gw.stats.SuccessRequests, 1)
	if result.Meta == nil {
		result.Meta = &mcpgov.ResponseMeta{}
	}
	result.Meta.Price = "0"
	result.Meta.Name = gw.nodeName
	return mcpgov.NewSuccessResponse(req.ID, result)
}

func (gw *KongApproxGateway) GetStats() (total, success, rejected, errors int64) {
	return atomic.LoadInt64(&gw.stats.TotalRequests),
		atomic.LoadInt64(&gw.stats.SuccessRequests),
		atomic.LoadInt64(&gw.stats.RejectedRequests),
		atomic.LoadInt64(&gw.stats.ErrorRequests)
}
