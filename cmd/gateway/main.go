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

	// DAGOR 参数
	dagorRTTThreshold := flag.Float64("dagor-rtt-threshold", 200.0, "DAGOR: RTT 过载检测阈值 (ms)")
	dagorPriceStep := flag.Int64("dagor-price-step", 50, "DAGOR: 过载时优先级门槛每轮增量")

	// SBAC 参数
	sbacMaxSessions := flag.Int64("sbac-max-sessions", 50, "SBAC: 最大并发会话数")

	// PlanGate (MCPDP) 参数
	plangateMaxSessions := flag.Int("plangate-max-sessions", 30,
		"PlanGate (Full): 并发会话上限（<=0 表示不限制）")
	plangatePriceStep := flag.Int64("plangate-price-step", 40,
		"PlanGate: 过载涨价步长")

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
		handler = setupMCPDP(tools, *backendURL, *plangatePriceStep, *plangateMaxSessions)
	case "mcpdp-nolock":
		handler = setupMCPDPNoLock(tools, *backendURL, *plangatePriceStep)
	case "rajomon":
		handler = setupRajomon(tools, *backendURL, *rajomonPriceStep)
	case "dagor":
		handler = setupDagor(tools, *backendURL, *dagorRTTThreshold, *dagorPriceStep)
	case "sbac":
		handler = setupSBAC(tools, *backendURL, *sbacMaxSessions)
	default:
		log.Fatalf("未知模式: %s (可选: ng, srl, dp, dp-noregime, mcpdp, mcpdp-nolock, rajomon, dagor, sbac)", *mode)
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

func setupMCPDP(tools []mcpgov.MCPTool, backendURL string, priceStep int64, maxConcurrentSessions int) http.Handler {
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	// DetectorMaxConc 设置为超过 sessionCap 的值，确保在会话并发控制下价格保持低位
	// 这样 Pre-flight Atomic Admission 的预算检查不会误拒合理会话
	detectorMaxConc := int64(maxConcurrentSessions + 10)
	if detectorMaxConc < 20 {
		detectorMaxConc = 20
	}
	profileOverride := map[string]interface{}{
		"DetectorMaxConc":   detectorMaxConc,
		"DetectorPriceStep": int64(2),  // 低涨价步长，避免价格过激
		"DetectorDecayStep": int64(10), // 快速衰减，价格迅速回归 0
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false,
		"latencyThreshold":      50 * time.Millisecond, // 降敏：50ms 而非 500µs
		"priceStep":             priceStep,
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
		"burstyProfile":         profileOverride,
		"periodicProfile":       profileOverride,
		"steadyProfile":         profileOverride,
		"toolWeights": map[string]int64{
			"mock_heavy": 5,
		},
	}

	gov := mcpgov.NewMCPGovernor("mcpdp-gateway", callMap, opts)
	server := mcpgov.NewMCPDPServer("mcpdp-gateway", gov, 60*time.Second, maxConcurrentSessions)

	// 创建代理级过载检测器
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [MCPDP] 注册工具: %s", tool.Name)
	}
	return server
}

func init() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetOutput(os.Stdout)
}

// setupMCPDPNoLock 消融变体：相同 priceStep，但无预算锁、无并发会话上限
// 用于与 plangate_full 对比，展示预算锁+并发控制的价值
func setupMCPDPNoLock(tools []mcpgov.MCPTool, backendURL string, priceStep int64) http.Handler {
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false,
		"latencyThreshold":      50 * time.Millisecond, // 与 Full 保持一致
		"priceStep":             priceStep,
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

	gov := mcpgov.NewMCPGovernor("mcpdp-nolock-gateway", callMap, opts)
	// maxConcurrentSessions=0: 消融实验展示无并发控制时的级联失败
	server := mcpgov.NewMCPDPServerNoLock("mcpdp-nolock-gateway", gov, 60*time.Second, 0)

	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [MCPDP-NoLock] 注册工具: %s", tool.Name)
	}
	return server
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
