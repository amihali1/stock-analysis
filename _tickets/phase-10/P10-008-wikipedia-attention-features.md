# P10-008: Add Wikipedia page-view features (retail attention proxy) to directional model

**Status**: done (2026-05-07; v-bump deferred to first promotion-gate run after backfill)
**Phase**: 10
**Dependencies**: none (independent of all other P10 tickets — can ship in parallel)
**Estimated scope**: small (4-5 files)

## Description

Da, Engelberg & Gao (2011) "In Search of Attention" showed that retail attention measures predict short-horizon returns: spikes in attention precede price moves and reverse over the following weeks, particularly on small-cap and mid-cap names. Their canonical proxy was Google Trends, but Wikipedia page views are a strict superset of usefulness: same daily granularity, no rate limits, full historical archive back to 2015, and a clean REST API that doesn't require auth.

The Wikimedia REST API exposes per-page daily view counts at `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/{title}/daily/{start}/{end}`. The feature signal is: **abnormal attention** — current page-view count standardized against a trailing baseline. Spikes correlate with news, retail discussion, and (often) prices that have already moved.

This is the cheapest of the free-source tickets to ship: one endpoint, no auth, no rate-limit gymnastics, full backfill available, and the feature math is the same z-score pattern we already use for VIX percentile and Stocktwits volume.

## Acceptance Criteria

- [ ] New `WikipediaPageviews` table + Alembic migration. Columns: `ticker`, `view_date`, `page_views`, `wikipedia_title` (the resolved page title used for the lookup, for diagnostics). Composite unique index on `(ticker, view_date)`.
- [ ] Ticker → Wikipedia page mapping: a hand-curated JSON config at `backend/src/config/wikipedia_titles.json` mapping each watchlist ticker to its canonical English Wikipedia title (e.g., `"AAPL": "Apple_Inc."`, `"BRK-B": "Berkshire_Hathaway"`). For the 159-ticker watchlist this is a one-time effort; bake it in rather than auto-resolving (auto-resolution via Wikipedia search API is unreliable for ambiguous tickers like `F` → "Ford Motor Company" vs the letter F's disambiguation page).
- [ ] `WikipediaPageviewFetcher` in `backend/src/pipeline/wikipedia_fetcher.py` pulling from the per-article daily endpoint. User-Agent header is required by Wikimedia's policy: `User-Agent: stock-analysis andymihalik@gmail.com` (same convention as SEC).
- [ ] `backfill_wikipedia_pageviews.py` script: idempotent, accepts `--lookback-days` (default 730), fetches per ticker in date-chunked batches (the API allows up to ~50 days per call). Stub row with `page_views=0` when a date is missing from the response (Wikimedia returns gaps for low-traffic days, not zero).
- [ ] New `backend/src/features/wikipedia.py` with `WIKIPEDIA_FEATURE_COLS`. Minimum feature set:
  - `wiki_views_zscore_30d` (today's views standardized vs trailing 30d, excluding today)
  - `wiki_views_zscore_180d` (same on a 180d baseline — slower-moving regime)
  - `wiki_views_change_7d` (this week's mean − prior week's mean, normalized by prior week's mean)
  - `wiki_views_spike` (binary: today's views > 3× trailing 30d mean)
  - `wiki_views_log` (log1p of today's raw views — captures absolute popularity baseline)
- [ ] Sane defaults for missing data (all zeros for zscores/changes/spike, log=0).
- [ ] Scheduler: new `job_fetch_wikipedia_pageviews` runs daily Mon-Sun at 5:30 ET (Wikipedia data lags ~24h, and runs every day not just weekdays — non-trading days still produce attention data we want for Monday's prediction).
- [ ] Wire into `models/directional.py`, `pipeline/scheduler.py` recommendation feature dict, `scripts/diagnose_recs_v2.py`.
- [ ] Trainer integration: append to whichever v-bump bundles multiple new feature groups; safe to retrain on its own since backfill is dense (~730 rows × 159 tickers = ~115k rows on first backfill).
- [ ] Tests: `test_wikipedia_fetcher.py` (mocked HTTP for date-chunked backfill, gap handling, retry on transient errors), `test_wikipedia_features.py` (zscore on quiet vs spiking ticker, change-7d edge cases, spike threshold, default path, log scaling).

## Files to Create/Modify

- `backend/alembic/versions/<new>_add_wikipedia_pageviews.py` (new)
- `backend/src/db/models.py` (add `WikipediaPageviews`)
- `backend/src/config/wikipedia_titles.json` (new — 159-ticker → Wikipedia title map)
- `backend/src/pipeline/wikipedia_fetcher.py` (new)
- `backend/src/features/wikipedia.py` (new)
- `backend/src/models/directional.py` (extend)
- `backend/src/pipeline/scheduler.py` (new fetch job + inference feature dict)
- `backend/scripts/backfill_wikipedia_pageviews.py` (new)
- `backend/scripts/diagnose_recs_v2.py` (extend)
- `backend/tests/test_wikipedia_fetcher.py` (new)
- `backend/tests/test_wikipedia_features.py` (new)

## Notes

- **Title-mapping effort**: there's no shortcut here. Ambiguous tickers (`F`, `M`, `T`, `C`, `O`, `A`) and corporate-name aliases (`BRK-B` → "Berkshire_Hathaway", not "BRK-B"; `META` → "Meta_Platforms"; `GOOG`/`GOOGL` → both map to "Alphabet_Inc.") require human review. Generate a first-pass mapping using the company `longName` field already in the `Stock` table from yfinance metadata, then sanity-check by hand for the watchlist. Document non-obvious mappings in the JSON file.
- **`GOOG` vs `GOOGL`**: both share-classes map to the same Wikipedia page. That's correct — attention is for the company, not the share class. Don't deduplicate; both tickers should pull the same series with their own table rows.
- **`BRK-B` URL encoding**: hyphens in tickers are fine in our DB but the Wikipedia API doesn't see them — the title field is what matters. URL-encode page titles (spaces become underscores; non-ASCII gets percent-encoded — relevant for international names if the watchlist ever expands).
- **Density**: ~730 rows/ticker on backfill at 2 years × daily, dense enough to give the v-trainer real variance from day 1. This is the opposite of the short-interest density problem.
- **Weekend data**: Wikipedia returns views every day. Our trading data is weekdays only. The feature module should use the most-recent available `view_date` ≤ the trading day's prediction-as-of date (forward-fill weekends into Monday). Use `pd.merge_asof` like the macro / sector feature modules already do.
- **Lag awareness**: Wikimedia's pipeline finalizes daily counts ~24h after the day ends. The 5:30 ET cron pulls yesterday's finalized count; today's partial count is unavailable until tomorrow. The `wiki_views_zscore` features are therefore one-day-lagged by construction — fine for the 5-day horizon.
- **No special promotion logic**: dense backfill means this can be promoted on its own merits via the standard AUC ≥ v3 + 0.005 gate. Bundle with insider/8-K if convenient, but it's not blocked on accumulation like Stocktwits.
- **Attention → return sign is not stable in the literature**: high attention sometimes precedes positive returns (information arrival), sometimes negative (mean-reverting overreaction). Don't preprocess sign; let XGBoost discover the regime via interactions with macro and sector features.
