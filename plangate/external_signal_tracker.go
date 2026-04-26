package plangate

import (
	"log"
	"math"
	"sort"
	"sync"
	"time"
)

// ExternalSignalTracker 外部信号治理强度跟踪器（真实 LLM API 模式）
//
// ┌─────────────────────────────────────────────────────────────────────┐
// │ 论文 §3.5 Governance Intensity Tracking (真实 LLM 场景实现)      │
// │                                                                   │
// │ Eq.(5): S_raw = Σ_{d∈D_active} w_d·s_d / Σ_{d∈D_active} w_d    │
// │                                                                   │
// │ 三维信号融合:                                                       │
// │   d=429:       s_d = 滑动窗口内 429 响应占比 (配额压力)            │
// │   d=latency:   s_d = EMA(P95) / threshold (负载压力)              │
// │   d=rateLimit: s_d = 1 - EMA(remaining)/max (前瞻性配额压力)     │
// │                                                                   │
// │ 只对有数据的维度参与加权平均 (D_active ⊆ {429, latency, rateLimit})│
// │ 输出 I(t) ∈ [0, 1] 用于 Eq.(3) 和 Eq.(4) 中的强度调制            │
// └─────────────────────────────────────────────────────────────────────┘
//
// 三维信号融合:
//   1. 429 频率: 滑动窗口内 429 响应占比 → API 配额压力
//   2. 延迟 P95 EMA: API 响应延迟的第 95 百分位指数移动平均 → 后端负载压力
//   3. RateLimit-Remaining EMA: API 剩余配额的指数移动平均 → 前瞻性配额压力
//
// 信号组合 → 加权评分 → EMA 平滑 → 滞回激活/停用 → 治理强度 ∈ [0, 1]
type ExternalSignalTracker struct {
	mu sync.Mutex

	// 滑动窗口
	window         []apiResponseRecord
	windowDuration time.Duration

	// 延迟 P95 EMA
	latencyP95EMA    float64 // 平滑后的 P95 延迟 (ms)
	latencyThreshold float64 // 参考阈值: P95 达此值时延迟压力=1.0

	// RateLimit-Remaining EMA
	rateLimitEMA float64 // 平滑后的剩余配额
	rateLimitMax float64 // 参考最大值 (如 GLM-4-Flash = 200)

	// 融合权重
	w429       float64 // 429 频率信号权重
	wLatency   float64 // 延迟 P95 信号权重
	wRateLimit float64 // 剩余配额信号权重

	// EMA 参数
	emaAlpha float64

	// 滞回门控
	active                bool
	smoothedIntensity     float64
	activationThreshold   float64 // 原始评分超过此值才计入激活
	deactivationThreshold float64 // 原始评分低于此值才计入停用
	consecutiveHigh       int
	consecutiveLow        int
	activationCount       int // 连续 N 次高分才激活
	deactivationCount     int // 连续 N 次低分才停用
}

// apiResponseRecord 单次 API 响应的信号记录
type apiResponseRecord struct {
	timestamp time.Time
	is429     bool
	latencyMs float64
	remaining float64 // -1 表示不可用
}

// NewExternalSignalTracker 创建外部信号跟踪器
//
//	rateLimitMax: API 配额上限 (如 GLM-4-Flash = 200)
//	latencyThresholdMs: P95 延迟达到此值时延迟压力=1.0 (如 5000ms)
func NewExternalSignalTracker(rateLimitMax float64, latencyThresholdMs float64) *ExternalSignalTracker {
	return &ExternalSignalTracker{
		windowDuration:        30 * time.Second,
		latencyThreshold:      latencyThresholdMs,
		rateLimitMax:          rateLimitMax,
		emaAlpha:              0.15,
		w429:                  0.5,  // 429 是最强的限流信号
		wLatency:              0.3,  // 延迟是负载的直接反映
		wRateLimit:            0.2,  // 剩余配额是前瞻性信号
		activationThreshold:   0.15, // 综合评分 > 0.15 才开始计入激活
		deactivationThreshold: 0.05, // 综合评分 < 0.05 才开始计入停用
		activationCount:       3,    // 连续 3 次高分激活（3×report 间隔）
		deactivationCount:     10,   // 连续 10 次低分停用（防震荡）
	}
}

// ReportResponse 报告单次 API 响应的外部信号
// 由 makeProxyHandlerWithSignals 在每次后端响应后调用
func (t *ExternalSignalTracker) ReportResponse(is429 bool, latencyMs float64, rateLimitRemaining float64) {
	t.mu.Lock()
	defer t.mu.Unlock()

	now := time.Now()
	t.window = append(t.window, apiResponseRecord{
		timestamp: now,
		is429:     is429,
		latencyMs: latencyMs,
		remaining: rateLimitRemaining,
	})

	// 裁剪过期记录
	cutoff := now.Add(-t.windowDuration)
	trimIdx := 0
	for trimIdx < len(t.window) && t.window[trimIdx].timestamp.Before(cutoff) {
		trimIdx++
	}
	if trimIdx > 0 {
		t.window = t.window[trimIdx:]
	}

	// 计算原始综合评分
	rawScore := t.computeRawScoreLocked()

	// 滞回门控
	if !t.active {
		if rawScore > t.activationThreshold {
			t.consecutiveHigh++
			t.consecutiveLow = 0
			if t.consecutiveHigh >= t.activationCount {
				t.active = true
				t.smoothedIntensity = rawScore
				log.Printf("[ExternalSignalTracker] ACTIVATED: rawScore=%.3f", rawScore)
			}
		} else {
			t.consecutiveHigh = 0
		}
	} else {
		if rawScore < t.deactivationThreshold {
			t.consecutiveLow++
			t.consecutiveHigh = 0
			if t.consecutiveLow >= t.deactivationCount {
				t.active = false
				t.smoothedIntensity = 0
				log.Printf("[ExternalSignalTracker] DEACTIVATED after %d low cycles", t.deactivationCount)
				return
			}
			// 活跃但低分 → 衰减强度
			t.smoothedIntensity *= (1 - t.emaAlpha)
		} else {
			t.consecutiveLow = 0
			t.consecutiveHigh++
			// EMA 平滑更新
			t.smoothedIntensity = t.emaAlpha*rawScore + (1-t.emaAlpha)*t.smoothedIntensity
		}
	}
}

// computeRawScoreLocked 计算三维信号加权融合评分 ∈ [0, 1]
// >>> Eq.(5): S_raw = Σ_{d∈D_active} w_d·s_d / Σ_{d∈D_active} w_d
// 调用者必须持有 mu 锁
func (t *ExternalSignalTracker) computeRawScoreLocked() float64 {
	n := len(t.window)
	if n == 0 {
		return 0
	}

	// ── 信号 1: 429 频率 ──
	// >>> Eq.(5) 维度 d=429: s_d = count429/n
	count429 := 0
	for _, r := range t.window {
		if r.is429 {
			count429++
		}
	}
	rate429 := float64(count429) / float64(n)

	// ── 信号 2: 延迟 P95 EMA ──
	// >>> Eq.(5) 维度 d=latency: s_d = EMA(P95_latency) / latencyThreshold
	latencies := make([]float64, 0, n)
	for _, r := range t.window {
		if r.latencyMs > 0 {
			latencies = append(latencies, r.latencyMs)
		}
	}
	var latencyPressure float64
	if len(latencies) > 0 {
		sort.Float64s(latencies)
		p95idx := int(math.Ceil(float64(len(latencies))*0.95)) - 1
		if p95idx < 0 {
			p95idx = 0
		}
		if p95idx >= len(latencies) {
			p95idx = len(latencies) - 1
		}
		p95 := latencies[p95idx]
		t.latencyP95EMA = t.emaAlpha*p95 + (1-t.emaAlpha)*t.latencyP95EMA
		latencyPressure = t.latencyP95EMA / t.latencyThreshold
		if latencyPressure > 1.0 {
			latencyPressure = 1.0
		}
	}

	// ── 信号 3: RateLimit-Remaining EMA ──
	// >>> Eq.(5) 维度 d=rateLimit: s_d = 1 - EMA(remaining) / rateLimitMax
	var rateLimitPressure float64
	// 使用最新的 rate_limit_remaining 值
	for i := len(t.window) - 1; i >= 0; i-- {
		if t.window[i].remaining >= 0 {
			t.rateLimitEMA = t.emaAlpha*t.window[i].remaining + (1-t.emaAlpha)*t.rateLimitEMA
			rateLimitPressure = 1.0 - (t.rateLimitEMA / t.rateLimitMax)
			if rateLimitPressure < 0 {
				rateLimitPressure = 0
			}
			if rateLimitPressure > 1.0 {
				rateLimitPressure = 1.0
			}
			break
		}
	}

	// ── 动态归一化加权融合 ──
	// >>> Eq.(5): 只对实际有信号的维度参与加权，避免无信号维度将评分压在 0.5 以下
	activeWeight := 0.0
	weightedSum := 0.0

	if count429 > 0 {
		activeWeight += t.w429
		weightedSum += t.w429 * rate429
	}
	if len(latencies) > 0 {
		activeWeight += t.wLatency
		weightedSum += t.wLatency * latencyPressure
	}
	hasRateLimit := false
	for _, r := range t.window {
		if r.remaining >= 0 {
			hasRateLimit = true
			break
		}
	}
	if hasRateLimit {
		activeWeight += t.wRateLimit
		weightedSum += t.wRateLimit * rateLimitPressure
	}

	var score float64
	if activeWeight > 0 {
		score = weightedSum / activeWeight // 归一化到 [0, 1]
	}
	if score > 1.0 {
		score = 1.0
	}
	return score
}

// GetIntensity 获取当前治理强度 [0, 1]（实现 IntensityProvider 接口）
func (t *ExternalSignalTracker) GetIntensity() float64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.smoothedIntensity
}

// IsActive 获取治理是否处于活跃状态（实现 IntensityProvider 接口）
func (t *ExternalSignalTracker) IsActive() bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.active
}

// GetStats 获取跟踪器统计信息（调试用）
func (t *ExternalSignalTracker) GetStats() map[string]interface{} {
	t.mu.Lock()
	defer t.mu.Unlock()
	count429 := 0
	for _, r := range t.window {
		if r.is429 {
			count429++
		}
	}
	return map[string]interface{}{
		"window_size":       len(t.window),
		"count_429":         count429,
		"latency_p95_ema":   t.latencyP95EMA,
		"rate_limit_ema":    t.rateLimitEMA,
		"active":            t.active,
		"intensity":         t.smoothedIntensity,
		"consecutive_high":  t.consecutiveHigh,
		"consecutive_low":   t.consecutiveLow,
	}
}
