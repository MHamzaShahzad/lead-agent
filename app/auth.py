"""Optional dashboard password. Set DASHBOARD_PASSWORD in .env to turn it on (empty = no login)."""
import hashlib
import hmac
import time

from . import config

COOKIE = "lead_agent_auth"
DAYS = 30


def _sig(exp):
    return hmac.new(config.DASHBOARD_PASSWORD.encode(), f"v1.{exp}".encode(), hashlib.sha256).hexdigest()


def make_token():
    exp = int(time.time()) + DAYS * 86400
    return f"{exp}.{_sig(exp)}"


def valid(token):
    try:
        exp, sig = (token or "").split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _sig(int(exp)))
    except ValueError:
        return False


def check_password(pw):
    return hmac.compare_digest(pw.encode(), config.DASHBOARD_PASSWORD.encode())
