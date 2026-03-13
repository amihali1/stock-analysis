# P3-001: Build FastAPI routes

**Status**: todo
**Phase**: 3
**Dependencies**: P2-003
**Estimated scope**: medium

## Description
Expose recommendations, analysis, and health data as REST API endpoints.

## Acceptance Criteria
- [ ] `GET /api/health` — DB status, Ollama reachable, last pipeline run timestamp
- [ ] `GET /api/recommendations?strategy=short|options&limit=10` — top scored recommendations
- [ ] `GET /api/analysis/{ticker}` — full analysis: features, sentiment history, model scores, price chart data
- [ ] `GET /api/tickers` — list of tracked tickers with latest data timestamp
- [ ] Pydantic response models for all endpoints
- [ ] CORS middleware configured for frontend origin
- [ ] OpenAPI docs accessible at `/docs`

## Files to Create/Modify
- `backend/src/api/routes/recommendations.py`
- `backend/src/api/routes/analysis.py`
- `backend/src/api/routes/health.py`
- `backend/src/main.py` (register routes)
