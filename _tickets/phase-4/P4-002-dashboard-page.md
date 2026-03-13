# P4-002: Build dashboard home page

**Status**: done
**Phase**: 4
**Dependencies**: P4-001
**Estimated scope**: medium

## Description
Build the main dashboard showing today's top recommendations across both strategies.

## Acceptance Criteria
- [ ] Recommendations table sortable by score
- [ ] Filter tabs: All / Shorts / Options
- [ ] Each row shows: ticker, strategy, score, sentiment, position size, max loss
- [ ] Color coding: green for high confidence, yellow for medium, red for caution
- [ ] Click row to navigate to `/analysis/[ticker]`
- [ ] Auto-refresh every 5 minutes
- [ ] Loading and empty states

## Files to Create/Modify
- `frontend/src/app/page.tsx`
- `frontend/src/components/RecommendationCard.tsx`
- `frontend/src/components/RecommendationTable.tsx`
