// mcp_init.go
// MCPGovernor 初始化配置
// 提供 NewMCPGovernor 构造函数，用于创建并配置 MCP 服务治理实例
package mcpgov

import (
	"math/rand"
	"sync"
	"sync/atomic"
	"time"
)

// NewMCPGovernor 初始化 MCP 服务治理引擎实例
//
// 参数:
//   - nodeName: 当前节点名称（如 "tool-server-1", "client"）
//   - callmap: 定义工具间的调用关系 map[工具名][]下游依赖工具名
//   - options: 各种配置参数
//
// 使用示例:
//
//	callMap := map[string][]string{
//	    "get_weather": {},                        // get_weather 没有下游依赖
//	    "plan_trip":   {"get_weather", "search"}, // plan_trip 依赖两个下游工具
//	}
//	gov := NewMCPGovernor("server-1", callMap, map[string]interface{}{
//	    "loadShedding":     true,
//	    "pinpointQueuing":  true,
//	    "latencyThreshold": 500 * time.Microsecond,
//	    "priceStep":        int64(180),
//	})
func NewMCPGovernor(nodeName string, callmap map[string][]string, options map[string]interface{}) *MCPGovernor {
	// 初始化 MCPGovernor 结构体，设置默认值
	gov := &MCPGovernor{
		initprice:            0,
		nodeName:             nodeName,
		callMap:              callmap,
		priceTableMap:        sync.Map{},
		rateLimiting:         false,
		rateLimitWaiting:     false,
		loadShedding:         false,
		pinpointThroughput:   false,
		pinpointLatency:      false,
		pinpointQueuing:      false,
		rateLimiter:          make(chan int64, 1),
		fakeInvoker:          false,
		skipPrice:            false,
		priceFreq:            5,
		tokensLeft:           10,
		tokenUpdateRate:      time.Millisecond * 10,
		lastUpdateTime:       time.Now(),
		lastRateLimitedTime:  time.Now().Add(-time.Second),
		tokenUpdateStep:      1,
		tokenRefillDist:      "fixed",
		tokenStrategy:        "all",
		priceStrategy:        "step",
		throughputCounter:    0,
		priceUpdateRate:      time.Millisecond * 10,
		observedDelay:        time.Duration(0),
		clientTimeOut:        time.Duration(0),
		clientBackoff:        time.Duration(0),
		randomRateLimit:      -1,
		throughputThreshold:  0,
		latencyThreshold:     time.Duration(0),
		priceStep:            1,
		priceAggregation:     "maximal",
		guidePrice:           -1,
		consecutiveIncreases: 0,
		decayRate:            0.8,
		maxToken:             10,
		// v1.1 新增参数（默认值保持向后兼容）
		priceDecayStep:    1,     // 降价步长：默认 1（与旧版行为一致）
		priceSensitivity:  10000, // 灵敏度系数：默认 10000（与旧版行为一致）
		smoothingWindow:   1,     // 平滑窗口：默认 1（不平滑）
		integralThreshold: 0,     // 积分阈值：默认 0（不启用积分项）
		integralDecay:     0.8,   // 积分衰减：默认 0.8
	}

	// --- 解析 options 中的配置选项 ---

	if debugOpt, ok := options["debug"].(bool); ok {
		debug = debugOpt
	}

	if trackingPrice, ok := options["recordPrice"].(bool); ok {
		trackPrice = trackingPrice
	}

	// 设置初始价格
	if initprice, ok := options["initprice"].(int64); ok {
		gov.initprice = initprice
		logger("initprice of %s set to %d\n", nodeName, gov.initprice)
	}

	// 开启限流 (通常在客户端侧开启)
	if rateLimiting, ok := options["rateLimiting"].(bool); ok {
		gov.rateLimiting = rateLimiting
		logger("rateLimiting        of %s set to %v\n", nodeName, rateLimiting)
	}

	// 开启负载削减 (通常在服务端侧开启)
	if loadShedding, ok := options["loadShedding"].(bool); ok {
		gov.loadShedding = loadShedding
		logger("loadShedding        of %s set to %v\n", nodeName, loadShedding)
	}

	// 开启基于吞吐量的过载检测
	if pinpointThroughput, ok := options["pinpointThroughput"].(bool); ok {
		gov.pinpointThroughput = pinpointThroughput
		logger("pinpointThroughput  of %s set to %v\n", nodeName, pinpointThroughput)
	}

	// 开启基于延迟的过载检测
	if pinpointLatency, ok := options["pinpointLatency"].(bool); ok {
		gov.pinpointLatency = pinpointLatency
		logger("pinpointLatency     of %s set to %v\n", nodeName, pinpointLatency)
	}

	// 开启基于排队时间的过载检测 (关键特性)
	// Go runtime 的 goroutine 调度延迟可以精准反映 CPU 饱和度
	if pinpointQueuing, ok := options["pinpointQueuing"].(bool); ok {
		gov.pinpointQueuing = pinpointQueuing
		logger("pinpointQueuing     of %s set to %v\n", nodeName, pinpointQueuing)
	}

	// 开启伪调用模式 (压测用)
	if fakeInvoker, ok := options["fakeInvoker"].(bool); ok {
		gov.fakeInvoker = fakeInvoker
		logger("fakeInvoker     of %s set to %v\n", nodeName, fakeInvoker)
	}

	// 开启懒响应模式
	if skipPrice, ok := options["lazyResponse"].(bool); ok {
		gov.skipPrice = skipPrice
		logger("skipPrice       of %s set to %v\n", nodeName, skipPrice)
	}

	// 价格反馈频率
	if priceFreq, ok := options["priceFreq"].(int64); ok {
		gov.priceFreq = priceFreq
		logger("priceFreq       of %s set to %v\n", nodeName, priceFreq)
	}

	// 初始令牌数
	if tokensLeft, ok := options["tokensLeft"].(int64); ok {
		gov.tokensLeft = tokensLeft
		logger("tokensLeft      of %s set to %v\n", nodeName, tokensLeft)
	}

	// 令牌更新速率
	if tokenUpdateRate, ok := options["tokenUpdateRate"].(time.Duration); ok {
		gov.tokenUpdateRate = tokenUpdateRate
		logger("tokenUpdateRate     of %s set to %v\n", nodeName, tokenUpdateRate)
	}

	// 令牌更新步长
	if tokenUpdateStep, ok := options["tokenUpdateStep"].(int64); ok {
		gov.tokenUpdateStep = tokenUpdateStep
		logger("tokenUpdateStep     of %s set to %v\n", nodeName, tokenUpdateStep)
	}

	// 令牌填充分布模式
	if tokenRefillDist, ok := options["tokenRefillDist"].(string); ok {
		if tokenRefillDist != "fixed" && tokenRefillDist != "uniform" && tokenRefillDist != "poisson" {
			tokenRefillDist = "fixed"
		}
		gov.tokenRefillDist = tokenRefillDist
		logger("tokenRefillDist     of %s set to %v\n", nodeName, tokenRefillDist)
	}

	// 令牌策略
	if tokenStrategy, ok := options["tokenStrategy"].(string); ok {
		if tokenStrategy != "all" && tokenStrategy != "uniform" {
			tokenStrategy = "all"
		}
		gov.tokenStrategy = tokenStrategy
		logger("tokenStrategy       of %s set to %v\n", nodeName, tokenStrategy)
	}

	// 价格调整策略
	if priceStrategy, ok := options["priceStrategy"].(string); ok {
		gov.priceStrategy = priceStrategy
		logger("priceStrategy       of %s set to %v\n", nodeName, priceStrategy)
	}

	// 价格更新速率
	if priceUpdateRate, ok := options["priceUpdateRate"].(time.Duration); ok {
		gov.priceUpdateRate = priceUpdateRate
		logger("priceUpdateRate     of %s set to %v\n", nodeName, priceUpdateRate)
	}

	// 客户端超时时间
	if clientTimeOut, ok := options["clientTimeOut"].(time.Duration); ok {
		gov.clientTimeOut = clientTimeOut
		logger("clientTimeout       of %s set to %v\n", nodeName, clientTimeOut)
	}

	// 客户端退避时间
	if clientBackoff, ok := options["clientBackoff"].(time.Duration); ok {
		gov.clientBackoff = clientBackoff
		logger("clientBackoff       of %s set to %v\n", nodeName, clientBackoff)
	}

	// 随机限流阈值 (调试用)
	if randomRateLimit, ok := options["randomRateLimit"].(int64); ok {
		gov.randomRateLimit = randomRateLimit
		logger("randomRateLimit     of %s set to %v\n", nodeName, randomRateLimit)
	}

	// 限流等待模式：有超时时间则启用阻塞等待
	if gov.clientTimeOut > 0 {
		gov.rateLimitWaiting = true
	} else {
		gov.rateLimitWaiting = false
	}
	logger("rateLimitWaiting    of %s set to %v\n", nodeName, gov.rateLimitWaiting)

	// 吞吐量阈值
	if throughputThreshold, ok := options["throughputThreshold"].(int64); ok {
		gov.throughputThreshold = throughputThreshold
		logger("throughputThreshold of %s set to %v\n", nodeName, throughputThreshold)
	}

	// 延迟阈值
	if latencyThreshold, ok := options["latencyThreshold"].(time.Duration); ok {
		gov.latencyThreshold = latencyThreshold
		logger("latencyThreshold    of %s set to %v\n", nodeName, latencyThreshold)
	}

	// 价格调整步长
	if priceStep, ok := options["priceStep"].(int64); ok {
		gov.priceStep = priceStep
		logger("priceStep       of %s set to %v\n", nodeName, priceStep)
	}

	// 价格聚合方式
	if priceAggregation, ok := options["priceAggregation"].(string); ok {
		if priceAggregation != "maximal" && priceAggregation != "additive" && priceAggregation != "mean" {
			priceAggregation = "maximal"
		}
		gov.priceAggregation = priceAggregation
		logger("priceAggregation    of %s set to %v\n", nodeName, priceAggregation)
	}

	// 指导价格
	if guidePrice, ok := options["guidePrice"].(int64); ok {
		gov.guidePrice = guidePrice
		logger("guidePrice      of %s set to %v\n", nodeName, guidePrice)
	}

	// === v1.1 新增参数 ===

	// 降价步长（替代硬编码的 1，使降价速度与涨价成比例）
	if priceDecayStep, ok := options["priceDecayStep"].(int64); ok {
		if priceDecayStep > 0 {
			gov.priceDecayStep = priceDecayStep
		}
		logger("priceDecayStep      of %s set to %v\n", nodeName, priceDecayStep)
	}

	// 价格灵敏度系数（替代硬编码的 10000，独立控制 Kp 增益）
	if priceSensitivity, ok := options["priceSensitivity"].(int64); ok {
		if priceSensitivity > 0 {
			gov.priceSensitivity = priceSensitivity
		}
		logger("priceSensitivity    of %s set to %v\n", nodeName, priceSensitivity)
	}

	// 衰减率（expdecay 策略专用，控制连续涨价时的刹车力度）
	if decayRate, ok := options["decayRate"].(float64); ok {
		if decayRate > 0 && decayRate <= 1.0 {
			gov.decayRate = decayRate
		}
		logger("decayRate           of %s set to %v\n", nodeName, decayRate)
	}

	// 最大令牌容量（放大预算表达空间）
	if maxToken, ok := options["maxToken"].(int64); ok {
		if maxToken > 0 {
			gov.maxToken = maxToken
		}
		logger("maxToken            of %s set to %v\n", nodeName, maxToken)
	}

	// 移动平均平滑窗口大小（1=不平滑，>1 启用移动平均消除短期抖动）
	if smoothingWindow, ok := options["smoothingWindow"].(int); ok {
		if smoothingWindow >= 1 {
			gov.smoothingWindow = smoothingWindow
		}
		logger("smoothingWindow     of %s set to %v\n", nodeName, smoothingWindow)
	}

	// 积分阈值（>0 时启用积分项，解决稳态误差）
	if integralThreshold, ok := options["integralThreshold"].(float64); ok {
		if integralThreshold >= 0 {
			gov.integralThreshold = integralThreshold
		}
		logger("integralThreshold   of %s set to %v\n", nodeName, integralThreshold)
	}

	// 积分衰减系数（非过载时积分快速回落）
	if integralDecay, ok := options["integralDecay"].(float64); ok {
		if integralDecay > 0 && integralDecay <= 1.0 {
			gov.integralDecay = integralDecay
		}
		logger("integralDecay       of %s set to %v\n", nodeName, integralDecay)
	}

	// 初始化移动平均环形缓冲区
	if gov.smoothingWindow > 1 {
		gov.latencyHistory = make([]float64, gov.smoothingWindow)
	}

	// --- 启动后台任务 ---

	if gov.nodeName == "client" {
		// 客户端：启动令牌补充协程 (Token Refill)
		go gov.tokenRefill(gov.tokenRefillDist, gov.tokenUpdateStep, gov.tokenUpdateRate)
	} else {
		// 服务端：启动过载检测协程
		if gov.pinpointQueuing && gov.pinpointThroughput {
			go gov.checkBoth()
		} else if gov.pinpointThroughput {
			go gov.throughputCheck()
		} else if gov.pinpointLatency {
			go gov.latencyCheck()
		} else if gov.pinpointQueuing {
			go gov.queuingCheck()
		}
	}

	// --- 初始化价格表 ---

	gov.priceTableMap.Store("ownprice", gov.initprice)

	for method, nodes := range gov.callMap {
		gov.priceTableMap.Store(method, gov.initprice)
		logger("[初始化价格表]: 工具 %s 价格设为 %d\n", method, gov.initprice)

		for _, node := range nodes {
			gov.priceTableMap.Store(method+"-"+node, gov.initprice)
			logger("[初始化价格表]: 工具 %s-%s 价格设为 %d\n", method, node, gov.initprice)
		}
	}

	return gov
}

// GetTokensLeft 原子操作读取剩余令牌数
func (gov *MCPGovernor) GetTokensLeft() int64 {
	if !atomicTokens {
		return gov.tokensLeft
	}
	return atomic.LoadInt64(&gov.tokensLeft)
}

// DeductTokens 原子操作扣除令牌，返回是否成功
func (gov *MCPGovernor) DeductTokens(n int64) bool {
	if !atomicTokens {
		if gov.tokensLeft-n < 0 {
			return false
		}
		gov.tokensLeft -= n
		return true
	}
	// CAS 循环保证并发安全
	for {
		currentTokens := gov.GetTokensLeft()
		newTokens := currentTokens - n
		if newTokens < 0 {
			return false
		}
		if atomic.CompareAndSwapInt64(&gov.tokensLeft, currentTokens, newTokens) {
			return true
		}
	}
}

// AddTokens 原子操作增加令牌
func (gov *MCPGovernor) AddTokens(n int64) {
	if !atomicTokens {
		gov.tokensLeft += n
		return
	}
	atomic.AddInt64(&gov.tokensLeft, n)
}

// tokenRefill 后台协程：定期向令牌桶补充令牌
func (gov *MCPGovernor) tokenRefill(tokenRefillDist string, tokenUpdateStep int64, tokenUpdateRate time.Duration) {
	if tokenRefillDist == "poisson" {
		// 泊松分布模式：模拟更真实的随机流量
		ticker := time.NewTicker(gov.initialTokenUpdateInterval())
		defer ticker.Stop()

		lambda := float64(1) / float64(tokenUpdateRate.Milliseconds())

		for range ticker.C {
			gov.AddTokens(tokenUpdateStep)
			if gov.rateLimitWaiting {
				gov.unblockRateLimiter()
			}
			ticker.Reset(gov.nextTokenUpdateInterval(lambda))
		}
	} else {
		// 固定或均匀分布模式
		for range time.Tick(tokenUpdateRate) {
			if tokenRefillDist == "fixed" {
				gov.AddTokens(tokenUpdateStep)
			} else if tokenRefillDist == "uniform" {
				gov.AddTokens(rand.Int63n(tokenUpdateStep * 2))
			}
			if gov.rateLimitWaiting {
				gov.unblockRateLimiter()
			}
		}
	}
}

// initialTokenUpdateInterval 返回令牌补充的初始间隔
func (gov *MCPGovernor) initialTokenUpdateInterval() time.Duration {
	return gov.tokenUpdateRate
}

// nextTokenUpdateInterval 基于指数分布计算下一次令牌补充间隔 (泊松过程)
func (gov *MCPGovernor) nextTokenUpdateInterval(lambda float64) time.Duration {
	nextTickDuration := time.Duration(rand.ExpFloat64()/lambda) * time.Millisecond
	if nextTickDuration <= 0 {
		nextTickDuration = time.Millisecond
	}
	return nextTickDuration
}
