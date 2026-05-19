"""Tests for runtime-mutable trading settings."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, SystemSetting
from src.services.trading_settings import (
    get_trading_settings,
    update_trading_settings,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_defaults_from_config(db):
    out = get_trading_settings(db)
    # Defaults from src.config.Settings
    assert out["trading_mode"] == "disabled"
    assert out["auto_execute_enabled"] is False
    assert out["max_daily_loss"] == 200.0


def test_db_overrides_config(db):
    db.add(SystemSetting(key="trading_mode", value="paper"))
    db.add(SystemSetting(key="auto_execute_enabled", value="true"))
    db.commit()

    out = get_trading_settings(db)
    assert out["trading_mode"] == "paper"
    assert out["auto_execute_enabled"] is True


def test_update_creates_rows(db):
    out = update_trading_settings(db, {"trading_mode": "paper", "min_score_threshold": 0.85})
    assert out["trading_mode"] == "paper"
    assert out["min_score_threshold"] == 0.85
    assert db.query(SystemSetting).count() == 2


def test_update_replaces_existing(db):
    update_trading_settings(db, {"trading_mode": "paper"})
    update_trading_settings(db, {"trading_mode": "live"})
    out = get_trading_settings(db)
    assert out["trading_mode"] == "live"
    assert db.query(SystemSetting).filter_by(key="trading_mode").count() == 1


def test_update_rejects_unknown_key(db):
    with pytest.raises(ValueError, match="Unknown trading setting"):
        update_trading_settings(db, {"banana": "yellow"})


def test_update_rejects_invalid_mode(db):
    with pytest.raises(ValueError, match="Invalid trading_mode"):
        update_trading_settings(db, {"trading_mode": "yolo"})


class TestEffectivePerTradeCap:
    """`effective_per_trade_cap` derives per-trade cap from
    `daily_capital_cap × max_position_ratio` when ratio > 0, else falls back
    to `max_position_size`. Locks the auto-scale behavior so dropping the
    daily cap for live trading also shrinks per-trade — without this,
    cap=$1000 with stale max_position_size=$1000 fills the pool with one
    position."""

    def test_falls_back_to_max_position_size_when_ratio_zero(self):
        """Ratio=0 = legacy mode: read max_position_size verbatim. Today's
        prod config (ratio default 0.0)."""
        from src.config import Settings
        s = Settings(max_position_size=1000.0, max_position_ratio=0.0,
                     daily_capital_cap=5000.0)
        assert s.effective_per_trade_cap == 1000.0

    def test_ratio_derives_from_cap(self):
        """Ratio>0 = derive from cap. $1k cap × 0.25 = $250/trade."""
        from src.config import Settings
        s = Settings(max_position_size=1000.0, max_position_ratio=0.25,
                     daily_capital_cap=1000.0)
        assert s.effective_per_trade_cap == 250.0

    def test_ratio_overrides_legacy_max_position_size(self):
        """When ratio>0, max_position_size is IGNORED — ratio wins. Prevents
        the trap of a stale absolute value silently capping at $1000 after
        the user reduces cap to $1000 for live mode."""
        from src.config import Settings
        s = Settings(max_position_size=1000.0, max_position_ratio=0.20,
                     daily_capital_cap=1000.0)
        assert s.effective_per_trade_cap == 200.0


def test_bool_coercion_round_trip(db):
    update_trading_settings(db, {"auto_execute_enabled": True})
    out = get_trading_settings(db)
    assert out["auto_execute_enabled"] is True

    update_trading_settings(db, {"auto_execute_enabled": False})
    out = get_trading_settings(db)
    assert out["auto_execute_enabled"] is False
