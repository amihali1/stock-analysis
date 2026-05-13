"""Smoke-check Phase 3 dual-direction inference end-to-end.

Loads the drop and rise directional models, predicts on a handful of watchlist
tickers, and runs the full Ensemble → rec_ranker pipeline. Prints both candidate
sides for each ticker plus the dedup'd top-K so any obvious regressions
(missing pickles, dimension mismatch, NaN prob) surface before scheduler run.

Usage:
    python -m scripts.validate_dual_directional
    python -m scripts.validate_dual_directional --tickers AAPL,MSFT,NVDA,SPY,AMD
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func

from src.db.models import PriceHistory, SentimentScore, TechnicalIndicator
from src.db.session import SessionLocal
from src.models.directional import DirectionalModel
from src.models.ensemble import Ensemble, SignalInputs
from src.models.volatility import VolatilityModel
from src.pipeline.rec_ranker import Candidate, select_candidates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("validate_dual")

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "SPY", "AMD"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    drop_model = DirectionalModel(direction="drop")
    drop_model.load()
    rise_model = DirectionalModel(direction="rise")
    rise_model.load()
    logger.info("Loaded drop+rise models")

    vol_model = VolatilityModel()
    try:
        vol_model.load()
    except Exception:
        logger.warning("Volatility model unavailable — using 0.25 default")
        vol_model = None

    ensemble = Ensemble()
    db = SessionLocal()
    candidates: list[Candidate] = []
    rows = []
    try:
        for ticker in tickers:
            ind = (
                db.query(TechnicalIndicator)
                .filter_by(ticker=ticker)
                .order_by(TechnicalIndicator.date.desc())
                .first()
            )
            if ind is None:
                logger.warning("%s: no technical indicators — skipping", ticker)
                continue
            price = (
                db.query(PriceHistory)
                .filter_by(ticker=ticker)
                .order_by(PriceHistory.date.desc())
                .first()
            )
            if price is None or not price.close:
                logger.warning("%s: no price — skipping", ticker)
                continue

            features = {
                "rsi_14": ind.rsi_14 or 50,
                "macd": ind.macd or 0,
                "macd_signal": ind.macd_signal or 0,
                "macd_histogram": ind.macd_histogram or 0,
                "bb_percent_b": ind.bb_percent_b or 0.5,
                "bb_upper": ind.bb_upper or price.close,
                "bb_lower": ind.bb_lower or price.close,
                "sma_50": ind.sma_50 or price.close,
                "sma_200": ind.sma_200 or price.close,
                "sma_crossover": ind.sma_crossover or 0,
                "volume_zscore": ind.volume_zscore or 0,
                "return_5d_lag": 0.0,
                "return_10d_lag": 0.0,
                "return_20d_lag": 0.0,
                "close_to_sma50_ratio": price.close / (ind.sma_50 or price.close),
                "close_to_sma200_ratio": price.close / (ind.sma_200 or price.close),
                "volatility_20d": 0.2,
            }

            drop_prob, _ = drop_model.predict(features)
            rise_prob, _ = rise_model.predict(features)

            sent = (
                db.query(func.avg(SentimentScore.sentiment), func.avg(SentimentScore.confidence))
                .filter_by(ticker=ticker)
                .first()
            )
            sent_score = float(sent[0]) if sent and sent[0] is not None else 0.0
            sent_conf = float(sent[1]) if sent and sent[1] is not None else 0.0

            predicted_vol = 0.25
            if vol_model is not None:
                try:
                    pv = vol_model.predict_latest(ticker, db=db)
                    if pv is not None:
                        predicted_vol = pv
                except Exception:
                    pass

            inputs = SignalInputs(
                ticker=ticker,
                drop_prob=drop_prob,
                rise_prob=rise_prob,
                predicted_vol=predicted_vol,
                sentiment_score=sent_score,
                sentiment_confidence=sent_conf,
                current_price=price.close,
            )
            scores = ensemble.score(inputs)
            rows.append((ticker, drop_prob, rise_prob, scores))
            for s in scores:
                candidates.append(Candidate(
                    ticker=ticker, score=s, direction=s.direction,
                    extras={"price_close": price.close},
                ))
    finally:
        db.close()

    print()
    print(f"{'ticker':<8}{'drop_p':>8}{'rise_p':>8}{'bear':>8}{'bull':>8}")
    for ticker, dp, rp, scores in rows:
        bear = next(s for s in scores if s.direction == "drop")
        bull = next(s for s in scores if s.direction == "rise")
        print(f"{ticker:<8}{dp:>8.3f}{rp:>8.3f}{bear.score:>8.3f}{bull.score:>8.3f}")

    selected = select_candidates(candidates, top_k=args.top_k)
    print()
    print(f"Top-{args.top_k} after dedup (one direction per ticker):")
    print(f"{'rank':<6}{'ticker':<8}{'dir':<6}{'score':>8}")
    for i, c in enumerate(selected, 1):
        print(f"{i:<6}{c.ticker:<8}{c.direction:<6}{c.score.score:>8.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
