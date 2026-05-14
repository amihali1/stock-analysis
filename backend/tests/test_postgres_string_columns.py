"""Postgres-backed integration tests for enum-like string columns.

These exist because the unit suite runs on SQLite, and SQLite silently ignores
`VARCHAR(N)` length constraints. Postgres enforces them and raises
`StringDataRightTruncation` on overflow. On 2026-05-14 the production scheduler
shipped Phase 5 strategy labels (`bull_spread` (11), `call_options` (12)) into
`recommendations.strategy` which was still declared `String(10)` — every row of
that morning's batch rolled back in prod, while the 405-test SQLite suite was
green pre-merge. This test class catches the same family of bug at PR time.

Mechanism: spin up an ephemeral Postgres container via testcontainers, build
the schema from the current SQLAlchemy models (`Base.metadata.create_all`), and
attempt to insert each known-valid enum-string value. Any too-narrow column
raises and the test fails.

Skipped automatically when testcontainers / docker is unavailable so the rest
of the suite still runs in minimal environments. To run locally:
    pytest backend/tests/test_postgres_string_columns.py -v
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

testcontainers = pytest.importorskip(
    "testcontainers.postgres",
    reason="testcontainers[postgres] not installed — pip install 'testcontainers[postgres]'",
)
PostgresContainer = testcontainers.PostgresContainer

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, Recommendation, PaperTrade, Stock


# Source of truth for valid strategy labels lives in
# `backend/src/services/order_mapper.py` (the `strategy_label=` kwargs at each
# branch). If a new strategy is added there, mirror it here — and bump the
# column width if needed.
VALID_STRATEGY_VALUES = [
    "short",         # bearish stock short
    "options",       # bearish put (single-leg)
    "spread",        # bearish vertical spread
    "long",          # bullish stock long
    "call_options",  # bullish call (single-leg)
    "bull_spread",   # bullish vertical spread
]

VALID_DIRECTION_VALUES = ["long", "short"]
VALID_RISK_TYPE_VALUES = ["defined", "undefined"]
VALID_PAPER_TRADE_STATUS_VALUES = ["open", "closed"]
VALID_OPTION_TYPE_VALUES = ["call", "put"]


@pytest.fixture(scope="module")
def postgres_engine():
    """Module-scoped ephemeral Postgres + schema from current models."""
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:
        pytest.skip(f"Could not start postgres container (docker unreachable?): {exc}")
    try:
        engine = create_engine(container.get_connection_url())
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        container.stop()


@pytest.fixture
def db_session(postgres_engine):
    """Per-test session that rolls back at teardown to keep cases isolated."""
    with Session(postgres_engine) as session:
        session.begin()
        try:
            yield session
        finally:
            session.rollback()


def _seed_stock(session: Session, ticker: str = "TEST") -> None:
    if session.query(Stock).filter_by(ticker=ticker).first() is None:
        session.merge(Stock(ticker=ticker, name="Test Stock"))
        session.flush()


@pytest.mark.parametrize("strategy", VALID_STRATEGY_VALUES)
def test_recommendation_strategy_fits(db_session, strategy):
    _seed_stock(db_session)
    db_session.add(
        Recommendation(
            ticker="TEST",
            date=date(2026, 5, 14),
            direction="long" if strategy in ("long", "call_options", "bull_spread") else "short",
            strategy=strategy,
            score=0.5,
            risk_type="defined",
        )
    )
    db_session.flush()  # raises StringDataRightTruncation here if narrow


@pytest.mark.parametrize("strategy", VALID_STRATEGY_VALUES)
def test_paper_trade_strategy_fits(db_session, strategy):
    _seed_stock(db_session)
    db_session.add(
        PaperTrade(
            ticker="TEST",
            direction="long" if strategy in ("long", "call_options", "bull_spread") else "short",
            strategy=strategy,
            status="open",
            entry_price=100.0,
            opened_at=datetime(2026, 5, 14, 12, 0, 0),
        )
    )
    db_session.flush()


@pytest.mark.parametrize("direction", VALID_DIRECTION_VALUES)
def test_recommendation_direction_fits(db_session, direction):
    _seed_stock(db_session)
    db_session.add(
        Recommendation(
            ticker="TEST",
            date=date(2026, 5, 14),
            direction=direction,
            strategy="long" if direction == "long" else "short",
            score=0.5,
        )
    )
    db_session.flush()


@pytest.mark.parametrize("risk_type", VALID_RISK_TYPE_VALUES)
def test_recommendation_risk_type_fits(db_session, risk_type):
    _seed_stock(db_session)
    db_session.add(
        Recommendation(
            ticker="TEST",
            date=date(2026, 5, 14),
            direction="short",
            strategy="short",
            score=0.5,
            risk_type=risk_type,
        )
    )
    db_session.flush()


@pytest.mark.parametrize("status", VALID_PAPER_TRADE_STATUS_VALUES)
def test_paper_trade_status_fits(db_session, status):
    _seed_stock(db_session)
    db_session.add(
        PaperTrade(
            ticker="TEST",
            direction="short",
            strategy="short",
            status=status,
            entry_price=100.0,
        )
    )
    db_session.flush()


@pytest.mark.parametrize("option_type", VALID_OPTION_TYPE_VALUES)
def test_recommendation_option_type_fits(db_session, option_type):
    _seed_stock(db_session)
    db_session.add(
        Recommendation(
            ticker="TEST",
            date=date(2026, 5, 14),
            direction="long" if option_type == "call" else "short",
            strategy="call_options" if option_type == "call" else "options",
            score=0.5,
            option_type=option_type,
        )
    )
    db_session.flush()
