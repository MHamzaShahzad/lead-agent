# Lead Agent — local WhatsApp lead generation with a free, private AI

Finds local businesses, scores them with a local AI (Ollama), drafts a personal WhatsApp message for each, and
tracks replies and follow-ups in a phone-friendly dashboard. **Nothing is sent until you approve it.**
Everything runs on your own computer: no cloud AI, no paid API, your leads never leave your machine.

> ⚠️ This uses your normal WhatsApp number through an unofficial gateway. Read [WhatsApp limits and ban risk](#whatsapp-limits-tos-and-ban-risk) first.

![Dashboard screenshot](docs/screenshots/today.png)
<!-- Add your own screenshots to docs/screenshots/ (today.png, chats.png, lead.png). -->

```
OpenStreetMap / CSV / manual -> SQLite (leads.db, auto-dedupe) -> Ollama: score + draft
WhatsApp <-> WAHA (Docker) <-webhook-> FastAPI dashboard: you approve -> WAHA -> WhatsApp
```

## Features

- **Find leads**: OpenStreetMap search (niche + city), CSV import, or by hand. Duplicates (phone, website, name+city) merge.
- **AI score (0-100)** and a draft first message per lead, written from your business description. Edit, approve, send;
  or tick several and "Send selected". Quick rewrites: shorter, more formal, Roman Urdu.
- **Replies**: incoming WhatsApp messages land in Chats with an AI tag (interested, asking price, not now, not
  interested) and a suggested next step. Unknown numbers that write to you become leads.
- **Follow-ups** after 3 and 7 days (editable); a reply cancels them.
- **Safety**: approval before every send, daily limit, gap between messages, per-weekday sending hours, STOP/opt-out
  respected everywhere, opt-out line on first messages, optional dashboard password.
- Lead page with chat, notes and reminders, campaign tags, "find email/phone on website", CSV export, reply-rate stats.
- Daily backup of `leads.db` to `backups/` (last 14). Optional phone push on replies via [ntfy](https://ntfy.sh).

## Requirements

| | macOS | Linux | Windows 10/11 |
|---|---|---|---|
| Python 3.11+ | `brew install python` | distro package | [python.org](https://www.python.org/downloads/) (tick "Add to PATH") |
| Ollama | [ollama.com](https://ollama.com/download) | `curl -fsSL https://ollama.com/install.sh \| sh` | [ollama.com](https://ollama.com/download) |
| Docker | Docker Desktop, or `brew install colima docker docker-compose && colima start` | Docker Engine + compose plugin | Docker Desktop (WSL 2) |

About 4 GB free RAM for the AI model (`llama3.2:3b`) and a phone with WhatsApp (a separate business number is best).

## Quick start

```bash
git clone https://github.com/<you>/<repo>.git && cd <repo>
cp .env.example .env          # Windows: copy .env.example .env
```

1. **Edit `.env`**: set `DASHBOARD_PASSWORD`, `WAHA_API_KEY` and `WEBHOOK_TOKEN` to long random values
   (e.g. `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`), describe your business in `MY_BUSINESS`,
   set `DEFAULT_COUNTRY_CODE`, and pick `WAHA_IMAGE`: `devlikeapro/waha:gows` (Intel/AMD) or
   `devlikeapro/waha:gows-arm` (Apple Silicon / ARM).
2. **Local AI**: `ollama pull llama3.2:3b` (Ollama must be running: open the app, or `ollama serve`).
3. **WhatsApp gateway**: `docker compose --env-file .env up -d`
4. **Dashboard**: `./run.sh` (macOS/Linux) or `powershell -ExecutionPolicy Bypass -File .\run.ps1` (Windows).
   Open http://localhost:8000 and sign in with `DASHBOARD_PASSWORD`.
5. **Link WhatsApp**: in the dashboard go to **More → WhatsApp**, enter your number with country code and press
   *Get pairing code*. You get an 8-character **pairing code**: on your phone open WhatsApp → Settings → Linked devices →
   Link a device → *Link with phone number instead*, and type the code. (Alternatively scan the QR code in the WAHA
   dashboard at http://localhost:3000/dashboard, user `admin`, password = `WAHA_API_KEY`.)
6. Add a few leads (**Add leads**), check the drafts in **Today**, and send.

Restart after code or `.env` changes: stop the dashboard (Ctrl+C, or `pkill -f "uvicorn app.main:app"`) and run it again.

## Configuration

Most settings are in the dashboard's **Settings** tab and are saved to `.env`: app name and tagline, business
description, daily limit, seconds between messages, follow-up days, per-weekday sending hours, opt-out line, AI model.
Only in `.env`: `DASHBOARD_PASSWORD`, `WAHA_API_KEY`, `WEBHOOK_TOKEN`, `DEFAULT_COUNTRY_CODE`, `WAHA_IMAGE`,
`OLLAMA_URL`, `WAHA_URL`, `NTFY_TOPIC`. Every option is described in [.env.example](.env.example).

Using it from your phone: [docs/REMOTE_ACCESS.md](docs/REMOTE_ACCESS.md) (Tailscale; never expose port 8000 to the internet).

## WhatsApp limits, ToS and ban risk

- WAHA connects your **normal WhatsApp account** as a linked device. This is **not** the official API and is against
  WhatsApp's Terms of Service for automated or bulk messaging. Your number can be **banned**, temporarily or forever.
  You use this at your own risk; the authors accept no liability.
- Only message businesses that plausibly want to hear from you, follow your local anti-spam and privacy laws
  (e.g. GDPR, PECR, TCPA), and stop when someone says STOP (the app does this automatically).
- Start with 10-15 messages a day on a new number, keep the gap between messages, personalise every message,
  avoid links in first messages, and prefer a separate number.
- For volume or marketing use the official [WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api)
  (first messages must be approved templates).

## Privacy and security

- **Data stays local**: leads and chats are in `leads.db`, WhatsApp session keys in `waha_sessions/`, settings and
  secrets in `.env`. All three are git-ignored; back them up privately and never commit or share them.
- The AI runs locally in Ollama. The only outside calls are OpenStreetMap (lead search), the websites you ask it to
  scan for contacts, WhatsApp itself, and ntfy if you turn it on (it gets "New reply from <name>" only).
- **Set `DASHBOARD_PASSWORD`.** The dashboard listens on your network (`0.0.0.0`) so the Docker container can reach the
  webhook; use `HOST=127.0.0.1 ./run.sh` if your Docker setup allows it. Never port-forward 8000 or 3000 to the internet;
  use Tailscale or a VPN for remote access.
- `/webhook/waha` must stay reachable without the dashboard login, so protect it with `WEBHOOK_TOKEN`
  (docker-compose.yml passes it to WAHA). WAHA's port 3000 is bound to localhost only.
- Secrets are never written to logs or shown in the dashboard.

## Troubleshooting

- **WAHA dashboard says "not connected" / 401**: it caches the API key in the browser. After changing `WAHA_API_KEY`,
  recreate the container (`docker compose --env-file .env up -d --force-recreate`), log in again and re-enter the key
  in the WAHA dashboard's server settings, or clear site data for localhost:3000.
- **Replies don't arrive**: check `docker logs waha` for webhook errors; the dashboard must run on port 8000 and
  `WEBHOOK_TOKEN` must match in `.env` for both (recreate the container after changing it). On Linux without Docker
  Desktop, `host.docker.internal` comes from `extra_hosts` in docker-compose.yml.
- **"Add the country code"** when sending: the number starts with 0; set `DEFAULT_COUNTRY_CODE` or edit the number.
- **Drafts say `[fallback]`**: Ollama was not reachable. Run `ollama list`, check `OLLAMA_URL` and the model name.
- **Apple Silicon: container exits / exec format error**: set `WAHA_IMAGE=devlikeapro/waha:gows-arm`.
- **Pairing code doesn't work**: codes expire within minutes; press Link again. Make sure the number has the country code.

## Development and tests

```bash
.venv/bin/python -m pytest -q                 # no Ollama or WhatsApp needed (fallbacks + fake sender)
REAL_OLLAMA=1 .venv/bin/python -m pytest -q   # with the real local AI
```
Windows: `.\.venv\Scripts\python.exe -m pytest -q`. CI runs the tests on every push (`.github/workflows/tests.yml`).

**Code map**: `app/main.py` (routes, webhook, background loop), `app/ui.py` (HTML/CSS/JS, server-rendered, no build
step), `app/db.py` (SQLite), `app/ai.py` (Ollama prompts, reply tags), `app/whatsapp.py` (WAHA, limits, sending hours),
`app/finder.py` (OSM, CSV, website contacts), `app/notify.py` (ntfy), `app/auth.py`, `app/config.py`.
Build notes: [docs/HISTORY.md](docs/HISTORY.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
