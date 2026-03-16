# 基于动态定价的 MCP 服务治理实验方案

> 目标：对比 无治理(NG) / 静态限流(SRL) / 动态定价+自适应档位(DP) 三种网关策略，
> 在多种负载模式与 heavy_ratio 下的表现，形成 CCF-B/C 投稿级别的完整实验证据链。

---

## 一、实验架构

```
                        ┌─────────────────────────┐
                        │     Load Generator      │
                        │  (Step / Sine / Poisson) │
                        │  heavy_ratio + budget    │
                        └────────────┬────────────┘
                                     │ JSON-RPC
                                     ▼
                        ┌─────────────────────────┐
                        │    Gateway（三选一）      │
                        │  ① No Governance (NG)    │
                        │  ② Static Rate Limit(SRL)│
                        │  ③ Dynamic Pricing (DP)  │
                        │    + Regime Detector     │
                        └────────────┬────────────┘
                                     │
                                     ▼
                        ┌─────────────────────────┐
              ┌────────→│     MCP Server (:8080)   │←── psutil 采样(500ms)
              │         │  轻量: calculator/weather │     → CPU%, RSS MB
              │         │  重量: mock_heavy         │
              │         └─────────────────────────┘
              │
     监控协程 (psutil.Process)
     每 500ms 记录:
       - timestamp
       - cpu_percent
       - memory_rss_mb
```

---

## 二、MCP 工具矩阵

### 轻量级工具（Mouse Flow，延迟 < 200ms）

| 工具 | 文件 | 说明 | 典型延迟 |
|------|------|------|:---:|
| `calculate` | calculator.py | 精确数学运算 (加减乘除、sqrt、factorial) | < 1ms |
| `get_weather` (mock) | mock_weather.py | 模拟天气查询 (sleep 50-200ms) | 50-200ms |
| `web_fetch` (mock) | mock_web_fetch.py | 模拟网络 I/O (sleep 100-500ms) | 100-500ms |
| `text_format` | text_formatter.py | JSON/regex/Base64 文本处理 | < 5ms |

### 重量级工具（Elephant Flow，延迟 > 1s，可控资源消耗）

| 工具 | 文件 | 资源消耗 | 典型延迟 |
|------|------|---------|:---:|
| `mock_heavy` | mock_heavy.py | CPU burn (可控 ms) + 内存 (可控 MB) | 1-60s |
| `doc_embedding` | doc_embedding.py | 内存密集 (最高 1000MB) | 2-30s |
| `python_sandbox` | python_sandbox.py | CPU 密集 + 长队列阻塞 | 5-60s |

> **`mock_heavy` 是基准工具**：通过参数 `cpu_burn_ms` 和 `memory_mb` 精确控制资源消耗，
> 消除硬件抖动差异，使实验结果可复现。推荐实验中重量请求统一使用 `mock_heavy(cpu_burn_ms=5000, memory_mb=50)`。

### 工具是否够用？

**够用。** 理由：
1. **轻/重二分法清晰**：4 个轻量工具（几乎零资源） vs 3 个重量工具（可控高资源），构成清晰的 2×2 matrix
2. **mock_heavy 可精确控制**：不依赖外部 API，CPU 和内存消耗可参数化，适合论文的可复现性要求
3. **覆盖三种真实场景**：
   - `calculator` → 类 chatbot 轻量工具调用
   - `doc_embedding` → 类 RAG 知识库检索
   - `python_sandbox` → 类 Code Interpreter 执行

---

## 三、三个独立变量

| 变量 | 含义 | 取值 |
|------|------|------|
| `method` | 治理方法（网关策略） | NG / SRL / DP |
| `heavy_ratio` | 重量级请求占比 | 0.1, 0.2, 0.3, 0.5 |
| `high_budget_ratio` | 高预算请求占比 | 0.2, 0.5, 0.8 |

---

## 四、三种网关策略详细说明

### ① No Governance (NG) — 无治理基线

```
所有请求直接透传，不做任何准入控制。
```

- 配置：`loadShedding=false, rateLimiting=false`
- 行为：来多少放多少，CPU/内存随负载线性增长，直到系统崩溃
- 作用：证明"不治理会怎样"

### ② Static Rate Limit (SRL) — 静态限流基线

```
固定 QPS 上限，超出直接拒绝，不区分请求类型/预算。
```

- 配置：`rateLimiting=true, loadShedding=false, QPS=N, BurstSize=M`
- 行为：一刀切限流，轻量和重量请求被同等拒绝，无预算感知
- 作用：代表现有 MCP 生态中最常见的限流方式

### ③ Dynamic Pricing (DP) — 动态定价 + 自适应档位

```
排队延迟 → Regime Detector → 自适应选择参数档位 → P-controller 动态调价 → 令牌准入
```

- 配置：`loadShedding=true, pinpointQueuing=true, priceStrategy="expdecay", enableAdaptiveProfile=true`
- 行为：根据实时排队延迟动态调整工具调用价格，**高预算请求优先通过，低预算重量请求优先被拒**
- 核心优势：经济模型感知 — 在同等资源约束下，最大化加权吞吐（Goodput）

---

## 五、请求模型（2×2 矩阵）

```
                    低预算(budget=10)         高预算(budget=100)
  轻量请求          cost≈1, 几乎必过          cost≈1, 几乎必过
  重量请求          cost≈50, 可能被拒 ❌       cost≈50, 优先通过 ✓
```

- **轻量请求**：随机调 `calculator` / `mock_weather` / `text_format`，cost ≈ 1
- **重量请求**：统一调 `mock_heavy(cpu_burn_ms=5000, memory_mb=50)`，cost ≈ 50

**核心洞察**：DP 方法的差异化准入在「重量 × 低预算」这个象限体现最明显 — SRL 和 NG 无法区分这四种请求类型。

---

## 六、Load Regime Detector + Parameter Profile（DP 核心机制）

### 6.1 架构

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

### 6.2 Regime Detector 状态机

检测器基于两个统计信号进行分类：

| 信号 | 计算方式 | 含义 |
|------|---------|------|
| `variance` | 最近 N 个 gapLatency 的样本方差 | 反映延迟波动程度 |
| `delta` | \|gapLatency_now - gapLatency_prev\| | 反映单步突变幅度 |

**分类规则（优先级从高到低）**：

```
if delta ≥ regimeSpikeThreshold   → bursty    (突发流量：瞬间剧变)
if variance ≥ regimeVarianceHigh  → periodic  (周期流量：持续波动)
if variance ≤ regimeVarianceLow   → steady    (稳态流量：低波动)
otherwise                         → 保持当前状态 (滞环效应，避免频繁切换)
```

**安全措施**：
- 冷却时间 (`profileSwitchCooldown`)：两次切换之间的最小间隔，防止抖动
- 最小样本数：窗口内至少 3 个样本才开始检测

### 6.3 三套参数档位（参考值）

| 参数 | Bursty (突发) | Periodic (周期) | Steady (稳态) | 含义 |
|------|:---:|:---:|:---:|------|
| `PriceStep` | 200 | 100 | 150 | 涨价步长（Kp 增益的一部分） |
| `PriceDecayStep` | 20 | 10 | 15 | 降价步长 |
| `PriceSensitivity` | 8000 | 15000 | 10000 | P-controller 增益分母 |
| `LatencyThreshold` | 300μs | 500μs | 400μs | 过载判定延迟阈值 |
| `DecayRate` | 0.9 | 0.75 | 0.8 | 指数衰减系数 |
| `PriceUpdateRate` | 5ms | 20ms | 10ms | 采样/调价周期 |
| `MaxToken` | 200 | 200 | 200 | 令牌桶容量上限 |

**设计理念**：
- **Bursty**：低阈值 + 高步长 + 短周期 → 快速感知、快速涨价、快速恢复
- **Periodic**：高阈值 + 低步长 + 长周期 → 高阻尼，避免跟随波纹震荡
- **Steady**：均衡配置 → 精准趋近目标，避免不必要的价格波动

### 6.4 Regime Detector 推荐参数

| 参数 | 默认值 | 含义 |
|------|:---:|------|
| `regimeWindow` | 20 | 方差计算的滑动窗口大小 |
| `regimeVarianceLow` | 0.02 ms² | 低于此方差 → steady |
| `regimeVarianceHigh` | 0.20 ms² | 高于此方差 → periodic |
| `regimeSpikeThreshold` | 0.80 ms | 单步 delta 超此值 → bursty |
| `profileSwitchCooldown` | 200ms | 切换冷却时间 |

---

## 七、三种负载模式 × heavy_ratio 推荐

### 1. Step（阶梯脉冲）— 测反应速度与恢复能力

```
QPS
 │        ┌────────┐
 │        │ burst  │
 │────────┘        └────────
 └──────────────────────────→ t
```

| heavy_ratio | 实验目的 |
|:---:|---------|
| 0.1 | 轻负载脉冲：NG 也能扛，SRL 误杀轻量，DP 几乎无感 |
| **0.3（主力）** | 混合脉冲：最能展现三种方法的差异化反应 |
| 0.5 | 重载脉冲：NG 崩溃，SRL 盲拒 50%，DP 精准淘汰低预算重量 |

**观测重点**：burst 时刻的延迟尖峰大小、恢复时间、CPU/内存尖峰高度

**DP Regime Detector 预期行为**：
- 空闲期 → steady 档位
- burst 到来 → delta 剧增 → 切换 bursty 档位（5ms 更新、200 步长快速涨价）
- burst 结束 → variance 下降 → 切回 steady 档位
- **关键证据**：恢复时间 DP < SRL < NG

---

### 2. Sine（正弦波）— 测自适应能力

```
QPS
 │    ╱╲      ╱╲
 │  ╱    ╲  ╱    ╲
 │╱        ╲╱        ╲
 └──────────────────────→ t
```

| heavy_ratio | 实验目的 |
|:---:|---------|
| **0.2** | 正常波动：SRL 在波峰误杀、波谷浪费容量 |
| **0.4** | 严重波动：DP 的价格曲线应与负载正弦波高度相关 |

**观测重点**：DP 价格曲线 vs CPU 利用率曲线的相关性（双 Y 轴图）

**DP Regime Detector 预期行为**：
- 持续波动 → variance 维持在中高区间 → periodic 档位（20ms 更新、100 步长、低增益）
- **核心故事**：periodic 档位的长采样周期使 DP 不会追着每个波峰涨价，避免震荡

---

### 3. Poisson（泊松分布）— 稳态性能，出论文主表

```
QPS
 │  ╷╷ ╷  ╷╷╷ ╷ ╷╷  ╷╷╷╷  ╷   (random arrivals)
 └──────────────────────────→ t
```

| heavy_ratio | 实验目的 |
|:---:|---------|
| 0.1 | 轻量主导（chatbot 日常场景） |
| 0.2 | 典型混合负载 |
| **0.3（default）** | 中等比例，最通用 |
| 0.5 | 重载主导（极端压力） |

**观测重点**：full sweep 出核心对比表（吞吐、延迟、拒绝率、公平性、资源效率）

**DP Regime Detector 预期行为**：
- 低波动 → variance 始终很低 → steady 档位（10ms 更新、150 步长、均衡增益）
- **核心故事**：steady 档位提供最精准的价格收敛，震荡幅度最小

---

## 八、全部评估指标体系

### A. 主力指标（论文核心表/核心图）

| 指标 | 定义 | 用途 |
|------|------|------|
| **Throughput** | 单位时间成功处理的请求数 (req/s) | 衡量系统的基本处理能力 |
| **Latency P50 / P95 / P99** | 请求延迟百分位数 | 尾部延迟是治理质量的核心体现 |
| **Rejection Rate** | 被拒请求占比（总体 + 按类型/预算分组） | 衡量治理的"精准度" |
| **Fairness Index** | Jain's Fairness Index，按预算分组计算 | 证明 DP 的经济公平性 |
| **Goodput** | 加权吞吐 = Σ(通过请求 × budget 权重) | 衡量经济效率——同样的资源下谁创造更多价值 |
| **Recovery Time** | 从过载到 P95 恢复到基线 1.5 倍以内的时间 | Step 实验专用 |

### B. 辅助资源指标（解释性图表）

| 指标 | 采集方式 | 用途 |
|------|---------|------|
| **后端 CPU 利用率 (%)** | `psutil.Process(pid).cpu_percent()`，每 500ms 采样 | 展示治理层对后端负载的"削峰"效果 |
| **后端内存 RSS (MB)** | `psutil.Process(pid).memory_info().rss / 1024²`，每 500ms | 展示 DP 防止 memory burst / OOM 的能力 |
| **CPU 峰值 (Peak CPU%)** | 实验期间 CPU% 的 max 值 | Poisson sweep 表格中的汇总列 |
| **内存峰值 (Peak RSS MB)** | 实验期间 RSS 的 max 值 | 同上 |
| **资源效率 (Goodput / Avg CPU%)** | 每 1% CPU 利用率创造的加权吞吐 | 证明 DP 不是靠"少做事"降延迟，而是"做对的事" |

### C. DP 专属指标（Regime Detector 评估）

| 指标 | 定义 | 用途 |
|------|------|------|
| **Regime 切换准确率** | 检测到的 regime 与注入的负载类型一致比例 | Detector 有效性 |
| **价格收敛时间** | 从过载到价格稳定的耗时 | 自适应速度 |
| **价格震荡幅度** | 稳态下价格的标准差 | 控制精度 |

---

## 九、四组实验设计

### Exp1：负载模式对比（全局鸟瞰）

| 项目 | 设置 |
|------|------|
| 固定 | heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | 3 methods × 3 load patterns (Step/Sine/Poisson) |
| **主力指标** | Throughput, P95, Rejection Rate |
| **资源指标** | 每种模式下 Avg CPU%, Peak CPU%, Peak RSS |
| **目标** | 证明 DP 在所有负载模式下均表现最优 |

**图表**：3×3 小多图（3 负载 × 3 方法），每个小图含 CDF 延迟曲线

---

### Exp2：heavy_ratio 敏感性分析（论文核心表）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, high_budget_ratio=0.5 |
| 变化 | 3 methods × 4 heavy_ratio {0.1, 0.2, 0.3, 0.5} |
| **主力指标** | Throughput, P95, P99, Rejection Rate, Goodput |
| **资源指标** | Avg CPU%, Peak CPU%, Peak RSS MB |
| **目标** | 随着重量比例增大，DP 降级最优雅，资源利用最高效 |

**图表**：

- 图 A（主力）：X 轴 heavy_ratio → Y 轴 Throughput/Rejection Rate，三条线
- 图 B（辅助）：X 轴 heavy_ratio → Y 轴 Avg CPU%，三条线。讲的故事：
  - NG：CPU 随 heavy_ratio 线性飙升，0.5 时 100% 崩溃
  - SRL：CPU 被压低（因为盲拒了很多请求）
  - **DP：CPU 略高于 SRL（因为放行了更多高价值请求），但远低于 NG → 资源效率最优**

---

### Exp3：预算公平性分析（差异化亮点，DP 核心卖点）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, heavy_ratio=0.3 |
| 变化 | 3 methods × 3 high_budget_ratio {0.2, 0.5, 0.8} |
| **主力指标** | 四组通过率（轻低/轻高/重低/重高）, Fairness Index, Goodput |
| **资源指标** | Avg CPU%, Peak RSS MB |
| **目标** | DP 能根据预算做精准分流，SRL/NG 做不到 |

**图表**：

- 图 A（主力）：分组柱状图 — X 轴 {NG, SRL, DP}，四组柱子 = 四种请求类型的通过率
- 图 B（辅助）：散点图 — X 轴 Avg CPU% → Y 轴 Goodput，每个点标注 method+ratio，证明 **DP 在相同资源消耗下 Goodput 最高**

---

### Exp4：过载恢复（时间序列，论文亮点图）

| 项目 | 设置 |
|------|------|
| 固定 | Step, heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | 3 methods 的时间序列对比 |
| **主力指标** | P95 延迟(t), Rejection Rate(t), Recovery Time |
| **资源指标** | CPU%(t), RSS MB(t) |
| **目标** | DP 恢复最快、尖峰最小 |

**图表（整张图的核心亮点）**：四行子图叠加，共享 X 轴（时间）

```
子图1: QPS 输入曲线 (Step 脉冲)              ← 实验条件
子图2: P95 延迟 (三条线: NG/SRL/DP)          ← 主力指标
子图3: CPU% (三条线)                         ← 解释延迟差异
子图4: Memory RSS MB (三条线)                ← 解释 OOM/内存压力
```

这张四合一时间序列图的 Story：
1. **t=burst**：QPS 突增
2. **NG**：CPU 100% → 内存飙升 → P95 失控 → 最终超时崩溃
3. **SRL**：CPU ~60%（盲拒了很多） → 内存平稳 → P95 可控但拒绝率高
4. **DP**：CPU ~75%（精准拒绝低价值重量请求） → 内存平稳 → P95 最低 → **恢复最快**

---

## 十、资源监控实现（极简方案）

```python
# monitor.py — 在 load generator 侧启动一个采样协程
import psutil, time, csv

def monitor_process(pid, output_csv, interval=0.5):
    proc = psutil.Process(pid)
    with open(output_csv, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'cpu_percent', 'memory_rss_mb'])
        while True:
            cpu = proc.cpu_percent(interval=None)
            rss = proc.memory_info().rss / (1024 * 1024)
            writer.writerow([time.time(), cpu, round(rss, 2)])
            time.sleep(interval)
```

- 进程级采集，不受其他进程干扰
- 500ms 间隔，开销可忽略
- 输出 CSV，后续用 matplotlib 画图

---

## 十一、实验矩阵汇总

| 实验 | 自变量 | 组合数 | 核心证据 |
|------|--------|:---:|---------|
| Exp1 负载模式 | 3 methods × 3 patterns | 9 | DP 全模式最优 |
| Exp2 heavy_ratio | 3 methods × 4 ratios | 12 | DP 优雅降级 |
| Exp3 预算公平性 | 3 methods × 3 budget_ratios | 9 | DP 经济模型生效 |
| Exp4 过载恢复 | 3 methods × 时间序列 | 3 | DP 恢复最快 |
| **合计** | | **33** | |

每组重复 3 次 → **共 99 次试验**

---

## 十二、代码实现位置

| 文件 | 新增/修改内容 |
|------|------|
| `mcp_governor.go` | `Profile` struct（含 MaxToken）；MCPGovernor 增加 Regime Detector 字段 |
| `overloadDetection.go` | `initAdaptiveProfiles()` / `applyProfileOptions()` / `maybeApplyAdaptiveProfile()` / `calculateVariance()`；集成到 `queuingCheck()` |
| `mcp_init.go` | Regime Detector + Profile 的 options 解析（含三套 profile options 覆盖） |

---

## 十三、使用方式

```go
gov := NewMCPGovernor("server-1", callMap, map[string]interface{}{
    "loadShedding":          true,
    "pinpointQueuing":       true,
    "priceStrategy":         "expdecay",
    "enableAdaptiveProfile": true,
    // 可选：覆盖任意 profile 参数
    "burstyProfile": map[string]interface{}{
        "PriceStep": int64(250),
        "MaxToken":  int64(300),
    },
})
```

---

## 十四、论文 Story 线（完整证据链）

> **问题**：现有 MCP 网关缺乏经济模型感知的服务治理，Static Rate Limiting 对所有请求一刀切。
>
> **方案**：基于动态定价 + Token 准入 + Load Regime Detector 的自适应治理框架（DP）。
>
> **证据链**：
>
> | 论点 | 实验 | 主力指标 | 资源辅证 |
> |------|------|---------|---------|
> | DP 在所有负载模式下均最优 | Exp1 | Throughput, P95 | 各模式下 CPU/内存对比 |
> | DP 随重载比例增大降级最优雅 | Exp2 | Throughput, Goodput vs heavy_ratio | CPU% vs heavy_ratio 曲线 |
> | DP 能基于预算做精准准入（核心亮点）| Exp3 | 四组通过率, Fairness Index | Goodput/CPU 效率散点图 |
> | DP 过载恢复最快 | Exp4 | Recovery Time, P95(t) | CPU%(t) + RSS(t) 时间序列 |
>
> **一句话总结资源指标的角色**：它们不是用来"比谁用得少"，而是证明 **DP 在更高的资源利用率下依然维持低延迟和高公平性** — 即"资源效率"而非"资源节约"。
