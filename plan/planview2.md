我认真读完了。整体判断是：**这篇稿子有明显亮点，问题抓得很准，系统故事也完整，但按严格的 systems reviewer 视角，最大风险是“主张比真实证据略强”**。你最有价值的地方不是“比所有 baseline 都全面更强”，而是你把一个很容易被忽略的问题说清楚了：**multi-step agent 的治理单元和工作负载单元不一致，会造成 mid-session rejection 的级联浪费**；然后你用 **session commitment = atomic admission + temporal isolation + continuation value** 把这个问题抽象出来，这个 framing 是整篇 paper 最强的资产。

我觉得你这篇最强的地方有四个。第一，**问题定义很强**。ABD 这个指标抓住了语义层面的失败，而不是只看吞吐/延迟，这比单纯堆系统指标更容易让 reviewer 记住。第二，**P&S / ReAct 双模设计很顺**：P&S 走 pre-flight + reservation，ReAct 走 sunk-cost-aware discount，这不是为了复杂而复杂，而是和 agent 形态天然对齐。第三，**实验覆盖面够广**，不是只有 mock，还有 steady-state commercial API、bursty real-LLM、self-hosted vLLM，这说明你知道 reviewer 会质疑真实性。第四，**你对负结果比较诚实**，比如 steady-state real-LLM 里提升并不显著，你没有硬掰成全面领先，而是提成 no-regret，这反而增加可信度。

但 reviewer 最可能打你的点也很集中。最大的问题是**claim 强度和 evidence 强度不完全匹配**。你在摘要、引言里有不少很硬的表述，比如 hard commitment、deterministic protection、2× goodput，甚至“per-request governance cannot address, even in principle”这种话；可真正最贴近现实的结果，尤其是 steady-state real-LLM 那组，更像是在说：**资源弹性足时不吃亏，突发/后端受限时能显著降低 wasted work**。这其实已经是个很好的结论了，但它不是“全面碾压”。所以这篇最稳的主叙事应该是：**waste reduction + no-regret deployment**，而不是 generic superiority。Table 11 和 Table 12 其实已经把这个故事讲出来了。

第二个风险是**真实世界说服力还差最后一脚**。你最漂亮的 separation 主要来自 mock hard-capacity 实验；真实商业 API 的 steady-state 结果里，success/ABD 基本不显著，真正稳定的优势主要是 P95 tail latency 和 bursty 条件下的 waste reduction。顶会 reviewer 很可能会问：这类“后端容量受限、burst 明显、multi-step session 很重”的场景，在真实 MCP/agent deployment 里到底有多常见？现在 paper 给出的答案还不够“经验化”。换句话说，你的机制是合理的，但**workload realism 的 grounding 还可以再强一点**。

第三个风险是**baseline 与叙事一致性**有些小坑。比如 5.1 的 baseline 介绍和后面 Table 5 里出现的 SRL 并不完全一致；PlanGate 的 `max_sessions=30` 与 SBAC 的 150 差距很大，虽然你后面解释了“优势不只是 tighter cap”，但 reviewer 看到数字时会先怀疑 fairness；Rajomon 只扫 `price_step` 也可能被问是不是调参还不够充分。它们不是致命问题，但都是会消耗 rebuttal 精力的点。

从论文写作本身看，我会给你一个**“已经有成熟论文感，但还能再收一轮刀”**的评价。摘要现在太满了，数字、设置、N 值、边界条件塞得太多，读者反而不容易抓住一句 take-away。主文也有点“想把所有合理内容都放进去”，结果是主线被冲淡：reputation/security、Why not Envoy/Kong、discount 理论推导这些都不是坏内容，但一起放主文，会让论文看起来贡献面很宽、焦点略散。其实你的主文只要牢牢抓住四件事就够了：**问题、抽象、设计、三组关键证据（Table 4 / Table 11 / Table 12）**。其余很多内容都可以弱化或放 appendix。

还有几个必须修的细节。模板上的 **“Conference’17, July 2017, Washington, DC, USA”** 和匿名占位符如果这是 final version 一定要处理；图的出版感也还差一点，像 `price_step` 这种原始下划线、部分 caption 过于论断式，都会让 reviewer 潜意识里觉得不够 polished。另一个概念上的点是：**P&S 的 budget-reserved commitment 和 ReAct 的 soft commitment 不是同强度保证**。你文中其实知道这一点，但最好在摘要和引言最前面就明说，不然 reviewer 容易抓“这不是统一 guarantee”来打。

如果按“能不能过审”来讲，我的判断是：**有明显接受潜力，但要靠收敛主张来变稳**。投偏 agent systems / infra 的 venue，我觉得竞争力很强；按更苛刻的 systems 顶会口味，它会比较两极分化——喜欢的人会很喜欢你的 problem framing 和系统抽象，不喜欢的人会抓住“real-world gains mostly no-regret, not dramatic improvement”这个点。

你现在最值得做的三件事是：第一，把标题、摘要、引言统一成“**把 mid-session failure 尽量前移成 step-0 rejection，从而减少 doomed sessions 和 cascading waste**”；第二，统一 baseline、术语、单位和表述口径，把所有容易被抓的小问题清掉；第三，把主文压缩一点，让 reviewer 读完只记住一个核心句子。

如果你要，我可以下一条直接按**顶会审稿意见格式**给你写一版完整 review。







可以，下面我按**顶会审稿意见**的风格，给你写一版相对正式、可直接参考的 review。

---

# Review for “PlanGate: Session Commitment for Multi-Step LLM Agent Tool Governance”

## 1. Paper Summary

本文关注 multi-step LLM agent 在工具调用场景下的治理问题。作者指出，现有治理机制通常以单次请求为单位，但 agent 的实际工作负载单位是多步 session，因此当一个 session 在中途某一步被拒绝时，前面已经投入的工具执行与 LLM 推理成本都会被浪费。为此，论文提出了 **session commitment** 这一抽象，定义其应包含三个性质：**atomic admission、temporal isolation 和 continuation value**。在此基础上，作者实现了一个 MCP-compatible gateway——**PlanGate**：对 Plan-and-Solve 类 agent 通过 pre-flight admission + budget reservation 提供更强的承诺保护；对 ReAct 类 agent 通过 sunk-cost-aware discount 提供软承诺。实验部分包含 mock hard-capacity 场景、steady-state commercial API 场景、bursty real-LLM 场景以及 self-hosted vLLM 场景，核心指标是 admitted-but-doomed rate (ABD)，并报告 success rate、goodput、tail latency、cascade waste 等结果。论文的主要结论是：在资源约束显著、突发明显或后端 worker 受限时，session-level governance 能显著减少 doomed sessions 与 cascading waste；而在 provider-managed、资源相对充裕的 steady-state 商业 API 场景中，PlanGate 至少表现为一个 no-regret operating point。 

## 2. Overall Assessment

这是篇**问题意识很强、系统抽象也比较完整**的论文。作者抓住了一个当前 agent systems 中确实重要、但在传统 microservice overload control 里没有被正面刻画的问题：**治理单元与 workload 单元不一致所引发的 mid-session rejection 和级联浪费**。论文最有价值的部分不是具体哪个公式或某个工程技巧，而是把这个问题提升成了一个较清晰的系统抽象，即 session commitment。这个 framing 是有新意的，也容易让人记住。 

同时，这篇论文也存在比较明显的风险：**主张强度略高于证据强度**。在 mock hard-capacity 条件下，效果非常漂亮；但在更贴近真实部署的 steady-state commercial API 设置下，PlanGate 的优势主要体现为“不会更差、P95 更稳”，而不是全面、大幅领先。论文自己其实也给出了这个边界：Table 11 中各方法 success/ABD 差异并不显著，而 bursty real-LLM 场景下更清晰的收益是**waste reduction**，不是 success rate 的显著提升。也就是说，这篇论文最稳妥的主叙事应当是：**session commitment 在受限和突发条件下能够把 mid-session failure 前移成 step-0 rejection，从而减少浪费；在资源更弹性的场景下至少是 no-regret 的治理默认项**。当前版本有些段落仍写得过满、过硬。 

## 3. Strengths

### (1) Problem formulation is strong and timely

论文提出的核心问题非常好：传统 per-request governance 并不等价于 multi-step agent 的正确治理，因为后者的价值单位是整个 session，而不是单个 tool call。这个问题不仅成立，而且通过 admitted-but-doomed rate (ABD) 被很好地 operationalize 了出来。ABD 作为主指标，比单看吞吐、拒绝率、延迟更贴近 agent workload 的真实语义。 

### (2) The abstraction is clean and memorable

作者把 session commitment 拆成 atomic admission、temporal isolation、continuation value 三部分，这种 decomposition 很清楚，也确实能解释为什么现有方法做不到“session-level protection”。特别是把 P&S 和 ReAct 区分成两种 commitment 语义：前者是 budget-reserved commitment，后者是 sunk-cost-aware soft commitment，这个设计比较自然，不是为了复杂而复杂。 

### (3) System design is coherent

PlanGate 的实现逻辑比较顺：P&S 用 DAG 预检与 reservation，ReAct 用 step-aware discount；两者通过统一 gateway 和 header-driven routing 串起来。文章在系统层面并没有停留在概念宣言，而是给出了一套可以运行的设计，包括 price engine、session manager、intensity tracking、reservation TTL、plan drift 处理等。作为 systems paper，这一点是加分项。 

### (4) Evaluation has breadth and is relatively honest

实验设计覆盖了 mock、steady real-LLM、bursty real-LLM、self-hosted inference 几层场景，说明作者意识到仅靠模拟环境不足以建立说服力。更重要的是，论文没有把所有结果都硬说成“显著更好”：在 steady-state GLM-4-Flash 实验里，作者明确写出 success/ABD 无显著差异，并把 PlanGate 的价值表述为 no-regret property；在 bursty real-LLM 中则把重点转为 doomed-session reduction 和 cascade waste reduction。这种写法比“全面碾压”更可信。 

## 4. Weaknesses

### (1) Claims are stronger than the strongest evidence supports

这是我最大的 concern。摘要、引言和贡献总结里有一些很强的表述，比如 “per-request governance cannot address, even in principle”、“deterministic protection”、“2× goodput of the best per-request pricing baseline”等。这些话在 mock hard-capacity 条件下大体成立，但在更现实的 steady-state commercial API 条件下，Table 11 显示各方法 success rate 和 ABD 非显著；而在 bursty real-LLM 条件下，Table 12 也显示 ABD 本身并没有显著下降，真正显著的是 PARTIAL 和 Cascade 的减少。换言之，论文最扎实的结论是“减少浪费、将失败前移”，而不是对一般性真实部署都显著提升任务成功率。当前版本在叙事上仍然有一点“把最好看的 mock 结果当成全局结论”的倾向。 

### (2) Real-world grounding is still not fully convincing

论文在真实 API 条件下的结果更接近“边界刻画”而非“强验证”。这本身没有问题，但 reviewer 会追问：现实中的 MCP/agent deployment，到底多常处于本文强调的那种后端受限、burst 明显、session 长且价值链式累积的 regime？论文展示了这种 regime 下 PlanGate 很有价值，却没有充分证明这是足够常见或足够关键的 production setting。也就是说，机制是对的，但 workload realism 还差一些经验支撑。 

### (3) Baseline fairness can be questioned

论文花了不少力气讲 baseline fairness，但仍有几个地方容易被 reviewer 抓住。比如，PlanGate 的 `max_sessions=30` 而 SBAC/PP 是 150，这在视觉上会非常刺眼，即便作者后文解释“优势不只是 tighter cap”也未必足以立即消除疑虑。另一个例子是 Rajomon 的 sensitivity analysis 只扫了 `price_step`，reviewer 很可能会问是否还有其他关键参数共同影响结果。此外，正文前面对 baseline 的介绍与后面个别实验表格里出现的系统名称也有一些不完全一致的感觉，容易造成读者困惑。 

### (4) Two commitment semantics are not equally strong, but the writing sometimes blurs them

P&S 模式下的保护依赖 declared plan validity、backend availability 与 reservation scope；ReAct 模式下则只是 continuation-aware 的 soft commitment。论文正文其实承认了这一点，也在多个地方强调 boundary conditions，但摘要和引言里的写法有时仍让人产生一种“PlanGate 统一提供某种强承诺”的印象。对于严苛 reviewer 来说，这会被认为概念包装略重。更安全的做法是从一开始就明确：**P&S 是 scoped hard-ish protection，ReAct 是 soft protection**。 

### (5) The paper is slightly overpacked

文章想证明的东西很多：新抽象、双模设计、mock 强结果、steady real no-regret、bursty waste reduction、自托管验证、安全与信誉机制、Why not Envoy/Kong、理论分析、fairness。每一块单独看都不算没用，但全部塞入主文后，主线会变得有点散。读完之后最应当让 reviewer 记住的是“session commitment reduces doomed-session waste in multi-step agents”，而不是十几个子点。当前版本有一些内容其实更适合进 appendix。 

## 5. Detailed Comments

### Major comment 1: Reframe the main takeaway around waste reduction, not broad superiority

如果我是作者，我会把整篇文章的主叙事进一步收敛成一句话：**PlanGate turns wasteful mid-session failures into cheap step-0 rejections under multi-step agent workloads**。这句话比“session commitment solves per-request governance insufficiency”更稳，也和最强证据最一致。Table 4、Table 11、Table 12 实际上已经共同支持了这个更精确的 narrative：mock 下差距巨大；steady-state real API 下 no-regret；bursty real-LLM 下显著减少 doomed sessions 和 cascade waste。 

### Major comment 2: Tighten the scope of “deterministic” or “hard” claims

当前稿子在多个地方写到 P&S 的 deterministic protection，但正文自己又说明 reservation 只在 declared scope 内有效，并受 TTL、plan drift、undeclared substitution、backend failure 等条件限制。建议把这类表述统一改成类似：**price-induced mid-session rejection is eliminated within the declared plan scope under reservation-valid and backend-available execution**。这会显著减少被 reviewer 抓语义漏洞的风险。 

### Major comment 3: Clarify baseline parameterization and fairness much more aggressively

SBAC/PP 与 PlanGate 的 session cap 设置差异，需要在正文主结果附近就主动解释，而不是主要留到 limitations 里解释。否则 reviewer 一眼看表，很容易产生“你只是 admission 更狠”这种第一印象。建议直接在主文中明确给出一个简洁解释：PlanGate 的收益并不来自单纯 tighter cap，因为 PG-noRes 与 PlanGate 使用相同 cap，但结果差异仍然巨大。这个论证你已经有了，但现在放置位置不够“防守型”。 

### Major comment 4: The real-LLM section should be presented as boundary characterization rather than validation of the same claim

我认为 §5.10–§5.12 最好的组织方式不是“更真实，所以更强验证”，而是“刻画 benefit boundary”。目前文章其实已经部分这么写了，但还不够彻底。steady-state real-LLM 的真正价值不在于证明显著 superiority，而在于证明**在 provider-managed elastic regime 中，session commitment 不会带来额外代价**；bursty real-LLM 的价值则在于说明**当 bottleneck 回到 backend worker scarcity 时，waste reduction 能重新显现**。如果这样写，逻辑会比现在更加自洽。 

### Minor comment 1: The abstract is overloaded

摘要塞了太多数字、实验条件和限定词，虽然信息很全，但读者不容易抓住一条主线。建议压缩为三层：问题、方法、边界化结论。很多细节数字可以留到正文或 footnote。 

### Minor comment 2: There are still polish issues inconsistent with a “final version”

稿件里还有明显模板残留，如 “Conference’17, July 2017, Washington, DC, USA” 和匿名占位符。若这是 final version，这类问题必须清理，否则会非常伤观感。图表中诸如 `price_step` 这样的原始命名也偏像实验草稿，不像最终成稿。 

### Minor comment 3: Some content may be better moved to appendix

例如 Why not Envoy/Kong 的大段展开、信誉机制的完整细节、部分理论推导与扩展讨论，如果篇幅受限，建议优先保住 problem framing、system design、main empirical takeaway，把次主线内容收缩。 

## 6. Questions for the Authors

1. 论文最强的真实世界收益体现在 backend-limited burst overload 下的 waste reduction，而不是 steady-state success improvement。作者是否能进一步说明，这类 regime 在真实 MCP/agent deployment 中的代表性有多强？有没有来自真实 trace、服务日志或公开 workload 的支持？ 

2. SBAC/PP 与 PlanGate 的 session cap 设定差异较大。作者能否提供一组更直接的 cap-matched comparison，或者至少在主文中更突出 PG-noRes 的 role，以更有力地证明收益来源于 reservation + pricing composition，而非更保守 admission？ 

3. Rajomon baseline 仅对 `price_step` 做了敏感性扫描。是否还有其他关键参数会改变其表现？作者为什么认为当前 sweep 已足以代表 Rajomon 的 best-case？ 

4. 对于 ReAct 模式，continuation-aware discount 在 loop / error-cycle 场景下可能保护“注定无效”的 session。虽然文章在 limitations 里提到了这个问题，但是否有任何实验观察表明它在当前 workload 中并不严重？ 

## 7. Recommendation

**Overall: Weak Accept / Borderline Accept**

我会给这篇论文一个偏正面的评价。它有一个很清晰、值得 systems 社区关注的问题切口，也提出了一个有辨识度的抽象，并且在 mock 与部分 real-LLM 场景中给出了有说服力的证据。最大的风险不在于方法没价值，而在于**写法有时把“在特定受限 regime 下很有价值”写成了“对一般真实场景普遍强于 baseline”**。如果作者能收紧 claim、强化对真实边界的定位、把 baseline fairness 防守做得更主动，这篇论文会稳很多。 

## 8. Confidence

**Confidence: 4 / 5**

我对自己的判断比较有把握。论文的 strongest claim 与 strongest evidence 之间存在轻微错位，这一点我认为是客观且重要的；但我也认为该工作的问题定义和抽象本身具有明显价值，不应因为 real-LLM steady-state 提升不够 dramatic 就被低估。 

---

如果你愿意，我下一条可以继续帮你写一版**更像真实审稿系统里那种英文 review**，或者直接把这篇 review 改成**“作者 rebuttal 应对清单”**。