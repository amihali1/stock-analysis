# P6-003: Alerts UI

**Status**: todo
**Phase**: 6
**Dependencies**: P5-003, P4-001
**Estimated scope**: medium

## Description
Frontend page to configure Discord/Telegram webhook settings, view alert history, and acknowledge alerts.

## Acceptance Criteria
- [ ] `/alerts` page with two sections: settings and history
- [ ] Settings: add/edit Discord webhook URL or Telegram bot token + chat ID
- [ ] Toggle alert types per channel (stop-loss, target hit, high conviction)
- [ ] Configurable score threshold slider
- [ ] Test button that sends a test alert to the configured channel
- [ ] Alert history table: type, ticker, message, timestamp, acknowledged status
- [ ] Bulk acknowledge button
- [ ] Unread alert count badge in navigation
- [ ] Add nav link for Alerts page

## Files to Create/Modify
- `frontend/src/app/alerts/page.tsx`
- `frontend/src/components/AlertSettingsForm.tsx`
- `frontend/src/components/AlertHistory.tsx`
- `frontend/src/lib/api.ts` (add alert functions)
- `frontend/src/lib/types.ts` (add alert types)
- `frontend/src/app/layout.tsx` (add nav link + badge)
