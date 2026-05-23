#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
LOG_DIR="${REPO_DIR}/results/log/cloudlab"
REDIS_ADDR="${1:-}"

cd "${REPO_DIR}"

if [[ -d "${LOG_DIR}" ]]; then
  shopt -s nullglob
  for pid_file in "${LOG_DIR}"/*.pid; do
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      sleep 1
      kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${pid_file}"
  done
  shopt -u nullglob
  rm -f "${LOG_DIR}"/*.log
fi

pkill -f "gateway_linux" 2>/dev/null || true
pkill -f "mcp_server/server.py" 2>/dev/null || true

if [[ -n "${REDIS_ADDR}" ]]; then
  REDIS_HOST="${REDIS_ADDR%:*}"
  REDIS_PORT="${REDIS_ADDR##*:}"
  if redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" ping >/dev/null 2>&1; then
    redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" --scan --pattern 'pg:*' | \
      while IFS= read -r key; do
        if [[ -n "${key}" ]]; then
          redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT}" del "${key}" >/dev/null
        fi
      done
  fi
fi
