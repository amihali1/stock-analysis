#!/bin/bash
# Deploy backend + frontend to homelab GPU VM.
#
# Usage: ./scripts/deploy.sh
#
# Override host/user with env if needed:
#   REMOTE_USER=alice REMOTE_HOST=10.0.0.99 ./scripts/deploy.sh

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-10.0.0.47}"
REMOTE_USER="${REMOTE_USER:-proxmox}"
REMOTE_DIR="${REMOTE_DIR:-/opt/stock-analysis}"

SSH_TARGET="${REMOTE_USER}@${REMOTE_HOST}"

echo "Deploying to ${SSH_TARGET}:${REMOTE_DIR}..."

# Sync backend + frontend. rsync is preferred but isn't available on stock
# Git-for-Windows (where most deploys originate), so fall back to a tar
# stream over ssh. The tar fallback is additive (won't delete remote files
# that were removed locally) — if you need pruning, run `git status` clean
# and reach for rsync explicitly.
sync_tree() {
    local src="$1"
    local dest="$2"
    shift 2
    if command -v rsync >/dev/null 2>&1; then
        rsync -avz "$@" "$src/" "${SSH_TARGET}:${dest}/"
    else
        echo "  (rsync not available — using tar fallback for $src)"
        local exclude_args=()
        for arg in "$@"; do
            case "$arg" in
                --exclude=*) exclude_args+=("$arg") ;;
            esac
        done
        ( cd "$src" && tar "${exclude_args[@]}" -czf - . ) \
            | ssh "${SSH_TARGET}" "mkdir -p ${dest} && cd ${dest} && tar -xzf -"
    fi
}

sync_tree backend "${REMOTE_DIR}/backend" \
    --exclude=.venv --exclude=__pycache__ --exclude='*.db' \
    --exclude=.pytest_cache --exclude=notebooks

sync_tree frontend "${REMOTE_DIR}/frontend" \
    --exclude=node_modules --exclude=.next

# Build and restart all services.
ssh "${SSH_TARGET}" "cd ${REMOTE_DIR}/backend && docker compose up -d --build"

# Run alembic migrations. Skipping this drops new columns/widenings on the
# floor — portfolio_sync crashed on 2026-05-26 because migration
# q7r9s1t3u5v7 (VARCHAR(10)->VARCHAR(25) for OCC option symbols) shipped
# but was never applied. Run after `up -d --build` so the new image is
# what executes alembic.
echo ""
echo "Running alembic migrations..."
ssh "${SSH_TARGET}" "docker exec backend-backend-1 alembic upgrade head"

# Settings smoke test. The pipeline crashes silently 6+ hours later on
# AttributeError when config/__init__.py drifts from scheduler.py
# references. See partial_scp_deploy_drift memory. Catch it here.
echo ""
echo "Smoke-testing Settings..."
ssh "${SSH_TARGET}" "docker exec backend-backend-1 python -c '
from src.config import get_settings
from src.pipeline import scheduler  # noqa: F401 — import-time AttributeError surface
s = get_settings()
print(\"  Settings OK ({} fields)\".format(len(s.model_dump())))
'"

echo ""
echo "Health:"
ssh "${SSH_TARGET}" "sleep 3 && curl -s http://localhost:8000/api/health | head -c 400"
echo ""
echo ""
echo "Deployed successfully."
echo "  Backend:  http://${REMOTE_HOST}:8000/api/health"
echo "  Frontend: http://${REMOTE_HOST}:3100"
echo "  Docs:     http://${REMOTE_HOST}:8000/docs"
