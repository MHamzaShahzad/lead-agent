"""Optional push alert (ntfy.sh) when a lead writes to us. Off unless NTFY_TOPIC is set. Never includes message text."""
import httpx

from . import config


def reply_alert(lead_name):
    """Send 'New reply from <name>'. Returns True if sent. Never raises."""
    topic = config.NTFY_TOPIC
    if not topic:
        return False
    try:
        r = httpx.post(f"https://ntfy.sh/{topic}", content=f"New reply from {lead_name}".encode("utf-8"), timeout=5)
        return r.status_code < 300
    except Exception:
        return False
