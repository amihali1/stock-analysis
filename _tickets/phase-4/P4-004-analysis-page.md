# P4-004: Build analysis deep-dive page

**Status**: todo
**Phase**: 4
**Dependencies**: P4-003
**Estimated scope**: medium

## Description
Build the per-ticker analysis page showing full signal breakdown.

## Acceptance Criteria
- [ ] Route: `/analysis/[ticker]`
- [ ] Stock chart with indicators
- [ ] Signal breakdown: directional score, vol prediction vs implied vol, sentiment score
- [ ] Sentiment history chart (score over time)
- [ ] Position sizing details for both strategies
- [ ] Recent headlines used for sentiment
- [ ] Model confidence indicators

## Files to Create/Modify
- `frontend/src/app/analysis/[ticker]/page.tsx`
- `frontend/src/components/SentimentGauge.tsx`
- `frontend/src/components/SignalBreakdown.tsx`
- `frontend/src/components/PositionSizer.tsx`
