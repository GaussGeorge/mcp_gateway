# 测试实施思路（v3 — 整合双模式评测理论与四大核心优化）

> 本文档是 `experiment_design.md` 的精简实施版，聚焦"怎么做"而非"为什么做"。
> v2 新增内容以 🆕 标注，源自 `advice_doubao.md` 和 `advice_gemini.md` 的评审建议。
>
> v3 更新要点（整合 `advice_gemini_and_doubao.md`）：
> - 🆕 引入**双模式实验框架**：无菌实验室模式（Exp1-7）+ 真实战场模式（Exp8）
> - 🆕 无菌模式固定轻量工具比例（80% calculate + 20% web_fetch），变量正交
> - 🆕 战场模式使用全部 7 个**真实** MCP 工具 + Trace-driven 分布
> - 🆕 负载生成器防协调遗漏（Anti-Coordinated Omission）+ 全局随机种子
> - 🆕 新增 **P999 尾延迟**指标（战场模式核心度量）
> - 🆕 Exp8 升级为「真实战场验证」，优先级升至 P0

---

## 一、测试对象总览

### 1.1 被测网关（6 种）

| 网关 | 简称 | 代码位置 | 行为 |
|------|------|---------|------|
| No Governance | **NG** | `baseline/ng_gateway.go` | 全部透传，不做任何准入控制 |
| Static Rate Limit | **SRL** | `baseline/srl_gateway.go` | 固定 QPS 令牌桶限流，不区分请求类型/预算 |
| Dynamic Pricing | **DP** | `mcp_governor.go` + `overloadDetection.go` | 完整版：动态定价 + 工具权重 + Regime Detector |
| 🆕 DP-NoRegime | **DP-NR** | 同上，禁用 Regime | 固定 Steady 档位，不随负载切换 |
| 🆕 DP-NoWeight | **DP-NW** | 同上，所有权重=1 | 不区分轻量/重量工具 |
| 🆕 DP-FixedPrice | **DP-FP** | 同上，禁用动态调价 | 固定价格，不随排队延迟变化 |

> DP-NR/DP-NW/DP-FP 三个消融变体仅在 Exp5 中使用。

### 1.2 被测 MCP 工具

**轻量级（Mouse Flow，weight=1~2）：**
- `calculate` — 数学运算，<1ms
- `get_weather` (mock) — 模拟天气查询，50-200ms
- `web_fetch` (mock) — 模拟网络 I/O，100-500ms（weight=2）
- `text_format` — 文本处理，<5ms

**重量级（Elephant Flow，weight=8~10）：**
- `mock_heavy`（**基准工具**，weight=10）— `cpu_burn_ms=5000, memory_mb=50`（**无菌实验室模式专用**）
- `llm_reason`（weight=10）— 本地大模型推理，GPU 密集（**真实战场模式专用**）
- `doc_embedding`（weight=8）— 本地 Embedding 向量化，GPU 密集（**真实战场模式专用**）
- `python_sandbox`（weight=10）— CPU 密集 + 长队列（**真实战场模式专用**）

> 实验中重量请求统一使用 `mock_heavy(cpu_burn_ms=5000, memory_mb=50)` 作为可复现基准（仅限无菌实验室模式）。
> 🆕 **Exp8 战场模式**：使用全部 3 个真实重量工具（llm_reason / doc_embedding / python_sandbox），
> 轻量工具中 Weather/WebFetch 仍用 Mock（无需真实 API，保证延迟确定性）。
> **MCP Server 启动**：`python server.py --mode sterile` 或 `--mode battlefield`

### 🆕 1.3 双模式实验框架

| 模式 | 适用实验 | 工具数 | 轻量工具比例 | 重量工具 | 目标 |
|------|---------|:---:|------|------|------|
| **无菌实验室** | Exp1-7 | 3 | 固定 80% calculate + 20% web_fetch | 仅 mock_heavy | 变量正交、曲线平滑、统计显著 |
| **真实战场** | Exp8 | 7 | Trace-driven 40:20:25:15（Weather/WebFetch 为 Mock） | llm_reason(真实) 50% + doc_embedding(真实) 30% + python_sandbox(真实) 20% | 工业级落地、P999 防雪崩 |

> **核心理念**：审稿人既要求实验变量纯净可控，又要求系统能应对真实世界混沌流量。双模式分治完美满足这两种要求。

### 1.4 三个自变量

| 变量 | 含义 | 取值 |
|------|------|------|
| `method` | 网关策略 | NG / SRL / DP（含消融变体） |
| `heavy_ratio` | 重量级请求占比 | 0.1, 0.2, 0.3, 0.5（极端：0.7） |
| `high_budget_ratio` | 高预算请求占比 | 0.2, 0.5, 0.8 |

---

## 二、八组实验设计

### Exp1：负载模式对比（全局鸟瞰）
- **固定**：heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：3 methods × 3 patterns（Step/Sine/Poisson）= **9 组**
- **核心指标**：Throughput, P95, Rejection Rate, Error Rate
- **输出图表**：图7（CDF 3×3 小多图，含 KS 检验 p 值）
- **目标**：证明 DP 在所有负载模式下均表现最优

### Exp2：heavy_ratio 敏感性分析（论文核心表）
- **固定**：Poisson, high_budget_ratio=0.5
- **变化**：3 methods × 4 heavy_ratio = **12 组**
- **核心指标**：Throughput, P95, P99, Rejection Rate, Error Rate, **Goodput**
- **输出图表**：图1（摘要表）+ 图5（敏感性曲线，含 Goodput 线）
- **目标**：随重量比例增大，DP 降级最优雅，Goodput 始终最高

### Exp3：预算公平性分析（DP 核心卖点）
- **固定**：Poisson, heavy_ratio=0.3
- **变化**：3 methods × 3 high_budget_ratio = **9 组**
- **核心指标**：四组通过率（轻低/轻高/重低/重高）, Fairness Index, Goodput
- **输出图表**：图2（柱状图）+ 图6（散点图）+ 🆕 图11（公平性CDF）+ 🆕 图14（Goodput分解）
- **目标**：DP 能根据预算做精准分流，SRL/NG 做不到

### Exp4：过载恢复（时间序列亮点图）
- **固定**：Step, heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：3 methods 时间序列对比 = **3 组**
- **核心指标**：P95(t), Rejection Rate(t), Error Rate(t), Recovery Time
- **输出图表**：图3（面积堆叠）+ 图4（四合一+Regime底色）+ 图8（价格vs CPU）+ 🆕 图10（Regime切换时序）
- **目标**：DP 恢复最快、尖峰最小、Error 最低；Regime 切换可视化

### 🆕 Exp5：消融实验（Ablation Study，CCF-B/C 硬门槛）
- **固定**：Poisson, heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：4 DP变体 + SRL 基线 = **5 组**
- **核心指标**：Throughput, P95, Fairness Index, Goodput（均标注均值±标准差，做 ANOVA 检验）
- **输出图表**：🆕 图9（消融柱状图+误差棒）
- **目标**：每个子模块（Regime/Weight/动态调价）的独立贡献统计显著（p<0.05）
- **消融变体配置**：

| 变体 | enableAdaptiveProfile | toolWeights | priceStrategy |
|------|:---:|:---:|:---:|
| DP-Full | true | 正常(10/8/10) | expdecay |
| DP-NR | **false**（固定 Steady） | 正常 | expdecay |
| DP-NW | true | **全部=1** | expdecay |
| DP-FP | true | 正常 | **固定价格** |

### 🆕 Exp6：网关自身开销（Gateway Overhead）
- **固定**：Poisson, heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：3 methods × 5 并发级别 {10, 50, 100, 200, 500} = **15 组**
- **核心指标**：Gateway Processing Latency P50/P95/P99 (μs), Gateway CPU%, Gateway RSS MB
- **输出图表**：图9 附表/附图（CDF + 并发折线图）
- **目标**：DP 网关延迟 P99 < 100μs，CPU < 5%，证明计算开销可忽略
- **采集方法**：Go 侧 `time.Since` 记录纯网关处理耗时（排除后端执行时间）

### 🆕 Exp7：参数敏感性分析（鲁棒性验证）
- **固定**：Poisson, heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：DP 5×5 网格 + SRL 5×5 网格 = **50 组**
- **DP 扫描**：PriceSensitivity {5000,8000,10000,15000,20000} × LatencyThreshold {200,300,400,500,600}μs → Goodput
- **SRL 扫描**：QPS {30,40,50,60,80} × BurstSize {50,75,100,125,150} → Rejection Rate
- **输出图表**：🆕 图12（双热力图）
- **目标**：DP 热力图大面积暖色（参数不敏感），SRL 冷热分明

### 🆕 Exp8：真实战场验证（Real Battlefield Mode，升级为 P0）
- **🆕 模式**：**真实战场模式**（非 mock，使用全部 7 个真实 MCP 工具）
- **🆕 工具分布**：轻量 85%（calculate 40% / weather(Mock) 20% / text_format 25% / web_fetch(Mock) 15%）；重量 15%（llm_reason(真实) 50% / doc_embedding(真实) 30% / python_sandbox(真实) 20%）——Trace-driven 对齐真实 AI Agent 调用分布
- **场景 A**：Poisson, **heavy_ratio=0.15**（Trace-driven 真实比例）, high_budget_ratio=0.5 → 3 methods
- **场景 B**：**Step 脉冲**, heavy_ratio=0.15, high_budget_ratio=0.5 → 3 methods
- **合计**：**6 组**
- **🆕 核心指标**：**P999 尾延迟**、Throughput、Error Rate、Recovery Time
- **输出图表**：🆕 图13（真实战场柱状图 + P999 对比）
- **目标**：证明 DP 不是只能跑 Mock 的玩具，而是工业级防雪崩网关
- **安全措施**：推理引擎设 max 并发、发压机设 timeout=15s、仅 Exp8 用真实工具
- **🆕 P999 核心价值**：真实 llm_reason/python_sandbox 偶发阻塞数十秒 → NG 的 P999 失控到 60s+ → DP 精准拦截后 P999 降低一个数量级

---

## 三、完整指标体系

### A. 主力指标（10 个，出论文核心表/图）

| 指标 | 定义 | 使用实验 |
|------|------|---------|
| Throughput | 单位时间成功处理请求数 (req/s) | 全部 |
| Latency P50/P95/P99 | 请求延迟百分位数 | 全部 |
| 🆕 **Latency P999** | **请求延迟第 99.9 百分位数（战场模式核心指标）** | **Exp8** |
| Rejection Rate (%) | 治理层主动拒绝占比（**健康保护**） | 全部 |
| Error Rate (%) | 后端执行失败占比（**保护失败**） | 全部 |
| Fairness Index | Jain's Fairness Index，按预算分组 | Exp3, Exp5 |
| **Goodput** | **加权吞吐 = Σ(通过请求 × budget)（全文核心指标 C 位）** | **Exp2, Exp3, Exp5, Exp7** |
| Hi-Budget Success Rate | 高预算请求的成功率 | Exp3 |
| Recovery Time | P95 恢复到基线 1.5× 以内的时间 | Exp4, Exp8 |

> **Goodput 是全文的核心度量**：在摘要表(图1)和敏感性曲线(图5)中用最粗线条/加粗列突出。
> 传统 Throughput 只看"做了多少事"，Goodput 看"做对了多少有价值的事"。

### B. 辅助资源指标（5 个，解释性图表）
- 后端 CPU%（psutil，500ms 采样）
- 后端内存 RSS MB
- CPU 峰值 / 内存峰值
- 资源效率 = Goodput / Avg CPU%

### 🆕 C. 网关自身开销指标（3 个，Exp6 专用）
- **Gateway Processing Latency (μs)**：纯网关层处理延迟（Go `time.Since` 打点）
- **Gateway CPU%**：网关进程 CPU 占用（psutil）
- **Gateway RSS MB**：网关进程内存占用（psutil）

### D. DP 专属指标（3 个）
- Regime 切换准确率（检测 regime 与注入负载类型的一致性）
- 价格收敛时间
- 价格震荡幅度（稳态下价格标准差）

### 🆕 E. 统计科学性要求
- **所有实验**：3 次重复，报告均值 ± 标准差
- **所有柱状图/折线图**：标注误差棒
- **组间比较**：ANOVA 或 t-test，标注 p 值
  - * p<0.05, ** p<0.01, *** p<0.001
- **分布比较**（CDF 图）：KS 检验 p 值标注

---

## 四、可视化图表清单（14 张）

### 图表规范
- **配色**：DP=蓝(#2196F3), SRL=橙(#FF9800), NG=红(#F44336)
- **消融变体**：DP-NR=浅蓝虚线, DP-NW=蓝色点线, DP-FP=蓝灰色
- **字体**：Arial/Helvetica，统一大小
- **误差棒**：3 次重复标准差
- **自包含**：每图标题/图例/标注无需翻正文即可理解

| 编号 | 图表类型 | 实验 | 核心故事 | 优先级 |
|:---:|------|:---:|------|:---:|
| 图1 | 摘要表 (均值±std, Goodput加粗) | 全局 | 一览核心指标 | P0 |
| 图2 | 按预算分组柱状图 (含显著性标注) | Exp3 | DP 预算公平性 | P0 |
| 图3 | 面积时间序列 (Succ/Rej/Err + Recovery Time) | Exp4 | 过载保护效果 | P0 |
| 图4 | 四合一时间序列 (QPS+P95+CPU+Mem, **Regime底色**) | Exp4 | 延迟+资源+Regime状态 | P0 |
| 图5 | 敏感性曲线 (Throughput/Reject/CPU/**Goodput**) | Exp2 | heavy_ratio 影响 | P0 |
| 图6 | 散点图 (CPU% vs Goodput) | Exp3 | 资源效率证明 | P0 |
| 图7 | CDF 3×3 小多图 (统一坐标+**KS检验p值**) | Exp1 | 全模式延迟分布 | P0 |
| 图8 | 双Y轴曲线 (price vs CPU%, **Regime底色+相关系数**) | Sine | 价格自适应 | P0 |
| **图9** | 🆕 **消融柱状图 (误差棒+ANOVA p值)** | **Exp5** | **各模块独立贡献** | **P0** |
| **图10** | 🆕 **Regime切换时序 (阶梯线+P95+ownprice)** | **Exp4** | **机制可视化** | **P0** |
| **图11** | 🆕 **公平性CDF 2×2矩阵 (KS检验+Jain's Index)** | **Exp3** | **深度公平性分布** | **P1** |
| **图12** | 🆕 **参数敏感性双热力图** | **Exp7** | **鲁棒性/参数不敏感** | **P1** |
| **图13** | 🆕 **真实战场柱状图 + P999** | **Exp8** | **工业级落地 + 防雪崩** | **P0** |
| **图14** | 🆕 **Goodput分解堆叠图 (轻量+重量贡献)** | **Exp3** | **"做对的事"故事** | **P1** |

---

## 五、需要调整的关键参数

### 5.1 DP Regime Detector 三套档位

| 参数 | Bursty | Periodic | Steady |
|------|:---:|:---:|:---:|
| PriceStep | 200 | 100 | 150 |
| PriceDecayStep | 20 | 10 | 15 |
| PriceSensitivity | 8000 | 15000 | 10000 |
| LatencyThreshold | 300μs | 500μs | 400μs |
| DecayRate | 0.9 | 0.75 | 0.8 |
| PriceUpdateRate | 5ms | 20ms | 10ms |
| MaxToken | 200 | 200 | 200 |

> **Bursty** = 快速反应（低阈值+高步长+短周期）
> **Periodic** = 高阻尼防震荡（高阈值+低步长+长周期）
> **Steady** = 精准收敛（均衡配置）

### 5.2 Regime Detector 参数

| 参数 | 默认值 |
|------|:---:|
| regimeWindow | 20 |
| regimeVarianceLow | 0.02 ms² |
| regimeVarianceHigh | 0.20 ms² |
| regimeSpikeThreshold | 0.80 ms |
| profileSwitchCooldown | 200ms |

### 5.3 SRL 参数（固定值）
- `QPS` = 50，`BurstSize` = 100
- Exp7 参数扫描时在 QPS {30~80} × BurstSize {50~150} 范围内变化

### 5.4 工具权重（DP 专属）
- 轻量：calculate=1, weather=1, text_format=1, web_fetch=2
- 重量：mock_heavy=10（无菌模式）, llm_reason=10（战场模式）, doc_embedding=8（战场模式）, python_sandbox=10
- DP-NW 消融变体：全部设为 1

### 5.5 🆕 Exp7 参数扫描范围

**DP 网格**：
```
PriceSensitivity: [5000, 8000, 10000, 15000, 20000]
LatencyThreshold: [200, 300, 400, 500, 600] (μs)
→ 25 个网格点，每点输出 Goodput
```

**SRL 网格**：
```
QPS: [30, 40, 50, 60, 80]
BurstSize: [50, 75, 100, 125, 150]
→ 25 个网格点，每点输出 Rejection Rate
```

---

## 六、实验矩阵 & 试验次数

| 实验 | 自变量 | 组合数 | 优先级 |
|------|--------|:---:|:---:|
| Exp1 负载模式 | 3 methods × 3 patterns | 9 | P0 |
| Exp2 heavy_ratio | 3 methods × 4 ratios | 12 | P0 |
| Exp3 预算公平性 | 3 methods × 3 budget_ratios | 9 | P0 |
| Exp4 过载恢复 | 3 methods × 时间序列 | 3 | P0 |
| 🆕 Exp5 消融 | 4 DP变体 + SRL | 5 | P0 |
| 🆕 Exp6 网关开销 | 3 methods × 5 并发级别 | 15 | P0 |
| 🆕 Exp7 参数敏感性 | 25+25 网格 | 50 | P1 |
| 🆕 Exp8 真实战场验证 | 3 methods × 2 场景（真实工具） | 6 | **🆕 P0** |
| **合计** | | **109** | |

- **P0（务必完成）**：Exp1-6 + Exp8 = 59 组 × 3 次重复 = **177 次**
- **P1（加分项）**：Exp7 = 50 组 × 1 次取值 = **50 次**
- **完整版总计**：约 **227 次**

---

## 七、数据采集要求

### 7.1 每次试验输出的 CSV 文件

**请求级日志**（`results/{exp}_{pattern}_{method}_{ratio}.csv`）：

```csv
timestamp, request_id, tool_name, tool_category, budget, is_high_budget,
status(success/rejected/error), latency_ms, gateway_latency_us,
ownprice, regime_state
```

**资源监控日志**（`results/{exp}_{pattern}_{method}_{ratio}_resource.csv`）：

```csv
timestamp, backend_cpu_percent, backend_memory_rss_mb,
gateway_cpu_percent, gateway_memory_rss_mb
```

### 7.2 🆕 网关侧额外打点（Exp6/Exp10 所需）

Go 网关需记录：
- **gateway_latency_us**：`time.Since(start)` 纯网关处理延迟（不含后端）
- **regime_state**：当前 Regime Detector 状态（steady/periodic/bursty）
- **ownprice**：当前动态价格
- **consecutive_increases**：连续涨价次数

### 7.3 统计处理流程

```
3 次重复 CSV → pandas 合并 → 
  ├─ 计算 均值/标准差/置信区间
  ├─ 计算百分位数 (P50/P95/P99/🆕 P999)
  ├─ ANOVA / t-test → p 值
  ├─ KS 检验 → 分布差异显著性
  ├─ Jain's Fairness Index
  └─ Goodput = Σ(success × budget)
```

---

## 八、待实现组件清单

| 组件 | 文件 | 状态 | 说明 |
|------|------|:---:|------|
| Load Generator | `load_generator.py` | ❌ | asyncio + aiohttp，支持 Step/Sine/Poisson，🆕 **双模式工具选择器 + 防协调遗漏** |
| 🆕 全局随机种子 | `load_generator.py` | ❌ | `random.seed(SEED)` + `np.random.seed(SEED)`，100% 可复现 |
| 🆕 双模式工具配置 | `load_generator.py` | ❌ | 无菌模式(3工具+固定比例) / 战场模式(7工具+Trace-driven) || 🆕 MCP Server 模式切换 | `server.py --mode` | ✅ | `--mode sterile`(3工具) / `--mode battlefield`(7工具) || Resource Monitor | `monitor.py` | ❌ | psutil 采样（后端+网关两个进程） |
| 🆕 Gateway Latency Logger | Go 网关侧打点 | ❌ | `time.Since` 记录纯网关延迟 |
| 🆕 Regime State Logger | Go 网关侧打点 | ❌ | 记录 regime/ownprice/consecutive 状态 |
| 🆕 消融变体配置 | 配置文件/命令行参数 | ❌ | DP-NR/DP-NW/DP-FP 的开关逻辑 |
| Result Collector | `collect_results.py` | ❌ | CSV 合并、统计计算、ANOVA/KS 检验、🆕 **P999 计算** |
| Visualization 14 张 | `plot_figures.py` | ❌ | matplotlib 生成全部图表（统一配色/字体） |
| Experiment Runner | `run_experiments.sh` | ❌ | 自动编排 109 组实验 |

### 实现优先级

```
第一步: Load Generator（一切数据的源头）
  └─ 必须完全异步非阻塞（🆕 防协调遗漏 Anti-Coordinated Omission）
  └─ 精确控制 heavy_ratio, high_budget_ratio, 负载模式
  └─ 🆕 双模式工具选择器（无菌 3 工具 / 战场 7 工具）
  └─ 🆕 全局随机种子固定（random.seed + np.random.seed）
  └─ 🆕 泊松到达过程（指数分布间隔，非固定间隔）

第二步: Gateway 打点增强
  └─ gateway_latency_us, regime_state, ownprice 记录
  └─ 消融变体的开关逻辑

第三步: Resource Monitor
  └─ 同时采集后端进程 + 网关进程

第四步: Result Collector + Statistics
  └─ pandas 汇总 + ANOVA/KS 检验

第五步: Visualization（14 张图）
  └─ 统一配色方案, 误差棒, 显著性标注, Regime 底色

第六步: Experiment Runner
  └─ 自动调度所有实验组合
```

---

## 九、图表与实验的闭环映射

```
Exp1 (负载模式)     → 图7  (CDF 3×3 小多图)
Exp2 (heavy_ratio)  → 图1  (摘要表) + 图5 (敏感性曲线)
Exp3 (预算公平性)   → 图2  (柱状图) + 图6 (散点图) 
                     + 图11 (公平性CDF) + 图14 (Goodput分解)
Exp4 (过载恢复)     → 图3  (面积堆叠) + 图4 (四合一+Regime底色)
                     + 图8  (价格vs CPU) + 图10 (Regime切换时序)
Exp5 (消融实验)     → 图9  (消融柱状图)
Exp6 (网关开销)     → 图9附 (CDF + 并发折线)
Exp7 (参数敏感性)   → 图12 (双热力图)
Exp8 (真实战场验证) → 图13 (真实战场柱状图 + P999)
```

---

## 十、核心故事线

> **DP 不是靠"少做事"降延迟，而是靠"做对的事"实现更高的资源效率和经济公平性。**
> 🆕 **双模式评测**：无菌实验室模式证明算法优越性，真实战场模式证明工业级落地能力。

| 论点 | 实验 | 图表 | 关键证据 |
|------|------|------|---------|
| DP 全负载模式最优 | Exp1 | 图7 | CDF 曲线左移 + KS 检验显著 |
| DP 优雅降级 | Exp2 | 图1+5 | Goodput 线始终最高 |
| DP 精准分流（**核心卖点**） | Exp3 | 图2+6+11+14 | 四组通过率分化 + Fairness CDF |
| DP 恢复最快 | Exp4 | 图3+4+10 | Recovery Time最短 + Regime自适应可视化 |
| **每个模块有独立贡献** | Exp5 | 图9 | ANOVA p<0.05 消融证据 |
| **网关不是瓶颈** | Exp6 | 附图 | P99<100μs, CPU<5% |
| **参数不敏感** | Exp7 | 图12 | 热力图大面积暖色 |
| 🆕 **工业级落地 + 防雪崩** | **Exp8** | **图13** | **🆕 P999 降低一个数量级 + Error Rate 趋近 0（真实工具）** |