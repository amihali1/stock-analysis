# Session Log

Chronological record of what each agent session accomplished. Read the latest entries to understand current project state.

---

## 2026-03-13 — Session 1: Project Scaffold

**Agent**: Initial scaffolding
**What was done**:
- Created project directory structure
- Initialized git repository
- Set up agent memory store in `_memory/`
- Created ticket system in `_tickets/` with all Phase 0-5 tickets
- Wrote core backend files: pyproject.toml, FastAPI entry, DB models, config, Dockerfile, docker-compose.yml
- Wrote Alembic migration setup
- Wrote frontend package.json and basic Next.js layout
- Created .gitignore, CLAUDE.md, README placeholder

**Current state**: Phase 0 tickets ready for pickup. No code has been run yet — dependencies not installed.
**Next steps**: Pick up Phase 0 tickets starting with P0-001 (install deps, verify DB connection)

---

## 2026-03-13 — Session 2: P0-001 Project Dependencies

**Agent**: Claude Opus 4.6
**Ticket**: P0-001
**What was done**:
- Fixed `pyproject.toml` build-backend (`setuptools.backends._legacy` → `setuptools.build_meta`)
- Created venv at `backend/.venv`
- Installed all deps (including dev extras) — all succeed
- Verified imports: `fastapi, sqlalchemy, yfinance, xgboost, torch` all OK
- Verified `uvicorn src.main:app` starts and responds
- Fixed FastAPI deprecation warning: `regex=` → `pattern=` in recommendations route
- Marked P0-001 as done

**Current state**: Backend venv working, all deps installed. PyTorch is CPU-only on Windows (expected).
**Next steps**: P0-002 (DB models/migrations), P0-003 (data fetcher) — both have P0-001 as only dependency

---

## 2026-03-13 — Session 2 (cont): P0-002 through P0-005

**Agent**: Claude Opus 4.6
**Tickets**: P0-002, P0-003, P0-004, P0-005

**What was done**:

**P0-002 (DB models/migrations)**:
- Generated Alembic initial migration for all 6 tables
- Verified tables create in SQLite, tested insert/query with relationships on all tables

**P0-003 (Data fetcher)**:
- Created `src/pipeline/data_fetcher.py` with `DataFetcher` class
- Fetches OHLCV from yfinance, upserts to `price_history` (no duplicates)
- Auto-creates Stock rows and populates metadata from yfinance info
- Tested: 502 rows/ticker for 2y, idempotent re-runs return 0

**P0-004 (Feature engineering)**:
- Created `src/pipeline/feature_eng.py` with `FeatureEngineer` class
- Computes RSI(14), MACD(12,26,9), Bollinger Bands(20,2), SMA 50/200, SMA crossover, volume z-score(20)
- All indicators implemented from scratch with pandas/numpy (no TA-Lib)

**P0-005 (Pipeline integration)**:
- Created `src/pipeline/runner.py` — orchestrates fetch → features in single pipeline
- Created `tests/test_pipeline.py` — 4 tests, all passing
- End-to-end: 5 tickers processed in ~3.3s

**Fixes**:
- Fixed `pyproject.toml` build-backend (setuptools.backends._legacy → setuptools.build_meta)
- Fixed FastAPI deprecation: `regex=` → `pattern=` in recommendations route
- Fixed pandas FutureWarning in SMA crossover (shift fill_value)

**Current state**: Phase 0 complete. All pipeline infrastructure working.
**Next steps**: Phase 1 (P1-001 Ollama client, P1-002 sentiment pipeline, P1-003 Reddit sentiment)

---

## 2026-03-13 — Session 2 (cont): Phase 1 — Sentiment Analysis

**Agent**: Claude Opus 4.6
**Tickets**: P1-001, P1-002, P1-003

**What was done**:

**P1-001 (Ollama client)**:
- Created `services/ollama_client.py` — async httpx client with retry (3 attempts, exponential backoff)
- `generate()`, `generate_json()`, `is_available()` methods
- JSON extraction fallback for markdown code blocks / embedded JSON
- 9 unit tests with mocked HTTP, all passing

**P1-002 (Sentiment pipeline)**:
- Created `services/headline_fetcher.py` — FinvizFetcher, NewsApiFetcher classes
- Created `pipeline/sentiment.py` — SentimentAnalyzer with structured prompts
- Pydantic validation of Ollama responses + regex fallback
- Confidence-weighted composite scoring
- Stores raw LLM responses in `sentiment_scores` table for debugging
- 7 tests including full integration test with mocked Ollama

**P1-003 (Reddit sentiment)**:
- Added RedditFetcher to `headline_fetcher.py` — searches r/wallstreetbets, r/stocks, r/options
- Extracts post titles + top comments
- Integrated into SentimentAnalyzer fetcher chain
- Gracefully skips when Reddit credentials not configured

**Fixes**:
- Added `from __future__ import annotations` to all new modules (Python 3.10 `X | None` syntax)

**Current state**: Phase 0 + Phase 1 complete. 20 tests passing.
**Next steps**: Phase 2 (ML models: directional XGBoost, volatility LSTM, ensemble)

---

## 2026-03-13 — Session 2 (cont): Phase 2 — ML Models

**Agent**: Claude Opus 4.6
**Tickets**: P2-001, P2-002, P2-003

**What was done**:

**P2-001 (Directional XGBoost)**:
- Created `models/directional.py` with `DirectionalModel` class
- `build_dataset()` joins prices + indicators, creates binary label (drop >3% in 5 days)
- 17 features including lagged returns, price/SMA ratios, 20d realized vol
- Walk-forward validation (3 folds), time-based splits
- Test: acc=0.633, AUC=0.549 — beats random baseline
- Model serialized to `trained_models/directional_xgb_v1.pkl`

**P2-002 (Volatility LSTM)**:
- Created `models/volatility.py` with `VolatilityLSTM` (2-layer, 64 hidden)
- 60-day lookback sequences, 4 features (return, volume, RSI, hist vol)
- Predicts 5-day annualized realized volatility
- Test: MAE=0.1085, Corr=0.3264
- Early stopping, gradient clipping, LR scheduling
- Model serialized to `trained_models/volatility_lstm_v1.pt`

**P2-003 (Ensemble + Position Sizer)**:
- Created `models/ensemble.py` — weighted combination of directional/vol/sentiment signals
- Created `models/position_sizer.py` — short and options sizing with $5,000 constraint
- Shorts: margin-aware share calculation, stop-loss, target
- Options: contract sizing, premium budget, strike calculation
- 14 tests covering edge cases (expensive stocks, zero price, confidence scaling)

**Current state**: Phases 0-2 complete. 34 tests passing.
**Next steps**: Phase 3 (API routes, scheduler, Docker deployment)

---

## 2026-03-13 — Session 2 (cont): Phase 3 — API & Deployment

**Agent**: Claude Opus 4.6
**Tickets**: P3-001, P3-002, P3-003

**What was done**:

**P3-001 (API routes)**:
- Implemented `GET /api/recommendations` — queries DB, sorted by score, filterable by strategy
- Implemented `GET /api/analysis/{ticker}` — full analysis: prices, indicators, sentiments, recommendations
- Added `GET /api/tickers` — all tracked tickers with latest price data
- Created `api/schemas.py` with Pydantic response models for all endpoints
- Health endpoint now returns scheduler job status

**P3-002 (Scheduler)**:
- Created `pipeline/scheduler.py` with APScheduler AsyncIOScheduler
- 4 cron jobs (Mon-Fri ET): 6:00 fetch prices, 6:30 indicators, 7:00 sentiment, 7:30 recommendations
- Each job logs success/failure and records last run time for health endpoint
- `job_generate_recommendations` runs full ensemble → position sizer → DB pipeline
- Scheduler starts/stops with FastAPI lifespan

**P3-003 (Docker deployment)**:
- Updated Dockerfile: copies trained_models, auto-runs alembic migrations on startup
- Updated docker-compose: env_file support, GPU passthrough enabled
- Fixed deploy.sh to include trained model artifacts in rsync

**Current state**: Phases 0-3 complete. 34 tests passing. Backend fully functional.
**Next steps**: Phase 4 (Next.js frontend: scaffold, dashboard, charts, analysis page, paper trading)

---

## 2026-03-13 — Session 2 (cont): Phase 4 — Frontend

**Agent**: Claude Opus 4.6
**Tickets**: P4-001, P4-002, P4-003, P4-004, P4-005

**What was done**:

**P4-001 (Frontend scaffold)**:
- Set up Next.js 15 with TypeScript, Tailwind v4, PostCSS
- Created typed API client (`lib/api.ts`) and type definitions (`lib/types.ts`) matching backend schemas
- Dark-themed layout with navigation (Dashboard, Shorts, Options, Paper Trades)

**P4-002 (Dashboard)**:
- Sortable recommendations table with strategy filter tabs (All/Shorts/Options)
- Score color coding, position sizing, max loss display
- Click-through to per-ticker analysis, auto-refresh every 5 minutes

**P4-003 (Stock charts)**:
- `StockChart` component using lightweight-charts (TradingView)
- Candlestick chart with volume histogram
- SMA 50/200 overlays, Bollinger Band overlays, dark theme

**P4-004 (Analysis page)**:
- `/analysis/[ticker]` route with full signal breakdown
- SignalBreakdown, SentimentGauge, PositionDetail components
- Latest technical indicators summary grid

**P4-005 (Paper trading)**:
- Added `PaperTrade` model to DB + Alembic migration
- API: POST /api/paper-trades, POST /api/paper-trades/{id}/close, GET /api/paper-trades
- Frontend page with summary stats (win rate, total P&L) and trade table
- Open trades show current price and unrealized P&L

**Current state**: Phases 0-4 complete. 34 backend tests passing. Frontend builds clean.
**Next steps**: Phase 5 (backtesting, model retraining, alerts, options spreads)

---

## 2026-03-13 — Session 3: Phase 5 — Backtesting, Retraining, Alerts, Spreads

**Agent**: Claude Opus 4.6
**Tickets**: P5-001, P5-002, P5-003, P5-004

**What was done**:

**P5-001 (Backtesting engine)**:
- Created `models/backtester.py` — `Backtester` class replaying signals on historical data
- Supports short, options, and combined strategies
- Walk-forward retraining with configurable interval
- Metrics: Sharpe ratio, win rate, max drawdown, profit factor, stop-loss/target hit rates
- Daily equity curve tracking with unrealized P&L
- `compare_strategies()` for side-by-side strategy comparison
- API: POST /api/backtest, POST /api/backtest/compare
- 14 tests covering all execution paths

**P5-002 (Model retraining)**:
- Created `models/retrainer.py` — champion/challenger comparison for both models
- Directional: compares AUC-ROC, deploys only if improved beyond threshold
- Volatility: compares MAE (lower is better), same threshold logic
- Backs up current champion before deploying challenger
- Metrics persisted to `trained_models/{model}_metrics.json`
- Scheduler job: first Sunday of each month at 2:00 AM ET

**P5-003 (Alerting system)**:
- Created `services/alerting.py` — `AlertService` with Discord and Telegram webhook support
- Alert types: stop_loss, target_hit, high_conviction, position_closed
- `check_paper_trade_alerts()` monitors open trades for stop/target hits
- `check_high_conviction_alerts()` flags high-score recommendations
- DB models: `Alert` (history), `AlertSetting` (configurable preferences)
- Alembic migration for alerts + alert_settings tables
- API: GET/POST /api/alerts, POST /api/alerts/{id}/acknowledge, CRUD /api/alert-settings

**P5-004 (Options spreads)**:
- Created `models/options_strategies.py` — `SpreadBuilder` with three strategy types
- Bear call spread (credit): high directional + low vol signals
- Bear put spread (debit): high directional + high vol signals
- Iron condor (credit): high vol + neutral directional signals
- Simplified Black-Scholes pricing for premium/delta estimation
- Greeks: delta, theta, vega exposure on all spreads
- Earnings-aware: flags if expiry crosses earnings date
- Position sizing respects $5,000 max constraint
- Extended `PositionSizer.size_spread()` method
- Frontend: `PLDiagram.tsx` (canvas-based P&L chart), `GreeksDisplay.tsx`
- 15 tests covering all spread types and BS estimates

**Current state**: Phases 0-5 complete. 63 tests passing.
**Next steps**: All planned tickets done. Future work: live trading integration, more spread strategies.
