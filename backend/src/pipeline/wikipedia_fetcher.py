"""Wikipedia daily page-views fetcher (P10-008).

Pulls per-article daily view counts from the Wikimedia REST API as a retail
attention proxy for the directional model. The endpoint is free and
unauthenticated; Wikimedia requires a descriptive User-Agent with a contact
address (same convention as SEC EDGAR).

Endpoint:
    https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
        en.wikipedia/all-access/all-agents/{title}/daily/{start}/{end}

The ticker -> Wikipedia title map is hand-curated at
`backend/src/config/wikipedia_titles.json`. Tickers without a mapping are
skipped (returning 'no_title') rather than auto-resolved, because the
Wikipedia search API is unreliable for ambiguous tickers (e.g. F, M, T).

Dates missing from the response (Wikimedia returns gaps for low-traffic days,
not zeros) are stubbed with `page_views=0` so downstream z-score math has a
dense series.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from src.db.models import WikipediaPageviews
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)

USER_AGENT = "stock-analysis andymihalik@gmail.com"
BASE_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/all-agents"
)
# Wikimedia accepts wider ranges, but capping at ~50d keeps single calls under
# ~3KB and lets the retry budget cover transient errors.
MAX_DAYS_PER_CALL = 50
REQUEST_TIMEOUT = 20.0
RATE_LIMIT_SLEEP = 0.1
MAX_RETRIES = 3
BACKOFF_BASE = 1.5

DEFAULT_TITLES_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "wikipedia_titles.json"
)


def _date_chunks(start: date, end: date, max_days: int) -> Iterable[tuple[date, date]]:
    """Yield (chunk_start, chunk_end) ranges covering [start, end] inclusive."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_ts(ts: str) -> date | None:
    """Parse Wikimedia 'YYYYMMDDHH' timestamp to a date."""
    try:
        return datetime.strptime(ts[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


class WikipediaPageviewFetcher:
    """Fetch and persist daily Wikipedia page-view rows for the watchlist."""

    def __init__(
        self,
        db: Session | None = None,
        sleep_s: float = RATE_LIMIT_SLEEP,
        titles_path: Path | str | None = None,
        client: httpx.Client | None = None,
    ):
        self._owns_db = db is None
        self.db: Session = db or SessionLocal()
        self.sleep_s = sleep_s
        self.titles_path = Path(titles_path) if titles_path else DEFAULT_TITLES_PATH
        self._owns_client = client is None
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        self._titles_cache: dict[str, str] | None = None

    def close(self):
        if self._owns_client:
            self.client.close()
        if self._owns_db:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def load_titles(self) -> dict[str, str]:
        if self._titles_cache is None:
            with open(self.titles_path, encoding="utf-8") as f:
                payload = json.load(f)
            self._titles_cache = payload.get("titles", {}) or {}
        return self._titles_cache

    def fetch_all(
        self,
        tickers: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, str]:
        """Fetch the [start_date, end_date] range for each ticker.

        Defaults to fetching yesterday only (the cron use case). Returns
        {ticker: status} where status is 'ok', 'no_title', 'no_data', or 'error'.
        """
        if end_date is None:
            end_date = date.today() - timedelta(days=1)
        if start_date is None:
            start_date = end_date

        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        results: dict[str, str] = {}
        titles = self.load_titles()
        for ticker in tickers:
            title = titles.get(ticker)
            if not title:
                logger.warning("No Wikipedia title mapped for %s; skipping", ticker)
                results[ticker] = "no_title"
                continue
            try:
                results[ticker] = self.fetch_one(ticker, title, start_date, end_date)
            except Exception:
                logger.exception("Wikipedia fetch failed for %s", ticker)
                results[ticker] = "error"
            time.sleep(self.sleep_s)
        return results

    def fetch_one(
        self, ticker: str, title: str, start_date: date, end_date: date
    ) -> str:
        """Fetch one ticker over [start, end]. Returns ok / no_data / error.

        Empty response across all chunks counts as 'no_data' (and we still
        write zero rows so the series stays dense for feature math).
        """
        all_views: dict[date, int] = {}
        any_response = False
        for chunk_start, chunk_end in _date_chunks(start_date, end_date, MAX_DAYS_PER_CALL):
            chunk = self._fetch_chunk(title, chunk_start, chunk_end)
            if chunk is None:
                continue
            any_response = True
            all_views.update(chunk)

        # Densify: stub missing days with 0 across the requested range.
        cur = start_date
        while cur <= end_date:
            views = all_views.get(cur, 0)
            self._upsert(ticker, cur, views, title)
            cur += timedelta(days=1)
        self.db.commit()

        if not any_response:
            return "no_data"
        return "ok"

    def _fetch_chunk(
        self, title: str, start_date: date, end_date: date
    ) -> dict[date, int] | None:
        """Fetch one date-range chunk. Returns {date: views} or None on 404/error."""
        encoded = quote(title, safe="")
        url = f"{BASE_URL}/{encoded}/daily/{_fmt(start_date)}/{_fmt(end_date)}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.client.get(url)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Wikipedia HTTP error for %s (%s..%s) attempt %d: %s",
                    title, start_date, end_date, attempt, exc,
                )
                if attempt == MAX_RETRIES:
                    return None
                time.sleep(BACKOFF_BASE ** attempt)
                continue

            if resp.status_code == 404:
                logger.info(
                    "Wikipedia 404 for %s (%s..%s) — no data or unknown title",
                    title, start_date, end_date,
                )
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(
                    "Wikipedia %d for %s attempt %d", resp.status_code, title, attempt,
                )
                if attempt == MAX_RETRIES:
                    return None
                time.sleep(BACKOFF_BASE ** attempt)
                continue
            if resp.status_code != 200:
                logger.warning(
                    "Wikipedia %d for %s — giving up on chunk", resp.status_code, title,
                )
                return None

            try:
                data = resp.json()
            except ValueError:
                logger.exception("Wikipedia non-JSON response for %s", title)
                return None
            return self._parse_items(data.get("items") or [])

        return None

    @staticmethod
    def _parse_items(items: list[dict]) -> dict[date, int]:
        out: dict[date, int] = {}
        for item in items:
            d = _parse_ts(item.get("timestamp", ""))
            if d is None:
                continue
            views = item.get("views")
            if views is None:
                continue
            try:
                out[d] = int(views)
            except (TypeError, ValueError):
                continue
        return out

    def _upsert(
        self, ticker: str, view_date: date, page_views: int, title: str
    ) -> None:
        existing = (
            self.db.query(WikipediaPageviews)
            .filter_by(ticker=ticker, view_date=view_date)
            .first()
        )
        if existing is None:
            self.db.add(
                WikipediaPageviews(
                    ticker=ticker,
                    view_date=view_date,
                    page_views=page_views,
                    wikipedia_title=title,
                    fetched_at=datetime.utcnow(),
                )
            )
        else:
            existing.page_views = page_views
            existing.wikipedia_title = title
            existing.fetched_at = datetime.utcnow()
