"""Fetch headlines from Finviz and NewsAPI for sentiment analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from finvizfinance.quote import finvizfinance

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Headline:
    title: str
    source: str  # finviz, newsapi, reddit
    date: date | None = None
    url: str = ""


class FinvizFetcher:
    """Fetch news headlines from Finviz for a given ticker."""

    def fetch(self, ticker: str, max_headlines: int = 10) -> list[Headline]:
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


class NewsApiFetcher:
    """Fetch headlines from NewsAPI (requires API key)."""

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.newsapi_key

    def fetch(self, ticker: str, max_headlines: int = 10) -> list[Headline]:
        if not self.api_key:
            logger.debug("NewsAPI key not configured, skipping")
            return []

        try:
            from newsapi import NewsApiClient

            api = NewsApiClient(api_key=self.api_key)
            results = api.get_everything(
                q=ticker,
                language="en",
                sort_by="publishedAt",
                page_size=max_headlines,
            )

            headlines = []
            for article in results.get("articles", []):
                headlines.append(
                    Headline(
                        title=article.get("title", ""),
                        source="newsapi",
                        date=_parse_date(article.get("publishedAt")),
                        url=article.get("url", ""),
                    )
                )

            logger.info(f"{ticker}: fetched {len(headlines)} NewsAPI headlines")
            return headlines

        except Exception:
            logger.exception(f"{ticker}: failed to fetch NewsAPI headlines")
            return []


class RedditFetcher:
    """Fetch ticker mentions from Reddit using PRAW."""

    SUBREDDITS = ["wallstreetbets", "stocks", "options"]

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.reddit_client_id
        self.client_secret = settings.reddit_client_secret
        self.user_agent = settings.reddit_user_agent

    def fetch(self, ticker: str, max_headlines: int = 10) -> list[Headline]:
        if not self.client_id or not self.client_secret:
            logger.debug("Reddit credentials not configured, skipping")
            return []

        try:
            import praw

            reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                user_agent=self.user_agent,
            )

            headlines = []
            for sub_name in self.SUBREDDITS:
                if len(headlines) >= max_headlines:
                    break
                try:
                    subreddit = reddit.subreddit(sub_name)
                    for post in subreddit.search(
                        f"${ticker} OR {ticker}", sort="new", time_filter="week", limit=5
                    ):
                        text = post.title
                        # Include top comment if available
                        post.comment_sort = "best"
                        post.comments.replace_more(limit=0)
                        if post.comments:
                            top_comment = post.comments[0].body[:200]
                            text = f"{post.title} | Top comment: {top_comment}"

                        headlines.append(
                            Headline(
                                title=text,
                                source="reddit",
                                date=date.fromtimestamp(post.created_utc),
                                url=f"https://reddit.com{post.permalink}",
                            )
                        )
                        if len(headlines) >= max_headlines:
                            break
                except Exception:
                    logger.debug(f"Failed to search r/{sub_name} for {ticker}")

            logger.info(f"{ticker}: fetched {len(headlines)} Reddit headlines")
            return headlines

        except Exception:
            logger.exception(f"{ticker}: failed to fetch Reddit headlines")
            return []


def _parse_date(val) -> date | None:
    """Best-effort date parsing."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        from datetime import datetime

        if hasattr(val, "date"):
            return val.date()
        return datetime.fromisoformat(str(val).replace("Z", "+00:00")).date()
    except Exception:
        return None
