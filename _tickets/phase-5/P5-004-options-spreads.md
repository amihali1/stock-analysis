# P5-004: Expand to options spread strategies

**Status**: done
**Phase**: 5
**Dependencies**: P2-003, P4-004
**Estimated scope**: large

## Description
Add defined-risk options strategies: bull put spreads, bear call spreads, iron condors.

## Acceptance Criteria
- [ ] Spread builder: given directional + vol signals, suggest appropriate spread
- [ ] Position sizing for spreads (max loss = spread width × contracts)
- [ ] Profit/loss diagrams in frontend
- [ ] Greeks display (delta, theta, vega)
- [ ] Earnings-aware: flag if expiration crosses earnings date

## Files to Create/Modify
- `backend/src/models/options_strategies.py`
- `backend/src/models/position_sizer.py` (extend for spreads)
- `frontend/src/components/PLDiagram.tsx`
- `frontend/src/components/GreeksDisplay.tsx`
