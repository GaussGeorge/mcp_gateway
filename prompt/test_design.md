## 整体测试思路梳理

### 一、三个被测网关

| 网关 | 代码位置 | 行为 |
|------|---------|------|
| **NG (No Governance)** | `baseline/ng_gateway.go` | 全部透传，不做任何准入控制 |
| **SRL (Static Rate Limit)** | `baseline/srl_gateway.go` | 固定 QPS 令牌桶限流，不区分轻重/预算 |
| **DP (Dynamic Pricing)** | `mcp_governor.go` + `overloadDetection.go` | 动态定价 + 工具权重 + Regime Detector 自适应档位切换 |

### 二、被测 MCP 工具矩阵

**轻量级（Mouse Flow，weight=1~2）：**
- `calculate` — 数学运算，<1ms
- `get_weather` (mock) — 模拟天气查询，50-200ms
- `web_fetch` (mock) — 模拟网络 I/O，100-500ms
- `text_format` — 文本处理，<5ms

**重量级（Elephant Flow，weight=8~10）：**
- `mock_heavy`（**基准工具**，weight=10）— CPU burn + 内存分配，参数可控
- `doc_embedding`（weight=8）— 内存密集型
- `python_sandbox`（weight=10）— CPU 密集 + 长队列

> 实验中重量请求统一使用 `mock_heavy(cpu_burn_ms=5000, memory_mb=50)` 作为可复现基准。

### 三、三个自变量

| 变量 | 含义 | 取值 |
|------|------|------|
| `method` | 网关策略 | NG / SRL / DP |
| `heavy_ratio` | 重量级请求占比 | 0.1, 0.2, 0.3, 0.5 |
| `high_budget_ratio` | 高预算请求占比 | 0.2, 0.5, 0.8 |

### 四、四组实验设计

#### Exp1：负载模式对比（全局鸟瞰）
- **固定**：heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：3 methods × 3 patterns（Step/Sine/Poisson）= **9 组**
- **核心指标**：Throughput, P95, Rejection Rate, Error Rate
- **目标**：证明 DP 在所有负载模式下均表现最优

#### Exp2：heavy_ratio 敏感性分析（论文核心表）
- **固定**：Poisson, high_budget_ratio=0.5
- **变化**：3 methods × 4 heavy_ratio = **12 组**
- **核心指标**：Throughput, P95, P99, Rejection Rate, Error Rate, Goodput
- **目标**：随重量比例增大，DP 降级最优雅

#### Exp3：预算公平性分析（DP 核心卖点）
- **固定**：Poisson, heavy_ratio=0.3
- **变化**：3 methods × 3 high_budget_ratio = **9 组**
- **核心指标**：四组通过率（轻低/轻高/重低/重高）, Fairness Index, Goodput
- **目标**：DP 能根据预算做精准分流

#### Exp4：过载恢复（时间序列亮点图）
- **固定**：Step, heavy_ratio=0.3, high_budget_ratio=0.5
- **变化**：3 methods 时间序列对比 = **3 组**
- **核心指标**：P95(t), Rejection Rate(t), Error Rate(t), Recovery Time

**合计：33 组 × 每组重复 3 次 = 99 次试验**

### 五、完整指标体系

#### A. 主力指标（8 个，出论文核心表/图）

| 指标 | 定义 |
|------|------|
| Throughput | 单位时间成功处理请求数 (req/s) |
| Latency P50/P95/P99 | 请求延迟百分位数 |
| Rejection Rate (%) | 治理层主动拒绝的请求占比（**健康保护行为**） |
| Error Rate (%) | 后端执行失败的请求占比（**保护失败**） |
| Fairness Index | Jain's Fairness Index，按预算分组 |
| Goodput | 加权吞吐 = Σ(通过请求 × budget 权重) |
| Hi-Budget Success Rate | 高预算请求的成功率 |
| Recovery Time | 从过载到 P95 恢复到基线 1.5 倍以内的时间 |

#### B. 辅助资源指标（5 个，解释性图表）
- 后端 CPU%（psutil，500ms 采样）
- 后端内存 RSS MB
- CPU 峰值 / 内存峰值
- 资源效率 = Goodput / Avg CPU%

#### C. DP 专属指标（3 个）
- Regime 切换准确率
- 价格收敛时间
- 价格震荡幅度（稳态下价格标准差）

### 六、可视化图表清单（8 张）

| 编号 | 图表类型 | 对应实验 | 核心故事 |
|:---:|------|:---:|------|
| 图1 | **摘要表 (Table)** | 全局 | 一览三方核心指标 |
| 图2 | **柱状图**（按预算分组成功率） | Exp3 | DP 预算公平性 |
| 图3 | **面积时间序列图**（Succ/Rej/Err 堆叠） | Exp4 | 过载保护效果 |
| 图4 | **四合一时间序列**（QPS+P95+CPU+Memory） | Exp4 | 延迟+资源的综合故事 |
| 图5 | **曲线图**（heavy_ratio 敏感性） | Exp2 | Throughput/Rejection/CPU 三线对比 |
| 图6 | **散点图**（Avg CPU% vs Goodput） | Exp3 | 资源效率证明 |
| 图7 | **CDF 小多图**（3×3 延迟分布） | Exp1 | 全负载模式延迟分布 |
| 图8 | **双 Y 轴曲线**（DP 价格 vs CPU%） | Sine | 价格自适应能力 |

### 七、需要调整的关键参数

#### DP Regime Detector 参数（三套档位）：

| 参数 | Bursty | Periodic | Steady |
|------|:---:|:---:|:---:|
| PriceStep | 200 | 100 | 150 |
| PriceDecayStep | 20 | 10 | 15 |
| PriceSensitivity | 8000 | 15000 | 10000 |
| LatencyThreshold | 300μs | 500μs | 400μs |
| DecayRate | 0.9 | 0.75 | 0.8 |
| PriceUpdateRate | 5ms | 20ms | 10ms |

> **设计理念**：Bursty = 快速反应；Periodic = 高阻尼防震荡；Steady = 精准收敛

#### SRL 需要调的参数：
- `QPS` 上限值和 `BurstSize`（静态值，实验中固定）

#### 工具权重（DP 专属）：
- 轻量工具 weight=1（calculate, weather, text_format），web_fetch=2
- 重量工具 weight=8~10（mock_heavy=10, doc_embedding=8, python_sandbox=10）

### 八、当前缺失的组件

根据项目扫描，以下实验关键组件**尚未实现**：

1. **Load Generator**（`load_generator.py`）— 三种负载模式的请求发生器
2. **Resource Monitor**（`monitor.py`）— psutil 进程级 CPU/内存采样
3. **可视化脚本** — matplotlib 画 8 张图的代码
4. **实验编排脚本** — 自动跑 99 次试验、收集 CSV 结果

---

**总结**：三网关 × 七工具 × 三负载模式 × 三自变量，通过 4 组实验（33×3=99 次试验），用 8 个主力指标 + 5 个资源指标 + 3 个 DP 专属指标，产出 8 张图表，形成完整的论文证据链。核心故事是：**DP 不是靠"少做事"降延迟，而是靠"做对的事"实现更高的资源效率和经济公平性**。

需要我开始实现哪个缺失的组件？