"""Runtime-mutable trading settings backed by the system_settings table.

Settings here override the defaults in ``Settings`` (config.py) when present.
The fallback order is: DB row → environment/config default.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import SystemSetting


# Whitelist of trading settings exposed via the API. All other keys are rejected.
TRADING_KEYS: dict[str, type] = {
    "trading_mode": str,           # disabled | paper | live
    "auto_execute_enabled": bool,
    "min_score_threshold": float,       # bull-side exec floor
    "min_score_threshold_bear": float,  # bear-side exec floor (lower base rate)
    "max_daily_loss": float,
    "max_open_positions": int,
}

VALID_MODES = {"disabled", "paper", "live"}


def _coerce(raw: str, target: type) -> Any:
    if target is bool:
        return raw.lower() in ("true", "1", "yes", "on")
    if target is int:
        return int(float(raw))
    if target is float:
        return float(raw)
    return raw


def get_trading_settings(db: Session, defaults: Any | None = None) -> dict[str, Any]:
    """Return the current trading settings, merging DB overrides over config defaults.

    ``defaults`` lets callers pass an already-loaded ``Settings`` object so the
    same monkeypatch of ``get_settings`` flows through both layers.
    """
    settings = defaults if defaults is not None else get_settings()
    out: dict[str, Any] = {}
    for key in TRADING_KEYS:
        out[key] = getattr(settings, key)

    rows = db.query(SystemSetting).filter(SystemSetting.key.in_(TRADING_KEYS.keys())).all()
    for row in rows:
        out[row.key] = _coerce(row.value, TRADING_KEYS[row.key])
    return out


def update_trading_settings(db: Session, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply updates to the trading settings. Returns the new merged view."""
    for key, raw_value in updates.items():
        if key not in TRADING_KEYS:
            raise ValueError(f"Unknown trading setting: {key}")
        if key == "trading_mode" and raw_value not in VALID_MODES:
            raise ValueError(f"Invalid trading_mode: {raw_value}")

        value_str = str(raw_value).lower() if isinstance(raw_value, bool) else str(raw_value)
        existing = db.get(SystemSetting, key)
        if existing is None:
            db.add(SystemSetting(key=key, value=value_str))
        else:
            existing.value = value_str
    db.commit()
    return get_trading_settings(db)
