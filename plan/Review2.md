## 评审意见

**论文标题**: PlanGate: Sunk-Cost-Aware Dynamic Pricing for Multi-Step LLM Agent Tool Governance

**评审结论**: **Major Revision** (大修)

---

### 1. 研究动机与问题定义

**评价**: 问题定义清晰，但动机存在过度包装

论文识别出的"cascading compute waste"问题是真实存在的——多步骤LLM Agent在执行过程中被中断确实会造成计算资源浪费。将MCP (Model Context Protocol) 作为研究背景是合适的，这是当前Agent系统的前沿方向。

**问题**:
- **动机缺乏量化支撑**: 论文声称"122.6 out of 500 sessions suffer cascade failures"，但没有解释这个数据的来源。是真实生产环境的测量？还是模拟实验？缺乏说服力。
- **问题紧迫性论证不足**: 论文没有证明这是一个"必须解决"的问题。当前业界是如何处理这个问题的？为什么现有方案不够用？

---

### 2. 技术贡献与创新性

**评价**: 核心思想有新意，但创新程度被夸大

论文提出的四个机制（pre-flight atomic admission、budget reservation、sunk-cost-aware pricing、reputation-based security）在概念上是合理的。特别是将"沉没成本"（sunk cost）概念引入准入控制，是一个有趣的思路。

**严重问题**:

1. **与Rajomon的关系处理不当**  
   论文承认Rajomon (OSDI'25) 使用类似的动态定价思想，但声称PlanGate在"session-level abstraction"上有根本区别。然而：
   - 这个"根本区别"的论证薄弱。从系统角度看，session tracking只是一个工程实现问题，不构成理论创新。
   - 论文将Rajomon排除在实验对比之外的理由（"structurally unsuited"）站不住脚。实际上，Rajomon的架构完全可以扩展支持session tracking。

2. **核心公式缺乏理论分析**  
   论文的核心定价公式 $P_K = P_{eff} \times I(t) / (1 + K^2 \cdot \alpha)$ 完全基于启发式（heuristic），没有任何：
   - 最优性证明
   - 稳定性分析
   - 激励兼容性论证
   
   对于CCF-A级别的论文，一个没有任何理论支撑的启发式公式是不够的。

3. **Budget Reservation并非新概念**  
   数据库领域的两阶段提交、分布式事务中的锁机制，都与这里的"price locking"思想类似。论文没有充分讨论与这些经典工作的关系。

---

### 3. 系统设计与实现

**评价**: 工程设计合理，但细节缺失严重

**问题**:

1. **关键参数完全手工调参**  
   - $\alpha = 0.4$ (mock) / $0.5$ (real-LLM) 是手工设定的
   - $\tau_{ban} = 0.3$, $\tau_{warn} = 0.5$ 也是经验值
   - 论文承认这一点，但声称"the same $\alpha = 0.5$ works across both GLM and DeepSeek without per-provider tuning"作为辩护。这不是优点——这说明参数设置过于粗糙。

2. **External Signal Tracker设计缺乏深度**  
   论文提到使用HTTP 429、latency P95、RateLimit-Remaining三个信号，但：
   - 权重 $w_{429}=0.5, w_{latency}=0.3, w_{rateLimit}=0.2$ 的设定依据是什么？
   - 为什么选择EMA（指数移动平均）？是否有其他滤波器对比？

3. **Session Capacity Semaphore实现过于简单**  
   使用Go的buffered channel作为信号量是一个 undergraduate-level 的实现。对于CCF-A论文，应该讨论：
   - 分布式场景下的状态同步
   - 故障恢复机制
   - 与后端实际负载的反馈闭环

---

### 4. 实验评估

**评价**: 实验设计有诚意，但存在严重缺陷

**优点**:
- 使用两个真实LLM API (GLM-4-Flash, DeepSeek-V3) 进行验证
- 进行了消融实验 (ablation study)
- 统计检验 (bootstrap, permutation test) 的使用是加分项

**严重缺陷**:

1. **Baseline选择存在偏向性**  
   - 排除Rajomon的理由不充分
   - "No Governance"作为baseline过于弱——任何合理的系统都会比"无治理"好
   - 缺少与更先进的调度算法的对比（如基于强化学习的调度）

2. **实验规模过小**  
   - Real-LLM实验只有50 sessions × 5 runs = 250 sessions per configuration
   - 对于统计显著性声称，这个样本量是不够的
   - 论文声称"N=5 provides sufficient power"，但没有提供power analysis

3. **Mock实验与真实环境差距过大**  
   - 使用 `asyncio.sleep` 模拟工具延迟过于简化
   - 真实的LLM推理有高度可变的延迟（受输入长度、输出长度、缓存命中率影响）
   - 论文承认这一点，但声称"real-LLM experiments mitigate this"——然而真实实验规模又太小

4. **关键声明缺乏支撑**  
   - "3.2× effective goodput improvement"只在mock环境中实现，真实LLM实验只有10%左右的提升
   - 论文强调"contention-dependent advantage"，但这实际上是在为有限的效果辩护

5. **Adversarial实验过于简单**  
   - 10% adversarial agents 的设定是任意的
   - 攻击方式（inflated budgets, oversized DAGs）过于naive
   - 没有讨论更复杂的攻击（如Sybil攻击、collusion攻击）

---

### 5. 写作质量

**评价**: 整体可读，但存在以下问题

1. **Related Work部分薄弱**  
   - 与microservice overload control领域的联系讨论不够深入
   - 缺少与multi-agent scheduling工作的对比（如Parrot）

2. **Limitations部分流于形式**  
   - 列出的限制（manual tuning, distributed deployment, token-level governance）都是真实的
   - 但论文没有说明这些限制对结论的影响程度

3. **Figure和Table存在不一致**  
   - Table 3显示PlanGate P50=3.9ms，但Table 4显示wo-SessionCap P50=5.1ms——后者反而更高？需要解释

---

### 6. 具体修改建议

**必须修改**:

1. **补充理论分析**  
   - 为核心定价公式提供至少一个理论性质（如：在特定假设下的最优性）
   - 或者使用排队论模型分析系统稳定性

2. **加强Baseline对比**  
   - 实现Rajomon的session-aware版本进行对比
   - 或者解释为什么这在技术上不可行（需要令人信服的理由）

3. **扩大实验规模**  
   - Real-LLM实验至少增加到200-500 sessions per configuration
   - 提供更多真实场景的workload（不同任务类型、不同工具组合）

4. **参数自适应机制**  
   - 为 $\alpha$ 提供自适应调整机制（如基于观察到的cascade rate）
   - 或者提供详细的敏感性分析

5. **讨论公平性与效率的权衡**  
   - 论文声称JFI=0.922，但这是aggregate metric
   - 需要分析不同agent类型（short vs. long sessions）的公平性

**建议修改**:

1. 补充与LLM serving系统（如vLLM）的集成讨论
2. 提供更详细的故障场景分析（gateway crash, backend failure）
3. 讨论与现有API gateway（如Kong, Envoy）的集成方案

---

### 7. 总体评价

这篇论文提出了一个合理的系统设计方案，实验工作也比较扎实。但是：

- **创新性被夸大**: 核心思想（动态定价+沉没成本）是启发式的，缺乏理论支撑；与Rajomon的区别被过度强调
- **实验存在缺陷**: 规模偏小，baseline选择有偏向性，mock与真实环境差距大
- **工程细节不足**: 关键参数完全手工设定，分布式场景讨论缺失

如果作者能够：
1. 补充核心理论分析（哪怕是简化模型下的分析）
2. 扩大真实LLM实验规模
3. 提供更公平的baseline对比

这篇论文有可能达到CCF-A的录用标准。但目前版本只能给出**Major Revision**的结论。

---

**评分**: 3/5 (Borderline - Major Revision Required)