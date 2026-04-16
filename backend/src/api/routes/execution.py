"""API routes for order execution."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.db.session import get_db

router = APIRouter()


@router.post("/execute/recommendation/{rec_id}")
def execute_recommendation(rec_id: int, db: Session = Depends(get_db)):
    """Manually execute a single recommendation."""
    from src.services.execution_engine import ExecutionEngine

    engine = ExecutionEngine(db)
    return engine.execute_recommendation_by_id(rec_id)


@router.post("/execute/close/{ticker}")
def close_position(ticker: str, db: Session = Depends(get_db)):
    """Manually close a specific position."""
    from src.services.execution_engine import ExecutionEngine

    engine = ExecutionEngine(db)
    return engine.close_position(ticker)


@router.post("/execute/emergency-close")
def emergency_close(db: Session = Depends(get_db)):
    """Emergency liquidation — close all positions and cancel all orders."""
    from src.services.execution_engine import ExecutionEngine

    engine = ExecutionEngine(db)
    return engine.close_all_positions()


@router.get("/execute/log")
def execution_log(limit: int = 50, db: Session = Depends(get_db)):
    """Get recent execution history."""
    from src.services.execution_engine import ExecutionEngine

    engine = ExecutionEngine(db)
    return engine.get_execution_log(limit=limit)
