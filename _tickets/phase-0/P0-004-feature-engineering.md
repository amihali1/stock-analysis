# P0-004: Build feature engineering module

**Status**: todo
**Phase**: 0
**Dependencies**: P0-003
**Estimated scope**: medium

## Description
Build `feature_eng.py` to compute technical indicators from price history. Store results in `technical_indicators` table.

## Acceptance Criteria
- [ ] Compute: RSI (14-period), MACD (12,26,9), Bollinger Bands (20,2), 50-day SMA, 200-day SMA, SMA crossover signal, volume z-score (20-period)
- [ ] `FeatureEngineer` class with `compute_features(ticker: str)` method
- [ ] Reads from `price_history`, writes to `technical_indicators`
- [ ] Handles edge cases: not enough data for 200-day SMA, zero volume
- [ ] Can process all tickers in batch
- [ ] Can be run as `python -m src.pipeline.feature_eng`

## Files to Create/Modify
- `backend/src/pipeline/feature_eng.py`

## Notes
Use pandas/numpy for calculations. Do NOT use TA-Lib (C dependency, hard to install). Implement indicators from scratch — they're simple formulas.
