# P7-004: Portfolio sync with Alpaca

**Status**: done
**Phase**: 7
**Dependencies**: P7-001, P4-005
**Estimated scope**: medium

## Description
Sync real positions, balances, and order history from Alpaca into the platform's database. Provides a unified view alongside internal paper trades.

## Acceptance Criteria
- [ ] `PortfolioSync` class in `services/portfolio_sync.py`:
  - `sync_positions()` — pulls all open positions from Alpaca, upserts to local DB
  - `sync_orders()` — pulls recent order history, records fills/cancels/rejects
  - `sync_account()` — pulls account equity, buying power, day trade count
  - `get_portfolio_summary()` — aggregated view: total equity, open P&L, daily P&L, position count
- [ ] DB model `AlpacaPosition` with: ticker, qty, side, avg_entry, current_price, unrealized_pl, market_value, synced_at
- [ ] DB model `AlpacaOrder` with: alpaca_order_id, ticker, side, qty, type, status, filled_price, submitted_at, filled_at
- [ ] Alembic migration for new tables
- [ ] Scheduler job: sync positions every 5 minutes during market hours
- [ ] API endpoints:
  - `GET /api/portfolio` — account summary + open positions
  - `GET /api/portfolio/orders` — recent order history with status
- [ ] Tests with mocked Alpaca responses

## Files to Create/Modify
- `backend/src/services/portfolio_sync.py` (new)
- `backend/src/db/models.py` (add AlpacaPosition, AlpacaOrder)
- `backend/src/api/routes/portfolio.py` (new)
- `backend/src/pipeline/scheduler.py` (add sync job)
- `backend/alembic/versions/xxx_add_alpaca_tables.py` (new migration)
- `backend/tests/test_portfolio_sync.py` (new)
