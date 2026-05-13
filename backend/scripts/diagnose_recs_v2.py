"""Per-ticker score diagnostic using the deployed v2 directional model + full Phase 9 features."""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import statistics

from sqlalchemy import func

from src.config import get_settings
from src.db.models import PriceHistory, SentimentScore, TechnicalIndicator
from src.db.session import SessionLocal
from src.db.watchlist import get_watchlist_tickers
from src.features.analyst import get_analyst_features
from src.features.earnings import get_earnings_features
from src.features.insider import get_insider_features
from src.features.macro import get_macro_features
from src.features.options import get_options_features
from src.features.sector import get_sector_features
from src.features.sentiment import get_sentiment_features
from src.features.short_interest import get_short_interest_features
from src.features.wikipedia import get_wikipedia_features
from src.models.directional import DirectionalModel
from src.models.ensemble import Ensemble, SignalInputs


def main() -> int:
    db = SessionLocal()
    settings = get_settings()
    model = DirectionalModel()
    model.load()
    ensemble = Ensemble(min_confidence=settings.min_confidence)

    print(f"min_confidence={settings.min_confidence}")
    print(f"{'ticker':<7}{'score':>7}{'dir_p':>8}{'dir_c':>8}{'sent_s':>8}{'meets':>7}")

    probs = []
    confs = []
    scores = []
    n_above_score = 0
    n_meets = 0
    for ticker in get_watchlist_tickers(db):
        if ticker.startswith("^"):
            continue
        ind = (
            db.query(TechnicalIndicator)
            .filter_by(ticker=ticker)
            .order_by(TechnicalIndicator.date.desc())
            .first()
        )
        price = (
            db.query(PriceHistory)
            .filter_by(ticker=ticker)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        if not ind or not price:
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
            "return_5d_lag": 0,
            "return_10d_lag": 0,
            "return_20d_lag": 0,
            "close_to_sma50_ratio": price.close / (ind.sma_50 or price.close),
            "close_to_sma200_ratio": price.close / (ind.sma_200 or price.close),
            "volatility_20d": 0.2,
        }
        features.update(get_options_features(db, ticker, price.date))
        features.update(get_macro_features(db, price.date))
        features.update(get_sector_features(db, ticker, price.date))
        features.update(get_sentiment_features(db, ticker, price.date))
        features.update(get_earnings_features(db, ticker, price.date))
        features.update(get_analyst_features(db, ticker, price.date))
        features.update(get_short_interest_features(db, ticker, price.date))
        features.update(get_wikipedia_features(db, ticker, price.date))
        features.update(get_insider_features(db, ticker, price.date))
        try:
            dp, dc = model.predict(features)
        except Exception:
            dp, dc = 0.5, 0.0
        probs.append(dp)
        confs.append(dc)
        sent = (
            db.query(func.avg(SentimentScore.sentiment), func.avg(SentimentScore.confidence))
            .filter_by(ticker=ticker)
            .first()
        )
        ss = float(sent[0]) if sent and sent[0] is not None else 0.0
        sc = float(sent[1]) if sent and sent[1] is not None else 0.0
        inp = SignalInputs(
            ticker=ticker,
            drop_prob=dp, rise_prob=0.0,
            predicted_vol=0.25,
            sentiment_score=ss, sentiment_confidence=sc,
            current_price=price.close,
        )
        sc_obj = next(s for s in ensemble.score(inp) if s.direction == "drop")
        scores.append(sc_obj.score)
        if sc_obj.score >= 0.5:
            n_above_score += 1
        if sc_obj.meets_confidence:
            n_meets += 1
        print(f"{ticker:<7}{sc_obj.score:>7.2f}{dp:>8.3f}{dc:>8.3f}{ss:>8.3f}{str(sc_obj.meets_confidence):>7}")

    if probs:
        probs.sort(); confs.sort(); scores.sort()
        print()
        print(f"dir_prob   min/median/max: {probs[0]:.3f} / {statistics.median(probs):.3f} / {probs[-1]:.3f}")
        print(f"dir_conf   min/median/max: {confs[0]:.3f} / {statistics.median(confs):.3f} / {confs[-1]:.3f}")
        print(f"score      min/median/max: {scores[0]:.3f} / {statistics.median(scores):.3f} / {scores[-1]:.3f}")
        print(f"score >= 0.5:        {n_above_score}/{len(probs)}")
        print(f"meets_confidence:    {n_meets}/{len(probs)}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
