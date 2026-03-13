"""APScheduler cron jobs for daily pipeline automation."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.db.models import Base
from src.db.session import engine

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _record_run(job_name: str, status: str):
    """Record job completion time in health endpoint state."""
    from src.api.routes.health import last_job_runs
    last_job_runs[job_name] = f"{status} at {datetime.now().isoformat()}"


def job_fetch_prices():
    """6:00 AM ET — Fetch new price data for all tickers."""
    logger.info("Scheduler: starting price fetch")
    try:
        from src.pipeline.data_fetcher import DataFetcher
        Base.metadata.create_all(engine)
        fetcher = DataFetcher()
        results = fetcher.fetch_daily(period="5d")
        fetcher.close()
        total = sum(v for v in results.values() if v > 0)
        logger.info(f"Scheduler: price fetch complete — {total} new rows across {len(results)} tickers")
        _record_run("fetch_prices", f"ok ({total} rows)")
    except Exception:
        logger.exception("Scheduler: price fetch failed")
        _record_run("fetch_prices", "error")


def job_compute_indicators():
    """6:30 AM ET — Compute technical indicators."""
    logger.info("Scheduler: starting indicator computation")
    try:
        from src.pipeline.feature_eng import FeatureEngineer
        eng = FeatureEngineer()
        results = eng.compute_all()
        eng.close()
        total = sum(v for v in results.values() if v > 0)
        logger.info(f"Scheduler: indicators complete — {total} new rows")
        _record_run("compute_indicators", f"ok ({total} rows)")
    except Exception:
        logger.exception("Scheduler: indicator computation failed")
        _record_run("compute_indicators", "error")


async def job_sentiment():
    """7:00 AM ET — Fetch headlines and run sentiment analysis."""
    logger.info("Scheduler: starting sentiment analysis")
    try:
        from src.pipeline.sentiment import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        results = await analyzer.analyze_all()
        analyzer.close()
        scored = sum(1 for v in results.values() if v.get("scores_computed", 0) > 0)
        logger.info(f"Scheduler: sentiment complete — {scored} tickers scored")
        _record_run("sentiment", f"ok ({scored} tickers)")
    except Exception:
        logger.exception("Scheduler: sentiment analysis failed")
        _record_run("sentiment", "error")


def job_generate_recommendations():
    """7:30 AM ET — Run ML models and generate recommendations."""
    logger.info("Scheduler: starting recommendation generation")
    try:
        from src.db.session import SessionLocal
        from src.db.models import PriceHistory, TechnicalIndicator, SentimentScore, Recommendation, Stock
        from src.models.directional import DirectionalModel
        from src.models.ensemble import Ensemble, SignalInputs
        from src.models.position_sizer import PositionSizer
        from src.config import get_settings
        from datetime import date
        from sqlalchemy import func

        db = SessionLocal()
        settings = get_settings()
        dir_model = DirectionalModel()
        ensemble = Ensemble()
        sizer = PositionSizer()
        today = date.today()
        count = 0

        try:
            for ticker in settings.default_watchlist:
                # Get latest indicator row
                ind = (
                    db.query(TechnicalIndicator)
                    .filter_by(ticker=ticker)
                    .order_by(TechnicalIndicator.date.desc())
                    .first()
                )
                if not ind:
                    continue

                # Get latest price
                price = (
                    db.query(PriceHistory)
                    .filter_by(ticker=ticker)
                    .order_by(PriceHistory.date.desc())
                    .first()
                )
                if not price or not price.close:
                    continue

                # Build features for directional model
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

                try:
                    dir_prob, dir_conf = dir_model.predict(features)
                except Exception:
                    dir_prob, dir_conf = 0.5, 0.0

                # Get latest sentiment
                sent = (
                    db.query(func.avg(SentimentScore.sentiment), func.avg(SentimentScore.confidence))
                    .filter_by(ticker=ticker)
                    .first()
                )
                sent_score = float(sent[0]) if sent and sent[0] is not None else 0.0
                sent_conf = float(sent[1]) if sent and sent[1] is not None else 0.0

                inputs = SignalInputs(
                    ticker=ticker,
                    directional_prob=dir_prob,
                    directional_confidence=dir_conf,
                    predicted_vol=0.25,  # Default until LSTM is run on sequences
                    sentiment_score=sent_score,
                    sentiment_confidence=sent_conf,
                    current_price=price.close,
                )

                score = ensemble.score(inputs)

                # Only generate recommendations for strong signals
                if score.score < 0.5:
                    continue

                # Generate short recommendation
                short_rec = sizer.size_short(score, price.close)
                if short_rec:
                    rec = Recommendation(
                        ticker=ticker, date=today, strategy="short",
                        score=score.score,
                        directional_signal=score.directional_signal,
                        volatility_signal=score.volatility_signal,
                        sentiment_signal=score.sentiment_signal,
                        entry_price=short_rec.entry_price,
                        stop_loss=short_rec.stop_loss,
                        target_price=short_rec.target_price,
                        position_size=short_rec.position_size,
                        max_loss=short_rec.max_loss,
                    )
                    db.add(rec)
                    count += 1

                # Generate options recommendation
                options_rec = sizer.size_options(score, price.close)
                if options_rec:
                    rec = Recommendation(
                        ticker=ticker, date=today, strategy="options",
                        score=score.score,
                        directional_signal=score.directional_signal,
                        volatility_signal=score.volatility_signal,
                        sentiment_signal=score.sentiment_signal,
                        entry_price=options_rec.entry_price,
                        stop_loss=options_rec.entry_price * 1.05,
                        target_price=options_rec.strike,
                        position_size=options_rec.position_size,
                        max_loss=options_rec.max_loss,
                        contracts=options_rec.contracts,
                        strike=options_rec.strike,
                        option_type=options_rec.option_type,
                    )
                    db.add(rec)
                    count += 1

            db.commit()
        finally:
            db.close()

        logger.info(f"Scheduler: recommendations complete — {count} new recommendations")
        _record_run("recommendations", f"ok ({count} recs)")

    except Exception:
        logger.exception("Scheduler: recommendation generation failed")
        _record_run("recommendations", "error")


def init_scheduler():
    """Initialize and start the scheduler with market-hours cron jobs (Eastern Time)."""
    # Weekdays only (mon-fri)
    scheduler.add_job(job_fetch_prices, CronTrigger(hour=6, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="fetch_prices", replace_existing=True)
    scheduler.add_job(job_compute_indicators, CronTrigger(hour=6, minute=30, timezone="US/Eastern", day_of_week="mon-fri"), id="compute_indicators", replace_existing=True)
    scheduler.add_job(job_sentiment, CronTrigger(hour=7, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="sentiment", replace_existing=True)
    scheduler.add_job(job_generate_recommendations, CronTrigger(hour=7, minute=30, timezone="US/Eastern", day_of_week="mon-fri"), id="recommendations", replace_existing=True)

    scheduler.start()
    logger.info("Scheduler started with 4 daily jobs (6:00/6:30/7:00/7:30 AM ET, Mon-Fri)")


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down")
