# Build history

Phase-by-phase notes moved out of the README. Newer items (July-style summary at the bottom) came after phase 6.

## State at the end of phase 6 (2026-11-12)

Reference setup: Ollama (`llama3.2:3b`), Colima + WAHA `gows-arm` on an Apple Silicon Mac,
dashboard `./run.sh` on http://localhost:8000.
19 tests pass (`.venv/bin/python -m pytest -q`). Restart after code changes: `pkill -f "uvicorn app.main:app"; ./run.sh`.

Dashboard code: `app/ui.py` (HTML/CSS/JS, server-rendered, no build step) and the `dashboard()` route in `app/main.py`.
Redesigned 2026-11-12 as a phone-first app: bottom tab bar on phones (Today, Chats, Add, Leads, More → Follow-ups/WhatsApp/Settings), sidebar on desktop (>=900px). Section ids unchanged (approvals, replies, leads, add, menu, followups, whatsapp, settings). Built for 50+ items: Today shows drafts as compact rows in one list (replies, then follow-ups, then first messages), with filter chips (counts), 10 at a time + "Show 10 more", one open at a time; after Send/Discard the next draft opens (sessionStorage). Chats has a search box and shows 20 at a time.
Phase 1 done: lead detail page `/leads/{id}` (edit fields, chat bubbles, follow-ups, stage, write message, re-score,
cancel follow-ups, delete with confirm); Leads names and Replies rows (grouped per lead) link to it. Lead action POSTs
accept a hidden `next` path so they redirect back to the page they came from. Phase 2 done: Approvals cards have "Write a new one" + Shorter / More formal / Roman Urdu rewrites (use the edited
text), live character count, warning for `[fallback]`/`[placeholder]`, last 3 messages above replies/follow-ups, and failed
sends shown with the error and a "Try again" button. Phase 3 done: Leads tab has sort (best score / newest / stage), "Show more" past 50, row checkboxes with a bulk bar
(write messages, set stage, delete with confirm), Export CSV (`/leads.csv`), and an "AI is working on N leads…" banner that
auto-refreshes every 5s. Phase 4 done: overview cards on top of Approvals (waiting, sent today/limit, replies this week, leads by stage);
Settings tab edits business description, daily limit, seconds between sends, follow-up days and Ollama model, saved to
`.env` (other lines/secrets untouched) and applied without restart (`config.save_settings`); times show the exact local
time on hover (`<time>` tags, stored as UTC). Phase 5 done: toast messages (auto-hide, red + `role=alert` for errors), every clicked button shows "Working…",
keyboard `j`/`k` between drafts and Ctrl/⌘+Enter to send, skip link, label/input association, focus outlines, aria-current
on tabs. All five UI phases complete.
Phase 6 done (safety & results): optional dashboard password (`DASHBOARD_PASSWORD=` in `.env`, empty = no login; 30-day
cookie; `/webhook/waha` stays open because WAHA must reach it, so keep port 8000 off the internet); opted-out leads can't be
messaged anywhere (send, retry, bulk, write message) and show a clear mark; Approvals has reply rate / won / sent-in-7-days
cards; tick drafts and "Send selected" sends them in the background spaced by the min-seconds rule, skipping drafts with
warnings and stopping at the daily limit. Fixed: a send blocked by the limit no longer leaves the draft stuck as "approved".

## Dashboard UI plan (next)

Keep it server-rendered (no React/build step), light + dark mode, works on a phone (375px), plain-English labels.
Do the phases in order; run the tests and check desktop + mobile screenshots after each.

**Phase 1 – Lead detail & conversations (DONE)**
- Click a lead → detail page `/leads/{id}`: all fields editable (name, phone, website, niche, city, notes), AI score + reason,
  full WhatsApp conversation as chat bubbles (in/out, time, sent/draft/failed), planned follow-ups, stage.
- Actions there: write message, re-score, delete lead (with confirm), cancel follow-ups.
- Replies tab: group by lead (latest message per lead), click opens the lead detail.

**Phase 2 – Better approvals (DONE)**
- "Regenerate" button (new AI draft) and "Shorten" / "More formal" / "Roman Urdu" quick rewrites.
- Character count; warn if draft still contains `[fallback]` or placeholders like `[Name]`.
- Show the last 3 messages of the conversation above a reply draft.
- Failed sends shown with the error and a "Try again" button.

**Phase 3 – Leads table (DONE)**
- Sort by score / newest / stage; pagination or "show more" past 50.
- Checkboxes + bulk actions: write messages, change stage, delete.
- Export CSV button.
- While the AI is still scoring/drafting after an import, show "AI working on N leads…" and auto-refresh.

**Phase 4 – Overview & settings (DONE)**
- Overview cards at the top of Approvals: leads by stage (funnel), drafts waiting, sent today/limit, replies this week.
- Settings tab: edit `MY_BUSINESS`, daily limit, seconds between sends, follow-up days, Ollama model; saves to `.env`
  and applies without restart.
- Show times in the browser's local timezone (stored as UTC).

**Phase 5 – Polish (DONE)**
- Toast notifications instead of the flash bar; loading state on every button.
- Keyboard: `j/k` move between drafts, `⌘↵` send.
- Accessibility pass: labels, focus order, contrast.


## Later additions (2026-11-12)

- Remote access guide (`docs/REMOTE_ACCESS.md`, Tailscale).
- Daily database backups (`backups/`, last 14) and optional ntfy push on replies (`NTFY_TOPIC`).
- AI reply tags (interested / asking price / not now / not interested / other) with a suggested next step; keyword fallback when Ollama is down.
- Sending hours (default 09:00-20:00) and an opt-out line on first messages.
- "Find email/phone on website", campaign tag per lead with reply rate per campaign.
- Per-lead notes and dated reminders (shown in Today).
