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

DB_PATH = os.getenv("DB_PATH", str(ROOT / "leads.db"))
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
FOLLOWUP_DAYS = [int(x) for x in os.getenv("FOLLOWUP_DAYS", "3,7").split(",") if x.strip()]


ENV_PATH = ROOT / ".env"
SETTING_KEYS = ("BRAND_NAME", "BRAND_TAGLINE", "MY_BUSINESS", "OLLAMA_MODEL", "DAILY_SEND_LIMIT", "MIN_SECONDS_BETWEEN_SENDS", "FOLLOWUP_DAYS",
                "SEND_START", "SEND_END", "SEND_SCHEDULE", "OPTOUT_ENABLED", "OPTOUT_LINE")


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
    tmp = ENV_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n")
    if ENV_PATH.exists():
        tmp.chmod(ENV_PATH.stat().st_mode & 0o777)
    tmp.replace(ENV_PATH)
    g = globals()
    for k, v in values.items():
        os.environ[k] = v
        g[k] = ([int(x) for x in v.split(",") if x.strip()] if k == "FOLLOWUP_DAYS"
                else int(v) if k in ("DAILY_SEND_LIMIT", "MIN_SECONDS_BETWEEN_SENDS")
                else (v == "1") if k == "OPTOUT_ENABLED" else v)
