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

## 五、请求模型与工具权重

### 5.1 价格如何形成？

`ownprice` 是动态变化的，由 P-controller 根据排队延迟实时计算。
空闲时 `ownprice=0`，过载时 `ownprice` 自动上涨。

但如果轻/重工具共享同一个 `ownprice`，就无法区分“轻量工具占用 1ms CPU”和“重量工具占用 5s CPU + 50MB 内存”。
因此引入 **工具权重乘数 (Tool Weight)**：

```
客户端实际需支付的价格 = ownprice × toolWeight
```

### 5.2 工具权重配置

| 工具 | weight | 理由 |
|------|:---:|------|
| `calculate` | 1 | 计算型，资源开销可忽略 |
| `get_weather` (mock) | 1 | I/O mock，仅 sleep |
| `text_format` | 1 | 纯 CPU 但极快 |
| `web_fetch` (mock) | 2 | I/O 稍重 |
| `mock_heavy` | **10** | CPU burn + 内存分配，是轻量工具的 10 倍资源 |
| `doc_embedding` | **8** | 内存密集 |
| `python_sandbox` | **10** | CPU 密集 + 长队列 |

### 5.3 价格与准入的 2×2 矩阵

假设过载时 `ownprice = 8`：

```
                    低预算(budget=10)         高预算(budget=100)
  轻量请求          实际价格=8×1=8              实际价格=8×1=8
  (weight=1)        10≥8 → 通过 ✅          100≥8 → 通过 ✅

  重量请求          实际价格=8×10=80            实际价格=8×10=80
  (weight=10)       10<80 → 拒绝 ❌          100≥80 → 通过 ✅
```

**核心洞察**：工具权重使得 DP 在过载时自然形成“保护轻量请求、筛选重量请求、优先放行高预算”的三层准入。空闲时 `ownprice=0`，所有请求免费通过。

### 5.4 代码用法

```go
gov := NewMCPGovernor("server-1", callMap, map[string]interface{}{
    "loadShedding":    true,
    "pinpointQueuing": true,
    "toolWeights": map[string]int64{
        "mock_heavy":      10,
        "doc_embedding":   8,
        "python_sandbox":  10,
        // 轻量工具不需配置，默认 weight=1
    },
})
```

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
| **Rejection Rate (%)** | 被治理层拒绝的请求占比（tokens < price → 主动拒绝） | 衡量治理的“精准度”（拒的是不是该拒的） |
| **Error Rate (%)** | 后端执行失败的请求占比（超时/崩溃/OOM） | 衡量治理的“保护力”（没拒的是不是都能成功） |
| **Fairness Index** | Jain's Fairness Index，按预算分组计算 | 证明 DP 的经济公平性 |
| **Goodput** | 加权吞吐 = Σ(通过请求 × budget 权重) | 衡量经济效率——同样的资源下谁创造更多价值 |
| **Hi-Budget Success Rate (%)** | 高预算请求的成功率 | DP 核心卖点：高预算优先通过 |
| **Recovery Time** | 从过载到 P95 恢复到基线 1.5 倍以内的时间 | Step 实验专用 |

> **Rejection vs Error 的区别**：
> - **Rejection (拒绝)**：治理层主动拒绝，请求**未到达后端**，是“健康的保护行为”
> - **Error (错误)**：请求到达了后端，但执行**失败**（超时、crash、OOM、后端报错）
> - 理想的治理：**Rejection 高、Error 低** → 说明治理层有效保护了后端
> - 最差情况：**Rejection 低、Error 高** → 治理层没拤住，后端被压崩

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
| **主力指标** | Throughput, P95, Rejection Rate, Error Rate |
| **资源指标** | 每种模式下 Avg CPU%, Peak CPU%, Peak RSS |
| **目标** | 证明 DP 在所有负载模式下均表现最优 |

**图表**：3×3 小多图（3 负载 × 3 方法），每个小图含 CDF 延迟曲线

---

### Exp2：heavy_ratio 敏感性分析（论文核心表）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, high_budget_ratio=0.5 |
| 变化 | 3 methods × 4 heavy_ratio {0.1, 0.2, 0.3, 0.5} |
| **主力指标** | Throughput, P95, P99, Rejection Rate, Error Rate, Goodput |
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
| **主力指标** | P95 延迟(t), Rejection Rate(t), Error Rate(t), Recovery Time |
| **资源指标** | CPU%(t), RSS MB(t) |
| **目标** | DP 恢复最快、尖峰最小、Error 最低 |

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

## 十一、可视化图表清单

以下是论文中需要的全部图表，按实验分组：

### 图表 1：全局摘要表 (Table)

参考样式（对应附件表格图）：

| Strategy | Throughput | P50 (ms) | P95 (ms) | P99 (ms) | Reject % | Error % | Hi-Budget Succ% |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| No Governance | 173.4 | 94 | 660 | 854 | 0.0 | 0.1 | 99.9 |
| Dynamic Pricing | 134.2 | 44 | 145 | 292 | 73.1 | 0.0 | 56.2 |
| Static Rate Limit | 174.6 | 98 | 406 | 504 | 34.9 | 0.1 | 64.9 |

说明：DP 的 Reject% 高但 Error% 为 0，证明所有放行的请求都成功执行了。

### 图表 2：公平性分析 — 按预算分组的成功率柱状图 (Exp3)

参考样式（对应附件 Fairness Analysis 图）：

- **左图 (All-Phase)**：X 轴 = Budget 分组 {10, 50, 100}，Y 轴 = Success Rate (%)，三条柱子 = NG/DP/SRL
- **右图 (Overload-Phase)**：仅统计过载阶段的成功率
- **核心故事**：过载时 DP 的 Budget=100 成功率远高于 Budget=10，而 NG/SRL 无此区分

### 图表 3：过载保护时间序列图 (Exp4)

参考样式（对应附件 Overload Protection 图）：

- 三个子图并排：NG / DP / SRL
- 每个子图的 X 轴 = Time (s)，Y 轴 = Requests/sec
- 三个面积叠加：✅ Success (绿) / ⚠️ Rejected (橙) / ❌ Error (红)
- 子图标题显示统计汇总：`Succ 9879 (59%) Rej 6845 (41%) Err 157 (1%)`
- **核心故事**：
  - NG：红色区域 (Error) 在过载期迅速扩大 → 后端被压崩
  - SRL：橙色区域 (Rejected) 占据大部分 → 盲拒，绿色区域极小
  - DP：过载后橙色区域适度扩大，绿色区域保持可观，红色区域极小 → **精准拒绝，保护后端**

### 图表 4：四合一时间序列图 (Exp4 深入版)

四行子图叠加，共享 X 轴（时间）：

```
子图1: QPS 输入曲线 (Step 脉冲)                        ← 实验条件
子图2: P95 延迟 (三条线: NG/SRL/DP)                  ← 主力指标
子图3: CPU% (三条线)                               ← 资源解释
子图4: Memory RSS MB (三条线)                      ← OOM 危险
```

### 图表 5：heavy_ratio 敏感性曲线图 (Exp2)

- 图 A：X 轴 heavy_ratio {0.1, 0.2, 0.3, 0.5} → Y 轴 Throughput，三条线
- 图 B：X 轴 heavy_ratio → Y 轴 Rejection Rate，三条线
- 图 C：X 轴 heavy_ratio → Y 轴 Avg CPU%，三条线

### 图表 6：资源效率散点图 (Exp3)

- X 轴 = Avg CPU%，Y 轴 = Goodput
- 每个点 = 一个 (method, high_budget_ratio) 组合
- 证明：DP 在相同 CPU 消耗下 Goodput 最高

### 图表 7：CDF 延迟分布图 (Exp1)

- 3×3 小多图（3 负载模式 × 3 方法）
- 每个子图：X 轴 = Latency (ms), Y 轴 = CDF (%)

### 图表 8：DP 价格曲线 vs CPU 利用率（双 Y 轴图，Sine 实验用）

- X 轴 = Time (s)
- 左 Y 轴 = ownprice，右 Y 轴 = CPU%
- 证明价格曲线与负载正弦波高度相关

### 图表汇总

| 编号 | 图表类型 | 对应实验 | 核心证据 |
|:---:|------|:---:|------|
| 图1 | 摘要表 (Table) | 全局 | 一督三方核心指标 |
| 图2 | 柱状图 (按预算分组) | Exp3 | DP 预算公平性 |
| 图3 | 面积时间序列图 | Exp4 | 过载保护 (Succ/Rej/Err) |
| 图4 | 四合一时间序列 | Exp4 | 延迟+CPU+内存的综合故事 |
| 图5 | 曲线图 (敏感性) | Exp2 | heavy_ratio 影响 |
| 图6 | 散点图 (效率) | Exp3 | Goodput/CPU 资源效率 |
| 图7 | CDF 小多图 | Exp1 | 全负载模式延迟分布 |
| 图8 | 双 Y 轴曲线 | Sine | 价格自适应能力 |

---

## 十二、实验矩阵汇总

| 实验 | 自变量 | 组合数 | 核心证据 |
|------|--------|:---:|---------|
| Exp1 负载模式 | 3 methods × 3 patterns | 9 | DP 全模式最优 |
| Exp2 heavy_ratio | 3 methods × 4 ratios | 12 | DP 优雅降级 |
| Exp3 预算公平性 | 3 methods × 3 budget_ratios | 9 | DP 经济模型生效 |
| Exp4 过载恢复 | 3 methods × 时间序列 | 3 | DP 恢复最快 |
| **合计** | | **33** | |

每组重复 3 次 → **共 99 次试验**

---

## 十三、部署方案：本地 vs 云端

### 13.1 结论先行

**推荐云端两台 VM 部署**，原因：

| 维度 | 本地 | 云端 |
|------|------|------|
| 论文可信度 | "作者笔记本" → 审稿人质疑 | "ecs.c7.xlarge, 4vCPU/8GB" → 可复现 |
| CPU 稳定性 | 桌面 OS 后台进程干扰、睿频波动 | 独占 vCPU，可关闭 turbo boost |
| 负载生成器干扰 | 与 Server 共享 CPU → psutil 测量被污染 | 分离到独立 VM → 干净测量 |
| 并行加速 | 只能串行跑 99 次 | 可同时开 3 组 Server VM 并行 |
| 环境一致性 | 每次重启可能不同 | 镜像固化，随时销毁重建 |

> **但如果工具全是 mock 的，本地跑的"相对差异"也是有效的。**
> 云端的核心价值是：(1) 论文"实验环境"一节更专业，(2) 排除 CPU 抖动的绝对值偏差。
> 如果经费有限，**先本地调通全部流程，最终上云跑正式数据**即可。

### 13.2 架构：两台 VM

```
┌──────────────────────┐          JSON-RPC          ┌──────────────────────────────┐
│     VM-1 (Client)    │ ─────────(内网)──────────► │        VM-2 (Server)         │
│                      │                            │                              │
│  Load Generator (Py) │                            │  Gateway (Go)                │
│  - Step / Sine /     │                            │    ├─ NG / SRL / DP          │
│    Poisson           │                            │    └─ Regime Detector        │
│  - heavy_ratio       │                            │          │                   │
│  - budget            │                            │          ▼                   │
│                      │                            │  MCP Server (Py, :8080)      │
│  结果收集 & 画图     │                            │    ├─ calculator             │
│  - matplotlib        │                            │    ├─ mock_weather           │
│  - pandas            │                            │    ├─ mock_heavy             │
│                      │                            │    └─ ...                    │
│                      │                            │                              │
│                      │                            │  psutil Monitor (Py)         │
│                      │                            │    → cpu%, rss_mb → CSV      │
└──────────────────────┘                            └──────────────────────────────┘
        2 vCPU / 4GB                                        4 vCPU / 8GB
```

**为什么不是三台？**
- Gateway (Go) 和 MCP Server (Python) 是同一进程内的中间件调用关系，拆开反而引入额外网络延迟
- psutil 必须与 MCP Server 在同一台机器上才能采集进程级指标
- Load Generator 独立出去是为了防止它自身的 CPU 消耗污染 Server 侧的 psutil 测量

### 13.3 云服务器配置清单

以阿里云为例（腾讯云/AWS 选等价机型即可）：

#### VM-2：Server（核心，决定实验质量）

| 项 | 推荐 | 说明 |
|-------|------|------|
| **机型** | ecs.c7.xlarge | 计算优化型，CPU 性能稳定 |
| **vCPU** | 4 | `mock_heavy(cpu_burn_ms=5000)` 在 4 核下 CPU% 表现明显；2 核太容易打满看不出差异 |
| **内存** | 8 GB | 10 个并发 `mock_heavy(memory_mb=50)` = 500MB，8GB 留足余量不触发 swap |
| **磁盘** | 40GB ESSD (PL0) | 写 CSV 日志即可，无高 IOPS 需求 |
| **OS** | Ubuntu 22.04 LTS | Go 1.21+ / Python 3.10+ 官方支持 |
| **网络** | 与 VM-1 同 VPC、同可用区 | 内网延迟 < 0.1ms，排除网络变量 |

#### VM-1：Client（辅助，要求不高）

| 项 | 推荐 | 说明 |
|-------|------|------|
| **机型** | ecs.c7.large | 够用即可 |
| **vCPU** | 2 | Load Generator 是单线程 asyncio，2 核足够 |
| **内存** | 4 GB | 仅发请求 + 收集结果 |
| **磁盘** | 40GB ESSD (PL0) | 存结果 CSV + 画图脚本 |
| **OS** | Ubuntu 22.04 LTS | 与 Server 一致 |

#### 费用估算（按需计费）

| VM | 单价 (约) | 99 次试验 × 2min ≈ 4h | 含调试 |
|----|:---:|:---:|:---:|
| VM-2 (4C8G) | ¥0.8/h | ¥3.2 | ¥20 (含调试5天) |
| VM-1 (2C4G) | ¥0.4/h | ¥1.6 | ¥10 |
| **合计** | | | **¥30 左右** |

> 实际费用极低。如果用抢占式实例 (Spot)，还可再降 50-70%。

### 13.4 环境搭建步骤（Server VM-2）

```bash
# 1. 系统准备
sudo apt update && sudo apt install -y build-essential python3-pip python3-venv

# 2. Go 环境
wget https://go.dev/dl/go1.22.4.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.22.4.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
source ~/.bashrc

# 3. Python 依赖
python3 -m venv ~/mcp-env
source ~/mcp-env/bin/activate
pip install psutil matplotlib pandas

# 4. 关闭 CPU turbo boost（消除频率波动，提高可复现性）
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo
# 或 AMD:
# echo 0 | sudo tee /sys/devices/system/cpu/cpufreq/boost

# 5. 编译 Gateway
cd ~/mcp-governance
go build ./...

# 6. 启动 MCP Server
python3 mcp_server.py --port 8080 &

# 7. 启动 psutil 监控
python3 monitor.py --pid $(pgrep -f mcp_server) --output /tmp/resource.csv &
```

### 13.5 环境搭建步骤（Client VM-1）

```bash
# 1. Python 环境
sudo apt update && sudo apt install -y python3-pip python3-venv
python3 -m venv ~/mcp-env
source ~/mcp-env/bin/activate
pip install aiohttp matplotlib pandas numpy

# 2. 运行实验（以 Exp1-Step-DP 为例）
python3 load_generator.py \
    --server http://<VM-2内网IP>:8080 \
    --pattern step \
    --method DP \
    --heavy_ratio 0.3 \
    --high_budget_ratio 0.5 \
    --duration 120 \
    --output results/exp1_step_dp.csv
```

### 13.6 可复现性清单（论文"实验环境"一节直接用）

论文建议写法：

> **实验环境**：实验在阿里云上进行。服务端使用 ecs.c7.xlarge 实例（4 vCPU Intel Xeon Ice Lake, 8GB RAM, Ubuntu 22.04），关闭 Turbo Boost 以保证 CPU 频率稳定。负载生成器部署在同 VPC 同可用区的 ecs.c7.large 实例（2 vCPU, 4GB RAM）上，内网延迟 < 0.1ms。Go 1.22, Python 3.10。每组实验重复 3 次取均值，误差棒表示标准差。

### 13.7 并行加速方案（可选）

如果 99 次串行跑太慢（约 3-4 小时），可以：

```
VM-2a (Server) ←── VM-1 ──→ VM-2b (Server)
                    │
                    └──────→ VM-2c (Server)
```

- 3 台 Server VM 并行跑 3 个 Exp（Exp1/Exp2/Exp3 同时跑）
- VM-1 用多线程或 tmux 同时向 3 台 Server 发请求
- 额外费用 ≈ ¥60，但时间缩短到 1-1.5 小时
- **注意**：每台 Server VM 的配置必须完全一致（同机型、同区域）

### 13.8 本地调试 → 云端正式数据的推荐流程

```
阶段 1: 本地开发（不花钱）
  ├─ 调通全部 7 个工具
  ├─ 调通 Load Generator 的 3 种负载模式
  ├─ 调通 NG / SRL / DP 三种网关
  ├─ 跑 2-3 个 smoke test，验证指标采集正确
  └─ 画出初版图表，确认图表格式

阶段 2: 云端正式实验（花 ¥30）
  ├─ 开两台 VM，部署环境
  ├─ 关闭 Turbo Boost，确认 CPU 频率稳定
  ├─ 跑完 99 次试验 + 收集结果
  ├─ 将结果 CSV scp 回本地
  └─ 本地画最终版论文图表

阶段 3: 补充实验（如审稿人要求）
  ├─ 保存 VM 镜像（¥0.1/GB·月）
  └─ 随时从镜像重建，跑补充试验
```

---

## 附录 A、代码实现位置

| 文件 | 新增/修改内容 |
|------|------|
| `mcp_governor.go` | `Profile` struct（含 MaxToken）；MCPGovernor 增加 Regime Detector 字段 |
| `overloadDetection.go` | `initAdaptiveProfiles()` / `applyProfileOptions()` / `maybeApplyAdaptiveProfile()` / `calculateVariance()`；集成到 `queuingCheck()` |
| `mcp_init.go` | Regime Detector + Profile 的 options 解析（含三套 profile options 覆盖） |

---

## 附录 B、使用方式

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
