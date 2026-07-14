"""Compare Alpaca paper-trading outcomes against backtester predictions.

Used to validate that the live execution path produces results consistent with
historical backtests before flipping the trading mode to live.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from src.db.models import PaperTrade, PriceHistory
from src.models.backtester import Backtester

logger = logging.getLogger(__name__)

# Metric names compared between paper and backtest results.
COMPARED_METRICS = ("win_rate", "avg_pnl", "sharpe_ratio", "max_drawdown")
DIVERGENCE_THRESHOLD = 0.10  # >10% relative difference is flagged


def _compute_paper_metrics(trades: list[PaperTrade], hold_days: int = 5) -> dict[str, Any]:
    """Compute summary metrics for a list of closed paper trades."""
    closed = [t for t in trades if t.status == "closed" and t.pnl is not None]
    if not closed:
        return {
            "num_trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "total_pnl": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
        }

    pnls = [float(t.pnl) for t in closed]
    returns = []
    for t in closed:
        size = float(t.position_size or 0)
        if size > 0:
            returns.append(float(t.pnl) / size)

    winners = [p for p in pnls if p > 0]
    win_rate = len(winners) / len(pnls)

    if len(returns) > 1 and np.std(returns) > 0:
        sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252 / max(hold_days, 1))
    else:
        sharpe = 0.0

    # Equity curve sorted by close time, then max drawdown
    by_close = sorted(closed, key=lambda t: t.closed_at or datetime.min)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in by_close:
        cum += float(t.pnl)
        if cum > peak:
            peak = cum
        max_dd = max(max_dd, peak - cum)

    return {
        "num_trades": len(pnls),
        "win_rate": round(win_rate, 4),
        "avg_pnl": round(float(np.mean(pnls)), 2),
        "total_pnl": round(sum(pnls), 2),
        "sharpe_ratio": round(float(sharpe), 4),
        "max_drawdown": round(max_dd, 2),
    }


def _relative_diff(paper: float, backtest: float) -> float:
    """Relative difference normalized by max(|paper|, |backtest|).

    Returns 0 when both inputs are effectively zero (numerator wins).
    """
    denom = max(abs(paper), abs(backtest), 1e-9)
    return abs(paper - backtest) / denom


def _diagnose_divergence(metric: str, paper_val: float, bt_val: float) -> str:
    """Return a one-line guess at the cause of a divergent metric."""
    if metric == "avg_pnl" and paper_val < bt_val:
        return "Likely slippage on fills or unfavorable stop-loss timing in paper."
    if metric == "win_rate" and paper_val < bt_val:
        return "Possible adverse fill prices or premature stop-outs vs idealized backtest."
    if metric == "sharpe_ratio" and paper_val < bt_val:
        return "Higher realized variance than backtest — check fill quality and execution lag."
    if metric == "max_drawdown" and paper_val > bt_val:
        return "Paper drawdown exceeds backtest — review correlated entries and rapid sell-offs."
    return "Divergence direction does not match common failure modes; inspect trade-by-trade."


class PaperValidator:
    """Compare paper trading results vs the backtester over the same window."""

    def __init__(self, db: Session, backtester: Backtester | None = None):
        self.db = db
        self.backtester = backtester or Backtester()

    def validate(self, start_date: date, end_date: date) -> dict[str, Any]:
        """Run validation, returning paper metrics, backtest metrics, and divergence flags."""
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        paper_metrics, paper_trade_dump = self._paper_results(start_date, end_date)
        backtest_metrics = self._backtest_results(start_date, end_date)

        divergences = []
        for metric in COMPARED_METRICS:
            paper_val = float(paper_metrics.get(metric, 0))
            bt_val = float(backtest_metrics.get(metric, 0))
            diff = _relative_diff(paper_val, bt_val)
            if diff > DIVERGENCE_THRESHOLD:
                divergences.append({
                    "metric": metric,
                    "paper": paper_val,
                    "backtest": bt_val,
                    "relative_diff": round(diff, 4),
                    "diagnosis": _diagnose_divergence(metric, paper_val, bt_val),
                })

        return {
            "window": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            "paper": paper_metrics,
            "backtest": backtest_metrics,
            "benchmark": self._spy_benchmark(start_date, end_date, paper_metrics),
            "divergences": divergences,
            "ok": not divergences,
            "paper_sample": paper_trade_dump[:25],
        }

    def _spy_benchmark(
        self, start_date: date, end_date: date, paper_metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """Buy-and-hold SPY over the window vs the paper book.

        ~90% of retail algos underperform buy-and-hold in year one — this line
        keeps the scoreboard honest about whether the machinery beats doing
        nothing. paper_return_on_deployed is naive (total pnl / total capital
        deployed across closed trades, not time-weighted), so treat it as a
        rough comparison, not a performance figure.
        """
        rows = (
            self.db.query(PriceHistory.date, PriceHistory.close)
            .filter(
                PriceHistory.ticker == "SPY",
                PriceHistory.date >= start_date,
                PriceHistory.date <= end_date,
                PriceHistory.close.isnot(None),
            )
            .order_by(PriceHistory.date.asc())
            .all()
        )
        spy_return = None
        if len(rows) >= 2 and rows[0].close:
            spy_return = (rows[-1].close - rows[0].close) / rows[0].close

        deployed = self._deployed_capital(start_date, end_date)
        total_pnl = float(paper_metrics.get("total_pnl", 0) or 0)
        paper_return = total_pnl / deployed if deployed else None

        beats = None
        if spy_return is not None and paper_return is not None:
            beats = paper_return > spy_return
        return {
            "spy_return_pct": round(spy_return, 5) if spy_return is not None else None,
            "paper_return_on_deployed": round(paper_return, 5) if paper_return is not None else None,
            "deployed_capital": round(deployed, 2),
            "beats_spy": beats,
        }

    def _deployed_capital(self, start_date: date, end_date: date) -> float:
        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(end_date, time.max)
        rows = (
            self.db.query(PaperTrade.position_size)
            .filter(
                PaperTrade.status == "closed",
                PaperTrade.pnl.isnot(None),
                PaperTrade.closed_at >= start_dt,
                PaperTrade.closed_at <= end_dt,
            )
            .all()
        )
        return float(sum(r.position_size or 0 for r in rows))

    def _paper_results(
        self, start_date: date, end_date: date
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(end_date, time.max)
        trades = (
            self.db.query(PaperTrade)
            .filter(
                PaperTrade.status == "closed",
                PaperTrade.closed_at >= start_dt,
                PaperTrade.closed_at <= end_dt,
            )
            .order_by(PaperTrade.closed_at.asc())
            .all()
        )
        metrics = _compute_paper_metrics(trades, hold_days=self.backtester.hold_days)
        dump = [
            {
                "ticker": t.ticker,
                "strategy": t.strategy,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "pnl": float(t.pnl) if t.pnl is not None else None,
                "score": float(t.score) if t.score is not None else None,
            }
            for t in trades
        ]
        return metrics, dump

    def _backtest_results(self, start_date: date, end_date: date) -> dict[str, Any]:
        try:
            result = self.backtester.run(
                strategy="combined",
                start_date=start_date,
                end_date=end_date,
            )
            return {
                "num_trades": result.metrics.get("num_trades", 0),
                "win_rate": result.metrics.get("win_rate", 0.0),
                "avg_pnl": result.metrics.get("avg_pnl", 0.0),
                "total_pnl": result.metrics.get("total_pnl", 0.0),
                "sharpe_ratio": result.metrics.get("sharpe_ratio", 0.0),
                "max_drawdown": result.metrics.get("max_drawdown", 0.0),
            }
        except Exception as e:
            logger.warning("Backtest failed during validation: %s", e)
            return {
                "num_trades": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "total_pnl": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "error": str(e),
            }


def format_report(report: dict[str, Any]) -> str:
    """Render a human-readable summary of a validation report."""
    lines = []
    w = report["window"]
    lines.append(f"Paper-vs-Backtest Validation — {w['start_date']} to {w['end_date']}")
    lines.append("=" * 72)

    p, b = report["paper"], report["backtest"]
    lines.append(f"{'Metric':<18}{'Paper':>14}{'Backtest':>14}{'RelΔ':>10}")
    lines.append("-" * 72)
    for metric in ("num_trades", *COMPARED_METRICS):
        pv = p.get(metric, 0)
        bv = b.get(metric, 0)
        diff = _relative_diff(float(pv or 0), float(bv or 0)) if metric != "num_trades" else 0
        diff_str = f"{diff:.1%}" if metric != "num_trades" else "-"
        lines.append(f"{metric:<18}{str(pv):>14}{str(bv):>14}{diff_str:>10}")

    lines.append("-" * 72)
    bench = report.get("benchmark") or {}
    spy = bench.get("spy_return_pct")
    book = bench.get("paper_return_on_deployed")
    if spy is not None or book is not None:
        spy_s = f"{spy:+.2%}" if spy is not None else "n/a"
        book_s = f"{book:+.2%}" if book is not None else "n/a"
        verdict = (
            "BEATS SPY" if bench.get("beats_spy")
            else "TRAILS SPY" if bench.get("beats_spy") is False
            else "no comparison"
        )
        lines.append(
            f"Benchmark: SPY buy-and-hold {spy_s} vs book {book_s} "
            f"on ${bench.get('deployed_capital', 0):,.0f} deployed — {verdict}"
        )
        lines.append("-" * 72)
    if report["divergences"]:
        lines.append(f"FLAGGED {len(report['divergences'])} divergence(s) (>{int(DIVERGENCE_THRESHOLD * 100)}%):")
        for d in report["divergences"]:
            lines.append(
                f"  - {d['metric']}: paper={d['paper']} vs backtest={d['backtest']} "
                f"(Δ={d['relative_diff']:.1%}) — {d['diagnosis']}"
            )
    else:
        lines.append("No divergences above threshold. Paper results consistent with backtest.")
    return "\n".join(lines)
