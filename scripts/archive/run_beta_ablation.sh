#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# run_beta_ablation.sh — Beta 消融实验一键入口
#
# 实验场景: mock pure ReAct, beta ∈ {0, 0.5, 1, 2, 3}
# 结果输出: results/beta_ablation/  plots/beta_ablation/
#           tables/beta_ablation_table.tex
#
# 用法:
#   bash scripts/run_beta_ablation.sh              # 完整跑 (5 repeats)
#   bash scripts/run_beta_ablation.sh --dry-run    # 试运行
#   bash scripts/run_beta_ablation.sh --repeats 2  # 快速验证
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"

echo "═══════════════════════════════════════════════════════════"
echo "  Beta Ablation: ReAct Continuation Pricing"
echo "  beta ∈ {0, 0.5, 1, 2, 3}  |  pure ReAct  |  alpha=0.5"
echo "  结果目录: results/beta_ablation/"
echo "═══════════════════════════════════════════════════════════"

# ── 编译网关（如不存在）──
GW_BIN="$ROOT_DIR/gateway_linux"
if [ ! -f "$GW_BIN" ]; then
    echo "  [Build] go build -o gateway_linux ./cmd/gateway"
    go build -o "$GW_BIN" ./cmd/gateway
    echo "  [Build] 完成"
fi

# ── 运行实验 ──
exec python3 scripts/run_beta_ablation.py "$@"
