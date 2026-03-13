"""Unit tests for OllamaClient with mocked HTTP responses."""

import json
import pytest
import httpx

from src.services.ollama_client import OllamaClient, _extract_json


@pytest.fixture
def client():
    return OllamaClient(base_url="http://localhost:11434", timeout=5.0, max_retries=1)


class TestExtractJson:
    def test_direct_json(self):
        result = _extract_json('{"sentiment": 0.5, "confidence": 0.8}')
        assert result["sentiment"] == 0.5

    def test_markdown_code_block(self):
        text = 'Here is my analysis:\n```json\n{"sentiment": -0.3, "confidence": 0.7}\n```\n'
        result = _extract_json(text)
        assert result["sentiment"] == -0.3

    def test_embedded_json(self):
        text = "The sentiment is negative. {\"sentiment\": -0.8, \"confidence\": 0.9} That's my analysis."
        result = _extract_json(text)
        assert result["sentiment"] == -0.8

    def test_no_json(self):
        result = _extract_json("No JSON here at all.")
        assert result == {}


class TestOllamaClientGenerate:
    @pytest.mark.asyncio
    async def test_generate_success(self, client, monkeypatch):
        async def mock_request(self, method, url, **kwargs):
            return httpx.Response(
                200,
                json={"response": "Test response"},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)
        result = await client.generate("Hello")
        assert result == "Test response"

    @pytest.mark.asyncio
    async def test_generate_json_success(self, client, monkeypatch):
        payload = {"sentiment": 0.5, "confidence": 0.9, "reasoning": "test"}

        async def mock_request(self, method, url, **kwargs):
            return httpx.Response(
                200,
                json={"response": json.dumps(payload)},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)
        result = await client.generate_json("Analyze this")
        assert result["sentiment"] == 0.5
        assert result["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_generate_json_markdown_response(self, client, monkeypatch):
        raw_response = '```json\n{"sentiment": -0.7, "confidence": 0.6, "reasoning": "bad news"}\n```'

        async def mock_request(self, method, url, **kwargs):
            return httpx.Response(
                200,
                json={"response": raw_response},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)
        result = await client.generate_json("Analyze this")
        assert result["sentiment"] == -0.7


class TestOllamaClientHealth:
    @pytest.mark.asyncio
    async def test_is_available_true(self, client, monkeypatch):
        async def mock_get(self, url, **kwargs):
            return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        assert await client.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false(self, client, monkeypatch):
        async def mock_get(self, url, **kwargs):
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
        assert await client.is_available() is False
