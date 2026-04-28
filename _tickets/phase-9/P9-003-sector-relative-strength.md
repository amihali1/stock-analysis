# P9-003: Add sector relative-strength features

**Status**: done
**Phase**: 9
**Dependencies**: P9-002 (shares the macro feature join pattern)
**Estimated scope**: small (1-2 files)

## Description
A ticker's absolute return tells you less than its return *relative to its sector*. A name down 2% on a day the sector is down 4% is showing strength; down 2% when the sector is up 1% is the opposite. The sentiment model already touches sector-level news — give the directional model sector price action too.

## Acceptance Criteria
- [ ] Static ticker→sector-ETF mapping in `backend/src/config.py` (XLK for tech, XLF for financials, etc.); default to SPY for tickers without an obvious sector
- [ ] All sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLC, XLRE) added to the price-history fetch list
- [ ] Feature engineering computes per-ticker:
  - `return_5d_vs_sector` (ticker_ret_5d − sector_ret_5d)
  - `return_20d_vs_sector`
  - `sector_return_5d`, `sector_return_20d` (the sector's absolute return too — model can learn interactions)
- [ ] `DirectionalModel` retrained
- [ ] Tests verify join works for all sectors, SPY-fallback branch covered

## Files to Create/Modify
- `backend/src/config.py` (add SECTOR_ETF_MAP)
- `backend/src/features/sector.py` (new)
- `backend/src/models/directional.py` (extend feature list)
- `backend/src/pipeline/data_fetcher.py` (ensure sector ETFs are fetched)
- `backend/tests/test_sector_features.py` (new)

## Notes
- Sector assignment is static and can be hardcoded from the watchlist. Don't overbuild this — a simple dict is fine.
- Follow the same date-join pattern as macro features (P9-002).
- If a ticker moves between sectors historically (rare), ignore — accept minor mislabeling rather than building a date-versioned mapping.
