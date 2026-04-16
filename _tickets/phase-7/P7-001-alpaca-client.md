# P7-001: Alpaca client service

**Status**: done
**Phase**: 7
**Dependencies**: P3-001
**Estimated scope**: medium

## Description
Create a service layer wrapping the `alpaca-py` SDK for authentication, account info, and order submission. Support both paper and live environments via configuration.

## Acceptance Criteria
- [ ] Add `alpaca-py` to project dependencies
- [ ] Add Alpaca config to `Settings`: `alpaca_api_key`, `alpaca_secret_key`, `alpaca_base_url` (default to paper URL), `alpaca_trading_enabled` (default `false`)
- [ ] `AlpacaClient` class in `services/alpaca_client.py` with:
  - `get_account()` — returns buying power, equity, cash, day trade count
  - `get_positions()` — returns all open positions with current market value and unrealized P&L
  - `submit_order()` — submits market/limit/stop orders, returns order ID
  - `submit_bracket_order()` — submits entry + stop-loss + take-profit as OCO bracket
  - `cancel_order()` / `cancel_all_orders()`
  - `get_order()` / `get_orders()` — order status lookup
  - `is_market_open()` — checks trading clock
- [ ] Async-compatible (alpaca-py supports async)
- [ ] Connection test method for health endpoint
- [ ] Unit tests with mocked Alpaca API responses (10+ tests)

## Files to Create/Modify
- `backend/src/services/alpaca_client.py` (new)
- `backend/src/config.py` (add Alpaca settings)
- `backend/tests/test_alpaca_client.py` (new)
- `backend/pyproject.toml` (add alpaca-py dep)

## Notes
- Alpaca paper URL: `https://paper-api.alpaca.markets`
- Alpaca live URL: `https://api.alpaca.markets`
- The `alpaca_trading_enabled` flag is a top-level kill switch — if `false`, all order submission methods should raise an error
