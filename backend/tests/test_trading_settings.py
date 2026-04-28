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


def test_bool_coercion_round_trip(db):
    update_trading_settings(db, {"auto_execute_enabled": True})
    out = get_trading_settings(db)
    assert out["auto_execute_enabled"] is True

    update_trading_settings(db, {"auto_execute_enabled": False})
    out = get_trading_settings(db)
    assert out["auto_execute_enabled"] is False
