"""API routes for portfolio risk management and Alpaca sync."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/portfolio/risk")
def get_portfolio_risk(db: Session = Depends(get_db)):
    """Get full portfolio risk report: metrics, sector exposure, correlations."""
    from src.models.risk_manager import RiskManager

    rm = RiskManager(db)
    return rm.get_full_risk_report()


@router.get("/portfolio/history")
def get_portfolio_history(days: int = 30, db: Session = Depends(get_db)):
    """Get portfolio snapshot history."""
    from datetime import date, timedelta
    from src.db.models import PortfolioSnapshot

    cutoff = date.today() - timedelta(days=days)
    snapshots = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.date >= cutoff)
        .order_by(PortfolioSnapshot.date)
        .all()
    )
    return [
        {
            "date": s.date.isoformat(),
            "total_exposure": s.total_exposure,
            "total_max_loss": s.total_max_loss,
            "open_positions": s.open_positions,
            "beta_to_spy": s.beta_to_spy,
        }
        for s in snapshots
    ]


@router.post("/portfolio/check")
def check_position(ticker: str, position_size: float = 5000.0, db: Session = Depends(get_db)):
    """Check if a new position passes all risk controls."""
    from src.models.risk_manager import RiskManager

    rm = RiskManager(db)
    allowed, reasons = rm.can_open_position(ticker, position_size)
    return {"allowed": allowed, "reasons": reasons}


@router.get("/portfolio")
def get_portfolio_summary(db: Session = Depends(get_db)):
    """Account summary + open positions from Alpaca."""
    from src.services.portfolio_sync import PortfolioSync

    try:
        sync = PortfolioSync(db)
        return sync.get_portfolio_summary()
    except ValueError as e:
        return {"error": str(e), "detail": "Alpaca credentials not configured"}


@router.get("/portfolio/orders")
def get_portfolio_orders(status: str = "all", limit: int = 50, db: Session = Depends(get_db)):
    """Recent order history from Alpaca."""
    from src.db.models import AlpacaOrder

    orders = (
        db.query(AlpacaOrder)
        .order_by(AlpacaOrder.submitted_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "order_id": o.alpaca_order_id,
            "ticker": o.ticker,
            "side": o.side,
            "qty": o.qty,
            "type": o.order_type,
            "status": o.status,
            "filled_price": o.filled_price,
            "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        }
        for o in orders
    ]


@router.post("/portfolio/sync")
def trigger_portfolio_sync(db: Session = Depends(get_db)):
    """Manually trigger a full portfolio sync."""
    from src.services.portfolio_sync import PortfolioSync

    try:
        sync = PortfolioSync(db)
        pos_count = sync.sync_positions()
        order_count = sync.sync_orders()
        account = sync.sync_account()
        return {
            "synced_positions": pos_count,
            "new_orders": order_count,
            "equity": account["equity"],
        }
    except ValueError as e:
        return {"error": str(e), "detail": "Alpaca credentials not configured"}
