from datetime import date, datetime
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Index, Text, Enum,
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
    )

    id = Column(Integer, primary_key=True)
    ticker = Column(String(10), ForeignKey("stocks.ticker"), nullable=False)
    date = Column(Date, nullable=False)
    strategy = Column(String(10), nullable=False)  # short, options
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
    strategy = Column(String(10), nullable=False)  # short, options
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
