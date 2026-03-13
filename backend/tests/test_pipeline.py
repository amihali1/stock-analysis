"""Integration test for the data pipeline."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Stock, PriceHistory, TechnicalIndicator
from src.pipeline.data_fetcher import DataFetcher
from src.pipeline.feature_eng import FeatureEngineer
from src.pipeline.runner import run_pipeline


@pytest.fixture
def db_session(tmp_path):
    """Create a temporary SQLite DB for testing."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestDataFetcher:
    def test_fetch_single_ticker(self, db_session):
        fetcher = DataFetcher(db=db_session)
        results = fetcher.fetch_daily(tickers=["AAPL"], period="1mo")

        assert results["AAPL"] > 0
        prices = db_session.query(PriceHistory).filter_by(ticker="AAPL").all()
        assert len(prices) == results["AAPL"]

        # Verify stock was created
        stock = db_session.query(Stock).filter_by(ticker="AAPL").first()
        assert stock is not None

    def test_idempotent_fetch(self, db_session):
        fetcher = DataFetcher(db=db_session)
        fetcher.fetch_daily(tickers=["SPY"], period="1mo")
        results2 = fetcher.fetch_daily(tickers=["SPY"], period="1mo")
        assert results2["SPY"] == 0

    def test_invalid_ticker(self, db_session):
        fetcher = DataFetcher(db=db_session)
        results = fetcher.fetch_daily(tickers=["INVALIDTICKER999"])
        # Should not crash, returns 0 or -1
        assert "INVALIDTICKER999" in results


class TestFeatureEngineer:
    def test_compute_features(self, db_session):
        # First fetch enough data
        fetcher = DataFetcher(db=db_session)
        fetcher.fetch_daily(tickers=["MSFT"], period="1y")

        eng = FeatureEngineer(db=db_session)
        count = eng.compute_features("MSFT")
        assert count > 0

        indicators = db_session.query(TechnicalIndicator).filter_by(ticker="MSFT").all()
        assert len(indicators) == count

        # Spot-check a row has values
        ind = indicators[-1]
        assert ind.rsi_14 is not None
        assert 0 <= ind.rsi_14 <= 100
        assert ind.macd is not None
