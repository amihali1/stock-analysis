"""APScheduler cron jobs for daily pipeline automation."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime

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


def _open_position_capital(db) -> float:
    """Sum of capital currently locked in open Alpaca positions.

    Subtracted from daily_capital_cap at the start of each rec-generation
    run so the cap is account-wide, not per-run. Without this deduction the
    scheduler would happily emit a fresh $1k of recs each day on top of
    carry-over positions, and Alpaca would reject the lot on insufficient
    buying power.

    Shorts are weighted by the 1.5x margin multiplier to match the rec
    sizer's position_size convention (size_short writes margin_required as
    position_size). Longs use market_value directly.

    Residue positions (broker legs no strategy owns — e.g. spread/option legs
    whose PaperTrade closed but never unwound, or exercise/assignment stock)
    are EXCLUDED: they are unmanaged and will expire, and counting them let a
    pile of orphaned legs push open capital over the cap and shut out all recs
    (2026-07-30: 16 residue legs → open $60.5k > $50k cap → 0 recs). The real
    fix is unwinding those legs on close; this stops them starving the cap
    meanwhile.
    """
    from src.db.models import AlpacaPosition
    from src.services.portfolio_sync import owned_underlyings, _to_underlying
    owned = owned_underlyings(db)
    total = 0.0
    residue_skipped = 0.0
    for p in db.query(AlpacaPosition).all():
        mv = abs(p.market_value or 0.0)
        if (p.side or "").lower() == "short":
            mv *= 1.5
        u = _to_underlying(p.ticker)
        if u is not None and u not in owned:
            residue_skipped += mv  # unowned residue — don't let it starve the cap
            continue
        total += mv
    if residue_skipped:
        logger.info(
            "open_position_capital: excluded $%.0f residue (unowned legs)",
            residue_skipped,
        )
    return total


def _latest_close(db, ticker: str) -> float | None:
    """Most recent non-null close for a ticker, or None."""
    from src.db.models import PriceHistory  # deferred, matches module convention

    row = (
        db.query(PriceHistory)
        .filter(PriceHistory.ticker == ticker, PriceHistory.close.isnot(None))
        .order_by(PriceHistory.date.desc())
        .first()
    )
    return float(row.close) if row else None


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


def _spy_regime(db) -> str:
    """SPY 50d-SMA regime: 'up' (SPY above its 50d SMA), 'down', or 'unknown'.

    Drives the regime funding tilt (see config.enable_regime_tilt). The
    2026-07-27 regime split found both monetized strategies positive in both
    regimes but ~1.8-3.3x stronger in down-tape, so this is used to reorder
    funding priority, never to gate a direction off.
    """
    from src.db.models import PriceHistory, TechnicalIndicator
    price = (
        db.query(PriceHistory)
        .filter(PriceHistory.ticker == "SPY", PriceHistory.close.isnot(None))
        .order_by(PriceHistory.date.desc())
        .first()
    )
    ind = (
        db.query(TechnicalIndicator)
        .filter(TechnicalIndicator.ticker == "SPY", TechnicalIndicator.sma_50.isnot(None))
        .order_by(TechnicalIndicator.date.desc())
        .first()
    )
    if price is None or ind is None or not ind.sma_50:
        return "unknown"
    return "up" if price.close > ind.sma_50 else "down"


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


def job_fetch_prices(label: str = "fetch_prices"):
    """Fetch new price data for all tickers.

    Runs twice on weekdays: 6:00 AM ET (label ``fetch_prices``) picks up the
    prior session's finalized close pre-market, and 4:30 PM ET (label
    ``fetch_prices_close``) picks up the session that just closed so the
    evening paper-exit run can count it. Distinct labels keep the two runs'
    telemetry from clobbering each other in /api/health.
    """
    logger.info("Scheduler: starting price fetch (%s)", label)
    try:
        from src.pipeline.data_fetcher import DataFetcher
        Base.metadata.create_all(engine)
        fetcher = DataFetcher()
        results = fetcher.fetch_daily(period="5d")
        fetcher.close()
        total = sum(v for v in results.values() if v > 0)
        pipeline_prices_fetched_total.inc(total)
        logger.info(f"Scheduler: price fetch complete — {total} new rows across {len(results)} tickers")
        _record_run(label, f"ok ({total} rows)")
    except Exception:
        logger.exception("Scheduler: price fetch failed")
        _record_run(label, "error")


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
        from src.services.options_chain import OptionsChainFetcher
        from datetime import date, timedelta
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
        chain_fetcher = OptionsChainFetcher()
        chain_hits = 0
        chain_misses = 0
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
        bear_capped = 0
        bull_capped = 0
        too_expensive = 0
        ticker_errors = 0
        pair_recs = 0
        bear_no_pair = 0
        # Per-stage sizer-rejection telemetry. Diagnoses bear-shutout root cause
        # (2026-05-19+: 0 bear / N bull across multiple runs). Each counter is
        # bumped when the corresponding sizer returns None for a selected
        # candidate of that direction, BEFORE falling through to the next
        # sizer in the cascade. A bear with all three None still contributes
        # to bear_no_short AND no_sizer_match.
        bear_no_spread = 0
        bear_no_options = 0
        bear_no_short = 0
        bull_no_bull_spread = 0
        bull_no_call = 0
        bull_no_long = 0
        capital_cap = settings.daily_capital_cap
        open_capital = _open_position_capital(db)
        available_cap = max(0.0, capital_cap - open_capital)
        candidates: list[Candidate] = []

        try:
            from src.db.watchlist import get_watchlist_tickers
            watchlist = get_watchlist_tickers(db)
            for ticker in watchlist:
                # One bad ticker must not kill the whole run (2026-07-02:
                # NULL closes crashed annualized_vol_20d and zeroed recs for
                # three weeks). Log, count, move on.
                try:
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

                    # Drop unaffordable tickers before ML (saves top-K slots).
                    # See config.max_ticker_price for trade-off rationale.
                    if settings.max_ticker_price > 0 and price.close > settings.max_ticker_price:
                        too_expensive += 1
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

                    from src.models.directional import annualized_vol_20d
                    vol_20d = annualized_vol_20d(recent_closes)

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
                        "volatility_20d": vol_20d,
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
                except Exception:
                    ticker_errors += 1
                    logger.exception(f"Scheduler: candidate build failed for {ticker}")
                    continue

            # Pure top-K by composite score. The legacy dir_prob floor was a
            # knife-edge on v3's isotonic calibration plateaus; under sigmoid
            # calibration outputs cluster tightly around base rate, making any
            # absolute lift floor noise. See rec_ranker.py for full history.
            cand_bear = sum(1 for c in candidates if c.direction == "drop")
            cand_bull = len(candidates) - cand_bear
            selected = select_candidates(
                candidates,
                top_k=settings.recommendations_top_k,
                min_score=settings.recommendations_min_score or None,
            )
            selected_bear = sum(1 for c in selected if c.direction == "drop")
            selected_bull = len(selected) - selected_bear

            # Regime funding tilt (2026-07-27 sweep): in down-tape rise's per-$
            # edge jumps, so fund rise candidates first when the daily cap binds.
            # Stable sort preserves intra-direction score order. Never gates a
            # direction off — both are positive in both regimes.
            spy_regime = _spy_regime(db)
            regime_tilt_applied = False
            if settings.enable_regime_tilt and spy_regime == "down":
                selected.sort(key=lambda c: 0 if c.direction == "rise" else 1)
                regime_tilt_applied = True
                logger.info(
                    "Scheduler: regime tilt applied (SPY down-tape) — rise funded first"
                )

            # Per-direction capital reservation: funding is score-ordered and
            # bull scores systematically exceed bear scores (different model
            # scales), so a tight cap funds all bulls first and starves the
            # cheaper bears. When reserve>0 and both directions have candidates,
            # cap each direction at (1-reserve) of available_cap so the other
            # side keeps a guaranteed floor. Keyed by candidate direction
            # (rise/drop). dir_ceiling == available_cap means inactive.
            dir_used = {"rise": 0.0, "drop": 0.0}
            dir_ceiling = available_cap
            if (settings.per_direction_capital_reserve > 0
                    and selected_bear > 0 and selected_bull > 0):
                dir_ceiling = available_cap * (1.0 - settings.per_direction_capital_reserve)

            def _fetch_chain(ticker_: str, target_days: int = 30) -> tuple[list[dict] | None, date | None]:
                """Fetch options chain for a ticker. Returns (chain, expiration_date).

                The expiration date is critical: when chain_data is used the leg
                strikes come from the actual chain expiry, so rec.expiry MUST be
                set to that same date or the downstream OCC symbol carries the
                wrong YYMMDD and Alpaca rejects with "asset not found".
                """
                nonlocal chain_hits, chain_misses
                try:
                    exp = chain_fetcher.find_expiration_near_days(ticker_, target_days)
                    if not exp:
                        chain_misses += 1
                        return None, None
                    chain = chain_fetcher.fetch_chain(ticker_, exp)
                    if chain:
                        chain_hits += 1
                        return chain, date.fromisoformat(exp)
                    chain_misses += 1
                    return None, None
                except Exception:
                    logger.exception("Scheduler: chain fetch failed for %s", ticker_)
                    chain_misses += 1
                    return None, None

            for cand in selected:
                ticker = cand.ticker
                score = cand.score
                close_price = cand.extras["price_close"]
                direction = cand.direction

                # Cascade routing: try each tier; on cap-bust, fall through to the
                # next (cheaper) sizer instead of skipping the ticker. capped is
                # counted once per ticker only if no later tier landed.
                persisted = False
                cap_busted = False

                if direction == "rise":
                    # Bullish routing: defined-risk first, then long-call options,
                    # then long stock. Long-stock is direction-equivalent to a
                    # short borrow on the bearish side (highest-risk fallback).
                    chain_data, chain_expiry = _fetch_chain(ticker)
                    bull_spread_rec = sizer.size_bull_spread(score, close_price, chain_data=chain_data)
                    if bull_spread_rec is None:
                        bull_no_bull_spread += 1
                    else:
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
                            expiry=chain_expiry or (today + timedelta(days=bull_spread_rec.expiry_days)),
                            legs_json=json.dumps([leg.model_dump() for leg in bull_spread_rec.legs]),
                            risk_type="defined",
                            notes=bull_spread_rec.strategy_name,
                        )
                        cost = _rec_capital_cost(rec)
                        if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                            db.add(rec)
                            capital_used += cost
                            dir_used[direction] += cost
                            count += 1
                            bull_spread_recs += 1
                            bull_recs += 1
                            persisted = True
                        else:
                            cap_busted = True

                    if not persisted:
                        call_rec = sizer.size_options(
                            score, close_price, option_type="call",
                            chain_data=chain_data,
                        )
                        if call_rec is None:
                            bull_no_call += 1
                        else:
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
                                expiry=chain_expiry or (today + timedelta(days=call_rec.expiry_days)),
                                risk_type="defined",
                            )
                            cost = _rec_capital_cost(rec)
                            if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                                db.add(rec)
                                capital_used += cost
                                dir_used[direction] += cost
                                count += 1
                                call_options_recs += 1
                                bull_recs += 1
                                persisted = True
                            else:
                                cap_busted = True

                    if not persisted:
                        long_rec = sizer.size_long(score, close_price)
                        if long_rec is None:
                            bull_no_long += 1
                        else:
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
                            if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                                db.add(rec)
                                capital_used += cost
                                dir_used[direction] += cost
                                count += 1
                                long_recs += 1
                                bull_recs += 1
                                persisted = True
                            else:
                                cap_busted = True

                    if not persisted:
                        if cap_busted:
                            bull_capped += 1
                        else:
                            no_sizer_match += 1
                            logger.debug(
                                "Scheduler: %s (bull) ranked but no sizer matched "
                                "(bull_spread/call/long all returned None)", ticker,
                            )
                    continue

                # Bearish routing (direction == "drop").
                #
                # 2026-07-07 bear_monetization sweep: bear picks carry relative
                # alpha but every absolute-decline structure loses in a rising
                # tape — naked short -0.14%/10d, credit spreads -4..-7.5% on
                # collateral DESPITE 69-89% win rates (tail blow-throughs eat
                # the credits). Short pick + equal-$ long SPY = +0.47%/10d
                # market-neutral. Pair is the only bear route when enabled;
                # legacy spread/options/short cascade below is the fallback
                # for enable_pair_short=False.
                if settings.enable_pair_short:
                    hedge_price = _latest_close(db, settings.pair_hedge_symbol)
                    pair_rec = None
                    if hedge_price is not None:
                        pair_rec = sizer.size_pair_short(
                            score, close_price, hedge_price,
                            hedge_symbol=settings.pair_hedge_symbol,
                        )
                    if pair_rec is None:
                        bear_no_pair += 1
                    else:
                        rec = Recommendation(
                            ticker=ticker, date=today, direction="short",
                            strategy="pair_short",
                            score=score.score,
                            directional_signal=score.directional_signal,
                            volatility_signal=score.volatility_signal,
                            sentiment_signal=score.sentiment_signal,
                            entry_price=pair_rec.entry_price,
                            stop_loss=pair_rec.stop_loss,
                            target_price=pair_rec.target_price,
                            position_size=pair_rec.position_size,
                            max_loss=pair_rec.max_loss,
                            legs_json=json.dumps([
                                {"leg": "short", "ticker": ticker,
                                 "qty": pair_rec.shares, "entry": pair_rec.entry_price},
                                {"leg": "hedge", "ticker": pair_rec.hedge_symbol,
                                 "qty": pair_rec.hedge_shares, "entry": pair_rec.hedge_entry},
                            ]),
                            risk_type="defined",
                            notes="Market-neutral pair: short pick + equal-$ long hedge",
                        )
                        cost = _rec_capital_cost(rec)
                        if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                            db.add(rec)
                            capital_used += cost
                            dir_used[direction] += cost
                            count += 1
                            pair_recs += 1
                            bear_recs += 1
                            persisted = True
                        else:
                            cap_busted = True

                    if not persisted:
                        if cap_busted:
                            bear_capped += 1
                        else:
                            no_sizer_match += 1
                            logger.debug(
                                "Scheduler: %s (bear) ranked but pair sizer "
                                "returned None", ticker,
                            )
                    continue

                # Legacy bear cascade: spread → put options → short.
                chain_data, chain_expiry = _fetch_chain(ticker)
                spread_rec = sizer.size_spread(score, close_price, chain_data=chain_data)
                if spread_rec is None:
                    bear_no_spread += 1
                else:
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
                        expiry=chain_expiry or (today + timedelta(days=spread_rec.expiry_days)),
                        legs_json=json.dumps([leg.model_dump() for leg in spread_rec.legs]),
                        risk_type="defined",
                        notes=spread_rec.strategy_name,
                    )
                    cost = _rec_capital_cost(rec)
                    if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                        db.add(rec)
                        capital_used += cost
                        dir_used[direction] += cost
                        count += 1
                        spread_recs += 1
                        bear_recs += 1
                        persisted = True
                    else:
                        cap_busted = True

                if not persisted:
                    options_rec = sizer.size_options(
                        score, close_price, chain_data=chain_data,
                    )
                    if options_rec is None:
                        bear_no_options += 1
                    else:
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
                            expiry=chain_expiry or (today + timedelta(days=options_rec.expiry_days)),
                            risk_type="defined",
                        )
                        cost = _rec_capital_cost(rec)
                        if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                            db.add(rec)
                            capital_used += cost
                            dir_used[direction] += cost
                            count += 1
                            options_recs += 1
                            bear_recs += 1
                            persisted = True
                        else:
                            cap_busted = True

                if not persisted:
                    short_rec = sizer.size_short(score, close_price)
                    if short_rec is None:
                        bear_no_short += 1
                    else:
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
                        if capital_used + cost <= available_cap and dir_used[direction] + cost <= dir_ceiling:
                            db.add(rec)
                            capital_used += cost
                            dir_used[direction] += cost
                            count += 1
                            short_recs += 1
                            bear_recs += 1
                            persisted = True
                        else:
                            cap_busted = True

                if not persisted:
                    if cap_busted:
                        bear_capped += 1
                    else:
                        no_sizer_match += 1
                        logger.debug(
                            "Scheduler: %s (bear) ranked but no sizer matched "
                            "(spread/options/short all returned None)", ticker,
                        )

            db.commit()
        finally:
            db.close()
            try:
                chain_fetcher.close()
            except Exception:
                logger.exception("Scheduler: chain_fetcher.close failed")

        # Per-direction bumps (drop-side dead-zone visibility, 2026-05-14).
        # Both .inc() calls run unconditionally so each direction emits a
        # series even when bear_recs or bull_recs is zero — a regime shutout
        # (e.g. 0 bear / 10 bull on 2026-05-14) must be distinguishable from
        # "no scrape happened" in Grafana.
        pipeline_recommendations_generated_total.labels(direction="bear").inc(bear_recs)
        pipeline_recommendations_generated_total.labels(direction="bull").inc(bull_recs)
        logger.info(
            "Scheduler: recommendations complete — %d new (%d bear / %d bull), "
            "%d watchlist, %d candidates (%d bear / %d bull), "
            "%d skipped (no indicator), %d skipped (no price), "
            "%d skipped (too expensive), "
            "top_k=%d selected (%d bear / %d bull), "
            "bear sizers: %d pair / %d spread / %d options / %d short "
            "[None: %d pr / %d sp / %d op / %d sh], "
            "bull sizers: %d bull_spread / %d call / %d long "
            "[None: %d bsp / %d c / %d lg], "
            "%d no_sizer_match, "
            "capital: $%.0f used / $%.0f avail (open $%.0f, cap $%.0f), "
            "%d capped (%db/%dB)",
            count, bear_recs, bull_recs, len(watchlist),
            len(candidates), cand_bear, cand_bull,
            no_indicator, no_price, too_expensive,
            len(selected), selected_bear, selected_bull,
            pair_recs, spread_recs, options_recs, short_recs,
            bear_no_pair, bear_no_spread, bear_no_options, bear_no_short,
            bull_spread_recs, call_options_recs, long_recs,
            bull_no_bull_spread, bull_no_call, bull_no_long,
            no_sizer_match,
            capital_used, available_cap, open_capital, capital_cap,
            bear_capped + bull_capped, bear_capped, bull_capped,
        )
        _record_run(
            "recommendations",
            f"ok ({count} recs [{bear_recs}b/{bull_recs}B], "
            f"cands {cand_bear}b/{cand_bull}B, sel {selected_bear}b/{selected_bull}B, "
            f"{no_indicator} no_ind, {no_price} no_price, "
            f"{too_expensive} too_exp, {ticker_errors} tkr_err, "
            f"bear: {pair_recs}pr/{spread_recs}sp/{options_recs}op/{short_recs}sh "
            f"[None pr:{bear_no_pair} {bear_no_spread}/{bear_no_options}/{bear_no_short}], "
            f"bull: {bull_spread_recs}sp/{call_options_recs}op/{long_recs}lg "
            f"[None {bull_no_bull_spread}/{bull_no_call}/{bull_no_long}], "
            f"{no_sizer_match} none, "
            f"cap: ${capital_used:.0f}/${available_cap:.0f} "
            f"(open ${open_capital:.0f}, cap ${capital_cap:.0f}), "
            f"{bear_capped + bull_capped} capped [{bear_capped}b/{bull_capped}B], "
            f"regime {spy_regime}{'*' if regime_tilt_applied else ''}, "
            f"reserve {'on' if dir_ceiling < available_cap else 'off'} "
            f"(rise ${dir_used['rise']:.0f}/drop ${dir_used['drop']:.0f}), "
            f"chain {chain_hits}h/{chain_misses}m)",
        )

    except Exception:
        logger.exception("Scheduler: recommendation generation failed")
        _record_run("recommendations", "error")


def job_execute_recommendations():
    """10:00 AM ET — Auto-execute eligible recommendations (if enabled).

    Runs 30 min after the 09:30 ET regular session open so the execution_engine
    `allowed_hours_only` safety gate accepts submits (the prior 08:00 ET
    trigger blocked 100% of orders because the market was not yet open).
    """
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
            # Orphan sweep MUST run after sync_orders so just-submitted orders
            # are visible as in-flight (MU/LRCX premature closes, 2026-07-06/07).
            orphans = sync.close_orphan_paper_trades()
            # Residue detection also needs fresh orders in the DB, for the
            # same reason: an in-flight order is what marks a just-submitted
            # position as owned.
            residue = sync.detect_residue_positions()
            logger.info(
                f"Scheduler: portfolio sync complete — {pos} positions, "
                f"{orders} new orders, {orphans} orphans closed, "
                f"{len(residue)} residue"
            )
            _record_run(
                "portfolio_sync",
                f"ok ({pos} pos, {orders} orders, {orphans} orphaned, {len(residue)} residue)",
            )
        finally:
            db.close()
    except ValueError:
        logger.debug("Scheduler: portfolio sync skipped — Alpaca credentials not configured")
        _record_run("portfolio_sync", "skipped (no credentials)")
    except Exception:
        logger.exception("Scheduler: portfolio sync failed")
        _record_run("portfolio_sync", "error")


def job_monitor_fractional_exits():
    """Every 5 min during market hours — poll fractional positions for stop/target breach.

    Fractional long orders ship without broker-side brackets (Alpaca constraint),
    so the scheduler acts as the polling bracket. Skipped when
    `enable_fractional_shares` is off — paper $5k uses whole-share brackets and
    has no fractional positions to monitor.
    """
    try:
        from src.config import get_settings
        from src.db.session import SessionLocal
        from src.services.execution_engine import ExecutionEngine

        if not get_settings().enable_fractional_shares:
            _record_run("monitor_exits", "skipped (fractional disabled)")
            return

        logger.info("Scheduler: starting fractional exit monitor")
        db = SessionLocal()
        try:
            engine = ExecutionEngine(db)
            results = engine.monitor_fractional_exits()
            closed = sum(1 for r in results if r["status"] == "closed")
            errors = sum(1 for r in results if r["status"] == "error")
            logger.info(
                f"Scheduler: fractional exit monitor — {closed} closed, "
                f"{errors} errors, {len(results)} evaluated"
            )
            _record_run("monitor_exits", f"ok ({closed} closed / {len(results)} evaluated)")
        finally:
            db.close()
    except ValueError:
        _record_run("monitor_exits", "skipped (no credentials)")
    except Exception:
        logger.exception("Scheduler: fractional exit monitor failed")
        _record_run("monitor_exits", "error")


def job_evaluate_paper_exits(label: str = "paper_exits"):
    """Close open paper trades on stop/target/time/expiry rules.

    Runs twice on weekdays. The 6:15 AM ET run evaluates against the prior
    session's close (fetch_prices at 6:00 only sees completed bars pre-open).
    That leaves time-exits one session late: a trade whose Nth session
    completes today is not counted until tomorrow's run, and a boundary that
    lands on a Friday slips across the weekend (observed 2026-07-27: SNAP's
    10th session was Fri 7/24 but it closed Mon 7/27). The 4:30 PM ET run
    (label ``paper_exits_close``), after ``fetch_prices_close`` at 4:30 pulls
    the just-closed session, counts that session the same day so time-exits
    fire on schedule and price off the actual same-day close.

    Safe to run twice: evaluate_paper_exits only queries status='open' trades,
    so a trade closed in the morning run is not re-evaluated in the evening
    (and vice-versa) — no double-close.
    """
    logger.info("Scheduler: starting paper exit evaluation (%s)", label)
    try:
        from src.db.session import SessionLocal
        from src.services.paper_exits import evaluate_paper_exits

        db = SessionLocal()
        try:
            results = evaluate_paper_exits(db)
            closed = sum(1 for r in results if r["status"] == "closed")
            held = sum(1 for r in results if r["status"] == "held")
            errors = sum(1 for r in results if r["status"] == "error")

            # Mirror closes at the broker — a closed PaperTrade with a live
            # Alpaca position would desync the capital cap and trigger the
            # orphan sweep's inverse problem. Includes option/spread legs:
            # without unwinding them they lingered as residue and jammed the cap
            # (2026-07-30). unwind_broker_position routes by strategy.
            unwound = 0
            closed_ids = [
                r["id"] for r in results
                if r["status"] == "closed"
                and r.get("strategy") in (
                    "long", "short", "pair_short",
                    "options", "call_options", "spread", "bull_spread",
                )
            ]
            if closed_ids:
                try:
                    from src.db.models import PaperTrade
                    from src.services.execution_engine import ExecutionEngine
                    engine = ExecutionEngine(db)
                    for tid in closed_ids:
                        trade = db.get(PaperTrade, tid)
                        if trade is not None:
                            outcome = engine.unwind_broker_position(trade)
                            if outcome.get("status") in ("closed", "partial"):
                                unwound += 1
                except ValueError:
                    logger.info("paper exits: no Alpaca credentials, skipping broker unwind")

            logger.info(
                f"Scheduler: paper exits — {closed} closed ({unwound} unwound at broker), "
                f"{held} held, {errors} errors, {len(results)} evaluated"
            )
            _record_run(
                label,
                f"ok ({closed} closed / {unwound} unwound / {held} held / {len(results)} evaluated)",
            )
        finally:
            db.close()
    except Exception:
        logger.exception("Scheduler: paper exit evaluation failed")
        _record_run(label, "error")


def job_paper_validation():
    """Sunday 3:00 AM ET — persist the weekly paper-vs-backtest scoreboard.

    Trailing 30-day window. Writes a ValidationReport row and updates the
    paper_validation_* Prometheus gauges. This is the go/no-go time series
    for flipping paper trading to live.
    """
    logger.info("Scheduler: starting paper validation")
    try:
        import json as _json
        from datetime import timedelta

        from src.db.session import SessionLocal
        from src.db.models import ValidationReport
        from src.metrics import (
            paper_validation_num_trades,
            paper_validation_total_pnl,
            paper_validation_win_rate,
        )
        from src.services.live_gate import evaluate_gates, format_gates
        from src.services.paper_validation import PaperValidator, format_report

        end = date.today()
        start = end - timedelta(days=30)
        db = SessionLocal()
        try:
            report = PaperValidator(db).validate(start, end)
            report["live_gate"] = evaluate_gates(db)
            paper = report["paper"]
            db.add(ValidationReport(
                window_start=start,
                window_end=end,
                num_paper_trades=paper["num_trades"],
                paper_win_rate=paper["win_rate"],
                paper_total_pnl=paper["total_pnl"],
                ok=report["ok"],
                report_json=_json.dumps(report),
            ))
            db.commit()
            paper_validation_win_rate.set(paper["win_rate"])
            paper_validation_total_pnl.set(paper["total_pnl"])
            paper_validation_num_trades.set(paper["num_trades"])
            logger.info(
                "Scheduler: paper validation report\n%s\n%s",
                format_report(report), format_gates(report["live_gate"]),
            )
            gate_summary = ", ".join(
                f"{a['arm']}={'READY' if a['ready'] else 'not-ready'}"
                for a in report["live_gate"]["arms"]
            )
            _record_run(
                "paper_validation",
                f"ok ({paper['num_trades']} trades, wr {paper['win_rate']:.0%}, "
                f"pnl ${paper['total_pnl']:.0f}, {'ok' if report['ok'] else 'DIVERGENT'}; "
                f"gates: {gate_summary})",
            )
        finally:
            db.close()
    except Exception:
        logger.exception("Scheduler: paper validation failed")
        _record_run("paper_validation", "error")


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
    scheduler.add_job(job_evaluate_paper_exits, CronTrigger(hour=6, minute=15, timezone="US/Eastern", day_of_week="mon-fri"), id="paper_exits", replace_existing=True)
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

    # Auto-execute: 10:00 AM ET — 30 min after regular session open so the
    # `allowed_hours_only` safety gate in execution_engine accepts orders.
    # Prior 08:00 ET trigger blocked 100% of submits (market not yet open).
    scheduler.add_job(job_execute_recommendations, CronTrigger(hour=10, minute=0, timezone="US/Eastern", day_of_week="mon-fri"), id="execute_recommendations", replace_existing=True)

    # Portfolio sync: every 5 minutes, weekdays 9:30 AM - 4:00 PM ET
    scheduler.add_job(job_sync_portfolio, CronTrigger(minute="*/5", hour="9-15", timezone="US/Eastern", day_of_week="mon-fri"), id="portfolio_sync", replace_existing=True)
    # Also catch the 16:00 close
    scheduler.add_job(job_sync_portfolio, CronTrigger(minute="0,5", hour=16, timezone="US/Eastern", day_of_week="mon-fri"), id="portfolio_sync_close", replace_existing=True)

    # Evening close run: pull the just-closed session at 4:30 PM ET, then
    # re-evaluate paper exits at 4:45 PM ET. The morning batch (6:00/6:15)
    # only ever sees the prior session, so time-exits fired a session late;
    # this run counts today's session same-day and prices exits off the
    # actual close. Distinct job ids/labels from the morning runs.
    scheduler.add_job(job_fetch_prices, CronTrigger(hour=16, minute=30, timezone="US/Eastern", day_of_week="mon-fri"), id="fetch_prices_close", args=["fetch_prices_close"], replace_existing=True)
    scheduler.add_job(job_evaluate_paper_exits, CronTrigger(hour=16, minute=45, timezone="US/Eastern", day_of_week="mon-fri"), id="paper_exits_close", args=["paper_exits_close"], replace_existing=True)

    # Fractional exit monitor: every 5 minutes, weekdays 9:30 AM - 4:00 PM ET.
    # Acts as the polling bracket for fractional long orders, which Alpaca
    # cannot attach broker-side stop/target to. Skipped automatically when
    # enable_fractional_shares is off.
    scheduler.add_job(job_monitor_fractional_exits, CronTrigger(minute="*/5", hour="9-15", timezone="US/Eastern", day_of_week="mon-fri"), id="monitor_exits", replace_existing=True)

    # Monthly model retraining: first Sunday of each month at 2:00 AM ET
    scheduler.add_job(job_paper_validation, CronTrigger(hour=3, minute=0, timezone="US/Eastern", day_of_week="sun"), id="paper_validation", replace_existing=True)
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
