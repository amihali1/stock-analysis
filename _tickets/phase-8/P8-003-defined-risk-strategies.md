# P8-003: Default to defined-risk strategies

**Status**: done
**Phase**: 8
**Dependencies**: P8-001
**Estimated scope**: medium (3-5 files)

## Description
Shift the recommendation engine to prefer defined-risk strategies (vertical spreads, cash-secured puts, debit spreads) over naked/unlimited-risk positions. Naked shorts and uncovered options should only be recommended when the defined-risk alternative is unavailable or clearly inferior, and even then flagged as higher risk.

## Acceptance Criteria
- [ ] `SpreadBuilder` prioritizes defined-risk strategies: bear put spreads (debit), bear call spreads (credit with defined max loss), iron condors
- [ ] Naked short recommendations are deprioritized — only surface if no viable spread alternative exists for the signal
- [ ] Every recommendation includes `max_loss_dollars` field that is a hard, known number (not "unlimited")
- [ ] Add `risk_type` field to recommendations: `defined` | `undefined` — undefined positions carry a warning flag
- [ ] Frontend: undefined-risk positions show a visual warning indicator
- [ ] Position sizer refuses to size a position where max loss cannot be calculated
- [ ] Tests: verify defined-risk strategies are preferred, undefined-risk positions are flagged

## Files to Create/Modify
- `backend/src/models/options_strategies.py`
- `backend/src/models/position_sizer.py`
- `backend/src/models/ensemble.py`
- `backend/src/db/models.py` (add `risk_type` to Recommendation model)
- `backend/src/api/schemas.py`
- `frontend/src/lib/types.ts`
- `frontend/src/app/dashboard/` (warning indicator)
- `backend/alembic/versions/xxx_add_risk_type.py`

## Notes
- This pairs with P8-001 (budget) and P8-002 (confidence). Together they form the core risk management overhaul.
- Cash-secured puts are inherently defined-risk (max loss = strike - premium). These are good candidates for the $1,000 budget.
