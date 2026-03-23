# P6-006: Authentication

**Status**: todo
**Phase**: 6
**Dependencies**: P4-001
**Estimated scope**: medium

## Description
Add basic authentication to protect the dashboard and API when exposed beyond the homelab network.

## Acceptance Criteria
- [ ] Username/password auth with bcrypt password hashing
- [ ] `User` DB model with hashed password, created_at, last_login
- [ ] JWT token-based API authentication (access + refresh tokens)
- [ ] Login page in frontend with token storage in httpOnly cookie or localStorage
- [ ] Protected API routes (all except /api/health and /docs)
- [ ] Middleware to validate JWT on protected routes
- [ ] Default admin user created on first run (configurable via env vars)
- [ ] Logout endpoint that invalidates refresh token
- [ ] Session timeout (configurable, default 24h)

## Files to Create/Modify
- `backend/src/auth/` (new module: models, jwt, middleware, routes)
- `backend/src/db/models.py` (add User model)
- `backend/src/main.py` (add auth middleware)
- `backend/alembic/versions/` (new migration)
- `frontend/src/app/login/page.tsx`
- `frontend/src/lib/api.ts` (add auth headers)
- `frontend/src/app/layout.tsx` (auth guard)
