# P1-002: Build sentiment analysis pipeline

**Status**: todo
**Phase**: 1
**Dependencies**: P1-001, P0-002
**Estimated scope**: medium

## Description
Build the sentiment pipeline: fetch headlines from Finviz/NewsAPI, send to Ollama with structured prompts, parse sentiment scores, store in DB.

## Acceptance Criteria
- [ ] `SentimentAnalyzer` class in `pipeline/sentiment.py`
- [ ] Fetches headlines from at least one source (Finviz first, NewsAPI as stretch)
- [ ] Structured prompt returns JSON: `{"sentiment": float, "confidence": float, "reasoning": str}`
- [ ] Pydantic model validates Ollama responses
- [ ] Falls back to regex extraction if JSON parse fails
- [ ] Aggregates multiple headlines into composite score per ticker
- [ ] Stores results in `sentiment_scores` table
- [ ] Can be run as `python -m src.pipeline.sentiment`

## Files to Create/Modify
- `backend/src/pipeline/sentiment.py`
- `backend/src/services/headline_fetcher.py`
- `backend/tests/test_sentiment.py`

## Notes
Prompt engineering is key. Start with a simple zero-shot prompt, iterate based on output quality. Log raw Ollama responses for debugging.
