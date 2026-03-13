# P4-003: Build stock chart component

**Status**: todo
**Phase**: 4
**Dependencies**: P4-001
**Estimated scope**: medium

## Description
Build a reusable stock chart component using lightweight-charts (TradingView open-source).

## Acceptance Criteria
- [ ] `StockChart` component wraps `lightweight-charts`
- [ ] Candlestick chart with volume histogram
- [ ] Overlay: SMA lines (50, 200)
- [ ] Overlay: Bollinger Bands
- [ ] Responsive sizing
- [ ] Dark theme styled
- [ ] Used on `/analysis/[ticker]` page

## Files to Create/Modify
- `frontend/src/components/StockChart.tsx`
- `frontend/src/app/analysis/[ticker]/page.tsx`
