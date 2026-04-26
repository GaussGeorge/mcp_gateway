#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Exp-Real-3 一体化运行脚本 (WSL2 兼容)
# 支持多 LLM 提供者 (GLM / DeepSeek) + wo-SessionCap 消融
# 后端 + 网关 + Agent 客户端全部在同一进程树中运行
# 所有参数统一在 scripts/exp_config.sh 中配置
#
# 用法:
#   bash scripts/run_exp_real3_all.sh                     # GLM, 3 网关
#   bash scripts/run_exp_real3_all.sh --deepseek          # DeepSeek, 3 网关
#   bash scripts/run_exp_real3_all.sh --all-llm           # GLM + DeepSeek 依次跑
#   bash scripts/run_exp_real3_all.sh --wo-sessioncap     # 额外跑 wo-SessionCap
#   bash scripts/run_exp_real3_all.sh --all-llm --wo-sessioncap  # 完整对比
#   bash scripts/run_exp_real3_all.sh --smoke             # 快速测试 (5 agents)
# ═══════════════════════════════════════════════════════════════
set -e

cd /mnt/d/mcp-governance-main

# ── 加载配置 ──
CONFIG_FILE="scripts/exp_config.sh"
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
    echo "[Config] 已加载 $CONFIG_FILE"
else
    echo "[Config] ⚠ 未找到 $CONFIG_FILE，使用默认值"
    AGENTS=50; CONCURRENCY=10; MAX_STEPS=8; BUDGET=500; ARRIVAL_INTERVAL=0.5
    MAX_WORKERS=10; QUEUE_TIMEOUT=8; CONGESTION_FACTOR=0.5; BACKEND_PORT=8080
    SRL_QPS=20; SRL_BURST=40; SRL_MAX_CONC=10
    PLANGATE_MAX_SESSIONS=10; PLANGATE_PRICE_STEP=40; PLANGATE_SUNK_COST_ALPHA=0.5
    REAL_RATELIMIT_MAX=200; REAL_LATENCY_THRESHOLD=5000
    NG_PORT=9001; SRL_PORT=9002; PLANGATE_PORT=9005
    PLANGATE_NOSESSCAP_PORT=9006
    DEEPSEEK_API_BASE="https://api.deepseek.com/v1"
    DEEPSEEK_API_KEY=""; DEEPSEEK_MODEL="deepseek-chat"
    DEEPSEEK_RATELIMIT_MAX=60
fi

BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── 解析参数 ──
PROVIDERS="glm"
RUN_NOSESSCAP=false
REPEATS=1
CONC_SWEEP=""
for arg in "$@"; do
    case $arg in
        --deepseek)      PROVIDERS="deepseek" ;;
        --all-llm)       PROVIDERS="glm deepseek" ;;
        --wo-sessioncap) RUN_NOSESSCAP=true ;;
        --smoke)         AGENTS=5; CONCURRENCY=3; ARRIVAL_INTERVAL=0.2 ;;
        --repeats=*)     REPEATS="${arg#*=}" ;;
        --conc-sweep=*)  CONC_SWEEP="${arg#*=}" ;;
    esac
done

LOG_DIR="results/log/real_llm"
mkdir -p "$LOG_DIR"

# ── 清理旧进程 ──
pkill -f "server.py.*--mode real_llm" 2>/dev/null || true
pkill -f gateway_linux 2>/dev/null || true
sleep 1

# ── 启动后端 (所有 provider 共享) ──
echo ""
echo "[1/4] 启动后端..."
python3 mcp_server/server.py \
    --mode real_llm \
    --port $BACKEND_PORT \
    --max-workers $MAX_WORKERS \
    --queue-timeout $QUEUE_TIMEOUT \
    --congestion-factor $CONGESTION_FACTOR \
    --tool-delay-lightweight ${TOOL_DELAY_LIGHTWEIGHT:-0,0} \
    --tool-delay-medium ${TOOL_DELAY_MEDIUM:-0,0} \
    --tool-delay-heavyweight ${TOOL_DELAY_HEAVYWEIGHT:-0,0} > "${LOG_DIR}/_backend.log" 2>&1 &
BACKEND_PID=$!
sleep 4

if ! curl -s "${BACKEND_URL}" | grep -q "ok"; then
    echo "  ✗ 后端启动失败！"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi
echo "  ✓ 后端就绪 (PID=$BACKEND_PID)"

# ── 工具连通性测试 ──
echo ""
echo "[2/4] 测试 API 连通性..."
CALC_TEST=$(curl -s -X POST "${BACKEND_URL}" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":"t1","method":"tools/call","params":{"name":"calculate","arguments":{"operation":"add","a":1,"b":2}}}')
if echo "$CALC_TEST" | grep -q '"content"'; then
    echo "  ✓ calculate OK"
else
    echo "  ✗ calculate FAILED: $CALC_TEST"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi

# ── 清理函数 ──
cleanup() {
    echo ""
    echo "[Clean] 停止所有进程..."
    kill $BACKEND_PID 2>/dev/null || true
    pkill -f gateway_linux 2>/dev/null || true
    wait 2>/dev/null || true
    echo "[Clean] 完成"
}
trap cleanup EXIT

# ══════════════════════════════════════════════════════════════
# 主循环: 逐 LLM 提供者 × 逐网关
# ══════════════════════════════════════════════════════════════
for PROVIDER in $PROVIDERS; do
    # ── 设置 LLM 环境变量 ──
    case $PROVIDER in
        glm)
            export AGENT_LLM_BASE="https://open.bigmodel.cn/api/paas/v4"
            export AGENT_LLM_KEY="${GLM_API_KEY:-a22713062fa041e5a04b35b47ecbd7f9.yYrIROfseXak4pZA}"
            export AGENT_LLM_MODEL="glm-4-flash"
            CUR_RATELIMIT_MAX=$REAL_RATELIMIT_MAX
            # 自适应治理: 高 RPM → 放宽准入, 维持高并发
            CUR_MAX_SESSIONS=25
            CUR_SESSION_CAP_WAIT=30
            CUR_CONCURRENCY=$CONCURRENCY   # GLM 200 RPM → 保持默认并发
            ;;
        deepseek)
            export AGENT_LLM_BASE="$DEEPSEEK_API_BASE"
            export AGENT_LLM_KEY="$DEEPSEEK_API_KEY"
            export AGENT_LLM_MODEL="$DEEPSEEK_MODEL"
            CUR_RATELIMIT_MAX=${DEEPSEEK_RATELIMIT_MAX:-60}
            # 自适应治理: 低 RPM → 收紧准入 + 降低并发
            CUR_MAX_SESSIONS=10
            CUR_SESSION_CAP_WAIT=15
            # DeepSeek 60 RPM → 按容量比例调整并发
            CUR_CONCURRENCY=$(( CONCURRENCY * 60 / 200 ))
            [ "$CUR_CONCURRENCY" -lt 2 ] && CUR_CONCURRENCY=2
            [ "$CUR_CONCURRENCY" -gt "$CONCURRENCY" ] && CUR_CONCURRENCY=$CONCURRENCY
            ;;
    esac

    RESULT_DIR="results/exp_real3_${PROVIDER}"
    mkdir -p "$RESULT_DIR"

    # ── 网关列表 ──
    MODES="ng srl mcpdp-real"
    if [ "$RUN_NOSESSCAP" = true ]; then
        MODES="$MODES mcpdp-real-no-sessioncap"
    fi

    MODE_COUNT=$(echo $MODES | wc -w)
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  Exp-Real-3: 真·ReAct Agent [$PROVIDER]"
    echo "  Agents=$AGENTS  Conc=$CUR_CONCURRENCY  MaxSteps=$MAX_STEPS"
    echo "  网关: $MODES"
    echo "  重复: $REPEATS 次"
    echo "  RateLimitMax=$CUR_RATELIMIT_MAX  SessionCap=$CUR_MAX_SESSIONS  Wait=${CUR_SESSION_CAP_WAIT}s"
    echo "═══════════════════════════════════════════════════════════"

    echo ""
    echo "[3/4] 开始实验 (${MODE_COUNT}组 × $AGENTS agents × $REPEATS 次)..."

    for RUN_IDX in $(seq 1 $REPEATS); do
    echo ""
    echo "  ── 第 $RUN_IDX / $REPEATS 次重复 ──"

    for MODE in $MODES; do
        case $MODE in
            ng)                        PORT=$NG_PORT ;;
            srl)                       PORT=$SRL_PORT ;;
            mcpdp-real)                PORT=$PLANGATE_PORT ;;
            mcpdp-real-no-sessioncap)  PORT=${PLANGATE_NOSESSCAP_PORT:-9006} ;;
        esac
        RUN_TS=$(date +%Y%m%d_%H%M%S)
        OUTPUT="${RESULT_DIR}/${MODE}_run${RUN_IDX}_${RUN_TS}.csv"

        echo ""
        echo "  ══════════════════════════════════════"
        echo "  网关: $MODE (port=$PORT) [LLM: $PROVIDER]"
        echo "  ══════════════════════════════════════"

        # 启动网关
        case $MODE in
            ng)
                ./gateway_linux --mode ng --port $PORT --backend $BACKEND_URL > "${LOG_DIR}/_gateway_${MODE}_${PORT}.log" 2>&1 &
                ;;
            srl)
                ./gateway_linux --mode srl --port $PORT --backend $BACKEND_URL \
                    --srl-qps $SRL_QPS --srl-burst $SRL_BURST --srl-max-conc $SRL_MAX_CONC > "${LOG_DIR}/_gateway_${MODE}_${PORT}.log" 2>&1 &
                ;;
            mcpdp-real)
                ./gateway_linux --mode mcpdp-real --port $PORT --backend $BACKEND_URL \
                    --plangate-max-sessions $CUR_MAX_SESSIONS \
                    --plangate-price-step $PLANGATE_PRICE_STEP \
                    --plangate-sunk-cost-alpha $PLANGATE_SUNK_COST_ALPHA \
                    --plangate-session-cap-wait $CUR_SESSION_CAP_WAIT \
                    --real-ratelimit-max $CUR_RATELIMIT_MAX \
                    --real-latency-threshold $REAL_LATENCY_THRESHOLD > "${LOG_DIR}/_gateway_${MODE}_${PORT}.log" 2>&1 &
                ;;
            mcpdp-real-no-sessioncap)
                ./gateway_linux --mode mcpdp-real-no-sessioncap --port $PORT --backend $BACKEND_URL \
                    --plangate-price-step $PLANGATE_PRICE_STEP \
                    --plangate-sunk-cost-alpha $PLANGATE_SUNK_COST_ALPHA \
                    --real-ratelimit-max $CUR_RATELIMIT_MAX \
                    --real-latency-threshold $REAL_LATENCY_THRESHOLD > "${LOG_DIR}/_gateway_${MODE}_${PORT}.log" 2>&1 &
                ;;
        esac
        GW_PID=$!
        sleep 2

        # 验证网关
        GW_CHECK=$(curl -s "http://127.0.0.1:${PORT}" 2>/dev/null | head -c 10)
        if [ -z "$GW_CHECK" ]; then
            echo "  ✗ 网关启动失败，跳过..."
            kill $GW_PID 2>/dev/null || true
            continue
        fi
        echo "  ✓ 网关就绪 (PID=$GW_PID)"

        # 运行 Agent
        echo "  运行 $AGENTS 个 ReAct Agent..."
        python3 scripts/react_agent_client.py \
            --gateway "http://127.0.0.1:${PORT}" \
            --agents $AGENTS \
            --concurrency $CUR_CONCURRENCY \
            --max-steps $MAX_STEPS \
            --budget $BUDGET \
            --arrival-interval $ARRIVAL_INTERVAL \
            --gateway-mode $MODE \
            --output "$OUTPUT" || echo "  ⚠ Agent 运行出错"

        # 停止网关
        kill $GW_PID 2>/dev/null || true
        wait $GW_PID 2>/dev/null || true
        sleep 2
        echo "  ✓ $MODE 完成"
    done
    done  # end REPEATS loop

    # ── 聚合汇总 ──
    echo ""
    echo "[4/4] 聚合 $PROVIDER 汇总..."
    UNIFIED_SUMMARY="${RESULT_DIR}/summary_all.csv"
    FIRST=true
    for f in ${RESULT_DIR}/*_summary.csv; do
        if [ ! -f "$f" ]; then continue; fi
        if [ "$f" = "$UNIFIED_SUMMARY" ]; then continue; fi
        if [ "$FIRST" = true ]; then
            cat "$f" > "$UNIFIED_SUMMARY"
            FIRST=false
        else
            tail -n +2 "$f" >> "$UNIFIED_SUMMARY"
        fi
    done

    if [ -f "$UNIFIED_SUMMARY" ]; then
        echo ""
        echo "  ── $PROVIDER 对比汇总 ──"
        column -t -s, "$UNIFIED_SUMMARY" 2>/dev/null || cat "$UNIFIED_SUMMARY"
    fi
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  所有 LLM 提供者实验完成!"
echo "  结果目录: results/exp_real3_*"
echo "  可视化:   python3 scripts/plot_exp_real3.py --input results/exp_real3_glm"
echo "═══════════════════════════════════════════════════════════"

# ══════════════════════════════════════════════════════════════
# 并发度敏感性扫描 (W2: 回应审稿人并发调优公平性质疑)
# 用法: bash scripts/run_exp_real3_all.sh --deepseek --conc-sweep=2,3,4,5
# ══════════════════════════════════════════════════════════════
if [ -n "$CONC_SWEEP" ]; then
    IFS=',' read -ra CONC_VALUES <<< "$CONC_SWEEP"
    PROVIDER="deepseek"
    export AGENT_LLM_BASE="$DEEPSEEK_API_BASE"
    export AGENT_LLM_KEY="$DEEPSEEK_API_KEY"
    export AGENT_LLM_MODEL="$DEEPSEEK_MODEL"

    SWEEP_DIR="results/exp_conc_sweep_deepseek"
    mkdir -p "$SWEEP_DIR"

    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  并发度敏感性分析 (DeepSeek-V3)"
    echo "  扫描并发: ${CONC_VALUES[*]}"
    echo "═══════════════════════════════════════════════════════════"

    for CUR_C in "${CONC_VALUES[@]}"; do
        for MODE in ng srl mcpdp-real mcpdp-real-no-sessioncap; do
            case $MODE in
                ng)                        PORT=$NG_PORT ;;
                srl)                       PORT=$SRL_PORT ;;
                mcpdp-real)                PORT=$PLANGATE_PORT ;;
                mcpdp-real-no-sessioncap)  PORT=${PLANGATE_NOSESSCAP_PORT:-9006} ;;
            esac

            SWEEP_TS=$(date +%Y%m%d_%H%M%S)
            OUTPUT="${SWEEP_DIR}/${MODE}_c${CUR_C}_${SWEEP_TS}.csv"

            echo ""
            echo "  ── $MODE @ C=$CUR_C ──"

            # 启动网关
            case $MODE in
                ng)
                    ./gateway_linux --mode ng --port $PORT --backend $BACKEND_URL > "${LOG_DIR}/_gateway_sweep_${MODE}_${PORT}.log" 2>&1 &
                    ;;
                srl)
                    ./gateway_linux --mode srl --port $PORT --backend $BACKEND_URL \
                        --srl-qps $SRL_QPS --srl-burst $SRL_BURST --srl-max-conc $SRL_MAX_CONC > "${LOG_DIR}/_gateway_sweep_${MODE}_${PORT}.log" 2>&1 &
                    ;;
                mcpdp-real)
                    ./gateway_linux --mode mcpdp-real --port $PORT --backend $BACKEND_URL \
                        --plangate-max-sessions $CUR_MAX_SESSIONS \
                        --plangate-price-step $PLANGATE_PRICE_STEP \
                        --plangate-sunk-cost-alpha $PLANGATE_SUNK_COST_ALPHA \
                        --plangate-session-cap-wait ${CUR_SESSION_CAP_WAIT:-15} \
                        --real-ratelimit-max ${DEEPSEEK_RATELIMIT_MAX:-60} \
                        --real-latency-threshold $REAL_LATENCY_THRESHOLD > "${LOG_DIR}/_gateway_sweep_${MODE}_${PORT}.log" 2>&1 &
                    ;;
                mcpdp-real-no-sessioncap)
                    ./gateway_linux --mode mcpdp-real-no-sessioncap --port $PORT --backend $BACKEND_URL \
                        --plangate-price-step $PLANGATE_PRICE_STEP \
                        --plangate-sunk-cost-alpha $PLANGATE_SUNK_COST_ALPHA \
                        --real-ratelimit-max ${DEEPSEEK_RATELIMIT_MAX:-60} \
                        --real-latency-threshold $REAL_LATENCY_THRESHOLD > "${LOG_DIR}/_gateway_sweep_${MODE}_${PORT}.log" 2>&1 &
                    ;;
            esac
            GW_PID=$!
            sleep 2

            python3 scripts/react_agent_client.py \
                --gateway "http://127.0.0.1:${PORT}" \
                --agents $AGENTS \
                --concurrency $CUR_C \
                --max-steps $MAX_STEPS \
                --budget $BUDGET \
                --arrival-interval $ARRIVAL_INTERVAL \
                --gateway-mode "${MODE}_c${CUR_C}" \
                --output "$OUTPUT" || echo "  ⚠ 出错"

            kill $GW_PID 2>/dev/null || true
            wait $GW_PID 2>/dev/null || true
            sleep 2
        done
    done

    echo ""
    echo "  并发度敏感性分析完成: $SWEEP_DIR"
fi
