"""Watchlist helper: single source of truth for which tickers to process."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.db.models import Watchlist
from src.config import get_settings


def get_watchlist_tickers(db: Session) -> list[str]:
    """Return tickers from the watchlist table.

    If the table is empty, seeds it from settings.default_watchlist first.
    """
    tickers = [row.ticker for row in db.query(Watchlist.ticker).order_by(Watchlist.ticker).all()]

    if tickers:
        return tickers

    # Seed from default_watchlist
    settings = get_settings()
    for ticker in settings.default_watchlist:
        db.add(Watchlist(ticker=ticker))
    db.commit()

    return list(settings.default_watchlist)
