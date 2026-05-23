#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
INSTALL_REDIS=0

if [[ "${1:-}" == "--install-redis" ]]; then
  INSTALL_REDIS=1
fi

cd "${REPO_DIR}"

sudo apt-get update
sudo apt-get install -y \
  git \
  curl \
  wget \
  build-essential \
  golang-go \
  python3 \
  python3-venv \
  python3-pip \
  redis-tools \
  psmisc

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p results/log/cloudlab

if [[ ${INSTALL_REDIS} -eq 1 ]]; then
  sudo apt-get install -y redis-server
  sudo sed -i 's/^bind .*/bind 0.0.0.0/' /etc/redis/redis.conf
  sudo sed -i 's/^protected-mode .*/protected-mode no/' /etc/redis/redis.conf
  sudo systemctl restart redis-server
  redis-cli -h 127.0.0.1 ping
fi
