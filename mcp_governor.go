// mcp_governor.go
// MCP 服务治理引擎核心实现
// 参考 Rajomon 的动态定价思想，将过载控制应用于 MCP 工具调用场景
// 核心机制：每个工具调用 (tools/call) 需携带令牌(tokens)，服务端根据负载动态调整价格
package mcpgov

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"strconv"
	"sync"
	"time"
)

// 哨兵错误 (Sentinel Errors)，用于在逻辑流中进行特定的错误判断
var (
	// ErrInsufficientTokens 当令牌不足以支付当前价格时返回
	ErrInsufficientTokens = errors.New("令牌不足，触发负载削减 (Load Shedding)")
	// ErrRateLimited 当客户端令牌不足时返回
	ErrRateLimited = errors.New("令牌不足无法发送，触发限流 (Rate Limit)")

	// 全局配置开关
	debug        = false // 是否开启调试日志
	atomicTokens = false // 是否使用原子操作管理令牌
	trackPrice   = false // 是否记录价格追踪日志
)

// MCPGovernor 实现了基于 MCP 协议的服务治理引擎
//
// 核心思想 (源自 Rajomon)：
//   - 每个 MCP 工具调用 (tools/call) 都有一个"价格" (price)
//   - 客户端在请求的 _meta.tokens 中携带"令牌" (预算)
//   - 服务端根据当前负载 (排队延迟/吞吐量) 动态调整价格
//   - 当 tokens < price 时，触发负载削减，主动拒绝请求以保护服务
//
// 在 MCP 场景中的应用：
//   - AI Agent 调用多个工具时，通过令牌预算防止单个工具调用耗尽资源
//   - 工具服务通过动态定价实现自适应过载保护
//   - 多级工具链路通过价格传播实现端到端的拥塞控制
type MCPGovernor struct {
	initprice int64               // 初始价格
	nodeName  string              // 当前节点名称 (如 "tool-server-1")
	callMap   map[string][]string // 工具调用关系图：工具名 → 下游依赖的工具列表

	// sync.Map 是 Go 标准库提供的并发安全 Map
	// 存储内容包括：自身价格 ("ownprice"), 各工具的下游价格, 以及特定节点价格
	priceTableMap sync.Map

	rateLimiting       bool // 开关：是否开启客户端限流
	rateLimitWaiting   bool // 开关：限流时是否等待令牌（阻塞模式）
	loadShedding       bool // 开关：是否开启服务端负载削减
	pinpointThroughput bool // 开关：是否基于吞吐量检测过载
	pinpointLatency    bool // 开关：是否基于延迟检测过载
	pinpointQueuing    bool // 开关：是否基于排队时间检测过载 (关键特性)

	// Channel (通道) 用作信号量。当令牌不足时，请求在此等待令牌补充。
	rateLimiter chan int64

	fakeInvoker          bool          // 开关：伪调用模式 (压测开销用)
	skipPrice            bool          // 开关：是否跳过价格更新 (懒响应)
	priceFreq            int64         // 价格反馈频率 (每 n 个请求反馈一次)
	tokensLeft           int64         // 剩余令牌数 (钱包余额)
	tokenUpdateRate      time.Duration // 令牌更新速率
	lastUpdateTime       time.Time     // 上次更新时间
	lastRateLimitedTime  time.Time     // 上次被限流时间
	tokenUpdateStep      int64         // 每次更新增加的令牌数
	tokenRefillDist      string        // 令牌填充分布 (fixed, uniform, poisson)
	tokenStrategy        string        // 令牌分配策略 (all, uniform)
	priceStrategy        string        // 价格调整策略 (step, expdecay)
	throughputCounter    int64         // 吞吐量计数器
	priceUpdateRate      time.Duration // 价格更新速率
	observedDelay        time.Duration // 观测到的业务延迟
	clientTimeOut        time.Duration // 客户端等待令牌的超时时间
	clientBackoff        time.Duration // 客户端退避时间
	randomRateLimit      int64         // 随机限流阈值 (用于调试)
	throughputThreshold  int64         // 吞吐量阈值
	latencyThreshold     time.Duration // 延迟阈值
	priceStep            int64         // 价格调整步长
	priceAggregation     string        // 价格聚合策略 (maximal, additive, mean)
	guidePrice           int64         // 指导价格 (目标价格)
	consecutiveIncreases int64         // 价格连续上涨次数 (用于指数衰减策略)
	decayRate            float64       // 衰减率
	maxToken             int64         // 最大令牌容量

	// === 优化参数 (v1.1) ===
	priceDecayStep   int64 // 降价步长，替代硬编码的 1（使降价速度与涨价成比例）
	priceSensitivity int64 // 价格灵敏度系数，替代硬编码的 10000，独立控制 Kp 增益
	// 移动平均平滑窗口（消除短期延迟抖动对定价的干扰）
	smoothingWindow int       // 窗口大小（1=不平滑，>1 启用移动平均）
	latencyHistory  []float64 // 延迟历史记录（环形缓冲区）
	historyIndex    int       // 环形缓冲区写入位置
	historyCount    int       // 已写入的样本数
	// 简化积分项（解决轻微持续过载下的稳态误差）
	latencyIntegral   float64 // 延迟差异积分累加值
	integralThreshold float64 // 积分阈值，超过后额外提供涨价 boost
	integralDecay     float64 // 非过载时积分衰减系数

	// === 工具权重 (v1.3) ===
	toolWeights map[string]int64 // 工具权重乘数：重量工具 weight 高，实际价格 = ownprice × weight

	// === 自适应参数档位 (Load Regime Detector + Parameter Profile) ===
	enableAdaptiveProfile bool               // 是否启用负载状态检测与热切换
	regimeWindow          int                // 用于计算方差的窗口大小
	regimeHistory         []float64          // 排队延迟历史 (ms)
	regimeIndex           int                // 环形缓冲区写入位置
	regimeCount           int                // 已写入样本数
	regimeVarianceLow     float64            // 低方差阈值 (ms^2)
	regimeVarianceHigh    float64            // 高方差阈值 (ms^2)
	regimeSpikeThreshold  float64            // 突刺阈值 (ms)
	lastGapLatency        float64            // 上一周期的 gap latency
	activeRegime          string             // 当前生效的负载状态
	profileSwitchCooldown time.Duration      // 档位切换冷却时间
	lastProfileSwitch     time.Time          // 上次切换时间
	parameterProfiles     map[string]Profile // 各负载状态的参数档位
}

// Profile 定义一组可热切换的治理参数。
type Profile struct {
	PriceStep         int64
	PriceDecayStep    int64
	PriceSensitivity  int64
	LatencyThreshold  time.Duration
	DecayRate         float64
	PriceUpdateRate   time.Duration
	MaxToken          int64
	IntegralThreshold float64
	IntegralDecay     float64
	// proxyOverloadDetector 专用参数（代理架构下的自适应调节）
	DetectorPriceStep int64 // 检测器每单位过载的涨价步长
	DetectorDecayStep int64 // 检测器空闲时的降价步长
	DetectorMaxConc   int64 // 检测器的并发容量阈值
}

// ToolCallHandler 工具调用处理函数签名
// 接收上下文和工具调用参数，返回工具调用结果
// 在 MCP 中，这就是实际执行工具逻辑的函数
type ToolCallHandler func(ctx context.Context, params MCPToolCallParams) (*MCPToolCallResult, error)

// unblockRateLimiter 非阻塞地向限流器通道发送信号
// 当令牌补充后调用，唤醒一个正在等待的请求
func (gov *MCPGovernor) unblockRateLimiter() {
	// select + default 实现非阻塞发送：
	// 如果有 goroutine 在 <-rateLimiter 上等待，它会收到信号；
	// 如果没有人等待（通道已满），直接走 default 返回，不会卡死。
	select {
	case gov.rateLimiter <- 1:
		return
	default:
		return
	}
}

// RateLimiting 客户端限流检查
// 检查当前令牌是否足以支付目标工具的价格。如果 tokens < price，则阻止调用。
func (gov *MCPGovernor) RateLimiting(ctx context.Context, tokens int64, toolName string) error {
	servicePrice, _ := gov.RetrieveDSPrice(ctx, toolName)
	extraToken := tokens - servicePrice
	logger("[限流检查]: 检查请求。持有令牌 %d, 工具 %s 的价格为 %d\n", tokens, toolName, servicePrice)

	if extraToken < 0 {
		logger("[限流]: 令牌不足，请求被阻塞。")
		return ErrRateLimited
	}
	return nil
}

// LoadShedding 服务端核心准入控制逻辑
//
// >>> Algorithm 1 中 MCPGovernor 标准准入路径的核心实现
// >>> Eq.(1): P_eff(t) = P_own × w_t, 比较 tokens ≥ P_eff → ADMIT
//
// 根据价格表判断请求中的令牌是否足够，扣除后返回剩余令牌和当前价格。
//
// 返回值:
//  1. tokensLeft: 扣除自身价格后剩余的令牌数 (用于传递给下游工具)
//  2. priceString: 当前总价格字符串 (用于通过 _meta 返回给客户端)
//  3. error: 如果令牌不足则返回 ErrInsufficientTokens
func (gov *MCPGovernor) LoadShedding(ctx context.Context, tokens int64, toolName string) (int64, string, error) {
	// 如果未开启负载削减，直接放行
	if !gov.loadShedding {
		totalPrice, _ := gov.RetrieveTotalPrice(ctx, toolName)
		return tokens, totalPrice, nil
	}

	ownPriceVal, ok := gov.priceTableMap.Load("ownprice")
	if !ok {
		return 0, "", fmt.Errorf("[负载削减]: 未找到 %s 的自身价格", toolName)
	}
	ownPrice := ownPriceVal.(int64)

	// 应用工具权重乘数：重量工具的实际价格 = ownprice × weight
	if w, exists := gov.toolWeights[toolName]; exists && w > 1 {
		ownPrice *= w
	}

	downstreamPrice, err := gov.RetrieveDSPrice(ctx, toolName)
	if err != nil {
		logger("[负载削减]: 获取 %s 的下游价格失败: %v\n", toolName, err)
		return 0, "", err
	}

	// === 策略 A: 最大值聚合 (Maximal) ===
	// 适用于并行工具调用场景，总价格取决于链路中最慢的工具 (短板效应)
	if gov.priceAggregation == "maximal" {
		logger("[收到请求]: 工具 %s, 自身价格 %d, 下游价格 %d\n", toolName, ownPrice, downstreamPrice)

		if ownPrice < downstreamPrice {
			ownPrice = downstreamPrice
		}

		if tokens >= ownPrice {
			logger("[准入控制]: 请求通过。令牌 %d, 价格 %d\n", tokens, ownPrice)
			return tokens - ownPrice, strconv.FormatInt(ownPrice, 10), nil
		} else {
			logger("[准入控制]: 令牌不足被拒绝。令牌 %d, 价格 %d\n", tokens, ownPrice)
			return 0, strconv.FormatInt(ownPrice, 10), ErrInsufficientTokens
		}

		// === 策略 B: 平均值聚合 (Mean) ===
	} else if gov.priceAggregation == "mean" {
		totalPrice := (ownPrice + downstreamPrice) / 2
		logger("[收到请求]: 工具 %s, 均价 %d\n", toolName, totalPrice)
		if tokens >= totalPrice {
			return tokens - totalPrice, strconv.FormatInt(totalPrice, 10), nil
		} else {
			return 0, strconv.FormatInt(totalPrice, 10), ErrInsufficientTokens
		}

		// === 策略 C: 累加聚合 (Additive) ===
		// 适用于串行工具链场景，总价格 = 自身价格 + 所有下游价格
	} else if gov.priceAggregation == "additive" {
		totalPrice := ownPrice + downstreamPrice
		extraToken := tokens - totalPrice

		logger("[收到请求]: 工具 %s, 总价 %d (自身 %d + 下游 %d)\n", toolName, totalPrice, ownPrice, downstreamPrice)

		if extraToken < 0 {
			return 0, strconv.FormatInt(totalPrice, 10), ErrInsufficientTokens
		}

		if gov.pinpointThroughput {
			gov.Increment()
		}

		tokenLeft := tokens - ownPrice
		return tokenLeft, strconv.FormatInt(totalPrice, 10), nil
	}

	return 0, "", fmt.Errorf("不支持的价格聚合方法: %s", gov.priceAggregation)
}

// HandleToolCall 是 MCP 服务端的工具调用治理中间件
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ Algorithm 1 中的 MCPGovernor 标准准入路径                      │
// │                                                               │
// │ 流程: LoadShedding → Eq.(1) P_eff 比较 → 准入/拒绝            │
// │ ReAct 模式下由 handleReActMode() 委托此函数                  │
// │ P&S 模式下绕过 LoadShedding，使用 executeStepDirect()         │
// └─────────────────────────────────────────────────────────────────┘
//
// 等价于原 gRPC 实现中的 UnaryInterceptor，但使用 JSON-RPC 2.0 协议：
//   - 请求中的治理元数据通过 params._meta 传递（而非 gRPC metadata header）
//   - 响应中的价格信息通过 result._meta 返回（而非 gRPC response header）
//   - 错误通过 JSON-RPC error 对象返回（而非 gRPC status code）
//
// 流程：解析请求 → 提取治理元数据 → 负载削减判定 → 调用工具 → 附加价格信息
func (gov *MCPGovernor) HandleToolCall(ctx context.Context, req *JSONRPCRequest, handler ToolCallHandler) *JSONRPCResponse {
	// 1. 解析 tools/call 参数
	var params MCPToolCallParams
	if err := json.Unmarshal(req.Params, &params); err != nil {
		return NewErrorResponse(req.ID, CodeInvalidParams, "无效的工具调用参数", err.Error())
	}

	// 2. 提取治理元数据 (_meta)
	var tokens int64
	var callerName string
	toolName := params.Name

	if params.Meta != nil {
		tokens = params.Meta.Tokens
		callerName = params.Meta.Name
		if params.Meta.Method != "" {
			toolName = params.Meta.Method
		}
	}

	logger("[收到请求]: 来自 %s 的工具调用 %s, 令牌 %d\n", callerName, toolName, tokens)

	// 3. 执行负载削减 (核心准入控制)
	tokenLeft, priceString, err := gov.LoadShedding(ctx, tokens, toolName)

	// 4. 令牌不足 → 拒绝请求，通过 JSON-RPC error.data 返回价格
	if err == ErrInsufficientTokens && gov.loadShedding {
		if tokens%gov.priceFreq == 0 {
			logger("[拒绝响应]: 工具 %s 当前价格 %s\n", toolName, priceString)
		}
		return NewErrorResponse(req.ID, CodeOverloaded,
			fmt.Sprintf("工具 %s 过载，请求被 %s 拒绝。请稍后重试。", toolName, gov.nodeName),
			map[string]string{"price": priceString, "name": gov.nodeName, "regime": gov.activeRegime})
	}

	// 5. 其他错误
	if err != nil && err != ErrInsufficientTokens {
		return NewErrorResponse(req.ID, CodeInternalError, err.Error(),
			map[string]string{"regime": gov.activeRegime})
	}

	// 6. 如果是 additive 策略，将剩余令牌分配给下游工具
	if gov.priceAggregation == "additive" {
		_, _ = gov.SplitTokens(ctx, tokenLeft, toolName)
	}

	// 7. 调用实际的工具处理函数
	result, err := handler(ctx, params)
	if err != nil {
		return NewErrorResponse(req.ID, CodeInternalError, err.Error(),
			map[string]string{"regime": gov.activeRegime})
	}

	// 8. 在响应 _meta 中附加价格信息，供客户端更新缓存
	if !gov.skipPrice {
		if tokens%gov.priceFreq == 0 {
			if result.Meta == nil {
				result.Meta = &ResponseMeta{}
			}
			result.Meta.Price = priceString
			result.Meta.Name = gov.nodeName
			result.Meta.Regime = gov.activeRegime
			logger("[响应]: 工具 %s 的当前价格为 %s\n", toolName, priceString)
		}
	}

	return NewSuccessResponse(req.ID, result)
}

// HandleToolCallDirect 直接处理已解析的工具调用（不经过 JSON-RPC 解析）
// 适用于进程内调用或单元测试，避免序列化/反序列化开销
func (gov *MCPGovernor) HandleToolCallDirect(ctx context.Context, params MCPToolCallParams, handler ToolCallHandler) (*MCPToolCallResult, error) {
	var tokens int64
	toolName := params.Name

	if params.Meta != nil {
		tokens = params.Meta.Tokens
		if params.Meta.Method != "" {
			toolName = params.Meta.Method
		}
	}

	_, priceString, err := gov.LoadShedding(ctx, tokens, toolName)

	if err == ErrInsufficientTokens && gov.loadShedding {
		return nil, &RPCError{
			Code:    CodeOverloaded,
			Message: fmt.Sprintf("工具 %s 过载，请求被 %s 拒绝", toolName, gov.nodeName),
			Data:    map[string]string{"price": priceString, "name": gov.nodeName},
		}
	}

	if err != nil && err != ErrInsufficientTokens {
		return nil, err
	}

	result, err := handler(ctx, params)
	if err != nil {
		return nil, err
	}

	if !gov.skipPrice {
		if tokens%gov.priceFreq == 0 {
			if result.Meta == nil {
				result.Meta = &ResponseMeta{}
			}
			result.Meta.Price = priceString
			result.Meta.Name = gov.nodeName
		}
	}

	return result, nil
}

// ClientMiddleware 客户端治理中间件
// 等价于原 gRPC 实现中的 UnaryInterceptorEnduser
// 负责: 令牌注入到 _meta、限流检查、退避逻辑
func (gov *MCPGovernor) ClientMiddleware(ctx context.Context, params *MCPToolCallParams) error {
	toolName := params.Name

	// 退避检查：如果最近刚被限流过且冷却时间未到，直接放弃
	if gov.clientBackoff > 0 && time.Since(gov.lastRateLimitedTime) < gov.clientBackoff {
		if !gov.rateLimitWaiting {
			return &RPCError{Code: CodeRateLimited, Message: "客户端退避中，请求被丢弃"}
		}
	}

	startTime := time.Now()

	for {
		// 超时检查
		if gov.rateLimiting && gov.rateLimitWaiting && time.Since(startTime) > gov.clientTimeOut {
			return &RPCError{Code: CodeRateLimited, Message: "等待令牌超时"}
		}

		// 获取当前钱包余额
		tok := gov.GetTokensLeft()

		// 均匀策略：随机使用 0 到剩余令牌之间的值
		if gov.tokenStrategy == "uniform" && tok > 0 {
			tok = rand.Int63n(tok)
		}

		// 如果未开启限流，直接注入令牌
		if !gov.rateLimiting {
			gov.injectMeta(params, tok, toolName)
			break
		}

		// 检查令牌是否够用
		rateLimit := gov.RateLimiting(ctx, tok, toolName)

		if rateLimit == ErrRateLimited {
			if gov.clientBackoff > 0 {
				if time.Since(gov.lastRateLimitedTime) > gov.clientBackoff {
					gov.lastRateLimitedTime = time.Now()
				}
			}
			if !gov.rateLimitWaiting {
				return &RPCError{Code: CodeRateLimited, Message: "客户端被限流"}
			}
			// 阻塞等待令牌补充
			<-gov.rateLimiter
		} else {
			gov.injectMeta(params, tok, toolName)
			break
		}
	}

	return nil
}

// injectMeta 将治理元数据注入到工具调用参数的 _meta 字段
func (gov *MCPGovernor) injectMeta(params *MCPToolCallParams, tokens int64, toolName string) {
	if params.Meta == nil {
		params.Meta = &GovernanceMeta{}
	}
	params.Meta.Tokens = tokens
	params.Meta.Name = gov.nodeName
	params.Meta.Method = toolName
}

// UpdateResponsePrice 从工具调用响应中提取价格并更新本地缓存
// 等价于原 gRPC UnaryInterceptorClient 中解析 header["price"] 的逻辑
func (gov *MCPGovernor) UpdateResponsePrice(ctx context.Context, toolName string, result *MCPToolCallResult) {
	if result == nil || result.Meta == nil || result.Meta.Price == "" {
		logger("[响应后处理]: 未收到价格信息\n")
		return
	}

	price, err := strconv.ParseInt(result.Meta.Price, 10, 64)
	if err != nil {
		logger("[响应后处理]: 解析价格失败: %v\n", err)
		return
	}

	serverName := result.Meta.Name
	gov.UpdateDownstreamPrice(ctx, toolName, serverName, price)
	logger("[响应后处理]: 收到来自 %s 的价格 %d\n", serverName, price)
}

// GetToolEffectivePrice 获取指定工具的有效价格（ownPrice × 工具权重）
// >>> Eq.(1): P_eff(t) = P_own × w_t
// 用于 MCPDP 网关计算 DAG 全链路总价格 C_total = Σ P_eff(t_i)
func (gov *MCPGovernor) GetToolEffectivePrice(toolName string) int64 {
	ownPriceVal, ok := gov.priceTableMap.Load("ownprice")
	if !ok {
		return 0
	}
	ownPrice := ownPriceVal.(int64)
	if w, exists := gov.toolWeights[toolName]; exists && w > 1 {
		ownPrice *= w
	}
	return ownPrice
}
