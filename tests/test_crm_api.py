"""CRM REST API (/api/v1) and outgoing webhooks."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, webhooks

KEY = "test-key-123"
H = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "NTFY_TOPIC", "")
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    monkeypatch.setattr(config, "DEFAULT_COUNTRY_CODE", "92")
    monkeypatch.setattr(config, "API_KEY", KEY)
    monkeypatch.setattr(config, "CRM_WEBHOOK_URL", "")
    monkeypatch.setattr(config, "CRM_WEBHOOK_SECRET", "")
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sent_events(monkeypatch):
    """Webhook on, deliveries captured inline instead of going over the network."""
    monkeypatch.setattr(config, "CRM_WEBHOOK_URL", "http://crm.invalid/hook")
    monkeypatch.setattr(config, "CRM_WEBHOOK_SECRET", "s3cret")
    out = []
    monkeypatch.setattr(webhooks, "_dispatch", lambda fn, url, secret, body, ev, eid: out.append((secret, body, ev)))
    return out


def test_auth(api, monkeypatch):
    assert api.get("/api/v1/leads").status_code == 401
    assert api.get("/api/v1/leads", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.get("/api/v1/leads", headers={"Authorization": KEY}).status_code == 401
    assert api.get("/api/v1/leads", headers=H).status_code == 200
    monkeypatch.setattr(config, "API_KEY", "")
    assert api.get("/api/v1/leads", headers=H).status_code == 404
    assert api.get("/api/v1/leads").status_code == 404


def test_api_exempt_from_dashboard_login(api, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "pw")
    assert api.get("/api/v1/leads", headers=H).status_code == 200
    assert api.get("/api/leads", follow_redirects=False).status_code == 303     # old API stays behind login
    assert api.get("/openapi.json", follow_redirects=False).status_code == 303
    assert api.get("/openapi.json", headers=H).status_code == 200
    assert "/api/v1/leads" in api.get("/openapi.json", headers=H).json()["paths"]


def test_upsert_and_external_id(api):
    r = api.post("/api/v1/leads", headers=H, json={"name": "Acme Dental", "phone": "0300-1111111", "city": "Lahore",
                                                  "external_id": "hs-1", "campaign": "spring"})
    assert r.status_code == 200 and r.json()["created"] is True
    lead = r.json()["lead"]
    assert lead["source"] == "api" and lead["external_id"] == "hs-1" and lead["stage"] == "new"
    # same phone -> merge; fills empty email, keeps the rest
    r = api.post("/api/v1/leads", headers=H, json={"name": "Other", "phone": "+92 300 1111111", "email": "a@b.com"})
    assert r.json()["created"] is False and r.json()["lead"]["id"] == lead["id"]
    assert r.json()["lead"]["email"] == "a@b.com" and r.json()["lead"]["name"] == "Acme Dental"
    # external_id alone matches, even with different contact data
    r = api.post("/api/v1/leads", headers=H, json={"name": "Totally Different", "external_id": "hs-1", "niche": "dentist"})
    assert r.json()["created"] is False and r.json()["lead"]["id"] == lead["id"]
    assert len(db.list_leads()) == 1


def test_list_get_patch(api, sent_events):
    a = api.post("/api/v1/leads", headers=H, json={"name": "A", "phone": "0300-2222222", "campaign": "x"}).json()["lead"]
    b = api.post("/api/v1/leads", headers=H, json={"name": "B", "phone": "0300-3333333"}).json()["lead"]
    sent_events.clear()
    r = api.patch(f"/api/v1/leads/{a['id']}", headers=H, json={"stage": "won", "notes": "closed", "external_id": "z9"})
    assert r.status_code == 200 and r.json()["stage"] == "won" and r.json()["notes"] == "closed"
    assert "lead.stage_changed" in [e[2] for e in sent_events]
    ev = next(json.loads(e[1]) for e in sent_events if e[2] == "lead.stage_changed")
    assert ev["data"]["previous_status"] == "new" and ev["data"]["status"] == "won"
    assert api.patch(f"/api/v1/leads/{a['id']}", headers=H, json={"stage": "bogus"}).status_code == 422
    assert api.patch("/api/v1/leads/999", headers=H, json={"notes": "x"}).status_code == 404

    page = api.get("/api/v1/leads", headers=H, params={"stage": "won"}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == a["id"]
    assert api.get("/api/v1/leads", headers=H, params={"campaign": "x"}).json()["total"] == 1
    assert api.get("/api/v1/leads", headers=H, params={"limit": 1, "offset": 1}).json()["items"][0]["id"] in (a["id"], b["id"])
    assert api.get("/api/v1/leads", headers=H, params={"updated_since": "2999-01-01T00:00:00Z"}).json()["total"] == 0
    assert api.get("/api/v1/leads", headers=H, params={"updated_since": "2000-01-01"}).json()["total"] == 2
    assert api.get("/api/v1/leads", headers=H, params={"updated_since": "junk"}).status_code == 422
    db.add_message(a["id"], "in", "hello", "inbound", "received")
    d = api.get(f"/api/v1/leads/{a['id']}", headers=H).json()
    assert "hello" in [m["body"] for m in d["messages"]]
    assert api.get("/api/v1/leads/999", headers=H).status_code == 404


def test_webhook_signature_and_events(api, sent_events):
    api.post("/api/v1/leads", headers=H, json={"name": "Sig Co", "phone": "0300-4444444"})
    secret, body, ev = sent_events[0]
    assert ev == "lead.created"
    env = json.loads(body)
    assert set(env) == {"event", "id", "timestamp", "data"} and env["data"]["name"] == "Sig Co"
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert webhooks.sign(secret, body) == expected


def test_opt_out_event(api, sent_events):
    lead = api.post("/api/v1/leads", headers=H, json={"name": "Q", "phone": "0300-5555555"}).json()["lead"]
    sent_events.clear()
    api.patch(f"/api/v1/leads/{lead['id']}", headers=H, json={"stage": "opted_out"})
    assert [e[2] for e in sent_events if e[2].startswith("lead.")] == ["lead.stage_changed", "lead.opted_out"]


def test_webhook_noop_when_unset(api, monkeypatch):
    calls = []
    monkeypatch.setattr(webhooks, "_dispatch", lambda *a: calls.append(a))
    api.post("/api/v1/leads", headers=H, json={"name": "Quiet", "phone": "0300-6666666"})
    assert calls == []


def test_deliver_retries_never_raise(monkeypatch):
    monkeypatch.setattr(webhooks, "RETRY_DELAYS", (0, 0))
    n = []
    def boom(*a, **k):
        n.append(1); raise RuntimeError("down")
    monkeypatch.setattr(webhooks.httpx, "post", boom)
    assert webhooks._deliver("http://x.invalid", "s", b"{}", "lead.created", "id") is False
    assert len(n) == 3
