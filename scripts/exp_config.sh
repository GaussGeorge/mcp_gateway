#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Exp-Real-3 实验参数配置文件
# ═══════════════════════════════════════════════════════════════
#
# 用法:
#   1. 修改本文件中的参数
#   2. 运行: bash scripts/run_exp_real3_all.sh [--smoke] [--deepseek]
#   3. 脚本会自动 source 本文件
#
# 参数组快速切换: 直接修改下面的值即可
# ═══════════════════════════════════════════════════════════════

# ──────────────────────────────────────
# A. Agent 客户端参数
# ──────────────────────────────────────
AGENTS=50                   # 总 agent 数
CONCURRENCY=10              # agent 并发上限 (降低以减少 API 压力)
MAX_STEPS=8                 # 每个 agent 最大工具调用步数
BUDGET=300                  # 单次工具预算 token (收紧钱包，配合高基价实现经济治理)
ARRIVAL_INTERVAL=0.5        # agent 到达间隔 (秒), 放宽以减少瞬时压力

# ──────────────────────────────────────
# B. 后端 (MCP Server) 参数
# ──────────────────────────────────────
MAX_WORKERS=2               # 后端2线程, 中等压力甜蜜点
QUEUE_TIMEOUT=4             # 排队超时 (秒), 中等压力
CONGESTION_FACTOR=0.5       # 拥塞系数 (0.0~1.0)
BACKEND_PORT=8080           # 后端监听端口

# ──────────────────────────────────────
# C. SRL (令牌桶限流) 网关参数
# ──────────────────────────────────────
SRL_QPS=3                   # 令牌桶速率 (请求/秒), 严格限流
SRL_BURST=15                # 令牌桶最大容量
SRL_MAX_CONC=4              # SRL 最大并发连接数 (略高于worker数)

# ──────────────────────────────────────
# D. PlanGate (mcpdp-real) 网关参数
# ──────────────────────────────────────
PLANGATE_MAX_SESSIONS=12    # 最大并发会话数 (committed sessions + retry 保障完成)
PLANGATE_SESSION_CAP_WAIT=5 # Session Cap 排队等待超时 (秒), 快速反馈
PLANGATE_PRICE_STEP=400     # intensity 驱动基价 (K=0时396>300拒绝, K=1时263<300放行)
PLANGATE_SUNK_COST_ALPHA=0.5  # ReAct 沉没成本系数 (0=禁用)
REAL_RATELIMIT_MAX=200      # API 配额限制 (GLM=200, DeepSeek=60)
REAL_LATENCY_THRESHOLD=5000 # P95 延迟阈值 (ms), 超过则 latency pressure=1.0

# ──────────────────────────────────────
# E. 网关端口 (一般不需要改)
# ──────────────────────────────────────
NG_PORT=9001
SRL_PORT=9002
PLANGATE_PORT=9005

# ──────────────────────────────────────
# F. 工具执行延迟 (秒) — 模拟真实云工具的物理耗时
# ──────────────────────────────────────
TOOL_DELAY_LIGHTWEIGHT="1.5,3.0"    # calculate, text_format, real_weather
TOOL_DELAY_MEDIUM="2.5,5.0"         # real_web_search
TOOL_DELAY_HEAVYWEIGHT="0,0"        # deepseek_llm (已有真实延迟, 不额外加)

# ──────────────────────────────────────
# G. wo-SessionCap Real 模式端口
# ──────────────────────────────────────
PLANGATE_NOSESSCAP_PORT=9006

# ──────────────────────────────────────
# H. 多 LLM 提供者配置
# ──────────────────────────────────────
# DeepSeek-V3
DEEPSEEK_API_BASE="https://api.deepseek.com/v1"
DEEPSEEK_API_KEY="sk-082f52b3714846e18afc107e4ed7e031"
DEEPSEEK_MODEL="deepseek-chat"
DEEPSEEK_RATELIMIT_MAX=60    # DeepSeek RPM 限制较低
