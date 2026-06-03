#!/usr/bin/env bash
# setup_cloudlab_business_workflow_nodes.sh
# Idempotent dependency setup for CloudLab distributed business workflow smoke.
#
# Usage:
#   bash setup_cloudlab_business_workflow_nodes.sh
#
# Installs/checks: python3, python3-venv, pip, go, redis-server, sqlite3, curl, git
# Designed for Ubuntu 22.04 LTS 64-bit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== CloudLab Business Workflow Node Setup ==="
echo "Repo dir: $REPO_DIR"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Helper: check or install ──────────────────────────────────────────

check_cmd() {
    local cmd="$1"
    local pkg="${2:-$cmd}"
    if command -v "$cmd" &>/dev/null; then
        echo "  [OK] $cmd -> $(command -v "$cmd")"
        return 0
    else
        echo "  [MISS] $cmd"
        return 1
    fi
}

install_if_missing() {
    local cmd="$1"
    local pkg="${2:-$cmd}"
    if check_cmd "$cmd" "$pkg"; then
        return 0
    fi
    echo "  [INSTALL] $pkg"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "$pkg"
    echo "  [DONE] $pkg installed"
}

# ── OS detection ──────────────────────────────────────────────────────

if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "OS: $NAME $VERSION_ID"
fi

# ── Core dependencies ─────────────────────────────────────────────────

echo ""
echo "--- Core dependencies ---"

install_if_missing python3 python3
install_if_missing pip3 python3-pip
install_if_missing go golang-go
install_if_missing redis-server redis-server
install_if_missing sqlite3 sqlite3
install_if_missing curl curl
install_if_missing git git

# ── Python venv (ensure module is available) ──────────────────────────

echo ""
echo "--- Python venv ---"
if python3 -m venv --help &>/dev/null; then
    echo "  [OK] python3-venv available"
else
    echo "  [INSTALL] python3-venv"
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-venv
    echo "  [DONE] python3-venv installed"
fi

# ── Version report ────────────────────────────────────────────────────

echo ""
echo "--- Version report ---"
echo "python3: $(python3 --version 2>&1)"
echo "pip3:    $(pip3 --version 2>&1)"
echo "go:      $(go version 2>&1)"
echo "redis:   $(redis-server --version 2>&1 || echo 'redis-server not on PATH; check with dpkg')"
echo "sqlite3: $(sqlite3 --version 2>&1)"
echo "curl:    $(curl --version 2>&1 | head -1)"
echo "git:     $(git version 2>&1)"

# ── Python packages ───────────────────────────────────────────────────

echo ""
echo "--- Python packages ---"
pip3 install --quiet aiohttp 2>&1 | tail -1 || echo "  aiohttp install attempted"

# ── Go build check ────────────────────────────────────────────────────

echo ""
echo "--- Go gateway build check ---"
cd "$REPO_DIR"
if go build -o /dev/null ./cmd/gateway 2>&1; then
    echo "  [OK] go build ./cmd/gateway succeeds"
else
    echo "  [WARN] go build failed; check Go module setup"
fi

# ── Redis check (if redis-server is on this node) ─────────────────────

echo ""
echo "--- Redis check ---"
if command -v redis-server &>/dev/null; then
    if pgrep -u "$(whoami)" redis-server &>/dev/null; then
        echo "  [OK] redis-server is running"
    else
        echo "  [INFO] redis-server not running; will be started by experiment runner"
    fi
else
    echo "  [SKIP] redis-server not on this node"
fi

# ── Done ──────────────────────────────────────────────────────────────

echo ""
echo "=== Setup complete ==="
echo "Repo: $REPO_DIR"
echo "All dependencies checked. Ready for experiment."
