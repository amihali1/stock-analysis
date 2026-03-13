# P4-005: Add paper trading log

**Status**: todo
**Phase**: 4
**Dependencies**: P4-002
**Estimated scope**: medium

## Description
Add ability to "take" a recommendation and track paper P&L over time.

## Acceptance Criteria
- [ ] "Take Trade" button on recommendation cards
- [ ] Paper trades stored in backend DB (new `paper_trades` table)
- [ ] API endpoints: POST /api/paper-trades, GET /api/paper-trades
- [ ] Dashboard shows open paper trades with current P&L
- [ ] Auto-close logic when stop-loss or target hit
- [ ] Summary stats: win rate, average return, total P&L

## Files to Create/Modify
- `backend/src/db/models.py` (add PaperTrade model)
- `backend/src/api/routes/paper_trades.py`
- `frontend/src/app/paper-trades/page.tsx`
- `frontend/src/components/PaperTradeButton.tsx`
