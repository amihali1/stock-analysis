"""Tests for sentiment analysis pipeline."""

import asyncio
import json
import time
import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, SentimentScore, Stock
from src.pipeline.sentiment import (
    SentimentAnalyzer,
    SentimentResult,
    _fallback_parse,
    _is_relevant_to_ticker,
    _normalize_company_name,
    _ticker_aliases,
    _weighted_average,
)
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


class TestNormalizeCompanyName:
    def test_strips_inc(self):
        assert _normalize_company_name("Apple Inc.") == "Apple"
        assert _normalize_company_name("Amazon.com, Inc.") == "Amazon.com"

    def test_strips_corporate_suffixes(self):
        assert _normalize_company_name("Intel Corporation") == "Intel"
        assert _normalize_company_name("Bristol-Myers Squibb Company") == "Bristol-Myers Squibb"
        assert _normalize_company_name("Citigroup Inc.") == "Citigroup"

    def test_strips_the_prefix(self):
        assert _normalize_company_name("The Boeing Company") == "Boeing"

    def test_stacked_suffixes(self):
        assert _normalize_company_name("Bank of America Corporation") == "Bank of America"

    def test_preserves_compound_names(self):
        assert _normalize_company_name("Advanced Micro Devices, Inc.") == "Advanced Micro Devices"

    def test_strips_trailing_ampersand_connector(self):
        # Real cases from the 2026-05-26 dry run that produced dead aliases
        assert _normalize_company_name("Merck & Co., Inc.") == "Merck"
        assert _normalize_company_name("JPMorgan Chase & Co.") == "JPMorgan Chase"
        assert _normalize_company_name("Wells Fargo & Company") == "Wells Fargo"
        assert _normalize_company_name("Deere & Company") == "Deere"

    def test_strips_trailing_and_connector(self):
        assert _normalize_company_name("Eli Lilly and Company") == "Eli Lilly"

    def test_strips_companies_suffix(self):
        assert _normalize_company_name("Lowe's Companies, Inc.") == "Lowe's"


class TestTickerAliases:
    def test_includes_symbol(self):
        aliases = _ticker_aliases("INTC", "Intel Corporation")
        assert "INTC" in aliases
        assert "Intel" in aliases

    def test_no_name_falls_back_to_symbol_only(self):
        assert _ticker_aliases("XYZ", None) == ["XYZ"]
        assert _ticker_aliases("XYZ", "") == ["XYZ"]

    def test_short_residue_dropped(self):
        # Hypothetical name that normalizes to <3 chars wouldn't be useful
        assert _ticker_aliases("AA", "AA, Inc.") == ["AA"]

    def test_etf_sector_aliases_appended(self):
        aliases = _ticker_aliases(
            "XLU", "State Street Utilities Select Sector SPDR ETF"
        )
        assert "XLU" in aliases
        assert "utilities sector" in aliases
        assert "utility sector" in aliases
        assert not any("State Street" in a for a in aliases)

    def test_etf_skips_issuer_name(self):
        aliases = _ticker_aliases("QQQ", "Invesco QQQ Trust")
        assert "QQQ" in aliases
        assert "Nasdaq 100" in aliases
        assert not any("Invesco" in a for a in aliases)

    def test_vix_index_aliases(self):
        aliases = _ticker_aliases("^VIX", "CBOE Volatility Index")
        assert "VIX" in aliases
        assert "volatility index" in aliases

    def test_non_etf_ticker_unchanged(self):
        aliases = _ticker_aliases("AAPL", "Apple Inc.")
        assert aliases == ["AAPL", "Apple"]


class TestIsRelevantToTicker:
    """Real headlines from the 2026-05-26 INTC sentiment pollution audit.
    Off-ticker headlines must be filtered; genuine Intel mentions kept."""

    INTC_ALIASES = ["INTC", "Intel"]

    def test_drops_unrelated_viking_therapeutics(self):
        assert not _is_relevant_to_ticker(
            "Don't Buy Viking Therapeutics Stock Until You Read This",
            self.INTC_ALIASES,
        )

    def test_drops_walmart_costco(self):
        assert not _is_relevant_to_ticker(
            "Walmart vs. Costco: Which Is the Better \"Recession-Proof\" Stock to Buy Now?",
            self.INTC_ALIASES,
        )

    def test_drops_quantum_stock(self):
        assert not _is_relevant_to_ticker(
            "Why D-Wave Quantum Stock Skyrocketed Today",
            self.INTC_ALIASES,
        )

    def test_drops_pure_nvidia_earnings(self):
        assert not _is_relevant_to_ticker(
            "Nvidia Earnings Are Set to Make or Break the Chip Stock Rally",
            self.INTC_ALIASES,
        )

    def test_keeps_direct_intel_mention(self):
        assert _is_relevant_to_ticker(
            "Why Are Intel (INTC) Shares Soaring Today",
            self.INTC_ALIASES,
        )

    def test_keeps_intel_foundry(self):
        assert _is_relevant_to_ticker(
            "Intel Foundry: After A 3x Rally, Time For A Reality Check?",
            self.INTC_ALIASES,
        )

    def test_keeps_chip_compare_with_intel(self):
        # Tangential but mentions Intel directly — let Ollama judge nuance.
        assert _is_relevant_to_ticker(
            "Nvidia vs. AMD vs. Intel: Which is the best chip stock to own?",
            self.INTC_ALIASES,
        )

    def test_word_boundary_avoids_false_match(self):
        # 'AMD' is the ticker; must not match inside 'PYRAMID' or 'AMDS'
        assert not _is_relevant_to_ticker("Pyramid scheme indictment", ["AMD"])
        # But matches 'AMD shares'
        assert _is_relevant_to_ticker("AMD shares surge", ["AMD"])

    def test_case_insensitive(self):
        assert _is_relevant_to_ticker("intel shares up", ["Intel"])
        assert _is_relevant_to_ticker("APPLE BEATS Q4", ["Apple"])


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
        # Pre-seed stock with name so the alias filter accepts "Apple ..." headlines.
        db_session.add(Stock(ticker="AAPL", name="Apple Inc."))
        db_session.commit()

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


class TestRelevanceGate:
    """End-to-end: off-ticker headlines never reach Ollama and never produce
    rows. Reproduces the 2026-05-26 INTC sentiment pollution scenario."""

    @pytest.mark.asyncio
    async def test_off_ticker_headlines_excluded_from_aggregate(
        self, db_session, monkeypatch
    ):
        db_session.add(Stock(ticker="INTC", name="Intel Corporation"))
        db_session.commit()

        today = date.today()
        polluted_feed = [
            # Real off-ticker pollution from 2026-05-26 audit
            Headline(title="Don't Buy Viking Therapeutics Stock Until You Read This",
                     source="yahoo_rss", date=today),
            Headline(title="Why D-Wave Quantum Stock Skyrocketed Today",
                     source="yahoo_rss", date=today),
            Headline(title="Walmart vs. Costco: Recession-Proof Stock",
                     source="yahoo_rss", date=today),
            # Genuine Intel headlines
            Headline(title="Why Are Intel (INTC) Shares Soaring Today",
                     source="yahoo_rss", date=today),
            Headline(title="Intel Foundry Turnaround Is Gaining Traction",
                     source="yahoo_rss", date=today),
        ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: polluted_feed,
        )

        # Track which headlines Ollama actually saw.
        seen: list[str] = []
        async def mock_generate(self, prompt, model=None):
            # Extract the headline that the prompt embedded
            import re
            m = re.search(r'Headline: "([^"]+)"', prompt)
            if m:
                seen.append(m.group(1))
            return json.dumps({
                "sentiment": 0.5,
                "confidence": 0.9,
                "reasoning": "stub"
            })

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        analyzer = SentimentAnalyzer(db=db_session)
        result = await analyzer.analyze_ticker("INTC")

        # Only the 2 Intel-mentioning headlines should be scored
        assert result["scores_computed"] == 2, f"Got {result['scores_computed']}, seen={seen}"
        assert all("Intel" in h or "INTC" in h for h in seen), seen
        assert not any("Viking" in h or "D-Wave" in h or "Walmart" in h for h in seen)

        # And only 2 rows persisted
        rows = db_session.query(SentimentScore).filter_by(ticker="INTC").all()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_all_off_ticker_short_circuits(self, db_session, monkeypatch):
        db_session.add(Stock(ticker="INTC", name="Intel Corporation"))
        db_session.commit()

        # 100% pollution scenario
        polluted = [
            Headline(title="Tesla earnings preview", source="yahoo_rss", date=date.today()),
            Headline(title="Bitcoin hits new high", source="yahoo_rss", date=date.today()),
        ]
        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: polluted,
        )

        ollama_calls = 0
        async def mock_generate(self, prompt, model=None):
            nonlocal ollama_calls
            ollama_calls += 1
            return json.dumps({"sentiment": 0.0, "confidence": 1.0, "reasoning": ""})
        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        analyzer = SentimentAnalyzer(db=db_session)
        result = await analyzer.analyze_ticker("INTC")

        assert result["scores_computed"] == 0
        assert result["composite_sentiment"] is None
        assert ollama_calls == 0  # Filter saved Ollama from any work


class TestRecencyGate:
    @pytest.mark.asyncio
    async def test_stale_headlines_filtered(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)
        analyzer.max_headline_age_days = 7

        cutoff_minus_1 = date.today() - timedelta(days=8)
        recent = date.today() - timedelta(days=2)

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title="OLD AAPL ancient news", source="finviz", date=cutoff_minus_1),
                Headline(title="FRESH AAPL beat", source="finviz", date=recent),
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
            return [Headline(title="AAPL no date", source="finviz", date=None)]

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


class TestAnalyzeTickerHeadlineConcurrency:
    """analyze_ticker fans out headline-level Ollama calls with asyncio.gather.
    Serial would block on each ~5-10s Ollama call. The regression we're
    guarding: anyone reintroducing a for-loop over headlines would silently
    halve the sentiment job's throughput."""

    @pytest.mark.asyncio
    async def test_headlines_scored_concurrently(self, db_session, monkeypatch):
        analyzer = SentimentAnalyzer(db=db_session)
        analyzer.headline_concurrency = 4

        headlines = [
            Headline(title=f"AAPL H{i}", source="finviz", date=date.today())
            for i in range(6)
        ]

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return headlines

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        in_flight = 0
        max_in_flight = 0

        async def mock_generate(self, prompt, model=None):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return json.dumps({"sentiment": 0.1, "confidence": 0.7, "reasoning": "x"})

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        t0 = time.perf_counter()
        result = await analyzer.analyze_ticker("AAPL")
        elapsed = time.perf_counter() - t0

        assert result["scores_computed"] == 6
        # 6 headlines at concurrency=4, 0.05s each: 2 batches ≈ 0.10s.
        # Serial would be 0.30s. Generous bound for CI.
        assert elapsed < 0.25, f"expected concurrent execution, got {elapsed:.3f}s"
        assert max_in_flight >= 2, "headlines did not overlap"
        assert max_in_flight <= 4, "headline concurrency cap exceeded"

    @pytest.mark.asyncio
    async def test_batch_commit_writes_all_rows(self, db_session, monkeypatch):
        """Per-headline commits were replaced by a single batch commit per
        ticker. Verify all rows still land."""
        analyzer = SentimentAnalyzer(db=db_session)

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title=f"AAPL H{i}", source="finviz", date=date.today())
                for i in range(3)
            ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        async def mock_generate(self, prompt, model=None):
            return json.dumps({"sentiment": 0.2, "confidence": 0.8, "reasoning": "ok"})

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        await analyzer.analyze_ticker("AAPL")

        rows = db_session.query(SentimentScore).filter_by(ticker="AAPL").all()
        assert len(rows) == 3
        assert all(r.sentiment == 0.2 for r in rows)

    @pytest.mark.asyncio
    async def test_one_failing_headline_does_not_drop_others(
        self, db_session, monkeypatch
    ):
        """`return_exceptions=True` on the headline gather keeps a single
        Ollama failure from killing the entire ticker's batch."""
        analyzer = SentimentAnalyzer(db=db_session)

        def mock_finviz_fetch(self, ticker, max_headlines=10):
            return [
                Headline(title="AAPL GOOD-1", source="finviz", date=date.today()),
                Headline(title="AAPL BOOM",   source="finviz", date=date.today()),
                Headline(title="AAPL GOOD-2", source="finviz", date=date.today()),
            ]

        monkeypatch.setattr(
            "src.services.headline_fetcher.FinvizFetcher.fetch", mock_finviz_fetch
        )
        monkeypatch.setattr(
            "src.services.headline_fetcher.YahooRssFetcher.fetch",
            lambda self, ticker, max_headlines=10: [],
        )

        async def mock_generate(self, prompt, model=None):
            if "BOOM" in prompt:
                raise RuntimeError("ollama down")
            return json.dumps({"sentiment": 0.3, "confidence": 0.6, "reasoning": "ok"})

        monkeypatch.setattr(
            "src.services.ollama_client.OllamaClient.generate", mock_generate
        )

        result = await analyzer.analyze_ticker("AAPL")
        assert result["scores_computed"] == 2

        rows = db_session.query(SentimentScore).filter_by(ticker="AAPL").all()
        assert {r.headline for r in rows} == {"AAPL GOOD-1", "AAPL GOOD-2"}


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
