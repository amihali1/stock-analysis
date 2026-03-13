#!/bin/bash
# Deploy backend to homelab GPU VM
# Usage: ./scripts/deploy.sh

set -euo pipefail

REMOTE_HOST="10.0.0.47"
REMOTE_USER="$(whoami)"
REMOTE_DIR="/opt/stock-analysis"

echo "Deploying to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}..."

# Sync backend files (include trained models for inference)
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='*.db' \
    --exclude='.pytest_cache' --exclude='notebooks/' \
    backend/ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/backend/"

# Build and restart
ssh "${REMOTE_USER}@${REMOTE_HOST}" "cd ${REMOTE_DIR}/backend && docker compose up -d --build"

echo "Deployed. Check: curl http://${REMOTE_HOST}:8000/api/health"
