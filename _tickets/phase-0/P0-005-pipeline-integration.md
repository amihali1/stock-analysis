# P0-005: End-to-end pipeline integration test

**Status**: todo
**Phase**: 0
**Dependencies**: P0-004
**Estimated scope**: small

## Description
Wire up the data fetcher and feature engineer into a single pipeline run. Verify the full flow: fetch → store prices → compute features → store indicators.

## Acceptance Criteria
- [ ] `pipeline/runner.py` orchestrates fetch + feature computation
- [ ] Run for 5 test tickers end-to-end
- [ ] Verify data in DB: prices and indicators for all 5 tickers
- [ ] Logging shows clear progress and timing
- [ ] Add a basic test in `tests/test_pipeline.py`

## Files to Create/Modify
- `backend/src/pipeline/runner.py`
- `backend/tests/test_pipeline.py`

## Notes
This is the Phase 0 milestone: "Run the pipeline and see features stored in the database."
