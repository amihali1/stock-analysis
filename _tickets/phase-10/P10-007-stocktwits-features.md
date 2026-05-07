# P10-007: Add Stocktwits sentiment + message-volume features to directional model

**Status**: todo
**Phase**: 10
**Dependencies**: none (independent of the EDGAR P10-005/006 chain — can ship in parallel)
**Estimated scope**: medium (5-7 files)

## Description

Stocktwits is a social platform structured around tickers (every message is tagged with a `$TICKER` cashtag, and the platform exposes per-ticker public APIs). Each message also has an optional explicit user-tagged sentiment label (`Bullish` / `Bearish`), which means we get a free pre-labeled sentiment stream without having to run Ollama on retail noise.

The Reddit fetcher we ripped out in PR #17 silently failed because PRAW's text search returned 0 posts on every ticker — a known issue with how Reddit indexes cashtags. Stocktwits doesn't have this problem: cashtags are first-class identifiers, the API is keyed on them, and message volume per ticker is high enough on the watchlist names that we'll get real signal even on a single fetch.

Two distinct signals here: **message volume** (proxy for retail attention; spikes often precede or accompany price moves) and **net sentiment** (bull-bear ratio of explicitly-tagged messages). Both are short-window features — Stocktwits message half-life is ~hours, so anything older than 7 days is already old news for a 5-day directional prediction.

## Acceptance Criteria

- [ ] New `StocktwitsSnapshot` table + Alembic migration. Columns: `ticker`, `snapshot_date` (the day the snapshot was taken), `message_count_24h`, `bullish_count_24h`, `bearish_count_24h`, `total_followers` (per-ticker stream follower count, slow-moving ambient signal), `fetched_at`. Unique index on `(ticker, snapshot_date)`.
- [ ] `StocktwitsFetcher` in `backend/src/pipeline/stocktwits_fetcher.py` pulling from the public symbol-stream endpoint `https://api.stocktwits.com/api/2/streams/symbol/{TICKER}.json` (returns last ~30 messages with timestamps and sentiment labels). Rate limit: 200 req/hour per IP — fine for daily fetch across 159 tickers (~1 req/sec sustained). Use `httpx` with an exponential-backoff retry on 429.
- [ ] Aggregation: from the raw message list, compute `message_count_24h` (filter to messages in last 24h), `bullish_count_24h` (subset where `entities.sentiment.basic == "Bullish"`), `bearish_count_24h` (same for `"Bearish"`). Messages without explicit sentiment labels count toward `message_count_24h` only.
- [ ] `total_followers` from the `symbol.watchlist_count` field on the response root.
- [ ] No backfill script — Stocktwits doesn't expose historical messages in the public API (only the latest ~30 per stream). The table accumulates from day 0; features will be sparse-default for the first ~30 days.
- [ ] New `backend/src/features/stocktwits.py` with `STOCKTWITS_FEATURE_COLS`. Minimum feature set:
  - `stocktwits_message_zscore_30d` (today's count standardized vs trailing 30d)
  - `stocktwits_bull_bear_ratio_7d` (sum of bullish / max(1, sum of bullish + bearish), 7d window)
  - `stocktwits_bull_bear_ratio_change_7d` (this week's ratio − prior week's ratio)
  - `stocktwits_volume_spike` (binary: today's count > 2× trailing 30d mean)
  - `stocktwits_follower_count_log` (log1p of `total_followers` — slow-moving popularity baseline)
  - `has_stocktwits_data` (binary: at least one snapshot exists for this ticker)
- [ ] Sane defaults for tickers with no Stocktwits coverage (zscore=0, ratio=0.5, change=0, spike=0, follower_log=0, has_data=0).
- [ ] Scheduler: new `job_fetch_stocktwits` runs daily Mon-Fri at 7:05 ET (after sentiment job, before recommendations). Single job since there's no backfill — just a daily snapshot.
- [ ] Wire into `models/directional.py`, `pipeline/scheduler.py` recommendation feature dict, `scripts/diagnose_recs_v2.py`.
- [ ] Trainer integration: this should land in the same v-bump as the next other free-source ticket rather than its own retrain run, since features won't have meaningful variance until ~30 days of accumulation. Note in the trainer that the feature group is "ramping up."
- [ ] Tests: `test_stocktwits_fetcher.py` (mocked HTTP, sentiment label extraction, 24h window filter, 429 retry), `test_stocktwits_features.py` (zscore on quiet vs noisy ticker, ratio edge cases — all bullish, all bearish, no labeled, mixed — volume spike threshold, default path for new ticker).

## Files to Create/Modify

- `backend/alembic/versions/<new>_add_stocktwits_snapshots.py` (new)
- `backend/src/db/models.py` (add `StocktwitsSnapshot`)
- `backend/src/pipeline/stocktwits_fetcher.py` (new)
- `backend/src/features/stocktwits.py` (new)
- `backend/src/models/directional.py` (extend feature cols + defaults — but expect 0 importance until accumulation)
- `backend/src/pipeline/scheduler.py` (new fetch job + inference feature dict)
- `backend/scripts/diagnose_recs_v2.py` (extend)
- `backend/tests/test_stocktwits_fetcher.py` (new)
- `backend/tests/test_stocktwits_features.py` (new)

## Notes

- **API stability**: Stocktwits' public API is undocumented and has historically had silent breaking changes (param renames, response shape tweaks). Wrap the fetcher in a permissive try/except that logs the raw response on parse failure — don't let one ticker's malformed response kill the whole job. Pin the parsing to known fields (`messages[].entities.sentiment.basic`, `messages[].created_at`) and degrade gracefully if missing.
- **Sentiment label coverage**: only ~25-40% of Stocktwits messages carry an explicit Bullish/Bearish tag (the rest are unlabeled chatter). Don't apply Ollama or NLP to the unlabeled messages — the labeled subset is the high-quality signal; treating raw text as sentiment is what Reddit's failure showed doesn't work.
- **Coverage gaps**: Stocktwits coverage is concentrated in the most-traded names (mega-caps, popular tech, meme stocks). Some watchlist tickers (e.g., utilities, smaller industrials like `EMR`, `MMM`) may have 0-2 messages/day — features for those tickers will be near-default. `has_stocktwits_data` lets the model identify the regime.
- **Volume zscore caveat**: same autocorrelation issue as 8-K filing zscore. If sign instability shows up in feature importance, switch to a rolling 30d baseline that excludes the current day.
- **Follower count**: `watchlist_count` on the symbol metadata is the platform's ambient interest measure. Slow-moving (changes by tens per day on big names), but a useful absolute-popularity feature alongside the rate-based ones. Take `log1p` because the distribution is heavily right-skewed.
- **Don't ramp expectations**: Stocktwits is retail-sentiment, not institutional-flow. The signal will be noisier than insider txns or 8-K events. Land it because it's free, daily, and complements the rest of the feature set — but don't expect a big AUC bump on its own. Real value is likely in interaction effects with the cluster-buy / earnings-proximity features.
