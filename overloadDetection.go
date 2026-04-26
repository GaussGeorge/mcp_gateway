package mcpgov

import (
	"context"
	"math"
	"runtime/metrics"
	"sync/atomic"
	"time"
)

// 定义包级别的 context key 类型，防止与其他包的 context key 冲突
// 这是一个 Go 语言的最佳实践，避免使用 string 作为 key 导致冲突
type ctxKey string

// 定义具体的 key 常量
// GapLatency 指的是两个采样点之间的增量排队延迟
const GapLatencyKey ctxKey = "gapLatency"

// Increment 原子地增加吞吐量计数器
// 在高并发场景下，使用 atomic 操作比 mutex 锁性能更好
func (gov *MCPGovernor) Increment() {
	atomic.AddInt64(&gov.throughputCounter, 1)
}

// Decrement 原子地减少吞吐量计数器
func (gov *MCPGovernor) Decrement(step int64) {
	atomic.AddInt64(&gov.throughputCounter, -step)
}

// GetCount 获取当前的吞吐量计数，并将其重置为 0
// 注意：这是一个 "Read-and-Reset" 操作，用于获取上一个时间窗口内的总请求数
// 使用 atomic.SwapInt64 保证读取和重置是一个不可分割的原子操作
func (gov *MCPGovernor) GetCount() int64 {
	// 对应原子操作：取值并交换为0
	return atomic.SwapInt64(&gov.throughputCounter, 0)
}

// latencyCheck 基于“观察到的延迟”进行周期性检查
// 这里的 latency 是业务逻辑中记录的实际处理耗时 (End-to-End Latency)
// 这是一个后台协程，会一直运行
func (gov *MCPGovernor) latencyCheck() {
	// time.Tick 创建一个定时器通道，每隔 priceUpdateRate 触发一次
	for range time.Tick(gov.priceUpdateRate) {
		// 创建一个新的 incoming context（此处原代码注释掉了 request-id 的生成）

		// create a new incoming context with the "request-id" as "0"
		// ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("request-id", "0"))

		// 更新自身价格：
		// 判定逻辑：如果 观察到的总延迟 > (延迟阈值 * 请求数量)
		// 这实际上是在比较：平均延迟(observedDelay / GetCount) 是否超过了 单次请求的延迟阈值(latencyThreshold)
		// pt.GetCount() 会重置计数器，pt.observedDelay 也会被重置，所以比较的是上一个时间窗口内的均值
		gov.UpdateOwnPrice(gov.observedDelay.Milliseconds() > gov.latencyThreshold.Milliseconds()*gov.GetCount())

		// 重置观察到的延迟累加值，准备下一个周期
		gov.observedDelay = time.Duration(0)
	}
}

// queuingCheck 检查 Go 协程(Goroutine)的排队延迟是否超过了 SLO (服务等级目标)
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文 §3.6 Dynamic Pricing Engine 的输入信号源                  │
// │                                                               │
// │ 利用 Go runtime/metrics 读取调度器延迟直方图                │
// │ 计算 gapLatency → 传入 Eq.(6) 中作为 Δq 的基础             │
// │ 调用 maybeApplyAdaptiveProfile() 实现负载档位自适应切换    │
// │                                                               │
// │ 档位检测: bursty/periodic/steady 三种负载模式              │
// │ 每种模式对应不同的定价参数组 (Profile)                      │
// └─────────────────────────────────────────────────────────────────┘
//
// 它利用 Go runtime/metrics 库来读取底层的调度器延迟直方图 (Scheduler Latency)
// 这是一个更底层的指标，反映了 CPU 饱和度，比业务延迟更早预警过载
func (gov *MCPGovernor) queuingCheck() {
	// 初始化一个空的直方图指针，用于存储上一次的快照
	var prevHist *metrics.Float64Histogram

	for range time.Tick(gov.priceUpdateRate) {
		// 开始计时，用于统计本次检查操作本身的开销（监控自身的性能损耗）
		start := time.Now()

		// 读取当前的运行时直方图 (Go Runtime Metrics)
		// 这个函数会调用 runtime/metrics.Read
		currHist := readHistogram()

		if prevHist == nil {
			// 如果是第一次运行，没有历史数据做对比，直接保存当前直方图并跳过本次循环
			prevHist = currHist
			continue
		}

		// 计算“间隙延迟”(Gap Latency)：
		// 即在上一个周期(prev)到当前周期(curr)之间，产生的排队延迟的最大值
		// 这是通过两个直方图相减（Differential Histogram）计算出来的
		gapLatency := maximumQueuingDelayms(prevHist, currHist)

		// 自适应档位检测（在定价前执行，以便本轮使用新参数）
		gov.maybeApplyAdaptiveProfile(gapLatency)

		ctx := context.Background()

		logger("[增量等待时间最大值]: %f ms.\n", gapLatency)

		// 将计算出的排队延迟存入 context，传递给后续的 overloadDetection 函数使用
		ctx = context.WithValue(ctx, GapLatencyKey, gapLatency)

		// 根据价格策略更新价格
		if gov.priceStrategy == "step" {
			// step 策略：简单的步进调整 (涨/跌)
			gov.UpdateOwnPrice(gov.overloadDetection(ctx))
		} else {
			// 其他策略（如 exponential）：直接根据具体数值计算涨幅
			gov.UpdatePrice(ctx)
		}

		// 将当前直方图保存为“上一次”，用于下一次迭代的差分计算
		prevHist = currHist

		// 记录查询和计算直方图本身的耗时 (微秒转毫秒)
		logger("[查询延迟]:    计算开销为 %.2f 毫秒\n", float64(time.Since(start).Microseconds())/1000)
	}
}

// throughputCheck 仅基于吞吐量计数器进行检查
// 如果单位时间内的请求数 (RPS/QPS) 超过阈值，则认为过载
func (gov *MCPGovernor) throughputCheck() {
	for range time.Tick(gov.priceUpdateRate) {
		// 这里原先可能有直接减少计数器的逻辑，现已注释

		// pt.Decrement(pt.throughputThreshold)
		// ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("request-id", "0"))

		logger("[吞吐量计数器]:   当前计数为 %d\n", gov.throughputCounter)

		// 更新自身价格：
		// GetCount() 会返回当前周期的请求数并重置计数器
		// 如果 请求数 > 吞吐量阈值，则判定为过载，触发涨价
		gov.UpdateOwnPrice(gov.GetCount() > gov.throughputThreshold)
	}
}

// checkBoth 同时检查吞吐量和排队延迟 (And 逻辑)
// 更保守的策略：只有两者都满足条件时才触发价格调整
// 防止因为短时毛刺 (Spike) 导致误判
func (gov *MCPGovernor) checkBoth() {
	var prevHist *metrics.Float64Histogram

	for range time.Tick(gov.priceUpdateRate) {
		logger("[吞吐量计数器]:   当前计数为 %d\n", gov.throughputCounter)

		// 获取当前直方图
		currHist := readHistogram()

		// 计算两个直方图之间的差异 (增量直方图)
		diff := metrics.Float64Histogram{}
		if prevHist == nil {
			// 如果没有历史数据，差异就是当前数据本身
			diff = *currHist
		} else {
			// 计算差值：curr - prev，得到这个时间窗口内的分布情况
			// 注意：GetHistogramDifference 和 readHistogram 一样，是辅助函数
			diff = GetHistogramDifference(*prevHist, *currHist)
		}

		// 从差异直方图中提取统计指标 (毫秒)
		gapLatency := maximumBucket(&diff)

		// 计算当前直方图（累计值）的中位数延迟
		cumulativeLat := medianBucket(currHist)

		logger("[累计等待时间中位数]:   %f ms.\n", cumulativeLat)
		logger("[增量等待时间 90分位]: %f ms.\n", percentileBucket(&diff, 90))
		logger("[增量等待时间中位数]:  %f ms.\n", medianBucket(&diff))
		logger("[增量等待时间最大值]:  %f ms.\n", maximumBucket(&diff))

		// 联合判定逻辑 (Overload Condition)：
		// 1. 吞吐量是否超过阈值 (pt.GetCount > pt.throughputThreshold)
		//    AND
		// 2. 增量最大排队延迟是否超过延迟阈值 (gapLatency > pt.latencyThreshold)
		// 注意：gapLatency 单位是 ms，latencyThreshold 是 Duration，所以做了单位转换比较
		gov.UpdateOwnPrice(gov.GetCount() > gov.throughputThreshold && int64(gapLatency*1000) > gov.latencyThreshold.Microseconds())

		// 更新历史直方图
		prevHist = currHist
	}
}

// overloadDetection 是一个辅助函数，用于从 Context 中提取信号并进行判定
// 输入信号：通常是 GapLatencyKey 对应的排队延迟
// 输出：bool (true 表示过载，false 表示正常)
func (gov *MCPGovernor) overloadDetection(ctx context.Context) bool {
	// 如果开启了基于排队延迟的检测 (pinpointQueuing)
	if gov.pinpointQueuing {
		var gapLatency float64

		// 从 context 中读取排队延迟数值
		val := ctx.Value(GapLatencyKey)
		if val == nil {
			gapLatency = 0.0
		} else {
			// 类型断言：将 interface{} 转回 float64
			gapLatency = val.(float64)
		}

		// 比较：如果 排队延迟 > 延迟阈值，则返回 true (过载)
		// gapLatency * 1000 将毫秒转换为微秒，与 Microseconds() 进行比较
		if int64(gapLatency*1000) > gov.latencyThreshold.Microseconds() {
			return true
		}
	}
	return false
}

// ================================================================
// Load Regime Detector + Parameter Profile
// ================================================================

// initAdaptiveProfiles 初始化参数档位，支持 options 覆盖。
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文 §3.6 Dynamic Pricing Engine — Load Regime Adaptation       │
// │                                                               │
// │ 三种负载模式对应三组 Eq.(6) 参数:                            │
// │   Bursty:   极速熔断 — 低阈值+高增益+高衰减率                 │
// │   Periodic: 高阻尼防震荡 — 高阈值+低增益+快衰减              │
// │   Steady:   迟钝保守 — 默认档位 (DP-NoRegime 锁死于此)       │
// │                                                               │
// │ maybeApplyAdaptiveProfile() 基于方差+尖峰+趋势检测自动切换  │
// └─────────────────────────────────────────────────────────────────┘
func (gov *MCPGovernor) initAdaptiveProfiles(
	burstyOpt, periodicOpt, steadyOpt map[string]interface{},
) {
	// 默认值（对齐参考表）
	// Bursty: 极速熔断 — 低阈值+高增益+高衰减率，快速拉高价格拒绝低价值请求
	bursty := Profile{
		PriceStep:         300,
		PriceDecayStep:    30,
		PriceSensitivity:  6000,
		LatencyThreshold:  200 * time.Microsecond,
		DecayRate:         0.8,
		PriceUpdateRate:   5 * time.Millisecond,
		MaxToken:          200,
		IntegralThreshold: gov.integralThreshold,
		IntegralDecay:     gov.integralDecay,
		DetectorPriceStep: 50,
		DetectorDecayStep: 1,
		DetectorMaxConc:   2,
	}
	// Periodic: 高阻尼防震荡 — 高阈值+低增益+快衰减，价格安如泰山不追波
	periodic := Profile{
		PriceStep:         50,
		PriceDecayStep:    5,
		PriceSensitivity:  20000,
		LatencyThreshold:  600 * time.Microsecond,
		DecayRate:         0.995,
		PriceUpdateRate:   50 * time.Millisecond,
		MaxToken:          200,
		IntegralThreshold: gov.integralThreshold,
		IntegralDecay:     gov.integralDecay,
		DetectorPriceStep: 15,
		DetectorDecayStep: 3,
		DetectorMaxConc:   5,
	}
	// Steady: 迟钝保守（DP-NoRegime 锁死于此档位）
	steady := Profile{
		PriceStep:         10,
		PriceDecayStep:    1,
		PriceSensitivity:  10000,
		LatencyThreshold:  400 * time.Microsecond,
		DecayRate:         0.99,
		PriceUpdateRate:   200 * time.Millisecond,
		MaxToken:          200,
		IntegralThreshold: gov.integralThreshold,
		IntegralDecay:     gov.integralDecay,
		DetectorPriceStep: 2,
		DetectorDecayStep: 10,
		DetectorMaxConc:   8,
	}

	// options 覆盖
	if burstyOpt != nil {
		applyProfileOptions(&bursty, burstyOpt)
	}
	if periodicOpt != nil {
		applyProfileOptions(&periodic, periodicOpt)
	}
	if steadyOpt != nil {
		applyProfileOptions(&steady, steadyOpt)
	}

	if gov.parameterProfiles == nil {
		gov.parameterProfiles = make(map[string]Profile)
	}
	gov.parameterProfiles["steady"] = steady
	gov.parameterProfiles["periodic"] = periodic
	gov.parameterProfiles["bursty"] = bursty
}

// applyProfileOptions 用 options 字典覆盖 profile 字段。
func applyProfileOptions(p *Profile, opt map[string]interface{}) {
	if v, ok := opt["PriceStep"].(int64); ok {
		p.PriceStep = v
	}
	if v, ok := opt["PriceDecayStep"].(int64); ok {
		p.PriceDecayStep = v
	}
	if v, ok := opt["PriceSensitivity"].(int64); ok {
		p.PriceSensitivity = v
	}
	if v, ok := opt["LatencyThreshold"].(time.Duration); ok {
		p.LatencyThreshold = v
	}
	if v, ok := opt["DecayRate"].(float64); ok {
		p.DecayRate = v
	}
	if v, ok := opt["PriceUpdateRate"].(time.Duration); ok {
		p.PriceUpdateRate = v
	}
	if v, ok := opt["MaxToken"].(int64); ok {
		p.MaxToken = v
	}
	if v, ok := opt["IntegralThreshold"].(float64); ok {
		p.IntegralThreshold = v
	}
	if v, ok := opt["IntegralDecay"].(float64); ok {
		p.IntegralDecay = v
	}
	if v, ok := opt["DetectorPriceStep"].(int64); ok {
		p.DetectorPriceStep = v
	}
	if v, ok := opt["DetectorDecayStep"].(int64); ok {
		p.DetectorDecayStep = v
	}
	if v, ok := opt["DetectorMaxConc"].(int64); ok {
		p.DetectorMaxConc = v
	}
}

// maybeApplyAdaptiveProfile 根据最近窗口统计特征识别状态并热切换参数。
// >>> §3.6: 基于 variance/spike/trend 自动切换 Bursty/Periodic/Steady 档位
// 切换后热更新 Eq.(6) 中的所有参数: priceStep, priceSensitivity, latencyThreshold 等
func (gov *MCPGovernor) maybeApplyAdaptiveProfile(gapLatency float64) {
	if !gov.enableAdaptiveProfile || gov.regimeWindow <= 0 {
		return
	}

	gov.regimeHistory[gov.regimeIndex] = gapLatency
	gov.regimeIndex = (gov.regimeIndex + 1) % gov.regimeWindow
	if gov.regimeCount < gov.regimeWindow {
		gov.regimeCount++
	}

	if gov.regimeCount < 3 {
		gov.lastGapLatency = gapLatency
		return
	}

	variance := calculateVariance(gov.regimeHistory, gov.regimeCount)
	delta := math.Abs(gapLatency - gov.lastGapLatency)
	gov.lastGapLatency = gapLatency

	// 趋势检测：比较窗口前半均值 vs 后半均值，识别负载上升趋势
	trendRatio := 1.0
	if gov.regimeCount >= 10 {
		halfCount := gov.regimeCount / 2
		olderSum, newerSum := 0.0, 0.0
		for i := 0; i < halfCount; i++ {
			idx := (gov.regimeIndex + i) % gov.regimeWindow
			olderSum += gov.regimeHistory[idx]
		}
		for i := halfCount; i < gov.regimeCount; i++ {
			idx := (gov.regimeIndex + i) % gov.regimeWindow
			newerSum += gov.regimeHistory[idx]
		}
		olderMean := olderSum / float64(halfCount)
		newerMean := newerSum / float64(gov.regimeCount-halfCount)
		if olderMean > 0.5 {
			trendRatio = newerMean / olderMean
		}
	}

	targetRegime := gov.activeRegime
	if delta >= gov.regimeSpikeThreshold || trendRatio > 1.3 {
		targetRegime = "bursty"
	} else if variance >= gov.regimeVarianceHigh {
		targetRegime = "periodic"
	} else if variance <= gov.regimeVarianceLow {
		targetRegime = "steady"
	}

	if targetRegime == gov.activeRegime {
		return
	}
	if time.Since(gov.lastProfileSwitch) < gov.profileSwitchCooldown {
		return
	}

	profile, ok := gov.parameterProfiles[targetRegime]
	if !ok {
		return
	}

	gov.priceStep = maxInt64(profile.PriceStep, 1)
	gov.priceDecayStep = maxInt64(profile.PriceDecayStep, 1)
	gov.priceSensitivity = maxInt64(profile.PriceSensitivity, 1)
	if profile.LatencyThreshold > 0 {
		gov.latencyThreshold = profile.LatencyThreshold
	}
	if profile.DecayRate > 0 && profile.DecayRate <= 1.0 {
		gov.decayRate = profile.DecayRate
	}
	if profile.PriceUpdateRate > 0 {
		gov.priceUpdateRate = profile.PriceUpdateRate
	}
	if profile.MaxToken > 0 {
		gov.maxToken = profile.MaxToken
	}
	if profile.IntegralThreshold >= 0 {
		gov.integralThreshold = profile.IntegralThreshold
	}
	if profile.IntegralDecay > 0 && profile.IntegralDecay <= 1.0 {
		gov.integralDecay = profile.IntegralDecay
	}

	logger("[AdaptiveProfile]: %s -> %s, variance=%.4f, delta=%.4f, trend=%.2f, priceStep=%d, threshold=%s, decay=%.2f, updateRate=%s, detPS=%d detDS=%d detMC=%d\n",
		gov.activeRegime, targetRegime, variance, delta, trendRatio, gov.priceStep, gov.latencyThreshold.String(), gov.decayRate, gov.priceUpdateRate.String(),
		profile.DetectorPriceStep, profile.DetectorDecayStep, profile.DetectorMaxConc)

	gov.activeRegime = targetRegime
	gov.lastProfileSwitch = time.Now()
}

// calculateVariance 计算环形缓冲区中 count 个样本的方差。
func calculateVariance(data []float64, count int) float64 {
	if count < 2 {
		return 0
	}
	sum := 0.0
	for i := 0; i < count; i++ {
		sum += data[i]
	}
	mean := sum / float64(count)
	variance := 0.0
	for i := 0; i < count; i++ {
		diff := data[i] - mean
		variance += diff * diff
	}
	return variance / float64(count)
}

// maxInt64 返回两个 int64 中较大的那个。
func maxInt64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

// GetDetectorParams 返回当前活跃档位的代理检测器参数。
// 在反向代理架构中，proxyOverloadDetector 调用此方法读取自适应参数。
func (gov *MCPGovernor) GetDetectorParams() (priceStep, decayStep, maxConc int64) {
	profile, ok := gov.parameterProfiles[gov.activeRegime]
	if !ok {
		return 10, 5, 4 // 兜底默认值
	}
	return profile.DetectorPriceStep, profile.DetectorDecayStep, profile.DetectorMaxConc
}

// GetActiveRegime 返回当前活跃的负载档位名称。
func (gov *MCPGovernor) GetActiveRegime() string {
	return gov.activeRegime
}

// ApplyAdaptiveProfileSignal 外部注入过载信号，触发自适应档位检测。
// 在反向代理架构中，由 proxyOverloadDetector 调用，注入平滑后的并发度信号。
func (gov *MCPGovernor) ApplyAdaptiveProfileSignal(signal float64) {
	gov.maybeApplyAdaptiveProfile(signal)
}
