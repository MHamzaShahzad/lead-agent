"""Outgoing CRM webhooks. Set CRM_WEBHOOK_URL (and CRM_WEBHOOK_SECRET) in .env; empty URL = nothing is sent.

Events: lead.created, lead.updated, lead.stage_changed, message.sent, reply.received, lead.opted_out.
Delivery runs on a small background pool with a few retries; it never blocks or raises into the app,
and the secret is never logged.
"""
import hashlib
import hmac
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from . import config

log = logging.getLogger("lead_agent.webhooks")
EVENTS = ("lead.created", "lead.updated", "lead.stage_changed", "message.sent", "reply.received", "lead.opted_out")
RETRY_DELAYS = (2, 10, 60)          # seconds before retry 1, 2, 3 (so up to 4 attempts)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crm-webhook")


def enabled():
    return bool(config.CRM_WEBHOOK_URL)


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _dispatch(fn, *args):           # separate function so tests can run deliveries inline
    _pool.submit(fn, *args)


def _deliver(url, secret, body, event, event_id):
    headers = {"Content-Type": "application/json", "User-Agent": "LeadAgent-Webhook",
               "X-LeadAgent-Event": event, "X-LeadAgent-Delivery": event_id}
    if secret:
        headers["X-LeadAgent-Signature"] = sign(secret, body)
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            r = httpx.post(url, content=body, headers=headers, timeout=10)
            if r.status_code < 300:
                return True
            err = f"HTTP {r.status_code}"
        except Exception as e:      # never include the URL/secret: only the error class
            err = type(e).__name__
        if attempt < len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt])
        else:
            log.warning("CRM webhook %s gave up after %d attempts (%s)", event, attempt + 1, err)
    return False


def emit(event, data):
    """Queue one event. Safe to call anywhere: no-op when CRM_WEBHOOK_URL is unset, never raises."""
    try:
        if not config.CRM_WEBHOOK_URL:
            return
        event_id = str(uuid.uuid4())
        envelope = {"event": event, "id": event_id,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data}
        body = json.dumps(envelope, default=str, separators=(",", ":")).encode()
        _dispatch(_deliver, config.CRM_WEBHOOK_URL, config.CRM_WEBHOOK_SECRET, body, event, event_id)
    except Exception as e:
        log.warning("CRM webhook %s not queued (%s)", event, type(e).__name__)
