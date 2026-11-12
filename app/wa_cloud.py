"""Official Meta WhatsApp Cloud API provider (optional; WHATSAPP_PROVIDER=cloud)."""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db

GRAPH = "https://graph.facebook.com/v21.0"
WINDOW = timedelta(hours=24)


class CloudError(Exception):
    pass


def configured():
    return bool(config.WA_CLOUD_TOKEN and config.WA_CLOUD_PHONE_NUMBER_ID)


def _headers():
    return {"Authorization": f"Bearer {config.WA_CLOUD_TOKEN}"}


def to_number(phone, normalise):
    """Cloud API wants digits only (no @c.us). `normalise` is whatsapp.chat_id (country-code handling)."""
    return normalise(phone).split("@")[0]


def _post(payload):
    if not configured():
        raise CloudError("Cloud API is not configured: set WA_CLOUD_TOKEN and WA_CLOUD_PHONE_NUMBER_ID in .env.")
    r = httpx.post(f"{GRAPH}/{config.WA_CLOUD_PHONE_NUMBER_ID}/messages", headers=_headers(), timeout=30,
                   json={"messaging_product": "whatsapp", **payload})
    if r.status_code >= 300:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise CloudError(f"WhatsApp Cloud API error {r.status_code}: {msg}")
    return r.json()


def send_text(number, text):
    return _post({"to": number, "type": "text", "text": {"body": text, "preview_url": False}})


def send_template(number, name, language, params=()):
    tpl = {"name": name, "language": {"code": language}}
    if params:
        tpl["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
    return _post({"to": number, "type": "template", "template": tpl})


def in_service_window(phone, now=None):
    """True if the lead messaged us within the last 24 hours (free-form text allowed)."""
    lead = db.lead_by_phone(phone)
    if not lead:
        return False
    now = now or datetime.now(timezone.utc)
    for m in db.messages(lead_id=lead["id"]):          # newest first
        if m["direction"] == "in" and m["sent_at"]:
            t = datetime.strptime(m["sent_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return now - t < WINDOW
    return False


def window_note(phone):
    """Plain-English note for the UI, or '' if free text can be sent to this number."""
    if in_service_window(phone):
        return ""
    if config.WA_CLOUD_TEMPLATE:
        return (f"Outside WhatsApp's 24-hour window: this goes out as your approved template "
                f"'{config.WA_CLOUD_TEMPLATE}' with the draft text filled into {{{{1}}}}.")
    return ("Outside WhatsApp's 24-hour window: the Cloud API cannot send free text to a lead who has not "
            "messaged you in the last 24 hours. Set WA_CLOUD_TEMPLATE (an approved template) in .env.")


def send(phone, text, normalise):
    number = to_number(phone, normalise)
    if in_service_window(phone):
        return send_text(number, text)
    if not config.WA_CLOUD_TEMPLATE:
        raise CloudError(window_note(phone))
    # Template bodies cannot contain newlines/tabs inside a variable.
    flat = re.sub(r"\s*\n\s*", " ", text).strip()
    return send_template(number, config.WA_CLOUD_TEMPLATE, config.WA_CLOUD_TEMPLATE_LANG, [flat])


def info():
    """Returns (status, display_number or None) like the WAHA session_info."""
    if not configured():
        return "NOT_CONFIGURED", None
    try:
        r = httpx.get(f"{GRAPH}/{config.WA_CLOUD_PHONE_NUMBER_ID}", headers=_headers(), timeout=5,
                      params={"fields": "display_phone_number,verified_name"})
        if r.status_code in (400, 401, 403):
            return "WRONG_TOKEN", None
        if r.status_code >= 300:
            return "error", None
        return "WORKING", (r.json().get("display_phone_number") or "").replace("+", "").replace(" ", "") or None
    except Exception:
        return "offline", None


def verify_signature(raw, header):
    if not config.WA_CLOUD_APP_SECRET:
        return False
    want = "sha256=" + hmac.new(config.WA_CLOUD_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header or "")


def verify_challenge(mode, token, challenge):
    if (mode == "subscribe" and config.WA_CLOUD_VERIFY_TOKEN
            and hmac.compare_digest((token or "").encode(), config.WA_CLOUD_VERIFY_TOKEN.encode())):
        return challenge
    return None


def parse_webhook(payload):
    """Return (messages, statuses): messages = [(phone, text)], statuses = [(wamid, status, error_or_None)]."""
    msgs, stats = [], []
    for entry in payload.get("entry") or []:
        for ch in entry.get("changes") or []:
            v = ch.get("value") or {}
            for m in v.get("messages") or []:
                text = ""
                if m.get("type") == "text":
                    text = ((m.get("text") or {}).get("body") or "").strip()
                elif m.get("type") == "button":
                    text = ((m.get("button") or {}).get("text") or "").strip()
                elif m.get("type") == "interactive":
                    i = m.get("interactive") or {}
                    text = ((i.get("button_reply") or i.get("list_reply") or {}).get("title") or "").strip()
                if text and m.get("from"):
                    msgs.append((m["from"], text))
            for s in v.get("statuses") or []:
                err = ((s.get("errors") or [{}])[0]).get("title")
                stats.append((s.get("id"), s.get("status"), err))
    return msgs, stats
