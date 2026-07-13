"""Parse legs_json (persisted on recommendations and paper trades) into API leg models.

legs_json carries one of two shapes depending on strategy:
- option legs (spread/bull_spread): option_type/action/strike/premium/contracts
  (+ bid/ask since 2026-07-13, not exposed in the API)
- stock legs (pair_short): leg("short"|"hedge")/ticker/qty/entry

Both parsers are lenient: malformed rows are skipped, a fully unparseable
payload returns None rather than failing the endpoint.
"""

from __future__ import annotations

import json
import logging

from src.api.schemas import SpreadLegResponse, StockLegResponse

logger = logging.getLogger(__name__)


def _load_leg_list(legs_json: str | None) -> list[dict] | None:
    if not legs_json:
        return None
    try:
        raw = json.loads(legs_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse legs_json")
        return None
    if not isinstance(raw, list):
        return None
    return [leg for leg in raw if isinstance(leg, dict)]


def parse_option_legs(legs_json: str | None) -> list[SpreadLegResponse] | None:
    raw = _load_leg_list(legs_json)
    if not raw:
        return None
    legs: list[SpreadLegResponse] = []
    for leg in raw:
        try:
            legs.append(SpreadLegResponse(
                option_type=str(leg["option_type"]),
                action=str(leg["action"]),
                strike=float(leg["strike"]),
                premium=float(leg["premium"]) if leg.get("premium") is not None else None,
                contracts=int(leg["contracts"]) if leg.get("contracts") is not None else None,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return legs or None


def parse_stock_legs(legs_json: str | None) -> list[StockLegResponse] | None:
    raw = _load_leg_list(legs_json)
    if not raw:
        return None
    legs: list[StockLegResponse] = []
    for leg in raw:
        try:
            legs.append(StockLegResponse(
                leg=str(leg["leg"]),
                ticker=str(leg["ticker"]),
                qty=float(leg["qty"]),
                entry=float(leg["entry"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return legs or None
