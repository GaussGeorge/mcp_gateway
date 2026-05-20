package plangate

import "math"

// DiscountFunc 折扣函数类型：输入步骤数K和系数alpha，输出折扣因子 ∈ (0, 1]
//
// 折扣因子越小，对高步数K的会话保护力度越强（价格越低）。
// 评审要求：对比 Linear/Quadratic/Exponential/Logarithmic 四种函数族，
// 消融证明 Quadratic (K²) 是此场景下的帕累托最优解。
//
// 理论基础（最优停止框架）：
//   网关面临序贯博弈：放行步骤K → 可能完成任务回收价值V；拒绝 → 确定损失沉没成本C_K。
//   最优折扣的 "保护力度" d/dK[1/δ(K)] 应随K线性增长（二次方），
//   恒定增长（线性）保护不足，指数增长过于激进导致饥饿。
type DiscountFunc func(K float64, alpha float64) float64

// DiscountFuncName 折扣函数名称（配置和消融实验用）
type DiscountFuncName string

const (
	DiscountQuadratic   DiscountFuncName = "quadratic"   // 1/(1+K²α) — 默认，保护力度线性增长
	DiscountLinear      DiscountFuncName = "linear"       // 1/(1+Kα) — 保护力度恒定
	DiscountExponential DiscountFuncName = "exponential"  // 1/(1+e^(Kα)-1) — 保护力度指数增长
	DiscountLogarithmic DiscountFuncName = "logarithmic"  // 1/(1+α·ln(1+K)) — 保护力度递减
)

// QuadraticDiscount K² 二次方折扣（默认）
// >>> Table 3: d(k) = 1/(1+k²·α), Σ k·d(k) ≤ H_{N-1}/α, E[W]=O(ln N)
// >>> Eq.(10): E[W_d] ≈ c·q·Σ k·δ(k) = O(ln N)
// >>> Claim 1: 浪费比 vs Per-req = Θ(N²/ln N)
//
// δ(K) = 1 / (1 + K²·α)
// 性质：d/dK[1/δ] = 2Kα — 保护力度随步数线性增长
// K=3, α=0.5 → δ=0.18 (82%折扣)
func QuadraticDiscount(K float64, alpha float64) float64 {
	return 1.0 / (1.0 + K*K*alpha)
}

// LinearDiscount 线性折扣
// >>> Table 3: d(k) = 1/(1+k·α), Σ k·d(k) ≤ (N-1)/α, E[W]=O(N)
// δ(K) = 1 / (1 + K·α)
// 性质：d/dK[1/δ] = α — 保护力度恒定，对高步数会话保护不足
// K=3, α=0.5 → δ=0.40 (60%折扣)
func LinearDiscount(K float64, alpha float64) float64 {
	return 1.0 / (1.0 + K*alpha)
}

// ExponentialDiscount 指数折扣
// >>> Table 3: d(k) = 1/e^{kα} (未列入 Table 3 主表，消融实验 Exp8 对比用)
// δ(K) = 1 / (1 + e^(Kα) - 1) = 1 / e^(Kα)
// 性质：d/dK[1/δ] = α·e^(Kα) — 保护力度指数增长，过于激进可能饥饿新会话
// K=3, α=0.5 → δ=0.22 (78%折扣)
func ExponentialDiscount(K float64, alpha float64) float64 {
	v := math.Exp(K * alpha)
	return 1.0 / v
}

// LogarithmicDiscount 对数折扣
// >>> Table 3: d(k) = 1/(1+α·ln(1+k)), E[W] 介于 O(N) 和 O(ln N) 之间
// δ(K) = 1 / (1 + α·ln(1+K))
// 性质：d/dK[1/δ] = α/(1+K) — 保护力度递减，高步数时折扣增长放缓
// K=3, α=0.5 → δ=0.58 (42%折扣)
func LogarithmicDiscount(K float64, alpha float64) float64 {
	return 1.0 / (1.0 + alpha*math.Log(1.0+K))
}

// discountFuncRegistry 折扣函数注册表
var discountFuncRegistry = map[DiscountFuncName]DiscountFunc{
	DiscountQuadratic:   QuadraticDiscount,
	DiscountLinear:      LinearDiscount,
	DiscountExponential: ExponentialDiscount,
	DiscountLogarithmic: LogarithmicDiscount,
}

// GetDiscountFunc 根据名称获取折扣函数，未找到时返回默认二次方
func GetDiscountFunc(name DiscountFuncName) DiscountFunc {
	if fn, ok := discountFuncRegistry[name]; ok {
		return fn
	}
	return QuadraticDiscount
}
