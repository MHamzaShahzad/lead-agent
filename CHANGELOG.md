# Changelog

All notable changes to Lead Agent. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/). Earlier build notes live in [docs/HISTORY.md](docs/HISTORY.md).

## [1.0.0] - 2026-11-12

First stable release.

### Lead finding
- Search OpenStreetMap by niche and city, optional Google Places (New) source (`GOOGLE_PLACES_API_KEY`, official API only), CSV import, or add by hand.
- Duplicates (phone, website, name + city) merge instead of piling up.
- "Find email/phone on website" fills empty contact fields from a lead's own site.
- Campaign tag per lead, with reply rate per campaign.

### AI (local, via Ollama)
- Lead score (0-100) with a reason, and a personal first message per lead written from your business description.
- Quick rewrites: shorter, more formal, Roman Urdu. Warnings for `[fallback]` and placeholder text.
- Reply tags (interested, asking price, not now, not interested, other) with a suggested next step; keyword fallback when Ollama is down.
- AI follow-ups and AI email drafts (subject + body).

### Sending and channels
- Nothing is sent until you approve it. Send / Send selected feed a sending queue with a random gap between messages, per-weekday sending hours, daily limits and pause/resume.
- WhatsApp through WAHA (your own number, unofficial) or the official WhatsApp Cloud API (templates for first contact).
- Optional email channel: SMTP sending through the same queue, IMAP reply polling, unsubscribe line and `List-Unsubscribe`.
- STOP / unsubscribe handling: opted-out leads can never be messaged. Opt-out line on first messages.
- Automatic follow-ups (default day 3 and 7), cancelled when the lead replies.

### Dashboard
- Phone-first, server-rendered (no build step), light and dark mode. Today, Chats, Leads, Add leads, Follow-ups, WhatsApp, Settings.
- Lead detail page with the whole conversation, notes and dated reminders, stage, planned follow-ups.
- Keyboard shortcuts, toasts, accessibility pass, CSV export.
- First-run `/setup` page, optional dashboard password, branding (name and tagline).

### Integrations
- CRM REST API under `/api/v1` (Bearer `API_KEY`, upsert on `external_id`) and signed outgoing webhooks (`CRM_WEBHOOK_URL`), for HubSpot, n8n, Zapier, Make. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
- Optional push alert on replies through ntfy (never includes message text).

### Running it
- `run.sh` / `run.ps1`, `setup.py` guided setup, `docker-compose.yml` (WAHA) and `docker-compose.full.yml` (dashboard + WAHA + optional Ollama).
- **Demo mode** (`./run.sh --demo`, `DEMO_MODE=1` or `python -m app.demo`): sample leads, fake WhatsApp and email, "Simulate reply" button, its own database.
- Daily database backups (last 14). Remote access guide ([docs/REMOTE_ACCESS.md](docs/REMOTE_ACCESS.md)).

### Security
- Optional `WEBHOOK_TOKEN` on `/webhook/waha`, and optional `WAHA_WEBHOOK_HMAC_KEY` to verify WAHA's HMAC-SHA512 signature (`X-Webhook-Hmac`), constant-time compare.
- Cloud API webhook verified with `X-Hub-Signature-256`. Secrets are never logged or shown in the dashboard.

### Project
- MIT licence, pytest suite run by GitHub Actions on Python 3.11 and 3.12, contributing guide and issue/PR templates.
