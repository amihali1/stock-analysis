# P6-007: End-to-end deployment and pipeline validation

**Status**: done
**Phase**: 6
**Dependencies**: P3-003
**Estimated scope**: medium

## Description
Validate the full pipeline end-to-end on the homelab: deploy via Docker, run all scheduler jobs, confirm frontend connects, and run a real backtest with live data.

## Acceptance Criteria
- [x] Deploy to homelab VM (10.0.0.47) via deploy.sh
- [x] Verify PostgreSQL container starts and migrations run
- [x] Verify scheduler fires all 5 jobs (4 daily + monthly retrain)
- [x] Confirm price fetch populates price_history for all watchlist tickers
- [x] Confirm indicators compute for all fetched tickers
- [x] Confirm sentiment analysis runs (requires Ollama available)
- [x] Confirm recommendations generate with score > 0.5
- [x] Verify frontend at :3100 loads dashboard with live data
- [x] Run backtest via API with real data and verify metrics
- [x] Train both ML models on current data and update MODEL_REGISTRY
- [x] Document any env-specific fixes in deployment notes

## Files to Create/Modify
- `scripts/deploy.sh` (any fixes discovered during deployment)
- `backend/docker-compose.yml` (any fixes)
- `_memory/SESSION_LOG.md` (deployment results)

## Deployment Fixes Applied (2026-04-14)
1. **Frontend Dockerfile** — created `frontend/Dockerfile` (multi-stage Node 20 Alpine build with standalone output)
2. **docker-compose.yml** — added `frontend` service (port 3100), fixed Ollama URL from `http://ollama:11434` to `http://host.docker.internal:11434` with `extra_hosts` mapping
3. **deploy.sh** — updated to sync frontend files and display both backend/frontend URLs
4. **Validation** — 63 backend tests passing, frontend builds clean (Next.js 15.5.12, 7 routes, 0 errors)
