# P8-004: Update safety rails for $1,000 limit

**Status**: todo
**Phase**: 8
**Dependencies**: P8-001, P7-003
**Estimated scope**: small (1-2 files)

## Description
P7-003 creates `TradingSafetyRails` with a $5,000 `max_single_position` default. Update this to $1,000 to match the new budget constraint. Also tighten the daily loss limit proportionally.

## Acceptance Criteria
- [ ] `max_single_position` default → $1,000
- [ ] `max_daily_loss` default → $200 (proportional reduction from $500)
- [ ] Verify all safety rail tests pass with updated defaults
- [ ] If P7-003 is not yet done, add a note to P7-003 to use $1,000 from the start

## Files to Create/Modify
- `backend/src/services/safety_rails.py`
- `backend/tests/test_safety_rails.py`

## Notes
- If P7-003 hasn't been implemented yet, this ticket can be folded into P7-003 instead of being done separately. Check P7-003 status before starting.
