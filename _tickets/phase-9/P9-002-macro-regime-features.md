# P9-002: Add macro/market regime features to directional model

**Status**: done
**Phase**: 9
**Dependencies**: none
**Estimated scope**: small (1-2 files)

## Description
Individual-stock directional predictions are heavily conditioned on market regime. A 2% selloff setup on a single name behaves very differently when VIX = 15 and SPY is at ATH vs when VIX = 35 and SPY is in a drawdown. The current feature set is entirely per-ticker — add market context.

Part of the Tier 1 model-quality bundle (P9-001 + P9-002 + P9-006). Cheapest change of the three; highest code-to-signal ratio.

## Acceptance Criteria
- [ ] Ingest SPY and VIX daily into `price_history` as regular tickers (they may already be — verify)
- [ ] Feature engineering in `backend/src/features/macro.py` joins the per-bar `as_of_date` with SPY/VIX values from the same date
- [ ] Features added to directional model:
  - `vix_level` (raw VIX close)
  - `vix_percentile_252d` (rank within trailing year)
  - `spy_drawdown_pct` (SPY close vs trailing 252d max)
  - `spy_above_sma_50` (binary: SPY close > SPY SMA50)
  - `spy_above_sma_200` (binary)
  - `spy_return_5d`, `spy_return_20d`
- [ ] `DirectionalModel` retrained — document AUC change
- [ ] Tests verify features are correctly aligned by date (no look-ahead, no missing rows)
- [ ] Handle VIX data gaps gracefully (backfill forward, not backward)

## Files to Create/Modify
- `backend/src/features/macro.py` (new)
- `backend/src/models/directional.py` (extend feature list and training join logic)
- `backend/src/pipeline/data_fetcher.py` (add SPY and ^VIX to default ticker set if absent)
- `backend/tests/test_macro_features.py` (new)

## Notes
- VIX symbol in yfinance is `^VIX`.
- VIX has specific holidays that may differ from SPY — join on `as_of_date` with a left-join from ticker's price_history so ticker-days without VIX get nulls to be forward-filled.
- Don't include features like "next-day SPY return" — obvious leak but easy to accidentally add.
- Expect AUC bump of ~0.01-0.03 from this feature group alone on equity directional problems (published research on sector rotation / regime filters).
