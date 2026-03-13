# P1-003: Add Reddit sentiment source

**Status**: todo
**Phase**: 1
**Dependencies**: P1-002
**Estimated scope**: small

## Description
Add Reddit as a sentiment data source using PRAW. Fetch recent posts from r/wallstreetbets, r/stocks, r/options mentioning tracked tickers.

## Acceptance Criteria
- [ ] `RedditFetcher` class in `services/headline_fetcher.py` (or separate file)
- [ ] Searches relevant subreddits for ticker mentions
- [ ] Extracts post title + top comments as sentiment text
- [ ] Integrates with existing `SentimentAnalyzer` pipeline
- [ ] Reddit API credentials in config (env vars)
- [ ] Handles rate limits gracefully

## Files to Create/Modify
- `backend/src/services/headline_fetcher.py`
- `backend/src/config.py` (add Reddit credentials)
