给你一版更适合冲击 EuroSys/ATC 这类 CCF-A 的重写方案。核心原则只有一句话：

不要再把论文讲成“一个更聪明的动态定价网关”，而要讲成“为多步 LLM Agent 引入一种缺失的治理原语：session commitment”。

**一、重写后的论文故事线与 Contributions 重排**

你现在最需要的不是继续加功能，而是把全文叙事收紧成一个不可替代的系统抽象。推荐主线如下。

1. 问题重述：现有系统不是“价格不够好”，而是“治理单位错了”  
传统 overload control 的治理单位是 request。但多步 Agent 的价值单位是 session。对 request 做局部最优，会在 session 层面产生全局浪费，也就是 cascade waste。  
Introduction 第一页就应该用一个 5-step 会话例子，把“第 4 步拒绝导致前 3 步全部作废”讲透。这里不要先谈公式，先谈 abstraction mismatch。

2. 核心洞察：多步 Agent 需要三种传统网关没有的语义  
建议把全文的理论中心收敛成三条：
atomic admission：开始执行前，系统要判断“这段会话值不值得开始”。  
temporal isolation：一旦接纳，后续负载波动不能把已接纳会话随意打断。  
continuation value：越接近完成的会话，被拒绝的代价越高，治理决策必须显式感知这一点。  
这样你的 Plan-and-Solve 与 ReAct 就不再是两套分裂机制，而是同一 abstraction 的两种实现方式。

3. 系统设计：PlanGate 是 session commitment 的一个实现，而不是一组 feature  
Plan-and-Solve 对应 atomic admission + reservation。  
ReAct 对应 continuation pricing + soft commitment。  
budget reservation 不是“小技巧”，而是 temporal isolation 的实现。  
$K^2$ 折扣不是“经验公式”，而是 continuation value 的一个凸近似。  
reputation system 不再是核心贡献，降为附加安全机制，放在后半部分或附录。

4. 理论目标：不证明 $K^2$ 最优，只证明为什么折扣必须单调下降且偏凸  
我建议用“风险暴露递增”而不是“最优排队论”来写。  
理论上要支撑的不是“为什么必须是二次”，而是：  
随着会话推进，拒绝造成的沉没损失和完成价值损失都非线性上升，因此 continuation discount 不能是常数，也通常不该是纯线性。  
然后再用实验说明：在线性、对数、指数、二次中，二次在吞吐与级联浪费之间提供了最稳的折中。  
这比硬凑全局最优性安全得多。

5. 实证主张：不是“PlanGate 比所有基线都好”，而是“简单的 progress favoritism 还不够”  
下一轮评审一定会问：你这是不是只是在优先照顾快完成的会话。  
所以实证主张要改成：  
仅有 session bookkeeping 不够。  
仅有 progress priority 不够。  
只有 commitment + reservation + continuation-aware admission 三者结合，才能真正消除 cascade waste，同时保持可接受的公平性和吞吐。  
这是你论文最关键的实验论断。

按这个逻辑，贡献建议重排为：

1. 新抽象  
提出 session commitment governance，用 atomic admission、temporal isolation、continuation value 三个性质刻画多步 Agent 的治理需求。

2. 新系统  
设计并实现 PlanGate，在静态计划与动态 ReAct 两类 Agent 下统一实现 session-level commitment。

3. 新证据  
通过 Rajomon、session bookkeeping、Progress-Priority 等强基线对照，证明简单 request-level pricing 或 progress favoritism 都不能替代 session commitment。

4. 新经验结论  
证明 PlanGate 的收益是 contention-dependent：资源越稀缺，session-aware governance 的价值越高；在低 contention 下收益有限但边界清晰。

下面这些内容建议降级，不要再占据摘要和贡献列表中心：
reputation-based security  
多 provider 横向铺开  
“首个”或“彻底解决”这类大话  
分布式完整生产化承诺

**二、实验补充清单**

这部分不要再按“还能做什么”发散，而要按“为了证明主张必须补什么”来组织。建议直接分成四组。

1. 必补的强基线矩阵  
Rajomon-faithful：保留其原本 per-request pricing 逻辑，作为最强 request-level baseline。你现有实现基础在 rajomon_gateway.go。  
Rajomon + Session Bookkeeping：只加会话跟踪，不加 commitment、不加 reservation、不加 sunk-cost 折扣。用于证明“看见 session”本身不够。  
Progress-Priority Admission：负载高时优先拒绝低进度会话，而不是做价格治理。用于证明“优先照顾快做完的会话”也不够。  
PlanGate w/o Reservation：保留 continuation discount，但去掉 price lock。用于证明 temporal isolation 的必要性。  
PlanGate Full：完整系统。  
如果你时间够，再加一个 Progress-Priority + Reservation 的组合基线，会更漂亮，因为它能进一步说明不是单个组件能解释收益。

2. 必补的 workload 维度  
High-contention steady load：保留你最强优势场景。  
Bursty load：短时间 5x 到 10x 突发，检验 commitment 在瞬时拥塞下的保护作用。  
Long-tail sessions：10% 到 20% 会话有 15 步以上，检验 continuation value 在深会话上的必要性。  
Mixed short/long tenants：短任务和长任务混部，检验 fairness 不是只靠 JFI，而是看不同会话长度上的资源分配。  
Low-contention boundary：主动证明在低负载时收益有限，从而把论文写成“适用边界清晰”而不是“全场景通吃”。

3. 必补的指标  
Success rate  
Cascade failure rate  
Effective goodput  
Rejected-at-step-0 rate  
Admitted-but-doomed rate：已放行但最终级联失败的比例，这是你“commitment 质量”的关键指标。  
P99 和 P99.9 admission latency  
End-to-end P99 session latency  
State memory per active session  
Go GC pause / lookup overhead / cleanup overhead  
Fairness split by short sessions vs long sessions，而不只是总 JFI  
Reservation hit ratio 和 stale reservation ratio  
Price volatility / decision instability，用来回应控制稳定性问题

4. 必补的真实 LLM 与扩展性证据  
真实 LLM 不要先追求 provider 数量，先做深一个高 contention provider。  
建议主打一个高 contention provider 做 200 到 500 sessions per configuration，另保留一个低 contention provider 作为边界验证。  
如果预算有限，宁可减少 provider，也不要继续维持小样本多平台。  
分布式部分建议做最小可行原型，而不是空谈：2 到 3 个 gateway 节点，session affinity，Redis 做共享轻状态。  
你不需要做完整生产级系统，但至少要量化：
sync overhead  
stale-state penalty  
错误接纳率是否显著上升  
throughput 是否线性改善  
如果这个原型做不完，就不要装作“已可平滑扩展”，而是老实把 scope 收成 single-gateway governance，并把分布式放进 limitations。

5. Token-aware 的取舍标准  
我建议把它作为二阶段增强，不要一开始写进核心贡献。  
先做离线分析：用已有真实实验日志看“步数”和“token sunk cost”是否高度相关。  
如果相关性高，token-aware 没必要进主线。  
如果相关性不高，再做一个小节作为 LLM-native extension。  
它的价值是把论文从“微服务味很重”拉向“LLM-native governance”，但前提是不要打散主线。

**三、按周执行的可落地计划**

下面这版按 8 周排，目标是冲 EuroSys/ATC 的强投稿版本。如果你只有 6 周，我建议砍掉 token-aware 和分布式原型，只保住主线、强基线、真实 LLM 扩样。

1. 第 1 周：锁定故事线，先验证最危险假设  
产出：一页 paper story memo。  
要做的事：  
把摘要、引言、贡献列表先重写成 session commitment 版本。  
实现一个最小版 Progress-Priority Admission 原型。  
跑一组小规模 mock 实验，只看它是否拿走了 PlanGate 大部分收益。  
决策门槛：如果 Progress-Priority 接近 PlanGate，你要立刻调整论文主张，不要继续沿当前故事线硬推。

2. 第 2 周：补强 Rajomon 与组件必要性对照  
产出：baseline matrix 初版结果表。  
要做的事：  
把 Rajomon-faithful、Rajomon+Session Bookkeeping、PlanGate w/o Reservation 跑通。  
形成最小必要性矩阵：  
request-level pricing 不够  
session bookkeeping 不够  
no-reservation 不够  
只有 full commitment 才能稳定压低 cascade waste  
这一周结束后，你应该已经知道论文的核心 claim 能不能立住。

3. 第 3 周：补理论，不求大而全，只求闭环  
产出：theory section 初稿。  
要做的事：  
写一个 continuation-value / risk exposure 框架。  
证明折扣需要单调下降，且在高风险区间需要偏凸。  
把 $K^2$ 定位为一个稳健的 convex design point，而不是 theorem-optimal。  
同时补一个控制稳定性的弱分析，讨论价格波动边界，不需要搞成纯控制论文。

4. 第 4 周：补 mock 侧的深度实验与开销分析  
产出：核心 mock 图表包。  
要做的事：  
跑 bursty load、long-tail、mixed tenant。  
补 P99/P99.9、state memory、GC pause、admission latency。  
把 fairness 改成分层 fairness，而不是只报总 JFI。  
这一周的目标是让评审无法再说“实验只有成功率和平均延迟”。

5. 第 5 周：真实 LLM 深挖高 contention provider  
产出：real-LLM 主结果。  
要做的事：  
选一个高 contention provider，拉到 200 到 500 sessions per configuration。  
只保留最必要的 baselines：NG、Rajomon、Progress-Priority、PlanGate。  
补 bursty arrival 或 mixed-length workload 至少一种。  
目标不是 provider 多，而是统计功效够。

6. 第 6 周：真实 LLM 边界验证  
产出：边界条件图与讨论段。  
要做的事：  
选一个低 contention provider 或低并发配置。  
明确展示收益收缩区间。  
把结论写成：PlanGate 的价值随 contention 提升，而非无条件通吃。  
这会显著改善评审对你结论可信度的判断。

7. 第 7 周：分布式最小原型或范围收缩  
产出：二选一。  
选项 A：2 到 3 节点 gateway + Redis + session affinity 的最小原型结果。  
选项 B：如果工程上超时，就停止投入，转而做非常扎实的 single-gateway scope 收缩，补 state overhead 微基准，并在 limitations 里清楚写出多节点挑战。  
不要半成品。分布式一旦做，就必须有量化图；做不出来，就明确收缩 scope。

8. 第 8 周：整稿重构与投稿级打磨  
产出：投稿版论文。  
要做的事：  
重写摘要、引言、related work、discussion、limitations。  
删除或弱化 reputation 作为核心贡献的表述。  
把 experiments 重排为“证明主张”的顺序，而不是“枚举所有结果”的顺序。  
附录放 α 敏感性、更多 provider 结果、安全扩展、token-aware 离线分析。  
最后按审稿口吻自查三遍：  
是不是核心抽象清楚  
是不是强基线足够强  
是不是边界条件讲诚实

**最后的取舍建议**

如果你只能保住三件事，我建议优先级是：

1. Progress-Priority 与 Rajomon 的强对照  
2. 高 contention 真实 LLM 大样本  
3. 把论文彻底改写成 session commitment 的故事

如果还能再保住第四件事，优先补最小分布式原型。  
如果还有余力，第五件事再考虑 token-aware extension。  
声誉系统排在最后，甚至可以只保留附录。

如果你愿意，我下一步可以继续直接给你一版“新的摘要 + 引言第一页 + Contributions 段落”的可写入论文草稿。