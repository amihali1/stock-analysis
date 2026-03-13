# Design Decisions

Format: `[YYYY-MM-DD] DECISION: rationale`

## 2026-03-13 — Initial Architecture Decisions

### D001: FastAPI over Django/Flask
FastAPI for the backend. Async-native, auto-generates OpenAPI docs, lightweight. This is an internal tool, not a public API — Django's ORM/admin overhead is unnecessary.

### D002: PostgreSQL over SQLite for production
Concurrent writes from the data pipeline scheduler + reads from the API. SQLite's single-writer lock would bottleneck. SQLite is fine for local dev.

### D003: XGBoost for directional classifier
Interpretable feature importances (can see WHY a stock is flagged). Fast CPU training. Proven on tabular financial data. Start simple, upgrade later if needed.

### D004: PyTorch LSTM for volatility prediction
Need sequence modeling for time-series vol prediction. 2070 Super (8GB VRAM) handles this easily. PyTorch over TensorFlow for research-style iteration.

### D005: APScheduler over Celery
Single-machine deployment, no need for distributed task queue. APScheduler runs inside the FastAPI process. Revisit if we need distributed workers.

### D006: Time-based train/test splits only
CRITICAL for financial data. Random splits cause lookahead bias. Always split by date: train on older data, test on newer data. Walk-forward validation for backtesting.

### D007: lightweight-charts for frontend charting
TradingView's open-source library. Purpose-built for financial data, tiny bundle size, candlestick/volume support out of the box.

### D008: Structured JSON prompts for Ollama sentiment
No fine-tuning initially. Few-shot prompt engineering with strict JSON output format. Retry with stricter prompt on parse failure. Validate with Pydantic.

### D009: Agent memory in _memory/ directory
Markdown-based memory store so agents can read prior context and decisions without re-deriving everything. Saves tokens and prevents contradictory decisions across sessions.

### D010: Ticket-based task decomposition in _tickets/
Each phase broken into small, self-contained tickets. Prevents massive PRs. Each ticket has clear scope, acceptance criteria, and dependencies.
