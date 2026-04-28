"""API routes for runtime-mutable trading settings and safety rail status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services.safety_rails import TradingSafetyRails
from src.services.trading_settings import (
    VALID_MODES,
    get_trading_settings,
    update_trading_settings,
)

router = APIRouter()

LIVE_CONFIRM_TOKEN = "CONFIRM"


class TradingSettingsUpdate(BaseModel):
    trading_mode: str | None = None
    auto_execute_enabled: bool | None = None
    min_score_threshold: float | None = None
    max_daily_loss: float | None = None
    max_open_positions: int | None = None
    confirm: str | None = None  # required when switching to "live"


@router.get("/trading/settings")
def read_trading_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_trading_settings(db)


@router.put("/trading/settings")
def write_trading_settings(
    payload: TradingSettingsUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    confirm = updates.pop("confirm", None)

    if updates.get("trading_mode") == "live":
        if confirm != LIVE_CONFIRM_TOKEN:
            raise HTTPException(
                status_code=400,
                detail=f"Switching to live trading requires confirm='{LIVE_CONFIRM_TOKEN}'",
            )

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        return update_trading_settings(db, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/trading/safety-status")
def safety_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    rails = TradingSafetyRails(db)
    return rails.safety_status()


@router.get("/trading/modes")
def list_modes() -> dict[str, list[str]]:
    return {"modes": sorted(VALID_MODES)}
