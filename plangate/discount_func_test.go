package plangate

import (
	"math"
	"testing"
)

func TestDiscountFunctions(t *testing.T) {
	// 测试所有折扣函数在 K=0 时返回 1.0（无折扣）
	alpha := 0.5
	for name, fn := range discountFuncRegistry {
		result := fn(0, alpha)
		if math.Abs(result-1.0) > 1e-9 {
			t.Errorf("%s: K=0 should return 1.0, got %f", name, result)
		}
	}
}

func TestQuadraticDiscount(t *testing.T) {
	cases := []struct {
		K, alpha float64
		expected float64
	}{
		{0, 0.5, 1.0},
		{1, 0.5, 1.0 / 1.5},             // 1/(1+0.5) = 0.667
		{3, 0.5, 1.0 / (1 + 9*0.5)},     // 1/5.5 = 0.182
		{5, 0.5, 1.0 / (1 + 25*0.5)},    // 1/13.5 = 0.074
		{10, 0.5, 1.0 / (1 + 100*0.5)},  // 1/51 = 0.0196
	}
	for _, c := range cases {
		got := QuadraticDiscount(c.K, c.alpha)
		if math.Abs(got-c.expected) > 1e-6 {
			t.Errorf("Quadratic(K=%v, α=%v) = %v, want %v", c.K, c.alpha, got, c.expected)
		}
	}
}

func TestLinearDiscount(t *testing.T) {
	cases := []struct {
		K, alpha float64
		expected float64
	}{
		{0, 0.5, 1.0},
		{1, 0.5, 1.0 / 1.5},            // 0.667
		{3, 0.5, 1.0 / 2.5},            // 0.400
		{5, 0.5, 1.0 / 3.5},            // 0.286
		{10, 0.5, 1.0 / 6.0},           // 0.167
	}
	for _, c := range cases {
		got := LinearDiscount(c.K, c.alpha)
		if math.Abs(got-c.expected) > 1e-6 {
			t.Errorf("Linear(K=%v, α=%v) = %v, want %v", c.K, c.alpha, got, c.expected)
		}
	}
}

func TestExponentialDiscount(t *testing.T) {
	cases := []struct {
		K, alpha float64
		expected float64
	}{
		{0, 0.5, 1.0},
		{1, 0.5, 1.0 / math.Exp(0.5)},
		{3, 0.5, 1.0 / math.Exp(1.5)},
	}
	for _, c := range cases {
		got := ExponentialDiscount(c.K, c.alpha)
		if math.Abs(got-c.expected) > 1e-6 {
			t.Errorf("Exponential(K=%v, α=%v) = %v, want %v", c.K, c.alpha, got, c.expected)
		}
	}
}

func TestLogarithmicDiscount(t *testing.T) {
	cases := []struct {
		K, alpha float64
		expected float64
	}{
		{0, 0.5, 1.0},
		{1, 0.5, 1.0 / (1.0 + 0.5*math.Log(2.0))},
		{3, 0.5, 1.0 / (1.0 + 0.5*math.Log(4.0))},
	}
	for _, c := range cases {
		got := LogarithmicDiscount(c.K, c.alpha)
		if math.Abs(got-c.expected) > 1e-6 {
			t.Errorf("Logarithmic(K=%v, α=%v) = %v, want %v", c.K, c.alpha, got, c.expected)
		}
	}
}

func TestDiscountMonotonicity(t *testing.T) {
	// 所有折扣函数应随 K 增大而单调递减
	alpha := 0.5
	for name, fn := range discountFuncRegistry {
		prev := fn(0, alpha)
		for K := 1.0; K <= 10.0; K++ {
			curr := fn(K, alpha)
			if curr > prev+1e-12 {
				t.Errorf("%s: not monotonically decreasing at K=%v (prev=%v, curr=%v)",
					name, K, prev, curr)
			}
			prev = curr
		}
	}
}

func TestDiscountProtectionRate(t *testing.T) {
	// 验证不同函数的折扣幅度特性：
	// Quadratic 和 Exponential 在高 K 时给最大折扣（最低值），
	// Logarithmic 折扣最弱（最高值），Linear 居中。
	alpha := 0.5

	// K=1 时 Quadratic == Linear (都是 1/(1+α))，从 K≥2 开始分化
	for _, K := range []float64{2, 3, 5, 10} {
		qDiscount := QuadraticDiscount(K, alpha)
		lDiscount := LinearDiscount(K, alpha)
		logDiscount := LogarithmicDiscount(K, alpha)

		// Linear 应严格小于 Logarithmic
		if lDiscount >= logDiscount {
			t.Errorf("K=%v: expected Linear(%v) < Logarithmic(%v)", K, lDiscount, logDiscount)
		}
		// Quadratic 应严格小于 Linear（K≥2时）
		if qDiscount >= lDiscount {
			t.Errorf("K=%v: expected Quadratic(%v) < Linear(%v)", K, qDiscount, lDiscount)
		}
	}

	// 验证所有函数在 K=0 时相等（无折扣）
	for name, fn := range discountFuncRegistry {
		if v := fn(0, alpha); v != 1.0 {
			t.Errorf("%s(0, %v) = %v, want 1.0", name, alpha, v)
		}
	}
}

func TestGetDiscountFunc(t *testing.T) {
	// 已知名称
	fn := GetDiscountFunc(DiscountLinear)
	if fn == nil {
		t.Fatal("GetDiscountFunc(linear) returned nil")
	}
	if fn(3, 0.5) != LinearDiscount(3, 0.5) {
		t.Error("GetDiscountFunc(linear) returned wrong function")
	}

	// 未知名称 → 默认 quadratic
	fn = GetDiscountFunc("unknown")
	if fn(3, 0.5) != QuadraticDiscount(3, 0.5) {
		t.Error("GetDiscountFunc(unknown) should fallback to quadratic")
	}
}

// BenchmarkDiscountFunctions 四种折扣函数性能基准测试
func BenchmarkQuadraticDiscount(b *testing.B) {
	for i := 0; i < b.N; i++ {
		QuadraticDiscount(float64(i%10), 0.5)
	}
}

func BenchmarkLinearDiscount(b *testing.B) {
	for i := 0; i < b.N; i++ {
		LinearDiscount(float64(i%10), 0.5)
	}
}

func BenchmarkExponentialDiscount(b *testing.B) {
	for i := 0; i < b.N; i++ {
		ExponentialDiscount(float64(i%10), 0.5)
	}
}

func BenchmarkLogarithmicDiscount(b *testing.B) {
	for i := 0; i < b.N; i++ {
		LogarithmicDiscount(float64(i%10), 0.5)
	}
}
