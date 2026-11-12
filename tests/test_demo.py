import importlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "demo.db"))
    monkeypatch.setattr(config, "QUEUE_WORKER", False)
    monkeypatch.setattr(config, "SMTP_HOST", "demo.invalid")
    monkeypatch.setattr(config, "SMTP_FROM", "Demo <demo@example.com>")
    monkeypatch.setattr(config, "SEND_START", "00:00")
    monkeypatch.setattr(config, "SEND_END", "23:59")
    monkeypatch.setattr(config, "SEND_SCHEDULE", "")
    monkeypatch.setattr(config, "SEND_TYPING", False)
    monkeypatch.setattr(config, "DAILY_SEND_LIMIT", 1000)
    monkeypatch.setattr(config, "EMAIL_DAILY_LIMIT", 1000)
    monkeypatch.setattr(config, "CRM_WEBHOOK_URL", "")
    from app import ai, demo as d

    def boom(*a, **k):
        raise AssertionError("demo mode made a real network call")

    def ollama_down(*a, **k):
        raise httpx.ConnectError("no ollama")

    monkeypatch.setattr(httpx, "post", ollama_down)     # Ollama absent: canned drafts
    monkeypatch.setattr(httpx, "get", ollama_down)
    monkeypatch.setattr("smtplib.SMTP", boom)
    monkeypatch.setattr("smtplib.SMTP_SSL", boom)
    monkeypatch.setattr("imaplib.IMAP4_SSL", boom)
    d.SENT.clear()
    db.init()
    d.seed()
    return d


def test_config_refuses_leads_db():
    code = ("import os; os.environ['DEMO_MODE']='1'; os.environ['DEMO_DB_PATH']='/x/leads.db'; import app.config")
    import subprocess
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode != 0 and "leads.db" in (r.stderr + r.stdout)


def test_seed_covers_stages_and_is_idempotent(demo):
    leads = db.list_leads()
    assert 14 <= len(leads) <= 18
    assert {"new", "contacted", "replied", "won", "lost", "opted_out"} <= {l["status"] for l in leads}
    assert db.messages(status="draft") and db.messages(status="received")
    assert any(m["tag"] for m in db.messages(status="received"))
    assert db.list_followups() and db.campaign_results()
    assert demo.seed() == 0 and len(db.list_leads()) == len(leads)


def test_sends_are_recorded_never_real(demo):
    from app import sendqueue
    wa = next(m for m in db.messages(status="draft") if m["channel"] == "whatsapp")
    em = next(m for m in db.messages(status="draft") if m["channel"] == "email" and m["kind"] == "outreach")
    sendqueue.enqueue(wa["id"], wa["body"])
    sendqueue.enqueue(em["id"], em["body"], em["subject"])
    sendqueue.drain()
    assert sorted(s["channel"] for s in demo.SENT) == ["email", "whatsapp"]
    assert db.get_message(wa["id"])["status"] == "sent" and db.get_message(em["id"])["status"] == "sent"


def test_simulate_reply_uses_reply_pipeline(demo):
    lead = next(l for l in db.list_leads() if l["name"] == "Harbour View Bakery")
    before = len(db.messages(lead_id=lead["id"]))
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = c.post("/demo/simulate-reply", data={"lead_id": lead["id"], "text": "How much does it cost?"}, follow_redirects=False)
    assert r.status_code == 303
    msgs = db.messages(lead_id=lead["id"])
    assert len(msgs) >= before + 2                          # the reply and the AI-drafted answer
    got = next(m for m in msgs if m["direction"] == "in" and m["body"] == "How much does it cost?")
    assert got["tag"] == "asking_price"
    assert db.get_lead(lead["id"])["status"] == "replied"
    assert any(m["kind"] == "reply" and m["status"] == "draft" for m in msgs)
    assert not [f for f in db.lead_followups(lead["id"]) if f["status"] == "pending"]   # follow-ups cancelled


def test_simulate_stop_opts_out_and_email_lead_replies_by_email(demo):
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    spark = next(l for l in db.list_leads() if l["name"] == "Spark Electricians")
    c.post("/demo/simulate-reply", data={"lead_id": spark["id"], "preset": "stop"})
    assert db.get_lead(spark["id"])["status"] == "opted_out"
    assert any(m["channel"] == "email" and m["direction"] == "in" for m in db.messages(lead_id=spark["id"]))


def test_banner_and_button_shown_in_demo_only(demo, monkeypatch):
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    lead = db.list_leads()[0]
    assert "Demo mode" in c.get("/").text
    assert "Simulate reply" in c.get(f"/leads/{lead['id']}").text
    monkeypatch.setattr(config, "DEMO_MODE", False)
    assert "Demo mode" not in c.get("/").text
    assert c.post("/demo/simulate-reply", data={"lead_id": lead["id"]}).status_code == 404
