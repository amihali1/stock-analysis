from datetime import date, datetime
from sqlalchemy import (
    Boolean, Column, Integer, String, Float, Date, DateTime, ForeignKey, Index, Text, Enum,
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class Strategy(str, enum.Enum):
    SHORT = "short"
    OPTIONS = "options"


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), unique=True, nullable=False, index=True)
    name = Column(String(200))
    sector = Column(String(100))
    exchange = Column(String(50))

    prices = relationship("PriceHistory", back_populates="stock")
    indicators = relationship("TechnicalIndicator", back_populates="stock")
    sentiments = relationship("SentimentScore", back_populates="stock")
    predictions = relationship("ModelPrediction", back_populates="stock")
    recommendations = relationship("Recommendation", back_populates="stock")


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_price_ticker_date", "ticker", "date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    adj_close = Column(Float)

    stock = relationship("Stock", back_populates="prices")


class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"
    __table_args__ = (
        Index("ix_indicator_ticker_date", "ticker", "date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    date = Column(Date, nullable=False)
    rsi_14 = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    macd_histogram = Column(Float)
    bb_upper = Column(Float)
    bb_middle = Column(Float)
    bb_lower = Column(Float)
    bb_percent_b = Column(Float)
    sma_50 = Column(Float)
    sma_200 = Column(Float)
    sma_crossover = Column(Float)  # 1.0 = golden cross, -1.0 = death cross, 0 = neutral
    volume_zscore = Column(Float)

    stock = relationship("Stock", back_populates="indicators")


class SentimentScore(Base):
    __tablename__ = "sentiment_scores"
    __table_args__ = (
        Index("ix_sentiment_ticker_date", "ticker", "date"),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    date = Column(Date, nullable=False)
    source = Column(String(50))  # finviz, newsapi, reddit
    headline = Column(Text)
    sentiment = Column(Float)  # -1.0 to 1.0
    confidence = Column(Float)  # 0.0 to 1.0
    reasoning = Column(Text)
    raw_response = Column(Text)  # Raw LLM response for debugging
    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="sentiments")


class ModelPrediction(Base):
    __tablename__ = "model_predictions"
    __table_args__ = (
        Index("ix_prediction_ticker_date_model", "ticker", "date", "model_name"),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    date = Column(Date, nullable=False)
    model_name = Column(String(50), nullable=False)  # directional_xgb, volatility_lstm
    signal = Column(Float)  # Model-specific signal value
    confidence = Column(Float)
    metadata_json = Column(Text)  # Additional model-specific data as JSON
    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="predictions")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_rec_date_strategy", "date", "strategy"),
        Index("ix_rec_date_direction", "date", "direction"),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    date = Column(Date, nullable=False)
    direction = Column(String(5), nullable=False, default="short")  # long, short
    strategy = Column(String(32), nullable=False)  # short, options, spread, long, call_options, bull_spread
    score = Column(Float, nullable=False)  # Ensemble score
    directional_signal = Column(Float)
    volatility_signal = Column(Float)
    sentiment_signal = Column(Float)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    target_price = Column(Float)
    position_size = Column(Float)  # Dollar amount
    max_loss = Column(Float)  # Dollar amount
    contracts = Column(Integer)  # For options
    strike = Column(Float)  # For options
    expiry = Column(Date)  # For options
    option_type = Column(String(4))  # call, put
    risk_type = Column(String(10), default="undefined")  # defined, undefined
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="recommendations")


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    direction = Column(String(5), nullable=False, default="short")  # long, short
    strategy = Column(String(32), nullable=False)  # short, options, spread, long, call_options, bull_spread
    status = Column(String(10), nullable=False, default="open")  # open, closed
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float)
    target_price = Column(Float)
    position_size = Column(Float)
    max_loss = Column(Float)
    contracts = Column(Integer)
    strike = Column(Float)
    option_type = Column(String(4))
    exit_price = Column(Float)
    pnl = Column(Float)  # Realized P&L in dollars
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime)
    score = Column(Float)  # Ensemble score at time of trade

    stock = relationship("Stock")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    alert_type = Column(String(20), nullable=False)  # stop_loss, target_hit, high_conviction
    message = Column(Text, nullable=False)
    details_json = Column(Text)
    acknowledged = Column(Integer, default=0)  # 0 = unread, 1 = acknowledged
    created_at = Column(DateTime, default=datetime.utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), unique=True, nullable=False, index=True)
    sector = Column(String(100))
    added_at = Column(DateTime, default=datetime.utcnow)


class TradingLog(Base):
    __tablename__ = "trading_log"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    action = Column(String(20), nullable=False)  # submit, block, cancel, fill, error
    strategy = Column(String(20))
    qty = Column(Float)
    side = Column(String(10))
    order_id = Column(String(100))
    reason = Column(Text)  # Why blocked or error details
    passed_safety = Column(Integer, default=1)  # 0 = blocked, 1 = passed
    created_at = Column(DateTime, default=datetime.utcnow)


class AlpacaPosition(Base):
    __tablename__ = "alpaca_positions"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False, index=True)
    qty = Column(Float, nullable=False)
    side = Column(String(10), default="long")
    avg_entry_price = Column(Float)
    current_price = Column(Float)
    market_value = Column(Float)
    unrealized_pl = Column(Float)
    synced_at = Column(DateTime, default=datetime.utcnow)


class AlpacaOrder(Base):
    __tablename__ = "alpaca_orders"

    id = Column(Integer, primary_key=True)
    alpaca_order_id = Column(String(100), unique=True, index=True)
    ticker = Column(String(10), nullable=False)
    side = Column(String(10))
    qty = Column(Float)
    order_type = Column(String(20))
    status = Column(String(20))
    filled_price = Column(Float)
    submitted_at = Column(DateTime)
    filled_at = Column(DateTime)
    synced_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, unique=True, index=True)
    total_exposure = Column(Float, default=0)
    total_max_loss = Column(Float, default=0)
    open_positions = Column(Integer, default=0)
    beta_to_spy = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class OptionsChain(Base):
    __tablename__ = "options_chain"
    __table_args__ = (
        Index("ix_options_chain_ticker_exp", "ticker", "expiration"),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False, index=True)
    expiration = Column(String(10), nullable=False)  # YYYY-MM-DD
    strike = Column(Float, nullable=False)
    option_type = Column(String(4), nullable=False)  # call, put
    bid = Column(Float, default=0)
    ask = Column(Float, default=0)
    last = Column(Float, default=0)
    volume = Column(Integer, default=0)
    open_interest = Column(Integer, default=0)
    implied_vol = Column(Float, default=0)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class AlertSetting(Base):
    __tablename__ = "alert_settings"

    id = Column(Integer, primary_key=True)
    channel = Column(String(20), nullable=False)  # discord, telegram
    webhook_url = Column(String(500))
    bot_token = Column(String(200))
    chat_id = Column(String(100))
    enabled = Column(Integer, default=1)
    score_threshold = Column(Float, default=0.75)
    alert_stop_loss = Column(Integer, default=1)
    alert_target_hit = Column(Integer, default=1)
    alert_high_conviction = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class SystemSetting(Base):
    """Runtime-mutable settings (trading mode, auto-execute, thresholds)."""

    __tablename__ = "system_settings"

    key = Column(String(50), primary_key=True)
    value = Column(String(500), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SentimentHistory(Base):
    """Daily aggregated sentiment per ticker (P9-004)."""

    __tablename__ = "sentiment_history"
    __table_args__ = (
        Index("ix_sentiment_history_ticker_date", "ticker", "date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    date = Column(Date, nullable=False)
    sentiment_score = Column(Float)
    confidence = Column(Float)
    article_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class EarningsCalendar(Base):
    """Upcoming earnings dates per ticker (P9-005)."""

    __tablename__ = "earnings_calendar"
    __table_args__ = (
        Index("ix_earnings_calendar_ticker_date", "ticker", "earnings_date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    earnings_date = Column(Date, nullable=False)
    source = Column(String(20), default="yfinance")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class AnalystRating(Base):
    """Per-firm analyst rating change for a ticker (P10-001)."""

    __tablename__ = "analyst_ratings"
    __table_args__ = (
        Index("ix_analyst_ratings_ticker_date", "ticker", "date"),
        Index(
            "ix_analyst_ratings_unique",
            "ticker", "date", "firm", "to_grade",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    date = Column(Date, nullable=False)
    firm = Column(String(100))
    from_grade = Column(String(50))
    to_grade = Column(String(50))
    action = Column(String(20))  # up, down, init, main, reit
    source = Column(String(20), default="yfinance")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class OptionsSnapshot(Base):
    """Daily per-ticker summary of options-market state used as model features."""

    __tablename__ = "options_snapshots"
    __table_args__ = (
        Index("ix_options_snapshot_ticker_date", "ticker", "date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    date = Column(Date, nullable=False)
    iv_atm_30d = Column(Float)
    iv_atm_90d = Column(Float)
    iv_rank_252d = Column(Float)
    iv_percentile_252d = Column(Float)
    put_call_skew_25d = Column(Float)
    term_structure_slope = Column(Float)
    has_options = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShortInterestSnapshot(Base):
    """Per-ticker snapshot of FINRA short-interest report (P10-003).

    yfinance.info returns the latest two FINRA bi-monthly settlement reports
    via `dateShortInterest`/`sharesShort` and `sharesShortPreviousMonthDate`/
    `sharesShortPriorMonth`. Each backfill run can therefore add up to two
    historical points per ticker. Real time-series features (z-score, change-pct)
    become meaningful after ~6-12 months of accumulated snapshots.
    """

    __tablename__ = "short_interest_snapshots"
    __table_args__ = (
        Index("ix_short_interest_ticker_report", "ticker", "report_date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    report_date = Column(Date, nullable=False)  # FINRA settlement date
    shares_short = Column(Float)
    short_percent_of_float = Column(Float)
    short_ratio_days_to_cover = Column(Float)
    has_data = Column(Integer, default=1)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class WikipediaPageviews(Base):
    """Per-ticker daily Wikipedia page-view count (P10-008).

    Sourced from Wikimedia REST per-article daily pageviews endpoint. The
    `wikipedia_title` is the resolved English Wikipedia title used for the
    lookup (kept for diagnostics — same ticker in different runs should always
    resolve to the same title via the hand-curated config map). Missing days in
    the Wikimedia response are stubbed with `page_views=0` to keep the series
    dense for z-score/baseline math.
    """

    __tablename__ = "wikipedia_pageviews"
    __table_args__ = (
        Index("ix_wikipedia_pageviews_ticker_date", "ticker", "view_date", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    view_date = Column(Date, nullable=False)
    page_views = Column(Integer, default=0)
    wikipedia_title = Column(String(200))
    fetched_at = Column(DateTime, default=datetime.utcnow)


class SecCikMap(Base):
    """Ticker → SEC CIK resolution cache (P10-005).

    Sourced once daily from https://www.sec.gov/files/company_tickers.json. Kept
    in DB rather than an in-memory dict so multi-process workers and the
    backfill script share the same view, and so we have an audit trail of when
    a ticker's CIK last reconciled with SEC. CIK is stored as zero-padded
    10-digit string (the form EDGAR URLs require).
    """

    __tablename__ = "sec_cik_map"
    __table_args__ = (
        Index("ix_sec_cik_map_ticker", "ticker", unique=True),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    cik = Column(String(10), nullable=False)
    company_name = Column(String(200))
    fetched_at = Column(DateTime, default=datetime.utcnow)


class InsiderTransaction(Base):
    """SEC Form 4 insider transaction (P10-005).

    One row per insider per filing. Codes follow SEC Section 16 — the meaningful
    ones for our model are P (open-market purchase) and S (open-market sale).
    Grants (A), tax events (F, M), and gifts (G) are stored for completeness but
    filtered out of feature aggregation since they don't carry directional
    intent. `accession_number` is globally unique across SEC filings and
    de-dupes re-fetches without coordination.
    """

    __tablename__ = "insider_transactions"
    __table_args__ = (
        Index("ix_insider_tx_accession", "accession_number", unique=True),
        Index("ix_insider_tx_ticker_date", "ticker", "transaction_date"),
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), nullable=False)
    accession_number = Column(String(30), nullable=False)
    filing_date = Column(Date, nullable=False)
    transaction_date = Column(Date, nullable=False)
    insider_name = Column(String(200))
    insider_title = Column(String(200))
    transaction_code = Column(String(2))  # P/S/A/D/G/F/M/J/...
    shares = Column(Float)
    price_per_share = Column(Float)
    total_value = Column(Float)
    shares_owned_after = Column(Float)
    is_director = Column(Boolean, default=False)
    is_officer = Column(Boolean, default=False)
    is_10pct_owner = Column(Boolean, default=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)
