# P6-005: Portfolio-level risk management

**Status**: done
**Phase**: 6
**Dependencies**: P5-001, P4-005
**Estimated scope**: large

## Description
Add portfolio-level risk controls: correlation-aware position limits, sector exposure tracking, and aggregate risk metrics.

## Acceptance Criteria
- [ ] Correlation matrix computed from recent price returns (rolling 60-day)
- [ ] Reject new positions if portfolio correlation with existing positions exceeds threshold
- [ ] Sector exposure tracking: max % of capital per sector (configurable, default 30%)
- [ ] Portfolio-level metrics: total exposure, total max loss, beta to SPY, aggregate Greeks
- [ ] Daily portfolio value tracking in DB
- [ ] Risk dashboard: sector allocation pie chart, correlation heatmap, exposure summary
- [ ] API endpoint: GET /api/portfolio/risk
- [ ] Position limit: max total open positions across all strategies

## Files to Create/Modify
- `backend/src/models/risk_manager.py`
- `backend/src/db/models.py` (add PortfolioSnapshot model)
- `backend/src/api/routes/portfolio.py`
- `frontend/src/app/portfolio/page.tsx`
- `frontend/src/components/CorrelationHeatmap.tsx`
- `frontend/src/components/SectorAllocation.tsx`
