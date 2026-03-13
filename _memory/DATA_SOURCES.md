# Data Sources

## Price Data

### yfinance (primary)
- **Endpoint**: Python library, no API key needed
- **Rate limits**: Unofficial, ~2000 requests/hour generally safe
- **Gotchas**: Can break without notice (scrapes Yahoo Finance). Cache aggressively.
- **Data**: OHLCV, options chains, fundamentals, dividends, splits

### Alpha Vantage (fallback)
- **Endpoint**: `https://www.alphavantage.co/query`
- **API key**: Required (free tier)
- **Rate limits**: 5 calls/minute, 500/day on free tier
- **Data**: OHLCV, technical indicators, fundamentals

## Macro Data

### FRED API
- **Endpoint**: `https://api.stlouisfed.org/fred/`
- **API key**: Required (free)
- **Rate limits**: 120 requests/minute
- **Key series**: VIXCLS (VIX), DGS10 (10Y yield), DGS2 (2Y yield), FEDFUNDS

## Sentiment Sources

### Finviz
- **Library**: `finvizfinance`
- **Rate limits**: Be gentle, no official API
- **Data**: News headlines per ticker, screener data

### NewsAPI
- **Endpoint**: `https://newsapi.org/v2/everything`
- **API key**: Required (free tier)
- **Rate limits**: 100 requests/day on free tier
- **Data**: News articles with title, description, source

### Reddit (PRAW)
- **Endpoint**: Reddit API via PRAW library
- **API key**: Required (free, create app at reddit.com/prefs/apps)
- **Rate limits**: 60 requests/minute
- **Subreddits**: r/wallstreetbets, r/stocks, r/options, r/investing

## Filings

### SEC EDGAR
- **Endpoint**: `https://efts.sec.gov/LATEST/`
- **Rate limits**: 10 requests/second, must include User-Agent header
- **Data**: 10-K, 10-Q, 8-K filings
