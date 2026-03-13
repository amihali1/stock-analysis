# Agent Memory Index

This directory serves as persistent context for AI agents working on the stock-analysis platform.
Agents MUST read relevant memory files before starting work to avoid redundant decisions and token waste.

## How to Use

1. **Before starting any task**: Read `ARCHITECTURE.md` and `DECISIONS.md`
2. **After completing a task**: Update `SESSION_LOG.md` with what was done
3. **When making a design decision**: Add it to `DECISIONS.md` with rationale
4. **When discovering a gotcha/lesson**: Add it to `LESSONS.md`

## Memory Files

| File | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, tech stack, integration points |
| [DECISIONS.md](DECISIONS.md) | Design decisions with rationale (ADR-lite) |
| [LESSONS.md](LESSONS.md) | Gotchas, debugging lessons, things that didn't work |
| [SESSION_LOG.md](SESSION_LOG.md) | Chronological log of what each agent session accomplished |
| [DATA_SOURCES.md](DATA_SOURCES.md) | API endpoints, rate limits, data quirks |
| [MODEL_REGISTRY.md](MODEL_REGISTRY.md) | Trained models, hyperparams, performance metrics |
