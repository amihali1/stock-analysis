# P9-004: Convert sentiment into time-series features

**Status**: done
**Phase**: 9
**Dependencies**: none
**Estimated scope**: small (1-2 files)

## Description
Sentiment is currently scored at prediction time as a point-in-time value. The signal is noisy — a single bearish article means little, but *sudden change* in sentiment vs. baseline is meaningfully predictive. Store daily sentiment history and extract momentum/surprise features from it.

## Acceptance Criteria
- [ ] New `SentimentHistory` table (ticker, as_of_date, sentiment_score, confidence, article_count) — one row per ticker per day
- [ ] Scheduler job persists the daily sentiment result instead of discarding after use
- [ ] Features extracted at prediction time:
  - `sentiment_latest` (today's score)
  - `sentiment_ma_7d`, `sentiment_ma_30d`
  - `sentiment_momentum` (latest − 7d MA)
  - `sentiment_zscore_30d` ((latest − 30d mean) / 30d std)
  - `article_count_zscore_30d` (news volume surprise — big surprise up on flat sentiment is noteworthy too)
- [ ] `Ensemble` optionally consumes the new features (directional model will, via retraining)
- [ ] Backfill: run sentiment over last 90d on first deploy so the z-scores aren't NaN
- [ ] Tests for z-score calc, edge cases (< 30d history, all-same scores → std=0)

## Files to Create/Modify
- `backend/alembic/versions/<new>_add_sentiment_history.py` (new)
- `backend/src/db/models.py` (SentimentHistory)
- `backend/src/pipeline/scheduler.py` (persist sentiment)
- `backend/src/features/sentiment.py` (new, or extend existing)
- `backend/src/models/directional.py` (extend feature list)
- `backend/tests/test_sentiment_features.py` (new)

## Notes
- The backfill is slow (Ollama sentiment per ticker per day × 90 × ~50 tickers). Consider running it once manually inside the container rather than wiring an auto-backfill on first startup.
- Division-by-zero guard on the z-scores when std = 0 (return 0, not NaN).
- Don't delete `SentimentHistory` rows on a retention policy shorter than 1 year — z-scores reach back 30d at prediction time and 1y at training time.
