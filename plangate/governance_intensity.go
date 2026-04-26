package plangate

import (
	"log"
	"sync"
	"time"

	mcpgov "mcp-governance"
)

// GovernanceIntensityTracker 滞回门控治理强度跟踪器（mock 场景模式）
//
// ┌─────────────────────────────────────────────────────────────────┐
// │ 论文 §3.5 Governance Intensity Tracking (mock 场景实现)          │
// │                                                               │
// │ 输出 I(t) ∈ [0, 1]，用于 Eq.(3) 和 Eq.(4) 中的强度调制:      │
// │   Eq.(3): P_step0 = P_base × I(t) × L(t)                    │
// │   Eq.(4): P_K = P_eff × I(t) / (1 + K² · α_eff)              │
// │                                                               │
// │ mock 场景采样 ownPrice → EMA 平滑 → 滞回门控激活/停用        │
// │ raw = ownPrice / referencePrice, I(t) = EMA(raw)             │
// │ 真实 LLM 场景使用 ExternalSignalTracker (Eq.5 三维信号融合)    │
// └─────────────────────────────────────────────────────────────────┘
//
// 解决问题：低并发下 proxyOverloadDetector 产生的瞬时正价格导致
// Zero-Load Free Pass 失效，ReAct 会话被不必要地拒绝。
//
// 机制：
//   - 后台每 sampleInterval 采样 ownPrice
//   - 需要连续 activationThreshold 次正价才激活治理（过滤瞬时抖动）
//   - 需要连续 deactivationThreshold 次零价才停用治理（防止震荡）
//   - 激活后 EMA 平滑计算治理强度 ∈ [0, 1]
//   - 非活跃状态返回强度 0（等效 NG），活跃状态返回 (0, 1]
type GovernanceIntensityTracker struct {
	mu                    sync.Mutex
	governor              *mcpgov.MCPGovernor
	active                bool
	consecutivePositive   int
	consecutiveZero       int
	activationThreshold   int     // 激活所需连续正价采样次数
	deactivationThreshold int     // 停用所需连续零价采样次数
	smoothedIntensity     float64 // EMA 平滑后的治理强度
	emaAlpha              float64 // EMA 系数（越大跟踪越快）
	referencePrice        int64   // 强度归一化参考价格（通常 = maxToken）
	sampleInterval        time.Duration
}

// NewGovernanceIntensityTracker 创建并启动治理强度跟踪器
// referencePrice 是强度归一化参考：ownPrice 达到此值时 intensity=1.0
// 建议设为 maxToken×5，使低压下治理软化、高压下全力保护
func NewGovernanceIntensityTracker(gov *mcpgov.MCPGovernor, referencePrice int64) *GovernanceIntensityTracker {
	t := &GovernanceIntensityTracker{
		governor:              gov,
		activationThreshold:   20, // 20×10ms = 200ms 持续正价才激活（避免启动冲击误触发）
		deactivationThreshold: 10, // 10×10ms = 100ms 持续零价才停用
		emaAlpha:              0.15, // 低 alpha → 平滑单次尖峰，减少抖动
		referencePrice:        referencePrice,
		sampleInterval:        10 * time.Millisecond,
	}
	go t.run()
	return t
}

func (t *GovernanceIntensityTracker) run() {
	ticker := time.NewTicker(t.sampleInterval)
	defer ticker.Stop()
	for range ticker.C {
		t.sample()
	}
}

func (t *GovernanceIntensityTracker) sample() {
	ownPrice := t.governor.GetOwnPrice()

	t.mu.Lock()
	defer t.mu.Unlock()

	if !t.active {
		if ownPrice > 0 {
			t.consecutivePositive++
			t.consecutiveZero = 0
			if t.consecutivePositive >= t.activationThreshold {
				t.active = true
				raw := float64(ownPrice) / float64(t.referencePrice)
				if raw > 1.0 {
					raw = 1.0
				}
				t.smoothedIntensity = raw
				log.Printf("[IntensityTracker] ACTIVATED: ownPrice=%d intensity=%.3f", ownPrice, raw)
			}
		} else {
			t.consecutivePositive = 0
		}
		return
	}

	// 活跃状态
	if ownPrice == 0 {
		t.consecutiveZero++
		t.consecutivePositive = 0
		if t.consecutiveZero >= t.deactivationThreshold {
			t.active = false
			t.smoothedIntensity = 0
			log.Printf("[IntensityTracker] DEACTIVATED after %d zero cycles", t.deactivationThreshold)
			return
		}
		// 活跃但当前零价 → 衰减强度
		t.smoothedIntensity *= (1 - t.emaAlpha)
	} else {
		t.consecutiveZero = 0
		t.consecutivePositive++
		raw := float64(ownPrice) / float64(t.referencePrice)
		if raw > 1.0 {
			raw = 1.0
		}
		t.smoothedIntensity = t.emaAlpha*raw + (1-t.emaAlpha)*t.smoothedIntensity
	}
}

// GetIntensity 获取当前治理强度 [0, 1]
func (t *GovernanceIntensityTracker) GetIntensity() float64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.smoothedIntensity
}

// IsActive 获取治理是否处于活跃状态
func (t *GovernanceIntensityTracker) IsActive() bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.active
}
