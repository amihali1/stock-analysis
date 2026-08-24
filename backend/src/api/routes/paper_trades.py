"""Paper trading API endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.db.models import PaperTrade, PriceHistory
from src.api.leg_parsing import parse_option_legs, parse_stock_legs
from src.api.schemas import SpreadLegResponse, StockLegResponse
from src.services.paper_trade_valuation import (
    build_positions_map,
    fetch_live_prices,
    spread_entry_mark,
    underlying_tickers,
    value_open_trade,
    uses_underlying_price,
)

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
    direction: str = "short"
    expiry: str | None = None
    legs: list[SpreadLegResponse] | None = None
    stock_legs: list[StockLegResponse] | None = None


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

    # Build valuation maps once: broker MTM from alpaca_positions (5-min synced)
    # + a live stock-price batch. Both best-effort — on failure open trades fall
    # back to the stored daily close and broker P&L.
    open_trades = [t for t in trades if t.status == "open"]
    positions_map = build_positions_map(db) if open_trades else {}
    stock_prices = fetch_live_prices(underlying_tickers(open_trades)) if open_trades else {}

    responses = [_to_response(t, db, positions_map, stock_prices) for t in trades]

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


def _to_response(
    trade: PaperTrade,
    db: Session,
    positions_map: dict[str, dict] | None = None,
    stock_prices: dict[str, float] | None = None,
) -> PaperTradeResponse:
    """Convert DB model to response, adding live price + P&L for open trades.

    When valuation maps are supplied (list endpoint) open trades are marked to
    market via broker P&L + live prices. Without them (single open/close
    responses) we fall back to the latest stored close for current_price."""
    current_price = None
    unrealized_pnl = None

    if trade.status == "open":
        if positions_map is not None or stock_prices is not None:
            current_price, unrealized_pnl = value_open_trade(
                trade, positions_map or {}, stock_prices or {}
            )
        # Fall back to the latest stored close only for strategies whose
        # current_price IS the underlying stock price. Option strategies show an
        # option/spread mark, so the underlying close would be misleading — leave
        # None (row shows "—").
        if current_price is None and uses_underlying_price(trade.strategy):
            latest = (
                db.query(PriceHistory.close)
                .filter_by(ticker=trade.ticker)
                .order_by(PriceHistory.date.desc())
                .first()
            )
            if latest and latest[0]:
                current_price = latest[0]

    # For spreads, show the net entry premium (from legs) rather than the stored
    # entry_price, which is the underlying stock price at entry — so the entry
    # and current columns are both net premium and comparable.
    entry_price = trade.entry_price
    spread_entry = spread_entry_mark(trade)
    if spread_entry is not None:
        entry_price = spread_entry

    return PaperTradeResponse(
        id=trade.id,
        ticker=trade.ticker,
        strategy=trade.strategy,
        status=trade.status,
        entry_price=entry_price,
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
        direction=trade.direction or "short",
        expiry=trade.expiry.isoformat() if trade.expiry else None,
        legs=parse_option_legs(trade.legs_json),
        stock_legs=parse_stock_legs(trade.legs_json),
    )
