from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from src.db.session import get_db
from src.db.models import Recommendation
from src.api.leg_parsing import parse_option_legs, parse_stock_legs
from src.api.schemas import (
    RecommendationResponse,
    RecommendationsListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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

    items = [
        RecommendationResponse(
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
        )
        for r in recs
    ]

    return RecommendationsListResponse(recommendations=items, count=len(items))
