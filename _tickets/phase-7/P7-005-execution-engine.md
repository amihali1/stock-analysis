# P7-005: Automated execution engine

**Status**: done
**Phase**: 7
**Dependencies**: P7-002, P7-003, P7-004
**Estimated scope**: large

## Description
Tie together the recommendation pipeline, order mapper, safety rails, and Alpaca client into an automated execution flow. When the scheduler generates new recommendations, eligible ones are automatically submitted as orders (if trading mode is enabled).

## Acceptance Criteria
- [ ] `ExecutionEngine` class in `services/execution_engine.py`:
  - `execute_recommendations()` — called after recommendation generation:
    1. Filter recommendations above score threshold (configurable, default 0.7)
    2. Check each against safety rails
    3. Map to Alpaca orders via OrderMapper
    4. Submit via AlpacaClient
    5. Log results to trading_log
  - `close_position()` — manual close of a specific position
  - `close_all_positions()` — emergency liquidation
- [ ] Scheduler integration: optionally run after the 7:30 AM recommendation job
- [ ] Execution config in Settings:
  - `auto_execute_enabled` (default `false`) — must be explicitly enabled
  - `min_score_threshold` (default 0.7)
  - `preferred_strategies` (default `["short", "options"]`)
- [ ] Alert integration: send Discord/Telegram notification on every order submitted, filled, or rejected
- [ ] API endpoints:
  - `POST /api/execute/recommendation/{id}` — manually execute a single recommendation
  - `POST /api/execute/close/{ticker}` — manually close a position
  - `POST /api/execute/emergency-close` — close all positions
  - `GET /api/execute/log` — execution history with outcomes
- [ ] Tests: full execution flow (mock Alpaca), safety rail rejection flow, manual execution, emergency close

## Files to Create/Modify
- `backend/src/services/execution_engine.py` (new)
- `backend/src/api/routes/execution.py` (new)
- `backend/src/pipeline/scheduler.py` (add execution job)
- `backend/src/config.py` (add execution settings)
- `backend/tests/test_execution_engine.py` (new)

## Notes
- `auto_execute_enabled` defaults to `false` — even with trading mode on, auto-execution requires a separate opt-in
- Emergency close should bypass the normal safety rails (it IS a safety mechanism)
- Consider: should there be a confirmation delay? (e.g., order queued for 60s before submission, cancelable via API)
