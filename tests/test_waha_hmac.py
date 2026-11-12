import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import config, main

KEY = "s3cret-key"
BODY = json.dumps({"event": "message", "payload": {"from": "447700900101@c.us", "body": "hi", "fromMe": False}}).encode()


def sign(raw=BODY, key=KEY):
    return hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()


@pytest.fixture
def client(monkeypatch):
    got = []
    monkeypatch.setattr(main, "handle_inbound", lambda *a, **k: got.append(a))
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "")
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    c = TestClient(main.app)
    c.got = got
    return c


def post(c, headers=None, url="/webhook/waha"):
    return c.post(url, content=BODY, headers={"content-type": "application/json", **(headers or {})})


def test_valid_signature_accepted(client, monkeypatch):
    monkeypatch.setattr(config, "WAHA_WEBHOOK_HMAC_KEY", KEY)
    assert post(client, {"X-Webhook-Hmac": sign(), "X-Webhook-Hmac-Algorithm": "sha512"}).status_code == 200
    assert client.got


def test_invalid_signature_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "WAHA_WEBHOOK_HMAC_KEY", KEY)
    assert post(client, {"X-Webhook-Hmac": sign(key="other")}).status_code == 403
    assert post(client, {"X-Webhook-Hmac": sign(b"tampered")}).status_code == 403
    assert not client.got


def test_missing_signature_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "WAHA_WEBHOOK_HMAC_KEY", KEY)
    assert post(client).status_code == 403
    assert not client.got


def test_disabled_when_no_key(client, monkeypatch):
    monkeypatch.setattr(config, "WAHA_WEBHOOK_HMAC_KEY", "")
    assert post(client).status_code == 200
    assert client.got


def test_token_still_enforced_alongside_hmac(client, monkeypatch):
    monkeypatch.setattr(config, "WAHA_WEBHOOK_HMAC_KEY", KEY)
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "tok")
    good = {"X-Webhook-Hmac": sign()}
    assert post(client, good, "/webhook/waha?token=wrong").status_code == 403
    assert post(client, good, "/webhook/waha?token=tok").status_code == 200
    monkeypatch.setattr(config, "WAHA_WEBHOOK_HMAC_KEY", "")
    assert post(client, None, "/webhook/waha?token=tok").status_code == 200
