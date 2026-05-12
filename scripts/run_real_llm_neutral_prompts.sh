#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# run_real_llm_neutral_prompts.sh
# 真实 LLM 实验 (steady + bursty commercial API) — 中性 prompt 版本
#
# 包含:
#   B1. Steady commercial API  → results/real_llm_prompt_neutral/steady/
#   B2. Bursty real-LLM        → results/real_llm_prompt_neutral/bursty/
#
# 前置条件 (必须在 .env 中设置，或 export 到当前 Shell):
#   AGENT_LLM_KEY   — Agent 大脑 API Key
#   LLM_API_KEY     — 后端 deepseek_llm 工具 API Key (可与 AGENT_LLM_KEY 不同)
#
# 可选覆盖:
#   AGENT_LLM_BASE_URL — Agent 大脑 API endpoint (默认 GLM-4-Flash)
#   AGENT_LLM_MODEL    — Agent 大脑模型名
#
# 用法:
#   bash scripts/run_real_llm_neutral_prompts.sh             # 完整跑
#   bash scripts/run_real_llm_neutral_prompts.sh --dry-run   # 试运行
#   bash scripts/run_real_llm_neutral_prompts.sh --repeats 1  # 快速验证
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"

# ── 加载 .env（若存在）──
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# ── 检查必要环境变量 ──
if [ -z "${AGENT_LLM_KEY:-}" ] && [ -z "${LLM_API_KEY:-}" ]; then
    echo "ERROR: AGENT_LLM_KEY (或 LLM_API_KEY) 未设置。"
    echo "  请在 .env 中或通过 export 设置 API key。"
    echo "  示例: AGENT_LLM_KEY=your_key_here"
    exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "  Real LLM 实验 — 中性 Prompt 版本"
echo "  Agent LLM: ${AGENT_LLM_MODEL:-glm-4-flash}"
echo "  Endpoint:  ${AGENT_LLM_BASE_URL:-${AGENT_LLM_BASE:-[default GLM]}}"
echo "  结果目录: results/real_llm_prompt_neutral/{steady,bursty}/"
echo "═══════════════════════════════════════════════════════════"

# 解析参数: 区分 --dry-run / --repeats / B1/B2 选择
# --only-steady / --only-bursty 是本脚本自用参数，不转发给 Python
RUN_B1=true
RUN_B2=true
EXTRA_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --only-steady) RUN_B2=false ;;
        --only-bursty) RUN_B1=false ;;
        *) EXTRA_ARGS+=("$arg") ;;
    esac
done

# ── B1: Steady commercial API ──
if [ "$RUN_B1" = true ]; then
    echo ""
    echo "── B1: Steady commercial API ────────────────────────────"
    echo "  结果目录: results/exp_week5_C<CONCURRENCY>/"
    python3 scripts/run_real_llm_week5.py \
        "${EXTRA_ARGS[@]:-}" || {
        echo "  [WARN] Steady run exited with error code $?, continuing to B2..."
    }
fi

# ── B2: Bursty real-LLM ──
if [ "$RUN_B2" = true ]; then
    echo ""
    echo "── B2: Bursty real-LLM ──────────────────────────────────"
    echo "  结果目录: results/exp_bursty_C<CONCURRENCY>_B<BURST_SIZE>/"
    python3 scripts/run_real_llm_bursty.py \
        "${EXTRA_ARGS[@]:-}" || {
        echo "  [WARN] Bursty run exited with error code $?"
    }
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  完成。结果保存在 results/real_llm_prompt_neutral/"
echo "═══════════════════════════════════════════════════════════"
