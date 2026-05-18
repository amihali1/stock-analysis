"""Regression tests for the APScheduler wiring.

These exist because on 2026-05-08 job_sentiment silently produced no entry
in last_job_runs — the only async-def job in the file was registered fine
but never executed. The failure mode was invisible from the health
endpoint. Tests here assert (a) every expected job id is registered and
(b) every job callable, when invoked, records something in last_job_runs.
"""

from __future__ import annotations

import os

import pytest

from src.api.routes import health as health_module
from src.pipeline import scheduler as scheduler_module


EXPECTED_JOB_IDS = {
    "fetch_prices",
    "compute_indicators",
    "fetch_options",
    "fetch_insider_transactions",
    "fetch_wikipedia_pageviews",
    "sentiment",
    "recommendations",
    "execute_recommendations",
    "portfolio_sync",
    "portfolio_sync_close",
    "retrain_models",
    "fetch_earnings",
}


@pytest.fixture
def clean_scheduler():
    """Reset the module-level scheduler + last_job_runs between tests."""
    scheduler_module.scheduler.remove_all_jobs()
    health_module.last_job_runs.clear()
    yield
    scheduler_module.scheduler.remove_all_jobs()
    health_module.last_job_runs.clear()


def test_init_scheduler_registers_all_expected_jobs(clean_scheduler, monkeypatch):
    monkeypatch.setattr(scheduler_module.scheduler, "start", lambda: None)
    scheduler_module.init_scheduler()
    registered = {job.id for job in scheduler_module.scheduler.get_jobs()}
    assert registered == EXPECTED_JOB_IDS


@pytest.mark.skipif(
    bool(os.getenv("CI")),
    reason=(
        "Invokes real production scheduler callables which fan out to yfinance, "
        "Wikipedia, SEC, and the homelab Ollama at 10.0.0.47. On GitHub-hosted "
        "runners Ollama is unroutable and per-ticker TCP timeouts across the "
        "158-ticker default watchlist push the job out past the 6h CI ceiling. "
        "Run locally (without CI=1) to exercise the real bodies."
    ),
)
@pytest.mark.parametrize(
    "job_callable, job_name",
    [
        (scheduler_module.job_fetch_prices, "fetch_prices"),
        (scheduler_module.job_compute_indicators, "compute_indicators"),
        (scheduler_module.job_fetch_options, "fetch_options"),
        (scheduler_module.job_fetch_insider_transactions, "fetch_insider_transactions"),
        (scheduler_module.job_fetch_wikipedia_pageviews, "fetch_wikipedia_pageviews"),
        (scheduler_module.job_sentiment, "sentiment"),
        (scheduler_module.job_fetch_earnings, "fetch_earnings"),
        (scheduler_module.job_generate_recommendations, "recommendations"),
        (scheduler_module.job_execute_recommendations, "execute_recommendations"),
        (scheduler_module.job_sync_portfolio, "portfolio_sync"),
        (scheduler_module.job_retrain_models, "retrain_models"),
    ],
)
def test_job_records_entry_in_last_job_runs(job_callable, job_name, clean_scheduler):
    """Every job must call _record_run on both success and failure paths.

    We don't care whether the job succeeds in the test environment — we care
    that *some* entry lands in last_job_runs. A missing entry means the
    callable bailed before its try/except (the silent-failure mode that hid
    sentiment breakage on 2026-05-08).
    """
    job_callable()
    assert job_name in health_module.last_job_runs


def test_job_sentiment_is_synchronous(clean_scheduler):
    """job_sentiment must be a sync callable, not a coroutine function.

    Async jobs in AsyncIOScheduler can silently no-op if the loop captured
    at startup isn't the loop running the tick. The sync wrapper around
    asyncio.run() is the contract that guards against that regression.
    """
    import inspect
    assert not inspect.iscoroutinefunction(scheduler_module.job_sentiment)


def test_record_run_increments_error_counter_only_on_error(clean_scheduler):
    """`_record_run` must bump pipeline_job_errors_total when status is error.

    This counter is the only Prometheus signal that survives the scheduler's
    handler-level try/except swallowing — without it, a hard job failure looks
    identical to a successful run in the metrics view. Regression case:
    2026-05-14 07:30 EDT scheduler run failed with a VARCHAR truncation,
    `_record_run("recommendations", "error")` fired, and zero alerts triggered
    because nothing was incrementing on the error path.
    """
    from src.metrics import pipeline_job_errors_total

    before = pipeline_job_errors_total.labels(job="recommendations")._value.get()
    scheduler_module._record_run("recommendations", "ok (10 recs)")
    assert pipeline_job_errors_total.labels(job="recommendations")._value.get() == before

    scheduler_module._record_run("recommendations", "skipped (no credentials)")
    assert pipeline_job_errors_total.labels(job="recommendations")._value.get() == before

    scheduler_module._record_run("recommendations", "error")
    assert pipeline_job_errors_total.labels(job="recommendations")._value.get() == before + 1


def test_recommendations_counter_emits_both_directions(clean_scheduler):
    """`pipeline_recommendations_generated_total` must expose a series for each
    direction so a regime shutout (e.g. 0 bear / 10 bull on 2026-05-14) is
    visible in Grafana — not indistinguishable from "metric missing".

    The scheduler bumps the counter unconditionally for both directions at the
    end of each `job_generate_recommendations` run, including `.inc(0)` for
    the shutout side. This test asserts the counter is labeled by direction
    and that incrementing one label leaves the other untouched.
    """
    from src.metrics import pipeline_recommendations_generated_total

    bear_before = pipeline_recommendations_generated_total.labels(direction="bear")._value.get()
    bull_before = pipeline_recommendations_generated_total.labels(direction="bull")._value.get()

    pipeline_recommendations_generated_total.labels(direction="bear").inc(0)
    pipeline_recommendations_generated_total.labels(direction="bull").inc(10)
    assert pipeline_recommendations_generated_total.labels(direction="bear")._value.get() == bear_before
    assert pipeline_recommendations_generated_total.labels(direction="bull")._value.get() == bull_before + 10

    pipeline_recommendations_generated_total.labels(direction="bear").inc(3)
    assert pipeline_recommendations_generated_total.labels(direction="bear")._value.get() == bear_before + 3
    assert pipeline_recommendations_generated_total.labels(direction="bull")._value.get() == bull_before + 10


class _RecLike:
    """Tiny stand-in for a Recommendation row — only the fields _rec_capital_cost reads."""
    def __init__(self, position_size, max_loss):
        self.position_size = position_size
        self.max_loss = max_loss


def test_rec_capital_cost_takes_max_of_position_and_loss():
    """Credit spreads write `position_size = credit received` (small) and
    `max_loss = collateral` (larger). The capital cost is the collateral.
    Long stock/options write `position_size = capital deployed` (larger) and
    `max_loss = stop-loss-bounded` (smaller). The capital cost is the deployed.
    `max()` handles both without a per-strategy lookup."""
    # Credit spread shape
    assert scheduler_module._rec_capital_cost(_RecLike(50.0, 450.0)) == 450.0
    # Long stock shape (capital deployed >> stop-loss max loss)
    assert scheduler_module._rec_capital_cost(_RecLike(1000.0, 50.0)) == 1000.0
    # Long call (both equal — premium = max loss = capital)
    assert scheduler_module._rec_capital_cost(_RecLike(300.0, 300.0)) == 300.0
    # Null safety
    assert scheduler_module._rec_capital_cost(_RecLike(None, 500.0)) == 500.0
    assert scheduler_module._rec_capital_cost(_RecLike(500.0, None)) == 500.0


def test_daily_capital_cap_default_is_5000():
    """User decision (bullish_side_build memo, 2026-05-12): $5k direction-blind
    pool. Lock the default so a config drift doesn't silently re-open the gate."""
    from src.config import get_settings
    assert get_settings().daily_capital_cap == 5000.0


def test_sentiment_upsert_failure_does_not_drop_batch(clean_scheduler, monkeypatch):
    """A single ticker's sentiment upsert blowing up must not silently drop the
    remaining tickers in the batch. Regression target: prior to this fix the
    `for ticker in results.items()` loop in `_job_sentiment_async` had no
    per-iteration try/except, so a constraint violation or transient DB error
    on one ticker would bubble up to the outer except and skip every ticker
    after it. The fix isolates each upsert and rolls back the session so
    subsequent iterations start clean.
    """
    import asyncio

    from src.metrics import pipeline_job_errors_total

    fake_results = {
        "AAPL": {"ticker": "AAPL", "scores_computed": 3, "composite_sentiment": 0.2},
        "BAD":  {"ticker": "BAD",  "scores_computed": 5, "composite_sentiment": -0.1},
        "NVDA": {"ticker": "NVDA", "scores_computed": 2, "composite_sentiment": 0.5},
    }

    class _FakeAnalyzer:
        def __init__(self, *a, **kw): pass
        async def analyze_all(self, *a, **kw): return fake_results
        def close(self): pass

    upserted: list[str] = []

    def _fake_upsert(db, ticker, on_date, **kwargs):
        if ticker == "BAD":
            raise RuntimeError("simulated DB failure for BAD")
        upserted.append(ticker)

    monkeypatch.setattr(scheduler_module, "_record_run", lambda *a, **kw: None)
    monkeypatch.setattr("src.pipeline.sentiment.SentimentAnalyzer", _FakeAnalyzer)
    monkeypatch.setattr("src.features.sentiment.upsert_daily_sentiment", _fake_upsert)

    err_before = pipeline_job_errors_total.labels(job="sentiment_upsert")._value.get()

    asyncio.run(scheduler_module._job_sentiment_async())

    assert "AAPL" in upserted, "tickers before the failure must persist"
    assert "NVDA" in upserted, "tickers after the failure must NOT be dropped"
    assert "BAD" not in upserted

    err_after = pipeline_job_errors_total.labels(job="sentiment_upsert")._value.get()
    assert err_after == err_before + 1, "per-ticker failure must bump pipeline_job_errors_total"
