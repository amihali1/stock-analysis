# P5-001: Build backtesting framework

**Status**: done
**Phase**: 5
**Dependencies**: P2-003
**Estimated scope**: large

## Description
Build a backtesting engine to replay historical recommendations against actual outcomes.

## Acceptance Criteria
- [ ] `Backtester` class that replays model signals on historical data
- [ ] Metrics: Sharpe ratio, win rate, max drawdown, profit factor
- [ ] Walk-forward backtest (retrain periodically)
- [ ] Output: performance report as JSON + plots
- [ ] Jupyter notebook for interactive backtesting
- [ ] Compare strategies: short-only, options-only, combined

## Files to Create/Modify
- `backend/src/models/backtester.py`
- `backend/notebooks/backtesting.ipynb`
