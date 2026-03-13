# P0-003: Build data fetcher module

**Status**: done
**Phase**: 0
**Dependencies**: P0-002
**Estimated scope**: medium

## Description
Build `data_fetcher.py` to fetch daily OHLCV data from yfinance for a configurable watchlist of tickers. Store results in the `price_history` table. Handle errors gracefully (delisted tickers, API failures).

## Acceptance Criteria
- [ ] `DataFetcher` class with `fetch_daily(tickers: list[str], period: str)` method
- [ ] Fetches OHLCV data from yfinance
- [ ] Upserts into `price_history` table (no duplicates on ticker+date)
- [ ] Default watchlist of ~50 tickers across sectors (configurable via config)
- [ ] Logs errors per ticker without stopping the batch
- [ ] Can be run as `python -m src.pipeline.data_fetcher`

## Files to Create/Modify
- `backend/src/pipeline/data_fetcher.py`
- `backend/src/config.py` (add DEFAULT_WATCHLIST)

## Notes
Start with 2 years of daily data (`period="2y"`). For initial development, test with 5 tickers first.
