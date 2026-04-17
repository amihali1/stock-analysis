"""Ensemble scorer: combines directional, volatility, and sentiment signals."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SignalInputs(BaseModel):
    """Raw signals from individual models."""
    ticker: str
    directional_prob: float = Field(ge=0, le=1, description="Probability of >3% drop")
    directional_confidence: float = Field(ge=0, le=1)
    predicted_vol: float = Field(ge=0, description="Predicted annualized volatility")
    sentiment_score: float = Field(ge=-1, le=1, description="Composite sentiment (-1 to 1)")
    sentiment_confidence: float = Field(ge=0, le=1)
    current_price: float = Field(gt=0)


class EnsembleScore(BaseModel):
    """Combined score output from the ensemble."""
    ticker: str
    score: float = Field(description="Overall bearish score (0-1, higher = more bearish)")
    directional_signal: float
    volatility_signal: float
    sentiment_signal: float
    meets_confidence: bool = Field(default=True, description="Whether all individual signals meet the minimum confidence threshold")


class Ensemble:
    """Combine directional, volatility, and sentiment into a single score."""

    def __init__(
        self,
        weight_directional: float = 0.4,
        weight_volatility: float = 0.3,
        weight_sentiment: float = 0.3,
        min_confidence: float | None = None,
    ):
        total = weight_directional + weight_volatility + weight_sentiment
        self.w_dir = weight_directional / total
        self.w_vol = weight_volatility / total
        self.w_sent = weight_sentiment / total
        if min_confidence is not None:
            self.min_confidence = min_confidence
        else:
            from src.config import get_settings
            self.min_confidence = get_settings().min_confidence

    def score(self, inputs: SignalInputs) -> EnsembleScore:
        """Compute ensemble score from individual signals.

        Directional signal: probability of drop (0-1, higher = more bearish)
        Volatility signal: higher predicted vol = more opportunity for options (0-1)
        Sentiment signal: negative sentiment = bearish = higher signal (0-1)

        Each individual signal must meet min_confidence for the recommendation
        to be considered actionable (meets_confidence=True).
        """
        dir_signal = inputs.directional_prob

        # Normalize vol to 0-1 range (cap at 100% annualized vol)
        vol_signal = min(inputs.predicted_vol, 1.0)

        # Convert sentiment from [-1, 1] to [0, 1] where negative = high signal
        sent_signal = (1 - inputs.sentiment_score) / 2  # -1 -> 1.0, 0 -> 0.5, 1 -> 0.0

        # Weight by confidence
        sent_signal *= inputs.sentiment_confidence

        combined = (
            self.w_dir * dir_signal
            + self.w_vol * vol_signal
            + self.w_sent * sent_signal
        )

        # Check if each model independently meets the confidence threshold
        meets_confidence = (
            dir_signal >= self.min_confidence
            and vol_signal >= self.min_confidence
            and sent_signal >= self.min_confidence
        )

        return EnsembleScore(
            ticker=inputs.ticker,
            score=round(combined, 4),
            directional_signal=round(dir_signal, 4),
            volatility_signal=round(vol_signal, 4),
            sentiment_signal=round(sent_signal, 4),
            meets_confidence=meets_confidence,
        )
