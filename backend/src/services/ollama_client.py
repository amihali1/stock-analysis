"""Async HTTP client for the Ollama API with retry logic."""

from __future__ import annotations

import json
import logging

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

# Retryable status codes
_RETRYABLE = {408, 429, 500, 502, 503, 504}


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int = 3,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.ollama_timeout
        self.max_retries = max_retries

    async def generate(self, prompt: str, model: str | None = None) -> str:
        """Send a prompt to Ollama and return the response text."""
        model = model or self.model
        payload = {"model": model, "prompt": prompt, "stream": False, "think": False}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._request_with_retry(
                client, "POST", f"{self.base_url}/api/generate", json=payload
            )

        data = response.json()
        return data.get("response", "")

    async def generate_json(self, prompt: str, model: str | None = None) -> dict:
        """Send a prompt and parse the response as JSON.

        Falls back to extracting JSON from the response text if direct parse fails.
        """
        raw = await self.generate(prompt, model)
        logger.debug(f"Raw Ollama response: {raw[:500]}")

        # Try direct JSON parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code block or embedded JSON
        return _extract_json(raw)

    async def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def _request_with_retry(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff retry on failures."""
        import asyncio

        last_exc = None
        for attempt in range(self.max_retries):
            try:
                response = await client.request(method, url, **kwargs)
                if response.status_code not in _RETRYABLE:
                    response.raise_for_status()
                    return response
                logger.warning(
                    f"Ollama returned {response.status_code}, retry {attempt + 1}/{self.max_retries}"
                )
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning(f"Ollama request failed: {e}, retry {attempt + 1}/{self.max_retries}")
                last_exc = e

            if attempt < self.max_retries - 1:
                backoff = 2**attempt
                await asyncio.sleep(backoff)

        raise last_exc


def _extract_json(text: str) -> dict:
    """Try to extract a JSON object from text that may contain markdown or extra content."""
    import re

    # Look for ```json ... ``` blocks
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Look for first { ... } in the text
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not extract JSON from Ollama response: {text[:200]}")
    return {}
