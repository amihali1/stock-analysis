# P2-003: Build ensemble scorer and position sizer

**Status**: done
**Phase**: 2
**Dependencies**: P2-001, P2-002, P1-002
**Estimated scope**: medium

## Description
Combine directional, volatility, and sentiment signals into a single recommendation score. Implement position sizing with the $5,000 budget constraint.

## Acceptance Criteria
- [ ] `Ensemble` class in `models/ensemble.py`
- [ ] Weighted score: `w1*directional + w2*vol_signal + w3*sentiment` (default equal weights)
- [ ] Configurable weights
- [ ] `PositionSizer` class in `models/position_sizer.py`
- [ ] For shorts: calculates share count where margin requirement ≤ $5,000
- [ ] For options: filters contracts where premium × 100 × contracts ≤ $5,000
- [ ] Every recommendation includes: entry price, stop-loss, target, max loss in dollars
- [ ] Outputs `Recommendation` Pydantic model
- [ ] Unit tests for position sizing edge cases

## Files to Create/Modify
- `backend/src/models/ensemble.py`
- `backend/src/models/position_sizer.py`
- `backend/tests/test_position_sizer.py`
