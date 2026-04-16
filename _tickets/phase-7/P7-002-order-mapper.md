# P7-002: Order mapper — recommendations to Alpaca orders

**Status**: done
**Phase**: 7
**Dependencies**: P7-001, P2-003
**Estimated scope**: medium

## Description
Translate the platform's recommendation + position sizing output into concrete Alpaca order parameters. Handle both short equity and options strategies.

## Acceptance Criteria
- [ ] `OrderMapper` class in `services/order_mapper.py` with:
  - `recommendation_to_order()` — takes a recommendation row + position sizing, returns an Alpaca-ready order dict
  - Short recommendations → bracket sell-short order (entry limit, stop-loss, take-profit)
  - Options recommendations → single-leg option order (buy put or sell call) with appropriate limit price
  - Spread recommendations → multi-leg order if Alpaca supports, else individual legs with notes
- [ ] Validates order against account buying power before submission
- [ ] Dry-run mode: builds the order object and logs it without submitting
- [ ] Maps platform stop-loss/target prices to Alpaca's bracket order legs
- [ ] Respects $5,000 max position constraint (redundant check on top of position sizer)
- [ ] Tests covering: short mapping, put mapping, spread mapping, insufficient buying power rejection, dry-run mode

## Files to Create/Modify
- `backend/src/services/order_mapper.py` (new)
- `backend/tests/test_order_mapper.py` (new)

## Notes
- Alpaca options trading may require separate enablement on the account
- For spreads, check Alpaca's multi-leg support — may need to submit as individual legs with a linking tag
