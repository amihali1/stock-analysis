from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from src.db.session import get_db
from src.db.models import PaperTrade, Recommendation
from src.api.leg_parsing import parse_option_legs, parse_stock_legs
from src.api.schemas import (
    RecommendationResponse,
    RecommendationsListResponse,
)
from src.services.paper_trade_valuation import (
    build_positions_map,
    fetch_live_prices,
    value_open_trade,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _open_trade_map(db: Session, tickers: set[str]) -> dict[str, list[PaperTrade]]:
    """Open paper trades on the given tickers, grouped by ticker (newest first)."""
    if not tickers:
        return {}
    rows = (
        db.query(PaperTrade)
        .filter(PaperTrade.status == "open", PaperTrade.ticker.in_(tickers))
        .order_by(PaperTrade.opened_at.desc())
        .all()
    )
    grouped: dict[str, list[PaperTrade]] = {}
    for t in rows:
        grouped.setdefault(t.ticker.upper(), []).append(t)
    return grouped


def _match_open_trade(rec: Recommendation, trades: list[PaperTrade]) -> PaperTrade | None:
    """Pick the open trade backing a recommendation: same strategy if present,
    else the most recent open trade on that ticker."""
    if not trades:
        return None
    same = [t for t in trades if t.strategy == rec.strategy]
    return (same or trades)[0]


@router.get("/recommendations", response_model=RecommendationsListResponse)
def get_recommendations(
    strategy: Optional[str] = Query(None, pattern="^(short|options|spread|long|call_options|bull_spread)$"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get top recommendations, optionally filtered by strategy."""
    query = db.query(Recommendation).order_by(Recommendation.score.desc())

    if strategy:
        query = query.filter(Recommendation.strategy == strategy)

    recs = query.limit(limit).all()

    # Enrich with live underlying price and, where the rec is an open position,
    # its unrealized P&L. All best-effort: unavailable data → None (shows "—").
    tickers = {r.ticker.upper() for r in recs if r.ticker}
    stock_prices = fetch_live_prices(tickers)
    open_trades = _open_trade_map(db, tickers)
    positions_map = build_positions_map(db) if open_trades else {}

    items = []
    for r in recs:
        trade = _match_open_trade(r, open_trades.get((r.ticker or "").upper(), []))
        unrealized_pnl = None
        if trade is not None:
            _, unrealized_pnl = value_open_trade(trade, positions_map, stock_prices)

        items.append(RecommendationResponse(
            id=r.id,
            ticker=r.ticker,
            date=r.date,
            strategy=r.strategy,
            score=r.score,
            directional_signal=r.directional_signal,
            volatility_signal=r.volatility_signal,
            sentiment_signal=r.sentiment_signal,
            entry_price=r.entry_price,
            stop_loss=r.stop_loss,
            target_price=r.target_price,
            position_size=r.position_size,
            max_loss=r.max_loss,
            contracts=r.contracts,
            strike=r.strike,
            expiry=r.expiry,
            option_type=r.option_type,
            legs=parse_option_legs(r.legs_json),
            stock_legs=parse_stock_legs(r.legs_json),
            risk_type=r.risk_type or "undefined",
            notes=r.notes,
            current_price=stock_prices.get((r.ticker or "").upper()),
            unrealized_pnl=unrealized_pnl,
        ))

    return RecommendationsListResponse(recommendations=items, count=len(items))
