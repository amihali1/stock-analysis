# P6-002: Backtesting UI

**Status**: todo
**Phase**: 6
**Dependencies**: P5-001, P4-001
**Estimated scope**: large

## Description
Frontend page to configure, run, and visualize backtest results including equity curves, trade tables, and strategy comparisons.

## Acceptance Criteria
- [ ] `/backtest` page with configuration form (strategy, date range, hold days, score threshold, max concurrent)
- [ ] Ticker multi-select (default: all watchlist)
- [ ] Run backtest button that calls POST /api/backtest
- [ ] Loading state while backtest runs
- [ ] Results panel: summary metrics cards (Sharpe, win rate, drawdown, profit factor, total P&L)
- [ ] Equity curve chart (cumulative P&L + unrealized over time) using lightweight-charts
- [ ] Trade table: sortable by date/ticker/P&L/return, color-coded win/loss, stop/target hit indicators
- [ ] Strategy comparison tab: runs all three strategies, shows side-by-side metrics table
- [ ] Export results as JSON download

## Files to Create/Modify
- `frontend/src/app/backtest/page.tsx`
- `frontend/src/components/EquityCurve.tsx`
- `frontend/src/components/BacktestTradeTable.tsx`
- `frontend/src/components/BacktestMetrics.tsx`
- `frontend/src/lib/api.ts` (add backtest functions)
- `frontend/src/lib/types.ts` (add backtest types)
