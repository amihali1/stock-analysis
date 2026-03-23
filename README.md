# Stock Analysis Platform

A locally-hosted stock analysis platform combining ML-powered quantitative analysis with LLM-driven sentiment analysis. Generates short and options recommendations with a $5,000 max buy-in constraint per position.

## Architecture

```
[Data Sources]              [Homelab GPU VM: 10.0.0.47]         [Dev Machine]
 yfinance (OHLCV)            Ollama (sentiment LLM) :11434       Next.js UI :3100
 Finviz, NewsAPI (headlines)  FastAPI backend         :8000
 Reddit/PRAW (social)         ML models (XGBoost/LSTM)
                              PostgreSQL              :5432
```

## Features

### Data Pipeline (Phase 0)
- **Price ingestion** — Fetches daily OHLCV from yfinance for 50+ tickers across sectors, upserts to database with deduplication
- **Technical indicators** — Computes RSI (14), MACD (12/26/9), Bollinger Bands (20/2), SMA 50/200 crossovers, volume z-score — all from scratch with pandas/numpy
- **Pipeline runner** — Orchestrates fetch → feature computation in a single call

### Sentiment Analysis (Phase 1)
- **Multi-source headlines** — Finviz, NewsAPI, and Reddit (r/wallstreetbets, r/stocks, r/options) via PRAW
- **LLM scoring** — Structured prompts to Ollama returning calibrated sentiment (-1.0 to 1.0) with confidence and reasoning
- **Robust parsing** — Pydantic validation with regex fallback for non-JSON LLM responses
- **Audit trail** — Raw LLM responses stored for debugging

### ML Models (Phase 2)
- **Directional classifier** — XGBoost predicting >3% drops in 5 trading days, with walk-forward validation and 17 technical features
- **Volatility predictor** — PyTorch LSTM (2-layer, 64 hidden) predicting 5-day realized volatility from 60-day lookback sequences
- **Ensemble scorer** — Weighted combination of directional, volatility, and sentiment signals into a single bearish score
- **Position sizer** — Margin-aware short sizing and options contract sizing, both constrained to $5,000 max buy-in with stop-loss and max-loss calculations

### API & Automation (Phase 3)
- **REST API** — Full CRUD endpoints with Pydantic response models: recommendations (filterable by strategy), per-ticker analysis (prices, indicators, sentiments), ticker listing
- **Scheduler** — APScheduler with 4 daily cron jobs (Mon-Fri ET): price fetch (6:00), indicators (6:30), sentiment (7:00), recommendations (7:30) + monthly model retraining
- **Docker deployment** — Dockerfile with auto-migration, docker-compose with PostgreSQL + GPU passthrough, deploy script for homelab

### Frontend Dashboard (Phase 4)
- **Dashboard** — Sortable recommendations table with strategy filter tabs (All/Shorts/Options), color-coded scores, auto-refresh
- **Stock charts** — Candlestick + volume charts via lightweight-charts with SMA 50/200 and Bollinger Band overlays
- **Analysis page** — Per-ticker deep dive: chart, signal breakdown, sentiment gauge with headline history, position details, technical indicators
- **Paper trading** — Take trades from recommendations, track open positions with unrealized P&L, summary stats (win rate, total P&L)

### Backtesting & Retraining (Phase 5)
- **Backtesting engine** — Replay historical signals with configurable strategies (short/options/combined), position limits, and hold periods
- **Portfolio metrics** — Sharpe ratio, win rate, max drawdown, profit factor, daily equity curve, stop-loss/target hit rates
- **Strategy comparison** — Side-by-side backtest of short-only, options-only, and combined strategies
- **Walk-forward retraining** — Periodic model retraining during backtests for realistic evaluation
- **Automated retraining** — Monthly champion/challenger comparison for both XGBoost and LSTM models, auto-deploy only if metrics improve

### Alerts (Phase 5)
- **Webhook notifications** — Discord and Telegram support for stop-loss hits, target hits, and high-conviction signals
- **Alert history** — Full audit trail in database with acknowledge workflow
- **Configurable preferences** — Per-channel settings for alert types and score thresholds

### Options Spreads (Phase 5)
- **Spread builder** — Signal-driven strategy selection: bear call spreads, bear put spreads, iron condors
- **Black-Scholes pricing** — Simplified premium and delta estimation for position sizing
- **Greeks display** — Delta, theta, vega exposure on all spread positions
- **P&L diagrams** — Canvas-based profit/loss visualization with breakeven markers
- **Earnings awareness** — Flags spreads with expirations crossing earnings dates

### Planned (Phase 6)
- **Watchlist management** — UI to add/remove tickers instead of hardcoded config
- **Backtesting UI** — Configure, run, and visualize backtests with equity curves and trade tables
- **Alerts UI** — Configure webhook channels and view/acknowledge alert history
- **Real options chain data** — Pull live premiums, IV, and Greeks from yfinance/CBOE
- **Portfolio risk management** — Correlation-aware limits, sector exposure tracking, aggregate risk metrics
- **Authentication** — JWT-based login to protect dashboard and API
- **E2E deployment validation** — Full pipeline smoke test on homelab

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / FastAPI / SQLAlchemy / Alembic |
| ML | XGBoost, PyTorch (LSTM) |
| Sentiment | Ollama (local LLM) |
| Database | PostgreSQL (prod), SQLite (dev) |
| Frontend | Next.js 15 / TypeScript / Tailwind / lightweight-charts |
| Deployment | Docker Compose on homelab GPU VM |

## Quickstart

### Local Development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Initialize database (SQLite for dev)
alembic upgrade head

# Run the data pipeline
python -m src.pipeline.runner

# Start the API server
uvicorn src.main:app --reload --port 8000
```

### Docker (Production)

```bash
cd backend
docker compose up -d
```

This starts the FastAPI backend and PostgreSQL. Connect Ollama separately or add it to the compose network.

## Configuration

Set via environment variables or a `.env` file in `backend/`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./stock_analysis.db` | Database connection string |
| `OLLAMA_BASE_URL` | `http://10.0.0.47:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `mistral` | LLM model for sentiment analysis |
| `FRED_API_KEY` | — | FRED API key (macro data) |
| `NEWSAPI_KEY` | — | NewsAPI key (headlines) |
| `REDDIT_CLIENT_ID` | — | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | — | Reddit app client secret |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check (DB + Ollama + scheduler status) |
| `GET` | `/api/recommendations?strategy=short\|options&limit=10` | Top scored recommendations |
| `GET` | `/api/analysis/{ticker}?days=90` | Full analysis: prices, indicators, sentiments, recommendations |
| `GET` | `/api/tickers` | All tracked tickers with latest price data |
| `POST` | `/api/paper-trades` | Open a paper trade |
| `POST` | `/api/paper-trades/{id}/close` | Close a paper trade with exit price |
| `GET` | `/api/paper-trades?status=open\|closed` | List paper trades with summary stats |
| `POST` | `/api/backtest` | Run a backtest with configurable parameters |
| `POST` | `/api/backtest/compare` | Compare all three strategies side-by-side |
| `GET` | `/api/alerts` | Alert history with optional acknowledged filter |
| `POST` | `/api/alerts/{id}/acknowledge` | Acknowledge an alert |
| `GET` | `/api/alert-settings` | Get alert channel settings |
| `POST` | `/api/alert-settings` | Create/update alert channel settings |
| `GET` | `/docs` | Interactive OpenAPI documentation |

## Running Tests

```bash
cd backend
python -m pytest tests/ -v
```

## Project Structure

```
backend/
  src/
    main.py                 # FastAPI app entry point
    config.py               # Settings (env vars, watchlist)
    api/routes/             # REST endpoints
    db/
      models.py             # SQLAlchemy models (9 tables)
      session.py            # Database session management
    pipeline/
      data_fetcher.py       # yfinance OHLCV ingestion
      feature_eng.py        # Technical indicator computation
      sentiment.py          # Headline → Ollama → sentiment scores
      runner.py             # Pipeline orchestrator
      scheduler.py          # APScheduler cron jobs
    services/
      ollama_client.py      # Async Ollama HTTP client with retry
      headline_fetcher.py   # Finviz, NewsAPI, Reddit fetchers
      alerting.py           # Discord/Telegram webhook alerts
    models/
      directional.py        # XGBoost drop classifier + dataset builder
      volatility.py         # LSTM volatility predictor
      ensemble.py           # Weighted signal combiner
      position_sizer.py     # $5k-constrained position sizing
      backtester.py         # Historical strategy replay engine
      retrainer.py          # Champion/challenger model retraining
      options_strategies.py # Spread strategies (bear call, bear put, iron condor)
  alembic/                  # Database migrations
  tests/                    # Integration & unit tests
  trained_models/           # Serialized model artifacts
frontend/
  src/
    app/
      page.tsx              # Dashboard with recommendations table
      analysis/[ticker]/    # Per-ticker analysis deep dive
      paper-trades/         # Paper trading log
    components/
      StockChart.tsx        # Candlestick + volume + overlays
      SignalBreakdown.tsx   # Ensemble signal bars
      SentimentGauge.tsx    # Sentiment score + headlines
      PositionDetail.tsx    # Position sizing details
      PLDiagram.tsx         # Options spread P&L chart
      GreeksDisplay.tsx     # Options Greeks display
    lib/
      api.ts                # Typed API client
      types.ts              # TypeScript interfaces
```

## Design Constraints

- **$5,000 max buy-in** per recommendation (shorts: margin requirement, options: premium)
- **Time-based train/test splits only** for financial data (never random)
- **Every recommendation** includes stop-loss and max loss in dollars
- **Cache API data** aggressively in the database
- **Log raw LLM responses** for debugging and prompt iteration
