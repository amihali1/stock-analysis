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
