"""Tests for sentiment analysis pipeline."""

import asyncio
import json
import time
import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, SentimentScore
from src.pipeline.sentiment import SentimentAnalyzer, SentimentResult, _weighted_average, _fallback_parse
from src.services.headline_fetcher import Headline, YahooRssFetcher, _parse_date


@pytest.fixture
def db_session(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestSentimentResult:
    def test_valid_result(self):
        r = SentimentResult(sentiment=0.5, confidence=0.8, reasoning="test")
        assert r.sentiment == 0.5

    def test_bounds(self):
        with pytest.raises(Exception):
            SentimentResult(sentiment=1.5, confidence=0.5)


class TestWeightedAverage:
    def test_simple(self):
        scores = [
            SentimentResult(sentiment=0.5, confidence=1.0, reasoning="a"),
            SentimentResult(sentiment=-0.5, confidence=1.0, reasoning="b"),
        ]
        assert _weighted_average(scores) == 0.0

    def test_confidence_weighting(self):
        scores = [
            SentimentResult(sentiment=1.0, confidence=0.9, reasoning="strong"),
            SentimentResult(sentiment=-1.0, confidence=0.1, reasoning="weak"),
        ]
        avg = _weighted_average(scores)
        assert avg > 0.5  # Should lean heavily positive


class TestFallbackParse:
    def test_extracts_numbers(self):
        text = "The sentiment is 0.7 with confidence 0.8 because earnings beat."
        result = _fallback_parse(text)
        assert result is not None
        assert result.sentiment == 0.7

    def test_no_numbers(self):
        result = _fallback_parse("No useful data here")
        assert result is None


class TestYahooRssFetcher:
    def test_parses_typical_feed(self, monkeypatch):
        sample = {
            "bozo": False,
            "entries": [
                {
                    "title": "Apple announces new iPhone",
                    "link": "https://example.com/a",
                    "published": "Mon, 05 May 2026 14:00:00 +0000",
                },
                {
                    "title": "AAPL beats Q2 earnings",
                    "link": "https://example.com/b",
                    "published": "Tue, 06 May 2026 09:30:00 +0000",
                },
            ],
        }

        class _Parsed:
            bozo = sample["bozo"]
            entries = sample["entries"]

        monkeypatch.setattr("src.services.headline_fetcher.feedparser.parse", lambda url: _Parsed())

        results = YahooRssFetcher().fetch("AAPL")
        assert len(results) == 2
        assert results[0].source == "yahoo_rss"
        assert results[0].title == "Apple announces new iPhone"
        assert results[0].date == date(2026, 5, 5)
        assert results[1].date == date(2026, 5, 6)

    def test_empty_feed_returns_empty_list(self, monkeypatch):
        class _Parsed:
            bozo = False
            entries = []

        monkeypatch.setattr("src.services.headline_fetcher.feedparser.parse", lambda url: _Parsed())
        assert YahooRssFetcher().fetch("ZZZZ") == []

    def test_bozo_with_no_entries_returns_empty(self, monkeypatch):
        class _Parsed:
            bozo = True
            bozo_exception = ValueError("bad feed")
            entries = []

        monkeypatch.setattr("src.services.headline_fetcher.feedparser.parse", lambda url: _Parsed())
        assert YahooRssFetcher().fetch("AAPL") == []


class TestParseDate:
    def test_pandas_timestamp_normalized_to_date(self):
        import pandas as pd

        ts = pd.Timestamp("2026-05-07 09:30:00")
        parsed = _parse_date(ts)
        assert type(parsed) is date
        assert parsed == date(2026, 5, 7)

    def test_datetime_normalized_to_date(self):
        from datetime import datetime as _dt

        parsed = _parse_date(_dt(2026, 5, 7, 9, 30))
        assert type(parsed) is date
        assert parsed == date(2026, 5, 7)

    def test_plain_date_passes_through(self):
        d = date(2026, 5, 7)
        assert _parse_date(d) is d


class TestSentimentAnalyzerIntegration:
    @pytest.mark.asyncio
    async def test_analyze_with_mocked_ollama(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title="Apple beats Q4 earnings expectations", source="finviz", date=date.today()),
            ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        response_payload = json.dumps({
            "sentiment": 0.8,
            "confidence": 0.9,
            "reasoning": "Strong earnings beat is bullish"
        })

        async def mock_generate(self, prompt, model=None):
            return response_payload

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        result = await analyzer.analyze_ticker("AAPL")

        assert result["headlines"] == 1
        assert result["scores_computed"] == 1
        assert result["composite_sentiment"] == pytest.approx(0.8, abs=0.01)

        scores = db_session.query(SentimentScore).filter_by(ticker="AAPL").all()
        assert len(scores) == 1
        assert scores[0].sentiment == 0.8
        assert scores[0].raw_response == response_payload


class TestRecencyGate:
    @pytest.mark.asyncio
    async def test_stale_headlines_filtered(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)
        analyzer.max_headline_age_days = 7

        cutoff_minus_1 = date.today() - timedelta(days=8)
        recent = date.today() - timedelta(days=2)

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title="OLD: ancient news", source="finviz", date=cutoff_minus_1),
                Headline(title="FRESH: today's beat", source="finviz", date=recent),
            ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        calls: list[str] = []

        async def mock_generate(self, prompt, model=None):
            calls.append(prompt)
            return json.dumps({"sentiment": -0.4, "confidence": 0.7, "reasoning": "x"})

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        result = await analyzer.analyze_ticker("AAPL")

        assert result["scores_computed"] == 1, "only the recent headline should reach Ollama"
        assert len(calls) == 1
        assert "FRESH" in calls[0]
        assert "OLD" not in calls[0]

    @pytest.mark.asyncio
    async def test_no_recent_headlines_short_circuits(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)
        analyzer.max_headline_age_days = 7

        ancient = date.today() - timedelta(days=30)

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title="OLD A", source="finviz", date=ancient),
                Headline(title="OLD B", source="finviz", date=ancient),
            ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        called = False

        async def mock_generate(self, prompt, model=None):
            nonlocal called
            called = True
            return ""

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        result = await analyzer.analyze_ticker("AAPL")

        assert called is False, "Ollama must not be called when no headlines pass the recency gate"
        assert result["scores_computed"] == 0
        assert result["composite_sentiment"] is None
        assert result["headlines"] == 2  # raw fetched count preserved for diagnostics

    @pytest.mark.asyncio
    async def test_undated_headlines_kept(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)
        analyzer.max_headline_age_days = 7

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [Headline(title="No date", source="finviz", date=None)]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        async def mock_generate(self, prompt, model=None):
            return json.dumps({"sentiment": 0.0, "confidence": 0.5, "reasoning": "neutral"})

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        result = await analyzer.analyze_ticker("AAPL")
        assert result["scores_computed"] == 1


class _NopSession:
    def close(self):
        pass


class TestAnalyzeAllConcurrency:
    """analyze_all parallelizes ticker work so headline-fetcher I/O overlaps
    with Ollama scoring. Regressing to a serial loop would cost ~2x throughput
    on the live pipeline."""

    @pytest.mark.asyncio
    async def test_tickers_processed_concurrently(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "src.pipeline.sentiment.SessionLocal", lambda: _NopSession()
        )
        analyzer = SentimentAnalyzer(db=db_session)

        in_flight = 0
        max_in_flight = 0
        per_call_delay = 0.05

        async def fake_analyze_ticker(self, ticker, db=None):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(per_call_delay)
            in_flight -= 1
            return {"ticker": ticker, "scores_computed": 1}

        monkeypatch.setattr(SentimentAnalyzer, "analyze_ticker", fake_analyze_ticker)

        tickers = [f"T{i}" for i in range(9)]
        t0 = time.perf_counter()
        results = await analyzer.analyze_all(tickers=tickers, concurrency=3)
        elapsed = time.perf_counter() - t0

        assert len(results) == 9
        # Concurrency cap respected — semaphore guarantees this
        assert max_in_flight <= 3
        # Concurrency actually happens — serial would max at 1
        assert max_in_flight >= 2
        # 9 tickers at concurrency=3, 0.05s each ≈ 0.15s; serial would be 0.45s.
        # Generous bound for slow CI scheduling.
        assert elapsed < 0.35, f"expected <0.35s with concurrency, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_error_in_one_ticker_does_not_kill_others(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "src.pipeline.sentiment.SessionLocal", lambda: _NopSession()
        )
        analyzer = SentimentAnalyzer(db=db_session)

        async def fake_analyze_ticker(self, ticker, db=None):
            if ticker == "BOOM":
                raise RuntimeError("simulated failure")
            return {"ticker": ticker, "scores_computed": 1}

        monkeypatch.setattr(SentimentAnalyzer, "analyze_ticker", fake_analyze_ticker)

        results = await analyzer.analyze_all(
            tickers=["AAPL", "BOOM", "NVDA"], concurrency=2
        )

        assert results["AAPL"]["scores_computed"] == 1
        assert results["NVDA"]["scores_computed"] == 1
        assert results["BOOM"].get("error") is True

    @pytest.mark.asyncio
    async def test_each_ticker_gets_own_session(self, db_session, monkeypatch):
        """Per-ticker sessions prevent SQLAlchemy state from leaking across
        concurrent coroutines. If analyze_all reverts to sharing self.db,
        this test will see the same session reused."""
        created_sessions: list[_NopSession] = []

        def session_factory():
            s = _NopSession()
            created_sessions.append(s)
            return s

        monkeypatch.setattr("src.pipeline.sentiment.SessionLocal", session_factory)
        analyzer = SentimentAnalyzer(db=db_session)

        seen_dbs: list[object] = []

        async def fake_analyze_ticker(self, ticker, db=None):
            seen_dbs.append(db)
            return {"ticker": ticker, "scores_computed": 0}

        monkeypatch.setattr(SentimentAnalyzer, "analyze_ticker", fake_analyze_ticker)

        await analyzer.analyze_all(tickers=["A", "B", "C"], concurrency=2)

        # One fresh session per ticker, none is the analyzer's injected db
        assert len(created_sessions) == 3
        assert len(set(map(id, seen_dbs))) == 3
        assert db_session not in seen_dbs
