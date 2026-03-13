# P5-003: Add alerting system

**Status**: todo
**Phase**: 5
**Dependencies**: P4-005
**Estimated scope**: medium

## Description
Push notifications when recommendations hit stop-loss or target, or when high-conviction signals emerge.

## Acceptance Criteria
- [ ] Webhook support (Discord/Telegram)
- [ ] Alert triggers: stop-loss hit, target hit, new high-confidence recommendation
- [ ] Alert history in DB
- [ ] Configurable alert preferences
- [ ] API endpoint to manage alert settings

## Files to Create/Modify
- `backend/src/services/alerting.py`
- `backend/src/db/models.py` (add Alert model)
- `backend/src/api/routes/alerts.py`
