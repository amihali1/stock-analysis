# P9-001: Add options-based IV features to directional model

**Status**: done
**Phase**: 9
**Dependencies**: none
**Estimated scope**: medium (3-5 files)

## Description
The directional classifier is thin on forward-looking information. Options markets price the distribution of future returns directly — IV rank, put/call skew, and term structure are among the strongest single-variable predictors in equity prediction research. Wire these in as features.

Part of the Tier 1 model-quality bundle (P9-001 + P9-002 + P9-006). The model currently has AUC ≈ 0.52 and produces `directional_confidence` ≥ 0.75 for only ~10% of the watchlist on a typical day, which is why `job_generate_recommendations` returns 0 recs. Adding IV features is the highest-leverage change available without restructuring the model.

## Acceptance Criteria
- [ ] New `OptionsSnapshot` table + Alembic migration storing per-ticker daily: `iv_atm_30d`, `iv_rank_252d`, `iv_percentile_252d`, `put_call_skew_25d`, `term_structure_slope` (30d vs 90d IV)
- [ ] `OptionsFetcher` in `backend/src/pipeline/options_fetcher.py` pulling from yfinance option chains (`Ticker.option_chain(expiry)`); include error handling for tickers without listed options
- [ ] Scheduler job `job_fetch_options` runs daily after price fetch, before recommendations
- [ ] Feature engineering in `backend/src/features/options.py` extends the directional feature set
- [ ] `DirectionalModel` retrained with new features — document old vs new AUC in commit message
- [ ] Graceful fallback when options data missing (non-optionable ticker, API error): feature columns filled with median/0, flag `has_options` column added
- [ ] Tests for `OptionsFetcher` (mock yfinance), feature extraction edge cases (missing chain, zero volume), IV rank calc correctness

## Files to Create/Modify
- `backend/alembic/versions/<new>_add_options_snapshot.py` (new)
- `backend/src/db/models.py` (add OptionsSnapshot)
- `backend/src/pipeline/options_fetcher.py` (new)
- `backend/src/features/options.py` (new)
- `backend/src/models/directional.py` (extend feature list)
- `backend/src/pipeline/scheduler.py` (add job_fetch_options)
- `backend/tests/test_options_fetcher.py` (new)
- `backend/tests/test_options_features.py` (new)

## Notes
- IV rank = `(current_iv - min_iv_252d) / (max_iv_252d - min_iv_252d)`. Handles regime changes better than raw IV.
- Put/call skew (25-delta put IV − 25-delta call IV) captures downside fear — strong predictor of mean-reversion after sentiment flushes.
- yfinance returns option chains in `Ticker.option_chain(expiry)`; use the nearest expiry ≥ 30 DTE for 30d IV, ≥ 90 DTE for term structure.
- Ran into rate limits historically — keep `time.sleep(0.5)` between ticker calls in the job.
- Backfill script should populate last 2 years to give the model something to train on.
