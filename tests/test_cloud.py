"""WhatsApp Cloud API provider: all HTTP mocked, nothing is ever sent."""
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db, whatsapp, wa_cloud


@pytest.fixture()
def cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "b"))
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "pw")      # webhook must work without login
    monkeypatch.setattr(config, "DEFAULT_COUNTRY_CODE", "92")
    for k, v in dict(WHATSAPP_PROVIDER="cloud", WA_CLOUD_TOKEN="tok", WA_CLOUD_PHONE_NUMBER_ID="123",
                     WA_CLOUD_VERIFY_TOKEN="vt", WA_CLOUD_APP_SECRET="sec", WA_CLOUD_TEMPLATE="",
                     WA_CLOUD_TEMPLATE_LANG="en").items():
        monkeypatch.setattr(config, k, v)
    calls = []

    real_post = httpx.post

    def fake_post(url, **kw):
        if "graph.facebook.com" not in url:
            return real_post(url, **kw)              # Ollama stays pointed at a closed port
        calls.append((url, kw["json"]))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    db.init()
    return calls


def _lead_with_inbound(phone, sent_at=None):
    lid, _ = db.add_lead({"name": "A", "phone": phone, "source": "manual"})
    mid = db.add_message(lid, "in", "hi", "inbound", "received")
    if sent_at:
        db.update_message(mid, sent_at=sent_at)
    return lid


def test_send_text_in_window(cloud):
    _lead_with_inbound("+923001234567")
    whatsapp.send_text("+923001234567", "hello")
    url, body = cloud[0]
    assert url.endswith("/123/messages")
    assert body["type"] == "text" and body["to"] == "923001234567" and body["text"]["body"] == "hello"


def test_outside_window_without_template_blocked(cloud):
    db.add_lead({"name": "B", "phone": "+923009999999", "source": "manual"})
    with pytest.raises(whatsapp.SendBlocked, match="24-hour"):
        whatsapp.send_text("+923009999999", "hello")
    assert not cloud


def test_outside_window_uses_template(cloud, monkeypatch):
    monkeypatch.setattr(config, "WA_CLOUD_TEMPLATE", "intro")
    _lead_with_inbound("+923001234567", "2000-01-01 00:00:00")       # window long expired
    whatsapp.send_text("+923001234567", "line1\nline2")
    body = cloud[0][1]
    assert body["type"] == "template" and body["template"]["name"] == "intro"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "line1 line2"


def _sig(raw):
    return "sha256=" + hmac.new(b"sec", raw, hashlib.sha256).hexdigest()


def test_webhook_verify_and_signature(cloud):
    from app.main import app
    with TestClient(app) as c:
        ok = c.get("/webhook/cloud", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
        assert ok.status_code == 200 and ok.text == "42"
        assert c.get("/webhook/cloud", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"}).status_code == 403
        raw = b"{}"
        assert c.post("/webhook/cloud", content=raw, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403
        assert c.post("/webhook/cloud", content=raw).status_code == 403
        assert c.post("/webhook/cloud", content=raw, headers={"X-Hub-Signature-256": _sig(raw)}).status_code == 200


def test_incoming_message_creates_reply(cloud):
    from app.main import app
    payload = {"entry": [{"changes": [{"value": {
        "messages": [{"from": "923001112222", "type": "text", "text": {"body": "How much is it?"}}],
        "statuses": [{"id": "w1", "status": "delivered"}]}}]}]}
    raw = json.dumps(payload).encode()
    with TestClient(app) as c:
        r = c.post("/webhook/cloud", content=raw, headers={"X-Hub-Signature-256": _sig(raw)})
        assert r.status_code == 200
    lead = db.lead_by_phone("923001112222")
    assert lead
    msgs = db.messages(lead_id=lead["id"])
    assert any(m["direction"] == "in" and m["body"] == "How much is it?" and m["tag"] for m in msgs)
    assert any(m["status"] == "draft" for m in msgs)
