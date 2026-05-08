"""Tests for the SEC EDGAR HTTP base client (P10-005)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest

from src.services import sec_http
from src.services.sec_http import (
    USER_AGENT,
    SecHttpClient,
    _RateLimiter,
    make_client,
)


def _resp(status_code: int, payload: dict | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = payload or {}
    r.request = MagicMock()
    return r


class TestUserAgent:
    def test_make_client_sets_required_user_agent(self):
        c = make_client()
        try:
            assert c.headers["user-agent"] == USER_AGENT
        finally:
            c.close()

    def test_user_agent_includes_contact_email(self):
        # SEC fair-use rule: User-Agent must include a contact identifier.
        # Guard against future edits that drop the email.
        assert "@" in USER_AGENT


class TestRateLimiter:
    def test_zero_rate_means_no_wait(self):
        lim = _RateLimiter(rate_per_sec=0)
        t0 = time.monotonic()
        lim.wait(); lim.wait(); lim.wait()
        assert time.monotonic() - t0 < 0.05

    def test_enforces_minimum_interval(self):
        lim = _RateLimiter(rate_per_sec=20.0)  # min interval 50ms
        t0 = time.monotonic()
        lim.wait()
        lim.wait()
        lim.wait()
        # 3 waits → 2 enforced gaps → ~100ms minimum
        assert time.monotonic() - t0 >= 0.09


class TestSecHttpClientGet:
    def test_returns_2xx_response_immediately(self):
        underlying = MagicMock()
        underlying.get.return_value = _resp(200, {"ok": True})
        client = SecHttpClient(client=underlying, limiter=_RateLimiter(0))

        resp = client.get("https://data.sec.gov/foo")
        assert resp.status_code == 200
        assert underlying.get.call_count == 1

    def test_retries_on_429_then_succeeds(self):
        underlying = MagicMock()
        underlying.get.side_effect = [_resp(429), _resp(200, {"ok": 1})]
        client = SecHttpClient(
            client=underlying,
            backoff_base=1.0,  # no real sleep
            limiter=_RateLimiter(0),
        )

        resp = client.get("https://data.sec.gov/foo")
        assert resp.status_code == 200
        assert underlying.get.call_count == 2

    def test_retries_on_5xx(self):
        underlying = MagicMock()
        underlying.get.side_effect = [_resp(503), _resp(502), _resp(200, {"ok": 1})]
        client = SecHttpClient(
            client=underlying, backoff_base=1.0, limiter=_RateLimiter(0),
        )
        resp = client.get("https://data.sec.gov/foo")
        assert resp.status_code == 200
        assert underlying.get.call_count == 3

    def test_does_not_retry_4xx_other_than_429(self):
        # 403 = malformed/forbidden, retrying just wastes the rate budget.
        underlying = MagicMock()
        bad = _resp(403)
        bad.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=bad.request, response=bad,
        )
        underlying.get.return_value = bad
        client = SecHttpClient(
            client=underlying, backoff_base=1.0, limiter=_RateLimiter(0),
        )

        with pytest.raises(httpx.HTTPStatusError):
            client.get("https://data.sec.gov/foo")
        assert underlying.get.call_count == 1

    def test_gives_up_after_max_retries(self):
        underlying = MagicMock()
        underlying.get.return_value = _resp(503)
        client = SecHttpClient(
            client=underlying,
            max_retries=3,
            backoff_base=1.0,
            limiter=_RateLimiter(0),
        )
        with pytest.raises(httpx.HTTPStatusError):
            client.get("https://data.sec.gov/foo")
        assert underlying.get.call_count == 3

    def test_retries_on_network_error(self):
        underlying = MagicMock()
        underlying.get.side_effect = [
            httpx.ConnectError("connection refused"),
            _resp(200, {"ok": 1}),
        ]
        client = SecHttpClient(
            client=underlying, backoff_base=1.0, limiter=_RateLimiter(0),
        )
        resp = client.get("https://data.sec.gov/foo")
        assert resp.status_code == 200
        assert underlying.get.call_count == 2

    def test_uses_rate_limiter_before_each_request(self):
        underlying = MagicMock()
        underlying.get.return_value = _resp(200)
        limiter = MagicMock(spec=_RateLimiter)
        client = SecHttpClient(client=underlying, limiter=limiter)

        client.get("https://data.sec.gov/a")
        client.get("https://data.sec.gov/b")
        assert limiter.wait.call_count == 2

    def test_context_manager_closes_owned_client(self):
        with SecHttpClient(limiter=_RateLimiter(0)) as client:
            owned = client.client
        # httpx Client is closed — subsequent get raises.
        with pytest.raises(RuntimeError):
            owned.get("https://example.com")
