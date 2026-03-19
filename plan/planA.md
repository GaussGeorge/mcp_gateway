# 🏆 MCP 服务治理系统：A4000 单机敏捷版 Master Plan (CCF-C 冲刺最终完备版)

> **核心方法论**：Windows 单机回环部署 + Python 内嵌绑核隔离 + 纯 CPU Mock 模拟 + 4 组核心实验 + 6 张自包含核心图表 + Future Work 预期管理。

## 📍 Phase 1: Windows 单机基建与隔离有效性验证（1-2 天）
**目标：在 A4000 上建立互不干扰的物理隔离环境，并留下防守证据。**

* **[ ] 1.1 本地回环与端口规划**
    * 在 `config.yaml` 中将所有 IP 绑定为 `127.0.0.1`。
    * 规划端口：发压机无端口，MCP 后端 `8080`，NG 网关 `9001`，SRL 网关 `9002`，DP 网关 `9003`。
* **[ ] 1.2 核心动作：Windows 进程内嵌绑核 (CPU Affinity)**
    * **发压机 (`load_generator.py`)**：代码顶部嵌入 `psutil.Process().cpu_affinity([0, 1])` 独占 Core 0, 1。
    * **MCP 后端 (`server.py`)**：代码顶部嵌入 `psutil.Process().cpu_affinity(list(range(4, 16)))` 独占 Core 4~15。
    * **Go 网关**：写批处理脚本 `start /affinity C mcp_gateway.exe` 独占 Core 2, 3。
* **[ ] 1.3 💡[新增] 资源隔离验证实验与截图存档 (10 分钟防守)**
    * **操作**：同时启动发压机、Go 网关和 MCP 后端，打一波高并发脉冲。
    * **取证**：打开 Windows 任务管理器 -> 性能 -> CPU -> 右键“将图形更改为逻辑处理器”。
    * **验证**：截图保存并确认 Core 0,1（发压机）和 Core 4~15（后端）独立飙升，互相没有抢占重叠。这张截图将放入论文附录，作为“单机物理隔离有效”的铁证。
* **[ ] 1.4 “无菌”单点功能与错误码验证**
    * A4000 后端仅启动 `calculator`, `mock_web_fetch`, `mock_heavy` (纯 CPU 循环)。
    * 跑通 JSON-RPC 路由、动态计费逻辑，验证 `400` 和 `504` 错误码拦截。

## 📍 Phase 2: 轻量评测引擎与严谨基石（2-3 天）
**目标：写出带有固定种子、极简落盘和基础探针的自动化脚本。**

* **[ ] 2.1 极简全异步发压机 (`load_generator.py`)**
    * 使用 `asyncio` 编写，仅支持 Poisson 和 Step 两种波形。
    * **统计严谨性**：硬编码 `random.seed(20260401)` 和 `np.random.seed(20260401)`。
    * 只记录 4 列 CSV：`timestamp, status, latency_ms, budget`。
* **[ ] 2.2 基础资源探针 (`monitor.py`)**
    * 用 `psutil` 每 500ms 记录一次 MCP 进程的 `CPU%` 和 `Memory_MB`。无需监控 GPU。

## 📍 Phase 3: 核心实验跑批（3-4 天）
**目标：聚焦 CCF-C 核心故事线，只跑 4 组支撑性数据，自动重复 3 次。**

> **设定**：混合 80% 轻量请求 + 20% `mock_heavy` 重量请求。
> **规范**：每组配置自动运行 3 次，以便后续画误差棒。

* **[ ] 3.1 Exp1: 核心负载与恢复能力 (Step 脉冲)**
    * 打入突发 QPS，对比 NG 崩溃、SRL 盲拒、DP 熔断的表现。
* **[ ] 3.2 Exp2: Heavy Ratio 敏感性 (Poisson 稳态)**
    * 改变重请求比例（10%, 30%, 50%），跑 3 个点即可。
* **[ ] 3.3 Exp3: 预算公平性 (Poisson 稳态)**
    * 重负载下设置不同 `budget` (如 10 vs 100)，统计各自通过率。
* **[ ] 3.4 Exp4: 极简消融实验 (Poisson 稳态)**
    * 仅对比 DP-Full, DP-NoRegime 和 SRL 三者。

## 📍 Phase 4: 论文 6 图输出与“自包含”排版（3-5 天）
**目标：严格遵循“自包含性原则”，输出 6 张格式完美的 PDF 矢量图。**

> 💡 **[新增] 图表自包含规范 (Self-Contained Rule)**：
> 1.  必须有完整的图题 (Title)。
> 2.  坐标轴必须包含单位：例如 `Latency (ms)`, `Throughput (req/s)`, `Goodput (weighted req/s)`。
> 3.  必须包含清晰的图例 (Legend)：NG, SRL, DP。
> 4.  必须带有 `yerr` 误差棒（3 次均值 ± 标准差）。
> 5.  必须带有简易 t-test 显著性标星 (`* p < 0.05`)。

* **[ ] 图 1: 核心指标全局摘要表**
    * 纵列 NG, SRL, DP；横列 Throughput (req/s), P95 Latency (ms), Goodput (weighted req/s)。
* **[ ] 图 2: Heavy Ratio 敏感性折线图 (Exp2)**
    * X 轴：`Heavy Ratio (%)`；Y 轴：`Throughput & Goodput`。证明重载下 DP 产出不掉。
* **[ ] 图 3: 按预算分组通过率柱状图 (Exp3)**
    * X 轴：`Budget Group (Low/High)`；Y 轴：`Success Rate (%)`。DP 高预算柱子加 `*`。
* **[ ] 图 4: Goodput 拆解堆叠柱状图 (Exp3)**
    * X 轴：`Gateway Strategy`；Y 轴：`Absolute Goodput Contribution`。拆分轻量与重量请求的贡献面积。
* **[ ] 图 5: Step 浪涌状态面积堆叠图 (Exp1)**
    * X 轴：`Time (s)`；Y 轴：`Requests/sec`。展示 Success/Reject/Error 的随时间变化趋势。
* **[ ] 图 6: 自适应机制消融实验柱状图 (Exp4)**
    * X 轴：`Gateway Strategy` (DP-Full, DP-NoRegime, SRL)；Y 轴：`Goodput`。加误差棒和 `*`。

## 📍 Phase 5: 论文撰写与 Future Work 占位 (1 天)
**目标：利用特定话术提升论文整体印象分。**

* **[ ] 5.1 💡[新增] 植入 Future Work 占位符**
    * 在论文最后一章 `Conclusion and Future Work` 中，直接原封不动地加入这句话：
        > *"In future work, we plan to deploy our DP gateway in a distributed cloud environment with real GPU-accelerated LLM inference, and evaluate its performance under long-tail real-world workload traces."*
    * **价值**：完美消除评审对“纯 CPU Mock 模拟缺乏真实场景”的挑刺，表明这是宏大计划的基石篇章。

