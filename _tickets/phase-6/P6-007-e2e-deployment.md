# P6-007: End-to-end deployment and pipeline validation

**Status**: todo
**Phase**: 6
**Dependencies**: P3-003
**Estimated scope**: medium

## Description
Validate the full pipeline end-to-end on the homelab: deploy via Docker, run all scheduler jobs, confirm frontend connects, and run a real backtest with live data.

## Acceptance Criteria
- [ ] Deploy to homelab VM (10.0.0.47) via deploy.sh
- [ ] Verify PostgreSQL container starts and migrations run
- [ ] Verify scheduler fires all 5 jobs (4 daily + monthly retrain)
- [ ] Confirm price fetch populates price_history for all watchlist tickers
- [ ] Confirm indicators compute for all fetched tickers
- [ ] Confirm sentiment analysis runs (requires Ollama available)
- [ ] Confirm recommendations generate with score > 0.5
- [ ] Verify frontend at :3100 loads dashboard with live data
- [ ] Run backtest via API with real data and verify metrics
- [ ] Train both ML models on current data and update MODEL_REGISTRY
- [ ] Document any env-specific fixes in deployment notes

## Files to Create/Modify
- `scripts/deploy.sh` (any fixes discovered during deployment)
- `backend/docker-compose.yml` (any fixes)
- `_memory/SESSION_LOG.md` (deployment results)
