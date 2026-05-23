#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

cd "${REPO_DIR}"
mkdir -p .gocache
export GOCACHE="${REPO_DIR}/.gocache"

go version
python3 --version
go test ./plangate/... -run "Commitment|Amendment|Recovery|PlanAndSolve" -count=1
go build -o gateway_linux ./cmd/gateway
