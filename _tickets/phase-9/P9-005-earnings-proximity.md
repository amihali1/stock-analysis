# P9-005: Earnings proximity features

**Status**: done
**Phase**: 9
**Dependencies**: none
**Estimated scope**: small (1-2 files)

## Description
Trades within ~10 days of earnings behave structurally differently — IV is elevated, gap risk dominates, and the directional signal is drowned out by binary event risk. The model should know this both to learn different behavior pre/post-earnings and to let the gate filter earnings trades out if desired.

## Acceptance Criteria
- [ ] New `EarningsCalendar` table (ticker, earnings_date, source, fetched_at) populated via yfinance `Ticker.calendar` or a dedicated source
- [ ] Scheduler job `job_fetch_earnings` runs weekly (earnings dates don't change often)
- [ ] Features at prediction time:
  - `days_to_earnings` (int, capped at e.g. 90; -1 if unknown)
  - `earnings_within_3d` (bool)
  - `earnings_within_10d` (bool)
  - `days_since_earnings` (int; useful for post-earnings drift patterns)
- [ ] Optional config `SKIP_NEAR_EARNINGS` — if true, scheduler filters out `earnings_within_3d=True` before passing to ensemble (document in settings)
- [ ] Tests: unknown-earnings ticker returns -1, features flip correctly at boundaries

## Files to Create/Modify
- `backend/alembic/versions/<new>_add_earnings_calendar.py` (new)
- `backend/src/db/models.py` (EarningsCalendar)
- `backend/src/pipeline/earnings_fetcher.py` (new)
- `backend/src/features/earnings.py` (new)
- `backend/src/pipeline/scheduler.py` (add job_fetch_earnings; optional skip)
- `backend/src/config.py` (add SKIP_NEAR_EARNINGS flag)
- `backend/tests/test_earnings_features.py` (new)

## Notes
- yfinance's `Ticker.calendar` is flaky — handle missing data gracefully, don't crash the pipeline.
- Long-term consider a paid source (FMP, Alpha Vantage) but yfinance is sufficient for a first cut.
- `days_to_earnings = -1` signals "unknown" — the model learns to treat it differently than "100 days out".
