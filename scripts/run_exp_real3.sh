#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Exp-Real-3: 真·ReAct Agent 端到端验证实验
# ═══════════════════════════════════════════════════════════════
#
# 核心区别: LLM (GLM-4-Flash / DeepSeek-V3) 根据上一步实际返回值
#           决定下一步调哪个工具 —— 真正的 ReAct 行为
#
# 实验设计:
#   3 组对比: NG (无治理) vs SRL (令牌桶) vs PlanGate-Real (本文方案)
#   50 个 Agent, 分 5 波 × 10 并发
#   Backend: real_llm 模式, max-workers=10, queue-timeout=8
#   5 个真实工具: calculate, real_weather, real_web_search, text_format, deepseek_llm
#
# LLM 配置 (通过 .env):
#   AGENT_LLM_BASE — Agent 大脑 API endpoint
#   AGENT_LLM_KEY  — Agent 大脑 API key
#   AGENT_LLM_MODEL — Agent 大脑模型名
#   LLM_API_BASE/KEY/MODEL — 后端 deepseek_llm 工具
#
# 用法 (WSL2):
#   bash scripts/run_exp_real3.sh              # 默认 GLM-4-Flash
#   bash scripts/run_exp_real3.sh --deepseek   # 切换 DeepSeek-V3
# ═══════════════════════════════════════════════════════════════

set -e

# ── 解析参数 ──
USE_DEEPSEEK=false
for arg in "$@"; do
    case $arg in
        --deepseek) USE_DEEPSEEK=true ;;
    esac
done

# ── 基础参数 ──
BACKEND_PORT=8080
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
RESULT_DIR="results/exp_real3"
AGENTS=50
CONCURRENCY=10
MAX_STEPS=8
BUDGET=500
ARRIVAL_INTERVAL=0.5   # 每 0.5s 启动一个 Agent
MAX_WORKERS=10         # 后端并发瓶颈
QUEUE_TIMEOUT=8        # 队列超时

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── LLM 选择 ──
if [ "$USE_DEEPSEEK" = true ]; then
    LLM_LABEL="deepseek"
    echo "  ★ 使用 DeepSeek-V3 作为 Agent 大脑"
else
    LLM_LABEL="glm"
    echo "  ★ 使用 GLM-4-Flash 作为 Agent 大脑"
fi

RESULT_DIR="${RESULT_DIR}_${LLM_LABEL}"

# ── 网关配置 ──
declare -A GATEWAY_PORTS
GATEWAY_PORTS=(
    ["ng"]="9001"
    ["srl"]="9002"
    ["mcpdp-real"]="9005"
)

# ── 准备 ──
mkdir -p "$RESULT_DIR"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Exp-Real-3: 真·ReAct Agent 端到端验证实验"
echo "  时间戳:   $TIMESTAMP"
echo "  Agent数:  $AGENTS  并发: $CONCURRENCY  最大步数: $MAX_STEPS"
echo "  LLM:      $LLM_LABEL"
echo "  Backend:  max-workers=$MAX_WORKERS  queue-timeout=${QUEUE_TIMEOUT}s"
echo "  工具:     calculate, real_weather, real_web_search, text_format, deepseek_llm"
echo "═══════════════════════════════════════════════════════════"

# ── Step 1: 启动 Python MCP 后端 ──
echo ""
echo "[1/5] 启动 Python MCP 后端 (real_llm 模式)..."
if curl -s "${BACKEND_URL}" 2>/dev/null | grep -q "ok"; then
    echo "  后端已在运行，先关闭..."
    pkill -f "server.py.*--mode real_llm" 2>/dev/null || true
    sleep 2
fi

taskset -c 8-15 python3 mcp_server/server.py \
    --mode real_llm \
    --port $BACKEND_PORT \
    --max-workers $MAX_WORKERS \
    --queue-timeout $QUEUE_TIMEOUT \
    --congestion-factor 0.5 &
BACKEND_PID=$!
sleep 3

# 验证后端
if ! curl -s "${BACKEND_URL}" 2>/dev/null | grep -q "ok"; then
    echo "  ✗ 后端启动失败！"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi
echo "  ✓ 后端已启动 (PID=$BACKEND_PID, max-workers=$MAX_WORKERS)"

# ── Step 2: API 连通性测试 ──
echo ""
echo "[2/5] 测试工具连通性..."
CALC_TEST=$(curl -s -X POST "${BACKEND_URL}" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":"test","method":"tools/call","params":{"name":"calculate","arguments":{"operation":"add","a":1,"b":2}}}')
if echo "$CALC_TEST" | grep -q '"result"'; then
    echo "  ✓ calculate 工具正常"
else
    echo "  ✗ calculate 工具失败: $CALC_TEST"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi

# ── Step 3: 逐网关运行实验 ──
echo ""
echo "[3/5] 开始逐网关运行 (每组 $AGENTS agents)..."

for MODE in ng srl mcpdp-real; do
    PORT=${GATEWAY_PORTS[$MODE]}
    OUTPUT="${RESULT_DIR}/${MODE}_${TIMESTAMP}.csv"

    echo ""
    echo "  ══════════════════════════════════════"
    echo "  网关: $MODE (port=$PORT)"
    echo "  ══════════════════════════════════════"

    # 启动网关
    echo "  启动网关..."
    case $MODE in
        ng)
            taskset -c 4-7 ./gateway_linux --mode ng --port $PORT --backend $BACKEND_URL &
            ;;
        srl)
            taskset -c 4-7 ./gateway_linux --mode srl --port $PORT --backend $BACKEND_URL \
                --srl-qps 20 --srl-burst 40 --srl-max-conc $CONCURRENCY &
            ;;
        mcpdp-real)
            taskset -c 4-7 ./gateway_linux --mode mcpdp-real --port $PORT --backend $BACKEND_URL \
                --plangate-max-sessions $CONCURRENCY \
                --plangate-price-step 40 \
                --plangate-sunk-cost-alpha 0.5 \
                --real-ratelimit-max 200 \
                --real-latency-threshold 5000 &
            ;;
    esac
    GW_PID=$!
    sleep 2

    # 验证网关
    if ! curl -s "http://127.0.0.1:${PORT}" 2>/dev/null | head -c 1 | grep -q '{'; then
        echo "  ✗ 网关启动失败！跳过此组..."
        kill $GW_PID 2>/dev/null || true
        continue
    fi
    echo "  ✓ 网关已就绪"

    # 运行 Agent 客户端
    echo "  运行 $AGENTS 个 ReAct Agent (并发=$CONCURRENCY)..."
    taskset -c 0-3 python3 scripts/react_agent_client.py \
        --gateway "http://127.0.0.1:${PORT}" \
        --agents $AGENTS \
        --concurrency $CONCURRENCY \
        --max-steps $MAX_STEPS \
        --budget $BUDGET \
        --arrival-interval $ARRIVAL_INTERVAL \
        --gateway-mode $MODE \
        --output "$OUTPUT"

    # 停止网关
    echo "  停止网关 (PID=$GW_PID)..."
    kill $GW_PID 2>/dev/null || true
    wait $GW_PID 2>/dev/null || true
    sleep 3

    echo "  ✓ $MODE 完成"
done

# ── Step 4: 停止后端 ──
echo ""
echo "[4/5] 停止后端..."
kill $BACKEND_PID 2>/dev/null || true
wait $BACKEND_PID 2>/dev/null || true

# ── Step 5: 汇总 & 可视化 ──
echo ""
echo "[5/5] 生成汇总..."
SUMMARY="${RESULT_DIR}/ng_${TIMESTAMP}_summary.csv"
if [ -f "$SUMMARY" ]; then
    echo ""
    echo "  ── 三组对比汇总 ──"
    column -t -s, "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Exp-Real-3 完成！ ($LLM_LABEL)"
echo "  结果目录: $RESULT_DIR"
echo "  运行可视化: python3 scripts/plot_exp_real3.py --input $RESULT_DIR"
echo "═══════════════════════════════════════════════════════════"
