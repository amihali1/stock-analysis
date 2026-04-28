# P9-007: Extend training window, retrain, and backtest-validate the new model

**Status**: done
**Phase**: 9
**Dependencies**: P9-001, P9-002, P9-006 (tier 1 must ship first); P9-003/4/5 optional but preferred
**Estimated scope**: medium (3-5 files)

## Description
Once the feature set is expanded (P9-001 through P9-006), the model must be retrained on a sufficiently long window and validated end-to-end with a walk-forward backtest before the old model is replaced in production.

The AUC-0.52 baseline shipped in commit 8235009 was trained on the default window with the old feature set; re-running the same training script with the new features is *not* sufficient validation. This ticket closes the loop.

## Acceptance Criteria
- [ ] Training window extended to 5 years (or max yfinance allows for each ticker) — currently shorter
- [ ] Walk-forward backtest harness in `backend/src/backtest/`:
  - Rolling train/test splits (e.g., train on 2020-2022, test 2023; slide forward)
  - Simulate the full recommendation pipeline: ensemble scoring, confidence gate, position sizing
  - Record per-trade: entry, exit, P&L, max loss, which model(s) flagged it
  - Aggregate metrics: hit rate, avg P&L per trade, Sharpe, max drawdown, avg conf per trade
- [ ] Backtest report written to `backend/backtest_reports/<date>-<git-sha>.md` with old-vs-new comparison
- [ ] Minimum bar for shipping new model: AUC > 0.55, backtest hit rate > 52%, Brier score improved vs old
- [ ] Old model archived (not overwritten) — keep in `trained_models/archive/<date>/`
- [ ] Update `_memory/MODEL_REGISTRY.md` with new model metadata
- [ ] Deploy retrained model to VM via `docker cp` (same process as prior deploy) and confirm `/api/health` reports expected confidence distribution

## Files to Create/Modify
- `backend/src/backtest/walk_forward.py` (new, may extend existing backtest code if present)
- `backend/src/backtest/report.py` (new)
- `backend/scripts/run_backtest.py` (new)
- `backend/scripts/train_directional.py` (extend window)
- `_memory/MODEL_REGISTRY.md` (update)
- `backend/backtest_reports/` (new directory)

## Notes
- Do not deploy the new model if backtest thresholds aren't met — feature additions are not automatically improvements.
- The 5y window means some tickers in the current watchlist won't have full history (recent IPOs). Drop or truncate them in training rather than padding with zeros.
- Walk-forward is mandatory — a single-shot 80/20 split overstates performance on time-series data.
- After this ticket, `directional_confidence ≥ 0.75` hit rate should be tracked as a live metric (Prometheus gauge) so regression is visible.
