# P5-002: Automated model retraining pipeline

**Status**: todo
**Phase**: 5
**Dependencies**: P5-001
**Estimated scope**: medium

## Description
Monthly automated retraining of ML models on new data. Compare new model vs current, only deploy if improved.

## Acceptance Criteria
- [ ] Retraining script for directional and volatility models
- [ ] Champion/challenger comparison on holdout set
- [ ] Auto-deploy new model only if metrics improve
- [ ] Version tracking in MODEL_REGISTRY.md
- [ ] Scheduler job: first Sunday of each month

## Files to Create/Modify
- `backend/src/models/retrainer.py`
- `backend/src/pipeline/scheduler.py` (add monthly job)
