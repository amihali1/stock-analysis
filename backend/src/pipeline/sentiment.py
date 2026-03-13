"""Sentiment analysis pipeline: fetch headlines → analyze with Ollama → store scores."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.db.models import Stock, SentimentScore
from src.db.session import SessionLocal
from src.services.ollama_client import OllamaClient
from src.services.headline_fetcher import FinvizFetcher, NewsApiFetcher, RedditFetcher, Headline
from src.config import get_settings

logger = logging.getLogger(__name__)

SENTIMENT_PROMPT = """You are a financial sentiment analyst. Analyze the following news headline about stock ticker {ticker} and return a JSON object with exactly these fields:

- "sentiment": a float from -1.0 (extremely bearish) to 1.0 (extremely bullish), where 0.0 is neutral
- "confidence": a float from 0.0 to 1.0 indicating how confident you are in your assessment
- "reasoning": a brief 1-sentence explanation

Headline: "{headline}"

Respond with ONLY the JSON object, no other text:"""


class SentimentResult(BaseModel):
    sentiment: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class SentimentAnalyzer:
    def __init__(self, db: Session | None = None):
        self._owns_db = db is None
        self.db = db or SessionLocal()
        self.ollama = OllamaClient()
        self.fetchers = [FinvizFetcher(), NewsApiFetcher(), RedditFetcher()]

    def close(self):
        if self._owns_db:
            self.db.close()

    async def analyze_ticker(self, ticker: str) -> dict:
        """Fetch headlines and analyze sentiment for a single ticker.

        Returns summary dict with composite score and individual scores.
        """
        # Ensure stock exists
        stock = self.db.query(Stock).filter_by(ticker=ticker).first()
        if stock is None:
            stock = Stock(ticker=ticker)
            self.db.add(stock)
            self.db.commit()

        # Fetch headlines from all sources
        headlines: list[Headline] = []
        for fetcher in self.fetchers:
            try:
                headlines.extend(fetcher.fetch(ticker))
            except Exception:
                logger.exception(f"{ticker}: headline fetcher failed")

        if not headlines:
            logger.warning(f"{ticker}: no headlines found from any source")
            return {"ticker": ticker, "headlines": 0, "scores": []}

        logger.info(f"{ticker}: analyzing {len(headlines)} headlines")

        # Analyze each headline with Ollama
        scores: list[SentimentResult] = []
        for headline in headlines:
            try:
                result = await self._analyze_headline(ticker, headline)
                if result:
                    scores.append(result)
            except Exception:
                logger.exception(f"{ticker}: failed to analyze headline: {headline.title[:60]}")

        # Compute composite score (confidence-weighted average)
        composite = _weighted_average(scores) if scores else None

        return {
            "ticker": ticker,
            "headlines": len(headlines),
            "scores_computed": len(scores),
            "composite_sentiment": composite,
        }

    async def _analyze_headline(self, ticker: str, headline: Headline) -> SentimentResult | None:
        """Analyze a single headline with Ollama and store the result."""
        prompt = SENTIMENT_PROMPT.format(ticker=ticker, headline=headline.title)

        raw_response = await self.ollama.generate(prompt)

        # Try to parse as JSON, then fallback to regex
        import json as _json
        try:
            parsed = _json.loads(raw_response)
        except _json.JSONDecodeError:
            from src.services.ollama_client import _extract_json
            parsed = _extract_json(raw_response)

        try:
            result = SentimentResult(**parsed)
        except Exception:
            result = _fallback_parse(raw_response)
            if result is None:
                logger.warning(f"Could not parse sentiment for: {headline.title[:60]}")
                return None

        # Store in DB
        score = SentimentScore(
            ticker=ticker,
            date=headline.date or date.today(),
            source=headline.source,
            headline=headline.title,
            sentiment=result.sentiment,
            confidence=result.confidence,
            reasoning=result.reasoning,
            raw_response=raw_response,
        )
        self.db.add(score)
        self.db.commit()

        return result

    async def analyze_all(self, tickers: list[str] | None = None) -> dict[str, dict]:
        """Analyze sentiment for all tickers."""
        if tickers is None:
            tickers = get_settings().default_watchlist

        results = {}
        for ticker in tickers:
            try:
                results[ticker] = await self.analyze_ticker(ticker)
            except Exception:
                logger.exception(f"Sentiment analysis failed for {ticker}")
                results[ticker] = {"ticker": ticker, "error": True}

        return results


def _weighted_average(scores: list[SentimentResult]) -> float:
    """Compute confidence-weighted average sentiment."""
    total_weight = sum(s.confidence for s in scores)
    if total_weight == 0:
        return 0.0
    return sum(s.sentiment * s.confidence for s in scores) / total_weight


def _fallback_parse(text: str) -> SentimentResult | None:
    """Try to extract sentiment from unstructured text via regex."""
    import re

    # Look for numbers that could be sentiment
    numbers = re.findall(r"[-+]?(?:0\.\d+|1\.0|0)", text)
    if len(numbers) >= 2:
        try:
            sentiment = float(numbers[0])
            confidence = float(numbers[1])
            if -1.0 <= sentiment <= 1.0 and 0.0 <= confidence <= 1.0:
                return SentimentResult(
                    sentiment=sentiment,
                    confidence=confidence,
                    reasoning="parsed from unstructured response",
                )
        except ValueError:
            pass
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from src.db.models import Base
    from src.db.session import engine

    Base.metadata.create_all(engine)

    analyzer = SentimentAnalyzer()
    try:
        test_tickers = ["AAPL", "NVDA"]
        results = asyncio.run(analyzer.analyze_all(tickers=test_tickers))
        for ticker, data in results.items():
            if "error" in data:
                print(f"  {ticker}: ERROR")
            else:
                score = data.get("composite_sentiment")
                score_str = f"{score:.3f}" if score is not None else "N/A"
                print(f"  {ticker}: {data['headlines']} headlines, {data['scores_computed']} scored, composite={score_str}")
    finally:
        analyzer.close()
