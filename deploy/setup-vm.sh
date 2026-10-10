#!/usr/bin/env bash
# One-time setup of the Oracle Cloud Always Free ARM VM (Ubuntu 22.04 / 24.04), run from the repo root:
#   bash deploy/setup-vm.sh
# Installs Docker, then starts the backbone. Fill in .env (copied from .env.example) before the second run.
set -euo pipefail

if ! command -v docker >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y docker.io docker-compose-v2 git
  sudo usermod -aG docker "$USER"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill it in, then run this script again."
  exit 1
fi

sudo docker compose -f deploy/compose.yaml --env-file .env up -d
sudo docker compose -f deploy/compose.yaml --env-file .env ps
