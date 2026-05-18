"""APScheduler cron jobs for daily pipeline automation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.db.models import Base
from src.db.session import engine
from src.metrics import (
    pipeline_indicators_computed_total,
    pipeline_job_errors_total,
    pipeline_last_run_timestamp,
    pipeline_prices_fetched_total,
    pipeline_recommendations_generated_total,
    pipeline_sentiment_runs_total,
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _rec_capital_cost(rec) -> float:
    """Capital deployed by a Recommendation, for the direction-blind daily cap.

    Most strategies write `position_size` = cash deployed (long stock value,
    premium paid, short margin). Credit spreads break that convention: their
    `position_size` is the credit RECEIVED, while the actual capital tied up
    is the broker collateral = `max_loss`. Taking `max(position_size, max_loss)`
    consistently captures capital-at-risk across both flavors without a
    per-strategy lookup table.
    """
    return max(rec.position_size or 0.0, rec.max_loss or 0.0)


def _record_run(job_name: str, status: str):
    """Record job completion time in health endpoint state and Prometheus gauge.

    Also increments `pipeline_job_errors_total` when the job ended in error.
    APScheduler swallows handler exceptions (every job here is wrapped in a
    try/except that logs and continues) so a silent failure looks identical
    to a success in the job-store view. The error counter is the only signal
    Prometheus/Alertmanager can pick up — wire alerts on its rate.
    """
    from src.api.routes.health import last_job_runs
    now = datetime.now()
    last_job_runs[job_name] = f"{status} at {now.isoformat()}"
    outcome = "ok" if status.startswith("ok") else ("skipped" if status.startswith("skipped") else "error")
    pipeline_last_run_timestamp.labels(job=job_name, status=outcome).set(now.timestamp())
    if outcome == "error":
        pipeline_job_errors_total.labels(job=job_name).inc()


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


def job_sentiment():
    """7:00 AM ET — Fetch headlines, run sentiment analysis, persist daily aggregate.

    Sync wrapper around the async implementation. AsyncIOScheduler can run
    coroutine jobs directly, but doing so silently no-ops if the loop it
    captured at startup isn't the loop that handles the tick (we hit this on
    2026-05-08 — the job left no entry in last_job_runs). asyncio.run gives
    us a fresh loop in the executor thread and a hard error if the body
    fails to start.
    """
    asyncio.run(_job_sentiment_async())


async def _job_sentiment_async():
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
        # Each ticker upsert is isolated: a single SQLAlchemy failure (constraint
        # violation, transient DB hiccup) must not silently drop the remaining
        # tickers in the batch. Roll back the session after a failure so the next
        # iteration starts clean.
        today = _date.today()
        db = SessionLocal()
        upserted = 0
        upsert_failed = 0
        try:
            for ticker, info in results.items():
                if info.get("scores_computed", 0) == 0:
                    continue
                try:
                    upsert_daily_sentiment(
                        db, ticker, today,
                        sentiment_score=info.get("composite_sentiment"),
                        confidence=None,
                        article_count=int(info.get("scores_computed", 0)),
                    )
                    upserted += 1
                except Exception:
                    upsert_failed += 1
                    db.rollback()
                    pipeline_job_errors_total.labels(job="sentiment_upsert").inc()
                    logger.exception(
                        "Scheduler: sentiment upsert failed for %s — continuing", ticker
                    )
        finally:
            db.close()
        if upsert_failed:
            logger.warning(
                "Scheduler: sentiment upsert summary — %d ok, %d failed",
                upserted, upsert_failed,
            )

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


def job_fetch_wikipedia_pageviews():
    """5:30 AM ET (daily, Mon-Sun) — Fetch yesterday's Wikipedia page views.

    Wikimedia data lags ~24h. Runs every day (not just weekdays) so non-trading
    days still produce attention rows we can forward-fill into Monday's prediction.
    """
    from datetime import date as _date, timedelta as _td

    logger.info("Scheduler: starting Wikipedia pageview fetch")
    try:
        from src.pipeline.wikipedia_fetcher import WikipediaPageviewFetcher
        Base.metadata.create_all(engine)
        target_day = _date.today() - _td(days=1)
        with WikipediaPageviewFetcher() as fetcher:
            results = fetcher.fetch_all(start_date=target_day, end_date=target_day)
        ok = sum(1 for v in results.values() if v == "ok")
        logger.info(
            "Scheduler: Wikipedia pageviews complete — %d/%d tickers ok",
            ok, len(results),
        )
        _record_run("fetch_wikipedia_pageviews", f"ok ({ok}/{len(results)} tickers)")
    except Exception:
        logger.exception("Scheduler: Wikipedia pageview fetch failed")
        _record_run("fetch_wikipedia_pageviews", "error")


def job_fetch_insider_transactions():
    """6:50 AM ET — Fetch yesterday's SEC Form 4 insider filings for the watchlist."""
    logger.info("Scheduler: starting insider transaction fetch")
    try:
        from src.pipeline.insider_fetcher import InsiderTransactionFetcher
        Base.metadata.create_all(engine)
        with InsiderTransactionFetcher() as fetcher:
            results = fetcher.fetch_all()
        ok = sum(1 for v in results.values() if v == "ok")
        logger.info(
            "Scheduler: insider fetch complete — %d/%d tickers ok",
            ok, len(results),
        )
        _record_run("fetch_insider_transactions", f"ok ({ok}/{len(results)} tickers)")
    except Exception:
        logger.exception("Scheduler: insider transaction fetch failed")
        _record_run("fetch_insider_transactions", "error")


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
        drop_model = DirectionalModel(direction="drop")
        rise_model: DirectionalModel | None = DirectionalModel(direction="rise")
        try:
            rise_model.load()
        except Exception:
            # Rise pickle may be absent on first deploy or in dev — log once and
            # fall back to bearish-only. Bullish candidates simply won't be generated.
            logger.warning(
                "Scheduler: rise model unavailable, falling back to bearish-only recommendations"
            )
            rise_model = None
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
        no_indicator = 0
        no_price = 0
        spread_recs = 0
        options_recs = 0
        short_recs = 0
        bull_spread_recs = 0
        call_options_recs = 0
        long_recs = 0
        no_sizer_match = 0
        bear_recs = 0
        bull_recs = 0
        capital_used = 0.0
        capital_capped = 0
        capital_cap = settings.daily_capital_cap
        candidates: list[Candidate] = []

        try:
            from src.db.watchlist import get_watchlist_tickers
            watchlist = get_watchlist_tickers(db)
            for ticker in watchlist:
                # Get latest indicator row
                ind = (
                    db.query(TechnicalIndicator)
                    .filter_by(ticker=ticker)
                    .order_by(TechnicalIndicator.date.desc())
                    .first()
                )
                if not ind:
                    no_indicator += 1
                    continue

                # Get latest price
                price = (
                    db.query(PriceHistory)
                    .filter_by(ticker=ticker)
                    .order_by(PriceHistory.date.desc())
                    .first()
                )
                if not price or not price.close:
                    no_price += 1
                    continue

                # Lagged returns mirror training (directional.py: close.pct_change(N)).
                # Inference previously hardcoded these to 0, silently masking a strong
                # signal class on every prediction.
                recent_closes = [
                    r.close
                    for r in db.query(PriceHistory.close)
                    .filter(PriceHistory.ticker == ticker, PriceHistory.date <= price.date)
                    .order_by(PriceHistory.date.desc())
                    .limit(21)
                    .all()
                ]

                def _return_lag(n: int) -> float:
                    if len(recent_closes) <= n or not recent_closes[n]:
                        return 0.0
                    return (recent_closes[0] - recent_closes[n]) / recent_closes[n]

                # Build features for directional model
                from src.features.analyst import get_analyst_features
                from src.features.earnings import get_earnings_features
                from src.features.insider import get_insider_features
                from src.features.macro import get_macro_features
                from src.features.options import get_options_features
                from src.features.sector import get_sector_features
                from src.features.sentiment import get_sentiment_features
                from src.features.short_interest import get_short_interest_features
                from src.features.wikipedia import get_wikipedia_features
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
                    "return_5d_lag": _return_lag(5),
                    "return_10d_lag": _return_lag(10),
                    "return_20d_lag": _return_lag(20),
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
                features.update(get_wikipedia_features(db, ticker, price.date))
                features.update(get_insider_features(db, ticker, price.date))

                # Optional: skip recommendations within 3 days of earnings (P9-005)
                if settings.skip_near_earnings and earnings_feats["earnings_within_3d"] == 1.0:
                    logger.debug("Scheduler: %s skipped — earnings within 3 days", ticker)
                    continue

                try:
                    drop_prob, _ = drop_model.predict(features)
                except Exception:
                    drop_prob = 0.5

                if rise_model is not None:
                    try:
                        rise_prob, _ = rise_model.predict(features)
                    except Exception:
                        rise_prob = 0.0
                else:
                    rise_prob = 0.0

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
                    drop_prob=drop_prob,
                    rise_prob=rise_prob,
                    predicted_vol=predicted_vol,
                    sentiment_score=sent_score,
                    sentiment_confidence=sent_conf,
                    current_price=price.close,
                )

                # Two scores per ticker: one bearish, one bullish.
                for s in ensemble.score(inputs):
                    # Skip the bullish branch entirely if the rise model is unavailable —
                    # rise_prob=0 collapses the directional component to 0 but vol+sent
                    # could still surface noise. Better to suppress than to emit.
                    if s.direction == "rise" and rise_model is None:
                        continue
                    candidates.append(Candidate(
                        ticker=ticker,
                        score=s,
                        direction=s.direction,
                        extras={"price_close": price.close},
                    ))

            # Pure top-K by composite score. The legacy dir_prob floor was a
            # knife-edge on v3's isotonic calibration plateaus; under sigmoid
            # calibration outputs cluster tightly around base rate, making any
            # absolute lift floor noise. See rec_ranker.py for full history.
            selected = select_candidates(
                candidates,
                top_k=settings.recommendations_top_k,
                min_score=settings.recommendations_min_score or None,
            )

            for cand in selected:
                ticker = cand.ticker
                score = cand.score
                close_price = cand.extras["price_close"]
                direction = cand.direction

                if direction == "rise":
                    # Bullish routing: defined-risk first, then long-call options,
                    # then long stock. Long-stock is direction-equivalent to a
                    # short borrow on the bearish side (highest-risk fallback).
                    bull_spread_rec = sizer.size_bull_spread(score, close_price)
                    if bull_spread_rec:
                        rec = Recommendation(
                            ticker=ticker, date=today, direction="long",
                            strategy="bull_spread",
                            score=score.score,
                            directional_signal=score.directional_signal,
                            volatility_signal=score.volatility_signal,
                            sentiment_signal=score.sentiment_signal,
                            entry_price=bull_spread_rec.current_price,
                            stop_loss=None,
                            target_price=None,
                            position_size=abs(bull_spread_rec.net_credit),
                            max_loss=bull_spread_rec.max_loss,
                            contracts=bull_spread_rec.contracts,
                            risk_type="defined",
                            notes=bull_spread_rec.strategy_name,
                        )
                        cost = _rec_capital_cost(rec)
                        if capital_used + cost > capital_cap:
                            capital_capped += 1
                            continue
                        db.add(rec)
                        capital_used += cost
                        count += 1
                        bull_spread_recs += 1
                        bull_recs += 1
                        continue

                    call_rec = sizer.size_options(score, close_price, option_type="call")
                    if call_rec:
                        rec = Recommendation(
                            ticker=ticker, date=today, direction="long",
                            strategy="call_options",
                            score=score.score,
                            directional_signal=score.directional_signal,
                            volatility_signal=score.volatility_signal,
                            sentiment_signal=score.sentiment_signal,
                            entry_price=call_rec.entry_price,
                            stop_loss=call_rec.entry_price * 0.95,
                            target_price=call_rec.strike,
                            position_size=call_rec.position_size,
                            max_loss=call_rec.max_loss,
                            contracts=call_rec.contracts,
                            strike=call_rec.strike,
                            option_type=call_rec.option_type,
                            risk_type="defined",
                        )
                        cost = _rec_capital_cost(rec)
                        if capital_used + cost > capital_cap:
                            capital_capped += 1
                            continue
                        db.add(rec)
                        capital_used += cost
                        count += 1
                        call_options_recs += 1
                        bull_recs += 1
                        continue

                    long_rec = sizer.size_long(score, close_price)
                    if long_rec:
                        rec = Recommendation(
                            ticker=ticker, date=today, direction="long",
                            strategy="long",
                            score=score.score,
                            directional_signal=score.directional_signal,
                            volatility_signal=score.volatility_signal,
                            sentiment_signal=score.sentiment_signal,
                            entry_price=long_rec.entry_price,
                            stop_loss=long_rec.stop_loss,
                            target_price=long_rec.target_price,
                            position_size=long_rec.position_size,
                            max_loss=long_rec.max_loss,
                            risk_type=long_rec.risk_type,
                        )
                        cost = _rec_capital_cost(rec)
                        if capital_used + cost > capital_cap:
                            capital_capped += 1
                            continue
                        db.add(rec)
                        capital_used += cost
                        count += 1
                        long_recs += 1
                        bull_recs += 1
                        continue

                    no_sizer_match += 1
                    logger.debug(
                        "Scheduler: %s (bull) ranked but no sizer matched "
                        "(bull_spread/call/long all returned None)", ticker,
                    )
                    continue

                # Bearish routing (direction == "drop"): spread → put options → short.
                spread_rec = sizer.size_spread(score, close_price)
                if spread_rec:
                    rec = Recommendation(
                        ticker=ticker, date=today, direction="short",
                        strategy="spread",
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
                    cost = _rec_capital_cost(rec)
                    if capital_used + cost > capital_cap:
                        capital_capped += 1
                        continue
                    db.add(rec)
                    capital_used += cost
                    count += 1
                    spread_recs += 1
                    bear_recs += 1
                    continue

                options_rec = sizer.size_options(score, close_price)
                if options_rec:
                    rec = Recommendation(
                        ticker=ticker, date=today, direction="short",
                        strategy="options",
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
                    cost = _rec_capital_cost(rec)
                    if capital_used + cost > capital_cap:
                        capital_capped += 1
                        continue
                    db.add(rec)
                    capital_used += cost
                    count += 1
                    options_recs += 1
                    bear_recs += 1
                    continue

                short_rec = sizer.size_short(score, close_price)
                if short_rec:
                    rec = Recommendation(
                        ticker=ticker, date=today, direction="short",
                        strategy="short",
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
                    cost = _rec_capital_cost(rec)
                    if capital_used + cost > capital_cap:
                        capital_capped += 1
                        continue
                    db.add(rec)
                    capital_used += cost
                    count += 1
                    short_recs += 1
                    bear_recs += 1
                    continue

                no_sizer_match += 1
                logger.debug(
                    "Scheduler: %s (bear) ranked but no sizer matched "
                    "(spread/options/short all returned None)", ticker,
                )

            db.commit()
        finally:
            db.close()

        # Per-direction bumps (drop-side dead-zone visibility, 2026-05-14).
        # Both .inc() calls run unconditionally so each direction emits a
        # series even when bear_recs or bull_recs is zero — a regime shutout
        # (e.g. 0 bear / 10 bull on 2026-05-14) must be distinguishable from
        # "no scrape happened" in Grafana.
        pipeline_recommendations_generated_total.labels(direction="bear").inc(bear_recs)
        pipeline_recommendations_generated_total.labels(direction="bull").inc(bull_recs)
        logger.info(
            "Scheduler: recommendations complete — %d new (%d bear / %d bull), "
            "%d watchlist, %d candidates evaluated (dual-direction), "
            "%d skipped (no indicator), %d skipped (no price), "
            "top_k=%d selected, "
            "bear sizers: %d spread / %d options / %d short, "
            "bull sizers: %d bull_spread / %d call / %d long, "
            "%d no_sizer_match, "
            "capital: $%.0f used / $%.0f cap, %d capped",
            count, bear_recs, bull_recs, len(watchlist), len(candidates),
            no_indicator, no_price, len(selected),
            spread_recs, options_recs, short_recs,
            bull_spread_recs, call_options_recs, long_recs,
            no_sizer_match,
            capital_used, capital_cap, capital_capped,
        )
        _record_run(
            "recommendations",
            f"ok ({count} recs [{bear_recs}b/{bull_recs}B], "
            f"{len(candidates)}/{len(watchlist)*2} candidates, "
            f"{no_indicator} no_ind, {no_price} no_price, "
            f"bear: {spread_recs}sp/{options_recs}op/{short_recs}sh, "
            f"bull: {bull_spread_recs}sp/{call_options_recs}op/{long_recs}lg, "
            f"{no_sizer_match} none, "
            f"cap: ${capital_used:.0f}/${capital_cap:.0f}, {capital_capped} capped)",
        )

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
    scheduler.add_job(job_fetch_insider_transactions, CronTrigger(hour=6, minute=50, timezone="US/Eastern", day_of_week="mon-fri"), id="fetch_insider_transactions", replace_existing=True)
    scheduler.add_job(job_fetch_wikipedia_pageviews, CronTrigger(hour=5, minute=30, timezone="US/Eastern"), id="fetch_wikipedia_pageviews", replace_existing=True)
    # Sentiment job takes ~3.3h on the homelab (159 tickers × ~20 headlines × 3.74s
    # per scored headline, post-parallelization on 2026-05-18 with OLLAMA_NUM_PARALLEL=2).
    # Started at 07:00 ET it could not finish before the 07:30 ET recommendations cut.
    # 04:00 ET start finishes ~07:20 — ~10 min cushion before recs, and catches the
    # 04:00-07:30 pre-market wire window (BMO earnings, EU open, overnight catalysts)
    # for the tickers scored later in the run.
    scheduler.add_job(job_sentiment, CronTrigger(hour=4, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="sentiment", replace_existing=True)
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
