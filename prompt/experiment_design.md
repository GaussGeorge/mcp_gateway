# Load Regime Detector + Parameter Profile 实验方案

## 1. 问题背景

MCP 服务治理引擎通过动态定价实现过载保护。核心控制回路为：

```
排队延迟(gapLatency) → 过载检测 → P-controller 调价 → 令牌准入 → 负载削减
```

**痛点**：单一参数配置无法适应所有负载模型。突发流量需要快速反应（高增益）、周期性流量需要避免震荡（高阻尼）、稳态流量需要精准收敛（均衡增益）。

## 2. 核心思路

### 2.1 架构：Load Regime Detector + Parameter Profile

```
                          ┌────────────────────┐
  gapLatency ────────────►│  Regime Detector   │
  (每个 tick 采样)         │  (统计特征识别)     │
                          └────────┬───────────┘
                                   │ targetRegime
                          ┌────────▼───────────┐
                          │  Profile Switcher   │
                          │  (冷却时间保护)      │
                          └────────┬───────────┘
                                   │ 热切换参数
                   ┌───────────────┼───────────────┐
                   ▼               ▼               ▼
             ┌──────────┐   ┌──────────┐   ┌──────────┐
             │  Bursty   │   │ Periodic │   │  Steady  │
             │  Profile  │   │  Profile │   │  Profile │
             └──────────┘   └──────────┘   └──────────┘
```

### 2.2 Regime Detector 状态机

检测器基于两个统计信号进行分类：

| 信号 | 计算方式 | 含义 |
|------|---------|------|
| `variance` | 最近 N 个 gapLatency 的样本方差 | 反映延迟波动程度 |
| `delta` | `|gapLatency_now - gapLatency_prev|` | 反映单步突变幅度 |

**分类规则（优先级从高到低）**：

```
if delta ≥ regimeSpikeThreshold   → bursty    (突发流量：瞬间剧变)
if variance ≥ regimeVarianceHigh  → periodic  (周期流量：持续波动)
if variance ≤ regimeVarianceLow   → steady    (稳态流量：低波动)
otherwise                         → 保持当前状态 (滞环效应)
```

**安全措施**：
- 冷却时间 (`profileSwitchCooldown`)：两次切换之间的最小间隔，防止抖动
- 最小样本数：窗口内至少 3 个样本才开始检测

### 2.3 三套参数档位（参考值）

| 参数 | Bursty (突发) | Periodic (周期) | Steady (稳态) | 含义 |
|------|:---:|:---:|:---:|------|
| `PriceStep` | 200 | 100 | 150 | 涨价步长 |
| `PriceDecayStep` | 20 | 10 | 15 | 降价步长 |
| `PriceSensitivity` | 8000 | 15000 | 10000 | P-controller 增益分母 |
| `LatencyThreshold` | 300μs | 500μs | 400μs | 过载判定延迟阈值 |
| `DecayRate` | 0.9 | 0.75 | 0.8 | 指数衰减系数 |
| `PriceUpdateRate` | 5ms | 20ms | 10ms | 采样/调价周期 |
| `MaxToken` | 200 | 200 | 200 | 令牌桶容量上限 |

**设计理念**：
- **Bursty**：低阈值 + 高步长 + 短周期 → 快速感知、快速涨价、快速恢复
- **Periodic**：高阈值 + 低步长 + 长周期 → 避免跟随波纹震荡
- **Steady**：均衡配置 → 精准趋近目标，避免不必要的价格波动

### 2.4 Regime Detector 推荐参数

| 参数 | 默认值 | 含义 |
|------|:---:|------|
| `regimeWindow` | 20 | 方差计算的滑动窗口大小 |
| `regimeVarianceLow` | 0.02 ms² | 低于此方差 → steady |
| `regimeVarianceHigh` | 0.20 ms² | 高于此方差 → periodic |
| `regimeSpikeThreshold` | 0.80 ms | 单步 delta 超此值 → bursty |
| `profileSwitchCooldown` | 200ms | 切换冷却时间 |

## 3. 实验设计

### 3.1 对照组设计

| 组别 | 描述 | 配置 |
|------|------|------|
| **Baseline** | 无治理 | `loadShedding=false` |
| **Static-Burst** | 静态参数（Burst Profile 固定） | `enableAdaptiveProfile=false`，手动设 Burst 参数 |
| **Static-Periodic** | 静态参数（Periodic Profile 固定） | `enableAdaptiveProfile=false`，手动设 Periodic 参数 |
| **Static-Steady** | 静态参数（Steady Profile 固定） | `enableAdaptiveProfile=false`，手动设 Steady 参数 |
| **Adaptive** | 本方案（Regime Detector + Profile Switch） | `enableAdaptiveProfile=true` |

### 3.2 负载模型（自变量）

| 编号 | 模型 | Go 实现 | 特征 |
|------|------|---------|------|
| L1 | **Step (突发)** | N 个 goroutine 同时 `go callTool()` | 瞬间并发爆发 |
| L2 | **Sine (周期)** | 并发数按 `A·sin(2πt/T)+B` 变化 | 持续波动 |
| L3 | **Poisson (稳态)** | 按泊松分布 `rate=λ` 发请求 | 稳定但随机 |
| L4 | **Mixed** | L1→L3→L2 依次切换（每段 10s） | 验证自适应切换 |

**负载强度**：
- light：`heavy_ratio = 0`（全轻量工具）
- mixed：`heavy_ratio = 0.3`（30% 重量工具）
- heavy：`heavy_ratio = 0.7`（70% 重量工具）

### 3.3 评价指标（因变量）

| 指标 | 计算方式 | 理想目标 |
|------|---------|---------|
| **P99 延迟** | 所有成功请求延迟的第 99 百分位 | 尽可能低 |
| **吞吐量** | 成功请求数 / 运行时间 (req/s) | 尽可能高 |
| **拒绝率** | 被拒请求数 / 总请求数 | 过载时高，空闲时 0 |
| **价格收敛时间** | 从过载到价格稳定的耗时 | 越短越好 |
| **价格震荡幅度** | 稳态下价格的标准差 | 越低越好 |
| **Regime 切换准确率** | 检测到的 regime 与注入的负载类型一致的比例 | 接近 100% |

### 3.4 实验矩阵

```
5 组配置 × 4 种负载模型 × 3 种负载强度 = 60 组实验
每组重复 3 次，共 180 次试验
```

### 3.5 实验步骤

```
Step 1: 启动 MCP Server（配置对应组别的参数）
Step 2: 预热 2s（建立基线）
Step 3: 注入负载（按对应模型运行 30s）
Step 4: 收集指标（价格时间线、请求结果、延迟采样）
Step 5: 输出结果 CSV / JSON
```

## 4. 代码实现位置

| 文件 | 新增/修改内容 |
|------|------|
| `mcp_governor.go` | 新增 `Profile` struct；新增 Regime Detector 字段到 `MCPGovernor` |
| `overloadDetection.go` | 新增 `initAdaptiveProfiles()`、`maybeApplyAdaptiveProfile()`、`applyProfileOptions()`；在 `queuingCheck()` 中集成 |
| `mcp_init.go` | 新增 Regime Detector + Profile 的 options 解析；调用 `initAdaptiveProfiles()` |

## 5. 使用方式

```go
gov := NewMCPGovernor("server-1", callMap, map[string]interface{}{
    "loadShedding":         true,
    "pinpointQueuing":      true,
    "priceStrategy":        "expdecay",
    "enableAdaptiveProfile": true,
    // 可选：覆盖三套 profile 中的任意参数
    "burstyProfile": map[string]interface{}{
        "PriceStep": int64(250),
    },
})
```
