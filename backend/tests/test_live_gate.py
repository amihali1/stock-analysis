"""Live-money go/no-go gate evaluation (D014, 2026-07-13).

Locks the per-arm gate rules: evidence bar (>=20 closes since the arm's
baseline AND >=3 clean weeks), performance bar (mean return > 0, win rate
within tolerance of backtest where a baseline exists), pre-baseline and
NULL-pnl exclusions, and the incident clock reset via system_settings.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, PaperTrade, Stock, SystemSetting
from src.services.live_gate import (
    ARMS,
    INCIDENT_KEY,
    ArmSpec,
    evaluate_arm,
    evaluate_gates,
    format_gates,
)

BASELINE = date(2026, 7, 10)
PAIR_SPEC = ArmSpec(
    name="pair", strategies=("pair_short",), baseline=BASELINE, backtest_win_rate=0.58,
)
TODAY = BASELINE + timedelta(weeks=4)  # 4 clean weeks after baseline


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Stock(ticker="TEST"))
    s.commit()
    yield s
    s.close()


def _seed_trades(db, n: int, wins: int, opened: date = BASELINE, pnl_win=10.0, pnl_loss=-10.0):
    for i in range(n):
        db.add(PaperTrade(
            ticker="TEST", strategy="pair_short", direction="short", status="closed",
            entry_price=100.0, position_size=1000.0,
            pnl=pnl_win if i < wins else pnl_loss,
            opened_at=datetime.combine(opened, datetime.min.time()),
            closed_at=datetime.combine(opened + timedelta(days=14), datetime.min.time()),
        ))
    db.commit()


class TestEvidenceBar:
    def test_ready_when_all_gates_clear(self, db):
        # 25 closes, 60% wins, mean positive, 4 clean weeks
        _seed_trades(db, 25, wins=15)
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.evidence_ok and r.performance_ok and r.ready

    def test_too_few_closes_not_ready(self, db):
        _seed_trades(db, 19, wins=12)
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.evidence_ok is False
        assert r.ready is False

    def test_too_few_clean_weeks_not_ready(self, db):
        _seed_trades(db, 25, wins=15)
        r = evaluate_arm(db, PAIR_SPEC, today=BASELINE + timedelta(weeks=2))
        assert r.evidence_ok is False

    def test_pre_baseline_trades_excluded(self, db):
        # 25 winners opened BEFORE the baseline must not count
        _seed_trades(db, 25, wins=25, opened=BASELINE - timedelta(days=30))
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.closed_trades == 0
        assert r.ready is False

    def test_null_pnl_closes_excluded(self, db):
        # Orphan-swept unfilled orders: closed rows with NULL pnl are not trades
        _seed_trades(db, 20, wins=13)
        for _ in range(10):
            db.add(PaperTrade(
                ticker="TEST", strategy="pair_short", direction="short", status="closed",
                entry_price=100.0, position_size=1000.0, pnl=None,
                opened_at=datetime.combine(BASELINE, datetime.min.time()),
            ))
        db.commit()
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.closed_trades == 20

    def test_incident_resets_clean_clock(self, db):
        _seed_trades(db, 25, wins=15)
        incident = TODAY - timedelta(weeks=1)
        db.add(SystemSetting(key=INCIDENT_KEY, value=incident.isoformat()))
        db.commit()
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.weeks_clean == pytest.approx(1.0)
        assert r.evidence_ok is False
        assert r.clean_since == incident.isoformat()


class TestPerformanceBar:
    def test_win_rate_below_floor_not_ready(self, db):
        # 45% < floor 48% even though mean could be positive with skewed pnl
        _seed_trades(db, 20, wins=9, pnl_win=50.0, pnl_loss=-10.0)
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.mean_return > 0
        assert r.win_rate < 0.48
        assert r.performance_ok is False

    def test_negative_mean_not_ready(self, db):
        # 55% wins but losses dwarf wins -> negative expectancy
        _seed_trades(db, 20, wins=11, pnl_win=5.0, pnl_loss=-50.0)
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.win_rate >= 0.48
        assert r.mean_return < 0
        assert r.performance_ok is False

    def test_no_win_rate_floor_when_backtest_unknown(self, db):
        spec = ArmSpec(name="bull", strategies=("pair_short",), baseline=BASELINE,
                       backtest_win_rate=None)
        # 40% win rate would fail the pair floor, but bull has no floor
        _seed_trades(db, 20, wins=8, pnl_win=40.0, pnl_loss=-10.0)
        r = evaluate_arm(db, spec, today=TODAY)
        assert r.win_rate_floor is None
        assert r.performance_ok is True
        assert r.ready is True

    def test_zero_trades_not_ready(self, db):
        r = evaluate_arm(db, PAIR_SPEC, today=TODAY)
        assert r.mean_return is None
        assert r.performance_ok is False


class TestGatesSummary:
    def test_evaluate_gates_covers_all_arms(self, db):
        gates = evaluate_gates(db, today=TODAY)
        assert {a["arm"] for a in gates["arms"]} == {spec.name for spec in ARMS}
        assert gates["any_ready"] is False

    def test_format_gates_one_line_per_arm(self, db):
        gates = evaluate_gates(db, today=TODAY)
        text = format_gates(gates)
        assert "NOT READY" in text
        assert len(text.splitlines()) == 1 + len(ARMS)
