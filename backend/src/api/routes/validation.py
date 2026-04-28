"""API route for paper-vs-backtest validation."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services.paper_validation import PaperValidator

router = APIRouter()


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
