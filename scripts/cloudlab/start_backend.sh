#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

cd "${REPO_DIR}"
mkdir -p results/log/cloudlab

PORT="${1:-8080}"
MAX_WORKERS="${2:-16}"
LOG_PATH="results/log/cloudlab/backend_${PORT}.log"

printf 'cloudlab-backend port=%s max_workers=%s\n' "${PORT}" "${MAX_WORKERS}" > "${LOG_PATH}"
nohup .venv/bin/python mcp_server/server.py \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --max-workers "${MAX_WORKERS}" \
  --queue-timeout 1.0 \
  --congestion-factor 0.5 \
  >> "${LOG_PATH}" 2>&1 &
echo $! > "results/log/cloudlab/backend_${PORT}.pid"
