"""Watchlist management API routes."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.db.models import Watchlist
from src.db.watchlist import get_watchlist_tickers
from src.api.schemas import WatchlistItem, WatchlistResponse, WatchlistAddRequest

router = APIRouter()

TICKER_RE = re.compile(r"^[A-Z]{1,10}$")


@router.get("/watchlist", response_model=WatchlistResponse)
def list_watchlist(db: Session = Depends(get_db)):
    # Ensure seeded if empty
    get_watchlist_tickers(db)
    items = db.query(Watchlist).order_by(Watchlist.ticker).all()
    return WatchlistResponse(
        tickers=[
            WatchlistItem(ticker=w.ticker, sector=w.sector, added_at=w.added_at)
            for w in items
        ],
        count=len(items),
    )


@router.post("/watchlist", response_model=WatchlistResponse)
def add_to_watchlist(body: WatchlistAddRequest, db: Session = Depends(get_db)):
    for raw in body.tickers:
        ticker = raw.strip().upper()
        if not TICKER_RE.match(ticker):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid ticker format: '{raw}'. Must be 1-10 uppercase letters.",
            )
        existing = db.query(Watchlist).filter_by(ticker=ticker).first()
        if existing is None:
            db.add(Watchlist(ticker=ticker))
    db.commit()

    items = db.query(Watchlist).order_by(Watchlist.ticker).all()
    return WatchlistResponse(
        tickers=[
            WatchlistItem(ticker=w.ticker, sector=w.sector, added_at=w.added_at)
            for w in items
        ],
        count=len(items),
    )


@router.delete("/watchlist/{ticker}")
def remove_from_watchlist(ticker: str, db: Session = Depends(get_db)):
    item = db.query(Watchlist).filter_by(ticker=ticker.upper()).first()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not in watchlist")
    db.delete(item)
    db.commit()
    return {"detail": f"Removed {ticker.upper()} from watchlist"}
