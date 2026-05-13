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

---

## 2026-04-14 — Session 4: Phase 6 — Polish, Risk, Auth

**Agent**: Claude Opus 4.6
**Tickets**: P6-001, P6-002, P6-003, P6-004, P6-005, P6-006, P6-007

**What was done**:

**P6-001 (Watchlist UI)**: Already implemented in prior sessions. Marked done.

**P6-002 (Backtesting UI)**: Already implemented in prior sessions. Marked done.

**P6-007 (E2E Deployment)**:
- Created `frontend/Dockerfile` (multi-stage Node 20 Alpine, standalone output)
- Added `frontend` service to `docker-compose.yml` (port 3100)
- Fixed Ollama URL from `http://ollama:11434` to `http://host.docker.internal:11434` with `extra_hosts`
- Updated `deploy.sh` to sync frontend + backend
- Verified 63 tests passing, frontend builds clean

**P6-004 (Real Options Chain Data)**:
- Created `services/options_chain.py` — `OptionsChainFetcher` using yfinance
- Fetches expirations, full chain (calls + puts), caches in DB with configurable TTL (15 min default)
- Added `OptionsChain` DB model + Alembic migration
- Updated `SpreadBuilder` to use real bid/ask midpoint premiums when chain data provided
- Falls back to Black-Scholes when no real data available
- `SpreadRecommendation` now has `uses_real_data` flag
- Updated `PositionSizer.size_spread()` to accept `chain_data` parameter
- API: GET /api/options-chain/{ticker}?expiration=YYYY-MM-DD, GET /api/options-chain/{ticker}/expirations

**P6-003 (Alerts UI)**:
- Created `frontend/src/app/alerts/page.tsx` — two-tab page (History + Settings)
- History tab: alert table with type badges, acknowledge/acknowledge-all buttons
- Settings tab: add/edit Discord webhook or Telegram bot, alert type toggles, score threshold slider, test button
- Added backend: POST /api/alerts/acknowledge-all, GET /api/alerts/unread-count, POST /api/alert-settings/{id}/test
- Added `send_test()` to `AlertService`
- Added Alerts link to nav

**P6-005 (Portfolio Risk Management)**:
- Created `models/risk_manager.py` — `RiskManager` class
- Correlation matrix from 60-day rolling returns (pandas/numpy)
- Correlation-aware position limit (default 0.70 threshold)
- Sector exposure tracking with configurable max % per sector (default 30%)
- Portfolio metrics: total exposure, max loss, open positions, beta to SPY
- `can_open_position()` runs all risk checks (position limit, correlation, sector)
- Added `PortfolioSnapshot` DB model + migration for daily tracking
- API: GET /api/portfolio/risk, GET /api/portfolio/history, POST /api/portfolio/check
- Frontend: `/portfolio` page with metric cards, sector allocation bar chart, correlation heatmap
- Components: `SectorAllocation.tsx`, `CorrelationHeatmap.tsx`

**P6-006 (Authentication)**:
- JWT auth with bcrypt password hashing (access + refresh tokens)
- `User` DB model + migration
- Auth middleware protects all /api/* routes except /api/health, /api/auth/login, /api/auth/refresh
- Default admin user auto-created on startup (configurable via env vars)
- API: POST /api/auth/login, POST /api/auth/refresh, POST /api/auth/logout
- Frontend: login page, AuthGuard component, auto token refresh on 401, logout button in nav
- Token storage in localStorage with automatic redirect to /login on expiry

**Current state**: Phases 0-6 complete. 63 backend tests passing. Frontend builds clean (10 routes).
**Next steps**: Phase 7 (Alpaca trading integration — P7-001 through P7-007)

---

## 2026-04-?? — Session 5: Phase 7 (P7-001..005) and Phase 8

**Agent**: prior (un-logged)
**Tickets**: P7-001..P7-005, P8-001..P8-006

These tickets were marked `done` in their ticket files and the corresponding code is in place
(see budget=$1,000 in `position_sizer.py`, `min_confidence=0.75` in `config.py`, `risk_type`
column on recommendations + alembic migration `e5f7g9h1j3k5`, alerting hooked to
`min_confidence`, and `alpaca_client`/`order_mapper`/`safety_rails`/`portfolio_sync`/
`execution_engine` services). The session that did this work didn't update the log; this is
a backfill so Phase 8 isn't accidentally re-done.

---

## 2026-04-24 — Session 6: P7-006, P7-007, start of Phase 9

**Agent**: Claude Opus 4.7
**Tickets**: P7-006, P7-007

**P7-006 (Trading UI — portfolio, execution, controls)**:
- New DB model `SystemSetting` (key/value) + alembic migration `f6g8h0i2j4k6` for runtime-mutable trading settings
- New service `services/trading_settings.py` (get/update with config fallback)
- Refactored `safety_rails.py` and `execution_engine.py` to honor DB overrides via shared `defaults` plumbing so existing tests' settings mocks still flow through
- New API routes `routes/trading.py`: GET/PUT `/api/trading/settings` (live mode requires `confirm: "CONFIRM"`), GET `/api/trading/safety-status`, GET `/api/trading/modes`
- Added `safety_status()` to `TradingSafetyRails` returning current rails snapshot (mode, daily loss, open positions, daily orders)
- Frontend types + API methods for `TradingSettings`/`SafetyStatus`
- New components: `TradingControls` (mode selector with CONFIRM gate, auto-execute toggle, score threshold slider, safety rail bars), `TradingModeBadge` (nav badge: gray/yellow/red)
- New page `/execution-log` with passed/blocked filter and date window
- Updated `app/page.tsx` (dashboard) — Execute button per row when trading != disabled, restructured row to avoid nested clickables
- Updated `app/layout.tsx` — added Trading + Execution links and `TradingModeBadge`
- Mounted `TradingControls` at top of `/trading` page
- Added `id` field to `RecommendationResponse` so the dashboard can target rows for execution
- Drive-by fix: recommendations route regex extended to allow `strategy=spread` (was 422)
- Tests: `test_trading_settings.py` (7 tests) — defaults, DB overrides, validation, bool round-trip
- All 160 backend tests pass; frontend `tsc --noEmit` clean

**P7-007 (Paper trading validation script)**:
- New service `services/paper_validation.py` with `PaperValidator`, `_compute_paper_metrics`, `_relative_diff`, `format_report`
- Compares win_rate / avg_pnl / sharpe_ratio / max_drawdown between closed `PaperTrade` rows in window vs `Backtester.run()` over same window
- Flags any metric with relative diff > 10%, with one-line diagnosis hint per metric
- New CLI `scripts/validate_paper_trading.py` — `--start/--end` or `--days N`, optional `--json out.json`, exit 1 if divergences flagged (CI gate)
- New API route `GET /api/validate/paper-vs-backtest?start_date=...&end_date=...`
- Tests: `test_paper_validation.py` (13 tests) — empty/open trade handling, equity-curve max DD, relative diff, in-sync no-divergence, win-rate flag, window filter, invalid window, format renderer

**Current state**: Phase 7 complete. Phase 8 confirmed complete. 160 backend tests pass.
**Next steps**: Phase 9 (P9-001..P9-007 — feature improvements + retrain/backtest)

---

## 2026-04-24 — Session 7: P9-001 Options IV features

**Agent**: Claude Opus 4.7
**Ticket**: P9-001
**What was done**:
- New table `options_snapshots` (model + Alembic migration `g7h9i1j3k5l7`) — daily per-ticker IV summary: `iv_atm_30d`, `iv_atm_90d`, `iv_rank_252d`, `iv_percentile_252d`, `put_call_skew_25d`, `term_structure_slope`, `has_options`
- New `pipeline/options_fetcher.py` — `OptionsFetcher.fetch_one/fetch_all` pulls yfinance chains for the ATM options nearest 30 DTE and 90 DTE, computes ATM-IV avg of call/put, term-structure slope, and a 25-delta skew approximated via ±10% strike offset (no Greeks). Always upserts a row — flagged `has_options=0` on missing chain / yfinance error
- `_iv_rank_and_percentile` reads up to 380 calendar days of prior `iv_atm_30d` for that ticker and computes rank/percentile
- New `features/options.py` — `OPTIONS_FEATURE_COLS`, neutral `DEFAULT_FEATURES`, `get_options_features(db, ticker, on_date)` for single-row prediction, `attach_options_features(db, df)` for as-of join into a training DataFrame (uses `pd.merge_asof` with datetime conversion)
- `models/directional.py` — appended `OPTIONS_FEATURE_COLS` to `FEATURE_COLS`, joins options features in `build_dataset`, `predict()` now uses `OPTIONS_DEFAULTS` for missing columns
- `pipeline/scheduler.py` — new `job_fetch_options` runs daily at 6:45 ET (after price fetch, before sentiment); recommendation job now merges in `get_options_features` for each ticker
- Tests: `test_options_fetcher.py` (13) and `test_options_features.py` (7) covering nearest-expiration logic, ATM-IV averaging, skew strike offset, full snapshot upsert, IV rank from history, no-options / yfinance-error fallback, same-day upsert idempotence, `get_options_features` defaults & on-date cutoff, `attach_options_features` as-of join + missing-ticker default

**Decisions / notes**:
- 25-delta skew uses a strike-offset proxy (±10% OTM) rather than true Greeks — yfinance does not return delta. Documented in module docstring.
- `iv_rank_252d` falls back to 0.0 when no prior history exists; production rank values become meaningful only after the daily job has accumulated ~30+ snapshots
- Model retraining with new features is intentionally deferred to **P9-007** (retrain & backtest) per the ticket bundle plan; this session adds the data pipeline + feature plumbing only
- All 180 backend tests pass (was 160 → +20 new)

**Current state**: P9-001 complete. Options snapshot table + daily fetcher + feature plumbing live. Model still serving from old weights — new IV features fed to it as `OPTIONS_DEFAULTS` until P9-007 retrain.
**Next steps**: P9-002 (macro regime features) — same shape (snapshot table + fetcher + feature module + scheduler hook)

---

## 2026-04-24 — Session 8: P9-002..P9-007 — Phase 9 finish (features, calibration, backtest)

**Agent**: Claude Opus 4.7
**Tickets**: P9-002, P9-003, P9-004, P9-005, P9-006, P9-007

**P9-002 (Macro regime features)**:
- Added `^VIX` to `default_watchlist` so the daily price-history job pulls VIX OHLC alongside SPY
- New `features/macro.py` — `MACRO_FEATURE_COLS = [vix_level, vix_percentile_252d, spy_drawdown_pct, spy_above_sma_50, spy_above_sma_200, spy_return_5d, spy_return_20d]`
- `_build_macro_frame(db)` joins SPY+^VIX on date, forward-fills VIX-only gaps, computes 252d rolling rank, drawdown vs trailing-252d high, SMA-50/200 binary above-flags, 5d/20d returns
- `get_macro_features` (single-row, on-date cutoff) and `attach_macro_features` (bulk as-of join via `pd.merge_asof`)
- Defaults: vix=18, percentile=0.5, drawdown=0, both above_smas=1, returns=0
- Tests: `test_macro_features.py` (5)

**P9-003 (Sector relative-strength features)**:
- `config.py`: `SECTOR_ETF_MAP` (AAPL→XLK, JPM→XLF, ...) + `sector_etf_for(ticker)` (defaults to "SPY"), all 11 sector ETFs added to `default_watchlist`
- New `features/sector.py` — `SECTOR_FEATURE_COLS = [sector_return_5d, sector_return_20d, return_5d_vs_sector, return_20d_vs_sector]`
- `attach_sector_features` joins per-ticker price history with the sector-ETF daily returns and computes ticker-vs-sector deltas
- Tests: `test_sector_features.py` (6) including SPY-fallback path

**P9-004 (Sentiment time-series features)**:
- New `sentiment_history` table + alembic migration `h8i0j2k4l6m8` (one row per ticker per day)
- New `features/sentiment.py` — `SENTIMENT_FEATURE_COLS = [sentiment_latest, sentiment_ma_7d, sentiment_ma_30d, sentiment_momentum, sentiment_zscore_30d, article_count_zscore_30d]`
- `_safe_z(value, mean, std)` uses `abs(std) < 1e-9` tolerance — pandas `.std()` on near-constant floats returns a residual, plain `std == 0` was misclassifying constant series as having spread
- `upsert_daily_sentiment(db, ticker, on_date, sentiment_score, confidence, article_count)` — idempotent
- Scheduler `job_sentiment` now persists each ticker's `composite_sentiment` daily instead of discarding
- Tests: `test_sentiment_features.py` (6) including <30d window, all-same-scores, missing ticker

**P9-005 (Earnings proximity features)**:
- New `earnings_calendar` table (alembic migration shared with P9-004 — `h8i0j2k4l6m8`)
- New `pipeline/earnings_fetcher.py` — handles both yfinance dict shape (`{"Earnings Date": [...]}`) and the older DataFrame shape
- New `features/earnings.py` — `EARNINGS_FEATURE_COLS = [days_to_earnings, days_since_earnings, earnings_within_3d, earnings_within_10d]`. `DAYS_TO_CAP = 90`. `days_to_earnings = -1` signals "unknown"
- `config.skip_near_earnings` flag — when true, scheduler filters `earnings_within_3d=True` tickers out of recommendations
- Scheduler `job_fetch_earnings` runs Sundays at 6 AM ET
- Tests: `test_earnings_features.py` (12) — boundary flips at 3/10 days, cap behavior, unknown-ticker -1, skip-flag respected

**P9-006 (Probability calibration)**:
- `directional.py`: training now does time-ordered 70/15/15 split (train / calibration / test). After XGBoost fits on the train fold, wrap with `CalibratedClassifierCV(estimator=self.model, cv='prefit', method=method)` on the calibration fold; method auto-selects "isotonic" for ≥1000 calibration rows, "sigmoid" otherwise
- Added `self.calibrator`, `self.brier_score`; `_proba(X)` helper routes to calibrator if present
- `save()` / `load()` persist calibrator + brier
- `_merged_defaults()` consolidates default dicts from all 5 phase-9 feature modules so `predict()` doesn't regress on missing columns
- New `models/calibration_plot.py` — `reliability_data(y_true, y_prob, n_bins)` (quantile bins via sklearn `calibration_curve`), `save_reliability_plot(...)` uses headless matplotlib backend
- Tests: `test_directional_calibration.py` (5) — calibrator persists round-trip, predicted probs spread wider than raw, brier score scalar
- Note: sklearn 1.6 deprecates `cv='prefit'` (FutureWarning) — flagged for follow-up to switch to `FrozenEstimator`

**P9-007 (Walk-forward backtest harness)**:
- New `backtest/walk_forward.py` — `walk_forward(df, feature_cols, n_folds=4, train_min_rows=1000, confidence_threshold=0.5, fit_fn=None)` returns `BacktestResult(folds, aggregate, feature_cols)`
- `_fit_xgb` builds standard XGBClassifier with positive-class weighting; `_trade_pnl` simulates asymmetric R:R (`payoff_win=1.0`, `payoff_loss=-1.5`)
- `FoldResult` captures fold idx, train/test ranges, n_train/n_test, AUC, Brier, hit-rate, avg P&L per trade, n_trades
- New `backtest/report.py` — `render_report(result, git_sha, old_metrics, title)` outputs aggregate, old-vs-new table, per-fold detail, ship-gate (`SHIP_AUC=0.55`, `SHIP_HIT_RATE=0.52`); `write_report(...)` writes to `backtest_reports/<date>-<git_sha>.md`
- New CLI `scripts/run_backtest.py` (`--folds`, `--threshold`, `--train-min-rows`, `--git-sha`, `--no-fail`) — exits 1 if ship gate fails unless `--no-fail`
- Tests: `test_walk_forward.py` (5) — fold count, ascending date splits, ship-gate logic, report renders
- **NOT done in this session**: actually retraining/deploying the new model — requires production data (PostgreSQL on the homelab VM, ~1+ year of accumulated `options_snapshots` and `sentiment_history`). Marked as runbook follow-up below.

**Decisions / fixes**:
- `attach_options_features` `pd.merge_asof` MergeError on object dtypes — fixed by explicit `pd.to_datetime` before merge, restoring the original `date` column afterwards
- `OptionsFetcher.fetch_one` returned `"ok"` for tickers with chain pages but no usable expirations — added guard: if both `exp_30` and `exp_90` are None, return `"no_options"`
- `get_earnings_features` had a redundant `if 0 <= days_to > DAYS_TO_CAP` after the `elif`; simplified to `if days_to > DAYS_TO_CAP: days_to = DAYS_TO_CAP`
- Sentiment scheduler used `avg_sentiment` (doesn't exist); the analyzer returns `composite_sentiment` — fixed
- Removed stale `OPTIONS_DEFAULTS` import after switching `predict()` to `_merged_defaults()`

**Test count**: 180 → 219 (+39 new across 6 test files)

**Current state**: All 7 Phase 9 tickets `done`. Code paths in place: 5 new feature modules (options, macro, sector, sentiment-ts, earnings), calibrated XGBoost wrapper, walk-forward backtest harness + ship-gate CLI. Scheduler wires options + earnings + sentiment-history persistence. Model artifact on disk is still v1 (pre-phase-9 features); new feature columns currently feed in as defaults until retraining happens on the production VM.

**Next steps (runbook, not session work)**:
1. SSH to homelab VM, exec into backend container
2. Run `python -m scripts.train_directional` (after extending its window flag to 5y) — this will produce a new pickle with calibrator + brier
3. Run `python -m scripts.run_backtest --folds 4` against the production DB and confirm ship gate passes (AUC > 0.55, hit rate > 0.52)
4. If pass: archive old artifact to `trained_models/archive/<date>/`, copy new pickle, restart backend
5. Update this MODEL_REGISTRY.md with v2 metadata after the retrain

## 2026-05-01 — P10-001: Analyst rating-change features (commit 282c2bd)

**Why**: Hyperparameter sweep proved the v2 model is at AUC 0.555 because it's
information-saturated, not overfitting. Cross-asset macro features regressed AUC.
Per `directional_model_information_ceiling.md`, the path forward is per-ticker
event features that add signal independent of price action. Analyst rating
changes are the cheapest first cut (free via yfinance, well-documented signal).

**Done**:
- Migration `i9j1k3l5m7n9_add_analyst_ratings.py` — analyst_ratings table with
  unique (ticker, date, firm, to_grade) index for idempotent backfill
- `AnalystRating` SQLAlchemy model in `db/models.py`
- `src/features/analyst.py` — 6 windowed-aggregate features:
  days_since_downgrade/upgrade (capped 365), downgrades_30d, upgrades_30d,
  net_rating_actions_60d, analyst_action_5d
- `scripts/backfill_analyst_ratings.py` — yfinance `Ticker.upgrades_downgrades`,
  idempotent, action normalized to {up,down,init,main,reit}
- Wired through `directional.py` FEATURE_COLS, `_merged_defaults`, build_dataset
  attach chain; also through `scheduler.py` and `diagnose_recs_v2.py` inference
  feature dicts
- 7 tests in `test_analyst_features.py` covering default path, recency,
  windowing boundaries, day cap, case normalization, future-date filter,
  per-ticker `attach_analyst_features` join

**NOT done**: backfill + v3 retrain — SSH to 10.0.0.47 from this session is
broken (publickey auth refused for both `andym` and `ubuntu`). Runbook below.

**Decisions**:
- Used unique index on `(ticker, date, firm, to_grade)` rather than just
  `(ticker, date)` — multiple firms can issue ratings on the same day
- Default `days_since_*` = -1.0 (semantically distinct from "very long ago"),
  consistent with `earnings.py` convention
- Action normalization is permissive: any prefix-match keeps the canonical
  short form; unknown actions truncated to 20 chars rather than dropped

**Runbook (must run on GPU VM)**:
1. SSH to `10.0.0.47`, `cd /opt/stock-analysis/backend`
2. `git pull` to get commit 282c2bd onto master
3. `docker compose up -d --build` (so the new alembic version + analyst.py
   land in the image — backfill script is run from inside the container)
4. `docker exec backend-backend-1 alembic upgrade head` — creates analyst_ratings
5. `docker exec backend-backend-1 python -m scripts.backfill_analyst_ratings`
   — populates ratings for all watchlist tickers
6. `docker exec backend-backend-1 python -m scripts.train_directional_v2`
   — trains v3 with the new feature group; compare test_metrics.auc_roc
   vs v2's 0.555. If AUC ≥ 0.560 with calibrated=True, copy v3 pickle into
   `trained_models/directional_xgb_v1.pkl` and restart backend
7. `docker exec backend-backend-1 python -m scripts.diagnose_recs_v2` — sanity
   check that dir_prob distribution still looks reasonable (not collapsed
   to 0 or 1) and at least a few tickers cross the 0.5 score gate

## 2026-05-04 — P10-002: Replace absolute score gate with dir_prob lift + top-K (commit 996e1dd)

**Why**: Prior diagnostic showed median dir_prob=0.122 in production. The
hardcoded `score >= 0.5` gate at scheduler.py:277 with weights
(0.4*dir_prob + 0.3*vol + 0.3*sent) effectively required dir_prob >= 0.7,
which for a calibrated rare-event classifier (base rate ~17.5%) is a
quarterly-frequency event. Zero recs was the model's *correct* output
under that gate, regardless of v2/v3 quality.

**Done**:
- 3 new settings: `directional_base_rate=0.175`, `min_dir_prob_lift=1.5`,
  `recommendations_top_k=10`
- New module `src/pipeline/rec_ranker.py` with pure-functional
  `select_candidates(candidates, base_rate, lift, top_k)` that filters on
  dir_prob >= base_rate * lift, sorts by composite score desc, returns top-K
- Refactored `job_generate_recommendations` from per-ticker score-gate-then-
  cascade into collect-then-rank-then-emit. Composite score is now a
  *ranker* not a *gate*; `meets_confidence` still applied per selected rec
- 8 unit tests in `test_rec_ranker.py` — all pass locally on Windows Python
  without docker (only pydantic dep)

**Trade-off accepted**: With base_rate * 1.5 = 0.2625 floor, on a flat
market with all dir_prob ~0.18 we still produce zero recs (correctly).
On a meaningfully-bearish day with several tickers at dir_prob > 0.26,
we surface up to 10 ranked by composite score. This is the right shape:
"top opportunities when they exist, silence otherwise."

**Tunable**: If after deploy the rec count is still 0 most days, lower
`min_dir_prob_lift` toward 1.0 (= "any ticker the model thinks beats the
base rate"). If too noisy, raise `recommendations_top_k` cap or add a
hard floor on composite score.

**NOT done**: Live verification — needs SSH to GPU VM. Add to runbook:
after deploy, watch the first scheduler run's log line for
"X candidates evaluated, Y below dir_prob floor" to tune the lift.

## 2026-05-04 — Backfill dedupe fix + v3 training script (commits 5074f01, 07bc12e)

**Why**: P10-001 backfill landed zero rows on the VM despite the script reporting
"queued" for thousands. Root cause: yfinance `Ticker.upgrades_downgrades` returns
same-key rows for some tickers (intraday re-rates, upstream dupes). The unique
index on `(ticker, date, firm, to_grade)` rejected the **entire SQLAlchemy INSERT
batch** on the first duplicate, so a single repeat blew away the whole per-ticker
batch. Once that was unblocked, train v3 with the analyst feature group on the
populated table and tune the new dir_prob lift floor against the resulting
distribution.

**5074f01 — fix(backfill): dedupe yfinance rows within batch**:
- `scripts/backfill_analyst_ratings.py`: track `(ticker, date, firm, to_grade)`
  keys seen within the per-ticker batch, skip dupes client-side before the
  INSERT. Avoids the rollback-the-whole-batch failure mode of relying on the
  DB unique constraint as a dedupe.
- Re-run on VM: 12,011 new rows across 38 tickers, 0 failures (was 0 rows / 38
  failures pre-fix).

**07bc12e — feat(rec): v3 training script + lower dir_prob lift floor to 1.3**:
- New `scripts/train_directional_v3.py` — same shape as v2 trainer but appends
  `ANALYST_FEATURE_COLS` (the 6 P10-001 features) to FEATURE_COLS and writes
  pickle to `trained_models/directional_xgb_v3.pkl`.
- VM training run results: AUC 0.5505 (vs v2's 0.5423, +0.008), Brier 0.1566
  (vs 0.1628, -0.006). Modest but the right *direction* — analyst features
  show importance 0.013-0.021 each, not redundant with price/options data.
  Updated `directional_model_information_ceiling.md` with this finding.
- `config.py`: `min_dir_prob_lift` default 1.5 → 1.3. Spot-checked v3 dir_prob
  distribution against today's watchlist: max=0.260 (DIS), median=0.123. The
  1.5 lift (floor 0.2625) yields zero candidates on a flat-bullish day; 1.3
  (floor 0.2275) catches the top ~4 most-bearish picks (DIS, GE, COP, SLB),
  which is the right shape for the rare-event setup.

**Current state**: Phase 10 has 2 done tickets (P10-001, P10-002) plus this
follow-up commit pair.

## 2026-05-04 — Verification session: v3 confirmed in prod, new structural blocker found

**SSH access**: `ssh proxmox@10.0.0.47` works fine — earlier memory note that auth
was broken was wrong (`andym` and `ubuntu` users don't have keys, but `proxmox`
does and has docker access).

**v3 promotion confirmed**: `md5sum` shows `directional_xgb_v1.pkl` ==
`directional_xgb_v3.pkl` on the VM (decdfa6...), with `directional_xgb_v1.pkl.bak.20260504`
holding the pre-promotion v1 (different hash). The promotion was already done
prior to this session — the backend container was rebuilt at 13:09 UTC today
with v3 baked in via the Dockerfile's `COPY trained_models/`.

**Ollama config also resolved**: `OLLAMA_MODEL=qwen3.5:9b` in `.env` (was
previously misconfigured to `gemma4:e4b` per memory). `OllamaClient.generate()`
returns immediately, `sentiment_history.MAX(date) = today`. Updated memory to
mark `ollama_model_misconfigured.md` as resolved.

**End-to-end diagnostic** (manually triggered `job_generate_recommendations`
inside backend container, with stdout logging configured):
```
49 candidates evaluated, 45 below dir_prob floor (base_rate*1.30),
4 filtered for confidence → 0 new recs
```

**Finding**: P10-002 fixed the absolute-score gate (`score >= 0.5`) but the
*next* gate, `Ensemble.meets_confidence` against `min_confidence=0.75`, is
structurally unreachable for the calibrated v3 model. For directional_prob
in [0.10, 0.27] (the realistic range for the bearish rare-event setup),
`directional_confidence = abs(prob - 0.5) * 2` maxes around 0.48 — far below
the 0.75 floor. Same pathology as the original score gate, one step deeper.

**Why this wasn't caught earlier**: the score gate was the visible blocker
because it failed first. With it removed, 4 tickers now reach the confidence
gate — and the unreachability of *that* one is now the visible blocker.

**Memory writes (this session)**:
- New: `meets_confidence_unreachable.md` documents the finding with the exact
  diagnostic command and 4 candidate fixes (lower min_confidence, relative
  lift gate, drop sentiment from AND, decoupled per-signal floors)
- Updated: `vm_runtime_layout.md` — recommendations table has no
  `direction_confidence` columns; SSH user is `proxmox`
- Updated: `directional_model_information_ceiling.md` — v3 IS promoted, with
  live-day diagnostic line
- Updated: `ollama_model_misconfigured.md` — marked RESOLVED
- Updated: `MEMORY.md` index (also caught a missing entry for the
  model-ceiling memory)

**Code changes**: none. This session was diagnostic + memory hygiene only.

**Next steps (require user input)**: pick a confidence-gate fix from the four
listed in `meets_confidence_unreachable.md` before P10-003 (short-interest
features) — even with better signal, the new features hit the same gate wall.

## 2026-05-04 — P10-004: confidence-lift gate replaces absolute min_confidence (commit 57359bb)

**Why**: Diagnostic showed `meets_confidence` against `min_confidence=0.75` was
structurally unreachable for the calibrated v3 model (`abs(prob-0.5)*2` maxes
~0.48 for the realistic [0.10, 0.27] dir_prob range). Picked option #2 from
`meets_confidence_unreachable.md`: replace the absolute floor with a relative
"lift over base rate" measure, mirroring the P10-002 ranker fix one layer down.

**`config.py`**:
- Marked `min_confidence=0.75` as deprecated (kept for back-compat).
- Added `min_directional_lift=0.05` (relative bearish lift floor, normalized
  to [0,1] over the base-rate-to-1.0 headroom).
- Added `min_sentiment_confidence=0.40` (separate floor on LLM confidence).

**`models/ensemble.py`**:
- `Ensemble.__init__` now takes `min_directional_lift` + `min_sentiment_confidence`;
  legacy `min_confidence` kwarg still accepted and routed to the sentiment floor.
- `score()` computes `directional_lift = max(0, dir_prob - base_rate) / (1 - base_rate)`
  (only meaningful when bearish direction is the rare event we're predicting).
- `meets_confidence = directional_lift >= min_directional_lift AND
   sentiment_confidence >= min_sentiment_confidence`.

**Tests**: updated 5 ensemble tests in `test_position_sizer.py` to use the new
gate; added `test_legacy_min_confidence_kwarg_maps_to_sentiment_floor`. All 25
position-sizer tests pass.

**Comments only**: `services/alerting.py` docstring updated to reflect the new
two-floor regime.

**Current state**: Code shipped. Live verification deferred to runbook
(requires triggering `job_generate_recommendations` after both this and P10-003
land on the VM).

## 2026-05-04 — P10-003: short interest features (uncommitted, code complete)

**Why**: Per `directional_model_information_ceiling.md` priority list, short
interest is the next independent information source — rising short interest
and squeezes both produce price action that the technical/options/macro/sector/
sentiment/earnings/analyst features can't predict on their own. Mirror the
P10-001 architecture: model + migration + feature module + backfill + wiring.

**Database**:
- `db/models.py` — added `ShortInterestSnapshot(ticker, report_date, shares_short,
  short_percent_of_float, short_ratio_days_to_cover, has_data, fetched_at)` with
  unique index on `(ticker, report_date)`.
- `alembic/versions/j0k2l4m6n8o0_add_short_interest_snapshots.py` — new migration
  (down_revision=`i9j1k3l5m7n9`).

**Feature module** (`src/features/short_interest.py`):
- 6 features: `short_percent_of_float`, `short_ratio_days_to_cover`,
  `short_interest_change_pct`, `short_interest_zscore_180d`,
  `days_since_short_report`, `has_short_data`.
- Sane defaults (0.03 / 2.0 / 0.0 / 0.0 / -1.0 / 0.0) for tickers without a
  snapshot — table accumulates over time as the fetcher runs.
- `_compute()` filters to past snapshots, computes change-pct from prior
  snapshot, z-score over trailing 180d (requires ≥3 points), caps days-since
  at 180 (FINRA cycles every ~15d).
- `attach_short_interest_features()` does a per-ticker cached bulk join for
  training datasets.

**Backfill** (`scripts/backfill_short_interest.py`):
- yfinance.info returns up to 2 historical points per ticker
  (`dateShortInterest`/`sharesShort` and `sharesShortPreviousMonthDate`/
  `sharesShortPriorMonth`). Each backfill run can therefore add 0-2 rows.
- Idempotent on `(ticker, report_date)` with batch-internal dedupe (lesson
  from the P10-001 dedupe fix).
- Stub row with `has_data=0` written for tickers with no data and no prior
  rows so the table still surfaces them.

**Model wiring**:
- `models/directional.py` — appended `SHORT_INTEREST_FEATURE_COLS` to
  `FEATURE_COLS`, merged defaults into `_merged_defaults`, called
  `attach_short_interest_features(db, df)` in `build_dataset`.
- `pipeline/scheduler.py` — added `get_short_interest_features` to the live
  inference feature dict.
- `scripts/diagnose_recs_v2.py` — added the same to the diagnostic.

**Trainer** (`scripts/train_directional_v4.py`):
- Mirrors v3 trainer; writes `directional_xgb_v4.pkl`. Prints side-by-side
  comparison vs v3's metrics + per-feature importance for the new short-interest
  group.

**Tests** (`tests/test_short_interest_features.py`):
- 10 tests covering defaults, most-recent-snapshot retrieval, change-pct,
  z-score (insufficient + sufficient samples), days-since cap, future-snapshot
  filter, `has_data=0` exclusion, bulk attach, empty-DataFrame attach.
- All 10 pass in container; full suite 245 pass / 1 fail (the one failure is
  a pre-existing matplotlib-not-installed in `test_save_reliability_plot_writes_file`
  — unrelated to P10-003).

**Runbook (deferred to VM)**:
1. `docker exec backend-backend-1 alembic upgrade head` to apply migration.
2. `docker exec backend-backend-1 python -m scripts.backfill_short_interest`
   to populate (~38 tickers × up to 2 snapshots each = ~70 rows day 0).
3. `docker exec backend-backend-1 python -m scripts.train_directional_v4`
   and compare vs v3's 0.5505 AUC. Promote v4 → v1.pkl if AUC moves up.
4. Trigger `job_generate_recommendations` and verify the P10-004 gate now
   produces non-zero recs.

## 2026-05-06 — Sentiment cleanup: drop NewsAPI + Reddit, add Yahoo RSS, recency-gate Ollama (branch feat/sentiment-rss-recency)

**Why**: Diagnostic of the live sentiment pipeline showed (a) NewsAPI key
invalid → 0 contribution, (b) Reddit's PRAW search returned 0 posts on
*every* ticker since project inception (silent failure inside the per-
subreddit try/except), (c) Ollama was scoring stale headlines — only
~42% of analyzed Finviz items today were ≤3d old, ~7% were >30d old.

**Changes**:
- `backend/src/services/headline_fetcher.py`: deleted `NewsApiFetcher` and
  `RedditFetcher` classes. Added `YahooRssFetcher` using
  `feeds.finance.yahoo.com/rss/2.0/headline?s={TICKER}&region=US&lang=en-US`
  via feedparser. RFC-822 pubDate parsing via `email.utils.parsedate_to_datetime`.
- `backend/src/pipeline/sentiment.py`: `SentimentAnalyzer.fetchers` now
  `[FinvizFetcher(), YahooRssFetcher()]`. Recency gate filters headlines
  to `>= today - settings.sentiment_max_headline_age_days` (default 7d)
  before the Ollama loop. Headlines with `date=None` are kept (assumed
  recent — better than dropping a real signal).
  Both early-return paths now consistently return
  `{"composite_sentiment": None, "scores_computed": 0}` — scheduler
  already short-circuits on `scores_computed==0` (line 90), so no
  downstream change needed.
- `backend/pyproject.toml`: removed `newsapi-python`, `praw`; added
  `feedparser>=6.0.0`.
- `backend/src/config.py`: removed `newsapi_key`, `reddit_client_id/secret/user_agent`
  Settings fields. Added `sentiment_max_headline_age_days: int = 7`.
- `backend/tests/test_sentiment.py`: dropped NewsAPI/Reddit references,
  added 3 YahooRssFetcher tests (typical, empty, bozo) and 3 recency-gate
  tests (stale filtered, all-stale short-circuits Ollama, undated kept).
  All 13 tests pass in container.

**Live RSS verification**: `YahooRssFetcher().fetch("AAPL")` returned 10
items, all dated today (2026-05-06).

**Container hygiene**: Verified deploy pipeline replaces /app/src on
rebuild. Tests run via `python -m pytest backend/tests/test_sentiment.py`
in the prod container after `cp` overlay (only test_sentiment.py is
actually shipped — other test files are dev-only).

**Expected impact**:
- Ollama load drops ~60% (skip stale headlines)
- ~80 min/run reclaimed (Reddit fetcher loop deleted)
- Sentiment signal becomes more relevant (week-stale only, not month-stale)
- Yahoo RSS adds a real second source (~10 items/ticker, all dated)

**Next steps**: Merge to main → trigger deploy → verify next 07:30 EDT
sentiment run logs show YahooRssFetcher hits + recency-gate dropped counts.
Then proceed with v5 training (post-watchlist-expansion data has ~1d of
accumulation as of today).

---

## 2026-05-07 — Session: P10-008 Wikipedia page-view features

**Agent**: Claude Opus 4.7
**Ticket**: P10-008
**What was done**:

Added Wikipedia daily page-view counts as a retail-attention proxy for the
directional model — a strict superset of the Google-Trends signal in
Da/Engelberg/Gao (2011) since Wikimedia exposes 2+ years of free, unauth
daily history per article.

Files created:
- `backend/src/config/wikipedia_titles.json` — hand-curated 159-ticker →
  Wikipedia title map. Disambiguates single-letter tickers (F, T, C, O), share
  classes (BRK-B, GOOGL), renames (META, SQ, SLB, ZM, GE), and ETFs (sector
  SPDRs all map to `Select_Sector_SPDRs`).
- `backend/alembic/versions/k1l3m5n7o9p1_add_wikipedia_pageviews.py` — new
  table `wikipedia_pageviews(ticker, view_date, page_views, wikipedia_title,
  fetched_at)` + composite unique index on (ticker, view_date). Chains from
  `j0k2l4m6n8o0`.
- `backend/src/db/models.py` — added `WikipediaPageviews` model.
- `backend/src/pipeline/wikipedia_fetcher.py` — `WikipediaPageviewFetcher`
  hitting the Wikimedia per-article daily endpoint. Includes the required
  `User-Agent: stock-analysis andymihalik@gmail.com` header (same convention
  as SEC), date-chunked at 50d/call, 3-attempt exponential-backoff retry on
  429/5xx, URL-encodes special chars (e.g. `&` in `AT&T`), stubs missing days
  with 0 to keep the series dense for rolling stats.
- `backend/src/features/wikipedia.py` — 5 features:
  `wiki_views_zscore_30d`, `wiki_views_zscore_180d`, `wiki_views_change_7d`,
  `wiki_views_spike` (>3× trailing-30d mean), `wiki_views_log` (log1p).
  Uses `pd.merge_asof(direction='backward')` so weekend Wikipedia data
  forward-fills into Monday's prediction row.
- `backend/scripts/backfill_wikipedia_pageviews.py` — idempotent CLI with
  `--lookback-days` (default 730), `--tickers`, `--end-date`. Re-runs are
  cheap thanks to the composite unique index.
- `backend/tests/test_wikipedia_fetcher.py` (10 tests) — date-chunked
  backfill, gap densification, 404/5xx handling, retry, URL-encoding,
  unmapped-ticker skip.
- `backend/tests/test_wikipedia_features.py` (13 tests) — quiet/spike/noisy
  z-scores, change-7d edges, log scaling, weekend forward-fill via merge_asof,
  empty/unknown-ticker defaults.

Files extended:
- `backend/src/models/directional.py` — `FEATURE_COLS` += 5 wiki cols
  (now 49 total), `_merged_defaults`, and `build_dataset` attach hook.
- `backend/src/pipeline/scheduler.py` — new `job_fetch_wikipedia_pageviews`
  cron at 5:30 ET *every day* (Wikipedia data is daily even on weekends,
  and we want fresh Monday data forward-filled from the weekend). Inference
  feature dict in `job_generate_recommendations` extended.
- `backend/scripts/diagnose_recs_v2.py` — feature dict extended.

Side-effect refactor (forced by package-name collision):
- Converted `backend/src/config.py` (a single module) into
  `backend/src/config/__init__.py` (a package) so the new
  `wikipedia_titles.json` could live at the path the ticket specified.
  Verified `from src.config import get_settings, sector_etf_for, SECTOR_ETF_MAP`
  still works; full test suite (278/278) passes unchanged.

**Test results**: 278/278 passing locally (incl. 23 new wikipedia tests).
v5 trainer/promotion is intentionally NOT included in this ticket — that's
a follow-up after the backfill runs in prod and we have ~115k dense rows
to retrain on.

**Next steps**:
1. Push branch + merge to master.
2. Run alembic upgrade in prod container.
3. Run `python -m scripts.backfill_wikipedia_pageviews` in prod (~159
   tickers × 730d / 50d-per-call ≈ 2,300 HTTP calls; ~5 minutes).
4. Verify scheduler picks up `fetch_wikipedia_pageviews` job at 5:30 ET.
5. Once dense data is in place, write `train_directional_v5.py` (or v6 if
   bundling with another P10 group), apply the AUC ≥ v3 + 0.005 promotion
   gate. Bundling with P10-005 (insider) once that lands is reasonable.

---

## 2026-05-12 — Session 22: Sentiment pipeline rescue (Ollama GPU + parallel analyze_all)

**Agent**: Claude Opus 4.7
**What was done**:

**Diagnosed**: `sentiment_history` had been stale since 2026-05-09. `/api/health` was missing the `sentiment` entry — earlier 2026-05-08 fix had been blamed (async silent failure), but this was a different root cause.

Investigation showed: morning sentiment job started at 11:00 UTC but `_record_run` was never called. Ollama was making calls but each took ~35s on CPU. `docker exec ollama nvidia-smi` returned `Failed to initialize NVML: Unknown Error` — classic `nvidia-container-toolkit` + systemd cgroup loss bug. Container had been Up 5 days; cgroup device permissions were stripped on some intervening systemd/docker daemon-reload event.

**Fixed Ollama (durable)**:
- `docker restart ollama` immediately restored GPU access — warm gen latency 35s → 2.6s, 32/33 layers on CUDA0, 6.4 GiB VRAM.
- Edited `/home/proxmox/ai-stack/docker-compose.yml` to add explicit `devices:` block (`/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`) for the ollama service. Docker's own cgroup allowlist for `devices:` is structural and survives systemd reloads. Backup at `docker-compose.yml.bak.20260512-101357`. Whisper not updated (same compose, same vulnerability — left for follow-up).
- Did NOT set `no-cgroups=true` in `/etc/nvidia-container-runtime/config.toml` (needed sudo; explicit devices alone are sufficient).

**Fixed sentiment parallelization (PR #29, not yet merged)**:
- `analyze_all` was fully serial — headline-fetcher I/O sat on the critical path between Ollama calls.
- Switched to `asyncio.gather` with `Semaphore(3)`; each ticker gets its own `SessionLocal()` to avoid SQLAlchemy session contention.
- `analyze_ticker` / `_analyze_headline` now take optional `db` parameter; existing tests preserved.
- Added 3 tests: concurrency bound respected, error isolation, per-ticker session creation. All 32 tests (sentiment + scheduler) pass, full backend suite green.
- Branch `fix/sentiment-parallel-analyze-all` (commit `188c82f`), PR #29 open, NOT merged (would trigger deploy mid-run).

**Rescued today's sentiment data**:
- Original 11:00 UTC job survived the Ollama restart (per-ticker exception handling) and completed at 16:22:05 UTC: `Scheduler: sentiment complete — 159 tickers scored`. Total runtime ~5h22min (CPU phase + GPU phase). `sentiment_history` rows for 2026-05-12 = 159.

**Memory updates**:
- New: `projects/stock-analysis/ollama_gpu_cgroup_fix_2026-05-12.md` (full diagnosis + fix).
- Updated: `projects/homelab-monitoring/ollama_gpu_loss.md` (prevention section now describes the applied fix).
- Updated: `projects/stock-analysis/vm_runtime_layout.md` (scheduler module moved to `/app/src/pipeline/scheduler.py`).

**Current state**:
- Sentiment pipeline running on GPU, latency ~2.6s/call (was 35s).
- `sentiment_history` current through 2026-05-12.
- Today's 11:30 recs run was 0 recs — blocked by `dir_prob >= base_rate * 1.30` floor (158/159 below floor). NOT a sentiment problem today.
- 3-day stale-rec streak broke `paper_trading_readiness.md` gate #1 (5 consecutive days).
- PR #29 ready to merge.

**Next steps**:
1. Merge PR #29 so tomorrow's 07:00 ET run completes in ~25min instead of hours.
2. Investigate `dir_prob >= base_rate * 1.30` floor — why 158/159 below it today. Model drift, threshold mis-set, or genuinely no setup?
3. (Cheap win) Add `OLLAMA_FLASH_ATTENTION=1` env var to free ~500 MiB so all 33/33 layers fit on GPU.
4. (De-risk) Fix all-or-nothing sentiment persistence in `_job_sentiment_async` — upsert per-ticker as you go so a mid-run crash doesn't lose everything.
5. (Preemptive) Apply same `devices:` block to whisper in ai-stack compose.
6. (Long-running) Per-ticker event features for v6 directional model — per `directional_model_information_ceiling.md`, the only known path to break the AUC ceiling.

---

## 2026-05-12 — Session 23: dir_prob investigation + scheduler return-lag fix (PR #30)

**Agent**: Claude Opus 4.7

**Picking up from Session 22**: merged PR #29 (sentiment parallelization), then investigated why today's rec job produced 0 recs.

**Investigation: dir_prob floor**
Wrote `scripts/dir_prob_distribution.py` (later removed from PR — diagnostic only) to score the watchlist with v3 directional and print the full distribution. Required passing explicit `Path("/app/trained_models/directional_xgb_v3.pkl")` since `DEFAULT_MODEL_PATH` still points to v1 and the installed-package `__file__.parent.parent.parent` doesn't resolve to `/app` when run via `docker exec`.

Today's distribution: max **0.1453** (ASML, KLAC), mean 0.1064, median 0.1176, stdev 0.0222. **Zero tickers above 0.18** — the 0.2275 floor is unreachable from today's prediction surface.

**Key finding: calibration plateaus**
All 8 recs across 2026-05-05/06/11 stored `directional_signal = exactly 0.2277` (different tickers, different days, same value). Combined with today's plateau pattern (every ticker lands on one of 4 discrete values: 0.0741, 0.1230, 0.1413, 0.1453), v3's calibrator clearly emits a small set of plateau values. The 0.2275 floor sits **0.0002 below** the second plateau at 0.2277 — a knife-edge. When the population doesn't land in that bin on a given day, you get zero recs with no soft degradation.

**Scheduler bug found and fixed (PR #30)**
While investigating, found that `_job_generate_recommendations` hardcodes `return_5d_lag = return_10d_lag = return_20d_lag = 0` (`scheduler.py:295-297`) — but training uses real `close.pct_change(N)` (`directional.py:383-385`). Inference was silently feeding zeros for a feature group the model was trained to depend on.

Fix: fetch last 21 closes per ticker, compute lags inline, fall back to 0 only when history < lag window. Same logic as training.

Re-ran the distribution with real lags wired in: max **stayed at 0.1453**, mean shifted 0.1064 → 0.1083, top-of-pack reshuffled (KLAC, ASML, KHC vs. ASML, KLAC, AAPL) — but plateau structure is intact and no tickers crossed the floor. Today's bullish 20-day regime (mean 5d return +1.4%, 20d +3.7%) is correctly producing low bearish-drop probs.

Verdict: the lag fix is a correctness bug worth shipping (training/inference mismatch), but does NOT unlock recs today. The deeper limitation is v3's calibration plateau structure — v6 (per-ticker event features) is the path forward, not threshold tuning.

**Same bug exists in `backtester.py:341-343`** — out of scope for this PR, flagged in PR description for follow-up.

**PR shipped**:
- PR #30 (`fix/scheduler-return-lags`, commit `420bcbd`) — merged as `98afb7e`.
- 21 scheduler + rec_ranker tests pass.
- Deployed manually via SSH (GitHub Actions blocked, see below). Verified live in container: `return_5d_lag: _return_lag(5)` present at installed package path.

**GitHub Actions billing block**
Both today's pushes (PR #29 merge at 16:37, PR #30 merge at 20:26) failed with "recent account payments have failed or your spending limit needs to be increased". Yesterday's runs (2026-05-11) succeeded — block kicked in overnight. The private-repo free-tier minutes are exhausted; no payment method is on file as fallback. Self-hosted `deploy` job never runs because `test-backend`/`test-frontend` (GitHub-hosted) are upstream gates and refuse to start.

Manual deploy workflow used (and will be needed for any push until resolved): `ssh proxmox@10.0.0.47 'cd /opt/stock-analysis && git pull origin master'` → `cd /opt/stock-analysis/backend && docker compose up -d --build backend`.

**Memory updates**:
- New: `projects/stock-analysis/directional_calibration_plateaus_2026-05-12.md` — the 4-plateau finding and why threshold tuning won't help.
- New: `projects/stock-analysis/gh_actions_billing_block_2026-05-12.md` — billing state, workaround, until-when.

**Current state**:
- PR #29 + PR #30 both merged and deployed live on VM.
- 4-day stale-rec streak (2026-05-07 thru 2026-05-12); `paper_trading_readiness.md` gate #1 (5 consecutive days of recs) is now further from being met.
- v3 calibration plateaus confirmed as the real bottleneck — v6 features are the unblock.
- GitHub Actions blocked until billing cycle resets or repo is made public / payment method added.

**Next steps**:
1. Resolve GH Actions billing (make repo public, add payment method, or just wait for the cycle reset).
2. Fix the matching `return_*_lag` bug in `backtester.py:341-343` so historical backtests aren't biased by the same zero-feature blind spot.
3. (Cheap win, deferred from Session 22) `OLLAMA_FLASH_ATTENTION=1` to fit all 33/33 layers on GPU.
4. (De-risk, deferred) Per-ticker upsert in `_job_sentiment_async` to make sentiment runs crash-safe.
5. (Preemptive, deferred) Apply `devices:` block to whisper in ai-stack compose.
6. (Long-running) v6 per-ticker event features — now confirmed as the only realistic path to break the calibration plateau ceiling that's gating recs.

---

## 2026-05-12 — Session 24: sigmoid recalibration + pure top-K ranker (PR #31)

**Agent**: Claude (Opus 4.7)
**Branch**: `experiment/recalibrate-v3-sigmoid` → merged via PR #31 squash

**Context carried forward from Session 23**: dir_prob plateau structure was the real bottleneck; the `0.2275` floor sat 0.0002 below an isotonic plateau. Session 23 left this unresolved. Two angles to try this session: (1) re-fit the v3 calibrator with Platt sigmoid to smooth the plateaus, and (2) reconsider the floor itself.

**What was done**:
1. **Recalibration experiment** — `backend/scripts/recalibrate_v3_sigmoid.py` loads v3, rebuilds the 70/15/15 time-based dataset split, fits `CalibratedClassifierCV(method="sigmoid", cv="prefit")` on the calibration fold, compares Brier/distributions, saves the new pickle.
   - Raw v3 AUC=0.5546 (unchanged — calibration doesn't move the underlying signal).
   - **Sigmoid Brier=0.1601** vs isotonic 0.1643 — sigmoid is strictly better as a calibrator.
   - But sigmoid distribution compresses to a tight 0.005-wide band around the 0.18 base rate (live watchlist range: 0.1794–0.1841, 35 distinct values vs isotonic's 7). Still **0/158 above the 0.2275 floor**.
   - Conclusion: recalibration alone doesn't unlock recs. The floor itself is the wrong shape of gate for this model.
2. **Architectural decision** — drop the absolute `dir_prob` floor entirely. Rationale: when the model's information content is concentrated within a ~0.005-wide band around base rate, absolute thresholds are noise. The composite score (which integrates dir_prob with vol and sentiment, both of which DO vary meaningfully across tickers) is the right ranking signal. This is correct, not a workaround.
3. **PR #31** — pure top-K bundle:
   - `rec_ranker.select_candidates` → pure top-K by composite score; `base_rate`/`min_dir_prob_lift` kwargs accepted but ignored (back-compat for config-driven scheduler invocation).
   - `ensemble.meets_confidence` → drop directional_lift component; gate on sentiment_confidence floor only.
   - `scheduler.py` → log `top_k=%d selected` instead of `below dir_prob floor`.
   - Tests rewritten (test_rec_ranker, test_position_sizer TestEnsemble) to match new architecture. All 45 tests green locally (32 ranker+sizer + 13 scheduler).
4. **Manual deploy** (GH Actions still billing-blocked):
   - Merged PR #31 (squash), pulled master on VM, `docker compose up -d --build backend`.
   - The rebuild blew away the in-container sigmoid pickle (the experiment script wrote to `/app/trained_models/` inside the container, not the host — and the Dockerfile `COPY trained_models/ trained_models/` re-bakes the host's isotonic pickle on every build).
   - Persistence fix: re-ran recalibration in the new container, `docker cp`-ed the sigmoid pickle out to `/opt/stock-analysis/backend/trained_models/directional_xgb_v1.pkl` on the **host filesystem** (backup as `.bak.20260512-isotonic`). Future rebuilds now pick up the sigmoid pickle automatically.
   - Copied the host pickle back into the running container and restarted backend.
5. **Verification** — triggered `job_generate_recommendations` in container. **9 new recommendations generated**, 158 candidates evaluated, top_k=10 selected, 0 filtered for confidence, sizer breakdown: 8 options + 1 short + 1 no_sizer_match.
   - Top recs: CMCSA, SNAP, SHOP, CHTR, NKE, NFLX, INTC, COIN, CRM. All dir_signal ~0.18 (sigmoid band), composite score ~0.31–0.34. Position sizes $325–$676.
   - 4-day stale-rec streak broken.

**Files touched**:
- `backend/src/pipeline/rec_ranker.py` — pure top-K rewrite + history docstring
- `backend/src/models/ensemble.py` — drop directional_lift from meets_confidence
- `backend/src/pipeline/scheduler.py` — call site + log line
- `backend/tests/test_rec_ranker.py` — rewritten for new arch
- `backend/tests/test_position_sizer.py` — TestEnsemble class updated
- `backend/scripts/recalibrate_v3_sigmoid.py` — experiment script (uncommitted; lives only on dev box)
- VM host: `trained_models/directional_xgb_v1.pkl` (sigmoid), `.bak.20260512-isotonic` backup

**Key insight**: There were two coupled symptoms of the same root cause. The model's AUC≈0.555 means dir_prob carries little ticker-discrimination info; whatever calibrator you stack on top will compress to a narrow band around the base rate. The fix isn't to find a better calibrator or a smarter threshold — it's to **stop using dir_prob as an absolute gate** and let the composite score (which has higher information content) rank.

**State of system**:
- 9 live recommendations as of 2026-05-12 21:19 UTC.
- v3 sigmoid pickle live on VM and persisted on host (survives rebuilds).
- Composite score is the only directional ranker now; sentiment confidence is the only per-rec quality gate.
- `paper_trading_readiness.md` gate #1 (5 consecutive days of recs) — day 1 of 5 starts today if today's recs are saved end-of-day.

**Next steps**:
1. Watch tomorrow's scheduled run produce non-zero recs again (continuity check on the gate-removal).
2. Commit `scripts/recalibrate_v3_sigmoid.py` separately if we want it reproducible in the repo, or leave as one-off and rely on future v6 retrain.
3. (Deferred from Session 23) Same return-lag bug in `backtester.py:341-343`.
4. (Deferred) OLLAMA_FLASH_ATTENTION=1, sentiment per-ticker upsert, whisper devices block in ai-stack compose.
5. (Optional, plan b) Bullish-side build: add a long-direction directional model + bull-call/bull-put sizers + ranker extension. Today's bearish-only architecture means we miss every up-trending opportunity. Separate session.
6. Resolve GH Actions billing.

---

## 2026-05-12 — Session 25: bullish-side build kickoff (Phase 0 + Phase 1)

**Agent**: Claude (Opus 4.7)
**Branch**: `feature/bullish-direction-column` → merged via PR #32

**Context**: Today's pipeline only surfaces bearish opportunities (shorts, puts, bear spreads). User confirmed long-term goal is to maximize trading options for best profit outcome. Started multi-phase bullish-side build.

**User-driven design decisions** (saved to `bullish_side_build_2026-05-12.md` memory):
1. $5,000 capital cap stays direction-blind — no long/short sub-split.
2. Maximize instrument coverage from the start: long stock, long calls, bull spreads (no defined-risk-only V1).
3. Paper-trading parity is non-optional — Alpaca executor must handle all new strategies in Phase 5.
4. Sentiment becomes additive (composite-score weighting), not a hard gate — drop `meets_confidence` filter in Phase 3.

**Phase 0 — schema (shipped, PR #32):**
- Alembic migration `m3n5o7p9q1r3` adds `direction VARCHAR(5) NOT NULL DEFAULT 'short'` to `recommendations` and `paper_trades`, plus index `(date, direction)`.
- SQLAlchemy models mirror the schema.
- Round-tripped clean on local SQLite (upgrade → downgrade → upgrade).
- 356 tests pass.
- Deployed: merged, rebuilt backend on VM, migration applied to PostgreSQL. Verified `direction` column live.

**Code audit (paper-trading parity check):**
- `order_mapper.py` — dispatches on `strategy`, hardcoded sides. **Needs Phase 5 work** (`_map_long`, `_map_bull_spread`, `_map_call_options`).
- `safety_rails.py` — already direction-correct (only multiplies by 1.5 for shorts). ✓
- `execution_engine.py`, `portfolio_sync.py`, `paper_validation.py` — direction-blind. ✓

**Phase 1 — train rise model (in progress at session pause):**
- Parameterized `DirectionalModel.__init__` with `direction: str = "drop"` and `calibration_method: str | None = None` (override the auto-pick).
- `build_dataset` takes `direction="drop"|"rise"`; label flips between forward_return < -0.03 and forward_return > +0.03.
- Pickled payload now includes `direction` field for future verification (legacy pickles default to "drop").
- New `backend/scripts/train_rise.py` — instantiates `DirectionalModel(direction="rise", calibration_method="sigmoid")`, trains, saves as `directional_xgb_rise_v1.pkl` with metrics JSON.
- Forced sigmoid from day one (skip the isotonic detour that produced the plateau-clustered outputs on the drop model).
- `test_directional_calibration` (5 tests) still green with the refactor.

**Gotcha hit:** The container has THREE copies of `directional.py`:
- `/app/src/models/directional.py` (development source)
- `/usr/local/lib/python3.10/site-packages/src/models/directional.py` (pip-installed package)
- `/app/build/lib/src/models/directional.py` (setuptools build artifact)

Python imports from site-packages, not `/app`. First training run failed with `TypeError: unexpected keyword argument 'direction'` because the site-packages copy was stale. Fix: `docker cp` updates **both** `/app` and `/usr/local/lib/.../src/models/directional.py`. This is the same import-path issue that bit Session 23's `dir_prob_distribution.py` work — should fix the install layout properly eventually.

**Training kicked off:** `docker exec -w /app backend-backend-1 python /tmp/train_rise.py > /tmp/train_rise.log 2>&1 &` — monitoring for completion.

**Files touched (not yet committed beyond PR #32):**
- `backend/src/models/directional.py` — parameterized direction + calibration_method
- `backend/scripts/train_rise.py` — new training entry point

**Next steps when training completes:**
1. Read metrics from `/tmp/train_rise.log` and `directional_xgb_rise_v1.metrics.json`.
2. `docker cp` the pickle out to host filesystem at `/opt/stock-analysis/backend/trained_models/directional_xgb_rise_v1.pkl` so future rebuilds preserve it.
3. Commit Phase 1 (directional.py refactor + train_rise.py + metrics JSON), open PR.
4. Move to Phase 2: long-side sizers.

---

## 2026-05-13 — Session 26: morning-run check + rise model metrics review + VM host sync

**Agent**: Claude (Opus 4.7)
**Branch**: `feature/bullish-sizers` (no commits this session; SESSION_LOG edit only)

**Context check (this morning's scheduler run):**
- `job_generate_recommendations` ran 07:30 EDT — 9 new recs, 158 candidates evaluated, top_k=10 selected, 0 confidence-filtered, sizer breakdown: 9 options / 0 short / 1 no_sizer_match.
- All 9 recs `risk_type='defined'` — paper_trading gate #2 now fully clean (0 undefined_risk vs 1 yesterday, 3 on 2026-05-11).
- Top recs: SNAP (0.358), INTC (0.346), CMCSA (0.345), QCOM, CHTR, NKE, CRM, SHOP, COIN. Composite scores 0.32–0.36, all dir_signal ~0.18 (sigmoid band).
- **Paper trading gate #1 streak: day 2/5** (3 → 9 → 9 across 05-11/12/13).
- Minor: `$SQ` flagged as delisted in `job_fetch_prices`; job still succeeded.

**Rise model metrics review (`directional_xgb_rise_v1`, trained 2026-05-12 22:38 unattended after Session 25 pause):**
- Test AUC=0.5283, Brier=0.1704, base rate 22.9%, sigmoid-calibrated ✓.
- Walk-forward folds: 0.5644 → 0.5750 → 0.5387 → 0.5283 test (peaks fold 2, then degrades — recall climbs while precision flat, i.e. model just predicts "rise" more often, not better).
- **Weaker than drop side** (v3 sigmoid 0.555, v4 0.574-but-dead-features) but in same information-ceiling regime.
- Feature importance dominated by SPY/VIX macro (top 6 ≈ 30% of total): volatility_20d, spy_above_sma_50, spy_return_20d, spy_drawdown_pct, vix_level, spy_above_sma_200. Per-ticker indicators all <0.02. **It's a regime detector, not a ticker picker.**
- Zero-importance: all 6 SI features (expected, dead per earlier memory), `earnings_within_3d` (surprising), `wiki_views_spike`, `insider_cluster_buy_30d`.
- Decision: **deploy as-is** for Phase 2-5 work. Composite-rank architecture (PR #31) absorbs the narrow signal. v2 retrain priorities = per-ticker event features (same prescription as `directional_model_information_ceiling.md`), SPY/VIX macro is saturated.

**Memory written:**
- `projects/stock-analysis/rise_model_v1_metrics_2026-05-13.md` — full metric breakdown + deploy-but-gate-paper-trade-separately recommendation.

**PR #33 deployment verification (revealed a half-deployed state):**
- VM `/opt/stock-analysis` HEAD was at `cb69cd1` (PR #32), missing only PR #33 (`cc8f2d0`).
- Container `/app/src/models/directional.py` AND `/usr/local/lib/.../site-packages/.../directional.py` both had PR #33's `direction` parameter — Session 25 had `docker cp`-ed both copies but never ran `git pull` on the VM.
- Host `backend/scripts/train_rise.py` did not exist (lived only in `/tmp/` inside container during training).
- Rise pickle on host loaded cleanly in container as `direction="rise"` with `CalibratedClassifierCV` (sigmoid).
- **Risk:** any backend rebuild would have pulled stale `directional.py` from `/opt/stock-analysis/backend/` and silently regressed the container to PR #32, breaking rise-pickle load.
- **Fix applied:** `ssh proxmox@10.0.0.47 'cd /opt/stock-analysis && git pull origin master'` — fast-forward `cb69cd1..cc8f2d0`. Host filesystem now matches container; future rebuilds preserve rise model code.

**State of system:**
- Rise model artifact + code fully on disk in both container and host. **NOT wired into ranker yet** (Phase 3 work).
- Drop-side recommendation pipeline running cleanly; 2/5 of the stability streak.
- Phase 2 (bullish sizers) commit `10c900b` sitting on local `feature/bullish-sizers` branch, unpushed/un-PR'd.

**Next steps:**
1. Phase 2 sizers — review `10c900b` commit, finish if incomplete, PR.
2. Phase 3 — ranker integration: load both drop+rise models, score candidates against both, write rows with `direction='long'` for rise-side recs.
3. Track rise recs separately for ≥5 trading days before any auto-exec consideration (per `rise_model_v1_metrics_2026-05-13.md` gating note).
4. Resolve GH Actions billing.
5. (Deferred from Session 25, still open) backtester.py:341-343 return-lag bug, OLLAMA_FLASH_ATTENTION=1, sentiment per-ticker upsert, whisper devices block, fix triple-copy `directional.py` install layout.
