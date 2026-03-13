# P0-002: Finalize and test database models

**Status**: todo
**Phase**: 0
**Dependencies**: P0-001
**Estimated scope**: medium

## Description
Review the SQLAlchemy models in `backend/src/db/models.py`, ensure they match the architecture spec, and run Alembic to generate the initial migration. Verify tables are created in SQLite (dev mode).

## Acceptance Criteria
- [ ] All 6 core tables created: stocks, price_history, technical_indicators, sentiment_scores, model_predictions, recommendations
- [ ] Alembic initial migration generated and applied
- [ ] Can insert and query a test row in each table
- [ ] Foreign key relationships work correctly
- [ ] Indexes on (ticker, date) for time-series queries

## Files to Create/Modify
- `backend/src/db/models.py` (review/finalize)
- `backend/src/db/session.py` (review/finalize)
- `backend/alembic/versions/` (generated migration)
- `backend/alembic.ini` (verify config)

## Notes
Use SQLite for local dev (`sqlite:///./stock_analysis.db`). PostgreSQL config is for homelab deployment.
