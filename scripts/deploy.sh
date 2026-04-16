#!/bin/bash
# Deploy backend + frontend to homelab GPU VM
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

# Sync frontend files
rsync -avz --exclude='node_modules' --exclude='.next' \
    frontend/ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/frontend/"

# Build and restart all services
ssh "${REMOTE_USER}@${REMOTE_HOST}" "cd ${REMOTE_DIR}/backend && docker compose up -d --build"

echo ""
echo "Deployed successfully."
echo "  Backend:  http://${REMOTE_HOST}:8000/api/health"
echo "  Frontend: http://${REMOTE_HOST}:3100"
echo "  Docs:     http://${REMOTE_HOST}:8000/docs"
