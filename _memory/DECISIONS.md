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

## 2026-04-14 — Risk Management Strategy Update

### D011: $1,000 max trade cost (down from $5,000)
Focus on low-volume, low-cost trades. Every position (premium, margin, total cost) must stay under $1,000. This forces discipline and limits downside exposure per trade.

### D012: High-confidence-only trade filtering
Only surface trades where models agree with high confidence. The goal is NOT to find every opportunity — it's to only take trades with a strong edge. Marginal setups get skipped entirely. Fewer trades, higher win rate.

### D013: Prefer defined-risk strategies
Default to strategies with capped max loss (vertical spreads, cash-secured puts, debit spreads) over naked shorts or uncovered options. The max loss must be known and bounded before entry.

## 2026-07-13 — Live-Money Go/No-Go Criteria

### D014: Per-arm live-money gates (user decision, 2026-07-13)
Each strategy arm (pair_short bear book; long/bull_spread bull book) goes live independently when it clears ALL of:

1. **Evidence bar**: ≥20 closed paper trades in the arm AND ≥3 calendar weeks
   of clean operation (no pipeline incident requiring manual correctness fix;
   deploys and planned changes don't reset the clock — silent failures do).
   Evidence windows start at the arm's current-architecture baseline:
   pair book 2026-07-10, bull book 2026-07-14 (first marketable-limit fills).
   Pre-baseline history (May shorts n=5 0% hit, legacy book −$1,978) is excluded.
2. **Pass bar**: arm's mean 10-day return > 0 AND win rate within 10 points of
   its backtest (pair: ≥48% vs 58% backtest; bull: vs money-layer sweep
   baseline). Guards against both losing outright and winning on variance
   while underperforming design — the failure mode that killed the May shorts
   (0% live vs 25-30% backtest).
3. **Go-live scale**: $1k aggregate / $250 per trade (the designed live mode:
   fractional routing, max_ticker_price, per-trade cap all built for this).
   Scale-up only after 4 clean live weeks at $1k.

**Post-live kill rules** (defaults, revisit at go-live):
- Existing $200 max_daily_loss stays.
- Live aggregate drawdown ≤ −$300 (30% of book) → kill switch off, arm back
  to paper for ≥2 weeks with a written root-cause before retry.
- Any silent pipeline failure discovered while live → immediate paper, no
  threshold.

**Earliest gate evaluation**: pair book ~2026-08-03 (20 closes by ~7/30,
3 clean weeks from 7/13 baseline-reset), bull book ~2026-08-05. Weekly
ValidationReport is the measurement source.
