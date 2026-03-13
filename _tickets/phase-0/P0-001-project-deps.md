# P0-001: Install and verify Python dependencies

**Status**: todo
**Phase**: 0
**Dependencies**: none
**Estimated scope**: small

## Description
Install all Python dependencies from pyproject.toml. Verify imports work. Set up virtual environment.

## Acceptance Criteria
- [ ] Virtual environment created at `backend/.venv`
- [ ] All deps from pyproject.toml install without errors
- [ ] `python -c "import fastapi, sqlalchemy, yfinance, xgboost, torch"` succeeds
- [ ] `uvicorn src.main:app` starts without errors (health endpoint responds)

## Files to Create/Modify
- `backend/.venv/` (created by venv)
- `backend/pyproject.toml` (may need version pinning adjustments)

## Notes
If PyTorch CUDA doesn't work on Windows dev machine, that's fine — GPU training happens on homelab. CPU torch is sufficient for dev.
