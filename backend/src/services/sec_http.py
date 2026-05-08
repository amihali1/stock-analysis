"""SEC EDGAR HTTP base client (P10-005, reused by P10-006).

Centralizes the two non-negotiables for talking to SEC EDGAR:

  1. The fair-use User-Agent header. SEC requires every request to carry a
     descriptive User-Agent identifying the project and a contact email; without
     it EDGAR returns 403 Forbidden. Hardcoded here so individual fetchers
     can't accidentally send anonymous requests.

  2. A request-rate limiter. SEC's published cap is 10 req/sec per IP; we
     stay well under at 5 req/sec for headroom during bulk backfills, with a
     single shared token-bucket so concurrent fetchers don't blow the limit
     in aggregate.

Use `SecHttpClient` as a context manager, or pass an existing `httpx.Client`
that was constructed via `make_client()` if you need to share connection
pooling with another caller.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "stock-analysis andymihalik@gmail.com"
DEFAULT_RATE_LIMIT_PER_SEC = 5.0
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.5


class _RateLimiter:
    """Process-wide minimum-interval limiter shared by all SecHttpClient instances."""

    def __init__(self, rate_per_sec: float):
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


_GLOBAL_LIMITER = _RateLimiter(DEFAULT_RATE_LIMIT_PER_SEC)


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Construct an httpx.Client with the SEC-required User-Agent."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )


class SecHttpClient:
    """Thin wrapper around httpx.Client that enforces the SEC fair-use rules.

    Every GET goes through the shared rate limiter and retries on transient
    errors (5xx, 429, network) with exponential backoff. 4xx responses other
    than 429 raise immediately — they indicate a malformed request, not a
    transient blip.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        limiter: _RateLimiter | None = None,
    ):
        self._owns_client = client is None
        self.client = client or make_client()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._limiter = limiter or _GLOBAL_LIMITER

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "SecHttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._limiter.wait()
            try:
                resp = self.client.get(url, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("SEC GET %s network error (attempt %d): %s", url, attempt + 1, exc)
            else:
                if resp.status_code < 400:
                    return resp
                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "SEC GET %s status %d (attempt %d)",
                        url, resp.status_code, attempt + 1,
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"{resp.status_code}", request=resp.request, response=resp,
                    )
                else:
                    # 4xx other than 429 — don't retry, the request is broken.
                    resp.raise_for_status()
            time.sleep(self.backoff_base ** attempt)

        assert last_exc is not None  # for type checker
        raise last_exc
