"""APScheduler cron jobs for daily pipeline automation."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.db.models import Base
from src.db.session import engine
from src.metrics import (
    pipeline_indicators_computed_total,
    pipeline_last_run_timestamp,
    pipeline_prices_fetched_total,
    pipeline_recommendations_generated_total,
    pipeline_sentiment_runs_total,
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _record_run(job_name: str, status: str):
    """Record job completion time in health endpoint state and Prometheus gauge."""
    from src.api.routes.health import last_job_runs
    now = datetime.now()
    last_job_runs[job_name] = f"{status} at {now.isoformat()}"
    outcome = "ok" if status.startswith("ok") else ("skipped" if status.startswith("skipped") else "error")
    pipeline_last_run_timestamp.labels(job=job_name, status=outcome).set(now.timestamp())


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
        pipeline_prices_fetched_total.inc(total)
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
        pipeline_indicators_computed_total.inc(total)
        logger.info(f"Scheduler: indicators complete — {total} new rows")
        _record_run("compute_indicators", f"ok ({total} rows)")
    except Exception:
        logger.exception("Scheduler: indicator computation failed")
        _record_run("compute_indicators", "error")


async def job_sentiment():
    """7:00 AM ET — Fetch headlines, run sentiment analysis, persist daily aggregate."""
    logger.info("Scheduler: starting sentiment analysis")
    try:
        from datetime import date as _date

        from src.db.session import SessionLocal
        from src.features.sentiment import upsert_daily_sentiment
        from src.pipeline.sentiment import SentimentAnalyzer

        analyzer = SentimentAnalyzer()
        results = await analyzer.analyze_all()
        analyzer.close()
        scored = sum(1 for v in results.values() if v.get("scores_computed", 0) > 0)

        # Persist daily aggregated sentiment (P9-004) so we can compute z-scores later.
        today = _date.today()
        db = SessionLocal()
        try:
            for ticker, info in results.items():
                if info.get("scores_computed", 0) == 0:
                    continue
                upsert_daily_sentiment(
                    db, ticker, today,
                    sentiment_score=info.get("composite_sentiment"),
                    confidence=None,
                    article_count=int(info.get("scores_computed", 0)),
                )
        finally:
            db.close()

        pipeline_sentiment_runs_total.labels(status="ok").inc()
        logger.info(f"Scheduler: sentiment complete — {scored} tickers scored")
        _record_run("sentiment", f"ok ({scored} tickers)")
    except Exception:
        pipeline_sentiment_runs_total.labels(status="error").inc()
        logger.exception("Scheduler: sentiment analysis failed")
        _record_run("sentiment", "error")


def job_fetch_earnings():
    """Sunday 6:00 AM ET — Refresh upcoming earnings dates for the watchlist."""
    logger.info("Scheduler: starting earnings calendar fetch")
    try:
        from src.pipeline.earnings_fetcher import EarningsFetcher
        Base.metadata.create_all(engine)
        fetcher = EarningsFetcher()
        try:
            results = fetcher.fetch_all()
        finally:
            fetcher.close()
        ok = sum(1 for v in results.values() if v == "ok")
        logger.info("Scheduler: earnings fetch complete — %d/%d tickers ok", ok, len(results))
        _record_run("fetch_earnings", f"ok ({ok}/{len(results)} tickers)")
    except Exception:
        logger.exception("Scheduler: earnings fetch failed")
        _record_run("fetch_earnings", "error")


def job_fetch_options():
    """6:45 AM ET — Fetch daily options snapshots (IV, skew, term structure)."""
    logger.info("Scheduler: starting options snapshot fetch")
    try:
        from src.pipeline.options_fetcher import OptionsFetcher
        Base.metadata.create_all(engine)
        fetcher = OptionsFetcher()
        try:
            results = fetcher.fetch_all()
        finally:
            fetcher.close()
        ok = sum(1 for v in results.values() if v == "ok")
        logger.info(
            "Scheduler: options snapshots complete — %d/%d tickers ok",
            ok, len(results),
        )
        _record_run("fetch_options", f"ok ({ok}/{len(results)} tickers)")
    except Exception:
        logger.exception("Scheduler: options snapshot fetch failed")
        _record_run("fetch_options", "error")


def job_generate_recommendations():
    """7:30 AM ET — Run ML models and generate recommendations."""
    logger.info("Scheduler: starting recommendation generation")
    try:
        from src.config import get_settings
        from src.db.session import SessionLocal
        from src.db.models import PriceHistory, TechnicalIndicator, SentimentScore, Recommendation, Stock
        from src.models.directional import DirectionalModel
        from src.models.volatility import VolatilityModel
        from src.models.ensemble import Ensemble, SignalInputs
        from src.models.position_sizer import PositionSizer
        from src.pipeline.rec_ranker import Candidate, select_candidates
        from datetime import date
        from sqlalchemy import func

        settings = get_settings()
        db = SessionLocal()
        dir_model = DirectionalModel()
        vol_model = VolatilityModel()
        try:
            vol_model.load()
        except Exception:
            logger.exception("Scheduler: volatility model unavailable, falling back to default vol")
            vol_model = None
        ensemble = Ensemble()
        sizer = PositionSizer()
        today = date.today()
        count = 0
        filtered_count = 0
        below_floor_count = 0
        candidates: list[Candidate] = []

        try:
            from src.db.watchlist import get_watchlist_tickers
            for ticker in get_watchlist_tickers(db):
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
                from src.features.analyst import get_analyst_features
                from src.features.earnings import get_earnings_features
                from src.features.macro import get_macro_features
                from src.features.options import get_options_features
                from src.features.sector import get_sector_features
                from src.features.sentiment import get_sentiment_features
                from src.features.short_interest import get_short_interest_features
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
                earnings_feats = get_earnings_features(db, ticker, price.date)
                features.update(earnings_feats)
                features.update(get_analyst_features(db, ticker, price.date))
                features.update(get_short_interest_features(db, ticker, price.date))

                # Optional: skip recommendations within 3 days of earnings (P9-005)
                if settings.skip_near_earnings and earnings_feats["earnings_within_3d"] == 1.0:
                    logger.debug("Scheduler: %s skipped — earnings within 3 days", ticker)
                    continue

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

                predicted_vol = 0.25
                if vol_model is not None:
                    try:
                        pv = vol_model.predict_latest(ticker, db=db)
                        if pv is not None:
                            predicted_vol = pv
                    except Exception:
                        logger.exception(f"Scheduler: vol prediction failed for {ticker}")

                inputs = SignalInputs(
                    ticker=ticker,
                    directional_prob=dir_prob,
                    directional_confidence=dir_conf,
                    predicted_vol=predicted_vol,
                    sentiment_score=sent_score,
                    sentiment_confidence=sent_conf,
                    current_price=price.close,
                )

                score = ensemble.score(inputs)
                candidates.append(Candidate(
                    ticker=ticker,
                    score=score,
                    directional_prob=dir_prob,
                    extras={"price_close": price.close},
                ))

            # Rank: dir_prob beats base_rate * lift, sort by composite score, cap at top-K.
            # The composite score is now a ranker, not a gate — see rec_ranker.py for why.
            selected = select_candidates(
                candidates,
                base_rate=settings.directional_base_rate,
                min_dir_prob_lift=settings.min_dir_prob_lift,
                top_k=settings.recommendations_top_k,
            )
            below_floor_count = len(candidates) - len(
                [c for c in candidates
                 if c.directional_prob >= settings.directional_base_rate * settings.min_dir_prob_lift]
            )

            for cand in selected:
                ticker = cand.ticker
                score = cand.score
                close_price = cand.extras["price_close"]

                if not score.meets_confidence:
                    filtered_count += 1
                    logger.debug(f"Scheduler: {ticker} filtered — below confidence threshold "
                                 f"(dir={score.directional_signal}, vol={score.volatility_signal}, sent={score.sentiment_signal})")
                    continue

                # Prefer defined-risk strategies: try spread first
                spread_rec = sizer.size_spread(score, close_price)
                if spread_rec:
                    rec = Recommendation(
                        ticker=ticker, date=today, strategy="spread",
                        score=score.score,
                        directional_signal=score.directional_signal,
                        volatility_signal=score.volatility_signal,
                        sentiment_signal=score.sentiment_signal,
                        entry_price=spread_rec.current_price,
                        stop_loss=None,
                        target_price=None,
                        position_size=abs(spread_rec.net_credit),
                        max_loss=spread_rec.max_loss,
                        contracts=spread_rec.contracts,
                        risk_type="defined",
                        notes=spread_rec.strategy_name,
                    )
                    db.add(rec)
                    count += 1
                    continue

                options_rec = sizer.size_options(score, close_price)
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
                        risk_type="defined",
                    )
                    db.add(rec)
                    count += 1
                    continue

                short_rec = sizer.size_short(score, close_price)
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
                        risk_type="undefined",
                        notes="Naked short — no defined-risk alternative available",
                    )
                    db.add(rec)
                    count += 1

            db.commit()
        finally:
            db.close()

        pipeline_recommendations_generated_total.inc(count)
        logger.info(
            "Scheduler: recommendations complete — %d new, %d candidates evaluated, "
            "%d below dir_prob floor (base_rate*%.2f), %d filtered for confidence",
            count, len(candidates), below_floor_count, settings.min_dir_prob_lift, filtered_count,
        )
        _record_run("recommendations", f"ok ({count} recs, {filtered_count} filtered)")

    except Exception:
        logger.exception("Scheduler: recommendation generation failed")
        _record_run("recommendations", "error")


def job_execute_recommendations():
    """8:00 AM ET — Auto-execute eligible recommendations (if enabled)."""
    logger.info("Scheduler: starting recommendation execution")
    try:
        from src.db.session import SessionLocal
        from src.services.execution_engine import ExecutionEngine

        db = SessionLocal()
        try:
            engine = ExecutionEngine(db)
            results = engine.execute_recommendations()
            submitted = sum(1 for r in results if r["status"] == "submitted")
            logger.info(f"Scheduler: execution complete — {submitted}/{len(results)} submitted")
            _record_run("execute_recommendations", f"ok ({submitted}/{len(results)} submitted)")
        finally:
            db.close()
    except ValueError:
        logger.debug("Scheduler: execution skipped — Alpaca credentials not configured")
        _record_run("execute_recommendations", "skipped (no credentials)")
    except Exception:
        logger.exception("Scheduler: recommendation execution failed")
        _record_run("execute_recommendations", "error")


def job_sync_portfolio():
    """Every 5 minutes during market hours — Sync positions and orders from Alpaca."""
    logger.info("Scheduler: starting portfolio sync")
    try:
        from src.db.session import SessionLocal
        from src.services.portfolio_sync import PortfolioSync

        db = SessionLocal()
        try:
            sync = PortfolioSync(db)
            pos = sync.sync_positions()
            orders = sync.sync_orders()
            logger.info(f"Scheduler: portfolio sync complete — {pos} positions, {orders} new orders")
            _record_run("portfolio_sync", f"ok ({pos} pos, {orders} orders)")
        finally:
            db.close()
    except ValueError:
        logger.debug("Scheduler: portfolio sync skipped — Alpaca credentials not configured")
        _record_run("portfolio_sync", "skipped (no credentials)")
    except Exception:
        logger.exception("Scheduler: portfolio sync failed")
        _record_run("portfolio_sync", "error")


def job_retrain_models():
    """First Sunday of each month — Retrain ML models with latest data."""
    logger.info("Scheduler: starting model retraining")
    try:
        from src.models.retrainer import Retrainer
        retrainer = Retrainer()
        results = retrainer.retrain_all()
        summary_parts = []
        for model_name, result in results.items():
            summary_parts.append(f"{model_name}={result['status']}")
        summary = ", ".join(summary_parts)
        logger.info(f"Scheduler: model retraining complete — {summary}")
        _record_run("retrain_models", f"ok ({summary})")
    except Exception:
        logger.exception("Scheduler: model retraining failed")
        _record_run("retrain_models", "error")


def init_scheduler():
    """Initialize and start the scheduler with market-hours cron jobs (Eastern Time)."""
    # Weekdays only (mon-fri)
    scheduler.add_job(job_fetch_prices, CronTrigger(hour=6, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="fetch_prices", replace_existing=True)
    scheduler.add_job(job_compute_indicators, CronTrigger(hour=6, minute=30, timezone="US/Eastern", day_of_week="mon-fri"), id="compute_indicators", replace_existing=True)
    scheduler.add_job(job_fetch_options, CronTrigger(hour=6, minute=45, timezone="US/Eastern", day_of_week="mon-fri"), id="fetch_options", replace_existing=True)
    scheduler.add_job(job_sentiment, CronTrigger(hour=7, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="sentiment", replace_existing=True)
    scheduler.add_job(job_generate_recommendations, CronTrigger(hour=7, minute=30, timezone="US/Eastern", day_of_week="mon-fri"), id="recommendations", replace_existing=True)

    # Auto-execute: 8:00 AM ET (after 7:30 AM recommendation generation)
    scheduler.add_job(job_execute_recommendations, CronTrigger(hour=8, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="execute_recommendations", replace_existing=True)

    # Portfolio sync: every 5 minutes, weekdays 9:30 AM - 4:00 PM ET
    scheduler.add_job(job_sync_portfolio, CronTrigger(minute="*/5", hour="9-15", timezone="US/Eastern", day_of_week="mon-fri"), id="portfolio_sync", replace_existing=True)
    # Also catch the 16:00 close
    scheduler.add_job(job_sync_portfolio, CronTrigger(minute="0,5", hour=16, timezone="US/Eastern", day_of_week="mon-fri"), id="portfolio_sync_close", replace_existing=True)

    # Monthly model retraining: first Sunday of each month at 2:00 AM ET
    scheduler.add_job(job_retrain_models, CronTrigger(hour=2, minute=0, timezone="US/Eastern", day_of_week="sun", day="1-7"), id="retrain_models", replace_existing=True)

    # Weekly earnings calendar refresh — Sunday 6:00 AM ET (P9-005)
    scheduler.add_job(job_fetch_earnings, CronTrigger(hour=6, minute=0, timezone="US/Eastern", day_of_week="sun"), id="fetch_earnings", replace_existing=True)

    scheduler.start()
    logger.info("Scheduler started with 4 daily jobs + portfolio sync (5min) + monthly retrain")


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down")
