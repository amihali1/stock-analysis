# Stock Analysis Platform

## Project Overview
Locally-hosted stock analysis platform with ML-powered quantitative analysis and LLM sentiment analysis. Provides short and options recommendations with $5,000 max buy-in constraint.

## Agent Workflow
1. **Before any work**: Read `_memory/MEMORY_INDEX.md` and relevant memory files
2. **Pick a ticket**: Check `_tickets/` for `todo` tickets whose dependencies are all `done`
3. **Branch**: Create branch `ticket/TICKET-ID` (e.g., `ticket/P0-003`)
4. **Work**: Implement the ticket's acceptance criteria
5. **Update ticket**: Set status to `done`
6. **Update session log**: Add entry to `_memory/SESSION_LOG.md`
7. **Commit**: Small, focused commits per ticket

## Tech Stack
- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy / Alembic
- **ML**: XGBoost, PyTorch (LSTM)
- **Sentiment**: Ollama (local LLM at 10.0.0.47:11434)
- **Database**: PostgreSQL (prod), SQLite (dev)
- **Frontend**: Next.js 15 / TypeScript / Tailwind / lightweight-charts

## Key Conventions
- Time-based train/test splits ONLY for financial data (never random)
- Every recommendation must include stop-loss and max loss in dollars
- **$1,000 max cost per trade** (total premium, margin, or position cost)
- **Risk-first filtering**: Only surface trades with high probability of profit — skip marginal setups
- **Minimum confidence threshold**: Models must agree with high confidence before a trade is recommended
- Prefer defined-risk strategies (e.g., vertical spreads, cash-secured puts) over naked/unlimited-risk positions
- Cache API data aggressively in the database
- Log raw LLM responses for debugging

## Directory Structure
- `_memory/` — Agent memory store (read before working)
- `_tickets/` — Task tickets organized by phase
- `backend/` — Python FastAPI backend + ML pipeline
- `frontend/` — Next.js dashboard
- `scripts/` — Deployment and utility scripts
