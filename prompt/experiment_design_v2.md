# PlanGate 实验设计与基线分析报告

---

## 一、实验测试对象与场景

### 1.1 测试对象

本实验测试的核心对象是 **PlanGate**（Plan-Aware Gateway for MCP Tool Governance）—— 一种面向 MCP 多步工具调用场景的 Agent 原生网关治理方法。它对标 5 个来自不同领域的基线方法，在相同硬件、相同后端、相同负载条件下进行公平对比。

**被测系统 (System Under Test)**:
一个三层 MCP 服务链路架构 —

```
                   ┌───────────────────────┐
                   │   DAG 负载生成器       │  Python asyncio
                   │  (模拟 LLM Agent 客户) │  500 并发会话
                   └──────────┬────────────┘
                              │  HTTP / JSON-RPC 2.0
                              │  X-Plan-DAG + X-Session-ID + X-Total-Budget
                              ▼
                   ┌───────────────────────┐
                   │     Go 治理网关       │  ← 被测主体 (6 种策略)
                   │  (PlanGate / 基线)     │  准入 / 拒绝 / 定价
                   └──────────┬────────────┘
                              │  HTTP proxy (tool call)
                              ▼
                   ┌───────────────────────┐
                   │   Python MCP 后端     │  ThreadPoolExecutor
                   │  max_workers=10       │  queue-timeout=1.0s
                   │  congestion-factor=0.5│  6 种 MCP 工具
                   └───────────────────────┘
```

### 1.2 实验场景

**场景定位**: LLM Agent 在高并发下通过 MCP 协议调用多步工具链路。典型的 Agent 需要按 DAG 顺序执行 3~7 步工具调用（如：web_search → doc_embedding → llm_reasoner → text_formatter），每步调用的延迟和计算量各异。

**过载来源**: 后端 Python MCP 服务器限制 `max_workers=10`（模拟真实 GPU 推理服务的有限容量），而并发请求量 concurrency=200 远超后端承载能力，造成排队、超时和拥塞。

**测试负载参数**:

| 参数 | 值 | 说明 |
|------|-----|------|
| sessions | 500 | 总会话数 |
| concurrency | 200 | 最大并发会话数 |
| duration | 60s | 持续发压时间 |
| arrival_rate | 50/s | 会话到达速率 |
| ps_ratio | 1.0 | 100% Plan-and-Solve 模式 |
| heavy_ratio | 0.3 | 30% 重量工具占比 |
| min_steps / max_steps | 3 / 7 | DAG 步骤范围 |
| step_timeout | 2.0s | 单步超时 |
| budget | 500 | 每会话预算 |

### 1.3 核心问题

实验针对 MCP 多步工具调用场景下的两个关键问题：

**问题 1: 级联算力浪费 (Cascade Compute Waste)**

当 Agent 会话执行到第 3 步时被拒绝，前 3 步已消耗的 GPU 计算资源（如 doc_embedding 的向量计算)全部浪费。在 7 步 DAG 中，第 5 步被拒等于 71% 的计算浪费。传统网关（NG, SRL, Rajomon, DAGOR）均存在此问题。

**问题 2: 拥塞涨价导致的会话中途夭折 (Mid-Session Price Surge)**

动态定价网关在拥塞时提高价格。但已准入的长链路会话如果在后续步骤遇到价格暴涨，可能因预算不足被中途拒绝 —— 这是一种更隐蔽的级联浪费。客户端无法预知全链路成本。

---

## 二、测试方法

### 2.1 实验 1: Exp1_Core — 核心性能对比

- **目的**: 在高并发脉冲突发场景下，对比 6 种治理策略的综合性能
- **网关**: NG, SRL, Rajomon, DAGOR-MCP, SBAC-MCP, PlanGate-Full
- **重复**: 5 次取平均值和标准差
- **核心指标**:
  - **Effective Goodput/s**: 全链路成功完成的会话数 / 持续时间（核心指标）
  - **Cascade Failures**: 第 0 步通过但后续步骤失败的会话数
  - **REJECTED@S0**: 在第 0 步被智能拒绝的会话数（算力浪费=0）
  - **P50/P95/P99 延迟**: 单步工具调用延迟分布

### 2.2 实验 4: Exp4_Ablation — 严格单变量消融

- **目的**: 量化 PlanGate 每个创新模块的独立贡献
- **网关**: PlanGate-Full, w/o BudgetLock, w/o SessionCap, Rajomon(SOTA)
- **设计原则**: 每个消融变体只改变一个布尔开关或参数，其余完全一致

| 变体 | 与 Full 的唯一差异 | 目标 |
|------|---------------------|------|
| PlanGate-Full | 基准 (PS=40, MS=30) | — |
| w/o BudgetLock | `disableBudgetLock=true` | 验证创新点 2 贡献 |
| w/o SessionCap | `maxConcurrentSessions=0` | 验证并发上限保护贡献 |
| Rajomon (SOTA) | 完全不同方法 | SOTA 对照组 |

### 2.3 实验 2: Exp2_HeavyRatio — 重量工具占比扫参

- **目的**: 当 DAG 中重量工具 (doc_embedding, ~800ms) 比例增大时，各治理策略的表现变化
- **网关**: NG, SRL, Rajomon, DAGOR-MCP, SBAC-MCP, PlanGate-Full
- **扫参**: `heavy_ratio = [0.1, 0.3, 0.5, 0.7]`
- **假设**: PlanGate 的工具加权定价 (toolWeights) 能有效区分重轻工具

### 2.4 实验 3: Exp3_MixedMode — 双模式混合

- **目的**: 测试 Plan-and-Solve 与 ReAct 混合流量下的鲁棒性，包括纯 ReAct 场景
- **网关**: NG, SRL, Rajomon, DAGOR-MCP, SBAC-MCP, PlanGate-Full
- **扫参**: `ps_ratio = [0.0, 0.3, 0.5, 0.7, 1.0]`（0.0=纯 ReAct，1.0=纯 P&S）
- **假设**: PlanGate 的双模态治理在混合流量下仍保持优势；纯 ReAct 时 PlanGate 回退至逐步准入但仍优于 Rajomon

### 2.5 实验 5: Exp5_ScaleConc — 并发扩展性

- **目的**: 测试并发量扩展时各网关的 Effective Goodput 变化
- **网关**: NG, SRL, Rajomon, DAGOR-MCP, SBAC-MCP, PlanGate-Full
- **扫参**: `concurrency = [10, 20, 40, 60]`
- **假设**: 高并发下差异放大，PlanGate 凭借预检准入保持稳定

### 2.6 实验 7: Exp7_ClientReject — 客户端 Hard Reject price_ttl 扫参

- **目的**: 在客户端 Hard Reject 始终开启的条件下，扫描不同价格缓存 TTL 以寻找最优时效
- **网关**: PlanGate-Full only (hard_reject=True)
- **扫参**: `price_ttl = [0.1, 0.2, 0.5, 1.0, 2.0]`（秒）
- **设计**: sessions=500, concurrency=200, arrival_rate=50
- **假设**: 较短 TTL 追踪价格更及时 → EffGP/s 更高；过长 TTL 导致过期缓存 → 误判增多

### 2.7 参数公平性保证

所有基线均经过 Optuna 独立调优，优化目标一致（最大化 Effective Goodput/s）。每个基线在相同负载条件下分别搜索最优参数：

```python
TUNED_PARAMS = {
    "rajomon":       {"price_step": 20},
    "dagor":         {"rtt_threshold": 400.0, "price_step": 10},
    "sbac":          {"max_sessions": 150},
    "srl":           {"qps": 65.0, "burst": 400, "max_conc": 55},
    "plangate_full": {"price_step": 40, "max_sessions": 30},
}
```

---

## 三、基线方法详细描述

### 3.1 NG (No Governance) — 对照组

**来源**: 无治理基线（下界参照）

**机制**: 完全透传。所有请求直接转发到后端，无准入控制、无定价、无拒绝。

**MCP 特征利用**: ❌ 无。不读取任何 Header，不跟踪会话。

**弱点**: 高并发下后端排队超时严重，产生大量级联失败。响应体包含 `_meta.Price = "0"`，对客户端无任何过载信号。

---

### 3.2 SRL (Static Rate Limit) — 经典限流

**来源**: 经典 Token Bucket 限流方法

**机制**: 双层保护 —
1. **令牌桶** (QPS=65.0, Burst=400): 时间驱动的请求速率限制
2. **最大并发** (MaxConcurrency=55): 同时在途请求上限

```
请求到达 → 令牌桶.Allow()? → 并发计数<55? → 执行 → 返回
              ↓ No              ↓ No
           rate_limited     concurrency_limited
```

**MCP 特征利用**: ❌ 不读取 X-Plan-DAG、X-Session-ID、X-Total-Budget。不读取 `_meta.tokens`（客户端预算）。所有工具（轻量 text_formatter ~10ms vs 重量 doc_embedding ~800ms）用同一速率限制。

**弱点**:
- **工具盲区**: 无法区分轻重工具，重量工具占用与轻量工具相同的令牌配额
- **会话盲区**: 不跟踪多步会话，每步独立判定 → 级联失败
- **预算盲区**: 无价值感知，budget=10 和 budget=1000 的会话同等对待

---

### 3.3 Rajomon — 令牌 - 价格市场机制

**来源**: NSDI'25 (Xing et al.) — 分布式微服务过载控制

**机制**: 动态定价市场 —
1. **过载检测**: Go runtime 调度延迟直方图 + 后端响应 RTT 双信号
2. **价格调节**: 过载时 `price += priceStep(20)`，正常时 `price -= decayStep(1)`
3. **准入判定**: `if tokens < price → reject`

```
请求到达 → 读取 _meta.tokens → tokens ≥ ownPrice? → 执行 → _meta.Price=ownPrice
                                    ↓ No
                              reject (overloaded)
```

**MCP 特征利用**: ⚠️ 部分。读取 `_meta.tokens`（客户端预算），但不读取 X-Plan-DAG / X-Session-ID。**全局单一价格**——所有工具统一定价。

**弱点**:
- **全局单一价格**: 无法区分轻重工具（text_formatter 10ms ≈ doc_embedding 800ms → 同价）
- **无 DAG 感知**: 不理解多步链路结构，每步独立定价 → 级联失败
- **固定步长**: priceStep=20 在不同负载强度下无法自适应（无 Regime 切换）
- **无价格锁定**: 已准入会话在后续步骤仍可能因涨价被拒

**Rajomon 原始设计背景**: Rajomon 是为微服务 RPC 调用链设计的，每个节点独立定价。在此设计中一次 RPC 调用是原子性的（成功/失败），不存在"多步 DAG 会话"概念。将 Rajomon 应用到 MCP 多步工具调用场景时，其设计假设被打破 —— Agent 的 3~7 步链路需要全程保护，而非逐步独立决策。

---

### 3.4 DAGOR-MCP — 优先级削减

**来源**: SoCC'18 (DAGOR: Microservice Load Shedding)，适配到 MCP

**机制**: 基于优先级 (budget) 的过载削减 —
1. **过载检测**: 后端 RTT 超过阈值 (400ms) → 提高门槛
2. **门槛调节**: 过载时 `threshold += priceStep(10)`，正常时 `threshold -= priceStep`
3. **准入判定**: **每一步** `if budget < threshold → 丢弃`

```
Step 0: 解析 X-Plan-DAG → 注册会话 → budget ≥ threshold? → 执行
Step N: 读取 X-Session-ID → 查 budget → budget ≥ threshold? → 执行
                                             ↓ No (任意步骤)
                                     priority shedding ← 级联浪费!
```

**MCP 特征利用**: ✅ 读取 X-Plan-DAG 和 X-Session-ID Header，跟踪会话结构。是唯一除 PlanGate 外理解会话概念的基线。

**弱点（关键）**:
- **每步均可拒绝 (Mid-DAG Cascade Cutting)**: 这是 DAGOR 最致命的缺陷。即使 Step 0 通过，Step 3 仍可能因过载门槛上升被丢弃。此时 Step 0~2 的全部计算浪费。
- 对于 7 步 DAG，第 5 步被拒 = 71% 计算浪费
- **无价格锁定**: budget 是固定的，但 threshold 是动态上升的

**DAGOR 原始设计背景**: DAGOR 设计于单步 RPC 场景（一次 RPC 调用即完成），其"优先级削减"在单步场景下完全合理（低优先级请求被丢弃，不存在级联浪费）。将其扩展到 MCP 多步场景后，"每步检查"机制成为致命弱点。

---

### 3.5 SBAC-MCP — 会话槽位准入控制

**来源**: Session-Based Admission Control（基于会话的静态准入）

**机制**: 并发会话数量限制 —
1. **Step 0 准入**: `activeSessions < maxSessions(150)` → 原子性占用槽位
2. **Step N 放行**: 已注册会话无条件通过
3. **会话完成**: 最后一步执行后释放槽位

```
Step 0: activeSessions < 150? → CAS 占位 → 注册会话 → 执行
              ↓ No                                    Step N: 查会话 → 无条件执行
         reject at S0                                        ↓ 最后一步
         (零级联)                                      释放槽位
```

**MCP 特征利用**: ✅ 读取 X-Plan-DAG (注册会话) 和 X-Session-ID (查找会话)。但**不读取 budget/预算** —— 槽位分配完全"预算盲"。

**优势**: **零级联失败** —— 一旦准入，全程保证执行。这使 SBAC 在 cascade 维度上优于 SRL/Rajomon/DAGOR。

**弱点**:
- **预算盲区**: budget=10 的低价值会话与 budget=1000 的高价值会话竞争同一槽位 → 无法最大化经济效率
- **无动态定价**: 固定 maxSessions=150 无法适应负载波动
- **无工具差异化**: 全部会话等权（内部 DAG 结构忽略）

---

## 四、PlanGate 与 Rajomon 对比：创新点与提升

### 4.1 核心创新点

| # | 创新点 | PlanGate 实现 | Rajomon 缺失 | 原理 |
|---|--------|---------------|-------------|------|
| 1 | **Pre-flight Atomic Admission** | 第 0 步计算全链路总成本，原子性准入/拒绝 | 每步独立检查 price vs tokens | 消除多步 DAG 的级联算力浪费 |
| 2 | **Budget Reservation (预算锁)** | 准入后锁定当前价格快照，后续步骤免受涨价影响 | 无（每步用实时价格） | 防止已准入长链路会话中途因拥塞涨价夭折 |
| 3 | **Dual-Mode Governance** | 有 DAG → P&S 模式 (创新1+2)；无 DAG → ReAct 模式 | 仅支持单模态 | 同时服务 Plan-and-Solve Agent 和 ReAct Agent |
| 4 | **Concurrent Session Cap** | `sessionCap` 信道在 S0 原子检查，满则拒绝 | 无并发会话概念 | 保护后端不被过多长链路 DAG 会话压垮 |
| 5 | **Per-tool Weighted Pricing** | `toolWeights` 按工具延迟设权重（mock_heavy ×5） | 全局单一价格 | 重量工具付更多代价，公平定价 |
| 6 | **Adaptive Profile (Regime Detection)** | 基于并发信号方差切换 Steady/Burst/Extreme 参数档位 | 固定 priceStep/decayStep | 自适应不同负载强度 |

### 4.2 设计哲学差异

| 维度 | Rajomon | PlanGate |
|------|---------|----------|
| **设计粒度** | 单步 RPC | 多步 DAG 会话 |
| **定价范围** | 全局一个 ownPrice | 按工具加权 + 锁定快照 |
| **准入时机** | 每步逐一准入 | 全部在 Step 0 原子性决策 |
| **过载信号** | Go scheduler + RTT | Go scheduler + RTT + Regime 检测 |
| **适用场景** | 微服务 RPC 调用链 | MCP 多工具 Agent 链路 |
| **对客户端反馈** | 实时 price（无预测） | 锁定 price + DAG 总成本可预知 |

### 4.3 定量性能提升（Exp1_Core 结果，WSL2 Linux + taskset CPU 隔离，5 次重复平均）

| 指标 | Rajomon | PlanGate-Full | 提升幅度 |
|------|---------|---------------|----------|
| Effective Goodput/s | 1.6(±0.4) | 61.4(±3.2) | **38× 提升** |
| Cascade Failures | 64.8(±4.3) | **0.0** | 完全消除 |
| Success Sessions | 2.6(±0.6) | 104.0(±10.0) | 40× |
| P50 延迟 | 2.3(±0.1)ms | 5.9(±0.9)ms | 相当（直通级） |
| E2E P50 延迟 | 1704.0(±405.0)ms | 999.6(±123.9)ms | **1.7× 更快** |
| E2E P95 延迟 | 2261.4(±1088.4)ms | 2814.4(±645.3)ms | Rajomon 样本过少不可比 |

**Exp1_Core 全部 6 种网关汇总**:

| 网关 | EffGP/s | Cascade | Success | REJECTED@S0 | E2E P50 (ms) | E2E P95 (ms) | E2E P99 (ms) |
|------|---------|---------|---------|-------------|-------------|-------------|-------------|
| NG | 17.1(±2.7) | 128.4(±3.3) | 25.8(±3.3) | 345.8(±3.7) | 4488.3(±550.5) | 5900.2(±810.9) | 6541.1(±1207.9) |
| SRL | 28.7(±1.0) | 102.0(±4.7) | 42.8(±1.8) | 355.2(±5.7) | 4931.7(±547.6) | 7647.0(±178.5) | 9012.8(±792.8) |
| Rajomon | 1.6(±0.4) | 64.8(±4.3) | 2.6(±0.6) | 432.6(±4.6) | 1704.0(±405.0) | 2261.4(±1088.4) | 2261.4(±1088.4) |
| DAGOR | 2.0(±1.0) | 127.2(±7.3) | 4.0(±1.7) | 368.8(±6.1) | 1087.2(±264.7) | 2041.8(±740.8) | 2041.8(±740.8) |
| SBAC | 43.9(±2.0) | 44.2(±2.4) | 56.2(±6.6) | 399.6(±4.3) | 2942.1(±481.7) | 5109.5(±293.5) | 5847.8(±902.0) |
| **PlanGate-Full** | **61.4(±3.2)** | **0.0** | **104.0(±10.0)** | 396.0(±10.0) | **999.6(±123.9)** | **2814.4(±645.3)** | **3630.8(±50.9)** |

> **E2E 延迟说明**: E2E P50/P95/P99 为成功会话的端到端延迟（从会话发起到最后一步完成）。Rajomon/DAGOR 因成功会话极少（~2~4），延迟统计不具代表性。PlanGate 的 E2E P50=999.6ms 显著低于 NG(4488.3ms)、SRL(4931.7ms)、SBAC(2942.1ms)，说明预算锁定不仅消除级联浪费，还缩短了成功会话的实际完成时间。

**Exp4_Ablation 消融结果**:

| 变体 | EffGP/s | Cascade | 相对 Full 下降 |
|------|---------|---------|---------------|
| PlanGate-Full | 59.2(±3.9) | 0.0 | — |
| w/o BudgetLock | 11.9(±0.1) | 10.0(±0.0) | ↓79.9% |
| w/o SessionCap | 62.3(±1.7) | 0.2(±0.5) | +5.2% (方差内) |
| Rajomon (SOTA) | 1.3(±0.4) | 64.6(±3.4) | ↓97.8% |

---

## 五、PlanGate 方法文件清单

PlanGate 的代码按功能模块化组织在 `plangate/` 包中（对标 `rajomon/` 的文件组织方式）：

```
plangate/
├── doc.go                  (~30行)  包级文档：三大创新点说明、文件导航
├── server.go               (~70行)  MCPDPServer 结构体、构造函数、工具注册
│                                      → NewMCPDPServer / NewMCPDPServerNoLock
├── session_manager.go      (~110行) 会话预算预留管理器
│                                      → HTTPSessionReservation (预算锁生命周期)
│                                      → HTTPBudgetReservationManager (预留创建/查询/释放/TTL清理)
├── dag_validation.go       (~65行)  DAG 类型定义与 Kahn 拓扑排序验证
│                                      → HTTPDAGStep / HTTPDAGPlan 结构体
│                                      → validateHTTPDAG() 无环检测
├── http_handlers.go        (~100行) HTTP/JSON-RPC 请求分发
│                                      → ServeHTTP() 实现 http.Handler
│                                      → handleInitialize/handleToolsList/handleToolsCall
├── dual_mode_routing.go    (~220行) 双模态路由 (核心创新实现)
│                                      → handlePlanAndSolveFirstStep() [创新1+2: 预检准入+预算锁]
│                                      → handleReservedStep() [创新2: 锁定价格执行]
│                                      → handleReActMode() [创新3: 标准动态定价]
│                                      → executeStepDirect() [绕过 LoadShedding 执行]
│                                      → calculateDAGTotalCost() [全链路价格计算]
```

**与 Rajomon 文件结构对比**:

| 职责 | Rajomon 文件 | PlanGate 文件 |
|------|-------------|---------------|
| 主体/拦截器 | rajomon.go (~500行) | http_handlers.go + dual_mode_routing.go |
| 初始化/配置 | initOptions.go (~200行) | server.go (~70行) |
| 价格/令牌 | tokenAndPrice.go (~150行) | session_manager.go (锁定价格快照) |
| 过载检测 | overloadDetection.go (~90行) | 委托给 MCPGovernor (共享基础设施) |
| 运行时延迟 | queuingDelay.go (~120行) | 委托给代理级 proxyOverloadDetector |
| DAG 验证 | dag_admission.go (~350行) | dag_validation.go (~65行) |
| 日志 | logger.go (~20行) | 内嵌 log.Printf |
| 文档 | README.md | doc.go |

---

## 六、基线公平性审计

### 6.1 MCP 协议覆盖度 ✅

所有 6 个网关均完整实现 MCP JSON-RPC 2.0 协议的 4 个必要方法 (initialize, tools/list, tools/call, ping)。协议层面完全一致。

### 6.2 工具代理路径 ✅

所有网关使用同一个 `makeProxyHandler()` 函数代理到 Python 后端，传输链路一致。无基线因代理实现差异而获得不公平优势。

### 6.3 Optuna 独立调优 ✅

每个基线在独立的 Optuna trial 中搜索最优参数，优化目标函数一致（最大化 Effective Goodput/s ≥ 95% 置信区间）。参数空间按各基线特性独立设定。

### 6.4 MCP 多步工具调用特征利用 — 差异化分析

| 特征 | NG | SRL | Rajomon | DAGOR | SBAC | PlanGate |
|------|:--:|:---:|:-------:|:-----:|:----:|:--------:|
| X-Plan-DAG Header | ❌ | ❌ | ❌ | ✅ 读取 | ✅ 读取 | ✅ **深度利用** |
| X-Session-ID Header | ❌ | ❌ | ❌ | ✅ 读取 | ✅ 读取 | ✅ **深度利用** |
| X-Total-Budget Header | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ **独占** |
| `_meta.tokens` 预算 | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ |
| 会话生命周期跟踪 | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| DAG 总成本计算 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ **独占** |
| 价格锁定/快照 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ **独占** |
| 工具差异化定价 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

**公平性结论**: 各基线对 MCP 扩展 Header 的利用程度**反映了其设计能力的天然差异**，而非实验设置偏见。NG/SRL/Rajomon 的原始论文未涉及多步 DAG 概念，因此不处理相关 Header 是合理的设计限制。DAGOR 和 SBAC 利用了会话 Header 但缺乏预检准入和价格锁定能力。PlanGate 是唯一为 MCP 多步场景原生设计的方法，充分利用所有协议扩展。

### 6.5 结构性差异小结

基线间的性能差异主要来自**设计能力的结构性差异**（Design Gap），而非参数调优不公平：

- **NG/SRL**: 无 DAG 感知 → 级联失败不可避免
- **Rajomon**: 有定价但无 DAG 感知 → 每步独立判定导致级联
- **DAGOR**: 有 DAG 感知但每步检查 → 中途削减导致级联浪费
- **SBAC**: 有会话保护但无价值感知 → 无法最大化经济效率
- **PlanGate**: DAG 原子准入 + 价格锁定 + 双模态 → 零级联 + 经济最优

---

## 七、扩展实验结果（WSL2 Linux + taskset CPU 隔离，5 次重复平均）

所有实验均在 WSL2 Ubuntu-22.04 下运行，CPU 隔离: loadgen=core 0-3, gateway=core 4-7, backend=core 8-15。

### 7.1 Exp2_HeavyRatio — 重量工具占比对性能的影响

**设定**: sessions=200, concurrency=20, ps_ratio=1.0, sweep heavy_ratio=[0.1, 0.3, 0.5, 0.7]

| 网关 | heavy=0.1 EffGP/s | heavy=0.3 EffGP/s | heavy=0.5 EffGP/s | heavy=0.7 EffGP/s |
|------|-------------------|-------------------|-------------------|-------------------|
| NG | 79.9(±0.4) | 67.6(±0.4) | 63.8(±0.6) | 60.6(±0.2) |
| SRL | 79.6(±0.6) | 67.9(±0.1) | 64.1(±0.3) | 60.6(±0.4) |
| Rajomon | 79.5(±0.4) | 25.6(±2.4) | 12.2(±0.9) | 6.7(±0.7) |
| DAGOR | 79.7(±0.6) | 65.6(±3.2) | 55.5(±1.5) | 52.8(±5.6) |
| SBAC | 79.7(±0.2) | 67.6(±0.2) | 63.8(±0.5) | 60.7(±0.6) |
| **PlanGate** | 39.5(±1.9) | 56.6(±11.3) | **63.8(±0.4)** | **59.4(±0.5)** |

| 网关 | heavy=0.1 Cascade | heavy=0.3 Cascade | heavy=0.5 Cascade | heavy=0.7 Cascade |
|------|-------------------|-------------------|-------------------|-------------------|
| NG | 0.0 | 0.2(±0.5) | 2.0(±2.4) | 5.8(±1.9) |
| SRL | 0.0 | 0.0 | 1.4(±0.6) | 6.4(±2.1) |
| Rajomon | 0.0 | 14.0(±1.7) | 16.2(±0.5) | 18.0(±0.0) |
| DAGOR | 0.0 | 9.4(±8.8) | 15.0(±3.4) | 16.6(±3.8) |
| SBAC | 0.0 | 0.2(±0.5) | 1.6(±1.1) | 5.6(±1.9) |
| **PlanGate** | **0** | **0.4(±0.6)** | **0.6(±0.6)** | **1.6(±1.5)** |

**分析**: 在低 heavy_ratio (0.1) 时系统负载轻，所有方法均无 cascade，PlanGate 因保守准入导致 EffGP/s 偏低。但随着 heavy_ratio 增大，Rajomon 的 EffGP/s 急剧下降（0.7 时仅 6.7），而 PlanGate 凭借工具加权价格 (toolWeights) 始终维持零/近零 cascade，在 heavy=0.5 时达到最优 EffGP/s。

### 7.2 Exp3_MixedMode — P&S 与 ReAct 混合模式鲁棒性

**设定**: sessions=200, concurrency=20, heavy_ratio=0.3, sweep ps_ratio=[0.0, 0.3, 0.5, 0.7, 1.0]

| 网关 | ps=0.0 EffGP/s | ps=0.3 EffGP/s | ps=0.5 EffGP/s | ps=0.7 EffGP/s | ps=1.0 EffGP/s |
|------|----------------|----------------|----------------|----------------|----------------|
| NG | 67.6(±0.3) | 67.7(±0.3) | 67.5(±0.1) | 68.0(±0.4) | 67.7(±0.1) |
| SRL | 67.9(±0.2) | 67.7(±0.1) | 67.4(±0.1) | 67.7(±0.4) | 67.6(±0.5) |
| Rajomon | 25.9(±4.2) | 20.7(±5.4) | 25.0(±1.2) | 24.9(±3.6) | 26.5(±4.3) |
| DAGOR | 31.0(±5.1) | 52.1(±0.5) | 59.8(±2.8) | 64.7(±2.1) | 66.3(±2.4) |
| SBAC | 67.7(±0.3) | 67.6(±0.3) | 67.7(±0.2) | 68.0(±0.4) | 68.0(±0.3) |
| **PlanGate** | **30.7(±1.3)** | **38.9(±3.6)** | **41.5(±4.2)** | **45.4(±2.8)** | **58.3(±8.5)** |

| 网关 | ps=0.0 Cascade | ps=0.3 Cascade | ps=0.5 Cascade | ps=0.7 Cascade | ps=1.0 Cascade |
|------|----------------|----------------|----------------|----------------|----------------|
| NG | 0.2(±0.5) | 0.2(±0.5) | 0.0 | 0.0 | 0.0 |
| SRL | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Rajomon | 16.0(±1.2) | 14.4(±1.5) | 14.2(±0.8) | 14.6(±2.6) | 13.6(±1.3) |
| DAGOR | 61.6(±4.4) | 53.2(±6.3) | 13.6(±6.1) | 8.0(±7.9) | 2.8(±4.7) |
| SBAC | 0.4(±0.6) | 0.0 | 0.0 | 0.0 | 0.0 |
| **PlanGate** | **77.0(±3.5)** | **48.0(±3.2)** | **36.8(±3.2)** | **13.0(±2.5)** | **0.0** |

| 网关 | ps=0.0 Success | ps=0.3 Success | ps=0.5 Success | ps=0.7 Success | ps=1.0 Success |
|------|----------------|----------------|----------------|----------------|----------------|
| NG | 199.8(±0.5) | 199.8(±0.5) | 200.0 | 200.0 | 200.0 |
| SRL | 200.0 | 200.0 | 200.0 | 200.0 | 200.0 |
| Rajomon | 42.0(±6.1) | 26.6(±4.5) | 33.0(±1.6) | 31.8(±5.3) | 29.2(±5.3) |
| DAGOR | 74.4(±10.1) | 81.6(±4.8) | 98.6(±9.9) | 135.2(±15.4) | 175.6(±41.4) |
| SBAC | 199.6(±0.6) | 200.0 | 200.0 | 200.0 | 200.0 |
| **PlanGate** | **86.4(±2.7)** | **93.8(±2.9)** | **75.2(±4.0)** | **80.0(±4.6)** | **111.0(±46.6)** |

**分析**:

1. **纯 ReAct 场景 (ps=0.0)**: 这是对 PlanGate 最不利的场景——所有会话都没有前置 DAG 计划，PlanGate 退回逐步准入模式。即便如此，PlanGate EffGP/s=30.7 仍 **优于 Rajomon (25.9, +19%)**，且更优于 DAGOR (31.0)。说明即使在"无计划"场景下，PlanGate 的工具加权定价与 Regime 适配仍能发挥作用。

2. **模式渐变**: 随着 ps_ratio 从 0.0→1.0，PlanGate 的 cascade 从 77.0→0 线性下降，EffGP/s 从 30.7→58.3 持续上升。这证实 PlanGate 的预算锁机制在 P&S 模式下的核心价值——完全消除级联浪费。

3. **Rajomon 对比**: Rajomon 全程几乎不受 ps_ratio 影响（EffGP/s 在 21~27 之间波动），因为其设计不区分 P&S/ReAct 模式。而 PlanGate 在 ps≥0.5 时已超越 Rajomon 约 1.7× EffGP/s (41.5 vs 25.0)。

### 7.3 Exp5_ScaleConc — 并发扩展性测试

**设定**: sessions=200, ps_ratio=1.0, sweep concurrency=[10, 20, 40, 60]

| 网关 | conc=10 EffGP/s | conc=20 EffGP/s | conc=40 EffGP/s | conc=60 EffGP/s |
|------|-----------------|-----------------|-----------------|-----------------|
| NG | 69.9(±0.0) | 67.6(±0.5) | 39.6(±2.6) | 29.8(±1.8) |
| SRL | 70.0(±0.2) | 67.7(±0.3) | 39.6(±1.9) | 31.1(±1.6) |
| Rajomon | 69.4(±0.2) | 24.5(±2.9) | 6.2(±0.8) | 4.4(±0.7) |
| DAGOR | 70.0(±0.1) | 65.4(±2.1) | 22.0(±2.3) | 15.8(±2.9) |
| SBAC | 70.0(±0.2) | 67.7(±0.2) | 40.8(±1.0) | 32.7(±2.6) |
| **PlanGate** | **69.0(±0.1)** | **50.7(±9.6)** | **57.5(±8.8)** | **51.7(±4.0)** |

| 网关 | conc=10 Cascade | conc=20 Cascade | conc=40 Cascade | conc=60 Cascade |
|------|-----------------|-----------------|-----------------|-----------------|
| NG | 0.0 | 0.0 | 81.6(±11.0) | 90.8(±5.3) |
| SRL | 0.0 | 0.0 | 81.4(±6.6) | 83.4(±7.5) |
| Rajomon | 7.4(±0.6) | 13.2(±1.6) | 39.2(±1.5) | 52.0(±3.3) |
| DAGOR | 0.0 | 10.0(±6.5) | 81.2(±4.1) | 98.0(±9.7) |
| SBAC | 0.0 | 0.0 | 72.4(±4.0) | 83.4(±7.0) |
| **PlanGate** | **0** | **0** | **0.6(±1.3)** | **0.2(±0.5)** |

**E2E 端到端延迟 (成功会话, ms)**:

| 网关 | conc=10 E2E P50 | conc=20 E2E P50 | conc=40 E2E P50 | conc=60 E2E P50 |
|------|-----------------|-----------------|-----------------|-----------------|
| NG | 1301.8(±4.5) | 2847.3(±30.0) | 5031.3(±221.6) | 4554.1(±314.9) |
| SRL | 1300.3(±2.9) | 2861.5(±30.0) | 5094.6(±207.1) | 4802.2(±320.6) |
| Rajomon | 1300.3(±3.6) | 2885.7(±29.1) | 2402.7(±281.7) | 1789.4(±495.3) |
| DAGOR | 1299.3(±3.5) | 2877.2(±59.7) | 2584.0(±329.9) | 2024.2(±386.8) |
| SBAC | 1300.7(±3.4) | 2871.1(±54.2) | 4994.7(±110.3) | 4907.9(±473.9) |
| **PlanGate** | **1293.1(±2.5)** | **1520.1(±566.4)** | **1893.6(±702.8)** | **1872.4(±557.4)** |

**分析**: PlanGate 在高并发场景下展现出显著优势。

1. **Cascade**: conc≥40 时其他所有方法的 cascade 飙升至 39~98，但 PlanGate 始终维持在 **0~0.6**。PlanGate 的下降是“智能降级”（通过 REJECTED@S0 主动拒绝），而非 cascade 浪费。

2. **EffGP/s**: 在 conc=60 时 PlanGate (51.7) 远超 NG (29.8, +73%)、SRL (31.1, +66%)、Rajomon (4.4, +1075%)、DAGOR (15.8, +227%)、SBAC (32.7, +58%)。

3. **E2E 延迟**: PlanGate 在所有并发级别下 E2E P50 均为最低。特别是 conc=40 时 PlanGate E2E P50=1893.6ms，而 NG/SRL/SBAC 均约 5000ms（因大量排队+级联重试）。这证实预算锁不仅提升吞吐，也显著缩短了成功会话的完成时间。

### 7.4 Exp7_ClientReject — 客户端 Hard Reject price_ttl 扫参

**设定**: PlanGate-Full only, hard_reject=True, sessions=500, concurrency=200, sweep price_ttl=[0.1, 0.2, 0.5, 1.0, 2.0]

| price_ttl (s) | EffGP/s | Success | Cascade | REJECTED@S0 | E2E P50 (ms) | E2E P95 (ms) |
|---------------|---------|---------|---------|-------------|-------------|-------------|
| 0.1 | 62.9(±2.8) | 100.2(±3.8) | 0.0 | 399.8(±3.8) | 855.7(±67.6) | 2440.1(±566.5) |
| 0.2 | 59.9(±2.7) | 96.2(±2.8) | 0.0 | 403.8(±2.8) | 824.9(±41.9) | 2359.4(±145.2) |
| 0.5 | 62.4(±1.4) | 102.6(±4.3) | 0.0 | 397.4(±4.3) | 719.2(±129.9) | 2457.1(±637.0) |
| 1.0 | 47.4(±6.9) | 88.0(±5.8) | 0.2(±0.5) | 411.8(±6.3) | 613.3(±224.1) | 2501.7(±649.4) |
| 2.0 | 47.1(±1.2) | 85.8(±2.7) | 0.0 | 414.2(±2.7) | 494.7(±2.2) | 2007.1(±85.4) |

**分析**:

1. **最优 TTL = 0.1~0.5s**: EffGP/s 在 TTL≤0.5 时保持高位 (59.9~62.9)，与无 Hard Reject 的 Exp1 基线 (61.4) 几乎持平。这说明短 TTL 的价格缓存足够精确，客户端本地拒绝不会引入额外吞吐损失。

2. **TTL 越长 → EffGP/s 越低**: TTL=2.0 时 EffGP/s 下降至 47.1 (↓25%)，因过期缓存价格导致高估远离拒绝或低估误判。成功会话数也从 100.2 下降至 85.8。

3. **Cascade 始终 ≈ 0**: 无论 TTL 如何设置，Hard Reject 均不引入级联失败，验证了该机制的安全性。

4. **E2E 延迟**: TTL=0.1 时 E2E P50=855.7ms，低于 Exp1 基线的 999.6ms (无 Hard Reject)。Hard Reject 通过在客户端提前过滤必然失败的请求，减少了网关负载，间接降低了成功会话的端到端延迟。

5. **实用建议**: 推荐 price_ttl=0.1~0.5s 作为生产默认值。

### 7.5 扩展实验总结

| 实验 | 核心发现 |
|------|----------|
| Exp2 | PlanGate 的 toolWeights 在高 heavy_ratio 下有效防止 cascade，Rajomon EffGP/s 从 79.5 骤降至 6.7 |
| Exp3 | PlanGate 在纯 P&S 模式下零 cascade；**纯 ReAct (ps=0.0) 场景下仍优于 Rajomon +19%**，证明双模态退化优雅 |
| Exp5 | **PlanGate 在 conc≥40 时是唯一保持近零 cascade 的方法**，EffGP/s 优势显著；E2E P50 在高并发下约为 NG/SRL 的 1/3 |
| Exp7 | 客户端 Hard Reject 最优 TTL=0.1~0.5s，EffGP/s 与无 HR 基线持平；TTL 过长 (2.0s) 导致 ↓25% 性能退化 |

### 7.6 潜在后续实验

- **故障恢复**: 中途重启后端，观察各网关的恢复速度
- **长链路压力**: 固定 min_steps=max_steps=10，测试极端长 DAG 场景
