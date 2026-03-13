# P3-002: Set up APScheduler cron jobs

**Status**: todo
**Phase**: 3
**Dependencies**: P3-001, P0-005, P1-002
**Estimated scope**: small

## Description
Configure APScheduler to run the daily pipeline automatically during market hours.

## Acceptance Criteria
- [ ] Scheduler starts with FastAPI app (lifespan event)
- [ ] Cron jobs (Eastern Time):
  - 6:00 AM: fetch new price data
  - 6:30 AM: compute technical indicators
  - 7:00 AM: fetch headlines + run sentiment analysis
  - 7:30 AM: run ML models + generate recommendations
- [ ] Each job logs start/end time and success/failure
- [ ] Failed job doesn't block subsequent jobs
- [ ] `GET /api/health` shows last run time for each job

## Files to Create/Modify
- `backend/src/pipeline/scheduler.py`
- `backend/src/main.py` (add lifespan)
