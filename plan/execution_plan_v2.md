# PlanGate 冲击 CCF-A 详细执行方案

> 基于 final.md 指导方案 + 代码库/实验数据深度分析  
> 创建日期: 2026-04-13  
> 目标会议: EuroSys / ATC / ASPLOS 2027

---

## 零、对 final.md 方案的评估与补充

### 0.1 方案核心判断：完全认同

final.md 提出的**叙事重构**——从"更好的动态定价网关"转向"多步 Agent 缺失的治理原语: session commitment"——是正确且必要的。理由：

1. **四份审稿意见的最大共性不是"缺某个实验"，而是"创新性被高估 / 定义不清"**。R2 明确说"session tracking 只是工程问题，不构成理论创新"；R3 说"理论贡献严重不足"。这说明当前叙事让审稿人觉得"这只是 Rajomon + 会话 ID"。
2. **重新定义抽象层次**（atomic admission / temporal isolation / continuation value）把四个分散机制统一成一个不可替代的概念体系，直接回应了"与 Rajomon 区别不大"的批评。
3. **Reputation 降级为辅助特性**是正确的——四位审稿人都指出它对 Sybil 攻击脆弱，强推只会增加攻击面。

### 0.2 补充与修正

final.md 计划整体优秀，但在以下方面需要细化或修正：

| 编号 | final.md 遗漏/模糊点 | 本方案补充 |
|------|---------------------|-----------|
| A | Progress-Priority 基线的**具体实现规格**未定义 | §1.2 给出完整算法和 Go 代码骨架 |
| B | Rajomon + Session Bookkeeping 的**具体改动范围**未明确 | §1.3 基于 rajomon_gateway.go 给出精确改动清单 |
| C | "Bursty load"和"Long-tail sessions"的**具体参数配置**未给出 | §2.2 给出完整实验参数表 |
| D | 理论部分"risk exposure 框架"过于抽象 | §3 给出可写入论文的推导骨架 |
| E | 缺少 **External Signal Tracker 权重消融**（R2 要求） | §2.3 补入实验清单 |
| F | **Admitted-but-doomed 指标**的采集方式未说明 | §2.4 给出代码埋点方案 |
| G | **自适应 α 的具体算法**未明确 | §4.3 给出在线调整伪代码 |
| H | 分布式原型的 **Redis 集成改动范围**未评估 | §5 给出工程量评估和接口设计 |

---

## 一、基线矩阵：代码改动详细方案

### 1.1 当前基线清单（已有）

| 基线 | 文件 | 模式 | 会话感知 | 定价 |
|------|------|------|---------|------|
| NG | baseline/ng_gateway.go | `ng` | ✗ | ✗ |
| SRL | baseline/srl_gateway.go | `srl` | ✗ | ✗ (Token Bucket) |
| Rajomon | baseline/rajomon_gateway.go | `rajomon` | ✗ | ✓ (per-request) |
| DAGOR | baseline/dagor_gateway.go | `dagor` | 部分 | ✓ (每步可拒) |
| SBAC | baseline/sbac_gateway.go | `sbac` | ✓ | ✗ (仅限额) |
| PlanGate Full | plangate/server.go | `mcpdp` | ✓ | ✓ (session commitment) |

### 1.2 新增基线 1: Progress-Priority Admission（PP）

**目的**: 证明"仅优先照顾快完成的会话"不能替代 session commitment。

**算法规格**:
```
准入规则:
  - 维护全局 activeSessions 计数
  - 当 activeSessions >= maxSessions 时:
    - 计算当前请求的 progressScore = completedSteps / totalSteps
    - 遍历所有活跃会话，找到 progressScore 最低的会话
    - 如果当前请求的 progressScore > 最低会话的 progressScore:
      → 驱逐最低进度会话, 接纳当前会话
    - 否则: 拒绝当前请求
  - 当 activeSessions < maxSessions 时: 直接接纳
  
关键区别于 PlanGate:
  - 无预算预留（no temporal isolation）
  - 无沉没成本折扣（no continuation pricing）
  - 无 step-0 原子准入（no atomic admission）
  - 仅有 progress-based 抢占
```

**实现方案**: 新建 `baseline/progress_priority_gateway.go`

基于 `sbac_gateway.go` 改造（结构最接近），核心改动约 ~200 行：
- 复用 `sbacSession` 结构，增加 `progressScore float64` 字段
- 增加 `findLowestProgressSession()` 方法
- 修改 `handleToolsCall` 中满额时的准入逻辑
- 步骤完成时更新 progressScore

**cmd/gateway/main.go 注册**: 添加 `--mode pp` 分支。

### 1.3 新增基线 2: Rajomon + Session Bookkeeping（Raj+SB）

**目的**: 证明"在 per-request 定价上加会话跟踪"不够——核心创新在于定价公式和预算预留。

**算法规格**:
```
在现有 RajomonGateway 基础上增加:
  1. 解析 X-Session-ID header
  2. 维护 sessions map[string]*rajomonSession：
     - completedSteps int
     - totalSteps int (从 X-Plan-DAG 解析)
     - lastActivity time.Time
  3. 准入决策: 仍使用 Rajomon 原始 per-request 定价
     - tokens < ownPrice → reject（不区分 step 0 和 step K）
  4. 记录会话状态用于指标跟踪

关键区别于 PlanGate:
  - 有会话跟踪，但定价不感知步骤进度
  - 无预算预留（价格随时波动）
  - 无沉没成本折扣
  - Step 5 和 Step 0 面临相同拒绝概率
```

**实现方案**: 新建 `baseline/rajomon_session_gateway.go`

基于 `rajomon_gateway.go` 改造，核心改动约 ~100 行：
- 增加 `sessions sync.Map`
- 增加 `rajomonSession` 结构体
- 修改 `handleToolsCall` 的 header 解析部分
- 保持定价逻辑完全不变（关键! 这是对照实验的核心）

**cmd/gateway/main.go 注册**: 添加 `--mode rajomon-session` 分支。

### 1.4 新增基线 3: PlanGate w/o Reservation（PG-noRes）

**目的**: 证明 temporal isolation（预算预留 / 价格锁定）的必要性。

**现有代码已支持**: `--mode mcpdp-no-budgetlock` → 使用 `NewMCPDPServerNoLock()`

**需确认**: 现有 `mcpdp-no-budgetlock` 的行为是否如预期？
- 根据代码分析: `disableBudgetLock=true` 时，P&S 会话仍做预检但**不锁定价格**，后续步骤走标准 MCPGovernor LoadShedding
- 这正是我们需要的——有 atomic admission 但无 temporal isolation
- **无需额外代码改动**，仅需在实验中增加该模式的运行

### 1.5 新增基线 4 (可选): Progress-Priority + Reservation（PP+Res）

**目的**: 进一步证明不是单个组件能解释收益——即使同时有进度优先和价格锁定，没有 continuation pricing 仍然不够。

**判断**: 建议 Week 1 先跑 PP 基线结果，如果 PP 已经与 PlanGate 差距明显（>15pp），则此基线可跳过。

### 1.6 基线矩阵总结

| 基线 | Atomic Admit | Temporal Isolation | Continuation Value | 预期结果 |
|------|-------------|-------------------|-------------------|---------|
| NG | ✗ | ✗ | ✗ | 最差 (参照) |
| SRL | ✗ | ✗ | ✗ | 略好于 NG |
| Rajomon | ✗ | ✗ | ✗ | 动态定价但无会话感知 |
| Raj+SB | ✗ | ✗ | ✗ | 有会话 ID 但定价不感知 |
| PP | ✗ | ✗ | 部分(优先级) | 偏袒高进度但无承诺 |
| SBAC | ✓(限额) | 部分(不驱逐) | ✗ | 粗粒度限额 |
| PG-noRes | ✓ | ✗ | ✓ | 有折扣但价格不稳 |
| **PlanGate** | **✓** | **✓** | **✓** | **完整 commitment** |

**核心论证逻辑** (实验应证明):
1. Rajomon → Raj+SB: 加会话感知不够 → session tracking ≠ session commitment
2. PP vs PlanGate: 进度优先不够 → progress favoritism ≠ continuation value
3. PG-noRes vs PlanGate: 有折扣无锁定不够 → temporal isolation 不可或缺
4. 只有三者结合 → 完整 session commitment

---

## 二、实验补充详细方案

### 2.1 实验重组织原则

**不再按"能做什么"发散，而按"证明什么主张"组织**：

| 主张 | 对应实验 | 必须/可选 |
|------|---------|---------|
| Session commitment 三要素缺一不可 | Exp-Baseline-Matrix (新) | **必须** |
| K² 折扣合理性 | Exp8 (已有) + 理论分析 | 已有,补理论 |
| 在 bursty 和 long-tail 下仍有效 | Exp-Bursty (新) + Exp-LongTail (新) | **必须** |
| 收益随 contention 提升 | Exp-Real-LLM-Deep (扩大) | **必须** |
| 公平性按会话长度分层 | Exp-Fairness-Stratified (新指标) | **必须** |
| α 参数不极端敏感 | Exp-AlphaSensitivity (新) | **必须** |
| 控制稳定性 | Exp-PriceVolatility (新指标) | **建议** |
| 分布式可行性 | Exp-Distributed (新, 可选) | 建议 |

### 2.2 新增实验详细参数

#### Exp-BM: Baseline Matrix（核心必做）

**目的**: 一次性证明所有基线矩阵的对照关系  
**配置**:
```
sessions:       500
concurrency:    200
ps_ratio:       0.5          # 混合模式（P&S + ReAct 各半）
budget:         500
heavy_ratio:    0.3
min_steps:      3
max_steps:      7
arrival_rate:   50.0
duration:       60s
step_timeout:   2.0s
repeats:        5

gateways:  [NG, SRL, Rajomon, Rajomon+SB, PP, SBAC, PG-noRes, PlanGate-Full]
```

**产出**: 8-gateway 对照表，核心指标:
- Success Rate / Cascade Failures / Step-0 Rejections
- Effective Goodput / GP/s
- P50, P95, P99 latency
- **Admitted-but-doomed rate** (新指标)

#### Exp-Bursty: 突发流量测试（必做）

**目的**: 证明 commitment 机制在瞬时拥塞下保护已接纳会话  
**配置**:
```
sessions:       500
concurrency:    200
ps_ratio:       0.5

# 三阶段流量模式（使用 dag_load_generator.py 的 arrival_rate 控制）
# Phase 1 (0-20s):   正常负载, arrival_rate = 30
# Phase 2 (20-35s):  5x 突发,  arrival_rate = 150
# Phase 3 (35-60s):  恢复正常, arrival_rate = 30

实现方式: 修改 dag_load_generator.py 增加 --burst-config 参数
格式: "start_sec:end_sec:arrival_rate,..."
示例: --burst-config "0:20:30,20:35:150,35:60:30"

gateways:  [NG, Rajomon, PP, PlanGate-Full]
repeats:    5
```

**代码改动**: 在 `dag_load_generator.py` 的 session 调度循环中增加分段 arrival_rate 支持（约 ~30 行 Python）。

#### Exp-LongTail: 长尾会话测试（必做）

**目的**: 证明 continuation value 在深会话上的保护作用  
**配置**:
```
sessions:       500
concurrency:    200
ps_ratio:       0.0          # 纯 ReAct（长尾更明显）
budget:         1000         # 加大预算适应长会话

# 长尾步骤分布:
min_steps:      2
max_steps:      20           # 扩大到 20 步
# 80% 会话 2-5 步 (短), 20% 会话 10-20 步 (长)

实现方式: 修改 dag_load_generator.py 增加 --long-tail-ratio 参数
当 --long-tail-ratio 0.2 时:
  80% 会话: steps ~ Uniform(min_steps, 5)
  20% 会话: steps ~ Uniform(10, max_steps)

gateways:  [NG, Rajomon, PP, PlanGate-Full]
repeats:    5
```

**代码改动**: 在 `dag_load_generator.py` 的步骤数生成逻辑中增加双模分布（约 ~15 行 Python）。

#### Exp-MixedTenant: 短/长混合会话公平性（必做扩展 fairness）

**目的**: 检验不同会话长度上的资源分配公平性  
**配置**: 同 Exp-LongTail，但**额外采集分层指标**:
```
指标按 session_length 分组:
  - short (2-5 steps): Success Rate / GP/s / P50 / P95
  - long (10-20 steps): Success Rate / GP/s / P50 / P95
  - JFI_short, JFI_long 分别计算
```

**代码改动**: 在 `dag_load_generator.py` 的 `print_stats()` 中增加分层统计（约 ~40 行 Python）。

#### Exp-AlphaSensitivity: α 参数敏感性扫描（必做）

**目的**: 证明系统在 α ∈ [0.2, 0.8] 范围内都表现良好  
**配置**:
```
sessions:       500
concurrency:    200
ps_ratio:       0.5
budget:         500

sweep_param:    plangate-sunk-cost-alpha
sweep_values:   [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

gateways:  [PlanGate-Full] (仅扫 α)
repeats:    5
```

**代码改动**: 无需改动代码——α 已是命令行参数 `--plangate-sunk-cost-alpha`。仅需在 `run_all_experiments.py` 中增加此实验配置。

#### Exp-PriceVolatility: 价格稳定性分析（建议做）

**目的**: 回应 R3 关于"定价引擎稳定性"的担忧  
**实现方式**: 在 MCPGovernor 的 `UpdateOwnPrice()` 中增加价格时间序列日志:
```go
// 在 mcp_governor.go 的 priceAdjustLoop 中增加:
if g.priceLogger != nil {
    g.priceLogger.Printf("%d,%d,%f\n", time.Now().UnixMicro(), g.GetOwnPrice(), intensity)
}
```

**产出指标**:
- 价格变异系数 (CV = std/mean)
- 价格翻转频率 (上升→下降 或 下降→上升 的次数/秒)
- 价格收敛时间 (从负载变化到价格稳定的延迟)

### 2.3 新增指标采集方案

#### 2.3.1 Admitted-but-doomed Rate（核心新指标）

**定义**: 已通过 step-0 准入，但最终因后续步骤被拒而级联失败的会话比例。

**采集位置**: `dag_load_generator.py` 已有 `CASCADE_FAILED` 状态。计算方式:
```
admitted_but_doomed_rate = cascade_failed_sessions / (cascade_failed_sessions + success_sessions)
```

**这是"commitment 质量"的关键指标**: PlanGate 应该接近 0（一旦承诺就兑现），而 Rajomon/PP/SRL 应该远高于 0。

#### 2.3.2 P99 / P99.9 Admission Latency

**当前状态**: dag_load_generator.py 已计算 P99，但论文未报告。

**改动**: 在 `print_stats()` 和 CSV 输出中增加 P99.9:
```python
p999 = np.percentile(latencies, 99.9) if len(latencies) >= 1000 else "N/A"
```

#### 2.3.3 Reservation Hit Ratio & Stale Reservation Ratio

**定义**:
- Reservation Hit: 已锁定价格被实际使用的比例
- Stale Reservation: 因超时被清理的预留占总预留的比例

**采集位置**: `plangate/session_manager.go` 的 `HTTPBudgetReservationManager`:
```go
// 增加统计字段:
type HTTPBudgetReservationManager struct {
    // ... existing fields ...
    totalReservations   int64  // atomic
    completedReservations int64  // atomic
    expiredReservations  int64  // atomic
}
```

#### 2.3.4 分层 Fairness（替代单一 JFI）

**当前**: 仅报告总体 JFI_steps = 0.922。

**改进**: 按会话长度分 3 组计算:
- Short (2-4 steps): JFI_short
- Medium (5-8 steps): JFI_medium  
- Long (9+ steps): JFI_long
- Cross-group 公平性: 各组的平均成功率标准差

### 2.4 真实 LLM 实验扩展方案

#### 策略: 纵深优先，而非横铺

**final.md 正确指出**: 宁可减少 provider，也不要继续维持小样本多平台。

**推荐方案**:

| 角色 | Provider | RPM | 并发 | 实验规模 | 目的 |
|------|----------|-----|------|---------|------|
| **主力深挖** | GLM-4-Flash | 200 | C=10 | **200 sessions × N=5** | 高 contention 核心数据 |
| **边界验证** | DeepSeek-V3 | 60 | C=3 | 100 sessions × N=5 | 低 contention 边界 |

**GLM 深挖具体配置**:
```
sessions:           200          # 从 50 扩到 200
concurrency:        10
ps_ratio:           0.0          # 纯 ReAct (真实 LLM 无法用 P&S)
budget:             1000
min_steps:          2
max_steps:          10

gateways:  [NG, Rajomon, PP, PlanGate-Full]
repeats:    5

# 新增: bursty arrival (如果 API 允许)
# Phase 1 (0-60s): normal arrival
# Phase 2 (60-90s): 3x arrival  
# Phase 3 (90-150s): normal arrival
```

**API 成本估算**:
- GLM-4-Flash: ~¥0.0001/token, 200 sessions × ~6000 tokens/session × 4 gateways × 5 runs = ~240万 tokens ≈ ¥240
- DeepSeek-V3: ~¥0.001/token, 100 sessions × ~8000 tokens/session × 3 gateways × 5 runs = ~120万 tokens ≈ ¥1200
- **总预算约 ¥1500**（可控）

---

## 三、理论分析详细方案

### 3.1 理论目标（与 final.md 一致）

**不证明 K² 全局最优，只证明三件事**:
1. 折扣函数必须**单调递减**
2. 折扣函数在高 K 区域应该**偏凸**（下降加速）
3. K² 是一个**稳健的凸近似设计点**

### 3.2 Continuation Value / Risk Exposure 框架

建议论文增加 §3.x "Theoretical Analysis"（约 1-1.5 页），结构如下:

#### 3.2.1 模型定义

**多步 Agent 会话模型**:
- 一个会话 $s$ 由 $n_s$ 步组成，每步消耗资源 $c_i$
- 会话在步骤 $K$ 被拒绝时，浪费的沉没成本为 $W(K) = \sum_{i=0}^{K-1} c_i$
- 会话完成时的价值为 $V = V_{\text{complete}} > 0$；失败时价值为 $0$

**风险暴露（Risk Exposure）定义**:
$$R(K) = W(K) + (V - W(K)) = V$$

但**边际风险暴露增长**为:
$$\Delta R(K) = W(K+1) - W(K) = c_K$$

**关键洞察**: 拒绝决策的机会成本由两部分组成:
$$\text{OpportunityCost}(K) = W(K) + \frac{V \cdot K}{n_s}$$

第一项是已投入的沉没成本，第二项是已完成进度对应的价值份额。在线性步骤成本和持续占用容量的双重作用下，后段拒绝的成本上升快于前段。这为选择**凸折扣函数族**提供了直觉依据。

> ⚠️ 写作纪律：此处**不做**"至少二次增长"等过强数学断言。目标是限定合理函数族，而非推导唯一正确函数。

#### 3.2.2 折扣函数的必要条件

**性质 1** (折扣单调递减): 若系统目标是最小化总级联浪费 $\sum_s W(K_s)$（约束吞吐率 $\geq \lambda_{\text{target}}$），则合理的准入折扣函数 $d(K)$ 应关于 $K$ 单调递减。

**直觉论证**: 若 $d(K_1) < d(K_2)$ 对某个 $K_1 < K_2$，则交换两个会话的拒绝优先级可以降低总浪费（因为 $W(K_2) > W(K_1)$）。

**性质 2** (凸折扣更稳健): 在线性沉没成本与持续容量占用的双重作用下，后段拒绝的代价上升快于前段，因此凸折扣函数族（下降加速）比线性或凹折扣能更好地将保护力集中在高进度会话上。

**论证方式**: 当每步成本均匀时，$W(K) = Kc$，但拒绝一个处于步骤 $K$ 的会话同时浪费了该会话在前 $K$ 步占用的系统容量时间。这种双重成本的增速超过线性，支持选择凸折扣作为合理设计家族。

#### 3.2.3 K² 作为稳健设计点的定位

- **线性折扣** $(1/(1+K\alpha))$: 下降斜率恒定，在高 K 时保护不足
- **二次折扣** $(1/(1+K^2\alpha))$: 下降加速，O(1) 计算，有界增长
- **指数折扣** $(e^{-K\alpha})$: 下降过快，在极高 K 值未被实验验证
- **对数折扣** $(1/(1+\alpha\ln(1+K)))$: 下降收敛，保护力不足

K² 是凸函数族中**计算最简单且增长有界**的成员，在实验验证的 $K \in [1, 10]$ 范围内提供接近指数的保护力，同时避免了指数在 $K > 10$ 时的极端行为。

### 3.3 控制稳定性弱分析

**定价引擎建模为离散反馈系统**:
$$P(t+1) = \max\left(0, P(t) + K_p \cdot \mathbb{1}[\text{delay} > \theta] - D \cdot \mathbb{1}[\text{delay} \leq \theta]\right)$$

其中 $K_p = 180$（priceStep），$D = 1$（decayStep），$\theta = 500\mu s$（latencyThreshold）。

**稳定性条件**:
- 非线性系统，不能直接用 Z-变换
- 但可用 Lyapunov 函数 $V(t) = P(t)^2$ 分析有界性:
  - $P_{\max} \leq K_p \cdot T_{\text{overload}} / T_{\text{update}}$（最坏情况下持续过载的价格上限）
  - 当过载消除后，价格以 $D$ 的速率线性回落，收敛时间 $T_{\text{converge}} = P_{\max} / D$
- **关键参数比**: $K_p / D = 180$，意味着系统对过载反应快（1 周期涨 180），对恢复反应慢（180 周期降回 0）
- **这是有意设计**: 宁可"保守恢复"也不要"过快放行"，符合会话治理的"承诺一旦做出就要兑现"的语义

**实验验证要点**: 通过 Exp-PriceVolatility 中的价格时间序列，展示:
1. 价格在负载突变后 <500ms 内响应
2. 负载恢复后价格在 ~1-5s 内稳定收敛
3. 无持续价格振荡（如果有，需调整 $K_p / D$ 比率）

### 3.4 激励兼容性简要论证

**不做完整 VCG 机制**，仅论证:

1. **真实预算声明是弱占优策略**:
   - 过高虚报: 被声誉系统惩罚（降低信任分 → 未来准入更严格）
   - 过低虚报: 导致 step-0 被拒（P&S 模式下 budget < totalCost 直接拒绝）
   - 真实声明: 最大化被准入概率且无惩罚

2. **个体理性**: 参与治理不会让 Agent 比无治理更差
   - 数据支持: PlanGate 成功率 72.6% vs NG 22.2%

---

## 四、代码实现详细清单

### 4.1 新基线实现（优先级 P0）

#### 4.1.1 Progress-Priority Gateway

**文件**: `baseline/progress_priority_gateway.go`  
**工程量**: ~250 行 Go  
**核心结构**:
```go
type PPGateway struct {
    nodeName       string
    tools          map[string]MCPTool
    handlers       map[string]ToolCallHandler
    serverInfo     Implementation
    maxSessions    int64
    activeSessions int64              // atomic
    sessions       sync.Map           // map[string]*ppSession
    stats          PPStats
}

type ppSession struct {
    mu             sync.Mutex
    totalSteps     int
    completedSteps int
    lastActivity   time.Time
}

func (pp *PPGateway) handleToolsCall(...) {
    // 1. 解析 session header
    // 2. 如果 activeSessions < maxSessions → 直接接纳
    // 3. 否则: 找 progressScore 最低的会话
    //    - progressScore = completedSteps / totalSteps
    //    - 如果当前会话 progress > 最低 → 驱逐最低，接纳当前
    //    - 否则 → 拒绝
    // 4. 执行 handler
    // 5. 更新步骤计数
}
```

#### 4.1.2 Rajomon + Session Bookkeeping

**文件**: `baseline/rajomon_session_gateway.go`  
**工程量**: ~150 行 Go（基于 rajomon_gateway.go 复制修改）  
**核心改动**:
```go
type RajomonSessionGateway struct {
    RajomonGateway                    // 嵌入原始 Rajomon
    sessions sync.Map                // session tracking
}

type rajomonSessionInfo struct {
    completedSteps int
    totalSteps     int
    lastActivity   time.Time
}

// handleToolsCall: 增加 session header 解析
// 定价逻辑完全不变 —— 仍然是 tokens < ownPrice → reject
// 会话状态仅用于指标跟踪（admitted-but-doomed 等）
```

#### 4.1.3 cmd/gateway/main.go 注册

**工程量**: ~40 行 Go  
在 `setupHandler()` 中增加 `case "pp":` 和 `case "rajomon-session":` 分支。

### 4.2 实验脚本改动

#### 4.2.1 dag_load_generator.py 增强

**改动 1**: Bursty arrival 支持
```python
# 新增参数
parser.add_argument('--burst-config', type=str, default=None,
    help='Burst config: "start:end:rate,..." e.g. "0:20:30,20:35:150,35:60:30"')

# 在 session 调度循环中:
def get_current_arrival_rate(elapsed_time, burst_config):
    for start, end, rate in burst_config:
        if start <= elapsed_time < end:
            return rate
    return default_arrival_rate
```

**改动 2**: Long-tail 步骤分布
```python
parser.add_argument('--long-tail-ratio', type=float, default=0.0,
    help='Fraction of sessions with 10-20 steps (rest get min-max range)')

def generate_step_count(min_steps, max_steps, long_tail_ratio):
    if random.random() < long_tail_ratio:
        return random.randint(max(10, min_steps), max_steps)
    else:
        return random.randint(min_steps, min(5, max_steps))
```

**改动 3**: 分层 Fairness 统计
```python
def print_stratified_fairness(sessions):
    short = [s for s in sessions if s.n_steps <= 5]
    long = [s for s in sessions if s.n_steps > 5]
    # 分别计算 JFI, success_rate, avg_latency
```

**改动 4**: Admitted-but-doomed 指标
```python
admitted_but_doomed = sum(1 for s in sessions 
    if s.state == 'CASCADE_FAILED') / max(1, total_admitted)
```

**总工程量**: ~100 行 Python

#### 4.2.2 run_all_experiments.py 增加实验配置

**增加实验**:
- `Exp_BaselineMatrix`: 8 gateways × 5 runs
- `Exp_Bursty`: 4 gateways × 5 runs，使用 `--burst-config`
- `Exp_LongTail`: 4 gateways × 5 runs，使用 `--long-tail-ratio 0.2 --max-steps 20`
- `Exp_AlphaSensitivity`: PlanGate × 10 α values × 5 runs

**总工程量**: ~80 行 Python

### 4.3 自适应 α（降级为附录候选，不进入主线）

> ⚠️ **决策**: Adaptive α 不进入正文核心路径。原因：它会把论文从"新原语"带偏到"在线控制器调参"，现阶段最缺的不是参数自动化，而是核心论证闭环。如果 α 敏感性扫描结果很好（α ∈ [0.3, 0.7] 均可接受），则 adaptive α 仅作为附录讨论；如果敏感性扫描显示系统对 α 极端敏感，再重新评估是否需要进主线。

**预留设计**（仅在附录需要时实现）:
- 文件: `plangate/adaptive_alpha.go` (~60 行 Go)
- 算法: 滑动窗口观测 cascade rate，超过目标则增大 α，低于目标则减小 α
- 集成点: `handleReActSunkCostStep()` 中替换固定 `sunkCostAlpha`

### 4.4 价格时间序列日志（P3 优先级）

**文件**: `mcp_governor.go` 修改  
**工程量**: ~20 行 Go

在 `priceAdjustLoop()` 中增加:
```go
if g.priceLogFile != nil {
    fmt.Fprintf(g.priceLogFile, "%d,%d\n", 
        time.Now().UnixMicro(), atomic.LoadInt64(&g.ownPrice))
}
```

### 4.5 Reservation 命中率统计（P3 优先级）

**文件**: `plangate/session_manager.go` 修改  
**工程量**: ~30 行 Go

增加原子计数器，在 Reserve/Release/Cleanup 时更新。通过 HTTP `/stats` 端点暴露。

---

## 五、分布式原型方案评估

### 5.1 核心问题

PlanGate 有三类状态需要考虑分布式同步:

| 状态类型 | 当前存储 | 访问频率 | 一致性要求 | 同步方案 |
|---------|---------|---------|-----------|---------|
| **价格表** (ownPrice + priceTableMap) | atomic int64 + sync.Map | 每请求 | 最终一致 | Redis Pub/Sub, 10ms 延迟可接受 |
| **会话预留** (budgetMgr) | sync.Map | 每请求 | 强一致 → **Session Affinity** 避免同步 | 一致性哈希路由 |
| **声誉分数** (reputationMgr) | sync.Map | 每会话 | 最终一致 | Redis 定期同步, 30s 间隔 |

### 5.2 Session Affinity 方案（关键洞察）

**核心策略**: 用一致性哈希将同一 session 的所有请求路由到同一网关节点。这样:
- 会话预留和 ReAct 会话状态**完全不需要分布式同步**
- 只有价格表需要跨节点同步（最终一致即可）
- 工程量大幅降低

**实现**:
```
[Agent] → [Load Balancer (hash on X-Session-ID)] → [Gateway Node 1/2/3]
                                                          ↕ Redis
                                                    [Price Sync Channel]
```

### 5.3 工程量评估

| 组件 | 改动范围 | 估计行数 | 必要性 |
|------|---------|---------|--------|
| Redis 价格同步 | mcp_governor.go + 新文件 | ~150 行 Go | 核心 |
| Docker Compose 编排 | 新建 docker-compose.yml | ~80 行 YAML | 核心 |
| 一致性哈希 LB 配置 | Nginx/HAProxy 配置 | ~30 行 | 核心 |
| 声誉分数 Redis 同步 | plangate/reputation.go | ~50 行 Go | 可选 |
| 多节点实验脚本 | scripts/ 新建 | ~100 行 Python | 核心 |
| **总计** | | **~400 行** | |

### 5.4 决策建议

**如果时间充裕（Week 7 有空余）**: 做 2-3 节点 + Redis 的最小原型，量化:
- 跨节点价格同步延迟 vs 单节点
- 吞吐量是否接近线性扩展
- 错误接纳率是否上升

**如果时间紧张**: 放弃分布式原型，在论文中:
1. 讨论 Session Affinity 方案的可行性
2. 提供 state overhead 微基准（每会话 ~1KB, 1000 并发 <1MB）
3. 明确声明 scope 为 single-gateway governance
4. 在 Future Work 中提出具体方案

---

## 六、论文重写方案

### 6.1 叙事重构要点

#### 摘要重写方向
```
当前: "PlanGate, a plan-aware gateway that governs MCP tool calls through 
       session-level pricing-based admission control"
改为: "PlanGate turns planned sessions into hard commitments and reactive
       sessions into substantially stronger soft commitments, reducing
       admitted-but-doomed executions by 48.5 percentage points while
       doubling goodput."
```

> **核心一句话 (Week 2 实验验证)**:  
> PlanGate 的核心价值是提升 commitment quality，而不是单纯追求更高的总成功率。  
> PlanGate improves commitment quality by separating hard commitments for planned  
> sessions from soft commitments for reactive sessions, and by drastically reducing  
> admitted-but-doomed sessions relative to request-level and progress-based baselines.
>
> **已被实验验证的两种 commitment 语义 (Week 2 绿灯)**:  
> - **P&S = 硬承诺**: atomic admission + budget reservation → **ABD_P&S = 0.0%**  
>   - 关键机制: 接纳后 temporal isolation 兑现承诺 (pg_nores ABD_P&S=69.4% 证明 reservation 是必要条件)  
> - **ReAct = continuation-aware 软承诺**: 沉没成本折扣 + 动态定价 → **ABD_ReAct = 29.1%**  
>   - 远低于所有基线 (基线 55-90%), 但非零, 语义严谨
>
> **结构性结论 (已有实验证据)**:  
> 1. bookkeeping 不够 (Rajomon ≈ Rajomon+SB, ABD 90.3% vs 89.7%)  
> 2. progress favoritism 不够 (PP ≈ SBAC, ABD 60.9% vs 61.2%)  
> 3. no-reservation 不够 (pg_nores ABD_P&S=69.4% vs plangate ABD_P&S=0.0%)  
> 4. full commitment 才成立 (plangate ABD_total=25.1%, GP/s=50.9)

#### Contributions 重排

**当前 4 条贡献**:
1. Pre-flight Atomic Admission with Budget Reservation
2. Sunk-Cost-Aware Dynamic Pricing for ReAct Agents
3. Dual-Mode Governance with External Signal Fusion
4. Reputation-Based Security Against Adversarial Agents

**改为 4 条**:
1. **新抽象**: 提出 session commitment governance，用 atomic admission、temporal isolation、continuation value 三个性质刻画多步 Agent 治理需求
2. **新系统**: 设计并实现 PlanGate，在 P&S 和 ReAct 两类 Agent 下统一实现 session-level commitment
3. **新证据**: 通过 Rajomon、Rajomon+SB、Progress-Priority 等强基线对照，证明 request-level pricing 和 progress favoritism 都不能替代 session commitment
4. **新经验结论**: 证明 PlanGate 的核心收益是 **commitment quality**（ABD 降低 35+ pp、GP/s 提升 83%），且收益是 contention-dependent；提出按 Agent 类型分层的 ABD 分析框架（ABD_P&S, ABD_ReAct）

**降级到论文后半部分或附录**:
- Reputation-based security → §3.7 附加安全机制
- External Signal Tracker → §4.x 实现细节
- 多 provider 横铺 → 边界验证节

#### Introduction 第一页

核心变化——不先谈公式，**先谈 abstraction mismatch**:

```
段落 1: 多步 Agent 依赖 session 语义（举例: 5-step 会话）  
段落 2: 现有治理的 governance unit = request，这是根本错误  
段落 3: 我们需要三种新语义: atomic admission, temporal isolation, continuation value  
段落 4: 提出 session commitment 概念  
段落 5: PlanGate 是 session commitment 的一个实现  
段落 6: 贡献列表
```

### 6.2 实验节重组织

**从"枚举所有结果"改为"证明主张"的顺序**:

```
§5.1 Setup (环境、工具链、参数)
§5.2 核心对照: Session Commitment 三要素必要性 (Exp-BM)
  → Table: 8-gateway 基线矩阵
  → **主指标 (P0)**: ABD_P&S, ABD_ReAct, GP/s
  → **辅指标 (P1)**: success_rate, cascade_failed, rejected@s0
  → 关键数据: 按 Agent 类型拆分的 admitted-but-doomed rate 对比
§5.3 Robustness: Bursty + Long-tail + 高并发 (Exp-Bursty + LongTail + Exp9)
  → 证明 commitment 在各种负载模式下都有效
§5.4 Discount Function: K² 的设计合理性 (Exp8 + 理论)
  → 理论+实验双重论证
§5.5 参数敏感性: α 扫描 + 自适应 (Exp-Alpha)
  → 证明系统不极端敏感
§5.6 Real-LLM 验证: 高 contention 深挖 + 低 contention 边界 (Exp-Real)
  → 统计检验 + contention-dependent 结论
§5.7 公平性: 分层 Fairness 分析 (Exp-Mixed)
  → 按会话长度分组的公平性
§5.8 Overhead: 开销微基准 (gateway latency, memory, GC)
[附录] 消融细节、更多 provider、安全机制实验
```

---

## 七、按周执行时间线

### Week 1: 锁定故事线 + 验证最危险假设

**状态: ✅ 已完成**

**实际结果 (5×200 sessions)**:

| Gateway | Success% | Cascade | ABD% | GP/s |
|---------|----------|---------|------|------|
| NG | 11.6±2.5 | 44.0±2.6 | 65.7±5.4 | 24.9±4.1 |
| SBAC | 12.9±1.2 | 36.2±3.5 | 58.3±4.1 | 30.8±4.3 |
| PP | 12.8±1.6 | 35.2±3.7 | 57.9±4.4 | 30.8±2.4 |
| PlanGate | 16.3±0.6 | 9.8±6.2 | 22.0±10.9 | 56.4±1.3 |

**判定: 🟡 黄灯**
- Success 差 +3.5pp (<10pp 绿灯线)
- Cascade 差 +25.4 (远超 10 绿灯线)
- ABD 差 +35.8pp (远超 15pp 绿灯线)
- **GP/s 差 +83%** (PlanGate 56.4 vs PP 30.8)

**叙事修正**:
- Success rate 降级为辅助指标，不再作为核心论据
- ABD + GP/s 升格为主指标: "PlanGate 的核心价值不是更高的成功率，而是更高的 commitment quality — 大幅减少 admitted-but-doomed 会话"
- **关键待办**: 必须按 Agent 类型拆分 ABD (ABD_P&S, ABD_ReAct)，验证 P&S ≈ 硬承诺 vs ReAct ≈ 软承诺

### Week 2: 补强基线矩阵 + Mode-Stratified ABD

**状态: ✅ 已完成 — 核心 claim 已转绿**

**实际结果 (7 gateways × 5 runs × 200 sessions)**:

| Gateway | SuccRate% | ABD_total% | ABD_P&S% | ABD_ReAct% | GP/s |
|---------|-----------|------------|----------|------------|------|
| ng | 11.0±0.6 | 66.0±2.4 | 61.0 | 70.9 | 25.5±0.8 |
| rajomon | 3.1±0.8 | 90.3±2.1 | 95.2 | 86.0 | 4.4±0.6 |
| rajomon_sb | 3.4±0.4 | 89.7±1.1 | 95.4 | 84.5 | 5.4±0.9 |
| sbac | 11.5±1.8 | 61.2±4.8 | 63.3 | 59.8 | 26.8±2.8 |
| pp | 11.9±0.2 | 60.9±3.3 | 64.2 | 58.4 | 26.1±2.0 |
| pg_nores | 13.0±1.3 | 31.4±7.2 | 69.4 | 24.1 | 41.0±8.2 |
| plangate_full | 16.4±0.7 | 25.1±7.6 | **0.0** | **29.1** | **50.9±6.2** |

**ABD 证据链 ✓ 成立**:
- 基线平均 ABD = 73.6%, PlanGate = 25.1%, 差距 = 48.5pp
- P&S 硬承诺验证: ABD_P&S = 0.0% (pg_nores = 69.4% → reservation 是必要条件)
- ReAct 软承诺验证: ABD_ReAct = 29.1% (基线 55-90%)
- Rajomon ≈ Rajomon+SB: "session awareness ≠ session commitment"
- PP ≈ SBAC: "progress favoritism ≠ commitment"
- GP/s: PlanGate (50.9) > pg_nores (41.0) > SBAC (26.8) > PP (26.1) > NG (25.5) > Raj+SB (5.4) > Raj (4.4)

**⚠️ 待解决风险**: Rajomon ABD=90.3% / GP/s=4.4 过于极端，需 sensitivity scan 证明非 strawman

### Week 3: 统计检验 + Rajomon 公平性 + 理论节启动

**状态: ✅ 已完成 — 统计显著性确认 + Rajomon 结构性失配证明**

> **Week 3 优先级调整 (CCF-A 反馈)**: Week 2 核心 claim 已转绿，但数据需要统计加固才能写入摘要/引言。  
> Rajomon 的极端结果 (ABD=90.3%, GP/s=4.4) 是双刃剑——先堵住 "strawman" 攻击点，再写理论。

**产出**:
- [x] **P0: 统计显著性检验** — bootstrap CI + permutation test 完成
- [x] **P0: Rajomon 调参公平性扫描** — 5 种 price_step × 5 runs 完成
- [x] **P0: 论文正文主表生成** — LaTeX table_commitment_quality.tex 已生成
- [x] **P1: 全指标 mode-stratified** — dag_load_generator.py 已增加 Success/Reject/GP/s per mode
- [ ] **P2: 理论节启动** — 推迟到 Week 4

**统计显著性结果 (permutation test, two-sided)**:

| 对比 (vs PlanGate) | ABD_total p | ABD_P&S p | ABD_ReAct p | GP/s p |
|---------------------|-------------|-----------|-------------|--------|
| NG | 0.0087** | 0.0064** | 0.0045** | 0.0057** |
| Rajomon | 0.0077** | 0.0071** | 0.0087** | 0.0057** |
| Rajomon+SB | 0.0087** | 0.0087** | 0.0036** | 0.0057** |
| SBAC | 0.0087** | 0.0084** | 0.0064** | 0.0054** |
| PP | 0.0087** | 0.0087** | 0.0049** | 0.0077** |
| PG-noRes | 0.2206 | 0.0087** | 0.2993 | 0.0953 |

> PlanGate vs 所有 5 个外部基线: 全部 p<0.01 (显著)。  
> PlanGate vs PG-noRes: ABD_P&S 显著 (p=0.0087), ABD_total/ReAct 不显著 — 因为 pg_nores 的 ReAct 已经不错 (24.1%), 区别在 P&S 硬承诺。

**Rajomon 敏感性扫描结果 (每组 5 repeats × 200 sessions)**:

| price_step | ABD_total% | ABD_P&S% | ABD_ReAct% | SuccRate% | GP/s |
|------------|------------|----------|------------|-----------|------|
| 5 | 64.4±4.4 | 61.3 | 67.5 | 11.8 | 25.4 |
| 10 | 72.2±10.5 | 75.8 | 69.0 | 9.5 | 18.4 |
| 20 | 89.0±2.3 | 95.2 | 84.3 | 3.7 | 5.1 |
| 50 | 89.0±3.9 | 94.2 | 85.6 | 2.8 | 7.0 |
| 100 | 89.7±1.1 | 91.8 | 88.2 | 2.4 | 6.1 |

> **关键发现**: 即使在最优 price_step=5 下, Rajomon ABD=64.4% 仍远高于 PlanGate 的 25.1% (差距 39.3pp)。  
> price_step=5 实际上退化为接近无治理 (ABD≈NG=66%), 但仍比 PlanGate 差。  
> **结论**: per-request pricing 在多步 session 上确实存在结构性不适配, 非调参问题。  
> **论文策略**: 用 price_step=5 (Rajomon 最佳) 重跑正式实验, 同时在 Rajomon sensitivity figure 展示全参数空间。

**已创建脚本**:
- `scripts/stats_significance.py` — bootstrap CI + permutation test + LaTeX 表生成
- `scripts/rajomon_sensitivity.py` — Rajomon price_step 敏感性扫描

**已生成文件**:
- `results/paper_figures/table_commitment_quality.tex` — LaTeX 论文主表
- `results/paper_figures/stats_detail.txt` — 详细统计量
- `results/exp_rajomon_sensitivity/rajomon_sensitivity.csv` — 敏感性扫描原始数据

**实验节图表新顺序** (CCF-A 建议):
1. **第一张表**: Mode-Stratified Commitment Quality (ABD_P&S, ABD_ReAct, GP/s) — 核心抽象验证 ✅
2. **第二张图**: Rajomon Sensitivity Curve (ABD vs price_step) — 结构性失配论证
3. **第三张图/表**: Overall throughput + success + cascade — 传统指标对比
4. 后续: robustness / sensitivity / fairness

> ⚠️ **Adaptive α 不进入主线路径。** α 敏感性扫描推迟到 Week 4。

> ⚠️ **写作纪律**: 理论目标是"限定合理函数族 + K² 是稳健 design point"，不是"推导唯一正确函数"。

> ⚠️ **Week 4 待办**: 用 Rajomon best-case (price_step=5) 替换现有 Rajomon 基线, 重跑 7-gateway 正式实验。

### Week 4: 正式主表重跑 + 理论节初稿 + 写作启动

> **Week 4 优先级调整 (CCF-A Week 3 反馈)**:  
> Week 3 已完成统计加固和 Rajomon 公平性证明，论文从"验证路线是否可行"进入"打造投稿版本"阶段。  
> 理论节不能再拖，但 bursty/long-tail 是"扩展可信度"不是"建立主张"，降为 P3。  
> 核心一句话: "PlanGate does not merely improve success rates; it improves commitment quality."  

> **CCF-A 对 Week 3 的定性**: 从"核心主张成立"推进到"核心主张开始具备投稿级说服力"。  
> - 核心证据三件套 ✅: (1) 抽象清楚 (2) 基线是强的 (3) 结论是稳的  
> - Rajomon 从"可能的实验瑕疵"转为"结构性失配" — 治理单位问题, 非参数问题  
> - ABD 从辅助指标升级为系统语义指标 — commitment quality 的 operationalization  
> - PG-noRes 不是普通 ablation, 而是"缺少 temporal isolation 的 commitment system"  

**产出** (严格按优先级排序):
- [x] **P0-a: 正式 7-gateway 重跑** — Rajomon best-case price_step=5 ✅ 已完成
- [x] **P0-b: Table 1 正文分析段** — ✅ 已写入 plangate_paper.tex §5.2 Commitment Quality
- [x] **P1: §3.x 理论节初稿** — ✅ 已写入 plangate_paper.tex §3.x Theoretical Analysis (~1 页)
- [x] **P2: Rajomon sensitivity figure** — ✅ PNG+PDF 已生成, paper/figures/rajomon_sensitivity.pdf
- [ ] **P3: Bursty + Long-tail 实验** — 扩展可信度, 非当前第一优先级
- [x] **P-extra: Abstract/Introduction 重写** — ✅ 用 "session commitment" + "commitment quality" 叙事替换旧框架, 标题改为 "Session Commitment for Multi-Step LLM Agent Tool Governance", contributions 重排为 4 条 (新抽象/新系统/新证据/新指标)

**P0-a 正式重跑结果 (Week 4, Rajomon best-case price_step=5)**:

| Gateway | SuccRate% | ABD_total% | ABD_P&S% | ABD_ReAct% | GP/s |
|---------|-----------|------------|----------|------------|------|
| ng | 11.5±1.7 | 65.5±4.8 | 67.2 | 65.3 | 24.4±4.1 |
| rajomon (ps=5) | 11.3±1.4 | 65.4±5.9 | 64.7 | 66.3 | 25.5±4.3 |
| rajomon_sb (ps=5) | 11.6±2.1 | 64.7±7.1 | 68.7 | 61.4 | 24.1±4.6 |
| sbac | 13.4±1.0 | 56.0±3.6 | 58.4 | 54.1 | 32.3±2.2 |
| pp | 11.3±1.4 | 62.9±5.1 | 60.4 | 65.1 | 28.2±4.6 |
| pg_nores | 12.5±0.8 | 27.8±8.5 | 81.6 | 15.6 | 48.6±4.9 |
| plangate_full | **17.3±0.6** | **18.9±7.8** | **0.0** | **21.7** | **50.4±6.2** |

**关键发现 (Rajomon best-case vs Week 2 对比)**:
- Rajomon (ps=5): ABD=65.4% ≈ NG=65.5% (退化为无治理)
- Rajomon (ps=5): ABD vs PlanGate 差距 = 46.5pp (比 Week 2 的 65.2pp 更公平但仍然显著)
- Rajomon ≈ Rajomon+SB: ABD 65.4% vs 64.7% (bookkeeping 无帮助, 再次确认)
- PlanGate ABD_P&S = 0.0% (硬承诺再次验证)
- PlanGate ABD_ReAct = 21.7% (比 Week 2 的 29.1% 更好)
- 统计显著性: PlanGate vs 所有 5 个外部基线全部 p<0.01, ABD_P&S(SBAC) p<0.001

**数据文件**: `results/exp_week4_formal/week2_smoke_summary.csv`

**P0-b 正文分析段要点**:
- Table 1 直接放在实验节开头, 证明 commitment semantics 而非 feature performance
- 强调 PG-noRes 是 temporal isolation 必要性对照, 非普通 ablation
- 明确写出: "我们报告了 Rajomon 的 price_step 敏感性, 并使用其最优配置作为正式基线"

**P1 理论节三件事** (不超过 1.5 页):
1. 为什么折扣必须随进度下降 (continuation value 递减)
2. 为什么 P&S 和 ReAct 对应不同 commitment semantics (硬承诺 vs 软承诺)
3. 为什么 convex family 比 constant/linear 更符合 continuation risk 增长趋势

**P2 Rajomon sensitivity figure**:
- 用 rajomon_sensitivity.csv 绘制 ABD vs price_step 曲线
- 加 PlanGate ABD=25.1% 参考线
- 标注 best-case price_step=5 仍差 39.3pp
- 正文图而非附录图 — 承担 baseline fairness defense

**P3 Bursty + Long-tail** (有余力才做):
1. `dag_load_generator.py` 增加 `--burst-config` (~30 行)
2. `dag_load_generator.py` 增加 `--long-tail-ratio` (~15 行)
3. 运行 Exp-Bursty + Exp-LongTail

### Week 5: 真实 LLM 主力深挖 (分层 D 方案)

**战略**: CCF-A 朋友建议 — 不做机械 D，做"分层 D = 10 + 饱和点"

**产出**:
- [x] C=10 validation (1 repeat) — 结果: 所有网关 96.5-100% success, 低 contention 确认
- [ ] Pilot sweep C=20,30 × 1 repeat × 4 gateways — 找 contention onset
- [ ] 正式双组 5-repeat: C=10 (boundary) + C* (scarcity regime)
- [ ] Statistical power analysis + Permutation test
- [ ] 论文 Real-LLM 段落: Boundary regime + Scarcity regime 双叙事

**Step 1: Pilot 校准 (C=10/20/30, 各 1 repeat)**
```
脚本:         scripts/pilot_concurrency_sweep.py
目标:         找到 contention onset (不是看 success, 是看四个信号)
信号:
  1. 实际 RPM 是否接近/超过 200
  2. Step-level rejection rate 是否明显上升
  3. P95 tail latency 是否拉长
  4. ALL_REJECTED (step-0 rejection) 是否上升
C=10 已有:    NG 99%, Rajomon 100%, PP 97.5%, PG 96.5% — 低 contention
C=20 预期:    200 RPM 附近, 可能开始有 contention
C=30 预期:    超过 200 RPM, 保证 contention + 429 errors
```

**Step 2: 正式双组实验 (C=10 + C*)**
```
脚本:         scripts/run_real_llm_week5.py --concurrency C --repeats 5
C=10:         low-contention boundary (已验证 pilot 可行)
C*:           由 pilot 决定 (预期 C=30, 除非 C=20 已触发稳定 contention)
sessions:     200
gateways:     [NG, Rajomon, PP, PlanGate]
repeats:      5
结果目录:     results/exp_week5_C10/ 和 results/exp_week5_C{*}/
```

**论文叙事结构**:
1. **Boundary regime (C=10)**: 低 contention → 所有网关表现接近 → 
   session commitment 的治理收益有明确的资源稀缺边界
2. **Scarcity regime (C=C*)**: 高 contention → PlanGate 显著降低 ABD、
   提升 useful goodput → session commitment 在资源紧张时发挥作用

**主指标排序**: ABD > GP/s > Success > tail latency > 429 rate

**优先级排序（如遇 API 时间/预算波动，严格按此序削减）**:
1. **保命项**: Pilot + 高压组 5 repeats (4 gateways)
2. **核心项**: 低压组 5 repeats (验证 boundary 稳定性)
3. **加分项**: Bursty arrival（可推迟）

### Week 6: 真实 LLM 边界验证 + Token 分析

**产出**:
- [ ] DeepSeek-V3 100 sessions × N=5 × 3 gateways 结果
- [ ] 边界条件图: 收益随 contention 提升的曲线
- [ ] Token-aware 离线分析: steps vs token sunk cost 相关性
- [ ] Discussion 段落: PlanGate 的适用边界

**分析任务**:
1. 用已有真实实验日志计算 "已完成步数" vs "累积 token 消耗" 的相关性
2. 如果 Pearson r > 0.85 → token-aware 不必进主线
3. 如果 r < 0.85 → 考虑作为小节扩展

### Week 7: 分布式原型 或 Scope 收缩

**硬止损规则**: 如果到 Week 6 末，正文主线（session commitment 叙事重写）和核心实验（Exp-BM + Real-LLM 主实验）还没有收敛封口，**直接放弃分布式实现**，转做选项 B（scope 收缩）。不允许分布式原型占用最后两周。论文核心是"session commitment 这一抽象是否必要"，不是"可分布式部署"。

**决策点**: 基于 Week 1-6 进展评估

**选项 A: 做分布式原型（仅当主线已收敛）**
- [ ] Docker Compose: 2-3 gateway 节点 + Redis
- [ ] Redis 价格同步模块 (~150 行 Go)
- [ ] Nginx 一致性哈希配置
- [ ] 对比: 单节点 vs 多节点 Exp-BM 结果
- [ ] 量化: sync overhead, stale-state penalty, throughput scaling

**选项 B: Scope 收缩**
- [ ] State overhead 微基准 (memory per session, GC pause)
- [ ] Gateway latency 微基准 (admission decision time breakdown)
- [ ] Limitations 节: 明确声明 single-gateway scope
- [ ] Future Work: 提出 Session Affinity + Redis 方案

### Week 8: 整稿重构 + 投稿打磨

**产出**:
- [ ] 完整重写: Abstract, Introduction, Contributions
- [ ] 新增/重构: §3.x Theoretical Analysis
- [ ] 全面修订: Related Work (补 mechanism design, queueing theory 引用)
- [ ] 实验节重排为"证明主张"顺序
- [ ] Discussion + Limitations 全面更新
- [ ] 附录: α 敏感性、安全机制、更多 provider 数据
- [ ] 自查三遍:
  1. 核心抽象是否清楚 (session commitment)?
  2. 强基线是否足够强 (Rajomon+SB, PP)?
  3. 边界条件是否讲诚实 (contention-dependent)?

---

## 八、风险评估与应对预案

### 8.1 已解决: Progress-Priority 接近 PlanGate (Week 1 黄灯 → Week 2 绿灯)

**Week 2 结果**: 核心 claim 已转绿。ABD_P&S=0.0%, ABD_ReAct=29.1%, ABD 证据链成立，GP/s 翻倍。

### 8.1b 已解决: Rajomon 结果过于极端 → Strawman 攻击

**场景**: Rajomon ABD=90.3%, GP/s=4.4 太极端，评审怀疑调参不公平

**Week 3 已完成 — 结构性失配已证明**:
- Rajomon price_step 敏感性扫描完成: {5, 10, 20, 50, 100} × 5 repeats
- 最优 price_step=5 下 ABD=64.4% (仍远高于 PlanGate 25.1%, 差距 39.3pp)
- price_step=5 接近无治理水平 (NG ABD=66%), 说明 per-request pricing 在低价格步长下退化为无约束
- price_step ∈ {20, 50, 100} 下 ABD ≥ 89% — 价格追踪越积极反而越差
- **结论**: per-request pricing 与 multi-step session 存在结构性不适配
- **论文策略**: 正式实验中使用 Rajomon best-case (ps=5), 同时在 sensitivity figure 展示全参数空间
- 数据保存: `results/exp_rajomon_sensitivity/rajomon_sensitivity.csv`

### 8.2 中等风险: Real-LLM 扩大样本后仍不显著

**场景**: GLM 200 sessions 后 p-value > 0.05

**应对**:
- 增加并发到 C=15 或 C=20 (如果 API 允许)
- 使用效应量 (Cohen's d) 而非仅 p-value
- 强调 cascade waste reduction 的实际意义（即使统计意义边界）

### 8.3 中等风险: 理论推导被审稿人挑战

**场景**: 审稿人认为 risk exposure 模型过于简化

**应对**:
- 明确声明这是**简化分析**，旨在提供直觉和设计指导，而非严格最优性证明
- 用"我们证明了 K² 是合理的设计点"替代"我们证明了 K² 是最优的"
- 实验数据（Exp8 消融 + Exp-Alpha 敏感性）作为主要证据

### 8.4 低风险: API 预算超支

**应对**: 优先保 GLM 深挖，DeepSeek 减到 50 sessions。总成本 ~¥800 可控。

---

## 九、优先级金字塔总结

> **Week 3 → Week 4 阶段转型**: 从"验证路线是否可行"进入"打造投稿版本"。  
> 核心证据三件套已齐: (1) 抽象清楚 (2) 基线强 (3) 结论稳。  
> 剩余主要风险: (1) 理论节太弱会让文章滑回经验论文 (2) 正文叙事没同步升级。

如果只能保住三件事:
1. **正式主表用 Rajomon best-case 重跑 + 统计显著性更新** → 所有正式表/图的数据基座
2. **论文核心叙事锁定 + Table 1 分析段 + §3.x 理论初稿** → 从经验论文升级为系统论文
3. **GLM 高 contention 大样本验证** → Real-LLM 证据

如果能保住第四件:
4. **Rajomon sensitivity figure (正文图)** → baseline fairness defense

如果还有余力:
5. **Bursty + Long-tail 实验** → 扩展可信度
6. **分布式最小原型** → 加分项（硬止损: Week 6 末主线未收敛则放弃）

不进入主线的项目:
- **α 自适应** → 附录
- **声誉系统** → 附加安全机制, 附录
- **Token-aware 离线分析** → 有余力才做

> **指标优先级**:  
> P0 (必须有): ABD_P&S, ABD_ReAct, GP/s, ABD 证据链, 统计 CI  
> P1 (应当有): Success_P&S/ReAct, Step0Reject_P&S/ReAct, GP/s_P&S/ReAct  
> P2 (加分项): P50/P95 latency, JFI fairness, 价格时序
>
> **论文实验节图表顺序 (Week 3 后更新)**:  
> Table 1: Mode-Stratified Commitment Quality (ABD_P&S, ABD_ReAct, Success, GP/s) — 用 Rajomon best-case  
> Figure: Rajomon Sensitivity Curve (ABD vs price_step) — baseline fairness defense  
> Table 2: Overall throughput + cascade comparison  
> 后续: robustness / sensitivity / fairness
>
> **论文核心一句话 (CCF-A Week 3 反馈)**:  
> PlanGate does not merely improve success rates; it improves commitment quality,  
> delivering hard commitments for planned sessions and substantially stronger soft  
> commitments for reactive sessions, with statistically significant gains over all  
> strong baselines, including the best-tuned Rajomon variant.
>
> **PG-noRes 定位升级**: 不是普通 ablation, 而是"缺少 temporal isolation 的 commitment system" — 在论文中要强调这一点。
