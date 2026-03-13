from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db.session import get_db
from src.db.models import Stock, PriceHistory, TechnicalIndicator, SentimentScore, Recommendation
from src.api.schemas import (
    AnalysisResponse, PricePoint, IndicatorPoint, SentimentEntry,
    RecommendationResponse, TickerInfo, TickerListResponse,
)

router = APIRouter()


@router.get("/analysis/{ticker}", response_model=AnalysisResponse)
def get_analysis(
    ticker: str,
    days: int = Query(90, ge=1, le=730),
    db: Session = Depends(get_db),
):
    """Get full analysis for a single ticker."""
    ticker = ticker.upper()
    stock = db.query(Stock).filter_by(ticker=ticker).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

    # Recent prices
    prices = (
        db.query(PriceHistory)
        .filter_by(ticker=ticker)
        .order_by(PriceHistory.date.desc())
        .limit(days)
        .all()
    )
    prices.reverse()

    # Recent indicators
    indicators = (
        db.query(TechnicalIndicator)
        .filter_by(ticker=ticker)
        .order_by(TechnicalIndicator.date.desc())
        .limit(days)
        .all()
    )
    indicators.reverse()

    # Recent sentiments
    sentiments = (
        db.query(SentimentScore)
        .filter_by(ticker=ticker)
        .order_by(SentimentScore.date.desc())
        .limit(30)
        .all()
    )
    sentiments.reverse()

    # Recent recommendations
    recs = (
        db.query(Recommendation)
        .filter_by(ticker=ticker)
        .order_by(Recommendation.date.desc())
        .limit(10)
        .all()
    )

    latest_price = None
    if prices:
        p = prices[-1]
        latest_price = PricePoint(
            date=p.date, open=p.open, high=p.high, low=p.low,
            close=p.close, volume=p.volume,
        )

    return AnalysisResponse(
        ticker=ticker,
        name=stock.name,
        sector=stock.sector,
        latest_price=latest_price,
        prices=[
            PricePoint(date=p.date, open=p.open, high=p.high, low=p.low,
                       close=p.close, volume=p.volume)
            for p in prices
        ],
        indicators=[
            IndicatorPoint(
                date=i.date, rsi_14=i.rsi_14, macd=i.macd,
                macd_signal=i.macd_signal, macd_histogram=i.macd_histogram,
                bb_upper=i.bb_upper, bb_middle=i.bb_middle, bb_lower=i.bb_lower,
                sma_50=i.sma_50, sma_200=i.sma_200, volume_zscore=i.volume_zscore,
            )
            for i in indicators
        ],
        sentiments=[
            SentimentEntry(
                date=s.date, source=s.source, headline=s.headline,
                sentiment=s.sentiment, confidence=s.confidence, reasoning=s.reasoning,
            )
            for s in sentiments
        ],
        recommendations=[
            RecommendationResponse(
                ticker=r.ticker, date=r.date, strategy=r.strategy, score=r.score,
                directional_signal=r.directional_signal, volatility_signal=r.volatility_signal,
                sentiment_signal=r.sentiment_signal, entry_price=r.entry_price,
                stop_loss=r.stop_loss, target_price=r.target_price,
                position_size=r.position_size, max_loss=r.max_loss,
                contracts=r.contracts, strike=r.strike, expiry=r.expiry,
                option_type=r.option_type, notes=r.notes,
            )
            for r in recs
        ],
    )


@router.get("/tickers", response_model=TickerListResponse)
def list_tickers(db: Session = Depends(get_db)):
    """List all tracked tickers with latest data timestamps."""
    stocks = db.query(Stock).order_by(Stock.ticker).all()

    items = []
    for stock in stocks:
        # Get latest price
        latest = (
            db.query(PriceHistory.date, PriceHistory.close)
            .filter_by(ticker=stock.ticker)
            .order_by(PriceHistory.date.desc())
            .first()
        )

        items.append(TickerInfo(
            ticker=stock.ticker,
            name=stock.name,
            sector=stock.sector,
            exchange=stock.exchange,
            latest_price_date=latest[0] if latest else None,
            latest_close=latest[1] if latest else None,
        ))

    return TickerListResponse(tickers=items, count=len(items))
