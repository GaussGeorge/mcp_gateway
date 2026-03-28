### 🔴 动作 3：人工介入——核心数据审计 (Sanity Check)

这是决定你论文生死的 10 分钟。跑完 Exp 1 和 Exp 4 后，打开生成的 CSV 文件，**像审稿人一样用极其苛刻的眼光核对以下三个“铁律”**：

**铁律 1：Rajomon 的算力浪费是否被成功打出？**
* **检查点**：在 Exp 1 的表格里，Rajomon 的 `Raw Goodput`（单步成功）是不是很高，但 `Effective Goodput`（全链路成功）出现了断崖式下跌？它的 `Cascade Failed` 数量是不是爆表了？
* **如果没出现**：说明你的压测压力不够大（后端还没被压死）。你需要立刻去调大 `run_all_experiments.py` 里的 `concurrency` 或 `qps`，**必须让没有预检准入的 Rajomon 出现严重的半路夭折！**

**铁律 2：PlanGate 的“零级联失败”是否兑现？**
* **检查点**：在 Exp 1 的表格里，PlanGate-Full 的 `Reject At Step 0` 应该很高，但是 `Cascade Failed` 必须接近 0，且 `Effective Goodput` 稳居第一。
* **如果没出现**：说明你的预检准入或预算锁存在漏网之鱼，需要去查 Go 网关的日志。

**铁律 3：消融实验的逻辑是否自洽？**
* **检查点**：在 Exp 4 的表格里，对比 `plangate_full` 和 `mcpdp-nolock`。在突发洪峰下，`mcpdp-nolock` 因为没有预算锁保护，一定会出现任务执行到第 3 步时突然被拒的情况，导致 `Effective Goodput` 显著低于 `plangate_full`。
* **如果两者差不多**：说明突发时间太短，或者价格涨得不够猛。调高 `qps_pattern` 中突发阶段的峰值！


