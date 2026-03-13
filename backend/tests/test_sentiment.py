"""Tests for sentiment analysis pipeline."""

import json
import pytest
import httpx
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Stock, SentimentScore
from src.pipeline.sentiment import SentimentAnalyzer, SentimentResult, _weighted_average, _fallback_parse
from src.services.headline_fetcher import Headline


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


class TestSentimentAnalyzerIntegration:
    @pytest.mark.asyncio
    async def test_analyze_with_mocked_ollama(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)

        # Mock Finviz to return a single headline
        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title="Apple beats Q4 earnings expectations", source="finviz", date=date.today()),
            ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )

        # Mock Ollama to return structured JSON
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

        # Verify stored in DB
        scores = db_session.query(SentimentScore).filter_by(ticker="AAPL").all()
        assert len(scores) == 1
        assert scores[0].sentiment == 0.8
        assert scores[0].raw_response == response_payload
