# Architecture

## System Overview

```
[Data Sources]          [Homelab GPU VM: 10.0.0.47]          [Dev Machine]
 yfinance                Ollama (sentiment LLM) :11434        Next.js UI :3100
 FRED, NewsAPI           FastAPI backend :8000
 Finviz, Reddit          ML models (PyTorch/XGBoost)
 SEC EDGAR               PostgreSQL :5432
```

## Tech Stack

- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy / Alembic / APScheduler
- **ML**: XGBoost (directional classifier), PyTorch LSTM (volatility predictor)
- **Sentiment**: Ollama at 10.0.0.47:11434 (structured JSON prompts)
- **Database**: PostgreSQL (prod on homelab Docker), SQLite (local dev)
- **Frontend**: Next.js 15 / TypeScript / Tailwind / lightweight-charts
- **Deployment**: Docker Compose on homelab GPU VM

## Key Integration Points

- **Backend → Ollama**: Docker internal DNS (`http://ollama:11434`) when both on homelab; `http://10.0.0.47:11434` from dev machine
- **Frontend → Backend**: `http://10.0.0.47:8000/api/...` (homelab) or `http://localhost:8000` (local dev)
- **Scheduler**: APScheduler inside FastAPI process, cron triggers during market hours (ET)

## Database Schema (Core Tables)

- `stocks` — ticker, name, sector, exchange
- `price_history` — ticker FK, date, OHLCV
- `technical_indicators` — ticker FK, date, RSI, MACD, BB, SMA, volume_zscore
- `sentiment_scores` — ticker FK, date, source, score, confidence, reasoning
- `model_predictions` — ticker FK, date, model_name, signal, confidence
- `recommendations` — ticker FK, date, strategy (short|options), score, position_size, stop_loss, max_loss

## Budget Constraint

Every recommendation enforces $5,000 max buy-in:
- Shorts: margin requirement ≤ $5,000
- Options: premium × 100 × contracts ≤ $5,000
- All recommendations include stop-loss and max loss in dollars
