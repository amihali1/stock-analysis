# P8-001: Update budget constraint from $5,000 to $1,000

**Status**: done
**Phase**: 8
**Dependencies**: none
**Estimated scope**: medium (3-5 files)

## Description
Replace all hardcoded $5,000 max buy-in constraints with $1,000. This is the foundational change for the risk management overhaul — every downstream ticket assumes the new limit.

## Acceptance Criteria
- [ ] `PositionSizer` in `models/position_sizer.py`: default max budget → $1,000
- [ ] Shorts: margin requirement ≤ $1,000
- [ ] Options: premium × 100 × contracts ≤ $1,000
- [ ] `SpreadBuilder` in `models/options_strategies.py`: max spread cost → $1,000
- [ ] `PositionSizer.size_spread()` updated to $1,000
- [ ] `RiskManager` in `models/risk_manager.py`: any references to $5,000 updated
- [ ] All existing position sizing tests updated for $1,000 expectations
- [ ] All spread tests updated for $1,000 expectations
- [ ] All tests pass

## Files to Create/Modify
- `backend/src/models/position_sizer.py`
- `backend/src/models/options_strategies.py`
- `backend/src/models/risk_manager.py`
- `backend/tests/test_position_sizer.py`
- `backend/tests/test_options_strategies.py` (if exists)

## Notes
- Search the entire codebase for "5000" and "5,000" to catch any stray references.
- The budget should be a configurable constant (e.g., `MAX_TRADE_COST`) imported from config, not scattered as magic numbers. If it's already centralized, just change the default. If not, centralize it as part of this ticket.
