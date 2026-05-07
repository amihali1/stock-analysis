"""Tests for sentiment analysis pipeline."""

import json
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
