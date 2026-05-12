#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# run_selfhosted_vllm_neutral_prompts.sh
# Self-Hosted vLLM 实验 (B3) — 中性 Prompt 版本
#
# 前置条件:
#   vLLM 已通过以下命令启动:
#     python -m vllm.entrypoints.openai.api_server \
#         --model /mnt/d/model_path/qwen3.5-4b \
#         --served-model-name qwen \
#         --trust-remote-code --host 127.0.0.1 --port 9999 \
#         --max-model-len 4096 --gpu-memory-utilization 0.8 \
#         --enforce-eager --max-num-seqs 8 \
#         --enable-auto-tool-choice --tool-call-parser hermes
#
#   Agent 大脑 API Key (商业 API；vLLM 本机无需 key):
#     需要在 .env 中设置 AGENT_LLM_KEY 或通过 export 设置
#
# 可配置环境变量 (有默认值，无需改动即可运行):
#   AGENT_LLM_BASE_URL — vLLM endpoint (默认 http://127.0.0.1:9999/v1)
#   AGENT_LLM_MODEL    — 模型名 (默认 qwen)
#   AGENT_LLM_KEY      — API key (默认 EMPTY，vLLM 不验证)
#
#   若同时使用商业 API 作为 agent 大脑，则:
#   AGENT_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
#   AGENT_LLM_MODEL=glm-4-flash
#   AGENT_LLM_KEY=<your_glm_key>
#
# 用法:
#   bash scripts/run_selfhosted_vllm_neutral_prompts.sh
#   bash scripts/run_selfhosted_vllm_neutral_prompts.sh --dry-run
#   bash scripts/run_selfhosted_vllm_neutral_prompts.sh --repeats 1
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

# ── Self-hosted vLLM 默认配置 ──
export AGENT_LLM_BASE_URL="${AGENT_LLM_BASE_URL:-http://127.0.0.1:9999/v1}"
export AGENT_LLM_MODEL="${AGENT_LLM_MODEL:-qwen}"
export AGENT_LLM_KEY="${AGENT_LLM_KEY:-EMPTY}"

echo "═══════════════════════════════════════════════════════════"
echo "  Self-Hosted vLLM 实验 — 中性 Prompt 版本 (B3)"
echo "  vLLM endpoint: $AGENT_LLM_BASE_URL"
echo "  模型:          $AGENT_LLM_MODEL"
echo "  结果目录: results/real_llm_prompt_neutral/selfhosted_vllm/"
echo "═══════════════════════════════════════════════════════════"

# ── 检查 vLLM 服务是否可达 ──
VLLM_HEALTH="${AGENT_LLM_BASE_URL%/v1}/health"
echo "  检查 vLLM 服务: $VLLM_HEALTH"
if ! curl -sf --max-time 5 "$VLLM_HEALTH" > /dev/null 2>&1; then
    # /health 可能不存在，尝试 /v1/models
    if ! curl -sf --max-time 5 "${AGENT_LLM_BASE_URL}/models" > /dev/null 2>&1; then
        echo "ERROR: vLLM 服务不可达 ($AGENT_LLM_BASE_URL)"
        echo "  请先启动 vLLM:"
        echo "    python -m vllm.entrypoints.openai.api_server \\"
        echo "        --model /mnt/d/model_path/qwen3.5-4b \\"
        echo "        --served-model-name qwen --trust-remote-code \\"
        echo "        --host 127.0.0.1 --port 9999 --max-model-len 4096 \\"
        echo "        --gpu-memory-utilization 0.8 --enforce-eager \\"
        echo "        --max-num-seqs 8 --enable-auto-tool-choice \\"
        echo "        --tool-call-parser hermes"
        exit 1
    fi
fi
echo "  vLLM 服务正常"

# ── 输出目录 ──
OUTDIR="$ROOT_DIR/results/real_llm_prompt_neutral/selfhosted_vllm"
mkdir -p "$OUTDIR"

# ── 运行实验 ──
exec python3 scripts/run_selfhosted_vllm.py \
    "$@"
