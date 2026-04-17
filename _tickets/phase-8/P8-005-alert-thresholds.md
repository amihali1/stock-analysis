# P8-005: Tighten alert thresholds for high-conviction-only strategy

**Status**: done
**Phase**: 8
**Dependencies**: P8-002
**Estimated scope**: small (1-2 files)

## Description
With the new high-confidence-only filtering (P8-002), the alerting system's "high conviction" threshold should be recalibrated. Since we're already filtering out low-confidence signals, alerts should only fire for the strongest setups.

## Acceptance Criteria
- [ ] `check_high_conviction_alerts()` threshold raised to match or exceed the ensemble's `min_confidence`
- [ ] Alert messages include the confidence score and which models agreed
- [ ] Reduce alert noise: don't alert on every recommendation, only on standout signals above a higher bar
- [ ] Tests updated for new thresholds

## Files to Create/Modify
- `backend/src/services/alerting.py`
- `backend/tests/test_alerting.py` (if exists)

## Notes
- The goal is fewer, higher-quality alerts. If we're only recommending high-conviction trades, the alert threshold should be even higher — the cream of the crop.
