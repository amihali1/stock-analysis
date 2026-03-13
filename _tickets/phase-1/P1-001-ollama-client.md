# P1-001: Build Ollama client service

**Status**: done
**Phase**: 1
**Dependencies**: P0-001
**Estimated scope**: small

## Description
Build an async HTTP client for the Ollama API. Handles connection, retries, and JSON response parsing.

## Acceptance Criteria
- [ ] `OllamaClient` class in `services/ollama_client.py`
- [ ] Async `generate(prompt: str, model: str) -> str` method using httpx
- [ ] Configurable base URL (default: `http://10.0.0.47:11434`)
- [ ] Retry logic: 3 attempts with exponential backoff on timeout/5xx
- [ ] Timeout: 30s per request
- [ ] Health check method: `async is_available() -> bool`
- [ ] Unit test with mocked HTTP responses

## Files to Create/Modify
- `backend/src/services/ollama_client.py`
- `backend/tests/test_ollama_client.py`
