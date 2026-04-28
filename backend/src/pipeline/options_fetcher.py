"""Daily options-snapshot fetcher.

Pulls option chains from yfinance and computes per-ticker IV summary metrics
that feed the directional model.

Metrics produced per ticker per day:
- iv_atm_30d           : Avg IV of nearest 30-DTE ATM call & put
- iv_atm_90d           : Avg IV of nearest 90-DTE ATM call & put
- iv_rank_252d         : (cur - min) / (max - min) over last 252 trading days
- iv_percentile_252d   : Fraction of last 252 days where IV was below current
- put_call_skew_25d    : Approx 25-delta put IV - 25-delta call IV (strike-offset proxy)
- term_structure_slope : iv_atm_90d - iv_atm_30d (positive = contango, negative = backwardation)

If a ticker has no listed options or yfinance errors out, an `OptionsSnapshot`
row is still written with `has_options=0` so downstream feature extraction can
fill missing values cleanly.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Iterable

import yfinance as yf
from sqlalchemy import and_
from sqlalchemy.orm import Session

from src.db.models import OptionsSnapshot, PriceHistory
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Rate-limit pause between yfinance ticker calls to avoid 429s.
RATE_LIMIT_SLEEP = 0.5

# Strike offsets used as a 25-delta proxy when option Greeks are not provided.
# A 25-delta put on a ~30 DTE chain typically sits ~5–10% OTM.
SKEW_PUT_OFFSET = -0.10
SKEW_CALL_OFFSET = 0.10


def _nearest_expiration(expirations: Iterable[str], today: date, target_days: int) -> str | None:
    best = None
    best_diff = float("inf")
    for exp_str in expirations:
        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            continue
        days = (exp_date - today).days
        if days < target_days:
            continue
        diff = days - target_days
        if diff < best_diff:
            best_diff = diff
            best = exp_str
    if best is None:
        # Fall back to any expiration ≥ today, picking the closest absolute distance
        for exp_str in expirations:
            try:
                exp_date = date.fromisoformat(exp_str)
            except ValueError:
                continue
            diff = abs((exp_date - today).days - target_days)
            if diff < best_diff:
                best_diff = diff
                best = exp_str
    return best


def _atm_iv(chain_calls, chain_puts, spot: float) -> float | None:
    """Average IV of the call & put nearest to spot."""

    def nearest_iv(df):
        if df is None or len(df) == 0:
            return None
        idx = (df["strike"] - spot).abs().idxmin()
        iv = df.loc[idx, "impliedVolatility"]
        return float(iv) if iv and iv > 0 else None

    call_iv = nearest_iv(chain_calls)
    put_iv = nearest_iv(chain_puts)
    vals = [v for v in (call_iv, put_iv) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _skew_iv(df, target_strike: float) -> float | None:
    """IV of the option whose strike is closest to target_strike."""
    if df is None or len(df) == 0:
        return None
    idx = (df["strike"] - target_strike).abs().idxmin()
    iv = df.loc[idx, "impliedVolatility"]
    return float(iv) if iv and iv > 0 else None


class OptionsFetcher:
    """Fetch and persist daily OptionsSnapshot rows for the watchlist."""

    def __init__(self, db: Session | None = None, sleep_s: float = RATE_LIMIT_SLEEP):
        self._owns_db = db is None
        self.db: Session = db or SessionLocal()
        self.sleep_s = sleep_s

    def close(self):
        if self._owns_db:
            self.db.close()

    def fetch_all(self, tickers: list[str] | None = None, snapshot_date: date | None = None) -> dict[str, str]:
        """Fetch snapshots for all watchlist tickers (or `tickers` if provided).

        Returns {ticker: status} where status is 'ok', 'no_options', or 'error'.
        """
        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        snapshot_date = snapshot_date or date.today()
        results: dict[str, str] = {}
        for ticker in tickers:
            try:
                results[ticker] = self.fetch_one(ticker, snapshot_date)
            except Exception:
                logger.exception("Options fetch failed for %s", ticker)
                results[ticker] = "error"
                self._upsert(ticker, snapshot_date, has_options=False)
            time.sleep(self.sleep_s)
        return results

    def fetch_one(self, ticker: str, snapshot_date: date) -> str:
        """Fetch a single ticker's snapshot. Returns 'ok' / 'no_options' / 'error'."""
        spot = self._latest_close(ticker)
        if not spot:
            logger.debug("No price for %s; skipping options snapshot", ticker)
            self._upsert(ticker, snapshot_date, has_options=False)
            return "no_price"

        stock = yf.Ticker(ticker)
        try:
            expirations = list(stock.options or [])
        except Exception:
            logger.exception("yfinance.options error for %s", ticker)
            self._upsert(ticker, snapshot_date, has_options=False)
            return "error"

        if not expirations:
            self._upsert(ticker, snapshot_date, has_options=False)
            return "no_options"

        exp_30 = _nearest_expiration(expirations, snapshot_date, 30)
        exp_90 = _nearest_expiration(expirations, snapshot_date, 90)

        if exp_30 is None and exp_90 is None:
            self._upsert(ticker, snapshot_date, has_options=False)
            return "no_options"

        iv_30 = skew = None
        if exp_30:
            try:
                chain_30 = stock.option_chain(exp_30)
                iv_30 = _atm_iv(chain_30.calls, chain_30.puts, spot)
                put_iv = _skew_iv(chain_30.puts, spot * (1 + SKEW_PUT_OFFSET))
                call_iv = _skew_iv(chain_30.calls, spot * (1 + SKEW_CALL_OFFSET))
                if put_iv is not None and call_iv is not None:
                    skew = put_iv - call_iv
            except Exception:
                logger.exception("Failed to load 30d chain for %s exp=%s", ticker, exp_30)

        iv_90 = None
        if exp_90 and exp_90 != exp_30:
            try:
                chain_90 = stock.option_chain(exp_90)
                iv_90 = _atm_iv(chain_90.calls, chain_90.puts, spot)
            except Exception:
                logger.exception("Failed to load 90d chain for %s exp=%s", ticker, exp_90)

        term_slope = (iv_90 - iv_30) if (iv_90 is not None and iv_30 is not None) else None

        rank, pct = self._iv_rank_and_percentile(ticker, iv_30)

        self._upsert(
            ticker,
            snapshot_date,
            has_options=True,
            iv_atm_30d=iv_30,
            iv_atm_90d=iv_90,
            iv_rank_252d=rank,
            iv_percentile_252d=pct,
            put_call_skew_25d=skew,
            term_structure_slope=term_slope,
        )
        return "ok"

    def _latest_close(self, ticker: str) -> float | None:
        row = (
            self.db.query(PriceHistory)
            .filter_by(ticker=ticker)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        return float(row.close) if row and row.close else None

    def _iv_rank_and_percentile(
        self, ticker: str, current_iv: float | None
    ) -> tuple[float | None, float | None]:
        """Compute IV rank and percentile from the last 252 trading days of snapshots."""
        if current_iv is None:
            return None, None
        cutoff = date.today() - timedelta(days=380)  # ~252 trading days
        history = (
            self.db.query(OptionsSnapshot.iv_atm_30d)
            .filter(
                and_(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.date >= cutoff,
                    OptionsSnapshot.iv_atm_30d.isnot(None),
                )
            )
            .all()
        )
        ivs = [float(r[0]) for r in history if r[0] is not None]
        if not ivs:
            return 0.0, 0.0
        ivs.append(current_iv)
        lo, hi = min(ivs), max(ivs)
        rank = (current_iv - lo) / (hi - lo) if hi > lo else 0.0
        pct = sum(1 for v in ivs if v < current_iv) / len(ivs)
        return round(rank, 4), round(pct, 4)

    def _upsert(self, ticker: str, snapshot_date: date, has_options: bool, **fields):
        existing = (
            self.db.query(OptionsSnapshot)
            .filter_by(ticker=ticker, date=snapshot_date)
            .first()
        )
        if existing is None:
            existing = OptionsSnapshot(
                ticker=ticker,
                date=snapshot_date,
                created_at=datetime.utcnow(),
            )
            self.db.add(existing)

        existing.has_options = 1 if has_options else 0
        for k, v in fields.items():
            setattr(existing, k, v)
        self.db.commit()
