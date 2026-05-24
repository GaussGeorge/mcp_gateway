#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

cd "${REPO_DIR}"
mkdir -p results/log/cloudlab

PORT="$1"
BACKEND_URL="$2"
REDIS_ADDR="$3"
NODE_ID="$4"
SECRET="$5"
RECOVERY_STORE="${6:-inmemory}"
LOG_PATH="results/log/cloudlab/gateway_${NODE_ID}_${PORT}.log"

printf 'cloudlab-gateway node-id=%s port=%s backend=%s redis-addr=%s commitment-token-mode=optional plan-amendment-mode=recovery-only recovery-store=%s\n' \
  "${NODE_ID}" "${PORT}" "${BACKEND_URL}" "${REDIS_ADDR}" "${RECOVERY_STORE}" > "${LOG_PATH}"
nohup ./gateway_linux \
  --mode mcpdp \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --backend "${BACKEND_URL}" \
  --node-id "${NODE_ID}" \
  --plangate-state-store redis \
  --plangate-redis-addr "${REDIS_ADDR}" \
  --commitment-token-mode optional \
  --commitment-token-secret "${SECRET}" \
  --enable-recovery=true \
  --recovery-store "${RECOVERY_STORE}" \
  --plan-amendment-mode recovery-only \
  --plan-amendment-require-commitment=true \
  --plan-amendment-max-count 3 \
  --plan-amendment-max-budget-delta 0 \
  --plangate-max-sessions 300 \
  --plangate-price-step 40 \
  >> "${LOG_PATH}" 2>&1 &
echo $! > "results/log/cloudlab/gateway_${PORT}.pid"
