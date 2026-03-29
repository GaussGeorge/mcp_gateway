# PlanGate 实验报告

## 一、实验测试对象

### 1.1 系统架构

实验测试的是一个面向 MCP (Model Context Protocol) 多步工具调用场景的网关治理系统。架构为三层：

```
DAG 负载生成器 (Python asyncio)
       ↓  HTTP/JSON-RPC 2.0
Go 治理网关 (NG / SRL / Rajomon / DAGOR / SBAC / PlanGate)
       ↓  HTTP proxy
Python MCP 后端 (ThreadPoolExecutor, max_workers=10)
```

- **后端物理约束**: `max-workers=10, queue-timeout=1.0s, congestion-factor=0.5` — 模拟真实 MCP 服务器在高并发下的拥塞行为
- **网关角色**: 在后端容量有限的条件下，不同治理策略决定哪些请求准入、哪些拒绝

### 1.2 待测方法: PlanGate

PlanGate 是我们提出的 Plan-Aware Gateway 方法，包含三大核心创新：

| # | 创新点 | 功能 | 对应代码 |
|---|--------|------|----------|
| 1 | **Pre-flight Atomic Admission** | 在第 0 步原子性计算 DAG 全链路总价格并准入/拒绝，实现零级联浪费 | `plangate/plangate.go: handlePlanAndSolveFirstStep()` |
| 2 | **Budget Reservation (预算锁)** | 准入后为会话锁定当前价格快照，防止已准入会话因拥塞涨价而中途失败 | `plangate/plangate.go: HTTPBudgetReservationManager` |
| 3 | **Dual-Mode Governance** | 有 DAG → Plan-and-Solve 模式 (创新1+2)；无 DAG → ReAct 模式 (标准动态定价) | `plangate/plangate.go: handleToolsCall()` |

Optuna 调优参数: `price_step=40, max_sessions=30`

### 1.3 基线方法

| 基线 | 来源 | 准入机制 | 多步 DAG 支持 |
|------|------|----------|---------------|
| **NG** (No Governance) | 对照组 | 无准入控制 | 无 |
| **SRL** (Static Rate Limit) | 经典方法 | 令牌桶 QPS 限流 + 最大并发 | 无（工具级） |
| **Rajomon** | OSDI'25 | 动态定价（调度延迟驱动） | 无（单一全局价格） |
| **DAGOR-MCP** | SoCC'18 | RTT 过载检测 + 优先级门槛 | 部分（读取 Header，但**每步都可拒绝**） |
| **SBAC-MCP** | Session-Based | 会话槽位准入 | 部分（S0 准入，后续放行，但**无预算感知**） |

所有基线均经过 Optuna 独立调优，目标函数一致（最大化 Effective Goodput/s）。

---

## 二、发现的问题与修正

### 2.1 问题: 消融设计变量混淆

原始消融实验 (Full vs NoLock) 存在 3 个变量差异，非真正单变量：

| 维度 | Full (旧) | NoLock (旧) |
|------|-----------|-------------|
| 预算锁 | ✅ 启用 | ❌ 禁用 |
| 会话上限 | 30 | 0 (无限制) |
| 构造函数 | `NewMCPDPServer` | `NewMCPDPServerNoLock` |

### 2.2 修正: 严格单变量消融

重新设计为每个消融变体只改变一个维度：

| 变体 | 与 Full 的唯一差异 | 其余参数 |
|------|---------------------|----------|
| **PlanGate-Full** | 基准 (Optuna: PS=40, MS=30) | — |
| **w/o BudgetLock** | `disableBudgetLock=true` | PS=40, MS=30 |
| **w/o SessionCap** | `maxConcurrentSessions=0` | PS=40, BudgetLock=on |
| **Rajomon (SOTA)** | 完全不同方法（对照） | 独立调优 |

### 2.3 问题: Profile Override 干扰

原来的 PlanGate-Full 使用了 Profile Override 覆盖检测器参数 (MaxConc=40, PriceStep=2, DecayStep=10)，导致价格几乎不上涨，准入过于宽松。去除 Profile Override 后（即直接使用 Optuna 调优的 PriceStep=40），EffGP/s 从 56.6 提升到 63.2。

**决策**: 直接使用 Optuna 调优参数的版本定义为 PlanGate-Full，不再将 w/o Profile 作为消融项。

---

## 三、实验方法论

### 3.1 Exp1_Core: 核心性能对比

- **目的**: 在高并发脉冲突发场景下对比 6 种治理策略的综合性能
- **负载**: 500 sessions, concurrency=200, duration=60s, arrival_rate=50/s, ps_ratio=1.0 (纯 Plan-and-Solve), heavy_ratio=0.3
- **网关**: NG, SRL, Rajomon, DAGOR, SBAC, PlanGate-Full
- **重复**: 5 次，取平均值和标准差
- **指标**: Effective Goodput/s, Cascade Failures, Success Sessions, P50/P95/P99 Latency

### 3.2 Exp4_Ablation: 严格单变量消融

- **目的**: 量化 PlanGate 每个创新模块的独立贡献
- **负载**: 与 Exp1 相同
- **网关**: PlanGate-Full, w/o BudgetLock, w/o SessionCap, Rajomon
- **重复**: 5 次
- **分析**: 以 Full 为基准，计算移除每个模块后的 Goodput 下降百分比

---

## 四、实验结果

### 4.1 Exp1_Core 结果

| Gateway | EffGP/s | Cascade | Success | Rej@S0 | P50 (ms) | P95 (ms) |
|---------|---------|---------|---------|--------|----------|----------|
| NG | ~12.4 | ~96 | ~17 | ~387 | 1014 | 1906 |
| SRL | ~29.3 | ~93 | ~40 | ~368 | 1007 | 1888 |
| Rajomon | ~0.72 | ~60 | ~1 | ~439 | 3.3 | 1411 |
| DAGOR | ~3.4 | ~113 | ~6 | ~382 | 1010 | 1551 |
| SBAC | ~41.7 | ~41 | ~45 | ~414 | 151 | 1299 |
| **PlanGate-Full** | **~65.7** | **0** | **~97** | ~402 | **4.4** | **835** |

**关键发现**:
- PlanGate-Full 的 EffGP/s 是第二名 SBAC 的 **1.58 倍**，是 Rajomon 的 **91 倍**
- PlanGate 实现**零级联失败**，而 DAGOR 产生 113 次级联（最严重）
- PlanGate 的 P50 延迟仅 4.4ms（与后端直通延迟相当），P95 也远低于其他方法

### 4.2 Exp4_Ablation 结果

| 变体 | EffGP/s | Cascade | Success | 相对 Full 下降 |
|------|---------|---------|---------|----------------|
| **PlanGate-Full** | **63.9** | 0.2 | 95 | — (基准) |
| w/o BudgetLock | 10.3 | 12 | 18 | **↓ 83.9%** |
| w/o SessionCap | 62.2 | 0.6 | 95 | ↓ 2.7% |
| Rajomon (SOTA) | 0.96 | 59 | 1.2 | ↓ 98.5% |

**关键发现**:

1. **BudgetLock 是核心贡献**: 移除后 EffGP/s 暴跌 83.9%（63.9 → 10.3），cascade 从 0 飙升到 12。这证明预算锁机制是 PlanGate 的主要创新贡献。

2. **SessionCap 在当前参数下贡献有限**: 仅 2.7% 差异。原因是 Optuna 调优的 PriceStep=40 已经足够高，价格机制本身已能有效控制准入，额外的并发槽位约束提供边际保护。

3. **Rajomon 在多步场景下几乎失效**: EffGP/s 仅 0.96，因为其单一全局价格策略无法应对 DAG 链路的异构负载。

---

## 五、基线公平性审计

### 5.1 MCP 协议覆盖度

所有 6 个基线均完整实现 MCP JSON-RPC 2.0 协议 (initialize, tools/list, tools/call, ping)。✅

### 5.2 工具代理一致性

所有基线使用相同的 `makeProxyHandler` 函数代理到 Python 后端，传输路径完全一致。✅

### 5.3 参数公平性

所有基线均经过 Optuna 独立调优，优化目标一致 (最大化 Effective Goodput/s)。✅

### 5.4 结构性差异（设计导致的固有不公平）

| 问题 | 受影响基线 | 说明 |
|------|-----------|------|
| **级联失败** | DAGOR | 每步都做准入检查，中途拒绝导致前置计算浪费 |
| **预算盲区** | SRL, NG, SBAC | 无法区分高价值/低价值会话 |
| **工具盲区** | SRL, Rajomon | 重量工具与轻量工具同价，无法差异化治理 |
| **DAG 无感知** | NG, SRL, Rajomon | 完全不理解多步链路结构 |

**结论**: 基线之间的性能差异主要来自**设计能力差异**（结构性的），而非参数调优不公平。这正是实验要证明的：PlanGate 的设计（DAG 感知 + 预算锁 + 双模态）相对于传统方法的结构性优势。

---

## 六、实验文件清单

| 文件 | 用途 |
|------|------|
| `plangate/plangate.go` | PlanGate 核心实现（3大创新） |
| `cmd/gateway/main.go` | 统一网关入口 (6 种模式 + 3 种消融变体) |
| `baseline/*.go` | 5 个基线实现 |
| `scripts/run_all_experiments.py` | 自动化跑批 (Exp1-Exp5) |
| `scripts/visualize_results.py` | 图表生成 |
| `scripts/dag_load_generator.py` | DAG 负载生成器 |
| `results/figures/` | 生成的图表 |
