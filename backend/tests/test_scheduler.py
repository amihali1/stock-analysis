"""Regression tests for the APScheduler wiring.

These exist because on 2026-05-08 job_sentiment silently produced no entry
in last_job_runs — the only async-def job in the file was registered fine
but never executed. The failure mode was invisible from the health
endpoint. Tests here assert (a) every expected job id is registered and
(b) every job callable, when invoked, records something in last_job_runs.
"""

from __future__ import annotations

import pytest

from src.api.routes import health as health_module
from src.pipeline import scheduler as scheduler_module


EXPECTED_JOB_IDS = {
    "fetch_prices",
    "compute_indicators",
    "fetch_options",
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


@pytest.mark.parametrize(
    "job_callable, job_name",
    [
        (scheduler_module.job_fetch_prices, "fetch_prices"),
        (scheduler_module.job_compute_indicators, "compute_indicators"),
        (scheduler_module.job_fetch_options, "fetch_options"),
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
