"""API route for paper-vs-backtest validation."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services.ic_metrics import rolling_ic_summary
from src.services.live_gate import evaluate_gates
from src.services.paper_validation import PaperValidator

router = APIRouter()


@router.get("/validate/ic")
def information_coefficient(
    window_days: int = Query(60, ge=5, le=365, description="Rolling window (days)"),
    sample: str = Query("universe", description="universe | funded"),
    db: Session = Depends(get_db),
):
    """Rolling mean rank-IC + IR per signal/horizon — the ranker's go/no-go metric."""
    return {"window_days": window_days, "sample": sample, "signals": rolling_ic_summary(db, window_days, sample)}


@router.get("/validate/paper-vs-backtest")
def paper_vs_backtest(
    start_date: date = Query(..., description="Start of window (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End of window (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
):
    """Compare Alpaca paper outcomes vs backtester over the same window."""
    try:
        return PaperValidator(db).validate(start_date, end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/validate/live-gate")
def live_gate(db: Session = Depends(get_db)):
    """Evaluate the D014 live-money go/no-go gates per strategy arm."""
    return evaluate_gates(db)
