# P8-006: Frontend updates for risk management changes

**Status**: done
**Phase**: 8
**Dependencies**: P8-001, P8-002, P8-003
**Estimated scope**: medium (3-5 files)

## Description
Update the frontend to reflect the new risk management constraints. Display the $1,000 budget, show confidence scores prominently, and add visual indicators for risk type.

## Acceptance Criteria
- [ ] Dashboard: any display of "$5,000" budget updated to "$1,000"
- [ ] Recommendation cards show model confidence score prominently
- [ ] Recommendations show `risk_type` badge: green "Defined Risk" or yellow/red "Undefined Risk" warning
- [ ] Analysis page: signal breakdown includes per-model confidence (directional, vol, sentiment)
- [ ] Position detail shows max loss clearly — no recommendation should show without a known max loss
- [ ] Paper trading page reflects $1,000 position sizes
- [ ] Portfolio risk page: any hardcoded budget references updated

## Files to Create/Modify
- `frontend/src/app/dashboard/page.tsx`
- `frontend/src/app/analysis/[ticker]/page.tsx`
- `frontend/src/app/paper-trades/page.tsx`
- `frontend/src/app/portfolio/page.tsx`
- `frontend/src/lib/types.ts` (add risk_type, confidence fields)

## Notes
- Keep UI changes minimal — update values and add badges, don't redesign layouts.
