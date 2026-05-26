"""Options chain data fetcher using yfinance."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import math

import yfinance as yf
from sqlalchemy.orm import Session

from src.db.models import OptionsChain
from src.db.session import SessionLocal


def _safe_float(v) -> float:
    """yfinance row values are often NaN; coerce to 0.0 cleanly."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else f


def _safe_int(v) -> int:
    return int(_safe_float(v))

logger = logging.getLogger(__name__)

# Default cache TTL: 15 minutes
DEFAULT_CACHE_TTL_MINUTES = 15


class OptionsChainFetcher:
    """Fetch and cache real options chain data from yfinance."""

    def __init__(self, cache_ttl_minutes: int = DEFAULT_CACHE_TTL_MINUTES):
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.db: Session = SessionLocal()

    def close(self):
        self.db.close()

    def get_expirations(self, ticker: str) -> list[str]:
        """Get available expiration dates for a ticker."""
        try:
            stock = yf.Ticker(ticker)
            return list(stock.options)
        except Exception:
            logger.exception(f"Failed to fetch expirations for {ticker}")
            return []

    def fetch_chain(self, ticker: str, expiration: str) -> list[dict]:
        """Fetch options chain for a specific ticker and expiration.

        Returns list of option dicts. Caches to DB with TTL.
        """
        # Check cache first
        cached = self._get_cached(ticker, expiration)
        if cached is not None:
            return cached

        # Fetch from yfinance
        try:
            stock = yf.Ticker(ticker)
            chain = stock.option_chain(expiration)
        except Exception:
            logger.exception(f"Failed to fetch chain for {ticker} exp={expiration}")
            return []

        rows: list[dict] = []

        for option_type, df in [("call", chain.calls), ("put", chain.puts)]:
            for _, row in df.iterrows():
                entry = {
                    "ticker": ticker,
                    "expiration": expiration,
                    "strike": _safe_float(row.get("strike", 0)),
                    "option_type": option_type,
                    "bid": _safe_float(row.get("bid", 0)),
                    "ask": _safe_float(row.get("ask", 0)),
                    "last": _safe_float(row.get("lastPrice", 0)),
                    "volume": _safe_int(row.get("volume", 0)),
                    "open_interest": _safe_int(row.get("openInterest", 0)),
                    "implied_vol": _safe_float(row.get("impliedVolatility", 0)),
                }
                rows.append(entry)

        # Store in DB cache
        self._cache_rows(ticker, expiration, rows)

        return rows

    def get_chain_for_strike(
        self, ticker: str, expiration: str, strike: float, option_type: str
    ) -> dict | None:
        """Get a specific option contract from the chain."""
        chain = self.fetch_chain(ticker, expiration)
        for row in chain:
            if (
                abs(row["strike"] - strike) < 0.01
                and row["option_type"] == option_type
            ):
                return row
        return None

    def find_nearest_strike(
        self, ticker: str, expiration: str, target_strike: float, option_type: str
    ) -> dict | None:
        """Find the option with strike nearest to the target."""
        chain = self.fetch_chain(ticker, expiration)
        candidates = [r for r in chain if r["option_type"] == option_type]
        if not candidates:
            return None
        return min(candidates, key=lambda r: abs(r["strike"] - target_strike))

    def find_expiration_near_days(self, ticker: str, target_days: int) -> str | None:
        """Find the expiration date closest to target_days from now."""
        expirations = self.get_expirations(ticker)
        if not expirations:
            return None

        from datetime import date

        today = date.today()
        best = None
        best_diff = float("inf")

        for exp_str in expirations:
            try:
                exp_date = date.fromisoformat(exp_str)
                diff = abs((exp_date - today).days - target_days)
                if diff < best_diff:
                    best_diff = diff
                    best = exp_str
            except ValueError:
                continue

        return best

    def _get_cached(self, ticker: str, expiration: str) -> list[dict] | None:
        """Return cached chain data if fresh enough, else None."""
        cutoff = datetime.utcnow() - self.cache_ttl
        rows = (
            self.db.query(OptionsChain)
            .filter_by(ticker=ticker, expiration=expiration)
            .filter(OptionsChain.fetched_at >= cutoff)
            .all()
        )
        if not rows:
            return None

        return [
            {
                "ticker": r.ticker,
                "expiration": r.expiration,
                "strike": r.strike,
                "option_type": r.option_type,
                "bid": r.bid,
                "ask": r.ask,
                "last": r.last,
                "volume": r.volume,
                "open_interest": r.open_interest,
                "implied_vol": r.implied_vol,
            }
            for r in rows
        ]

    def _cache_rows(self, ticker: str, expiration: str, rows: list[dict]):
        """Upsert chain data into DB cache."""
        # Delete stale rows for this ticker/expiration
        self.db.query(OptionsChain).filter_by(
            ticker=ticker, expiration=expiration
        ).delete()

        now = datetime.utcnow()
        for row in rows:
            self.db.add(
                OptionsChain(
                    ticker=row["ticker"],
                    expiration=row["expiration"],
                    strike=row["strike"],
                    option_type=row["option_type"],
                    bid=row["bid"],
                    ask=row["ask"],
                    last=row["last"],
                    volume=row["volume"],
                    open_interest=row["open_interest"],
                    implied_vol=row["implied_vol"],
                    fetched_at=now,
                )
            )

        self.db.commit()
        logger.info(f"Cached {len(rows)} options for {ticker} exp={expiration}")
