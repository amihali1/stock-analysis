# P6-004: Real options chain data

**Status**: todo
**Phase**: 6
**Dependencies**: P5-004
**Estimated scope**: large

## Description
Pull real options chain data (strikes, expirations, premiums, implied volatility, Greeks) from yfinance or CBOE instead of using Black-Scholes estimates.

## Acceptance Criteria
- [ ] `OptionsChainFetcher` service fetching available expirations and strikes per ticker
- [ ] Store options chain snapshots in DB (new `options_chain` table)
- [ ] Fields: ticker, expiration, strike, option_type, bid, ask, last, volume, open_interest, implied_vol, delta, gamma, theta, vega
- [ ] Cache with configurable TTL (default: 15 min during market hours)
- [ ] Update `SpreadBuilder` to use real premiums and Greeks instead of BS estimates
- [ ] Update `PositionSizer.size_spread()` to use real chain data
- [ ] API endpoint: GET /api/options-chain/{ticker}?expiration=YYYY-MM-DD
- [ ] Fallback to BS estimates when chain data unavailable

## Files to Create/Modify
- `backend/src/services/options_chain.py`
- `backend/src/db/models.py` (add OptionsChain model)
- `backend/src/models/options_strategies.py` (use real data)
- `backend/src/models/position_sizer.py` (use real data)
- `backend/src/api/routes/options.py`
- `backend/alembic/versions/` (new migration)
