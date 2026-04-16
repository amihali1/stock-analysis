# P7-003: Trading safety rails

**Status**: done
**Phase**: 7
**Dependencies**: P7-001
**Estimated scope**: medium

## Description
Hard safety limits that gate all order execution. These must be impossible to bypass without changing config — no "are you sure?" prompts, just hard stops.

## Acceptance Criteria
- [ ] `TradingSafetyRails` class in `services/safety_rails.py` with configurable limits:
  - `max_daily_loss`: max cumulative realized + unrealized loss per day (default $500)
  - `max_open_positions`: max concurrent open positions (default 5)
  - `max_single_position`: max dollar value per position (default $5,000 — mirrors existing constraint)
  - `max_daily_orders`: max orders submitted per day (default 20)
  - `allowed_hours_only`: restrict to market hours (default `true`)
  - `blocked_tickers`: list of tickers to never trade (e.g., leveraged ETFs)
  - `mode`: `disabled` | `paper` | `live` (default `disabled`)
- [ ] `check_order(order) -> (bool, reason)` — validates an order against all rails
- [ ] `check_daily_loss()` — queries Alpaca account P&L, blocks if limit hit
- [ ] All checks must pass before any order reaches Alpaca
- [ ] Safety config stored in `Settings` with env var overrides
- [ ] DB table `trading_log` recording every order attempt (passed or blocked) with reason
- [ ] Alembic migration for `trading_log` table
- [ ] Tests: each rail individually, combined scenario, mode=disabled blocks everything

## Files to Create/Modify
- `backend/src/services/safety_rails.py` (new)
- `backend/src/config.py` (add safety settings)
- `backend/src/db/models.py` (add TradingLog model)
- `backend/alembic/versions/xxx_add_trading_log.py` (new migration)
- `backend/tests/test_safety_rails.py` (new)

## Notes
- This is the most critical ticket in Phase 7. Every order must flow through safety rails.
- The `mode` setting is the master switch: `disabled` means no orders ever, `paper` allows paper only, `live` allows real money.
- Consider: should there be a cooldown after a daily loss limit is hit? (e.g., no trading for rest of day)
