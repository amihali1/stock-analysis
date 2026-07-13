"""Live-money go/no-go gate evaluation (decision D014, 2026-07-13).

Each strategy arm goes live independently when it clears ALL of:
1. Evidence: >= MIN_CLOSED_TRADES closed paper trades since the arm's baseline
   AND >= MIN_CLEAN_WEEKS weeks since max(baseline, last recorded incident).
2. Performance: mean per-trade return > 0 AND, where a backtest win rate
   exists, paper win rate >= backtest win rate - WIN_RATE_TOLERANCE.

Baselines pin each arm to its current architecture — history from before a
strategy's current form (May shorts, pre-marketable-limit bull spreads) is
excluded by date rather than judgment.

"Clean weeks" cannot be derived from the DB: an incident is a human call
(pipeline produced wrong behavior needing manual correction). Record one by
setting system_settings key `live_gate_last_incident` to an ISO date; the
clean-week clock restarts there. Deploys and planned changes don't count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.db.models import PaperTrade, SystemSetting

MIN_CLOSED_TRADES = 20
MIN_CLEAN_WEEKS = 3.0
WIN_RATE_TOLERANCE = 0.10
INCIDENT_KEY = "live_gate_last_incident"


@dataclass(frozen=True)
class ArmSpec:
    name: str
    strategies: tuple[str, ...]
    baseline: date  # current-architecture start; earlier trades excluded
    # Backtest win rate the paper book must stay within WIN_RATE_TOLERANCE of.
    # None = no reliable backtest win rate exists for this arm (the money-layer
    # sweep reports expectancy, not per-trade win rate), so only the
    # mean-return-positive check applies.
    backtest_win_rate: float | None = None
    notes: str = ""


ARMS: tuple[ArmSpec, ...] = (
    ArmSpec(
        name="pair",
        strategies=("pair_short",),
        baseline=date(2026, 7, 10),  # first pair_short fills (per-direction floor fix)
        backtest_win_rate=0.58,  # bear-monetization sweep 2026-07-07, SPY-hedged pair
        notes="Market-neutral short + SPY hedge, 10-session exits.",
    ),
    ArmSpec(
        name="bull",
        strategies=("long", "bull_spread", "call_options"),
        baseline=date(2026, 7, 14),  # first marketable-limit MLEG fills
        backtest_win_rate=None,  # money-layer sweep has expectancy only
        notes="Long stock + debit spreads. Win-rate floor unset until a bull backtest win rate exists.",
    ),
)


@dataclass
class ArmResult:
    arm: str
    strategies: tuple[str, ...]
    baseline: str
    clean_since: str
    closed_trades: int = 0
    weeks_clean: float = 0.0
    mean_return: float | None = None
    win_rate: float | None = None
    win_rate_floor: float | None = None
    evidence_ok: bool = False
    performance_ok: bool = False
    ready: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "strategies": list(self.strategies),
            "baseline": self.baseline,
            "clean_since": self.clean_since,
            "closed_trades": self.closed_trades,
            "closed_trades_needed": MIN_CLOSED_TRADES,
            "weeks_clean": round(self.weeks_clean, 2),
            "weeks_clean_needed": MIN_CLEAN_WEEKS,
            "mean_return": round(self.mean_return, 5) if self.mean_return is not None else None,
            "win_rate": round(self.win_rate, 4) if self.win_rate is not None else None,
            "win_rate_floor": self.win_rate_floor,
            "evidence_ok": self.evidence_ok,
            "performance_ok": self.performance_ok,
            "ready": self.ready,
            "detail": self.detail,
        }


def _last_incident(db: Session) -> date | None:
    row = db.get(SystemSetting, INCIDENT_KEY)
    if row is None or not row.value:
        return None
    try:
        return date.fromisoformat(row.value.strip())
    except ValueError:
        return None


def evaluate_arm(db: Session, spec: ArmSpec, today: date | None = None) -> ArmResult:
    today = today or date.today()
    incident = _last_incident(db)
    clean_since = max(spec.baseline, incident) if incident else spec.baseline

    result = ArmResult(
        arm=spec.name,
        strategies=spec.strategies,
        baseline=spec.baseline.isoformat(),
        clean_since=clean_since.isoformat(),
        win_rate_floor=(
            round(spec.backtest_win_rate - WIN_RATE_TOLERANCE, 4)
            if spec.backtest_win_rate is not None else None
        ),
    )

    baseline_dt = datetime.combine(spec.baseline, datetime.min.time())
    # NULL pnl excluded: orphan-swept unfilled orders are closed rows but not
    # trades (see mleg fill-rate notes) and must not count as evidence.
    closed = (
        db.query(PaperTrade)
        .filter(
            PaperTrade.strategy.in_(spec.strategies),
            PaperTrade.status == "closed",
            PaperTrade.pnl.isnot(None),
            PaperTrade.opened_at >= baseline_dt,
        )
        .all()
    )

    result.closed_trades = len(closed)
    result.weeks_clean = max((today - clean_since).days, 0) / 7.0
    result.evidence_ok = (
        result.closed_trades >= MIN_CLOSED_TRADES
        and result.weeks_clean >= MIN_CLEAN_WEEKS
    )

    if closed:
        returns = [
            float(t.pnl) / float(t.position_size)
            for t in closed
            if t.position_size and float(t.position_size) > 0
        ]
        result.mean_return = sum(returns) / len(returns) if returns else None
        result.win_rate = sum(1 for t in closed if float(t.pnl) > 0) / len(closed)

    mean_ok = result.mean_return is not None and result.mean_return > 0
    wr_ok = (
        result.win_rate_floor is None
        or (result.win_rate is not None and result.win_rate >= result.win_rate_floor)
    )
    result.performance_ok = mean_ok and wr_ok
    result.ready = result.evidence_ok and result.performance_ok

    parts = [
        f"{result.closed_trades}/{MIN_CLOSED_TRADES} closes",
        f"week {result.weeks_clean:.1f}/{MIN_CLEAN_WEEKS:.0f} clean",
    ]
    if result.mean_return is not None:
        parts.append(f"mean {result.mean_return:+.2%}")
    if result.win_rate is not None:
        floor = f" (floor {result.win_rate_floor:.0%})" if result.win_rate_floor else ""
        parts.append(f"wr {result.win_rate:.0%}{floor}")
    result.detail = ", ".join(parts)
    return result


def evaluate_gates(db: Session, today: date | None = None) -> dict[str, Any]:
    """Evaluate every arm. Returns dict suitable for embedding in reports."""
    arms = [evaluate_arm(db, spec, today=today) for spec in ARMS]
    return {
        "decision": "D014 (2026-07-13)",
        "arms": [a.as_dict() for a in arms],
        "any_ready": any(a.ready for a in arms),
    }


def format_gates(gates: dict[str, Any]) -> str:
    """One line per arm for logs and the weekly report."""
    lines = ["Live-money gates (D014):"]
    for a in gates["arms"]:
        verdict = "READY" if a["ready"] else "NOT READY"
        lines.append(f"  {a['arm']:<5} {verdict:<10} {a['detail']}")
    return "\n".join(lines)
