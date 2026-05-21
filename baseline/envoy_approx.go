package baseline

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"

	mcpgov "mcp-governance"
)

type EnvoyApproxConfig struct {
	GlobalQPS     float64
	GlobalBurst   int64
	GlobalMaxConc int64
	RouteQPS      float64
	RouteBurst    int64
	RouteMaxConc  int64
}

type envoyApproxHook struct {
	cfg   EnvoyApproxConfig
	store *MemoryStateStore
}

type envoyHookState struct {
	globalConcKey string
	routeConcKey  string
}

func (h *envoyApproxHook) Before(_ context.Context, req ReqDesc) (Decision, HookState) {
	if !h.store.AllowToken("envoy:rate:global", h.cfg.GlobalQPS, h.cfg.GlobalBurst) {
		return Decision{Allow: false, Reason: "envoy_global_rate_limited"}, nil
	}

	if req.Route != "" && h.cfg.RouteQPS > 0 && h.cfg.RouteBurst > 0 {
		if !h.store.AllowToken("envoy:rate:route:"+req.Route, h.cfg.RouteQPS, h.cfg.RouteBurst) {
			return Decision{Allow: false, Reason: "envoy_route_rate_limited"}, nil
		}
	}

	hs := envoyHookState{}
	if h.cfg.GlobalMaxConc > 0 {
		if !h.store.AcquireInFlight("envoy:conc:global", h.cfg.GlobalMaxConc) {
			return Decision{Allow: false, Reason: "envoy_global_circuit_open"}, nil
		}
		hs.globalConcKey = "envoy:conc:global"
	}

	if req.Route != "" && h.cfg.RouteMaxConc > 0 {
		routeKey := "envoy:conc:route:" + req.Route
		if !h.store.AcquireInFlight(routeKey, h.cfg.RouteMaxConc) {
			if hs.globalConcKey != "" {
				h.store.ReleaseInFlight(hs.globalConcKey)
			}
			return Decision{Allow: false, Reason: "envoy_route_circuit_open"}, nil
		}
		hs.routeConcKey = routeKey
	}

	return Decision{Allow: true}, hs
}

func (h *envoyApproxHook) After(_ context.Context, _ ReqDesc, state HookState, _ error) {
	hs, ok := state.(envoyHookState)
	if !ok {
		return
	}
	if hs.routeConcKey != "" {
		h.store.ReleaseInFlight(hs.routeConcKey)
	}
	if hs.globalConcKey != "" {
		h.store.ReleaseInFlight(hs.globalConcKey)
	}
}

type EnvoyApproxGateway struct {
	nodeName   string
	tools      map[string]mcpgov.MCPTool
	handlers   map[string]mcpgov.ToolCallHandler
	serverInfo mcpgov.Implementation
	hook       ProxyHook
	store      *MemoryStateStore

	stats EnvoyApproxStats
}

type EnvoyApproxStats struct {
	TotalRequests    int64
	SuccessRequests  int64
	RejectedRequests int64
	ErrorRequests    int64
}

func NewEnvoyApproxGateway(nodeName string, cfg EnvoyApproxConfig) *EnvoyApproxGateway {
	if cfg.GlobalQPS <= 0 {
		cfg.GlobalQPS = 65
	}
	if cfg.GlobalBurst <= 0 {
		cfg.GlobalBurst = 400
	}
	if cfg.GlobalMaxConc <= 0 {
		cfg.GlobalMaxConc = 55
	}
	if cfg.RouteQPS <= 0 {
		cfg.RouteQPS = 35
	}
	if cfg.RouteBurst <= 0 {
		cfg.RouteBurst = 100
	}
	if cfg.RouteMaxConc <= 0 {
		cfg.RouteMaxConc = 20
	}

	store := NewMemoryStateStore()
	return &EnvoyApproxGateway{
		nodeName:   nodeName,
		tools:      make(map[string]mcpgov.MCPTool),
		handlers:   make(map[string]mcpgov.ToolCallHandler),
		serverInfo: mcpgov.Implementation{Name: nodeName, Version: "1.0.0"},
		hook:       &envoyApproxHook{cfg: cfg, store: store},
		store:      store,
	}
}

func (gw *EnvoyApproxGateway) RegisterTool(tool mcpgov.MCPTool, handler mcpgov.ToolCallHandler) {
	gw.tools[tool.Name] = tool
	gw.handlers[tool.Name] = handler
}

func (gw *EnvoyApproxGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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

func (gw *EnvoyApproxGateway) handleToolsCall(ctx context.Context, r *http.Request, req *mcpgov.JSONRPCRequest) *mcpgov.JSONRPCResponse {
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
			fmt.Sprintf("Envoy approx rejected request: %s", decision.Reason),
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

func (gw *EnvoyApproxGateway) GetStats() (total, success, rejected, errors int64) {
	return atomic.LoadInt64(&gw.stats.TotalRequests),
		atomic.LoadInt64(&gw.stats.SuccessRequests),
		atomic.LoadInt64(&gw.stats.RejectedRequests),
		atomic.LoadInt64(&gw.stats.ErrorRequests)
}

func (gw *EnvoyApproxGateway) GetInFlight(route string) (int64, int64) {
	global := gw.store.InFlight("envoy:conc:global")
	if route == "" {
		return global, 0
	}
	return global, gw.store.InFlight("envoy:conc:route:"+route)
}
