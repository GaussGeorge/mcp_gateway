#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 批量参数组实验 — 自动运行 3 组参数，结果分开保存
# ═══════════════════════════════════════════════════════════════
set -e
cd /mnt/d/mcp-governance-main

CONFIG="scripts/exp_config.sh"

# ── 备份原配置 ──
cp "$CONFIG" "${CONFIG}.bak"

restore_config() {
    if [ -f "${CONFIG}.bak" ]; then
        mv "${CONFIG}.bak" "$CONFIG"
    fi
}
trap restore_config EXIT

run_one() {
    local LABEL=$1
    local CONFIG_CONTENT=$2
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║  参数组: $LABEL"
    echo "╚═══════════════════════════════════════════════════════════╝"

    # 写入配置文件
    cat > "$CONFIG" <<EOF
#!/bin/bash
# 参数组: $LABEL (自动生成)
$CONFIG_CONTENT
EOF

    # 运行实验
    bash scripts/run_exp_real3_all.sh

    # 将默认输出目录改名为带标签的目录
    RESULT_DIR="results/exp_real3_glm_${LABEL}"
    if [ -d "results/exp_real3_glm" ]; then
        rm -rf "$RESULT_DIR"
        mv "results/exp_real3_glm" "$RESULT_DIR"
        echo "  → 结果保存在: $RESULT_DIR"
    fi

    sleep 5
}

# ════════════════════════════════════════════════
# 参数组 1: 高压 (后端容量小, 并发高)
#   设计意图: max-workers=5 让后端很容易饱和,
#   concurrency=15 + interval=0.3 制造持续高并发,
#   SRL 限流偏紧, PlanGate 适度放行
# ════════════════════════════════════════════════
SET1='AGENTS=50
CONCURRENCY=15
MAX_STEPS=8
BUDGET=500
ARRIVAL_INTERVAL=0.3
MAX_WORKERS=5
QUEUE_TIMEOUT=4
CONGESTION_FACTOR=0.5
BACKEND_PORT=8080
SRL_QPS=12
SRL_BURST=25
SRL_MAX_CONC=8
PLANGATE_MAX_SESSIONS=12
PLANGATE_PRICE_STEP=40
PLANGATE_SUNK_COST_ALPHA=0.5
REAL_RATELIMIT_MAX=200
REAL_LATENCY_THRESHOLD=5000
NG_PORT=9001
SRL_PORT=9002
PLANGATE_PORT=9005'

run_one "set1_high" "$SET1"

# ════════════════════════════════════════════════
# 参数组 2: 极端压力 (后端极小, 并发极高)
#   设计意图: max-workers=3 后端几乎立刻满载,
#   queue-timeout=3 快速超时, concurrency=20 极端并发,
#   预期 NG 大量超时, SRL 部分存活, PlanGate 精准筛选
# ════════════════════════════════════════════════
SET2='AGENTS=50
CONCURRENCY=20
MAX_STEPS=8
BUDGET=500
ARRIVAL_INTERVAL=0.2
MAX_WORKERS=3
QUEUE_TIMEOUT=3
CONGESTION_FACTOR=0.5
BACKEND_PORT=8080
SRL_QPS=8
SRL_BURST=15
SRL_MAX_CONC=5
PLANGATE_MAX_SESSIONS=8
PLANGATE_PRICE_STEP=40
PLANGATE_SUNK_COST_ALPHA=0.5
REAL_RATELIMIT_MAX=200
REAL_LATENCY_THRESHOLD=5000
NG_PORT=9001
SRL_PORT=9002
PLANGATE_PORT=9005'

run_one "set2_extreme" "$SET2"

# ════════════════════════════════════════════════
# 参数组 3: 中等压力 (后端中等, 并发适中)
#   设计意图: max-workers=6 适度瓶颈,
#   concurrency=12 略超后端容量, interval=0.4 平缓到达,
#   SRL/PlanGate 参数宽松, 预期三者都有分化但不极端
# ════════════════════════════════════════════════
SET3='AGENTS=50
CONCURRENCY=12
MAX_STEPS=8
BUDGET=500
ARRIVAL_INTERVAL=0.4
MAX_WORKERS=6
QUEUE_TIMEOUT=5
CONGESTION_FACTOR=0.5
BACKEND_PORT=8080
SRL_QPS=15
SRL_BURST=30
SRL_MAX_CONC=10
PLANGATE_MAX_SESSIONS=15
PLANGATE_PRICE_STEP=40
PLANGATE_SUNK_COST_ALPHA=0.5
REAL_RATELIMIT_MAX=200
REAL_LATENCY_THRESHOLD=5000
NG_PORT=9001
SRL_PORT=9002
PLANGATE_PORT=9005'

run_one "set3_moderate" "$SET3"

# ── 恢复原配置 ──
mv "${CONFIG}.bak" "$CONFIG"
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  全部 3 组实验完成！"
echo "  set1_high:     results/exp_real3_glm_set1_high/"
echo "  set2_extreme:  results/exp_real3_glm_set2_extreme/"
echo "  set3_moderate: results/exp_real3_glm_set3_moderate/"
echo "═══════════════════════════════════════════════════════════"
