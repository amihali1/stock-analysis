"""Paper trading API endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.db.models import PaperTrade, PriceHistory

router = APIRouter()


class OpenTradeRequest(BaseModel):
    ticker: str
    strategy: str
    entry_price: float
    stop_loss: float | None = None
    target_price: float | None = None
    position_size: float | None = None
    max_loss: float | None = None
    contracts: int | None = None
    strike: float | None = None
    option_type: str | None = None
    score: float | None = None


class CloseTradeRequest(BaseModel):
    exit_price: float


class PaperTradeResponse(BaseModel):
    id: int
    ticker: str
    strategy: str
    status: str
    entry_price: float
    stop_loss: float | None
    target_price: float | None
    position_size: float | None
    max_loss: float | None
    contracts: int | None
    strike: float | None
    option_type: str | None
    exit_price: float | None
    pnl: float | None
    score: float | None
    opened_at: str | None
    closed_at: str | None
    current_price: float | None = None
    unrealized_pnl: float | None = None


class PaperTradeListResponse(BaseModel):
    trades: list[PaperTradeResponse]
    summary: dict


@router.post("/paper-trades", response_model=PaperTradeResponse)
def open_trade(req: OpenTradeRequest, db: Session = Depends(get_db)):
    """Open a paper trade."""
    trade = PaperTrade(
        ticker=req.ticker.upper(),
        strategy=req.strategy,
        status="open",
        entry_price=req.entry_price,
        stop_loss=req.stop_loss,
        target_price=req.target_price,
        position_size=req.position_size,
        max_loss=req.max_loss,
        contracts=req.contracts,
        strike=req.strike,
        option_type=req.option_type,
        score=req.score,
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return _to_response(trade, db)


@router.post("/paper-trades/{trade_id}/close", response_model=PaperTradeResponse)
def close_trade(trade_id: int, req: CloseTradeRequest, db: Session = Depends(get_db)):
    """Close a paper trade with an exit price."""
    trade = db.query(PaperTrade).get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status == "closed":
        raise HTTPException(status_code=400, detail="Trade already closed")

    trade.exit_price = req.exit_price
    trade.closed_at = datetime.utcnow()
    trade.status = "closed"

    # Calculate P&L
    if trade.strategy == "short":
        # Short profit = (entry - exit) * shares
        shares = int((trade.position_size or 0) / (trade.entry_price * 1.5)) if trade.entry_price else 0
        trade.pnl = (trade.entry_price - req.exit_price) * shares
    elif trade.strategy == "options":
        # Simplified: if stock dropped below strike, profit. Otherwise loss = premium.
        trade.pnl = -(trade.position_size or 0)  # Default to total loss
        if trade.strike and req.exit_price < trade.strike:
            intrinsic = trade.strike - req.exit_price
            trade.pnl = intrinsic * (trade.contracts or 1) * 100 - (trade.position_size or 0)

    db.commit()
    return _to_response(trade, db)


@router.get("/paper-trades", response_model=PaperTradeListResponse)
def list_trades(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """List all paper trades with summary stats."""
    query = db.query(PaperTrade).order_by(PaperTrade.opened_at.desc())
    if status:
        query = query.filter(PaperTrade.status == status)

    trades = query.all()
    responses = [_to_response(t, db) for t in trades]

    # Summary stats
    closed = [t for t in trades if t.status == "closed" and t.pnl is not None]
    wins = [t for t in closed if t.pnl > 0]

    summary = {
        "total_trades": len(trades),
        "open_trades": sum(1 for t in trades if t.status == "open"),
        "closed_trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else 0,
        "total_pnl": sum(t.pnl for t in closed),
        "avg_pnl": sum(t.pnl for t in closed) / len(closed) if closed else 0,
    }

    return PaperTradeListResponse(trades=responses, summary=summary)


def _to_response(trade: PaperTrade, db: Session) -> PaperTradeResponse:
    """Convert DB model to response, adding current price for open trades."""
    current_price = None
    unrealized_pnl = None

    if trade.status == "open":
        latest = (
            db.query(PriceHistory.close)
            .filter_by(ticker=trade.ticker)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        if latest and latest[0]:
            current_price = latest[0]
            if trade.strategy == "short":
                shares = int((trade.position_size or 0) / (trade.entry_price * 1.5)) if trade.entry_price else 0
                unrealized_pnl = (trade.entry_price - current_price) * shares

    return PaperTradeResponse(
        id=trade.id,
        ticker=trade.ticker,
        strategy=trade.strategy,
        status=trade.status,
        entry_price=trade.entry_price,
        stop_loss=trade.stop_loss,
        target_price=trade.target_price,
        position_size=trade.position_size,
        max_loss=trade.max_loss,
        contracts=trade.contracts,
        strike=trade.strike,
        option_type=trade.option_type,
        exit_price=trade.exit_price,
        pnl=trade.pnl,
        score=trade.score,
        opened_at=trade.opened_at.isoformat() if trade.opened_at else None,
        closed_at=trade.closed_at.isoformat() if trade.closed_at else None,
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
    )
