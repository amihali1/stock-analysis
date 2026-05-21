"""Pydantic response models for API endpoints."""

from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    ollama: str
    ollama_models: list[str] = []
    last_pipeline_run: str | None = None
    scheduler_jobs: dict[str, str] = {}


class TickerInfo(BaseModel):
    ticker: str
    name: str | None = None
    sector: str | None = None
    exchange: str | None = None
    latest_price_date: date | None = None
    latest_close: float | None = None


class TickerListResponse(BaseModel):
    tickers: list[TickerInfo]
    count: int


class SpreadLegResponse(BaseModel):
    option_type: str  # call|put
    action: str  # buy|sell
    strike: float
    premium: float | None = None
    contracts: int | None = None


class RecommendationResponse(BaseModel):
    id: int | None = None
    ticker: str
    date: date
    strategy: str
    score: float
    directional_signal: float | None = None
    volatility_signal: float | None = None
    sentiment_signal: float | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    position_size: float | None = None
    max_loss: float | None = None
    contracts: int | None = None
    strike: float | None = None
    expiry: date | None = None
    option_type: str | None = None
    legs: list[SpreadLegResponse] | None = None
    risk_type: str = "undefined"
    notes: str | None = None


class RecommendationsListResponse(BaseModel):
    recommendations: list[RecommendationResponse]
    count: int


class PricePoint(BaseModel):
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


class IndicatorPoint(BaseModel):
    date: date
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    volume_zscore: float | None = None


class SentimentEntry(BaseModel):
    date: date
    source: str | None = None
    headline: str | None = None
    sentiment: float | None = None
    confidence: float | None = None
    reasoning: str | None = None


class WatchlistItem(BaseModel):
    ticker: str
    sector: str | None = None
    added_at: datetime | None = None


class WatchlistResponse(BaseModel):
    tickers: list[WatchlistItem]
    count: int


class WatchlistAddRequest(BaseModel):
    tickers: list[str]


class AnalysisResponse(BaseModel):
    ticker: str
    name: str | None = None
    sector: str | None = None
    latest_price: PricePoint | None = None
    prices: list[PricePoint] = []
    indicators: list[IndicatorPoint] = []
    sentiments: list[SentimentEntry] = []
    recommendations: list[RecommendationResponse] = []
