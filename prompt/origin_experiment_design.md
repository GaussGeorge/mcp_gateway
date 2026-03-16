

## 完整实验设计方案（含资源监控）

---

### 一、实验架构

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

### 二、三个独立变量

| 变量 | 含义 | 取值 |
|------|------|------|
| `method` | 治理方法 | NG / SRL / DP |
| `heavy_ratio` | 重量级请求占比 | 0.1, 0.2, 0.3, 0.5 |
| `high_budget_ratio` | 高预算请求占比 | 0.2, 0.5, 0.8 |

---

### 三、请求模型（2×2 矩阵）

```
                    低预算(budget=10)     高预算(budget=100)
  轻量请求          cost≈1, 几乎必过      cost≈1, 几乎必过
  重量请求          cost≈50, 可能被拒      cost≈50, 优先通过 ✓
```

- 轻量请求：随机调 `calculator` / `mock_weather` / `text_formatter`
- 重量请求：统一调 `mock_heavy(cpu_burn_ms=5000, memory_mb=50)`

---

### 四、三种负载模式 × heavy_ratio 推荐

#### 1. Step（阶梯脉冲）— 测反应速度与恢复能力

```
QPS
 │        ┌────────┐
 │        │ burst  │
 │────────┘        └────────
 └──────────────────────────→ t
```

| heavy_ratio | 实验目的 |
|-------------|---------|
| 0.1 | 轻负载脉冲：NG 也能扛，SRL 误杀轻量，DP 几乎无感 |
| **0.3（主力）** | 混合脉冲：最能展现三种方法的差异化反应 |
| 0.5 | 重载脉冲：NG 崩溃，SRL 盲拒 50%，DP 精准淘汰低预算重量 |

**观测重点：** burst 时刻的延迟尖峰大小、恢复时间、CPU/内存尖峰高度

---

#### 2. Sine（正弦波）— 测自适应能力

```
QPS
 │    ╱╲      ╱╲
 │  ╱    ╲  ╱    ╲
 │╱        ╲╱        ╲
 └──────────────────────→ t
```

| heavy_ratio | 实验目的 |
|-------------|---------|
| **0.2** | 正常波动：SRL 在波峰误杀、波谷浪费容量 |
| **0.4** | 严重波动：DP 的价格曲线应与负载正弦波高度相关 |

**观测重点：** DP 价格曲线 vs CPU 利用率曲线的相关性（双 Y 轴图）

---

#### 3. Poisson（泊松分布）— 稳态性能，出论文主表

```
QPS
 │  ╷╷ ╷  ╷╷╷ ╷ ╷╷  ╷╷╷╷  ╷   (random arrivals)
 └──────────────────────────→ t
```

| heavy_ratio | 实验目的 |
|-------------|---------|
| 0.1 | 轻量主导（chatbot 日常场景） |
| 0.2 | 典型混合负载 |
| **0.3（default）** | 中等比例，最通用 |
| 0.5 | 重载主导（极端压力） |

**观测重点：** full sweep 出核心对比表（吞吐、延迟、拒绝率、公平性、资源效率）

---

### 五、全部评估指标体系

#### A. 主力指标（论文核心表/核心图）

| 指标 | 定义 | 用途 |
|------|------|------|
| **Throughput** | 单位时间成功处理的请求数 (req/s) | 衡量系统的基本处理能力 |
| **Latency P50 / P95 / P99** | 请求延迟百分位数 | 尾部延迟是治理质量的核心体现 |
| **Rejection Rate** | 被拒请求占比（总体 + 按类型/预算分组） | 衡量治理的"精准度" |
| **Fairness Index** | Jain's Fairness Index，按预算分组计算 | 证明 DP 的经济公平性 |
| **Goodput** | 加权吞吐 = Σ(通过请求 × budget 权重) | 衡量经济效率——同样的资源下谁创造更多价值 |
| **Recovery Time** | 从过载到 P95 恢复到基线 1.5 倍以内的时间 | Step 实验专用 |

#### B. 辅助资源指标（解释性图表）

| 指标 | 采集方式 | 用途 |
|------|---------|------|
| **后端 CPU 利用率 (%)** | `psutil.Process(server_pid).cpu_percent(interval=None)`，每 500ms 采样 | 展示治理层对后端负载的"削峰"效果 |
| **后端内存 RSS (MB)** | `psutil.Process(server_pid).memory_info().rss / 1024²`，每 500ms 采样 | 展示 DP 防止 memory burst / OOM 的能力 |
| **CPU 峰值 (Peak CPU%)** | 实验期间 CPU% 的 max 值 | Poisson sweep 表格中的汇总列 |
| **内存峰值 (Peak RSS MB)** | 实验期间 RSS 的 max 值 | 同上 |
| **资源效率 (Goodput / Avg CPU%)** | 每 1% CPU 利用率创造的加权吞吐 | 证明 DP 不是靠"少做事"降延迟，而是"做对的事" |

---

### 六、四组实验设计

#### Exp1：负载模式对比

| 项目 | 设置 |
|------|------|
| 固定 | heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | 3 methods × 3 load patterns (Step/Sine/Poisson) |
| **指标** | Throughput, P95, Rejection Rate |
| **资源指标** | 每种模式下 Avg CPU%, Peak CPU%, Peak RSS |
| **目标** | 证明 DP 在所有负载模式下均表现最优 |

**图表：** 3×3 小多图（3 负载 × 3 方法），每个小图含 CDF 延迟曲线

---

#### Exp2：heavy_ratio 敏感性分析（论文核心表）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, high_budget_ratio=0.5 |
| 变化 | 3 methods × 4 heavy_ratio {0.1, 0.2, 0.3, 0.5} |
| **指标** | Throughput, P95, P99, Rejection Rate, Goodput |
| **资源指标** | Avg CPU%, Peak CPU%, Peak RSS MB |
| **目标** | 随着重量比例增大，DP 降级最优雅，资源利用最高效 |

**图表：**

图 A（主力）：X 轴 heavy_ratio → Y 轴 Throughput/Rejection Rate，三条线

图 B（辅助）：X 轴 heavy_ratio → Y 轴 Avg CPU%，三条线。讲的故事：
- NG：CPU 随 heavy_ratio 线性飙升，0.5 时 100% 崩溃
- SRL：CPU 被压低（因为盲拒了很多请求）
- **DP：CPU 略高于 SRL（因为放行了更多高价值请求），但远低于 NG → 资源效率最优**

---

#### Exp3：预算公平性分析（差异化亮点）

| 项目 | 设置 |
|------|------|
| 固定 | Poisson, heavy_ratio=0.3 |
| 变化 | 3 methods × 3 high_budget_ratio {0.2, 0.5, 0.8} |
| **指标** | 四组通过率（轻低/轻高/重低/重高）, Fairness Index, Goodput |
| **资源指标** | Avg CPU%, Peak RSS MB |
| **目标** | DP 能根据预算做精准分流，SRL/NG 做不到 |

**图表：**

图 A（主力）：分组柱状图 — X 轴 {NG, SRL, DP}，四组柱子 = 四种请求类型的通过率

图 B（辅助）：散点图 — X 轴 Avg CPU% → Y 轴 Goodput，每个点标注 method+high_budget_ratio。证明 **DP 在相同资源消耗下 Goodput 最高**

---

#### Exp4：过载恢复（时间序列）

| 项目 | 设置 |
|------|------|
| 固定 | Step, heavy_ratio=0.3, high_budget_ratio=0.5 |
| 变化 | 3 methods 的时间序列对比 |
| **指标** | P95 延迟(t), Rejection Rate(t), Recovery Time |
| **资源指标** | CPU%(t), RSS MB(t) |
| **目标** | DP 恢复最快、尖峰最小 |

**图表（整张图的核心亮点）：** 四行子图叠加，共享 X 轴（时间）

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

### 七、资源监控的实现方式（极简方案）

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

### 八、论文 Story 线（完整证据链）

> **问题：** 现有 MCP 网关缺乏经济模型感知的服务治理，Static Rate Limiting 对所有请求一刀切。
>
> **方案：** 基于动态定价 + Token 准入的治理框架（DP）。
>
> **证据链：**
>
> | 论点 | 实验 | 主力指标 | 资源辅证 |
> |------|------|---------|---------|
> | DP 在所有负载模式下均最优 | Exp1 | Throughput, P95 | 各模式下 CPU/内存对比 |
> | DP 随重载比例增大降级最优雅 | Exp2 | Throughput, Goodput vs heavy_ratio | CPU% vs heavy_ratio 曲线 |
> | DP 能基于预算做精准准入（核心亮点）| Exp3 | 四组通过率, Fairness Index | Goodput/CPU 效率散点图 |
> | DP 过载恢复最快 | Exp4 | Recovery Time, P95(t) | CPU%(t) + RSS(t) 时间序列 |

**一句话总结资源指标的角色：** 它们不是用来"比谁用得少"，而是证明 **DP 在更高的资源利用率下依然维持低延迟和高公平性** —— 即"资源效率"而非"资源节约"。