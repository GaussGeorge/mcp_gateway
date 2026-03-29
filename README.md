# PlanGate: Plan-Aware Gateway for MCP Tool Governance

## 项目简介

PlanGate 是一个面向 MCP（Model Context Protocol）工具调用场景的计划感知治理网关。通过预算锁定（BudgetLock）和会话容量控制（SessionCap）两大核心机制，解决 LLM Agent 多步执行中的**级联算力浪费**和**拥塞涨价导致的会话夭折**问题。

### 三层实验架构

```
DAG 负载生成器（模拟 LLM Agent）
    ↓  HTTP
Go 治理网关（6 种策略可切换）
    ↓  HTTP
Python MCP 后端（max_workers=10）
```

### 被测网关策略

| 策略 | 说明 |
|------|------|
| **No-Gov (NG)** | 无治理直通 |
| **SRL** | 静态速率限制 |
| **Rajomon** | 基于排队延迟的准入控制 |
| **DAGOR-MCP** | 基于 RTT 阈值的过载检测 |
| **SBAC-MCP** | 基于会话数上限的准入控制 |
| **PlanGate-Full** | 本文提出：预算锁定 + 会话容量控制 |

---

## 环境要求

- **操作系统**: Ubuntu 22.04（推荐 WSL2）
- **Go**: 1.21+（用于编译网关）
- **Python**: 3.10+（用于后端和发压机）
- **CPU**: 建议 8 核以上（实验使用 taskset 绑核隔离）

### Python 依赖

```bash
pip3 install aiohttp numpy matplotlib optuna
```

---

## 快速开始

### 1. 编译 Go 网关

```bash
cd /path/to/mcp-governance-main
GOOS=linux GOARCH=amd64 go build -o gateway_linux ./cmd/gateway
```

### 2. 运行全部实验（推荐方式）

```bash
python3 scripts/run_all_experiments.py \
    --exp-list Exp1_Core Exp2_HeavyRatio Exp3_MixedMode Exp4_Ablation Exp5_ScaleConc Exp7_ClientReject \
    --repeats 5 \
    --gateway-binary ./gateway_linux \
    --cpu-backend 8-15 \
    --cpu-gateway 4-7 \
    --cpu-loadgen 0-3
```

**参数说明**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--exp-list` | 待运行实验列表（空格分隔） | — |
| `--exp` | 运行单个实验（或 `all`） | `all` |
| `--repeats` | 每组重复次数 | 5 |
| `--gateway-binary` | 预编译网关二进制路径 | 无（自动编译） |
| `--cpu-backend` | 后端 taskset 绑核 | 无 |
| `--cpu-gateway` | 网关 taskset 绑核 | 无 |
| `--cpu-loadgen` | 发压机 taskset 绑核 | 无 |
| `--backend` | Python MCP 后端地址 | `http://127.0.0.1:8080` |
| `--backend-max-workers` | 后端并发 worker 数（0=外部启动） | 10 |
| `--dry-run` | 只打印计划不实际运行 | False |

### 3. 生成图表

```bash
python3 scripts/visualize_results.py
```

图表输出至 `results/figures/`。

### 4. 查看聚合结果（含标准差）

```bash
python3 scripts/aggregate_results.py
```

---

## 实验矩阵

| 实验 | 目的 | Sessions | Concurrency | Sweep 参数 | 网关数 | 总 Trial 数 |
|------|------|----------|-------------|-----------|--------|------------|
| **Exp1_Core** | 核心性能对比 | 500 | 200 | — | 6 | 30 |
| **Exp2_HeavyRatio** | 重量工具占比敏感性 | 200 | 20 | heavy_ratio=[0.1,0.3,0.5,0.7] | 6 | 120 |
| **Exp3_MixedMode** | PS/ReAct 混合模式 | 200 | 20 | ps_ratio=[0.0,0.3,0.5,0.7,1.0] | 6 | 150 |
| **Exp4_Ablation** | 消融实验 | 500 | 200 | — | 4 | 20 |
| **Exp5_ScaleConc** | 并发扩展性 | 200 | 20 | concurrency=[10,20,40,60] | 6 | 120 |
| **Exp7_ClientReject** | 客户端价格缓存 TTL | 500 | 200 | price_ttl=[0.1,0.2,0.5,1.0,2.0] | 1 | 25 |

**总计**: 465 trials × 每 trial 约 60s ≈ 约 8 小时

---

## 核心指标

| 指标 | 含义 |
|------|------|
| **Effective Goodput/s** | 仅计 SUCCESS 会话的有效吞吐率 |
| **Cascade Failures** | 多步执行中途因拒绝或超时而浪费前序算力的会话数 |
| **REJECTED@S0** | 在第 0 步（入口）即被拒绝的会话数（无算力浪费） |
| **P50/P95/P99** | 单步请求延迟百分位（ms） |
| **E2E P50/P95/P99** | 成功会话端到端延迟百分位（ms） |

---

## 目录结构

```
mcp-governance-main/
├── cmd/gateway/          # Go 网关入口
├── plangate/             # PlanGate 核心实现（BudgetLock + SessionCap）
├── baseline/             # 基线网关实现（NG, SRL, Rajomon, DAGOR, SBAC）
├── rajomon/              # Rajomon 算法独立包
├── mcp_server/           # Python MCP 后端
│   ├── server.py         # 后端主程序
│   └── tools/            # 工具注册（calculator, weather, web_search 等）
├── scripts/
│   ├── run_all_experiments.py   # 自动化实验脚本（主入口）
│   ├── dag_load_generator.py    # DAG 负载发压机
│   ├── visualize_results.py     # 图表生成（含误差棒）
│   ├── aggregate_results.py     # 结果聚合（含标准差）
│   └── tune_baselines.py        # Optuna 基线调优
├── results/              # 实验结果（CSV + 图表）
│   ├── exp1_core/
│   ├── exp2_heavyratio/
│   ├── exp3_mixedmode/
│   ├── exp4_ablation/
│   ├── exp5_scaleconc/
│   ├── exp7_clientreject/
│   └── figures/          # 所有图表 PNG
└── prompt/
    └── experiment_design_v2.md  # 详细实验设计文档
```

---

## 调优参数（Optuna 搜索结果）

基线网关使用 `scripts/tune_baselines.py` 经 Optuna 搜索得到的最优参数：

```python
TUNED_PARAMS = {
    "rajomon":       {"price_step": 20},
    "dagor":         {"rtt_threshold": 400.0, "price_step": 10},
    "sbac":          {"max_sessions": 150},
    "srl":           {"qps": 65.0, "burst": 400, "max_conc": 55},
    "plangate_full": {"price_step": 40, "max_sessions": 30},
}
```

如需重新调优：

```bash
python3 scripts/tune_baselines.py --gateway-binary ./gateway_linux --trials 150
```

---

## WSL2 绑核建议

为保证实验可复现性，建议使用 taskset 进行 CPU 隔离：

```
CPU 0-3:   负载生成器 (--cpu-loadgen 0-3)
CPU 4-7:   Go 网关    (--cpu-gateway 4-7)
CPU 8-15:  Python 后端 (--cpu-backend 8-15)
```

---

## 单独运行某个实验

```bash
# 只跑 Exp1
python3 scripts/run_all_experiments.py --exp Exp1_Core --repeats 5 --gateway-binary ./gateway_linux

# 只跑 Exp4 消融
python3 scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 5 --gateway-binary ./gateway_linux

# 预览执行计划（不实际运行）
python3 scripts/run_all_experiments.py --exp-list Exp1_Core Exp2_HeavyRatio --dry-run
```

## License

MIT
