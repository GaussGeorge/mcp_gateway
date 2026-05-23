#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
INSTALL_REDIS=0
GO_VERSION="1.23.12"
GO_TARBALL="go${GO_VERSION}.linux-amd64.tar.gz"
GO_URL="https://go.dev/dl/${GO_TARBALL}"

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
  python3 \
  python3-venv \
  python3-pip \
  redis-tools \
  psmisc

install_go=1
if command -v go >/dev/null 2>&1; then
  current_go="$(go version | awk '{print $3}' | sed 's/^go//')"
  if [[ -n "${current_go}" ]] && dpkg --compare-versions "${current_go}" ge "1.23"; then
    install_go=0
  fi
fi

if [[ ${install_go} -eq 1 ]]; then
  tmp_tarball="/tmp/${GO_TARBALL}"
  wget -q -O "${tmp_tarball}" "${GO_URL}"
  sudo rm -rf /usr/local/go
  sudo tar -C /usr/local -xzf "${tmp_tarball}"
fi

export PATH="/usr/local/go/bin:${PATH}"
if ! grep -q '/usr/local/go/bin' "${HOME}/.profile" 2>/dev/null; then
  printf '\nexport PATH=/usr/local/go/bin:$PATH\n' >> "${HOME}/.profile"
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install aiohttp numpy

go version

mkdir -p results/log/cloudlab

if [[ ${INSTALL_REDIS} -eq 1 ]]; then
  sudo apt-get install -y redis-server
  sudo sed -i 's/^bind .*/bind 0.0.0.0/' /etc/redis/redis.conf
  sudo sed -i 's/^protected-mode .*/protected-mode no/' /etc/redis/redis.conf
  sudo systemctl restart redis-server
  redis-cli -h 127.0.0.1 ping
fi
