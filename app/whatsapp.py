"""WhatsApp via WAHA (open-source WhatsApp HTTP API running in Docker)."""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db, wa_cloud


class SendBlocked(Exception):
    pass


def _headers():
    return {"X-Api-Key": config.WAHA_API_KEY} if config.WAHA_API_KEY else {}


def chat_id(phone):
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):          # local format like 0300... -> needs DEFAULT_COUNTRY_CODE
        cc = re.sub(r"\D", "", config.DEFAULT_COUNTRY_CODE or "")
        if not cc:
            raise SendBlocked(f"Add the country code to {phone!r} (or set DEFAULT_COUNTRY_CODE in .env).")
        digits = cc + digits[1:]
    if len(digits) < 10:
        raise SendBlocked(f"Phone number looks invalid: {phone!r}")
    return f"{digits}@c.us"


def is_cloud():
    return config.WHATSAPP_PROVIDER == "cloud"


def status():
    if config.DEMO_MODE:
        return "WORKING"
    if is_cloud():
        return wa_cloud.info()[0]
    try:
        r = httpx.get(f"{config.WAHA_URL}/api/sessions/{config.WAHA_SESSION}", headers=_headers(), timeout=3)
        return r.json().get("status", f"HTTP {r.status_code}") if r.status_code < 500 else "error"
    except Exception:
        return "offline"


def session_info():
    """Returns (status, linked_number or None)."""
    if config.DEMO_MODE:
        return "WORKING", "15550100000"
    if is_cloud():
        return wa_cloud.info()
    try:
        r = httpx.get(f"{config.WAHA_URL}/api/sessions/{config.WAHA_SESSION}", headers=_headers(), timeout=3)
        if r.status_code == 404:
            return "NOT_CREATED", None
        if r.status_code == 401:
            return "WRONG_API_KEY", None
        d = r.json()
        return d.get("status", "error"), ((d.get("me") or {}).get("id") or "").split("@")[0] or None
    except Exception:
        return "offline", None


def request_pairing_code(phone):
    if is_cloud():
        raise SendBlocked("Not needed with the official Cloud API: there is nothing to link.")
    """Get WhatsApp into 'waiting for login' state, then ask for an 8-character pairing code."""
    import time
    number = chat_id(phone).split("@")[0]
    base, h = f"{config.WAHA_URL}/api", _headers()
    status, _ = session_info()
    if status in ("offline", "WRONG_API_KEY"):
        raise SendBlocked(f"WAHA is {status}. Is Docker/Colima running and WAHA_API_KEY right?")
    if status == "WORKING":
        raise SendBlocked("WhatsApp is already connected.")
    if status == "NOT_CREATED":
        httpx.post(f"{base}/sessions", headers=h, timeout=15, json={"name": config.WAHA_SESSION, "start": True})
    elif status != "SCAN_QR_CODE":           # FAILED / STOPPED / STARTING
        httpx.post(f"{base}/sessions/{config.WAHA_SESSION}/restart", headers=h, timeout=15)
    for _ in range(15):
        if session_info()[0] == "SCAN_QR_CODE":
            break
        time.sleep(2)
    r = httpx.post(f"{base}/{config.WAHA_SESSION}/auth/request-code", headers=h, timeout=20,
                   json={"phoneNumber": number})
    if r.status_code >= 300:
        raise SendBlocked(f"Could not get code: {r.text[:200]}")
    return r.json().get("code")


def _local_now():
    return datetime.now()


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def valid_window(start, end):
    return bool(_HHMM.fullmatch(start or "") and _HHMM.fullmatch(end or "") and start < end)


def week_windows(schedule=None):
    """7 entries Mon..Sun: (start, end) or None for a day off. Falls back to SEND_START-SEND_END for every
    day when the schedule is empty or malformed (this is also how the old single window migrates)."""
    schedule = config.SEND_SCHEDULE if schedule is None else schedule
    default = [(config.SEND_START, config.SEND_END)] * 7
    parts = [p.strip().lower() for p in (schedule or "").split(",")]
    if len(parts) != 7:
        return default
    out = []
    for p in parts:
        if p == "off":
            out.append(None)
            continue
        a, _, b = p.partition("-")
        if not valid_window(a, b):
            return default
        out.append((a, b))
    return out


def format_schedule(windows):
    return ",".join("off" if w is None else f"{w[0]}-{w[1]}" for w in windows)


def schedule_summary(windows=None):
    """Short text like 'Mon-Fri 09:00-20:00, Sat 10:00-14:00, Sun off'."""
    windows = week_windows() if windows is None else windows
    groups = []
    for i, w in enumerate(windows):
        if groups and groups[-1][2] == w:
            groups[-1][1] = i
        else:
            groups.append([i, i, w])
    return ", ".join((DAYS[a] if a == b else f"{DAYS[a]}-{DAYS[b]}") + " " + ("off" if w is None else f"{w[0]}-{w[1]}")
                     for a, b, w in groups)


def _next_open(now, windows):
    hhmm = now.strftime("%H:%M")
    for k in range(8):
        d = (now.weekday() + k) % 7
        w = windows[d]
        if w and (k > 0 or hhmm < w[0]):
            return ("today" if k == 0 else "tomorrow" if k == 1 else DAYS[d]) + f" at {w[0]}"
    return None


def next_open_dt(now):
    """Local datetime when the next sending window starts after `now` (None if every day is off)."""
    windows = week_windows()
    for k in range(8):
        d = (now.weekday() + k) % 7
        if windows[d]:
            h, m = map(int, windows[d][0].split(":"))
            start = (now + timedelta(days=k)).replace(hour=h, minute=m, second=0, microsecond=0)
            if start > now:
                return start
    return None


def hours_problem(now=None):
    """None if sending is allowed right now (local time, that weekday's window), else a plain-English reason."""
    now = now or _local_now()
    windows = week_windows()
    w = windows[now.weekday()]
    hhmm = now.strftime("%H:%M")
    if w and w[0] <= hhmm < w[1]:
        return None
    day = DAYS[now.weekday()]
    why = f"{day} is a day off" if w is None else f"{day} hours are {w[0]}-{w[1]}"
    nxt = _next_open(now, windows)
    return (f"Outside sending hours ({why}). The draft waits. "
            + (f"Next window: {nxt}." if nxt else "Every day is off: turn a day on in Settings."))


def check_limits(lead):
    """Guardrails so the number does not get banned."""
    if lead["status"] == "opted_out":
        raise SendBlocked("Lead opted out (said STOP).")
    problem = hours_problem()
    if problem:
        raise SendBlocked(problem)
    if db.sent_today() >= config.DAILY_SEND_LIMIT:
        raise SendBlocked(f"Daily limit of {config.DAILY_SEND_LIMIT} messages reached. Try tomorrow.")
    last = db.last_sent_at()
    if last:
        wait = config.MIN_SECONDS_BETWEEN_SENDS - (datetime.now(timezone.utc) - last).total_seconds()
        if wait > 0:
            raise SendBlocked(f"Slow down: wait {int(wait)}s between messages.")


def window_note(phone):
    """Cloud only: note when a draft to this number cannot go out as free text ('' otherwise)."""
    return wa_cloud.window_note(phone) if is_cloud() else ""


def typing(phone, on=True):
    """Show/hide the 'typing...' indicator (WAHA only). Best effort: never raises."""
    if is_cloud() or config.DEMO_MODE:
        return
    try:
        httpx.post(f"{config.WAHA_URL}/api/{'startTyping' if on else 'stopTyping'}", headers=_headers(), timeout=5,
                   json={"session": config.WAHA_SESSION, "chatId": chat_id(phone)})
    except Exception:
        pass


def send_text(phone, text):
    """Send a message through the configured provider. On Cloud, a lead outside the 24h window gets the
    approved first-contact template (text filled into {{1}})."""
    if config.DEMO_MODE:
        from . import demo
        return demo.fake_whatsapp(phone, text)
    if is_cloud():
        try:
            return wa_cloud.send(phone, text, chat_id)
        except wa_cloud.CloudError as e:
            raise SendBlocked(str(e)) from e
    r = httpx.post(f"{config.WAHA_URL}/api/sendText", headers=_headers(), timeout=30,
                   json={"session": config.WAHA_SESSION, "chatId": chat_id(phone), "text": text})
    if r.status_code >= 300:
        raise SendBlocked(f"WAHA error {r.status_code}: {r.text[:200]}")
    return r.json()


def verify_hmac(raw, header):
    """Check WAHA's X-Webhook-Hmac header (hex HMAC-SHA512 of the raw body). True when no key is configured."""
    key = config.WAHA_WEBHOOK_HMAC_KEY
    if not key:
        return True
    if not header:
        return False
    want = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(want.encode(), header.strip().lower().encode())


def parse_webhook(payload):
    """Return (phone, text) for an incoming user message, or None to ignore."""
    if payload.get("event") not in ("message", "message.any"):
        return None
    p = payload.get("payload") or {}
    sender = p.get("from", "")
    if p.get("fromMe") or not (p.get("body") or "").strip():
        return None                      # skip our own and media-only messages
    if sender.endswith("@lid"):          # WhatsApp privacy ID -> real phone number
        sender = resolve_lid(sender) or ""
    if not sender.endswith("@c.us"):
        return None                      # groups, status, channels, unresolvable IDs
    return sender.split("@")[0], p["body"].strip()


def resolve_lid(lid):
    try:
        r = httpx.get(f"{config.WAHA_URL}/api/{config.WAHA_SESSION}/lids/{lid}", headers=_headers(), timeout=5)
        return (r.json() or {}).get("pn") if r.status_code == 200 else None
    except Exception:
        return None
