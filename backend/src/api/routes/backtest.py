"""API routes for backtesting."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class BacktestRequest(BaseModel):
    tickers: Optional[list[str]] = None
    strategy: str = "combined"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    max_position: float = 5000.0
    hold_days: int = 5
    score_threshold: float = 0.5
    max_concurrent: int = 10


@router.post("/api/backtest")
def run_backtest(req: BacktestRequest):
    """Run a backtest and return results with metrics."""
    from src.models.backtester import Backtester

    bt = Backtester(
        max_position=req.max_position,
        hold_days=req.hold_days,
        score_threshold=req.score_threshold,
        max_concurrent_positions=req.max_concurrent,
    )
    result = bt.run(
        tickers=req.tickers,
        strategy=req.strategy,
        start_date=req.start_date,
        end_date=req.end_date,
    )
    return result.to_dict()


@router.post("/api/backtest/compare")
def compare_strategies(req: BacktestRequest):
    """Run backtests for all three strategies and return comparison."""
    from src.models.backtester import Backtester

    bt = Backtester(
        max_position=req.max_position,
        hold_days=req.hold_days,
        score_threshold=req.score_threshold,
        max_concurrent_positions=req.max_concurrent,
    )
    return bt.compare_strategies(
        tickers=req.tickers,
        start_date=req.start_date,
        end_date=req.end_date,
    )
