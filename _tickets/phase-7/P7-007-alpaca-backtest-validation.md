# P7-007: Backtest validation against Alpaca paper trading

**Status**: todo
**Phase**: 7
**Dependencies**: P7-005, P5-001
**Estimated scope**: medium

## Description
Run the platform in Alpaca paper mode for a validation period, then compare paper results against what the backtester would have predicted. This validates that the live execution path produces outcomes consistent with backtested performance.

## Acceptance Criteria
- [ ] Script `scripts/validate_paper_trading.py`:
  - Pulls completed paper trades from Alpaca (via portfolio sync)
  - Runs the backtester over the same date range with the same parameters
  - Compares: win rate, average P&L per trade, Sharpe ratio, max drawdown
  - Generates a comparison report (JSON + human-readable summary)
  - Flags significant divergences (>10% difference on any metric)
- [ ] Divergence analysis: identifies common causes (slippage, fill timing, data lag)
- [ ] API endpoint: `GET /api/validate/paper-vs-backtest?start_date=X&end_date=Y`
- [ ] Tests with synthetic trade data

## Files to Create/Modify
- `backend/scripts/validate_paper_trading.py` (new)
- `backend/src/api/routes/validation.py` (new)
- `backend/tests/test_paper_validation.py` (new)

## Notes
- This should run after at least 2 weeks of paper trading to have meaningful data
- Key divergences to watch: slippage on shorts (borrow availability), options fill rates, stop-loss execution timing
- This ticket is the gate before enabling live trading — don't go live until paper results are validated
