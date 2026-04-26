// cmd/gateway/main.go
// MCP 网关统一入口 — 支持四种治理策略 (NG / SRL / DP / DP-NoRegime)
// 网关接收发压机请求 → 应用治理逻辑 → 代理到 Python MCP 后端
//
// 用法:
//   go run ./cmd/gateway --mode dp          --port 9003 --backend http://127.0.0.1:8080
//   go run ./cmd/gateway --mode dp-noregime --port 9004 --backend http://127.0.0.1:8080
//   go run ./cmd/gateway --mode ng          --port 9001 --backend http://127.0.0.1:8080
//   go run ./cmd/gateway --mode srl         --port 9002 --backend http://127.0.0.1:8080
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
	"mcp-governance/baseline"
	"mcp-governance/plangate"
)

// proxyOverloadDetector 代理级过载检测器
// 在反向代理架构中，Go runtime 调度器延迟不反映实际负载，
// 因此用并发请求计数来驱动 DP 价格机制。
// 检测器参数（priceStep/decayStep/maxConc）从 MCPGovernor 的当前档位动态读取，
// 实现自适应过载检测：不同负载模式下使用不同的检测灵敏度。
type proxyOverloadDetector struct {
	gov            *mcpgov.MCPGovernor
	activeCount    int64   // atomic: 当前活跃的并发请求数
	interval       time.Duration
	currentPrice   int64   // 当前价格 (detector 内部跟踪)
	smoothActive   float64 // 指数平滑后的并发数（提供"记忆"，用于定价）
	regimeSignal   float64 // 对称轻度平滑并发数（用于 regime 检测）
}

func (d *proxyOverloadDetector) onRequestStart() {
	atomic.AddInt64(&d.activeCount, 1)
}

func (d *proxyOverloadDetector) onRequestEnd() {
	atomic.AddInt64(&d.activeCount, -1)
}

func (d *proxyOverloadDetector) run() {
	for range time.Tick(d.interval) {
		active := float64(atomic.LoadInt64(&d.activeCount))

		// 非对称指数平滑：快速响应过载，缓慢松弛恢复（用于定价）
		if active > d.smoothActive {
			d.smoothActive = 0.7*d.smoothActive + 0.3*active // 快速升
		} else {
			d.smoothActive = 0.99*d.smoothActive + 0.01*active // 缓慢降
		}

		// 对称轻度平滑（用于 regime 检测）：双向 alpha=0.2，保留负载变化信号
		d.regimeSignal = 0.8*d.regimeSignal + 0.2*active

		// 注入 regime 信号触发自适应档位检测
		d.gov.ApplyAdaptiveProfileSignal(d.regimeSignal)

		// 从当前活跃档位动态读取检测器参数
		priceStep, decayStep, maxConc := d.gov.GetDetectorParams()

		diff := int64(d.smoothActive) - maxConc

		if diff > 0 {
			d.currentPrice = diff * priceStep
		} else if d.currentPrice > 0 {
			d.currentPrice -= decayStep
			if d.currentPrice < 0 {
				d.currentPrice = 0
			}
		}

		d.gov.SetOwnPrice(d.currentPrice)
	}
}

func main() {
	mode := flag.String("mode", "dp", "网关模式: ng | srl | dp | dp-noregime")
	port := flag.Int("port", 9003, "网关监听端口")
	backendURL := flag.String("backend", "http://127.0.0.1:8080", "Python MCP 后端地址")
	host := flag.String("host", "127.0.0.1", "网关绑定地址")

	// SRL 参数
	srlQPS := flag.Float64("srl-qps", 50, "SRL: 令牌桶速率 (req/s)")
	srlBurst := flag.Int64("srl-burst", 100, "SRL: 令牌桶最大容量")
	srlMaxConc := flag.Int64("srl-max-conc", 20, "SRL: 最大并发连接数")

	// Rajomon 参数
	rajomonPriceStep := flag.Int64("rajomon-price-step", 100, "Rajomon: 过载涨价步长")

	// Rajomon+SB 参数
	rajomonSBPriceStep := flag.Int64("rajomon-sb-price-step", 100, "Rajomon+SB: 过载涨价步长")

	// DAGOR 参数
	dagorRTTThreshold := flag.Float64("dagor-rtt-threshold", 200.0, "DAGOR: RTT 过载检测阈值 (ms)")
	dagorPriceStep := flag.Int64("dagor-price-step", 50, "DAGOR: 过载时优先级门槛每轮增量")

	// SBAC 参数
	sbacMaxSessions := flag.Int64("sbac-max-sessions", 50, "SBAC: 最大并发会话数")

	// PP (Progress-Priority) 参数
	ppMaxSessions := flag.Int64("pp-max-sessions", 50, "PP: 最大并发会话数")

	// PlanGate (MCPDP) 参数
	plangateMaxSessions := flag.Int("plangate-max-sessions", 30,
		"PlanGate (Full): 并发会话上限（<=0 表示不限制）")
	plangatePriceStep := flag.Int64("plangate-price-step", 40,
		"PlanGate: 过载涨价步长")
	plangateSunkCostAlpha := flag.Float64("plangate-sunk-cost-alpha", 0.5,
		"PlanGate: ReAct 沉没成本系数 (0=禁用)")
	plangateSessionCapWait := flag.Int("plangate-session-cap-wait", 0,
		"PlanGate: Session Cap 排队等待超时 (秒), 0=立即拒绝")
	plangateDiscountFunc := flag.String("plangate-discount-func", "quadratic",
		"PlanGate: 沉没成本折扣函数 (quadratic|linear|exponential|logarithmic)")

	// PlanGate-Real 外部信号参数
	realRateLimitMax := flag.Float64("real-ratelimit-max", 200,
		"PlanGate-Real: API 配额上限 (GLM-4-Flash=200)")
	realLatencyThreshold := flag.Float64("real-latency-threshold", 5000,
		"PlanGate-Real: P95 延迟阈值 (ms), 达到此值时延迟压力=1.0")

	flag.Parse()

	// 先获取后端工具列表
	tools, err := fetchBackendTools(*backendURL)
	if err != nil {
		log.Fatalf("无法连接后端 %s: %v", *backendURL, err)
	}
	log.Printf("从后端获取到 %d 个工具", len(tools))

	var handler http.Handler

	switch *mode {
	case "ng":
		handler = setupNG(tools, *backendURL)
	case "srl":
		handler = setupSRL(tools, *backendURL, *srlQPS, *srlBurst, *srlMaxConc)
	case "dp":
		handler = setupDP(tools, *backendURL)
	case "dp-noregime":
		handler = setupDPNoRegime(tools, *backendURL)
	case "mcpdp":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-full", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			disableBudgetLock: false,
			sunkCostAlpha: *plangateSunkCostAlpha,
			discountFunc: *plangateDiscountFunc,
		})
	case "mcpdp-no-budgetlock":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-wo-budgetlock", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			disableBudgetLock: true,
			sunkCostAlpha: *plangateSunkCostAlpha,
			discountFunc: *plangateDiscountFunc,
		})
	case "mcpdp-no-sessioncap":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-wo-sessioncap", priceStep: *plangatePriceStep,
			maxConcurrentSessions: 0,
			disableBudgetLock: false,
			sunkCostAlpha: *plangateSunkCostAlpha,
			discountFunc: *plangateDiscountFunc,
		})
	case "mcpdp-real":
		handler = setupMCPDPReal(tools, *backendURL, mcpdpVariant{
			name: "plangate-real", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			sunkCostAlpha: *plangateSunkCostAlpha,
			sessionCapWait: time.Duration(*plangateSessionCapWait) * time.Second,
			discountFunc: *plangateDiscountFunc,
		}, *realRateLimitMax, *realLatencyThreshold)
	case "mcpdp-real-no-sessioncap":
		handler = setupMCPDPReal(tools, *backendURL, mcpdpVariant{
			name: "plangate-real-wo-sessioncap", priceStep: *plangatePriceStep,
			maxConcurrentSessions: 0,
			sunkCostAlpha: *plangateSunkCostAlpha,
			sessionCapWait: time.Duration(*plangateSessionCapWait) * time.Second,
			discountFunc: *plangateDiscountFunc,
		}, *realRateLimitMax, *realLatencyThreshold)
	case "rajomon":
		handler = setupRajomon(tools, *backendURL, *rajomonPriceStep)
	case "rajomon-session":
		handler = setupRajomonSession(tools, *backendURL, *rajomonSBPriceStep)
	case "dagor":
		handler = setupDagor(tools, *backendURL, *dagorRTTThreshold, *dagorPriceStep)
	case "sbac":
		handler = setupSBAC(tools, *backendURL, *sbacMaxSessions)
	case "pp":
		handler = setupPP(tools, *backendURL, *ppMaxSessions)
	default:
		log.Fatalf("未知模式: %s (可选: ng, srl, dp, dp-noregime, mcpdp, mcpdp-no-budgetlock, mcpdp-no-sessioncap, mcpdp-real, mcpdp-real-no-sessioncap, rajomon, rajomon-session, dagor, sbac, pp)", *mode)
	}

	addr := fmt.Sprintf("%s:%d", *host, *port)
	log.Printf("========================================")
	log.Printf("  MCP Gateway [%s] 启动", *mode)
	log.Printf("  监听: http://%s", addr)
	log.Printf("  后端: %s", *backendURL)
	log.Printf("========================================")

	server := &http.Server{
		Addr:         addr,
		Handler:      handler,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 120 * time.Second,
	}

	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("服务启动失败: %v", err)
	}
}

// fetchBackendTools 从 Python MCP 后端获取已注册的工具列表
func fetchBackendTools(backendURL string) ([]mcpgov.MCPTool, error) {
	reqBody := mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "init-list",
		Method:  "tools/list",
	}
	body, _ := json.Marshal(reqBody)

	resp, err := http.Post(backendURL, "application/json", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("连接后端失败: %w", err)
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(resp.Body)

	var rpcResp struct {
		Result struct {
			Tools []struct {
				Name        string      `json:"name"`
				Description string      `json:"description"`
				InputSchema interface{} `json:"inputSchema"`
			} `json:"tools"`
		} `json:"result"`
		Error *mcpgov.RPCError `json:"error"`
	}
	if err := json.Unmarshal(data, &rpcResp); err != nil {
		return nil, fmt.Errorf("解析后端响应失败: %w", err)
	}
	if rpcResp.Error != nil {
		return nil, fmt.Errorf("后端返回错误: %s", rpcResp.Error.Message)
	}

	tools := make([]mcpgov.MCPTool, len(rpcResp.Result.Tools))
	for i, t := range rpcResp.Result.Tools {
		tools[i] = mcpgov.MCPTool{
			Name:        t.Name,
			Description: t.Description,
			InputSchema: t.InputSchema,
		}
	}
	return tools, nil
}

// makeProxyHandler 创建一个将工具调用代理到后端的处理函数
// detector 可选：如果非 nil，则在请求前后追踪并发计数
func makeProxyHandler(backendURL string, toolName string, detector *proxyOverloadDetector) mcpgov.ToolCallHandler {
	client := &http.Client{Timeout: 120 * time.Second}

	return func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		if detector != nil {
			detector.onRequestStart()
			defer detector.onRequestEnd()
		}
		// 构建发往后端的 JSON-RPC 请求
		rpcReq := mcpgov.JSONRPCRequest{
			JSONRPC: "2.0",
			ID:      fmt.Sprintf("proxy-%s-%d", toolName, time.Now().UnixNano()),
			Method:  "tools/call",
		}
		paramsBytes, _ := json.Marshal(map[string]interface{}{
			"name":      params.Name,
			"arguments": params.Arguments,
		})
		rpcReq.Params = paramsBytes

		body, _ := json.Marshal(rpcReq)

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, backendURL, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("创建后端请求失败: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			return nil, fmt.Errorf("后端调用失败: %w", err)
		}
		defer resp.Body.Close()

		data, _ := io.ReadAll(resp.Body)

		var rpcResp struct {
			Result *struct {
				Content []mcpgov.ContentBlock `json:"content"`
				Meta    *struct {
					Tool      string  `json:"tool"`
					Category  string  `json:"category"`
					LatencyMs float64 `json:"latency_ms"`
				} `json:"_meta"`
			} `json:"result"`
			Error *mcpgov.RPCError `json:"error"`
		}
		if err := json.Unmarshal(data, &rpcResp); err != nil {
			return nil, fmt.Errorf("解析后端响应失败: %w", err)
		}
		if rpcResp.Error != nil {
			return nil, fmt.Errorf("后端工具执行错误: %s", rpcResp.Error.Message)
		}
		if rpcResp.Result == nil {
			return nil, fmt.Errorf("后端返回空结果")
		}

		return &mcpgov.MCPToolCallResult{
			Content: rpcResp.Result.Content,
		}, nil
	}
}

// === 网关初始化 ===

func setupNG(tools []mcpgov.MCPTool, backendURL string) http.Handler {
	gw := baseline.NewNGGateway("ng-gateway")
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [NG] 注册工具: %s", tool.Name)
	}
	return gw
}

func setupSRL(tools []mcpgov.MCPTool, backendURL string, qps float64, burst, maxConc int64) http.Handler {
	gw := baseline.NewSRLGateway("srl-gateway", baseline.SRLConfig{
		QPS:            qps,
		BurstSize:      burst,
		MaxConcurrency: maxConc,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [SRL] 注册工具: %s", tool.Name)
	}
	return gw
}

func setupDP(tools []mcpgov.MCPTool, backendURL string) http.Handler {
	// 构建 callMap: 每个工具无下游依赖
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":          int64(0),
		"rateLimiting":       false,
		"loadShedding":       true,
		"pinpointQueuing":    false, // 反向代理架构中 Go scheduler delay 无效
		"latencyThreshold":   500 * time.Microsecond,
		"priceStep":          int64(180),
		"priceStrategy":      "expdecay",
		"priceDecayStep":     int64(1),
		"priceSensitivity":   int64(10000),
		"maxToken":           int64(20),
		"smoothingWindow":    5,
		"integralThreshold":  0.5,
		"priceUpdateRate":    5 * time.Millisecond,
		"tokenUpdateRate":    100 * time.Millisecond,
		"tokenUpdateStep":    int64(1),
		"tokenRefillDist":    "fixed",
		"priceAggregation":   "maximal",
		"enableAdaptiveProfile": true,
		// Regime Detection 参数（标定为并发度信号，对称轻度平滑）
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
		"toolWeights": map[string]int64{
			"mock_heavy": 5, // 重量工具权重乘数 (800ms vs ~100ms ≈ 8:1)
		},
	}

	gov := mcpgov.NewMCPGovernor("dp-gateway", callMap, opts)
	server := mcpgov.NewMCPServer("dp-gateway", gov)

	// 创建代理级过载检测器（参数从 governor 当前档位动态读取）
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [DP] 注册工具: %s", tool.Name)
	}
	return server
}

func setupDPNoRegime(tools []mcpgov.MCPTool, backendURL string) http.Handler {
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	// 与 DP-Full 相同参数，但禁用自适应档位检测
	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false, // 反向代理架构中 Go scheduler delay 无效
		"latencyThreshold":      500 * time.Microsecond,
		"priceStep":             int64(180),
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": false, // 关键差异：禁用自适应档位
		// 与 DP-Full 相同的 Regime 参数（保证对比公平性）
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
		"toolWeights": map[string]int64{
			"mock_heavy": 5,
		},
	}

	gov := mcpgov.NewMCPGovernor("dp-noregime-gateway", callMap, opts)
	server := mcpgov.NewMCPServer("dp-noregime-gateway", gov)

	// 与 DP-Full 相同的过载检测器（但参数永远锁死在 Steady 档位）
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [DP-NoRegime] 注册工具: %s", tool.Name)
	}
	return server
}

// mcpdpVariant 配置 PlanGate 网关变体（用于严格单变量消融实验）
type mcpdpVariant struct {
	name                  string
	priceStep             int64
	maxConcurrentSessions int           // 有效并发会话上限 (0=不限制)
	disableBudgetLock     bool          // 是否禁用预算锁
	sunkCostAlpha         float64       // ReAct 沉没成本系数 (0=禁用)
	sessionCapWait        time.Duration // Session Cap 排队等待超时 (0=立即拒绝)
	discountFunc          string        // 折扣函数名称 (quadratic|linear|exponential|logarithmic)
}

func setupMCPDPVariant(tools []mcpgov.MCPTool, backendURL string, v mcpdpVariant) http.Handler {
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false,
		"latencyThreshold":      50 * time.Millisecond,
		"priceStep":             v.priceStep,
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": true,
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
		"toolWeights": map[string]int64{
			"mock_heavy": 5,
		},
	}

	gov := mcpgov.NewMCPGovernor(v.name, callMap, opts)

	var server *plangate.MCPDPServer
	if v.disableBudgetLock {
		server = plangate.NewMCPDPServerNoLock(v.name, gov, 60*time.Second, v.maxConcurrentSessions, v.sunkCostAlpha)
	} else {
		server = plangate.NewMCPDPServer(v.name, gov, 60*time.Second, v.maxConcurrentSessions, v.sunkCostAlpha)
	}

	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	// 设置折扣函数（消融实验支持）
	if v.discountFunc != "" {
		server.SetDiscountFunc(plangate.DiscountFuncName(v.discountFunc))
		log.Printf("  [%s] 折扣函数: %s", v.name, v.discountFunc)
	}

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [%s] 注册工具: %s", v.name, tool.Name)
	}
	return server
}

func init() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetOutput(os.Stdout)
}

func setupRajomon(tools []mcpgov.MCPTool, backendURL string, priceStep int64) http.Handler {
	gw := baseline.NewRajomonGateway("rajomon-gateway", baseline.RajomonConfig{
		PriceStep: priceStep,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [Rajomon] 注册工具: %s (priceStep=%d)", tool.Name, priceStep)
	}
	return gw
}

func setupRajomonSession(tools []mcpgov.MCPTool, backendURL string, priceStep int64) http.Handler {
	gw := baseline.NewRajomonSessionGateway("rajomon-session-gateway", baseline.RajomonSessionConfig{
		RajomonConfig: baseline.RajomonConfig{
			PriceStep: priceStep,
		},
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [Rajomon+SB] 注册工具: %s (priceStep=%d)", tool.Name, priceStep)
	}
	return gw
}

func setupDagor(tools []mcpgov.MCPTool, backendURL string, rttThresholdMs float64, priceStep int64) http.Handler {
	gw := baseline.NewDagorGateway("dagor-gateway", baseline.DagorConfig{
		RTTThresholdMs: rttThresholdMs,
		PriceStep:      priceStep,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [DAGOR] 注册工具: %s (rttThreshold=%.0fms, priceStep=%d)", tool.Name, rttThresholdMs, priceStep)
	}
	return gw
}

func setupSBAC(tools []mcpgov.MCPTool, backendURL string, maxSessions int64) http.Handler {
	gw := baseline.NewSBACGateway("sbac-gateway", baseline.SBACConfig{
		MaxSessions: maxSessions,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [SBAC] 注册工具: %s (maxSessions=%d)", tool.Name, maxSessions)
	}
	return gw
}

func setupPP(tools []mcpgov.MCPTool, backendURL string, maxSessions int64) http.Handler {
	gw := baseline.NewPPGateway("pp-gateway", baseline.PPConfig{
		MaxSessions: maxSessions,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [PP] 注册工具: %s (maxSessions=%d)", tool.Name, maxSessions)
	}
	return gw
}

// setupMCPDPReal 创建使用外部信号治理的 PlanGate 网关（真实 LLM 模式）
// 三维信号: 429 频率 + 延迟 P95 EMA + RateLimit-Remaining EMA
func setupMCPDPReal(tools []mcpgov.MCPTool, backendURL string, v mcpdpVariant,
	rateLimitMax float64, latencyThresholdMs float64) http.Handler {

	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false,
		"latencyThreshold":      50 * time.Millisecond,
		"priceStep":             v.priceStep,
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": true,
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
		"toolWeights": map[string]int64{
			"deepseek_llm":    5, // 重量级 LLM 工具
			"real_web_search": 2, // 中量级搜索工具
		},
	}

	gov := mcpgov.NewMCPGovernor(v.name, callMap, opts)

	// 创建外部信号跟踪器
	signalTracker := plangate.NewExternalSignalTracker(rateLimitMax, latencyThresholdMs)

	server := plangate.NewMCPDPServerWithExternalSignals(
		v.name, gov, 60*time.Second,
		v.maxConcurrentSessions, v.sunkCostAlpha, signalTracker,
		v.sessionCapWait, float64(v.priceStep),
	)

	// 代理级过载检测器（仍需要：驱动 ownPrice 用于沉没成本定价）
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	// 设置折扣函数（消融实验支持）
	if v.discountFunc != "" {
		server.SetDiscountFunc(plangate.DiscountFuncName(v.discountFunc))
		log.Printf("  [%s] 折扣函数: %s", v.name, v.discountFunc)
	}

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandlerWithSignals(
			backendURL, tool.Name, detector, signalTracker,
		))
		log.Printf("  [%s] 注册工具: %s (外部信号治理)", v.name, tool.Name)
	}
	return server
}

// makeProxyHandlerWithSignals 创建信号感知的代理处理函数
// 在标准代理基础上，解析后端 _meta 中的外部 API 信号并报告给 ExternalSignalTracker
func makeProxyHandlerWithSignals(
	backendURL string, toolName string,
	detector *proxyOverloadDetector,
	signalTracker *plangate.ExternalSignalTracker,
) mcpgov.ToolCallHandler {
	client := &http.Client{Timeout: 120 * time.Second}

	return func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		if detector != nil {
			detector.onRequestStart()
			defer detector.onRequestEnd()
		}

		start := time.Now()

		// 构建发往后端的 JSON-RPC 请求
		rpcReq := mcpgov.JSONRPCRequest{
			JSONRPC: "2.0",
			ID:      fmt.Sprintf("proxy-%s-%d", toolName, time.Now().UnixNano()),
			Method:  "tools/call",
		}
		paramsBytes, _ := json.Marshal(map[string]interface{}{
			"name":      params.Name,
			"arguments": params.Arguments,
		})
		rpcReq.Params = paramsBytes

		body, _ := json.Marshal(rpcReq)

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, backendURL, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("创建后端请求失败: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			// 网络错误 → 报告为高延迟信号
			elapsed := time.Since(start).Seconds() * 1000
			if signalTracker != nil {
				signalTracker.ReportResponse(false, elapsed, -1)
			}
			return nil, fmt.Errorf("后端调用失败: %w", err)
		}
		defer resp.Body.Close()

		data, _ := io.ReadAll(resp.Body)
		elapsed := time.Since(start).Seconds() * 1000

		var rpcResp struct {
			Result *struct {
				Content []mcpgov.ContentBlock `json:"content"`
				Meta    *struct {
					Tool                string  `json:"tool"`
					Category            string  `json:"category"`
					LatencyMs           float64 `json:"latency_ms"`
					Is429               bool    `json:"is_429"`
					HttpStatus          int     `json:"http_status"`
					ApiLatencyMs        float64 `json:"api_latency_ms"`
					RateLimitRemaining  float64 `json:"rate_limit_remaining"`
				} `json:"_meta"`
			} `json:"result"`
			Error *mcpgov.RPCError `json:"error"`
		}
		if err := json.Unmarshal(data, &rpcResp); err != nil {
			if signalTracker != nil {
				signalTracker.ReportResponse(false, elapsed, -1)
			}
			return nil, fmt.Errorf("解析后端响应失败: %w", err)
		}

		// 报告外部 API 信号给跟踪器
		if signalTracker != nil && rpcResp.Result != nil && rpcResp.Result.Meta != nil {
			meta := rpcResp.Result.Meta
			apiLatency := meta.ApiLatencyMs
			if apiLatency <= 0 {
				apiLatency = elapsed // 回退到端到端延迟
			}
			signalTracker.ReportResponse(meta.Is429, apiLatency, meta.RateLimitRemaining)
		} else if signalTracker != nil {
			// 无 _meta → 使用端到端延迟
			signalTracker.ReportResponse(false, elapsed, -1)
		}

		if rpcResp.Error != nil {
			// 后端返回错误 → 检查是否为过载 (429/503)
			is429 := resp.StatusCode == 429
			if signalTracker != nil && is429 {
				signalTracker.ReportResponse(true, elapsed, -1)
			}
			return nil, fmt.Errorf("后端工具执行错误: %s", rpcResp.Error.Message)
		}
		if rpcResp.Result == nil {
			return nil, fmt.Errorf("后端返回空结果")
		}

		return &mcpgov.MCPToolCallResult{
			Content: rpcResp.Result.Content,
		}, nil
	}
}
