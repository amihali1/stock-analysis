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

### Planned
- **Phase 3** — REST API, APScheduler for market-hours automation, Docker deployment
- **Phase 4** — Next.js dashboard with lightweight-charts, analysis pages, paper trading
- **Phase 5** — Backtesting engine, model retraining, alerts, options spreads

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
| `GET` | `/api/health` | Health check (DB + Ollama status) |
| `GET` | `/api/recommendations` | Top recommendations (filterable by strategy) |
| `GET` | `/api/analysis/{ticker}` | Full analysis for a single ticker |

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
      models.py             # SQLAlchemy models (6 core tables)
      session.py            # Database session management
    pipeline/
      data_fetcher.py       # yfinance OHLCV ingestion
      feature_eng.py        # Technical indicator computation
      sentiment.py          # Headline → Ollama → sentiment scores
      runner.py             # Pipeline orchestrator
    services/
      ollama_client.py      # Async Ollama HTTP client with retry
      headline_fetcher.py   # Finviz, NewsAPI, Reddit fetchers
    models/
      directional.py        # XGBoost drop classifier + dataset builder
      volatility.py         # LSTM volatility predictor
      ensemble.py           # Weighted signal combiner
      position_sizer.py     # $5k-constrained position sizing
  alembic/                  # Database migrations
  tests/                    # Integration & unit tests
  trained_models/           # Serialized model artifacts
frontend/                   # Next.js dashboard (Phase 4)
```

## Design Constraints

- **$5,000 max buy-in** per recommendation (shorts: margin requirement, options: premium)
- **Time-based train/test splits only** for financial data (never random)
- **Every recommendation** includes stop-loss and max loss in dollars
- **Cache API data** aggressively in the database
- **Log raw LLM responses** for debugging and prompt iteration
