# P6-001: Watchlist management UI

**Status**: todo
**Phase**: 6
**Dependencies**: P4-001
**Estimated scope**: medium

## Description
Allow users to add/remove tickers from the watchlist via the UI instead of relying on the hardcoded list in config.py.

## Acceptance Criteria
- [ ] `Watchlist` DB model storing user-configured tickers
- [ ] API endpoints: GET /api/watchlist, POST /api/watchlist, DELETE /api/watchlist/{ticker}
- [ ] Seed watchlist from `default_watchlist` on first run
- [ ] Pipeline and scheduler use DB watchlist instead of config
- [ ] Frontend page: searchable ticker input, current watchlist with remove buttons
- [ ] Sector/category tags on tickers
- [ ] Bulk import (paste comma-separated tickers)

## Files to Create/Modify
- `backend/src/db/models.py` (add Watchlist model)
- `backend/src/api/routes/watchlist.py`
- `backend/src/config.py` (fallback to DB watchlist)
- `backend/src/pipeline/scheduler.py` (read from DB)
- `frontend/src/app/watchlist/page.tsx`
- `frontend/src/lib/api.ts` (add watchlist functions)
