# 基于动态定价的 MCP 服务治理实验方案（v3）

> 目标：对比 无治理(NG) / 静态限流(SRL) / 动态定价+自适应档位(DP) 三种网关策略，
> 在多种负载模式与 heavy_ratio 下的表现，形成 CCF-B/C 投稿级别的完整实验证据链。
>
> v2 更新要点：
> - 新增消融实验（Exp5）验证各子模块独立贡献
> - 新增网关自身开销测量（Exp6）证明网关不是瓶颈
> - 新增参数敏感性热力图（Exp7）证明算法鲁棒性
> - 新增极端场景验证（Exp8）覆盖工业边界
> - 图表体系从 8 张扩展到 14 张，贯彻 Goodput 核心地位
> - 所有图表增加误差棒（3 次重复的标准差）和统计显著性标注
> - 时间序列图增加 Regime 背景底色标注
>
> v3 更新要点（整合「双模式评测理论」与「四大核心优化」）：
> - 🆕 引入**双模式实验框架**：无菌实验室模式（Exp1-7）+ 真实战场模式（Exp8）
> - 🆕 无菌模式固定轻量工具比例（80% calculate + 20% web_fetch），实现变量正交
> - 🆕 战场模式使用全部 7 个**真实** MCP 工具 + Trace-driven 分布
> - 🆕 负载生成器增加**防协调遗漏（Anti-Coordinated Omission）**设计
> - 🆕 全局随机种子固定（`random.seed(SEED)`），保证 100% 可复现
> - 🆕 新增 **P999 尾延迟**指标（战场模式核心度量）
> - 🆕 Exp8 重新定义为「真实战场验证」，使用真实大模型推理/向量化/沙盒

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
                        │    Gateway（多选一）      │
                        │  ① No Governance (NG)    │
                        │  ② Static Rate Limit(SRL)│
                        │  ③ Dynamic Pricing (DP)  │
                        │    + Regime Detector     │
                        │  ④ DP-NoRegime (消融)    │
                        │  ⑤ DP-NoWeight (消融)    │
                        │  ⑥ DP-FixedPrice(消融)   │
                        └────────────┬────────────┘
                                     │
                                     ▼
                        ┌─────────────────────────┐
              ┌────────→│     MCP Server (:8080)   │←── psutil 采样(500ms)
              │         │  无菌: calculator/web_fetch│     → CPU%, RSS MB
              │         │       /mock_heavy          │
              │         │  战场: +weather/text_format │
              │         │       /llm/embed/sandbox   │
              │         └─────────────────────────┘
              │
     监控协程 (psutil.Process)
     每 500ms 记录:
       - timestamp
       - cpu_percent
       - memory_rss_mb
       - gateway_cpu_percent (网关进程)
       - gateway_rss_mb (网关进程)
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

| 工具 | 文件 | 资源消耗 | 典型延迟 | 适用模式 |
|------|------|---------|:---:|:---:|
| `mock_heavy` | mock_heavy.py | CPU burn (可控 ms) + 内存 (可控 MB) | 1-60s | 无菌实验室 |
| `llm_reason` | llm_reasoner.py | 本地大模型推理，GPU 算力 + 显存密集 | 2-120s | 真实战场 |
| `doc_embedding` | doc_embedding.py | 调用本地模型 Embedding/摘要，GPU 密集 | 2-30s | 真实战场 |
| `python_sandbox` | python_sandbox.py | CPU 密集 + 长队列阻塞 | 5-60s | 真实战场 |

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
4. **🆕 双模式分治**：无菌模式仅用 3 个工具（变量正交），战场模式全部 7 个工具上阵（工业级验证）
5. **🆕 llm_reason 真实 GPU 消耗**：调用本地大模型（默认 qwen），流式推理产生持续 GPU 负载，TTFT 和 TPS 均可测量
6. **🆕 doc_embedding 真实 GPU 消耗**：调用本地 Embedding API（自动回退到 LLM 摘要模式），大文档分块产生持续 GPU 负载

---

## 二•五、🆕 双模式实验框架（Dual-Mode Testing Framework）

> **核心理念**：审稿人既要求实验变量**绝对可控**（物理学级别的纯净），又要求系统能应对**真实世界混沌流量**。
> 为同时满足这两种要求，我们将全部实验分为两种模式。

### 模式一：无菌实验室模式 (Sterile Laboratory Mode)

**适用实验**：Exp1 ~ Exp7（全部核心实验与参数扫描）

**工具选择（极简主义，仅 3 个工具）**：

| 类型 | 工具 | 固定比例 | 理由 |
|------|------|:---:|------|
| 轻量 | `calculate` | 80% | 纯 CPU 计算，延迟确定性最高（<1ms） |
| 轻量 | `web_fetch` (mock) | 20% | 引入 I/O 模拟，覆盖网络延迟场景（100-500ms） |
| 重量 | `mock_heavy` | 100% | 精确控制 CPU burn + 内存分配，消除硬件抖动 |

**🔑 核心优化——固定轻量工具比例，实现变量正交**：

在每次抽取轻量请求时，**不再均匀随机选取 4 个轻量工具**，而是**严格固定 80% calculate + 20% web_fetch**。

学术价值：
- 彻底消除轻量请求内部的资源消耗抖动（weather/text_format 的随机引入会带来不可控的背景噪声）
- 实验变量做到 **100% 正交（Orthogonal）**：只有 `heavy_ratio` 在变化，轻量部分完全锁定
- 误差棒（Error Bar）会极短，ANOVA 结果的统计显著性更强

### 模式二：真实战场模式 (Real Battlefield Mode)

**适用实验**：Exp8（真实战场验证）

**工具选择（全量混战，7 个真实工具全部上阵）**：

| 类型 | 工具 | 类内比例 | 真实场景对标 |
|------|------|:---:|------|
| 轻量 (85%) | `calculate` | 40% | 日常 chatbot 计算请求 |
| 轻量 | `get_weather` **(Mock)** | 20% | API 查询类请求（Mock：无需真实 API，延迟确定性高） |
| 轻量 | `text_format` | 25% | 文本处理/格式转换 |
| 轻量 | `web_fetch` **(Mock)** | 15% | 网络 I/O 密集请求（Mock：无需真实网络，延迟可控） |
| 重量 (15%) | `llm_reason` **(真实)** | 50% | **本地大模型推理**（GPU 密集，触发过载主力） |
| 重量 | `doc_embedding` **(真实)** | 30% | **RAG 向量化**（调用本地 Embedding 模型，GPU 密集） |
| 重量 | `python_sandbox` **(真实)** | 20% | Code Interpreter 沙盒执行（CPU 独占） |

**🔑 核心优化——Trace-driven Evaluation（基于真实轨迹驱动）**：

- 工具分布对齐工业界真实 AI Agent 的调用模式：轻量 85% + 重量 15%
- 重量工具中 RAG 向量化（doc_embedding）占主导，符合当前 LLM 应用以检索增强为核心的事实
- **重量级工具必须真实**：`llm_reason`（本地大模型推理）、`doc_embedding`（调用 Embedding 模型）、`python_sandbox`（真实子进程执行）
- **轻量级 Weather/WebFetch 仍用 Mock**：因为真实 API 的网络延迟不可控且与网关治理无关，Mock 提供确定性延迟
- "GPU 带不动"正是本文需要展示的核心痛点——无治理(NG)下系统崩溃，而 DP 网关精准拦截多余请求，保护后端

### 双模式对比汇总

| 维度 | 无菌实验室模式 | 真实战场模式 |
|------|-------------|------------|
| **适用实验** | Exp1 ~ Exp7 | Exp8 |
| **工具数量** | 3 个（calculate, web_fetch, mock_heavy） | 7 个（4 轻量 + 3 重量真实工具） |
| **轻量比例** | 固定 80:20 | Trace-driven 40:20:25:15（Weather/WebFetch 仍为 Mock） |
| **重量工具** | 仅 mock_heavy（精确可控） | llm_reason + doc_embedding + python_sandbox（全部真实 GPU/CPU 消耗） |
| **核心目标** | 变量正交、曲线平滑、统计显著 | 证明工业级落地能力、防雪崩 |
| **结果特征** | 误差棒极短、CDF 平滑 | 可能不平滑，但展示真实世界鲁棒性 |
| **关键指标** | P50/P95/P99, Goodput | **P999 尾延迟**、Error Rate、Recovery Time |

---

## 三、三个独立变量

| 变量 | 含义 | 取值 |
|------|------|------|
| `method` | 治理方法（网关策略） | NG / SRL / DP（含消融变体） |
| `heavy_ratio` | 重量级请求占比 | 0.1, 0.2, 0.3, 0.5（极端场景：0.7） |
| `high_budget_ratio` | 高预算请求占比 | 0.2, 0.5, 0.8 |

---

## 四、网关策略详细说明（含消融变体）

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

### ④⑤⑥ DP 消融变体（Ablation Study 专用）

消融实验需要拆解 DP 的三个核心模块，验证各子模块的独立贡献：

| 变体 | 简称 | 配置差异 | 禁用的模块 |
|------|------|---------|-----------|
| DP-Full | DP | 完整版（Regime + Weight + 动态调价） | 无 |
| DP-NoRegime | DP-NR | 固定使用 Steady 档位，不做 Regime 检测 | Regime Detector |
| DP-NoWeight | DP-NW | 所有工具 weight=1，不区分轻量/重量 | Tool Weights |
| DP-FixedPrice | DP-FP | 固定价格（取 Steady 档位的 LatencyThreshold 对应稳态价格），不做动态调价 | P-controller 动态调价 |

> **消融设计理念**：每个变体只禁用一个模块，保持其余不变，从而隔离每个模块的效果增量。

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
| `mock_heavy` | **10** | CPU burn + 内存分配，是轻量工具的 10 倍资源（无菌模式专用） |
| `llm_reason` | **10** | 本地大模型推理，GPU 密集（真实战场专用） |
| `doc_embedding` | **8** | 本地 Embedding，GPU 密集（真实战场专用） |
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
        "mock_heavy":      10,  // 无菌实验室模式
        "llm_reason":      10,  // 真实战场模式
        "doc_embedding":   8,   // 真实战场模式
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
| 🆕 **Latency P999** | 请求延迟第 99.9 百分位数 | **战场模式核心指标**：真实工具偶发极长阻塞，放大 DP 防雪崩价值 |
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

### C. 网关自身开销指标（系统论文必备，证明网关不是瓶颈）

| 指标 | 采集方式 | 用途 |
|------|---------|------|
| **网关处理延迟 (Gateway Latency μs)** | 请求进入网关到转发/拒绝的耗时（Go 侧 `time.Since` 打点） | 证明 DP 的计算开销（P-controller/Regime/权重）极低 |
| **网关 CPU 占用 (%)** | `psutil.Process(gateway_pid).cpu_percent()`，每 500ms | 证明 DP 在高并发下不会成为 CPU 瓶颈 |
| **网关内存 RSS (MB)** | `psutil.Process(gateway_pid).memory_info().rss / 1024²` | 证明滑动窗口/方差计算不会持续泄漏内存 |

### D. DP 专属指标（Regime Detector 评估）

| 指标 | 定义 | 用途 |
|------|------|------|
| **Regime 切换准确率** | 检测到的 regime 与注入的负载类型一致比例 | Detector 有效性 |
| **价格收敛时间** | 从过载到价格稳定的耗时 | 自适应速度 |
| **价格震荡幅度** | 稳态下价格的标准差 | 控制精度 |

---

## 九、八组实验设计

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

### Exp5：消融实验（Ablation Study，CCF-B/C 硬门槛）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | 4 变体：DP-Full / DP-NoRegime / DP-NoWeight / DP-FixedPrice |
| **主力指标** | Throughput, P95, Fairness Index, Goodput |
| **统计要求** | 3 次重复，每个指标标注均值 ± 标准差，组间做 ANOVA 检验 |
| **目标** | 拆解 DP 三大子模块（Regime Detector / Tool Weights / 动态调价）的独立贡献 |

**图表**：分组柱状图（带误差棒）

- X 轴：4 个 DP 变体 + SRL 基线
- Y 轴：4 个指标分别画
- **核心故事**：
  - DP-NoRegime vs DP-Full → Regime 切换的增量（负载自适应性）
  - DP-NoWeight vs DP-Full → 工具权重的增量（轻重分流能力）
  - DP-FixedPrice vs DP-Full → 动态调价的增量（弹性定价能力）
  - 每个模块都应有统计显著的正贡献（p < 0.05）

**预期结果**：
- 去掉 Regime → 在 Step/Sine 场景下 P95 退化最严重（无法适应负载变化）
- 去掉 Weight → Fairness Index 退化最严重（无法区分轻量/重量请求）
- 去掉动态调价 → Goodput 退化最严重（无法根据负载弹性调整准入门槛）

---

### Exp6：网关自身开销（Gateway Overhead，系统论文必备）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | 3 methods（NG / SRL / DP），并发数从 10 递增到 500 |
| **主力指标** | Gateway Processing Latency (P50/P95/P99 μs), Gateway CPU%, Gateway RSS MB |
| **目标** | 证明 DP 的计算开销（P-controller/Regime/权重乘法）极低，网关不是系统瓶颈 |

**图表**：

- 图 A：CDF 图 — 网关纯处理延迟分布（三条线：NG/SRL/DP），X 轴 μs 级别
- 图 B：折线图 — X 轴并发数 {10, 50, 100, 200, 500} → Y 轴 Gateway CPU%
- **预期结果**：DP 网关延迟 P99 < 100μs（相对于 mock_heavy 的 5000ms 可忽略），CPU 占用 < 5%

---

### Exp7：参数敏感性分析（鲁棒性验证）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | DP 的关键参数做网格扫描 |
| **目标** | 证明 DP 对参数不过度敏感，具备工程落地价值 |

**参数扫描范围**：

| 参数 | 扫描值 | 步数 |
|------|---------|:---:|
| PriceSensitivity | 5000, 8000, 10000, 15000, 20000 | 5 |
| LatencyThreshold | 200μs, 300μs, 400μs, 500μs, 600μs | 5 |

→ 5×5 = 25 个网格点，每个点的输出指标为 Goodput

**SRL 对比扫描**（证明 SRL 参数更难调）：

| 参数 | 扫描值 | 步数 |
|------|---------|:---:|
| QPS | 30, 40, 50, 60, 80 | 5 |
| BurstSize | 50, 75, 100, 125, 150 | 5 |

→ 5×5 = 25 个网格点，输出 Rejection Rate

**图表**：两张热力图并排

- 左图（DP）：X=PriceSensitivity, Y=LatencyThreshold, 颜色=Goodput
- 右图（SRL）：X=QPS, Y=BurstSize, 颜色=Rejection Rate
- **核心故事**：DP 热力图颜色在大范围内均匀偏暖（高 Goodput），说明参数不敏感；SRL 热力图冷热分明，说明需要精确调参才能达到可接受的拒绝率

---

### Exp8：🆕 真实战场验证（Real Battlefield Mode）

> **模式切换**：本实验使用**真实战场模式**，与 Exp1-7 的无菌实验室模式完全不同。
> 全部 7 个 MCP 工具上阵（包括真实的 llm_reason / doc_embedding / python_sandbox），
> 轻量工具中的 Weather/WebFetch 仍使用 Mock（无需真实 API，保证延迟确定性），
> 工具分布采用 Trace-driven 对齐工业真实 AI Agent 调用模式。
>
> **MCP Server 启动命令**：`python server.py --mode battlefield --port 8080`

| 项目 | 设置 |
|------|------|
| **模式** | 🆕 **真实战场模式**（非 mock，使用真实 MCP 工具） |
| **工具分布** | 轻量 85%（calculate 40% / weather(Mock) 20% / text_format 25% / web_fetch(Mock) 15%）<br>重量 15%（llm_reason(真实) 50% / doc_embedding(真实) 30% / python_sandbox(真实) 20%） |
| 场景 A | Poisson, **heavy_ratio=0.15**（Trace-driven 真实比例）, high_budget_ratio=0.5 |
| 场景 B | **Step 脉冲**, heavy_ratio=0.15, high_budget_ratio=0.5 |
| 变化 | 3 methods（NG / SRL / DP） |
| **主力指标** | 🆕 **P999 尾延迟**、Throughput、Error Rate、Recovery Time |
| **辅助指标** | P50/P95/P99、Rejection Rate、GPU 显存占用（如有） |
| **目标** | 证明 DP 不是只能跑 Mock 的玩具，而是能直接落地真实 AI Agent 场景的工业级网关 |

**为什么必须用真实工具？**

"GPU/CPU 带不动"正是本文的核心痛点。论文的故事链是：
1. 无治理(NG)下，真实的 llm_reason / doc_embedding / python_sandbox 并发涌入 → 后端 GPU 拥堵/OOM/超时崩溃
2. SRL 一刀切限流 → 大量轻量请求被误杀，P999 飙升到几十秒
3. **DP 动态涨价 → 精准拦截多余重量请求 → 后端始终在可承受范围内运行 → P999 被控制在合理水平**

**安全措施**（防止真实工具物理崩溃）：
- 推理引擎设置最大并发（如 vLLM `--max-num-seqs 16`），超出排队等待不会 OOM
- 发压机设置 `timeout=15s`，超时视为 Error
- 仅 Exp8 使用真实工具，Exp1-7 继续使用 mock（无菌模式）

**🆕 P999 尾延迟的核心价值**：

真实的 llm_reason 会产生持续 GPU 算力消耗（TTFT 可达数十秒），python_sandbox 会产生偶发极长 CPU 阻塞。P999 指标将把 DP "防雪崩"的价值放大到极致：
- NG：P999 可能达到 60s+（完全失控）
- SRL：P999 仍然很高（重请求堵死轻请求的队头阻塞）
- DP：P999 被有效控制（重量请求在网关层被动态价格拦截，轻量请求畅通无阻）

**图表**：分组柱状图 + P999 专项对比

- 左图（场景 A Poisson 稳态）：3 方法的 Throughput / Error Rate / **P999**
- 右图（场景 B Step 脉冲）：3 方法的 Recovery Time / P95 峰值 / **P999**
- **核心故事**：
  - NG 在真实重量工具冲击下完全崩溃（Error Rate > 50%，P999 失控）
  - SRL 大量误杀轻量请求（Rejection Rate 极高），P999 仍居高不下
  - **DP 精准拦截低预算重量请求，P999 比 NG 降低一个数量级，Error Rate 趋近于 0**

---

## 十、资源监控实现（含网关自身开销）

```python
# monitor.py — 在 Server 侧启动一个采样协程，同时监控后端 + 网关两个进程
import psutil, time, csv

def monitor_processes(backend_pid, gateway_pid, output_csv, interval=0.5):
    backend = psutil.Process(backend_pid)
    gateway = psutil.Process(gateway_pid)
    with open(output_csv, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'timestamp',
            'backend_cpu_percent', 'backend_memory_rss_mb',
            'gateway_cpu_percent', 'gateway_memory_rss_mb'
        ])
        while True:
            ts = time.time()
            b_cpu = backend.cpu_percent(interval=None)
            b_rss = backend.memory_info().rss / (1024 * 1024)
            g_cpu = gateway.cpu_percent(interval=None)
            g_rss = gateway.memory_info().rss / (1024 * 1024)
            writer.writerow([ts, b_cpu, round(b_rss, 2), g_cpu, round(g_rss, 2)])
            time.sleep(interval)
```

- 同时采集**后端进程**和**网关进程**的资源消耗
- 进程级采集，不受其他进程干扰
- 500ms 间隔，开销可忽略
- 输出 CSV，后续用 matplotlib 画图

### 网关处理延迟采集

在 Go 网关侧，额外记录每个请求在网关层的**纯处理延迟**（不含后端执行时间）：

```go
// 在 HandleToolCall 入口打点
gatewayStart := time.Now()
// ... 执行准入判断（价格计算、token 检查、regime 检测）
gatewayLatency := time.Since(gatewayStart)
// 记录到 gateway_latency_log（用于 Exp6 的 CDF 图）
```

---

## 十•五、🆕 负载生成器设计原则（Anti-Coordinated Omission）

> 无论网关写得多好，如果发压机写得不严谨，测出的数据全是废纸。

### 核心设计要求

**1. 固定全局随机种子（100% 可复现）**

```python
import random, numpy as np

SEED = 20260318
random.seed(SEED)
np.random.seed(SEED)
```

所有 3 次重复实验的请求序列（工具选择、预算分配、到达时间）完全一致，确保误差棒仅反映系统本身的抖动而非输入随机性。

**2. 防协调遗漏（Anti-Coordinated Omission）异步设计**

系统评测中最臭名昭著的陷阱是 **协调遗漏 (Coordinated Omission)**：当后端系统卡死时，基于多线程同步等待的发压机也会卡住不再发包，导致测出的延迟看起来"没那么糟"。

解决方案：使用 Python `asyncio` 全异步非阻塞发包：

```python
async def load_generator(target_qps, duration_sec, gateway_url, ...):
    async with aiohttp.ClientSession() as session:
        for i in range(total_requests):
            # 泊松到达过程：独立计算每个请求的到达时间
            inter_arrival_time = np.random.exponential(1.0 / target_qps)
            await asyncio.sleep(inter_arrival_time)

            # 创建异步任务，立即放入事件循环，绝不阻塞下一个请求的生成
            task = asyncio.create_task(trigger_request(session, ...))
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)
```

核心要点：
- 无论后端多卡，发压机都会严格按照设定的 QPS 持续发包
- 测出系统**最真实的极限延迟**（而非"看起来还行"的假延迟）
- 每个请求的延迟从 `asyncio.create_task` 前开始计时（含排队等待时间）

**3. 泊松到达过程（Poisson Arrival）**

使用指数分布的到达间隔而非固定间隔，更贴近真实世界的请求到达模式。

**4. 双模式工具选择器**

```python
class WorkloadConfig:
    STERILE = {  # 无菌实验室模式（Exp1-7）
        "light_tools": ["calculate", "web_fetch"],
        "light_weights": [0.8, 0.2],    # 固定 80:20 比例
        "heavy_tools": ["mock_heavy"],
        "heavy_weights": [1.0]
    }
    BATTLEFIELD = {  # 真实战场模式（Exp8）
        "light_tools": ["calculate", "get_weather", "text_format", "web_fetch"],
        "light_weights": [0.4, 0.2, 0.25, 0.15],     # Weather/WebFetch 仍用 Mock
        "heavy_tools": ["llm_reason", "doc_embedding", "python_sandbox"],
        "heavy_weights": [0.5, 0.3, 0.2]              # LLM推理占主导
    }
```

---

## 十一、可视化图表清单（14 张）

> **全局图表规范**（适配 CCF-B/C 评审要求）：
> - **配色一致性**：DP=蓝色(#2196F3), SRL=橙色(#FF9800), NG=红色(#F44336)
> - **消融变体**：DP-NR=浅蓝虚线, DP-NW=蓝色点线, DP-FP=蓝灰色
> - **字体**：Arial/Helvetica，标题 12pt，轴标签 10pt，图例 9pt
> - **误差棒**：所有柱状图/折线图标注 3 次重复的标准差
> - **统计显著性**：组间差异标注 * (p<0.05), ** (p<0.01), *** (p<0.001)
> - **自包含性**：每张图的标题/图例/标注应无需翻正文即可理解

以下是论文中需要的全部图表，按实验分组：

### 图表 1：全局摘要表 (Table)

参考样式：

| Strategy | Throughput | P50 (ms) | P95 (ms) | P99 (ms) | Reject % | Error % | **Goodput** | Hi-Budget Succ% |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| No Governance | 173.4±2.1 | 94±3 | 660±15 | 854±22 | 0.0±0.0 | 0.1±0.0 | **低** | 99.9±0.0 |
| Dynamic Pricing | 134.2±1.8 | 44±2 | 145±8 | 292±12 | 73.1±1.5 | 0.0±0.0 | **高** | 56.2±2.3 |
| Static Rate Limit | 174.6±3.0 | 98±4 | 406±18 | 504±20 | 34.9±2.0 | 0.1±0.0 | **中** | 64.9±1.8 |

**优化点**（相比 v1）：
- 所有数值标注 均值±标准差（95% 置信区间）
- **Goodput 列加粗/高亮**，作为全文核心指标的"C 位"
- 备注：DP 的 Reject% 高但 Error% 为 0，证明所有放行的请求都成功执行了

### 图表 2：公平性分析 — 按预算分组的成功率柱状图 (Exp3)

- **左图 (All-Phase)**：X 轴 = Budget 分组 {10, 50, 100}，Y 轴 = Success Rate (%)，三组柱子 = NG/DP/SRL
- **右图 (Overload-Phase)**：仅统计过载阶段的成功率
- **优化**：每组柱子上方标注**统计显著性**（如 DP vs SRL 用 * 标注 p<0.05）
- **核心故事**：过载时 DP 的 Budget=100 成功率远高于 Budget=10，而 NG/SRL 无此区分

### 图表 3：过载保护时间序列图 (Exp4)

- 三个子图并排：NG / DP / SRL
- 每个子图的 X 轴 = Time (s)，Y 轴 = Requests/sec
- 三个面积叠加：✅ Success (绿) / ⚠️ Rejected (橙) / ❌ Error (红)
- 子图标题显示统计汇总：`Succ 9879 (59%) Rej 6845 (41%) Err 157 (1%)`
- **优化**：标注 Recovery Time 的具体数值（如 DP: 1.2s, SRL: 3.8s, NG: N/A）
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

**优化**（采纳 Gemini 建议）：
- **背景底色标注 Regime 状态**：在 DP 的子图 2/3/4 中，用不同透明底色区分当前 Regime
  - 浅绿色底 = Steady，浅黄色底 = Periodic，浅红色底 = Bursty
  - 底色切换时刻用垂直虚线标注
- 标注关键事件："Step负载高阶段（t=10~20s）触发Bursty档位，P95延迟下降40%"

### 图表 5：heavy_ratio 敏感性曲线图 (Exp2)

- 图 A：X 轴 heavy_ratio {0.1, 0.2, 0.3, 0.5} → Y 轴 Throughput，三条线（带误差棒）
- 图 B：X 轴 heavy_ratio → Y 轴 Rejection Rate，三条线
- 图 C：X 轴 heavy_ratio → Y 轴 Avg CPU%，三条线
- **优化**：增加图 D — X 轴 heavy_ratio → Y 轴 **Goodput**（加粗最粗线条），突出 Goodput 的 C 位

### 图表 6：资源效率散点图 (Exp3)

- X 轴 = Avg CPU%，Y 轴 = Goodput
- 每个点 = 一个 (method, high_budget_ratio) 组合
- 证明：DP 在相同 CPU 消耗下 Goodput 最高

### 图表 7：CDF 延迟分布图 (Exp1)

- 3×3 小多图（3 负载模式 × 3 方法）
- 每个子图：X 轴 = Latency (ms), Y 轴 = CDF (%)
- **优化**：统一所有子图坐标轴范围，标注 **KS 检验 p 值**（证明 DP 与 NG/SRL 的延迟分布有显著差异）

### 图表 8：DP 价格曲线 vs CPU 利用率（双 Y 轴图，Sine 实验用）

- X 轴 = Time (s)
- 左 Y 轴 = ownprice，右 Y 轴 = CPU%
- **优化**：
  - 添加**背景底色标注 Regime 状态**（同图 4 的配色方案）
  - 标注"价格收敛阈值"和"稳态价格标准差"数值
  - 计算价格曲线与 CPU% 曲线的 Pearson 相关系数并标注

### 图表 9：消融实验对比图 (Exp5) — 🆕 核心新增

- **类型**：分组柱状图（带误差棒）
- **X 轴**：5 个变体 — DP-Full / DP-NoRegime / DP-NoWeight / DP-FixedPrice / SRL
- **Y 轴**：4 组子图分别为 Throughput / P95 / Fairness Index / Goodput
- **样式**：DP 变体用蓝色系渐变，SRL 用橙色作为参考基线
- **统计**：每对比较标注 ANOVA 或 t-test 的 p 值
- **核心故事**：
  - 证明 DP 的每个子模块（Regime/Weight/动态调价）都有统计显著的独立贡献
  - 回应评审"创新点是否堆砌"的质疑

### 图表 10：Regime 档位切换时间序列图 (Exp4) — 🆕 机制解释

- **类型**：双 Y 轴折线 + 阶梯线
- **X 轴**：时间（与图 4 共享）
- **主 Y 轴**：Regime 类型（阶梯线，Bursty=3 / Periodic=2 / Steady=1）
- **次 Y 轴**：P95 Latency（折线）+ ownprice（折线）
- **标注**：
  - 负载突变点（Step 负载的高/低阶段切换时刻）用垂直虚线标识
  - 档位切换时刻用圆点标记
  - 切换前后的 P95 变化百分比
- **核心故事**：可视化 DP 的核心机制——Regime Detector 如何感知负载变化并自适应切换参数档位

### 图表 11：细粒度公平性 CDF 图 (Exp3) — 🆕 深度公平性

- **类型**：2×2 子图矩阵（轻低 / 轻高 / 重低 / 重高 四种请求类型）
- **每个子图**：
  - X 轴 = 请求延迟 (ms)，Y 轴 = 累计通过率 CDF (%)
  - 3 条线 = NG / SRL / DP
  - 右上角标注 Jain's Fairness Index 数值
  - 标注 KS 检验 p 值（DP vs SRL 的分布差异显著性）
- **核心故事**：超越简单的通过率数值，用分布曲线证明 DP 的"精准分流"能力
  - 轻量请求：三种方法差异小（都能通过）
  - 重低请求：DP 明显右移（延迟高但少放行）vs NG 崩溃
  - 重高请求：DP 曲线接近轻量（高预算重量请求被优先放行）

### 图表 12：参数敏感性热力图 (Exp7) — 🆕 鲁棒性验证

- **两张热力图并排**：
  - 左图（DP）：X=PriceSensitivity {5000~20000}, Y=LatencyThreshold {200~600μs}, 颜色=Goodput
  - 右图（SRL）：X=QPS {30~80}, Y=BurstSize {50~150}, 颜色=Rejection Rate
- **颜色映射**：暖色=好（高 Goodput / 低 Rejection Rate），冷色=差
- **核心故事**：DP 热力图大面积暖色（参数不敏感），SRL 热力图冷热分明（需精确调参）

### 图表 13：🆕 真实战场验证对比图 (Exp8) — 工业级落地能力 + P999

- **两张子图**：
  - 左图（场景 A Poisson + 真实工具）：分组柱状图，X 轴 = {NG, SRL, DP}，Y 轴 = Throughput / Error Rate / **P999**
  - 右图（场景 B Step + 真实工具）：分组柱状图，X 轴 = {NG, SRL, DP}，Y 轴 = Recovery Time / P95 峰值 / **P999**
- **🆕 P999 柱子用特殊颜色高亮**（如深紫色），突出战场模式的核心指标
- **核心故事**：
  - 真实工具冲击下 NG 完全崩溃（P999 达到 60s+，Error Rate > 50%）
  - SRL 一刀切后 P999 仍居高不下（重请求堵死轻请求的队头阻塞）
  - **DP 精准拦截 → P999 降低一个数量级 → 工业级防雪崩能力**

### 图表 14：Goodput 分解堆叠图 (Exp3) — 🆕 核心故事强化

- **类型**：堆叠面积 + 折线（双 Y 轴）
- **X 轴**：high_budget_ratio {0.2, 0.5, 0.8}
- **主 Y 轴**（堆叠面积）：Goodput 分解
  - 轻量请求贡献（绿色）+ 重量请求贡献（蓝色）
- **次 Y 轴**（折线）：资源效率 = Goodput / Avg CPU%
- **三组（NG/SRL/DP）分别画**
- **核心故事**：DP "不是靠少做事降延迟，而是做对的事提升资源效率"——相同 CPU 消耗下，DP 的 Goodput 堆叠面积最大，且随 high_budget_ratio 增加效率线上升最快

### 图表汇总

| 编号 | 图表类型 | 对应实验 | 核心证据 | 优先级 |
|:---:|------|:---:|------|:---:|
| 图1 | 摘要表 (Table) | 全局 | 一览三方核心指标 + Goodput C 位 | P0 |
| 图2 | 柱状图 (按预算分组) | Exp3 | DP 预算公平性 | P0 |
| 图3 | 面积时间序列图 | Exp4 | 过载保护 (Succ/Rej/Err) | P0 |
| 图4 | 四合一时间序列 | Exp4 | 延迟+CPU+内存+Regime底色 | P0 |
| 图5 | 曲线图 (敏感性) | Exp2 | heavy_ratio 影响 + Goodput 线 | P0 |
| 图6 | 散点图 (效率) | Exp3 | Goodput/CPU 资源效率 | P0 |
| 图7 | CDF 小多图 | Exp1 | 全负载模式延迟分布 + KS 检验 | P0 |
| 图8 | 双 Y 轴曲线 | Sine | 价格自适应 + Regime 底色 | P0 |
| **图9** | **消融柱状图** | **Exp5** | **各子模块独立贡献** | **P0** |
| **图10** | **Regime 切换时序** | **Exp4** | **机制可视化** | **P0** |
| **图11** | **公平性 CDF 矩阵** | **Exp3** | **深度公平性分布** | **P1** |
| **图12** | **参数敏感性热力图** | **Exp7** | **鲁棒性/参数不敏感** | **P1** |
| **图13** | **🆕 真实战场柱状图 + P999** | **Exp8** | **工业级落地 + 防雪崩** | **P0** |
| **图14** | **Goodput 分解堆叠图** | **Exp3** | **"做对的事"核心故事** | **P1** |

> **优先级说明**：P0 = 论文核心图表（必须有），P1 = 加分项（拉开与普通论文的差距）

---

## 十二、实验矩阵汇总

| 实验 | 自变量 | 组合数 | 核心证据 | 优先级 |
|------|--------|:---:|---------|:---:|
| Exp1 负载模式 | 3 methods × 3 patterns | 9 | DP 全模式最优 | P0 |
| Exp2 heavy_ratio | 3 methods × 4 ratios | 12 | DP 优雅降级 | P0 |
| Exp3 预算公平性 | 3 methods × 3 budget_ratios | 9 | DP 经济模型生效 | P0 |
| Exp4 过载恢复 | 3 methods × 时间序列 | 3 | DP 恢复最快 | P0 |
| **Exp5 消融实验** | **4 DP变体 × 1 基线** | **5** | **各模块独立贡献** | **P0** |
| **Exp6 网关开销** | **3 methods × 5 并发级别** | **15** | **网关不是瓶颈** | **P0** |
| **Exp7 参数敏感性** | **DP 5×5 + SRL 5×5** | **50** | **参数鲁棒性** | **P1** |
| **Exp8 真实战场验证** | **3 methods × 2 场景（真实工具）** | **6** | **🆕 工业级落地 + P999** | **P0** |
| **合计** | | **109** | |

### 试验次数计算

- P0 实验（Exp1~Exp6 + Exp8）：60 组 × 3 次重复 = **180 次**
- P1 实验（Exp7 参数扫描）：50 组 × 1 次取值 = **50 次**
- **务必完成的最低限度**：P0 的 180 次 → 约 6-7 小时
- **完整版**：230 次 → 约 8 小时（可并行缩短）

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
>
> 🆕 **Exp8（真实战场模式）特别说明**：使用真实的 llm_reason / doc_embedding / python_sandbox（含大模型推理和Embedding），
> Server VM 需要配备 GPU（如 A4000/A10）或使用 CPU 推理引擎。推理引擎应设置最大并发（如 vLLM `--max-num-seqs 16`），
> 防止 OOM。发压机设置 `timeout=15s`。"GPU 带不动"正是 Exp8 需要展示的核心痛点。
> 轻量工具中的 Weather/WebFetch 仍使用 Mock（无需外部 API 依赖）。
> **MCP Server 启动**：`python server.py --mode battlefield --port 8080`

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

# 6. 启动 MCP Server（无菌实验室模式 Exp1-7）
python3 mcp_server/server.py --mode sterile --port 8080 &
# 或 真实战场模式 Exp8：
# python3 mcp_server/server.py --mode battlefield --port 8080 &

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

## 十四、论文 Story 线（完整证据链 v3）

> **问题**：现有 MCP 网关缺乏经济模型感知的服务治理，Static Rate Limiting 对所有请求一刀切。
>
> **方案**：基于动态定价 + Token 准入 + Load Regime Detector 的自适应治理框架（DP）。
>
> **评测方法论**：🆕 双模式实验框架——无菌实验室模式（Exp1-7，变量正交、统计严谨）+ 真实战场模式（Exp8，7个真实工具、P999尾延迟、工业级验证）。
>
> **证据链**（8 组实验 → 14 张图表 → 闭环论证）：
>
> | 论点 | 实验 | 图表 | 主力指标 | 资源辅证 |
> |------|------|------|---------|---------|
> | DP 在所有负载模式下均最优 | Exp1 | 图7 | Throughput, P95 (CDF + KS检验) | 各模式下 CPU/内存对比 |
> | DP 随重载比例增大降级最优雅 | Exp2 | 图5 | Throughput, Goodput vs heavy_ratio | CPU% vs heavy_ratio 曲线 |
> | DP 能基于预算做精准准入（核心亮点）| Exp3 | 图2, 图11, 图14 | 四组通过率, Fairness CDF, Goodput分解 | Goodput/CPU 效率散点图(图6) |
> | DP 过载恢复最快 | Exp4 | 图3, 图4, 图10 | Recovery Time, P95(t), Regime切换 | CPU%(t) + RSS(t) + Regime底色 |
> | **每个子模块都有独立贡献（消融）** | **Exp5** | **图9** | **Throughput/P95/Fairness/Goodput (ANOVA)** | **排除"堆砌创新"质疑** |
> | **网关不是系统瓶颈** | **Exp6** | **图9附表** | **Gateway Latency P99 < 100μs** | **Gateway CPU < 5%** |
> | **算法对参数不敏感** | **Exp7** | **图12** | **Goodput 热力图均匀** | **对比 SRL 的冷热分明** |
> | **极端场景下仍稳定 + 工业级落地** | **Exp8** | **图13** | **🆕 P999 尾延迟 / Error Rate（真实工具）** | **真实战场 + 防雪崩** |
>
> **论文图表与实验的闭环映射**：
> - Exp1 → 图7（CDF 小多图）
> - Exp2 → 图1（摘要表）+ 图5（敏感性曲线）
> - Exp3 → 图2（柱状图）+ 图6（散点图）+ 图11（CDF矩阵）+ 图14（Goodput分解）
> - Exp4 → 图3（面积堆叠）+ 图4（四合一）+ 图8（价格vs CPU）+ 图10（Regime切换）
> - Exp5 → 图9（消融柱状图）
> - Exp7 → 图12（热力图）
> - Exp8 → 图13（真实战场柱状图 + P999）
>
> **一句话总结**：DP 不是靠"少做事"降延迟，而是靠**"做对的事"**实现更高的资源效率和经济公平性——无菌实验室模式（Exp1-7）以变量正交的纯净环境证明算法优越性，真实战场模式（Exp8）以 7 个真实 MCP 工具和 P999 尾延迟证明工业级落地能力；每个子模块都有统计显著的独立贡献，算法在宽参数范围内稳健。
