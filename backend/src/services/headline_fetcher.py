"""Fetch headlines from Finviz and Yahoo Finance RSS for sentiment analysis.

NewsAPI and Reddit fetchers were removed in May 2026 — NewsAPI key was
invalid and Reddit's PRAW search returned 0 posts on every ticker since
the project's inception (silent inner-try/except). Yahoo Finance RSS
replaces them as the second-source signal: per-ticker feed, no auth, no
rate limits in practice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import feedparser
from finvizfinance.quote import finvizfinance

logger = logging.getLogger(__name__)


@dataclass
class Headline:
    title: str
    source: str  # finviz, yahoo_rss
    date: date | None = None
    url: str = ""


class FinvizFetcher:
    """Fetch news headlines from Finviz for a given ticker."""

    def fetch(self, ticker: str, max_headlines: int = 10) -> list[Headline]:
        # Finviz has no quote pages for index symbols (^VIX 404'd with a full
        # stack trace every sentiment run). Yahoo RSS remains the source for
        # index headlines.
        if ticker.startswith("^"):
            logger.debug(f"{ticker}: index symbol, skipping Finviz")
            return []
        try:
            stock = finvizfinance(ticker)
            news_df = stock.ticker_news()

            if news_df is None or news_df.empty:
                logger.warning(f"{ticker}: no Finviz headlines found")
                return []

            headlines = []
            for _, row in news_df.head(max_headlines).iterrows():
                headlines.append(
                    Headline(
                        title=str(row.get("Title", row.get("title", ""))),
                        source="finviz",
                        date=_parse_date(row.get("Date", row.get("date"))),
                        url=str(row.get("Link", row.get("link", ""))),
                    )
                )

            logger.info(f"{ticker}: fetched {len(headlines)} Finviz headlines")
            return headlines

        except Exception:
            logger.exception(f"{ticker}: failed to fetch Finviz headlines")
            return []


class YahooRssFetcher:
    """Fetch headlines from Yahoo Finance's per-ticker RSS feed.

    Endpoint: https://feeds.finance.yahoo.com/rss/2.0/headline?s={TICKER}&region=US&lang=en-US

    Yahoo's RSS has no API key, no rate limits in practice, and reliably
    returns ~10-20 recent items per ticker with RFC-822 pubDates. Empty
    feed = ticker has no recent news (returns []).
    """

    URL_TEMPLATE = (
        "https://feeds.finance.yahoo.com/rss/2.0/headline"
        "?s={ticker}&region=US&lang=en-US"
    )

    def fetch(self, ticker: str, max_headlines: int = 10) -> list[Headline]:
        url = self.URL_TEMPLATE.format(ticker=ticker)
        try:
            parsed = feedparser.parse(url)

            if parsed.bozo and not parsed.entries:
                logger.warning(f"{ticker}: Yahoo RSS parse failed ({parsed.bozo_exception!r})")
                return []

            headlines = []
            for entry in parsed.entries[:max_headlines]:
                headlines.append(
                    Headline(
                        title=str(entry.get("title", "")),
                        source="yahoo_rss",
                        date=_parse_rfc822(entry.get("published")),
                        url=str(entry.get("link", "")),
                    )
                )

            logger.info(f"{ticker}: fetched {len(headlines)} Yahoo RSS headlines")
            return headlines

        except Exception:
            logger.exception(f"{ticker}: failed to fetch Yahoo RSS headlines")
            return []


def _parse_date(val) -> date | None:
    """Best-effort date parsing for Finviz rows."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        if hasattr(val, "date"):
            return val.date()
        return datetime.fromisoformat(str(val).replace("Z", "+00:00")).date()
    except Exception:
        return None


def _parse_rfc822(val) -> date | None:
    """Parse RFC-822 timestamps used in RSS pubDate fields."""
    if not val:
        return None
    try:
        return parsedate_to_datetime(str(val)).date()
    except (TypeError, ValueError):
        return None
