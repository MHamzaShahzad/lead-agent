import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# Demo mode: sample data, fake WhatsApp/email, its own database. Never touches leads.db, .env or real services.
DEMO_MODE = os.getenv("DEMO_MODE", "").strip() in ("1", "true", "yes")
DB_PATH = str(ROOT / "demo.db") if DEMO_MODE else os.getenv("DB_PATH", str(ROOT / "leads.db"))
if DEMO_MODE:
    DB_PATH = os.getenv("DEMO_DB_PATH", DB_PATH)
    if Path(DB_PATH).resolve().name == "leads.db":
        raise SystemExit("Demo mode refuses to use leads.db. Set DEMO_DB_PATH to another file.")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
WAHA_URL = os.getenv("WAHA_URL", "http://localhost:3000")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")
# WhatsApp provider: "waha" (default, links a normal account, unofficial) or "cloud" (official Meta Cloud API).
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "waha").strip().lower()
WA_CLOUD_TOKEN = os.getenv("WA_CLOUD_TOKEN", "").strip()
WA_CLOUD_PHONE_NUMBER_ID = os.getenv("WA_CLOUD_PHONE_NUMBER_ID", "").strip()
WA_CLOUD_VERIFY_TOKEN = os.getenv("WA_CLOUD_VERIFY_TOKEN", "").strip()
WA_CLOUD_APP_SECRET = os.getenv("WA_CLOUD_APP_SECRET", "").strip()
WA_CLOUD_TEMPLATE = os.getenv("WA_CLOUD_TEMPLATE", "").strip()          # approved template, one {{1}} body variable
WA_CLOUD_TEMPLATE_LANG = os.getenv("WA_CLOUD_TEMPLATE_LANG", "en").strip() or "en"
# What YOU sell; the AI uses this to score leads and personalise messages.
MY_BUSINESS = os.getenv(
    "MY_BUSINESS",
    "We build modern websites and WhatsApp automation for small local businesses.",
)
# Branding shown in the dashboard header and browser tab (Settings tab or .env).
BRAND_NAME = os.getenv("BRAND_NAME", "Lead Agent").strip() or "Lead Agent"
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "").strip()
# Country calling code used for local numbers that start with 0 (e.g. 44 for UK, 92 for Pakistan). Empty = such
# numbers are refused until you add the country code.
DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "").strip().lstrip("+")
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "15"))
MIN_SECONDS_BETWEEN_SENDS = int(os.getenv("MIN_SECONDS_BETWEEN_SENDS", "45"))
# Random pause between queued sends: a value between SEND_GAP_MIN and SEND_GAP_MAX seconds each time.
# Unset = the fixed MIN_SECONDS_BETWEEN_SENDS above (so old setups behave as before).
SEND_GAP_MIN = int(os.getenv("SEND_GAP_MIN") or MIN_SECONDS_BETWEEN_SENDS)
SEND_GAP_MAX = max(SEND_GAP_MIN, int(os.getenv("SEND_GAP_MAX") or SEND_GAP_MIN))
SEND_TYPING = os.getenv("SEND_TYPING", "1") == "1"         # show "typing..." for a moment before each send
QUEUE_WORKER = os.getenv("QUEUE_WORKER", "1") == "1"       # background sender (turn off only for tests)
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")   # empty = no login
# Shared secret WAHA must send as ?token=... on the webhook URL (empty = webhook open; see docker-compose.yml).
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "").strip()
SEND_START = os.getenv("SEND_START", "09:00")          # local time window when messages may go out
SEND_END = os.getenv("SEND_END", "20:00")
# Per-weekday hours, Mon..Sun, e.g. "09:00-20:00,...,off". Empty = every day uses SEND_START-SEND_END.
SEND_SCHEDULE = os.getenv("SEND_SCHEDULE", "")
OPTOUT_ENABLED = os.getenv("OPTOUT_ENABLED", "1") == "1"   # add the opt-out line to FIRST messages
OPTOUT_LINE = os.getenv("OPTOUT_LINE", "Reply STOP to opt out")
BACKUP_DIR = os.getenv("BACKUP_DIR", str(ROOT / "backups"))
BACKUPS_KEEP = 14
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()   # empty = Google source hidden
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()           # empty = no push alerts
# CRM integration (docs/INTEGRATIONS.md). API_KEY empty = REST API disabled; CRM_WEBHOOK_URL empty = no outgoing webhooks.
API_KEY = os.getenv("API_KEY", "").strip()
CRM_WEBHOOK_URL = os.getenv("CRM_WEBHOOK_URL", "").strip()
CRM_WEBHOOK_SECRET = os.getenv("CRM_WEBHOOK_SECRET", "").strip()
# Optional email channel (docs: README "Email channel"). SMTP_HOST empty = email disabled; IMAP_HOST empty = replies not read.
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT") or 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()                 # e.g. "Sam <sam@example.com>"
SMTP_TLS = os.getenv("SMTP_TLS", "starttls").strip().lower()   # starttls (587), ssl (465) or none
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()
IMAP_PORT = int(os.getenv("IMAP_PORT") or 993)
IMAP_USER = os.getenv("IMAP_USER", "").strip()                 # empty = SMTP_USER
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "").strip()         # empty = SMTP_PASSWORD
EMAIL_DAILY_LIMIT = int(os.getenv("EMAIL_DAILY_LIMIT") or 30)
FOLLOWUP_DAYS = [int(x) for x in os.getenv("FOLLOWUP_DAYS", "3,7").split(",") if x.strip()]


if DEMO_MODE:
    # Fakes stand in for WhatsApp and email; nothing may leave the machine except an optional local Ollama call.
    WHATSAPP_PROVIDER, NTFY_TOPIC, CRM_WEBHOOK_URL, CRM_WEBHOOK_SECRET, API_KEY = "waha", "", "", "", ""
    SMTP_HOST, SMTP_FROM, IMAP_HOST = "demo.invalid", "Demo <demo@example.com>", ""
    BACKUP_DIR = str(ROOT / "demo_backups")
    DAILY_SEND_LIMIT = EMAIL_DAILY_LIMIT = 1000
    MIN_SECONDS_BETWEEN_SENDS = SEND_GAP_MIN = SEND_GAP_MAX = 2
    SEND_TYPING, SEND_START, SEND_END, SEND_SCHEDULE = False, "00:00", "23:59", ""
    MY_BUSINESS = os.getenv("MY_BUSINESS_DEMO", "We build modern websites and WhatsApp automation for small local businesses.")

ENV_PATH = ROOT / ".env"
SETTING_KEYS = ("BRAND_NAME", "BRAND_TAGLINE", "MY_BUSINESS", "OLLAMA_MODEL", "DAILY_SEND_LIMIT", "MIN_SECONDS_BETWEEN_SENDS", "SEND_GAP_MIN", "SEND_GAP_MAX", "FOLLOWUP_DAYS",
                "SEND_START", "SEND_END", "SEND_SCHEDULE", "OPTOUT_ENABLED", "OPTOUT_LINE",
                # first-run setup page (app/setup_page.py)
                "SETUP_DONE", "DASHBOARD_PASSWORD", "DEFAULT_COUNTRY_CODE", "WHATSAPP_PROVIDER", "WA_CLOUD_TOKEN", "WA_CLOUD_PHONE_NUMBER_ID",
                "WA_CLOUD_VERIFY_TOKEN", "WA_CLOUD_APP_SECRET", "WA_CLOUD_TEMPLATE", "GOOGLE_PLACES_API_KEY", "API_KEY", "CRM_WEBHOOK_URL",
                "CRM_WEBHOOK_SECRET",
                "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TLS", "IMAP_HOST", "IMAP_PORT", "IMAP_USER",
                "IMAP_PASSWORD", "EMAIL_DAILY_LIMIT")
SECRET_KEYS = ("SMTP_PASSWORD", "IMAP_PASSWORD", "DASHBOARD_PASSWORD", "WA_CLOUD_TOKEN", "WA_CLOUD_VERIFY_TOKEN", "WA_CLOUD_APP_SECRET", "GOOGLE_PLACES_API_KEY", "API_KEY", "CRM_WEBHOOK_SECRET")
SETUP_DONE = os.getenv("SETUP_DONE", "") == "1"


def needs_setup():
    """True on a fresh install: no SETUP_DONE flag and no business description saved yet (existing installs have one)."""
    return not DEMO_MODE and not SETUP_DONE and not os.environ.get("MY_BUSINESS", "").strip()


def mask(v):
    """Display form of a secret: never the whole value."""
    return "" if not v else ("••••" + v[-4:] if len(v) >= 12 else "••••")


def save_settings(values):
    """Write the given KEY=value pairs into .env (other lines, comments and secrets untouched) and apply them now."""
    values = {k: str(v).replace("\n", " ").replace("\r", " ").strip() for k, v in values.items() if k in SETTING_KEYS}
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    out, seen = [], set()
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if k in values:
            out.append(f"{k}={values[k]}")
            seen.add(k)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen]
    if not DEMO_MODE:                      # demo mode changes settings in memory only, never the real .env
        tmp = ENV_PATH.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n")
        if ENV_PATH.exists():
            tmp.chmod(ENV_PATH.stat().st_mode & 0o777)
        tmp.replace(ENV_PATH)
    g = globals()
    for k, v in values.items():
        os.environ[k] = v
        g[k] = ([int(x) for x in v.split(",") if x.strip()] if k == "FOLLOWUP_DAYS"
                else int(v) if k in ("DAILY_SEND_LIMIT", "MIN_SECONDS_BETWEEN_SENDS", "SEND_GAP_MIN", "SEND_GAP_MAX",
                                       "SMTP_PORT", "IMAP_PORT", "EMAIL_DAILY_LIMIT")
                else (v == "1") if k in ("OPTOUT_ENABLED", "SETUP_DONE") else v.lstrip("+") if k == "DEFAULT_COUNTRY_CODE"
                else v.lower() if k in ("WHATSAPP_PROVIDER", "SMTP_TLS") else v)
