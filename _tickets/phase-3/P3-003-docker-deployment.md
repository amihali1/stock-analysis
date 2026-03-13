# P3-003: Dockerize and deploy to homelab

**Status**: done
**Phase**: 3
**Dependencies**: P3-002
**Estimated scope**: medium

## Description
Create Docker setup for the backend + PostgreSQL. Deploy to homelab GPU VM at 10.0.0.47.

## Acceptance Criteria
- [ ] `Dockerfile` builds the FastAPI backend
- [ ] `docker-compose.yml` includes: backend, PostgreSQL, connects to existing Ollama network
- [ ] Environment variables for all secrets (API keys, DB password)
- [ ] PostgreSQL data persisted via Docker volume
- [ ] GPU passthrough for PyTorch inference (nvidia runtime)
- [ ] `scripts/deploy.sh` copies files and runs docker compose on homelab
- [ ] `curl http://10.0.0.47:8000/api/health` returns OK after deployment

## Files to Create/Modify
- `backend/Dockerfile`
- `backend/docker-compose.yml`
- `scripts/deploy.sh`
- `backend/.env.example`
