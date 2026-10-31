#!/usr/bin/env bash
# One-time (and repeatable) setup of the Oracle Cloud Always Free ARM VM on Oracle Linux 9, then the first
# rollout (AC-15). Run from the repository root as the login user (opc), with .env filled in:
#   bash deploy/vm/setup-ol9.sh [REF]
# Installs Docker CE and git, opens the api port in firewalld, keeps SELinux enforcing (no bind mounts: the code
# is baked into the image), starts the backbone, pulls the pinned Ollama models and rolls out REF (HEAD).
set -euo pipefail
cd "$(dirname "$0")/../.."
REF="${1:-HEAD}"

. /etc/os-release
if [[ "${ID}" != "ol" && "${ID}" != "rhel" && "${ID}" != "almalinux" && "${ID}" != "rocky" ]] \
   || [[ "${VERSION_ID%%.*}" != "9" ]]; then
  echo "This script is for Oracle Linux 9 (found ${PRETTY_NAME}); deploy/setup-vm.sh is the Ubuntu one." >&2
  exit 1
fi
[[ "$(uname -m)" == "aarch64" ]] || echo "warning: $(uname -m), not aarch64; the images are pinned for arm64" >&2

if ! command -v docker >/dev/null; then
  sudo dnf install -y dnf-plugins-core git
  sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
  sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker
id -nG "$USER" | grep -qw docker || sudo usermod -aG docker "$USER"   # takes effect at the next login

if systemctl is-active --quiet firewalld; then
  sudo firewall-cmd --quiet --permanent --add-port=8000/tcp
  sudo firewall-cmd --quiet --reload
fi
echo "SELinux: $(getenforce 2>/dev/null || echo unknown)"

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example: fill it in (DATABASE_URL = the Neon connection string), then rerun." >&2
  exit 1
fi
chmod 600 .env

DOCKER=(docker); docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
COMPOSE=("${DOCKER[@]}" compose -f deploy/compose.yaml -f deploy/vm/compose.vm.yaml --env-file .env)
APP_IMAGE=unset "${COMPOSE[@]}" up -d temporal qdrant jaeger openbao ollama

for model in $(python3 -c "import json; m = json.load(open('deploy/vm/models.json')); print(m['embedding']['model'], m['screening']['model'])"); do
  APP_IMAGE=unset "${COMPOSE[@]}" exec -T ollama ollama pull "$model"
done

python3 deploy/vm/release.py rollout "$REF"
