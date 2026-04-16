# P7-006: Trading UI — portfolio, execution, and controls

**Status**: todo
**Phase**: 7
**Dependencies**: P7-004, P7-005
**Estimated scope**: large

## Description
Frontend pages for viewing the Alpaca portfolio, manually triggering trades, and controlling the trading mode. This is the operator interface for the automated trader.

## Acceptance Criteria
- [ ] **Portfolio page** (`/portfolio`):
  - Account summary card: equity, buying power, daily P&L, day trade count
  - Open positions table: ticker, qty, side, entry, current, unrealized P&L, % change
  - Close button per position (calls execution engine)
  - Order history table with status badges (filled, partial, canceled, rejected)
- [ ] **Trading controls** (top-level or settings page):
  - Mode selector: Disabled / Paper / Live (with confirmation dialog for Live)
  - Auto-execute toggle with score threshold slider
  - Safety rail displays: current daily loss vs. limit, open positions vs. limit
  - Emergency close all button (with confirmation)
- [ ] **Execution log page** (`/execution-log`):
  - Table of all execution attempts: timestamp, ticker, action, status, reason (if blocked)
  - Filter by: passed/blocked, date range
- [ ] **Dashboard integration**:
  - Recommendations table gets an "Execute" button per row (when trading is enabled)
  - Visual indicator of trading mode in the nav bar (colored badge: gray/yellow/red)
- [ ] Auto-refresh on portfolio and execution log pages

## Files to Create/Modify
- `frontend/src/app/portfolio/page.tsx` (new)
- `frontend/src/app/execution-log/page.tsx` (new)
- `frontend/src/components/TradingControls.tsx` (new)
- `frontend/src/components/AccountSummary.tsx` (new)
- `frontend/src/components/PositionsTable.tsx` (new)
- `frontend/src/app/layout.tsx` (add nav items + trading mode badge)
- `frontend/src/lib/api.ts` (add portfolio/execution API methods)
- `frontend/src/lib/types.ts` (add portfolio/execution types)

## Notes
- The mode selector switching to "Live" should require typing "CONFIRM" or similar — not just a click
- Consider color scheme: gray = disabled, yellow = paper, red = live (always visible)
