"""Sentiment analysis pipeline: fetch headlines → analyze with Ollama → store scores."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.db.models import Stock, SentimentScore
from src.db.session import SessionLocal
from src.services.ollama_client import OllamaClient
from src.services.headline_fetcher import FinvizFetcher, YahooRssFetcher, Headline
from src.config import get_settings

logger = logging.getLogger(__name__)


# Strip these from Stock.name to derive an alias usable for substring matching.
# Order matters: longer/more-specific suffixes first so " Inc." doesn't shadow
# ", Inc." and leave a trailing comma. Repeated stripping iterates until stable.
_COMPANY_SUFFIXES = (
    ", Incorporated", " Incorporated",
    ", Inc.", " Inc.", ", Inc", " Inc",
    ", Corporation", " Corporation", ", Corp.", " Corp.",
    ", Company", " Company", ", Co.", " Co.",
    ", Companies", " Companies",
    ", Ltd.", " Ltd.", " Limited",
    " plc", " PLC", " N.V.", " S.A.", " AG", " SE",
    " Holdings", " Holding", " Group",
    " Class A", " Class B", " Class C",
)
_COMPANY_PREFIXES = ("The ",)
# Connector words left dangling after suffix removal: "Merck & Co." → "Merck &",
# "Eli Lilly and Company" → "Eli Lilly and". Trim these so the alias is a clean
# noun phrase that actually appears in headlines.
_TRAILING_CONNECTORS = (" &", " and", " or", "&")


def _normalize_company_name(name: str) -> str:
    """Strip corporate suffixes/prefixes so the residue is a usable alias.

    'Apple Inc.' → 'Apple'. 'The Boeing Company' → 'Boeing'.
    'Advanced Micro Devices, Inc.' → 'Advanced Micro Devices'.
    'Merck & Co., Inc.' → 'Merck'. 'Eli Lilly and Company' → 'Eli Lilly'.
    Iterates because some names stack suffixes ('Holdings, Inc.') and need
    a second connector-trim pass after the main suffix pass.
    """
    out = name.strip()
    changed = True
    while changed:
        changed = False
        for suf in _COMPANY_SUFFIXES:
            if out.endswith(suf):
                out = out[: -len(suf)].rstrip(",").rstrip()
                changed = True
                break
        for conn in _TRAILING_CONNECTORS:
            if out.endswith(conn):
                out = out[: -len(conn)].rstrip(",").rstrip()
                changed = True
                break
    for prefix in _COMPANY_PREFIXES:
        if out.startswith(prefix):
            out = out[len(prefix):].strip()
    return out


# Sector / index ETF alias overrides. The normalized State Street name
# ("State Street Materials Select Sector SPDR ETF" → "State Street Materials
# Select Sector SPDR") never appears in financial headlines, so the
# normalized-name path produces a dead alias. Map each ETF to sector-keyword
# phrases that actually show up in market commentary. Phrases must be
# specific enough to avoid false positives (e.g. "utilities sector" not
# "power") since the relevance filter is word-boundary substring, not NER.
_ETF_SECTOR_ALIASES: dict[str, list[str]] = {
    "XLB": ["materials sector", "materials stocks"],
    "XLC": ["communication services", "communications sector"],
    "XLE": ["energy sector", "energy stocks", "oil stocks"],
    "XLF": ["financial sector", "financials sector", "bank stocks"],
    "XLI": ["industrial sector", "industrials sector", "industrial stocks"],
    "XLK": ["tech sector", "technology sector", "tech stocks"],
    "XLP": ["consumer staples", "staples sector"],
    "XLRE": ["real estate sector", "REIT", "REITs"],
    "XLU": ["utilities sector", "utility sector", "utility stocks"],
    "XLV": ["health care sector", "healthcare sector"],
    "XLY": ["consumer discretionary", "discretionary sector"],
    "SPY": ["S&P 500", "S&P500"],
    "QQQ": ["Nasdaq 100", "Nasdaq-100"],
    "IWM": ["Russell 2000", "small caps", "small-cap stocks"],
    "^VIX": ["VIX", "volatility index", "fear gauge"],
}


def _ticker_aliases(ticker: str, stock_name: str | None) -> list[str]:
    """Return list of substrings that, if present in a headline, indicate
    the headline is at least nominally about the ticker.

    Always includes the bare symbol. Adds the corporate name (suffix-stripped)
    when available. For known sector/index ETFs, also includes
    sector-keyword phrases since their official corporate name never appears
    in headlines. Short residues (<= 2 chars) are dropped so the filter
    doesn't degenerate to matching every headline.
    """
    aliases: list[str] = [ticker.upper()]
    etf_extra = _ETF_SECTOR_ALIASES.get(ticker.upper(), [])
    # ETF corporate names ("State Street ...", "Invesco ...", "iShares ...")
    # match unrelated issuer-news headlines and aren't actually about the
    # fund. Skip the stock_name alias path entirely when ETF aliases exist.
    if stock_name and not etf_extra:
        normalized = _normalize_company_name(stock_name)
        if len(normalized) >= 3 and normalized.upper() != ticker.upper():
            aliases.append(normalized)
    aliases.extend(etf_extra)
    return aliases


def _is_relevant_to_ticker(headline: str, aliases: list[str]) -> bool:
    """True if any alias appears as a whole word in the headline.

    Word-boundary (\\b) match: 'AMD' matches 'AMD shares' but not 'PYRAMID';
    'Intel' matches 'Intel Foundry' but not 'Intelligence'. Case-insensitive.
    """
    if not aliases:
        return True
    for alias in aliases:
        pattern = rf"\b{re.escape(alias)}\b"
        if re.search(pattern, headline, re.IGNORECASE):
            return True
    return False

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
        self.fetchers = [FinvizFetcher(), YahooRssFetcher()]
        settings = get_settings()
        self.max_headline_age_days = settings.sentiment_max_headline_age_days
        self.headline_concurrency = settings.sentiment_headline_concurrency

    def close(self):
        if self._owns_db:
            self.db.close()

    async def analyze_ticker(self, ticker: str, db: Session | None = None) -> dict:
        """Fetch headlines and analyze sentiment for a single ticker.

        Returns summary dict with composite score and individual scores.

        `db` defaults to the analyzer's session; `analyze_all` overrides it
        with a per-ticker session so concurrent calls don't share state.
        """
        db = db or self.db
        # Ensure stock exists
        stock = db.query(Stock).filter_by(ticker=ticker).first()
        if stock is None:
            stock = Stock(ticker=ticker)
            db.add(stock)
            db.commit()

        # Fetch headlines from all sources
        headlines: list[Headline] = []
        for fetcher in self.fetchers:
            try:
                headlines.extend(fetcher.fetch(ticker))
            except Exception:
                logger.exception(f"{ticker}: headline fetcher failed")

        if not headlines:
            logger.warning(f"{ticker}: no headlines found from any source")
            return {"ticker": ticker, "headlines": 0, "scores_computed": 0, "composite_sentiment": None}

        # Recency gate: skip headlines older than the configured window so we
        # don't burn Ollama time scoring month-old news that has no bearing on
        # a 5-day directional prediction. Headlines with no parseable date are
        # kept (assumed-recent) — better than dropping a real signal.
        cutoff = date.today() - timedelta(days=self.max_headline_age_days)
        recent = [h for h in headlines if h.date is None or h.date >= cutoff]
        dropped = len(headlines) - len(recent)

        if not recent:
            logger.info(
                f"{ticker}: all {len(headlines)} headlines older than {self.max_headline_age_days}d — skipping"
            )
            return {"ticker": ticker, "headlines": len(headlines), "scores_computed": 0, "composite_sentiment": None}

        if dropped:
            logger.info(
                f"{ticker}: {len(recent)} recent headlines "
                f"({dropped} dropped as older than {self.max_headline_age_days}d)"
            )

        # Relevance gate: Yahoo RSS / Finviz tag plenty of headlines to a ticker
        # that are actually about a different company (sector pieces, sibling
        # names, recession think-pieces). Ollama dutifully scores them anyway
        # — its own reasoning has been observed saying "this is about a
        # different company" while still returning a sentiment value. Filter
        # before the LLM call: cheaper, deterministic, and saves the 5-10s/
        # headline Ollama latency.
        aliases = _ticker_aliases(ticker, stock.name if stock else None)
        relevant = [h for h in recent if _is_relevant_to_ticker(h.title, aliases)]
        off_ticker = len(recent) - len(relevant)
        if off_ticker:
            logger.info(
                f"{ticker}: dropped {off_ticker} off-ticker headlines "
                f"(aliases={aliases})"
            )

        if not relevant:
            logger.info(f"{ticker}: 0 relevant headlines after alias filter — skipping")
            return {
                "ticker": ticker,
                "headlines": len(headlines),
                "scores_computed": 0,
                "composite_sentiment": None,
            }

        logger.info(f"{ticker}: analyzing {len(relevant)} relevant headlines")
        headlines = relevant

        # Analyze headlines concurrently. Ollama is the per-call bottleneck
        # (5-10s on the homelab GPU), and a serial loop made the sentiment job
        # the longest task in the daily run (~2-3h for the full watchlist).
        # Each _analyze_headline is now pure (no DB) and we batch-commit at
        # the end of the ticker — one transaction per ticker, not per headline.
        headline_sem = asyncio.Semaphore(self.headline_concurrency)

        async def _bounded(h: Headline):
            async with headline_sem:
                return await self._analyze_headline(ticker, h)

        gathered = await asyncio.gather(
            *(_bounded(h) for h in headlines), return_exceptions=True
        )

        scores: list[SentimentResult] = []
        rows: list[SentimentScore] = []
        for headline, outcome in zip(headlines, gathered):
            if isinstance(outcome, BaseException):
                logger.exception(
                    f"{ticker}: failed to analyze headline: {headline.title[:60]}",
                    exc_info=outcome,
                )
                continue
            result, raw_response = outcome
            if result is None:
                continue
            scores.append(result)
            rows.append(SentimentScore(
                ticker=ticker,
                date=headline.date or date.today(),
                source=headline.source,
                headline=headline.title,
                sentiment=result.sentiment,
                confidence=result.confidence,
                reasoning=result.reasoning,
                raw_response=raw_response,
            ))

        if rows:
            db.add_all(rows)
            db.commit()

        # Compute composite score (confidence-weighted average)
        composite = _weighted_average(scores) if scores else None

        return {
            "ticker": ticker,
            "headlines": len(headlines),
            "scores_computed": len(scores),
            "composite_sentiment": composite,
        }

    async def _analyze_headline(
        self, ticker: str, headline: Headline
    ) -> tuple[SentimentResult | None, str]:
        """Score a single headline with Ollama. Pure — no DB writes.

        Returns (parsed result or None, raw Ollama response). The caller
        batches DB inserts so we open one transaction per ticker instead of
        one per headline (per-headline commits serialized analyze_ticker even
        though Ollama calls are now async).
        """
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
                return None, raw_response

        return result, raw_response

    async def analyze_all(
        self, tickers: list[str] | None = None, concurrency: int = 3
    ) -> dict[str, dict]:
        """Analyze sentiment for all tickers concurrently.

        Tickers are processed with a semaphore-bounded `asyncio.gather` so
        headline-fetcher I/O for one ticker overlaps with Ollama scoring for
        another. Each ticker uses its own SQLAlchemy session — sessions are
        not safe to share across concurrent coroutines.

        With `OLLAMA_NUM_PARALLEL=1` (default), Ollama itself still serializes
        generation, so concurrency=3 is the practical sweet spot: enough to
        keep the fetcher off the critical path without piling up at Ollama.
        """
        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        sem = asyncio.Semaphore(concurrency)

        async def _one(ticker: str) -> tuple[str, dict]:
            async with sem:
                local_db = SessionLocal()
                try:
                    return ticker, await self.analyze_ticker(ticker, db=local_db)
                except Exception:
                    logger.exception(f"Sentiment analysis failed for {ticker}")
                    return ticker, {"ticker": ticker, "error": True}
                finally:
                    local_db.close()

        pairs = await asyncio.gather(*(_one(t) for t in tickers))
        return dict(pairs)


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
