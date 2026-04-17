# P8-002: Add minimum confidence threshold to ensemble scorer

**Status**: done
**Phase**: 8
**Dependencies**: P8-001
**Estimated scope**: medium (3-5 files)

## Description
The ensemble scorer currently surfaces all recommendations regardless of model agreement. Add a minimum confidence threshold so only high-conviction trades are recommended. Models must agree with high confidence before a trade is surfaced — marginal setups should be filtered out entirely.

## Acceptance Criteria
- [ ] Add `min_confidence` config parameter (default 0.75 or higher — tune based on backtest results)
- [ ] `Ensemble` in `models/ensemble.py`: skip recommendations where any individual model confidence is below threshold
- [ ] Add `model_agreement` metric: all models must signal the same direction (bearish for shorts, high vol for vol plays)
- [ ] Recommendations that fail the threshold are logged but not stored in `recommendations` table
- [ ] Add `filtered_count` to recommendation generation logs (how many were skipped)
- [ ] `job_generate_recommendations` in scheduler respects the new threshold
- [ ] Backtest the threshold: run backtester with old vs new threshold, document win rate difference
- [ ] Tests for threshold filtering (signals below threshold rejected, above threshold accepted, edge cases at threshold)

## Files to Create/Modify
- `backend/src/models/ensemble.py`
- `backend/src/pipeline/scheduler.py`
- `backend/src/config.py`
- `backend/tests/test_ensemble.py` (new or extend existing)

## Notes
- The exact threshold value should be tunable via config/env var. Start conservative (0.75) and adjust after backtesting.
- Consider: should confidence be a composite score, or must EACH model independently meet the threshold? Recommendation: each model independently, since a high-confidence directional + low-confidence vol signal is a weak setup overall.
