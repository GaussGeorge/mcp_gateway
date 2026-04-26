# PlanGate 实验 → 源码完整映射

> 自动生成于 2026-04-20 ·覆盖 Tier 1 (Mock) / Tier 2 (Steady Real-LLM) / Tier 3 (Bursty Real-LLM + Self-hosted)

---

## 一、Go 源码机制映射

### 1.1 核心引擎（根目录，package `mcpgov`）

| 文件 | 论文机制 | 关键函数/结构体 |
|---|---|---|
| `mcp_governor.go` | **动态定价引擎**：Token-Price 市场机制核心 | `MCPGovernor` 结构体、`LoadShedding()`（服务端过载拒绝）、`RateLimiting()`（客户端限流）、`HandleToolCall()`（MCP 工具调用入口）、`HandleToolCallDirect()`（直接调用）、`ClientMiddleware()`、`UpdateResponsePrice()` |
| `mcp_init.go` | **参数初始化**：所有治理参数默认值与 Option 注入 | `NewMCPGovernor()` 构造函数（priceStep/decayStep/smoothingWindow/integralThreshold/regimeWindow 等全参数） |
| `tokenAndPrice.go` | **令牌分配 + 价格聚合**：多步链路的预算分配与价格传播 | `SplitTokens()`（下游预算分配）、`RetrieveDSPrice()`（下游聚合价格）、`RetrieveTotalPrice()`（总价 = ownPrice + downstreamPrice）、`UpdateOwnPrice()`（涨/降价逻辑） |
| `overloadDetection.go` | **过载检测**：三维过载检测 + Load Regime 自适应档位 | `queuingCheck()`（Go runtime 调度延迟检测）、`latencyCheck()`（业务延迟检测）、`throughputCheck()`（吞吐量检测）、`initAdaptiveProfiles()`（自适应档位初始化）、`maybeApplyAdaptiveProfile()`（档位切换）、`ApplyAdaptiveProfileSignal()`、`GetDetectorParams()` |
| `queuingDelay.go` | **排队延迟统计**：Go runtime/metrics 直方图分位数计算 | `medianBucket()`（P50）、`percentileBucket()`（P99/P90）、`maximumBucket()`（最大桶） |
| `mcp_protocol.go` | **MCP 协议类型**：JSON-RPC 2.0 + MCP 扩展 | `JSONRPCRequest`、`JSONRPCResponse`、`RPCError`、自定义错误码 `CodeOverloaded`/`CodeRateLimited`/`CodeTokenInsufficient` |
| `mcp_transport.go` | **HTTP 传输层**：MCP 标准 HTTP 服务端 | `MCPServer`、`ServeHTTP()`（路由 initialize/tools-list/tools-call/ping） |
| `logger.go` | 调试日志 + 价格追踪埋点 | `logger()`、`recordPrice()` |

### 1.2 PlanGate 模块（`plangate/`，package `plangate`）

| 文件 | 论文机制 | 关键函数/结构体 |
|---|---|---|
| `server.go` | **MCPDPServer**：PlanGate 完整网关（集成四大创新机制） | `MCPDPServer` 结构体、`NewMCPDPServer()`——创建含 BudgetMgr/ReactSession/DiscountFunc/IntensityTracker/ReputationMgr 的网关 |
| `http_handlers.go` | **请求分发**：P&S 首步预检 + 后续步锁价 + ReAct 沉没成本 | `ServeHTTP()`、`handlePlanAndSolveFirstStep()`（创新点 1: Pre-flight Atomic Admission）、`handlePlanAndSolveFollowUp()`（创新点 2: Budget Reservation 锁价放行）、`handleReActStep()`（创新点 3: 沉没成本折扣定价） |
| `session_manager.go` | **创新点 2: Budget Reservation（预算锁）** | `HTTPBudgetReservationManager`、`Reserve()`（锁定当前价格快照）、`Get()`（查找会话预留）、`Advance()`（推进步骤） |
| `dag_validation.go` | **DAG 校验**：Kahn 拓扑排序验证无环 | `HTTPDAGPlan`、`HTTPDAGStep`、`validateHTTPDAG()` |
| `discount_func.go` | **折扣函数族**：4 种沉没成本折扣函数 | `QuadraticDiscount()`（K²，默认）、`LinearDiscount()`（K）、`ExponentialDiscount()`（e^Kα）、`LogarithmicDiscount()`（ln(1+K)）、`GetDiscountFunc()` |
| `governance_intensity.go` | **滞回门控**（mock 场景）：防止低并发瞬时正价误拒 | `GovernanceIntensityTracker`、`GetIntensity()`、`IsActive()` |
| `external_signal_tracker.go` | **三维信号融合**（真实 LLM 场景）：429 频率 + P95 延迟 + RateLimit-Remaining | `ExternalSignalTracker`、`NewExternalSignalTracker()`、`Report()`、`GetIntensity()` |
| `reputation.go` | **信誉管理**：防恶意 Agent（审稿人要求） | `ReputationManager`、`AdjustBudget()`（信誉折扣预算）、`IsBanned()`、`RecordDAGViolation()` |
| `dual_mode_routing.go` | **创新点 3: Dual-Mode Governance** 双模态路由 | 有 X-Plan-DAG → P&S 模式；无 → ReAct 模式 |

### 1.3 基线对照（`baseline/`，package `baseline`）

| 文件 | 论文基线 | 缩写 | 核心机制 |
|---|---|---|---|
| `ng_gateway.go` | No Governance | NG | 全部透传，无准入控制 |
| `srl_gateway.go` | Static Rate Limit | SRL | 令牌桶 QPS + 最大并发数 |
| `rajomon_gateway.go` | Rajomon (OSDI'25) | Rajomon | Per-request Token-Price，排队延迟驱动涨降价，固定参数 |
| `rajomon_session_gateway.go` | Rajomon + Session Bookkeeping | Raj+SB | Rajomon 定价 + 会话跟踪（不影响定价决策） |
| `sbac_gateway.go` | Session-Based Admission Control | SBAC | 全局并发会话数限制，step-0 准入/后续无条件放行 |
| `progress_priority_gateway.go` | Progress Priority | PP | 并发会话限制 + 进度分数抢占（驱逐低进度会话） |
| `dagor_gateway.go` | DAGOR (SoCC'18) | DAGOR | 业务优先级 + RTT 过载检测 + 优先级脱落（中间步也丢弃） |

### 1.4 网关入口（`cmd/gateway/main.go`）

统一编译入口，`--mode` 参数路由到不同网关实现：

| `--mode` 值 | 对应网关 | 用于实验 |
|---|---|---|
| `ng` | `baseline.NGGateway` | 所有实验的 NG 基线 |
| `srl` | `baseline.SRLGateway` | Exp1-12 的 SRL 基线 |
| `rajomon` | `baseline.RajomonGateway` | Rajomon 基线/敏感性 |
| `rajomon-session` | `baseline.RajomonSessionGateway` | Raj+SB 基线 |
| `dagor` | `baseline.DagorGateway` | DAGOR 基线 |
| `sbac` | `baseline.SBACGateway` | SBAC 基线 |
| `pp` | `baseline.PPGateway` | PP 基线 |
| `mcpdp` | `plangate.MCPDPServer` | **PlanGate (mock 模式)** |
| `mcpdp-no-budgetlock` | PlanGate 消融变体 | Exp4 消融：禁用预算锁 |
| `mcpdp-no-sessioncap` | PlanGate 消融变体 | Exp4 消融：禁用会话上限 |
| `mcpdp-real` | PlanGate (真实 LLM 模式) | Tier 2/3 真实 LLM 实验 |
| `mcpdp-real-no-sessioncap` | PlanGate Real 消融 | Real-LLM 消融 |
| `dp` | `mcpgov.MCPServer`（原始 DP） | 早期实验（已被 PlanGate 替代） |
| `dp-noregime` | DP 无 Regime | 早期消融（已被 Exp4 替代） |

---

## 二、基础设施层（Python）

| 文件 | 角色 | 说明 |
|---|---|---|
| `mcp_server/server.py` | **MCP 后端**（被代理） | Python mock 工具服务端：calculate/web_fetch/weather/deepseek_llm 等工具，ThreadPoolExecutor 造成真实瓶颈 |
| `scripts/dag_load_generator.py` | **DAG 会话发压机** | 异步 P&S + ReAct 双模式会话，状态机管理，Raw/Effective 分离统计。**所有 Tier 1 mock 实验的核心驱动** |
| `scripts/load_generator.py` | **单步发压机**（早期） | Poisson/Step 波形，轻量+重量请求混合。早期 Exp 使用，后被 dag_load_generator 替代 |
| `scripts/react_agent_client.py` | **真实 ReAct Agent 客户端** | LLM function calling → 工具选择 → 网关 → 后端。**所有 Tier 2/3 真实 LLM 实验的核心驱动** |

---

## 三、实验 → 代码完整映射

### Tier 1 — Mock 仿真实验

#### Table 1 / Commitment Quality（7 网关对比）

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/smoke_test_week2.py` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG: `baseline/ng_gateway.go`; Rajomon: `baseline/rajomon_gateway.go`; Raj+SB: `baseline/rajomon_session_gateway.go`; SBAC: `baseline/sbac_gateway.go`; PP: `baseline/progress_priority_gateway.go`; PG-noRes: `plangate/` (mcpdp-no-budgetlock); PlanGate: `plangate/` (mcpdp) |
| **参数** | 200 sessions, C=200, ps_ratio=0.5, budget=500, 5 repeats |
| **结果目录** | `results/exp_week4_formal/` |
| **分析/图表** | `scripts/gen_ccfa_figures.py::fig_mock_cascade()` |

#### Rajomon Sensitivity（price_step 扫参）

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/rajomon_sensitivity.py` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | `baseline/rajomon_gateway.go` (mode=rajomon) |
| **参数** | price_step ∈ {5,10,20,50,100}, 200 sessions, C=200, 5 repeats |
| **结果目录** | `results/exp_rajomon_sensitivity/` |
| **分析/图表** | `scripts/plot_rajomon_sensitivity.py`, `scripts/gen_ccfa_figures.py::fig_rajomon_sensitivity()` |

#### Exp1: Core Performance

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp1_Core` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG/SRL/SBAC/PlanGate (4 网关) |
| **参数** | 500 sessions, C=200, ps_ratio=1.0, budget=500, heavy=0.3, 5 repeats |
| **结果目录** | `results/exp1_core/` |
| **分析/图表** | `scripts/gen_paper_figures.py::fig_exp1_cascade()`, `scripts/gen_ccfa_figures.py::fig_mock_cascade()` |

#### Exp4: Ablation Study

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp4_Ablation` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | PlanGate Full (mcpdp) / w/o-BudgetLock (mcpdp-no-budgetlock) / w/o-SessionCap (mcpdp-no-sessioncap) |
| **参数** | 500 sessions, C=200, ps_ratio=1.0, 5 repeats |
| **结果目录** | `results/exp4_ablation/` |
| **分析/图表** | `scripts/gen_paper_figures.py::fig_exp4_ablation()`, `scripts/gen_ccfa_figures.py::fig_ablation()` |

#### Exp8: Discount Function Ablation

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp8_DiscountAblation` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | `plangate/discount_func.go`（4 种折扣函数 × PlanGate）；mode=mcpdp + --plangate-discount-func |
| **参数** | 500 sessions, C=200, **ps_ratio=0.0（纯 ReAct）**, 5 repeats |
| **结果目录** | `results/exp8_discountablation/` |
| **分析/图表** | `scripts/gen_paper_figures.py::fig_exp8_discount()`, `scripts/gen_ccfa_figures.py::fig_discount_ablation()` |

#### Exp2: Heavy Ratio Sweep

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp2_HeavyRatio` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 200 sessions, sweep heavy_ratio ∈ {0.1, 0.3, 0.5, 0.7} |
| **结果目录** | `results/exp2_heavyratio/` |

#### Exp3: Mixed-Mode P&S+ReAct Sweep

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp3_MixedMode` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 200 sessions, sweep ps_ratio ∈ {0.0, 0.3, 0.5, 0.7, 1.0} |
| **结果目录** | `results/exp3_mixedmode/` |

#### Exp5: P&S Concurrency Sweep

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp5_ScaleConc` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 200 sessions, ps_ratio=1.0, sweep concurrency ∈ {10, 20, 40, 60} |
| **结果目录** | `results/exp5_scaleconc/` |

#### Exp6: ReAct Concurrency Sweep

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp6_ScaleConcReact` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 200 sessions, ps_ratio=0.0, sweep concurrency ∈ {10, 20, 40, 60} |
| **结果目录** | `results/exp6_scaleconcreact/` |

#### Exp7: Client Hard-Reject + Price TTL Sweep

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp7_ClientReject` |
| **发压机** | `scripts/dag_load_generator.py`（hard_reject 模式） |
| **网关代码** | PlanGate (mcpdp) |
| **参数** | 500 sessions, C=200, sweep price_ttl ∈ {0.1, 0.2, 0.5, 1.0, 2.0} |
| **结果目录** | `results/exp7_clientreject/` |

#### Exp9: Scale Stress

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp9_ScaleStress` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 500 sessions, sweep concurrency ∈ {200, 400, 600, 800, 1000} |
| **结果目录** | `results/exp9_scalestress/` |
| **分析/图表** | `scripts/gen_paper_figures.py::fig_exp9_scalability()`, `scripts/gen_ccfa_figures.py::fig_scalability()` |

#### Exp10: Adversarial

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp10_Adversarial` |
| **发压机** | `scripts/dag_load_generator.py`（adversarial_ratio=0.1） |
| **网关代码** | NG/SRL/SBAC/PlanGate + `plangate/reputation.go` |
| **参数** | 500 sessions, C=200, 10% 恶意 Agent |
| **结果目录** | `results/exp10_adversarial/` |
| **分析/图表** | `scripts/gen_paper_figures.py::fig_exp10_adversarial()`, `scripts/gen_ccfa_figures.py::fig_adversarial()` |

#### Exp11: Bursty Workload (Mock)

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp11_Bursty` |
| **发压机** | `scripts/dag_load_generator.py`（burst_pattern） |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 500 sessions, C=200, burst_pattern="15:10,80:8,15:10,120:7,15:10,50:5" |
| **结果目录** | `results/exp11_bursty/` |

#### Exp12: Long-Tail Workload

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_all_experiments.py --exp Exp12_LongTail` |
| **发压机** | `scripts/dag_load_generator.py`（longtail_ratio=0.2） |
| **网关代码** | NG/SRL/SBAC/PlanGate |
| **参数** | 500 sessions, C=200, 20% 会话 10-15 步 |
| **结果目录** | `results/exp12_longtail/` |

#### 补充：α Sensitivity Sweep (C3)

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_alpha_sweep.py` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | PlanGate (mcpdp) + NG |
| **参数** | α ∈ {0.2, 0.5, 0.8}, 200 sessions, C=200, 3 repeats |
| **结果目录** | `results/exp_alpha_sweep/` |

#### 补充：SBAC-30 (Cap-matched)

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_sbac30_experiment.py` |
| **发压机** | `scripts/dag_load_generator.py` |
| **网关代码** | `baseline/sbac_gateway.go` (max_sessions=30) |
| **参数** | 200 sessions, C=200, 5 repeats |
| **结果目录** | `results/exp_sbac30/` |

---

### Tier 2 — Steady-State Real LLM (GLM-4-Flash)

#### Real-LLM C=10 / C=40

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_real_llm_week5.py` (C=10 默认, `--concurrency 40` 得 C=40) |
| **Agent 客户端** | `scripts/react_agent_client.py`（真实 LLM function calling） |
| **网关代码** | NG / Rajomon / PP / PlanGate-Real (mcpdp-real) |
| **外部信号跟踪** | `plangate/external_signal_tracker.go`（429/P95/RateLimit-Remaining 三维融合） |
| **参数** | 200 agents, C=10 或 40, max_steps=10, budget=1000, GLM-4-Flash |
| **结果目录** | `results/exp_week5_C10/`, `results/exp_week5_C40/` |
| **分析/图表** | `scripts/gen_paper_figures.py::fig_cross_llm()` |

---

### Tier 3 — Bursty Real LLM + Self-hosted

#### Bursty Real-LLM (N=9)

| 项目 | 文件 |
|---|---|
| **Runner (主)** | `scripts/run_real_llm_bursty.py` (run1-5) |
| **Runner (补)** | `scripts/run_bursty_extra2.py` (run6-7, 只跑 NG + PlanGate) |
| **Agent 客户端** | `scripts/react_agent_client.py` |
| **网关代码** | NG / Rajomon / PP / PlanGate-Real (mcpdp-real, α=0.7, max_sessions=12) |
| **参数** | 200 agents, C=20, burst_size=30, burst_gap=8s, max_steps=15, budget=1000, GLM-4-Flash |
| **结果目录** | `results/exp_bursty_C20_B30/` |
| **统计分析** | `scripts/compute_bursty_n9.py`（N=9 统计 + t 检验） |

#### Self-hosted vLLM C=10

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_selfhosted_vllm.py` |
| **Agent 客户端** | `scripts/react_agent_client.py` |
| **网关代码** | NG / PlanGate-Real (mcpdp-real) |
| **后端** | `mcp_server/server.py` (mode=real_llm) + 本地 vLLM (Qwen3.5-4B, max-num-seqs=8) |
| **Agent Brain** | GLM-4-Flash（商业 API，可靠 function calling） |
| **参数** | 50 agents, C=10, burst_size=15, burst_gap=6s, max_steps=10, budget=800, max_workers=8 |
| **结果目录** | `results/exp_selfhosted_vllm_C10_W8/` |

#### Self-hosted vLLM C=20

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_selfhosted_c20.py` |
| **Agent 客户端** | `scripts/react_agent_client.py` |
| **网关代码** | NG / PlanGate-Real (mcpdp-real) |
| **参数** | 100 agents, C=20, burst_size=25, burst_gap=5s, max_workers=8 |
| **结果目录** | `results/exp_selfhosted_vllm_C20_W8/` |

#### 补充：DeepSeek Concurrency Sweep (C7)

| 项目 | 文件 |
|---|---|
| **Runner** | `scripts/run_deepseek_n3.py` |
| **Agent 客户端** | `scripts/react_agent_client.py` |
| **网关代码** | NG / PlanGate-Real (mcpdp-real) |
| **参数** | 50 agents, C ∈ {1, 3, 5}, max_steps=8, budget=300, DeepSeek-V3, 3 repeats |
| **结果目录** | `results/exp_deepseek_n3/`, `results/exp_conc_sweep_deepseek/` |
| **分析/图表** | `scripts/gen_ccfa_figures.py::fig_deepseek_sweep()` |

---

## 四、图表生成脚本映射

| 图表 | 生成脚本 | 函数 | 依赖数据 |
|---|---|---|---|
| Architecture | `gen_ccfa_figures.py` | `fig_architecture()` | 手绘 (matplotlib patches) |
| Mock Cascade Comparison | `gen_ccfa_figures.py` | `fig_mock_cascade()` | `exp_week4_formal/` |
| Ablation Study | `gen_ccfa_figures.py` | `fig_ablation()` | `exp4_ablation/` |
| Scalability | `gen_ccfa_figures.py` | `fig_scalability()` | `exp9_scalestress/` |
| Token Efficiency | `gen_ccfa_figures.py` | `fig_token_efficiency()` | `exp1_core/` |
| Fairness (Steps Boxplot) | `gen_ccfa_figures.py` | `fig_fairness()` | `exp1_core/` |
| DeepSeek Concurrency Sweep | `gen_ccfa_figures.py` | `fig_deepseek_sweep()` | `exp_conc_sweep_deepseek/` or `exp_deepseek_n3/` |
| Adversarial Robustness | `gen_ccfa_figures.py` | `fig_adversarial()` | `exp10_adversarial/` |
| Rajomon Sensitivity | `gen_ccfa_figures.py` | `fig_rajomon_sensitivity()` | `exp_rajomon_sensitivity/` |
| Discount Function Ablation | `gen_ccfa_figures.py` | `fig_discount_ablation()` | `exp8_discountablation/` |
| Cross-LLM Comparison | `gen_paper_figures.py` | `fig_cross_llm()` | `exp_week5_*` / `exp_bursty_*` |
| 心电图 (Price Timeseries) | `plot_paper_charts.py` | `plot_chart1_heartbeat()` | 网关运行日志 |
| 8 轮调优演进图 | `plot_paper_charts.py` | `plot_chart2_evolution()` | `results/evolution_8runs.csv` |
| 成功率 vs 吞吐 | `plot_paper_charts.py` | `plot_chart3_success_vs_goodput()` | `exp_week4_formal/` |
| 单任务 Token 效率 | `plot_paper_charts.py` | `plot_chart4_token_efficiency()` | `exp_week4_formal/` |
| 尾延迟 P50 vs P95 | `plot_paper_charts.py` | `plot_chart5_tail_latency()` | `exp_week4_formal/` |
| 步数公平性箱线图 | `plot_paper_charts.py` | `plot_chart6_fairness()` | `exp_week4_formal/` |
| Rajomon 敏感性 (独立) | `plot_rajomon_sensitivity.py` | `main()` | `exp_rajomon_sensitivity/` |

---

## 五、推荐复现执行顺序

### Phase 0: 环境准备
```bash
# 1. 编译 Go 网关（所有实验共用一个二进制）
go build -o gateway.exe ./cmd/gateway

# 2. 安装 Python 依赖
pip install -r mcp_server/requirements.txt
pip install aiohttp numpy matplotlib pandas scipy
```

### Phase 1: Tier 1 Mock 实验（无需外部 API，本地完成）

**执行顺序按实验复杂度递增排列：**

```bash
# Step 1: Rajomon 敏感性（独立实验，验证基线参数）
python scripts/rajomon_sensitivity.py --repeats 5

# Step 2: Table 1 / Commitment Quality（7 网关全量对比）
python scripts/smoke_test_week2.py --repeats 5

# Step 3: SBAC-30 补充实验
python scripts/run_sbac30_experiment.py --repeats 5

# Step 4: 跑全部 12 组 mock 实验（大约 4-6 小时）
python scripts/run_all_experiments.py --repeats 5

# 也可分组跑:
python scripts/run_all_experiments.py --exp Exp1_Core --repeats 5
python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 5
python scripts/run_all_experiments.py --exp Exp8_DiscountAblation --repeats 5
python scripts/run_all_experiments.py --exp Exp9_ScaleStress --repeats 5
python scripts/run_all_experiments.py --exp Exp10_Adversarial --repeats 5
python scripts/run_all_experiments.py --exp Exp11_Bursty --repeats 5
python scripts/run_all_experiments.py --exp Exp12_LongTail --repeats 5
# Exp2/3/5/6/7 参数鲁棒性实验
python scripts/run_all_experiments.py --exp Exp2_HeavyRatio --repeats 5
python scripts/run_all_experiments.py --exp Exp3_MixedMode --repeats 5
python scripts/run_all_experiments.py --exp Exp5_ScaleConc --repeats 5
python scripts/run_all_experiments.py --exp Exp6_ScaleConcReact --repeats 5
python scripts/run_all_experiments.py --exp Exp7_ClientReject --repeats 5

# Step 5: α 灵敏度扫描
python scripts/run_alpha_sweep.py --repeats 3
```

### Phase 2: Tier 2 Steady-State Real LLM（需要 GLM-4-Flash API Key）

```bash
# Step 6: Real-LLM C=10
python scripts/run_real_llm_week5.py --repeats 5

# Step 7: Real-LLM C=40（修改 CONCURRENCY 或传参）
# 需在脚本中设 CONCURRENCY=40 后执行
python scripts/run_real_llm_week5.py --repeats 5
```

### Phase 3: Tier 3 Bursty + Self-hosted（需要 GLM-4-Flash API + 可选 vLLM GPU）

```bash
# Step 8: Bursty Real-LLM (5 runs 主实验)
python scripts/run_real_llm_bursty.py --repeats 5

# Step 9: Bursty 补充 (run6-7)
python scripts/run_bursty_extra2.py --start-run 6 --count 2

# Step 10: N=9 统计汇总
python scripts/compute_bursty_n9.py

# Step 11: Self-hosted vLLM C=10（需本地 vLLM 服务）
python scripts/run_selfhosted_vllm.py --repeats 3

# Step 12: Self-hosted vLLM C=20
python scripts/run_selfhosted_c20.py --repeats 3

# Step 13: DeepSeek Concurrency Sweep（需 DeepSeek API Key）
python scripts/run_deepseek_n3.py --repeats 3
```

### Phase 4: 生成图表

```bash
# 生成全部 CCF-A 质量论文图表
python scripts/gen_ccfa_figures.py

# 备选图表脚本
python scripts/gen_paper_figures.py
python scripts/plot_paper_charts.py
python scripts/plot_rajomon_sensitivity.py
```

---

## 六、源码 → 论文机制速查

| 论文机制 | 主文件 | 辅助文件 |
|---|---|---|
| Pre-flight Atomic Admission (创新 1) | `plangate/http_handlers.go::handlePlanAndSolveFirstStep()` | `plangate/dag_validation.go` |
| Budget Reservation / 价格锁定 (创新 2) | `plangate/session_manager.go` | `plangate/http_handlers.go::handlePlanAndSolveFollowUp()` |
| Dual-Mode Governance (创新 3) | `plangate/dual_mode_routing.go` + `plangate/http_handlers.go::ServeHTTP()` | — |
| Sunk-Cost Continuation Pricing | `plangate/discount_func.go` | `plangate/http_handlers.go::handleReActStep()` |
| 动态定价（Token-Price Market） | `mcp_governor.go` + `tokenAndPrice.go` | `overloadDetection.go` |
| Load Regime 自适应档位 | `overloadDetection.go::initAdaptiveProfiles()` | `cmd/gateway/main.go::proxyOverloadDetector` |
| 排队延迟过载检测 | `overloadDetection.go::queuingCheck()` | `queuingDelay.go` |
| 代理架构过载检测 | `cmd/gateway/main.go::proxyOverloadDetector` | — |
| 滞回门控治理强度 (mock) | `plangate/governance_intensity.go` | — |
| 外部信号融合 (real LLM) | `plangate/external_signal_tracker.go` | — |
| Agent 信誉管理 | `plangate/reputation.go` | — |
| MCP 协议扩展 | `mcp_protocol.go` | `mcp_transport.go` |
