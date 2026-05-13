"""Ensemble scorer: combines directional, volatility, and sentiment signals.

Phase 3 (bullish-side build, 2026-05-13): scoring is now dual-direction.
`Ensemble.score()` returns a list with one bearish and one bullish EnsembleScore,
each carrying its own `direction` and direction-appropriate sentiment polarity.
The downstream ranker dedups by ticker and keeps the higher-scoring direction.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SignalInputs(BaseModel):
    """Raw signals from individual models."""
    ticker: str
    drop_prob: float = Field(ge=0, le=1, description="Probability of >3% drop in 5 trading days")
    rise_prob: float = Field(ge=0, le=1, description="Probability of >3% rise in 5 trading days")
    predicted_vol: float = Field(ge=0, description="Predicted annualized volatility")
    sentiment_score: float = Field(ge=-1, le=1, description="Composite sentiment (-1 to 1)")
    sentiment_confidence: float = Field(ge=0, le=1)
    current_price: float = Field(gt=0)


class EnsembleScore(BaseModel):
    """Combined score output from the ensemble for one direction."""
    ticker: str
    direction: str = Field(default="drop", description="'drop' (bearish) or 'rise' (bullish)")
    score: float = Field(description="Composite score (0-1, higher = stronger signal in this direction)")
    directional_signal: float
    volatility_signal: float
    sentiment_signal: float
    meets_confidence: bool = Field(default=True, description="Whether sentiment confidence clears the floor")


class Ensemble:
    """Combine directional, volatility, and sentiment into per-direction scores."""

    def __init__(
        self,
        weight_directional: float = 0.4,
        weight_volatility: float = 0.3,
        weight_sentiment: float = 0.3,
        directional_base_rate: float | None = None,
        min_directional_lift: float | None = None,
        min_sentiment_confidence: float | None = None,
        # Deprecated — single absolute floor was unreachable for the calibrated
        # rare-event directional model. When supplied, sets min_sentiment_confidence.
        min_confidence: float | None = None,
    ):
        total = weight_directional + weight_volatility + weight_sentiment
        self.w_dir = weight_directional / total
        self.w_vol = weight_volatility / total
        self.w_sent = weight_sentiment / total

        from src.config import get_settings
        settings = get_settings()
        self.base_rate = directional_base_rate if directional_base_rate is not None else settings.directional_base_rate
        self.min_directional_lift = min_directional_lift if min_directional_lift is not None else settings.min_directional_lift
        if min_sentiment_confidence is not None:
            self.min_sentiment_confidence = min_sentiment_confidence
        elif min_confidence is not None:
            self.min_sentiment_confidence = min_confidence
        else:
            self.min_sentiment_confidence = settings.min_sentiment_confidence

    def score(self, inputs: SignalInputs) -> list[EnsembleScore]:
        """Return one EnsembleScore per direction (drop, rise).

        Bearish branch: directional = drop_prob, sentiment polarity (1 - sent)/2
        (negative sentiment helps), confidence-weighted.

        Bullish branch: directional = rise_prob, sentiment polarity (1 + sent)/2
        (positive sentiment helps), confidence-weighted.

        Volatility contributes to both branches identically (vol is direction-blind
        — high vol is an options opportunity regardless of direction).
        """
        vol_signal = min(inputs.predicted_vol, 1.0)

        meets_confidence = inputs.sentiment_confidence >= self.min_sentiment_confidence

        bear_sent = (1 - inputs.sentiment_score) / 2 * inputs.sentiment_confidence
        bear_combined = (
            self.w_dir * inputs.drop_prob
            + self.w_vol * vol_signal
            + self.w_sent * bear_sent
        )
        bear = EnsembleScore(
            ticker=inputs.ticker,
            direction="drop",
            score=round(bear_combined, 4),
            directional_signal=round(inputs.drop_prob, 4),
            volatility_signal=round(vol_signal, 4),
            sentiment_signal=round(bear_sent, 4),
            meets_confidence=meets_confidence,
        )

        bull_sent = (1 + inputs.sentiment_score) / 2 * inputs.sentiment_confidence
        bull_combined = (
            self.w_dir * inputs.rise_prob
            + self.w_vol * vol_signal
            + self.w_sent * bull_sent
        )
        bull = EnsembleScore(
            ticker=inputs.ticker,
            direction="rise",
            score=round(bull_combined, 4),
            directional_signal=round(inputs.rise_prob, 4),
            volatility_signal=round(vol_signal, 4),
            sentiment_signal=round(bull_sent, 4),
            meets_confidence=meets_confidence,
        )

        return [bear, bull]
