# P4-001: Scaffold Next.js frontend

**Status**: todo
**Phase**: 4
**Dependencies**: P3-001
**Estimated scope**: medium

## Description
Set up the Next.js 15 project with TypeScript, Tailwind, and the API client.

## Acceptance Criteria
- [ ] Next.js 15 project initialized in `frontend/`
- [ ] TypeScript + Tailwind configured
- [ ] API client in `lib/api.ts` with typed fetch functions
- [ ] Type definitions in `lib/types.ts` matching backend Pydantic models
- [ ] Basic layout with navigation (Dashboard, Shorts, Options)
- [ ] Environment variable for backend URL
- [ ] `npm run dev` starts on port 3100

## Files to Create/Modify
- `frontend/package.json`
- `frontend/src/app/layout.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
