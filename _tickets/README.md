# Ticket System

Each ticket is a self-contained unit of work designed for a single agent session / small PR.

## Ticket Format

```markdown
# TICKET-ID: Title

**Status**: todo | in-progress | done
**Phase**: 0-5
**Dependencies**: list of ticket IDs that must be done first
**Estimated scope**: small (1-2 files) | medium (3-5 files) | large (6+ files)

## Description
What needs to be done.

## Acceptance Criteria
- [ ] Checkable items

## Files to Create/Modify
- list of files

## Notes
Any additional context.
```

## Status Legend
- `todo` — not started
- `in-progress` — being worked on
- `done` — completed and merged

## Workflow
1. Agent reads `_memory/` for context
2. Agent picks up a ticket whose dependencies are all `done`
3. Agent sets status to `in-progress`
4. Agent does the work, creates a branch `ticket/TICKET-ID`
5. Agent sets status to `done`
6. Agent updates `_memory/SESSION_LOG.md`
