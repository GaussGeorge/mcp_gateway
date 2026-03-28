# 从当前状态出发的完整执行规划
## 一、规划总览
本规划完全基于你现有的所有资产（MCP工具库、NG/SRL基线、DP核心机制、现有实验框架），仅做最小改动，分**4个阶段**落地，总周期**8周**，最终产出一套完整的、具备CCF-B竞争力的实验成果。

| 阶段 | 核心任务 | 时间周期 | 关键交付物 |
|------|----------|----------|------------|
| 阶段1：双顶会基线复现 | 复现Breakwater+Rajomon核心机制，统一接口 | 2周 | 两个顶会基线类上线，与现有网关接口完全兼容 |
| 阶段2：场景化DAG包装 | 给工具加Schema，设计3个真实Agent任务模板 | 1周 | 场景化发压机上线，支持React/Plan-and-Solve DAG请求 |
| 阶段3：完整实验设计与执行 | 设计并执行6组核心实验，采集所有指标 | 3周 | 完整实验数据，所有原始CSV日志 |
| 阶段4：图表生成与规范 | 生成并规范所有学术图表，形成完整证据链 | 2周 | 10张规范学术图表，论文实验章节初稿 |

---

## 二、阶段1：双顶会基线复现（2周）
### 核心目标
复现Breakwater（OSDI 2020）和Rajomon（OSDI 2025）的**核心算法逻辑**，适配你的单机MCP场景，与现有DP网关、NG/SRL基线保持完全一致的接口，确保对比公平。

### 具体操作
#### 1.1 Breakwater核心复现（1周）
**复现范围**：仅复现核心信用控制逻辑，不复现内核态优化、分布式部署。
**核心代码**：直接使用之前给你的`BreakwaterBaseline`类，确保接口与你的DP网关完全一致：
```python
# 统一接口规范，所有网关/基线必须实现这三个方法
class GatewayInterface:
    def check_admission(self, client_id: str, request: dict) -> Tuple[bool, float]:
        """准入控制：返回(是否准入, 消耗的资源量)"""
        raise NotImplementedError
    
    def update_state(self, interface: str, queuing_delay: float, downstream_info: dict = None):
        """更新内部状态：排队延迟、下游负载信息"""
        raise NotImplementedError
    
    def on_request_complete(self, client_id: str, used_resource: float, success: bool):
        """请求完成回调：返还资源、更新统计"""
        raise NotImplementedError
```
**验收标准**：
- [ ] `BreakwaterBaseline`实现`GatewayInterface`
- [ ] 手动测试：能正常准入/拒绝请求，能根据排队延迟调整总信用
- [ ] 与现有发压机框架完全兼容，无需修改发压机代码

#### 1.2 Rajomon核心复现（1周）
**复现范围**：仅复现Token-价格市场机制、接口粒度定价、最大下游价格策略，不复现分布式gRPC拦截器。
**核心代码**：直接使用之前给你的`RajomonBaseline`类，同样实现`GatewayInterface`。
**关键差异化**：保留你修改后的定价逻辑，作为你的核心创新，在论文里明确对比。
**验收标准**：
- [ ] `RajomonBaseline`实现`GatewayInterface`
- [ ] 手动测试：能正常Token准入/拒绝，能根据下游价格更新本地价格
- [ ] 与现有发压机框架完全兼容

---

## 三、阶段2：场景化DAG包装（1周）
### 核心目标
给现有MCP工具加输入输出Schema，设计3个真实Agent任务模板，让发压机生成有逻辑的DAG请求，摆脱“随机调用”的玩具感。

### 具体操作
#### 2.1 工具Schema包装（0.5周）
给你现有的7个工具（calculate/get_weather/web_fetch/text_format/mock_heavy/doc_embedding/python_sandbox）统一加`MCPSchemaWrapper`包装，定义清晰的输入输出字段，确保前一个工具的输出能作为后一个工具的输入。
**验收标准**：
- [ ] 所有7个工具都有明确的输入输出Schema
- [ ] 手动测试：能按`web_fetch → text_format → python_sandbox`的顺序链式调用

#### 2.2 3个真实Agent任务模板设计（0.5周）
| 任务模板 | 场景模式 | DAG链 | 轮次 | 预算权重 | 占比 |
|----------|----------|--------|------|----------|------|
| 天气助手 | React | `web_fetch(weather) → text_format → python_sandbox` | 3 | 30 | 50% |
| 数据分析 | Plan-and-Solve | `web_fetch(history) → doc_embedding → calculate → text_format` | 4 | 60 | 30% |
| 内容生成 | 混合负载 | `web_fetch(ref) → mock_heavy → doc_embedding → python_sandbox` | 4 | 100 | 20% |
**验收标准**：
- [ ] 3个任务模板代码实现完成
- [ ] 发压机支持按比例混合生成三种任务
- [ ] 能正常触发级联失败逻辑

---

## 四、阶段3：完整实验设计与执行（3周）
### 核心目标
设计并执行**6组核心实验**，覆盖所有核心场景，采集所有必要指标，形成完整的证据链。所有实验均独立重复**10次**（N=10），固定全局随机种子，保证可复现性。

### 实验环境统一配置
| 配置项 | 值 |
|--------|-----|
| 硬件 | 与你现有环境完全一致（A4000/绑核策略） |
| 软件 | Python 3.9+, 与现有环境一致 |
| 超时阈值 | 端到端30s，单轮工具调用5s |
| 排队延迟阈值 | 50ms（所有方案统一） |
| 参数调优 | 所有基线用贝叶斯优化调到最优 |

### 3.1 实验1：核心性能对比（Step脉冲负载）
**实验目的**：证明你的DP方案在突发过载场景下，对比所有基线的全面优势。
**负载设计**：0-20s稳态（QPS=30），20-60s突发（QPS=120），60-80s恢复（QPS=30）。
**对比方案**：NG、SRL、Breakwater、Rajomon、DP-Full、DP-NoRegime（6个方案）。
**需要测量的指标**：
| 指标 | 定义 |
|------|------|
| Throughput | 总请求吞吐量（req/s） |
| Goodput | 加权有效吞吐量（w-req/s） |
| P95/P999 Latency | 端到端尾延迟（ms） |
| Rejection Rate | 主动拒绝率（%） |
| Error Rate | 后端错误率（%） |
| **端到端SLO达标率** | 全链路成功且延迟<30s的请求占比（%） |
| **级联失败率** | 因某一轮失败导致全链路失败的请求占比（%） |
**需要输出的图表**：
- **Table 1**：核心性能指标对比表（类似你现有的Table 1，扩展到6个方案）
- **Figure 1**：请求状态时序图（6个子图，对应6个方案，类似你现有的Figure 5）

### 3.2 实验2：重载敏感性分析（不同Heavy Ratio）
**实验目的**：证明你的DP方案在重请求比例上升时，依然能保持稳定性能。
**负载设计**：固定QPS=80，改变重请求（mock_heavy）比例：10%、30%、50%。
**对比方案**：NG、SRL、Breakwater、Rajomon、DP-Full（5个方案）。
**需要测量的指标**：Throughput、Goodput、P95 Latency、SLO达标率。
**需要输出的图表**：
- **Figure 2**：重载敏感性折线图（2个子图：(a) Goodput (b) SLO达标率，X轴为Heavy Ratio）

### 3.3 实验3：预算公平性与资源效率
**实验目的**：证明你的DP方案能优先保障高价值多轮请求，提升资源利用率。
**负载设计**：混合负载，QPS=100，高低预算请求各占50%。
**对比方案**：NG、SRL、Breakwater、Rajomon、DP-Full（5个方案）。
**需要测量的指标**：
- 低/高预算请求的成功率（%）
- Goodput按工具类型的分解（w-req/s）
**需要输出的图表**：
- **Figure 3**：预算公平性柱状图（类似你现有的Figure 3）
- **Figure 4**：Goodput分解堆积柱状图（类似你现有的Figure 4）

### 3.4 实验4：复合流量消融实验（核心）
**实验目的**：证明你的Regime自适应档位切换机制的不可替代性。
**负载设计**：120s过山车复合流量（0-20s稳态/20-45s突发/45-80s周期波动/80-100s低负载/100-120s微脉冲）。
**对比方案**：DP-Full、DP-NoRegime、Breakwater、Rajomon（4个方案）。
**需要测量的指标**：
- Goodput（仅截取20-80s过载区间）
- Error Rate（仅截取20-80s过载区间）
- **SLA合规率**（Latency < 2s）
- **突发收敛时间**（过载后Goodput稳定的时间）
- **Regime切换日志**（仅DP-Full，用于附录）
**需要输出的图表**：
- **Figure 5**：消融实验核心指标对比（4个子图：(a) Goodput (b) Error Rate (c) SLA合规率 (d) 收敛时间）
- **Figure 6**：复合流量时序图（4个子图，对应4个方案）
- **Figure 7**：Regime切换与Error率联动图（仅DP-Full，类似你现有的Figure 8）

### 3.5 实验5：任务类型敏感性分析
**实验目的**：证明你的DP方案在不同类型的Agent任务混合下，依然能保持稳定。
**负载设计**：固定QPS=100，改变三种任务的混合比例：
- Case 1：100%轻量（天气助手）
- Case 2：50%轻量 + 50%重量
- Case 3：100%重量（内容生成）
**对比方案**：Breakwater、Rajomon、DP-Full（3个方案）。
**需要测量的指标**：SLO达标率、Goodput。
**需要输出的图表**：
- **Figure 8**：任务类型敏感性柱状图（X轴为混合比例，Y轴为SLO达标率）

### 3.6 实验6：参数鲁棒性分析
**实验目的**：证明你的DP方案对参数不敏感，易于部署。
**负载设计**：Step脉冲负载，QPS=120。
**对比方案**：Breakwater、Rajomon、DP-Full（3个方案）。
**需要测量的指标**：参数变异系数（CV）。
**需要输出的图表**：
- **Figure 9**：参数鲁棒性对比图（类似Rajomon论文的Figure 10）

---

## 五、阶段4：图表生成与规范（2周）
### 核心目标
生成所有10张规范学术图表，统一格式、配色、标注，形成完整的证据链，直接用于论文。

### 图表统一规范
| 规范项 | 要求 |
|--------|------|
| 配色 | 统一使用：<br>- DP-Full: 绿色 (#2ecc71)<br>- DP-NoRegime: 橙色 (#e67e22)<br>- Rajomon: 蓝色 (#3498db)<br>- Breakwater: 紫色 (#9b59b6)<br>- SRL: 深蓝色 (#2980b9)<br>- NG: 红色 (#e74c3c) |
| 误差棒 | 所有柱状图/折线图必须加95%置信区间误差棒 |
| 统计标注 | 所有对比必须加Welch t-test p值：<br>- `p < 0.05`: `*`<br>- `p < 0.01`: `**`<br>- `p < 0.001`: `***`<br>- `p >= 0.05`: `n.s.` |
| 自包含性 | 所有图表必须有：<br>- 清晰的标题（含实验编号）<br>- 坐标轴标签（带单位）<br>- 完整的图例<br>- 必要的统计标注 |
| 格式 | 所有图表导出为PDF矢量图，分辨率≥300dpi |

### 最终图表清单
| 图表编号 | 图表名称 | 对应实验 | 优先级 |
|----------|----------|----------|--------|
| Table 1 | Core Performance Metrics — Step-Pulse Workload | 实验1 | P0 |
| Figure 1 | Request-Status Distribution Over Time — Step-Pulse | 实验1 | P0 |
| Figure 2 | Impact of Heavy-Request Ratio on Gateway Performance | 实验2 | P0 |
| Figure 3 | Budget Fairness — Success Rate by Budget Group | 实验3 | P0 |
| Figure 4 | Goodput Contribution by Tool Type | 实验3 | P0 |
| Figure 5 | Ablation — Composite Workload | 实验4 | P0 |
| Figure 6 | Composite Workload Timeline — Ablation | 实验4 | P0 |
| Figure 7 | Regime Switching & Error Rate — Ablation | 实验4 | P1 |
| Figure 8 | Task Type Sensitivity — SLO Compliance | 实验5 | P1 |
| Figure 9 | Parameter Robustness — Coefficient of Variation | 实验6 | P2 |

---

## 六、最终验收标准
- [ ] 双顶会基线（Breakwater/Rajomon）上线，接口统一
- [ ] 场景化DAG发压机上线，支持3个真实Agent任务
- [ ] 6组核心实验全部完成，N=10，数据完整
- [ ] 10张规范学术图表全部生成，符合要求
- [ ] 所有原始CSV日志、实验代码整理归档