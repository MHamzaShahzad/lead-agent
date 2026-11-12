"""End-to-end test: import -> dedupe -> score/draft -> approve/send -> follow-up -> inbound reply.
WhatsApp is faked; Ollama is pointed at a closed port so the fallback path is exercised.
Run with real Ollama too:  REAL_OLLAMA=1 pytest -q
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config, db, whatsapp

CSV = """Business Name,Phone,Website,Category,City
Karachi Smiles Dental,0300-1234567,,dentist,Karachi
Karachi Smiles Dental Pvt Ltd,+92 300 1234567,,dentist,Karachi
Bean There Cafe,0321 7654321,https://www.beanthere.pk,cafe,Lahore
Bean There,,beanthere.pk/menu,cafe,Lahore
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    if not os.getenv("REAL_OLLAMA"):
        monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "MIN_SECONDS_BETWEEN_SENDS", 0)
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "SEND_START", "00:00")          # tests run at any time of day
    monkeypatch.setattr(config, "SEND_END", "23:59")
    monkeypatch.setattr(config, "SEND_SCHEDULE", "")
    monkeypatch.setattr(config, "NTFY_TOPIC", "")               # never push for real
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")      # never depend on the real .env password
    monkeypatch.setattr(config, "DEFAULT_COUNTRY_CODE", "92")   # test data uses local 03xx numbers
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "")
    sent = []
    monkeypatch.setattr(whatsapp, "send_text", lambda phone, text: sent.append((whatsapp.chat_id(phone), text)))
    monkeypatch.setattr(whatsapp, "status", lambda: "WORKING")
    monkeypatch.setattr(whatsapp, "session_info", lambda: ("WORKING", "971500000000"))
    from app.main import app
    with TestClient(app) as c:
        c.sent = sent
        yield c


def test_full_flow(client):
    r = client.post("/import", files={"file": ("leads.csv", CSV, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303
    leads = db.list_leads()
    assert len(leads) == 2, "duplicates by phone and domain should merge"
    assert all(l["score"] is not None for l in leads)

    drafts = db.messages(status="draft")
    assert len(drafts) == 2
    d = drafts[0]
    client.post(f"/messages/{d['id']}/send", data={"body": d["body"]})
    assert len(client.sent) == 1 and client.sent[0][0].startswith("92")
    assert not client.sent[0][1].startswith("[fallback]")
    lead = db.get_lead(d["lead_id"])
    assert lead["status"] == "contacted"
    assert len(db.list_followups()) == len(config.FOLLOWUP_DAYS)

    # time travel: make the first follow-up due
    with db.conn() as c:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE followups SET due_at=? WHERE step=1", (past,))
    client.post("/followups/run")
    assert [m for m in db.messages(status="draft") if m["kind"] == "followup"]

    # the lead replies on WhatsApp -> follow-ups cancelled, reply drafted
    client.post("/webhook/waha", json={"event": "message", "payload": {
        "from": "92" + lead["phone_key"][-10:] + "@c.us", "fromMe": False, "body": "Price kya hai?"}})
    assert db.get_lead(lead["id"])["status"] == "replied"
    assert db.list_followups() == []
    assert [m for m in db.messages(status="draft") if m["kind"] == "reply"]


def test_unknown_sender_becomes_lead_and_stop_opts_out(client):
    client.post("/webhook/waha", json={"event": "message", "payload": {
        "from": "923331112222@c.us", "fromMe": False, "body": "Hi, I run a gym in Lahore, need a website"}})
    lead = db.lead_by_phone("03331112222")
    assert lead and lead["source"] == "whatsapp" and lead["score"] is not None
    client.post("/webhook/waha", json={"event": "message", "payload": {
        "from": "923331112222@c.us", "fromMe": False, "body": "STOP"}})
    assert db.get_lead(lead["id"])["status"] == "opted_out"
    d = db.messages(status="draft", lead_id=lead["id"])[0]
    client.post(f"/messages/{d['id']}/send", data={"body": "hello"})
    assert client.sent == [], "opted-out leads must never be messaged"


def test_daily_limit(client, monkeypatch):
    monkeypatch.setattr(config, "DAILY_SEND_LIMIT", 1)
    for i in range(2):
        client.post("/leads", data={"name": f"Shop {i}", "phone": f"030012300{i}0", "city": "Multan"})
    for d in db.messages(status="draft"):
        client.post(f"/messages/{d['id']}/send", data={"body": d["body"]})
    assert len(client.sent) == 1


def test_followups_after_reply_when_lead_goes_quiet(client):
    msg = {"event": "message", "payload": {"from": "923000000017@c.us", "fromMe": False, "body": "Need a website"}}
    client.post("/webhook/waha", json=msg)
    d = db.messages(status="draft")[0]
    client.post(f"/messages/{d['id']}/send", data={"body": d["body"]})
    assert len(db.list_followups()) == len(config.FOLLOWUP_DAYS)
    with db.conn() as c:
        c.execute("UPDATE followups SET due_at='2000-01-01 00:00:00' WHERE step=1")
    client.post("/followups/run")
    assert [m for m in db.messages(status="draft") if m["kind"] == "followup"]
    client.post("/webhook/waha", json={**msg, "payload": {**msg["payload"], "body": "ok call me"}})
    assert db.list_followups() == [], "a new message from the lead cancels pending follow-ups"


def test_resolves_privacy_lid(monkeypatch):
    monkeypatch.setattr(whatsapp, "resolve_lid", lambda lid: "923000000017@c.us")
    assert whatsapp.parse_webhook({"event": "message", "payload": {"from": "238@lid", "body": "hi"}}) == ("923000000017", "hi")


def test_ignores_groups_and_own_messages():
    assert whatsapp.parse_webhook({"event": "message", "payload": {"from": "123@g.us", "body": "x"}}) is None
    assert whatsapp.parse_webhook({"event": "message", "payload": {"from": "9230@c.us", "fromMe": True, "body": "x"}}) is None


def test_dashboard_renders(client):
    client.post("/leads", data={"name": "<script>x</script>", "phone": "03001111111"})
    html = client.get("/").text
    assert "Lead Agent" in html and "<script>x" not in html


def test_relink_panel_and_already_connected(client):
    html = client.get("/").text
    assert "Link WhatsApp again" in html and "+971500000000" in html
    r = client.post("/whatsapp/relink", data={"phone": "971500000000"})   # follows redirect to dashboard
    assert "already connected" in r.text


def _lead_with_chat(client):
    client.post("/import", files={"file": ("l.csv", CSV, "text/csv")})
    lead = db.list_leads()[0]
    client.post(f"/messages/{db.messages(lead_id=lead['id'])[0]['id']}/send",
                data={"body": "Hello from Acme"})
    db.add_message(lead["id"], "in", "Interested, tell me more", "inbound", "received")
    db.add_message(lead["id"], "in", "Also what price?", "inbound", "received")
    return lead["id"]


def test_lead_back_link_follows_origin(client):
    lid = _lead_with_chat(client)
    dash = client.get("/").text
    assert f'href="/leads/{lid}?from=chats"' in dash and f'href="/leads/{lid}?from=leads"' in dash
    for frm, sec, label in (("chats", "replies", "Chats"), ("today", "approvals", "Today"),
                            ("leads", "leads", "Leads"), ("followups", "followups", "Follow-ups")):
        html = client.get(f"/leads/{lid}?from={frm}").text
        assert f'href="/#{sec}" data-from="{frm}" data-known="1"' in html and f"<span>{label}</span>" in html
    for q in ("", "?from=evil", "?from=%22%3E%3Cscript%3E"):        # unknown -> Leads, never echoed
        html = client.get(f"/leads/{lid}{q}").text
        assert 'href="/#leads" data-from="leads" data-known="0"' in html and "evil" not in html


def test_lead_detail_page_and_edit(client):
    lid = _lead_with_chat(client)
    html = client.get(f"/leads/{lid}").text
    assert "Hello from Acme" in html and "Interested, tell me more" in html and "bub in" in html
    assert "Planned follow-ups" in html and "Follow-up #1" in html
    r = client.post(f"/leads/{lid}", data={"name": "New Name", "phone": "0300 1112223", "website": "",
                    "niche": "clinic", "city": "Lahore", "notes": "call Monday"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/leads/{lid}"
    l = db.get_lead(lid)
    assert l["name"] == "New Name" and l["notes"] == "call Monday" and l["website"] is None
    assert l["phone_key"] == db.phone_key("0300 1112223")
    assert client.get("/leads/9999", follow_redirects=False).status_code == 303


def test_detail_actions_redirect_back_and_delete(client):
    lid = _lead_with_chat(client)
    back = f"/leads/{lid}"
    for path, data in ((f"/leads/{lid}/draft", {"next": back}), (f"/leads/{lid}/rescore", {"next": back}),
                       (f"/leads/{lid}/status", {"status": "won", "next": back}),
                       (f"/leads/{lid}/cancel-followups", {})):
        r = client.post(path, data=data, follow_redirects=False)
        assert r.headers["location"] == back, path
    assert db.get_lead(lid)["status"] == "won" and db.lead_followups(lid) == []
    assert client.post(f"/leads/{lid}/status", data={"status": "new", "next": "//evil.com"},
                       follow_redirects=False).headers["location"] == "/"
    r = client.post(f"/leads/{lid}/delete", follow_redirects=False)
    assert r.headers["location"] == "/"
    assert db.get_lead(lid) is None and db.messages(lead_id=lid) == []
    assert not [f for f in db.list_followups() if f["lead_id"] == lid]


def test_replies_grouped_by_lead_and_links(client):
    lid = _lead_with_chat(client)
    rows = db.replies_by_lead()
    assert len(rows) == 1 and rows[0]["n"] == 2 and rows[0]["body"] == "Also what price?"
    html = client.get("/").text
    assert f'href="/leads/{lid}?from=chats"' in html and "2 messages" in html


def test_phase2_regenerate_rewrite_warnings_failed(client, monkeypatch):
    from app import ai
    lid = _lead_with_chat(client)                        # first message sent, 2 inbound
    client.post("/import", files={"file": ("l.csv", CSV, "text/csv")})
    other = db.messages(status="draft")[0]               # a fallback draft
    html = client.get("/").text
    assert "The AI was offline" in html and "characters" in html and "Shorter" in html
    client.post(f"/messages/{other['id']}/regenerate")
    assert db.get_message(other["id"])["status"] == "draft" and db.get_message(other["id"])["body"]
    # rewrite uses the edited textarea text; stub the AI call
    monkeypatch.setattr(ai, "_chat", lambda s, u, **k: "Short version")
    client.post(f"/messages/{other['id']}/rewrite", data={"mode": "shorten", "body": "long long text"})
    assert db.get_message(other["id"])["body"] == "Short version"
    client.post(f"/messages/{other['id']}/rewrite", data={"mode": "bogus", "body": "x"})
    assert db.get_message(other["id"])["body"] == "Short version"
    db.update_message(other["id"], body="Hi [Name], hello")
    assert "placeholder like [Name]" in client.get("/").text
    # reply draft shows recent conversation
    mid = db.add_message(lid, "out", "Thanks! Let's talk", "reply", "draft")
    html = client.get("/").text
    assert "Recent conversation" in html and "Also what price?" in html
    # failed send shows error + Try again; retry goes through the fake sender
    db.update_message(mid, status="failed", error="WAHA down")
    html = client.get("/").text
    assert "Not sent: WAHA down" in html and "Try again" in html
    n = len(client.sent)
    client.post(f"/messages/{mid}/send", data={"body": "Thanks! Let's talk"})
    assert db.get_message(mid)["status"] == "sent" and len(client.sent) == n + 1


def test_phase3_bulk_export_and_working_banner(client):
    import csv, io
    client.post("/import", files={"file": ("l.csv", CSV, "text/csv")})
    a, b = [l["id"] for l in db.list_leads()]
    for m in db.messages(status="draft"):
        db.update_message(m["id"], status="rejected")
    # bulk write messages: only leads with a phone and no waiting draft
    client.post("/leads/bulk", data={"ids": [a, b], "action": "draft"})
    assert len(db.messages(status="draft")) == len([l for l in db.list_leads() if l["phone"]])
    n = len(db.messages(status="draft"))
    client.post("/leads/bulk", data={"ids": [a, b], "action": "draft"})
    assert len(db.messages(status="draft")) == n, "no duplicate drafts"
    # bulk stage
    client.post("/leads/bulk", data={"ids": [a, b], "action": "stage", "status": "lost"})
    assert {db.get_lead(a)["status"], db.get_lead(b)["status"]} == {"lost"}
    r = client.post("/leads/bulk", data={"action": "delete"}, follow_redirects=False)
    assert r.status_code == 303 and db.get_lead(a)
    # CSV export, formula-safe
    db.update_lead(a, notes="=HYPERLINK(\"x\")")
    r = client.get("/leads.csv")
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 2 and any(x["notes"].startswith("'=") for x in rows)
    # "AI working" banner for fresh unscored leads
    db.update_lead(a, score=None)
    assert "AI is working on 1 lead" in client.get("/").text
    db.update_lead(a, score=50)
    assert "AI is working" not in client.get("/").text
    # bulk delete removes messages too
    client.post("/leads/bulk", data={"ids": [a, b], "action": "delete"})
    assert db.list_leads() == [] and db.messages() == []


def test_phase4_overview_settings_and_local_times(client, monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keep me\nWAHA_API_KEY=secret123\nDAILY_SEND_LIMIT=25\nMY_BUSINESS=old\n")
    monkeypatch.setattr(config, "ENV_PATH", env)
    for k in config.SETTING_KEYS:                       # restore the in-memory values after the test
        monkeypatch.setattr(config, k, getattr(config, k))
    lid = _lead_with_chat(client)
    html = client.get("/").text
    assert "Waiting for approval" in html and "Replies this week" in html and "Leads by stage" in html
    assert 'id="settings"' in html and "<time datetime=" in html
    good = {"my_business": "We  sell\nthings", "ollama_model": "llama3.2:3b", "daily_limit": "10",
            "min_seconds": "5", "followup_days": "2, 5", "day0_on": "1", "day0_start": "09:00", "day0_end": "20:00"}
    r = client.post("/settings", data=good, follow_redirects=False)
    assert r.status_code == 303
    assert config.DAILY_SEND_LIMIT == 10 and config.MIN_SECONDS_BETWEEN_SENDS == 5
    assert config.FOLLOWUP_DAYS == [2, 5] and config.MY_BUSINESS == "We sell things"
    text = env.read_text()
    assert "# keep me" in text and "WAHA_API_KEY=secret123" in text and "DAILY_SEND_LIMIT=10" in text
    assert text.count("MY_BUSINESS=") == 1 and "FOLLOWUP_DAYS=2,5" in text
    assert "secret123" not in client.get("/").text
    for bad in ({"daily_limit": "abc"}, {"daily_limit": "0"}, {"followup_days": "7,3"},
                {"ollama_model": "two words"}, {"my_business": "  "}):
        client.post("/settings", data={**good, **bad})
        assert config.DAILY_SEND_LIMIT == 10 and config.FOLLOWUP_DAYS == [2, 5]
    assert db.replies_this_week() == 2
    assert "<time" in client.get(f"/leads/{lid}").text


def test_phase5_toasts_and_accessibility(client):
    r = client.post("/leads/bulk", data={"action": "delete"})      # follows redirect -> flash shown once
    assert 'class="toast err"' in r.text and 'role="alert"' in r.text and "Tick at least one lead" in r.text
    assert "Tick at least" not in client.get("/").text
    lid = _lead_with_chat(client)
    html = client.post(f"/leads/{lid}", data={"name": "X"}).text   # redirect lands on the detail page
    assert 'class="toast"' in html and 'role="status"' in html and "Skip to content" in html
    home = client.get("/").text
    assert 'aria-label="Sections"' in home and 'aria-label="Search leads"' in home and "j</kbd>" in home


def test_phase6_login(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret")
    r = client.get("/?x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert client.post("/leads", data={"name": "A"}, follow_redirects=False).headers["location"].startswith("/login")
    assert client.get("/api/leads", follow_redirects=False).status_code == 303
    # webhook stays reachable without a cookie
    assert client.post("/webhook/waha", json={"event": "other"}).status_code == 200
    assert client.post("/login", data={"password": "nope"}).status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303
    r = client.post("/login", data={"password": "s3cret", "next": "//evil.com"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/" and "lead_agent_auth" in r.headers["set-cookie"]
    assert "Sign out" in client.get("/").text
    client.cookies.set("lead_agent_auth", "123.abc")                   # forged cookie is rejected
    assert client.get("/", follow_redirects=False).status_code == 303
    client.get("/logout")
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")       # no password -> no login
    assert client.get("/", follow_redirects=False).status_code == 200


def test_phase6_optout_blocks_everything(client):
    lid = _lead_with_chat(client)
    db.update_lead(lid, status="opted_out")
    mid = db.add_message(lid, "out", "Hello again", "followup", "draft")
    n = len(client.sent)
    html = client.get("/").text
    assert "asked us to stop" in html
    client.post(f"/messages/{mid}/send", data={"body": "Hello again"})
    client.post("/messages/bulk-send", data={"ids": [mid]})
    assert len(client.sent) == n and db.get_message(mid)["status"] == "draft"
    client.post(f"/leads/{lid}/draft", data={})
    assert len([m for m in db.messages(lead_id=lid) if m["status"] == "draft"]) == 1
    assert "asked us to stop" in client.get(f"/leads/{lid}").text


def test_phase6_results_and_bulk_send(client):
    lid = _lead_with_chat(client)                     # sent 1, got 2 replies
    r = db.results()
    assert r["messaged"] == 1 and r["replied"] == 1 and r["rate"] == 100 and r["sent_week"] == 1
    assert "Reply rate" in client.get("/").text and "100%" in client.get("/").text
    for i in range(3):
        client.post("/leads", data={"name": f"Shop {i}", "phone": f"030055500{i}0", "city": "Multan"})
    drafts = [d for d in db.messages(status="draft")]
    assert len(drafts) >= 3
    db.update_message(drafts[0]["id"], body="Hi [Name], hello")    # has a placeholder -> skipped
    db.update_message(drafts[1]["id"], body="Clean message one")
    db.update_message(drafts[2]["id"], body="Clean message two")
    before = len(client.sent)
    ids = [d["id"] for d in drafts[:3]]
    r = client.post("/messages/bulk-send", data={"ids": ids})
    assert "Sending up to" in r.text
    assert len(client.sent) == before + 2
    assert db.get_message(drafts[0]["id"])["status"] == "draft"
    assert {db.get_message(i)["status"] for i in ids[1:]} == {"sent"}


def test_phase6_bulk_send_stops_at_daily_limit_and_keeps_drafts(client, monkeypatch):
    for i in range(3):
        client.post("/leads", data={"name": f"Cafe {i}", "phone": f"030066600{i}0", "city": "Multan"})
    drafts = db.messages(status="draft")
    for d in drafts:
        db.update_message(d["id"], body=f"Clean {d['id']}")
    monkeypatch.setattr(config, "DAILY_SEND_LIMIT", 1)
    client.post("/messages/bulk-send", data={"ids": [d["id"] for d in drafts]})
    assert len(client.sent) == 1
    assert len(db.messages(status="draft")) == len(drafts) - 1     # blocked ones stay drafts, not lost


def test_new_inbound_supersedes_old_reply_drafts(client):
    msg = {"event": "message", "payload": {"from": "971500000028@c.us", "fromMe": False, "body": "Hello"}}
    client.post("/webhook/waha", json=msg)
    client.post("/webhook/waha", json={**msg, "payload": {**msg["payload"], "body": "Price?"}})
    lead = db.lead_by_phone("971500000028")
    drafts = [m for m in db.messages(status="draft", lead_id=lead["id"]) if m["kind"] == "reply"]
    assert len(drafts) == 1, "only the latest reply draft should wait for approval"


def test_backup_keeps_14_and_shows_on_page(client):
    from pathlib import Path
    db.add_lead({"name": "Backup Co", "phone": "0300 1112223"})
    assert db.last_backup() is not None                    # the background loop already made one at startup
    first = Path(db.backup_now())
    import sqlite3
    assert sqlite3.connect(first).execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
    assert db.backup_if_due() is None                      # one per day
    d = Path(config.BACKUP_DIR)
    for i in range(20):
        (d / f"leads-2020010{i % 10}-0000{i:02d}.db").write_bytes(b"x")
    db.backup_now()
    assert len(list(d.glob("leads-*.db"))) == 14
    assert "Last automatic backup" in client.get("/").text


def test_reply_alert_off_by_default_and_never_leaks_or_fails(client, monkeypatch):
    from app import notify
    calls = []
    import types
    ok = lambda url, **kw: calls.append((url, kw)) or types.SimpleNamespace(status_code=200)
    monkeypatch.setattr(notify, "httpx", types.SimpleNamespace(post=ok))
    client.post("/leads", data={"name": "Ali Cafe", "phone": "0300 5550001"})
    from app.main import handle_inbound
    handle_inbound("923005550001", "secret text")
    assert calls == []                                      # off without NTFY_TOPIC
    monkeypatch.setattr(config, "NTFY_TOPIC", "test-topic")
    handle_inbound("923005550001", "secret text 2")
    assert len(calls) == 1 and calls[0][0].endswith("/test-topic")
    assert calls[0][1]["content"] == b"New reply from Ali Cafe"
    monkeypatch.setattr(notify, "httpx", types.SimpleNamespace(post=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))))
    handle_inbound("923005550001", "still works")           # must not raise
    assert len([m for m in db.messages(lead_id=1) if m["direction"] == "in"]) == 3


def test_reply_tags_fallback_and_display(client):
    from app import ai
    cases = {"How much does a website cost?": "asking_price", "Yes please call me": "interested",
             "Not now, maybe next month": "not_now", "no thanks, not interested": "not_interested",
             "Who is this?": "other"}
    for text, want in cases.items():
        tag, step = ai.classify_reply(text)
        assert tag == want and step, text
    client.post("/leads", data={"name": "Tag Co", "phone": "0300 7770001"})
    from app.main import handle_inbound
    handle_inbound("923007770001", "What is your price?")
    m = [x for x in db.messages(lead_id=1) if x["direction"] == "in"][0]
    assert m["tag"] == "asking_price" and m["next_step"]
    assert "Asking price" in client.get("/").text and "Asking price" in client.get("/leads/1").text
    handle_inbound("923007770001", "STOP")
    assert [x for x in db.messages(lead_id=1) if x["direction"] == "in"][0]["tag"] == "not_interested"


def test_init_adds_columns_to_old_database(tmp_path, monkeypatch):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, lead_id INTEGER, direction TEXT, body TEXT, kind TEXT, status TEXT, error TEXT, created_at TEXT, sent_at TEXT)")
    c.commit(); c.close()
    monkeypatch.setattr(config, "DB_PATH", str(p))
    db.init(); db.init()
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(messages)")}
    assert {"tag", "next_step"} <= cols


def test_sending_hours_block_send_and_bulk(client, monkeypatch):
    from datetime import datetime as dt
    client.post("/leads", data={"name": "Hours Co", "phone": "0300 8880001"})
    mid = db.messages(status="draft")[0]["id"]
    monkeypatch.setattr(config, "SEND_START", "09:00")
    monkeypatch.setattr(config, "SEND_END", "20:00")
    monkeypatch.setattr(whatsapp, "_local_now", lambda: dt(2026, 11, 12, 22, 30))
    r = client.post(f"/messages/{mid}/send", data={"body": "hi"})
    assert client.sent == [] and db.get_message(mid)["status"] == "draft"
    assert "Outside sending hours" in r.text
    r = client.post("/messages/bulk-send", data={"ids": [mid]})
    assert client.sent == [] and "outside sending hours" in r.text.lower()
    monkeypatch.setattr(whatsapp, "_local_now", lambda: dt(2026, 11, 12, 10, 0))
    client.post(f"/messages/{mid}/send", data={"body": "hi"})
    assert len(client.sent) == 1


def test_optout_line_first_message_only_and_settings(client, monkeypatch):
    client.post("/leads", data={"name": "Line Co", "phone": "0300 8880002"})
    mid = db.messages(status="draft")[0]["id"]
    client.post(f"/messages/{mid}/send", data={"body": "Hello there"})
    assert client.sent[-1][1] == "Hello there\n\nReply STOP to opt out"
    from app.main import handle_inbound
    handle_inbound("923008880002", "tell me more")
    rid = [m for m in db.messages(status="draft") if m["kind"] == "reply"][0]["id"]
    client.post(f"/messages/{rid}/send", data={"body": "Sure, a call?"})
    assert client.sent[-1][1] == "Sure, a call?"            # replies never get the line
    saved = {}
    monkeypatch.setattr(config, "save_settings", lambda v: saved.update(v))
    base = {"my_business": "We sell x", "ollama_model": "m", "daily_limit": "10", "min_seconds": "5", "followup_days": "3,7"}
    week = {f"day{i}_{k}": v for i in range(5) for k, v in (("on", "1"), ("start", "08:30"), ("end", "18:00"))}
    week.update({"day5_on": "1", "day5_start": "10:00", "day5_end": "14:00", "day6_start": "09:00", "day6_end": "20:00"})
    client.post("/settings", data={**base, **week, "optout_line": "Text STOP to leave"})
    assert saved["OPTOUT_ENABLED"] == "0"
    assert saved["SEND_SCHEDULE"] == ",".join(["08:30-18:00"] * 5 + ["10:00-14:00", "off"])
    saved.clear()
    r = client.post("/settings", data={**base, **week, "day2_start": "20:00", "day2_end": "09:00", "optout_on": "1", "optout_line": "x"})
    assert saved == {} and "Wed needs a start time" in r.text   # start after end is refused
    r = client.post("/settings", data={**base, "optout_line": "x"})
    assert saved == {} and "at least one sending day" in r.text


def test_week_windows_migrate_and_parse(monkeypatch):
    monkeypatch.setattr(config, "SEND_START", "08:00")
    monkeypatch.setattr(config, "SEND_END", "19:00")
    for legacy in ("", "garbage", "09:00-20:00,off", ",".join(["20:00-09:00"] * 7)):
        assert whatsapp.week_windows(legacy) == [("08:00", "19:00")] * 7    # old single window for every day
    sched = ",".join(["09:00-17:00"] * 5 + ["10:00-14:00", "OFF"])
    w = whatsapp.week_windows(sched)
    assert w[0] == ("09:00", "17:00") and w[5] == ("10:00", "14:00") and w[6] is None
    assert whatsapp.format_schedule(w) == sched.lower()
    assert whatsapp.schedule_summary(w) == "Mon-Fri 09:00-17:00, Sat 10:00-14:00, Sun off"


def test_hours_problem_per_day(monkeypatch):
    from datetime import datetime as dt
    monkeypatch.setattr(config, "SEND_SCHEDULE", ",".join(["09:00-17:00"] * 5 + ["10:00-14:00", "off"]))
    # 2026-11-13 is a Friday, 14th Saturday, 15th Sunday
    assert whatsapp.hours_problem(dt(2026, 11, 13, 9, 0)) is None
    assert whatsapp.hours_problem(dt(2026, 11, 13, 16, 59)) is None
    assert "Fri hours are 09:00-17:00" in whatsapp.hours_problem(dt(2026, 11, 13, 17, 0))
    assert "Next window: tomorrow at 10:00" in whatsapp.hours_problem(dt(2026, 11, 13, 17, 0))
    assert whatsapp.hours_problem(dt(2026, 11, 14, 11, 0)) is None
    assert "today at 10:00" in whatsapp.hours_problem(dt(2026, 11, 14, 8, 0))
    p = whatsapp.hours_problem(dt(2026, 11, 15, 12, 0))
    assert p.startswith("Outside sending hours (Sun is a day off)") and "tomorrow at 09:00" in p
    assert "Mon at 09:00" in whatsapp.hours_problem(dt(2026, 11, 14, 15, 0))
    monkeypatch.setattr(config, "SEND_SCHEDULE", ",".join(["off"] * 7))
    assert "Every day is off" in whatsapp.hours_problem(dt(2026, 11, 13, 10, 0))


def test_day_off_blocks_send(client, monkeypatch):
    from datetime import datetime as dt
    client.post("/leads", data={"name": "Sunday Co", "phone": "0300 8880009"})
    mid = db.messages(status="draft")[0]["id"]
    monkeypatch.setattr(config, "SEND_SCHEDULE", ",".join(["00:00-23:59"] * 6 + ["off"]))
    monkeypatch.setattr(whatsapp, "_local_now", lambda: dt(2026, 11, 15, 12, 0))
    r = client.post(f"/messages/{mid}/send", data={"body": "hi"})
    assert client.sent == [] and "Sun is a day off" in r.text
    r = client.post("/messages/bulk-send", data={"ids": [mid]})
    assert client.sent == [] and "day off" in r.text
    monkeypatch.setattr(whatsapp, "_local_now", lambda: dt(2026, 11, 16, 12, 0))
    client.post(f"/messages/{mid}/send", data={"body": "hi"})
    assert len(client.sent) == 1


def test_find_contacts_fills_only_empty_fields(client, monkeypatch):
    from app import finder
    html = '<a href="mailto:Info@shop.pk">x</a> <a href="tel:+92 300 1234567">call</a> <img src="logo@2x.png">'
    assert finder.extract_contacts(html) == ("info@shop.pk", "+92 300 1234567")
    pages = {}

    class R:
        def __init__(s, url, text, code=200): s.url, s.text, s.status_code = url, text, code
    def fake_get(url, **kw):
        pages[url] = kw
        if url.endswith("/contact"):
            return R("https://shop.pk/contact", html)
        return R("https://shop.pk/", "<p>hi</p>")
    monkeypatch.setattr(finder.httpx, "get", fake_get)
    lid, _ = db.add_lead({"name": "Shop", "website": "shop.pk", "email": "mine@x.com"})
    client.post(f"/leads/{lid}/find-contact")
    lead = db.get_lead(lid)
    assert lead["email"] == "mine@x.com" and lead["phone"] == "+92 300 1234567"   # email kept, phone filled
    assert all(kw["timeout"] == 8 for kw in pages.values()) and set(pages) == {"https://shop.pk", "https://shop.pk/contact"}
    # a redirect to another host is ignored
    monkeypatch.setattr(finder.httpx, "get", lambda url, **kw: R("https://evil.com/", html))
    assert finder.find_contacts_on_website("shop.pk") == (None, None)


def test_campaign_filter_export_and_rates(client):
    a, _ = db.add_lead({"name": "A Co", "phone": "0300 9990001"})
    b, _ = db.add_lead({"name": "B Co", "phone": "0300 9990002"})
    client.post(f"/leads/{a}", data={"name": "A Co", "campaign": "Dentists Nov"})
    client.post(f"/leads/{b}", data={"name": "B Co", "campaign": "Dentists Nov"})
    for lid in (a, b):
        db.add_message(lid, "out", "hi", "outreach", "sent")
    db.add_message(a, "in", "yes", "inbound", "received")
    res = db.campaign_results()
    assert res == [{"name": "Dentists Nov", "leads": 2, "messaged": 2, "replied": 1, "rate": 50}]
    page = client.get("/").text
    assert 'data-campaign="Dentists Nov"' in page and "Reply rate by campaign" in page
    csv_text = client.get("/leads.csv").text
    assert "campaign" in csv_text.splitlines()[0] and "Dentists Nov" in csv_text


def test_notes_and_reminders(client):
    from datetime import date, timedelta
    lid, _ = db.add_lead({"name": "Note Co", "phone": "0300 6660001"})
    client.post(f"/leads/{lid}/notes", data={"body": "Call after Eid", "remind_on": (date.today() - timedelta(days=1)).isoformat()})
    client.post(f"/leads/{lid}/notes", data={"body": "Likes blue", "remind_on": (date.today() + timedelta(days=5)).isoformat()})
    client.post(f"/leads/{lid}/notes", data={"body": "plain note"})
    client.post(f"/leads/{lid}/notes", data={"body": "bad", "remind_on": "tomorrow"})
    assert len(db.lead_notes(lid)) == 3 and len(db.lead_reminders(lid)) == 2
    due = db.due_reminders()
    assert [r["note"] for r in due] == ["Call after Eid"]
    page = client.get("/").text
    assert "Reminders" in page and "Call after Eid" in page
    assert "Call after Eid" in client.get(f"/leads/{lid}").text
    client.post(f"/reminders/{due[0]['id']}/done", data={"next": f"/leads/{lid}"})
    assert db.due_reminders() == []
    client.post(f"/leads/{lid}/delete")
    assert db.lead_notes(lid) == [] and db.lead_reminders(lid) == []


def test_replied_lead_page_can_write_and_send_reply(client):
    client.post("/leads", data={"name": "Reply Co", "phone": "0300 4440001"})
    lid = db.list_leads()[0]["id"]
    db.update_message(db.messages(status="draft")[0]["id"], status="rejected")
    from app.main import handle_inbound
    handle_inbound("923004440001", "tell me more")
    rid = db.messages(status="draft")[0]["id"]
    assert "Send on WhatsApp" in client.get(f"/leads/{lid}").text          # composer on the lead page
    db.update_message(rid, status="rejected")
    r = client.post(f"/leads/{lid}/draft", data={"next": f"/leads/{lid}"})  # used to say "reply in Chats"
    assert "Reply draft written" in r.text and db.messages(status="draft")[0]["kind"] == "reply"
    mid = db.messages(status="draft")[0]["id"]
    client.post(f"/messages/{mid}/send", data={"body": "Sure, a call?", "next": f"/leads/{lid}"})
    assert client.sent[-1][1] == "Sure, a call?"


def test_burst_of_inbound_messages_gives_one_reply_draft(client, monkeypatch):
    import threading, time as _t
    from app import ai, main
    client.post("/leads", data={"name": "Burst Co", "phone": "0300 1230001"})
    slow = lambda lead, hist: (_t.sleep(0.5), "reply")[1]
    monkeypatch.setattr(ai, "draft_reply", slow)
    ts = [threading.Thread(target=main.handle_inbound, args=("923001230001", f"msg {i}")) for i in range(3)]
    for t in ts:
        t.start(); _t.sleep(0.05)
    for t in ts:
        t.join()
    drafts = [m for m in db.messages(status="draft") if m["kind"] == "reply"]
    assert len(drafts) == 1
    assert len([m for m in db.messages(lead_id=drafts[0]["lead_id"]) if m["direction"] == "in"]) == 3


def test_osm_busy_is_an_error_not_zero_results(client, monkeypatch):
    import types
    from app import finder
    busy = types.SimpleNamespace(status_code=200, json=lambda: {"elements": [], "remark": "runtime error: Query timed out"})
    calls = []
    monkeypatch.setattr(finder.httpx, "post", lambda url, **kw: calls.append(url) or busy)
    r = client.post("/find", data={"niche": "dentist", "city": "Karachi"})
    assert "Search failed" in r.text and "busy" in r.text and len(calls) == 2      # tried the mirror too
    ok = types.SimpleNamespace(status_code=200, json=lambda: {"elements": [{"tags": {"name": "Smile Dental", "phone": "0300 1110000"}}]})
    seq = iter([types.SimpleNamespace(status_code=502, json=lambda: {}), ok])
    monkeypatch.setattr(finder.httpx, "post", lambda url, **kw: next(seq))
    assert finder.find_osm("dentist", "Karachi")[0]["name"] == "Smile Dental"


def test_local_number_needs_country_code(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_COUNTRY_CODE", "")
    with pytest.raises(whatsapp.SendBlocked):
        whatsapp.chat_id("0300 1234567")
    assert whatsapp.chat_id("+44 7700 900123") == "447700900123@c.us"
    monkeypatch.setattr(config, "DEFAULT_COUNTRY_CODE", "44")
    assert whatsapp.chat_id("07700 900123") == "447700900123@c.us"


def test_branding_from_settings(client, monkeypatch):
    monkeypatch.setattr(config, "ENV_PATH", config.Path(config.DB_PATH).with_name("env"))
    for k in config.SETTING_KEYS:                       # restore the in-memory values after the test
        monkeypatch.setattr(config, k, getattr(config, k))
    monkeypatch.setattr(config, "BRAND_NAME", "Lead Agent")
    monkeypatch.setattr(config, "BRAND_TAGLINE", "")
    monkeypatch.setattr(config, "MY_BUSINESS", "We sell cakes.")   # real .env may name a company
    html = client.get("/").text
    assert "<title>Lead Agent</title>" in html
    form = {"my_business": "We sell cakes.", "ollama_model": "llama3.2:3b", "daily_limit": "10", "min_seconds": "45",
            "followup_days": "3,7", "optout_on": "1", "optout_line": "Reply STOP to opt out",
            "brand_name": "Acme <b>Leads</b>", "brand_tagline": "Acme Ltd"}
    form.update({f"day{i}_on": "1" for i in range(7)})
    form.update({f"day{i}_start": "09:00" for i in range(7)})
    form.update({f"day{i}_end": "20:00" for i in range(7)})
    html = client.post("/settings", data=form).text
    assert "Settings saved" in html and "<title>Acme &lt;b&gt;Leads&lt;/b&gt;</title>" in html and "Acme Ltd" in html


def test_webhook_token(client, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "s3cret")
    msg = {"event": "message", "payload": {"from": "447700900999@c.us", "fromMe": False, "body": "hi"}}
    assert client.post("/webhook/waha", json=msg).status_code == 403
    assert client.post("/webhook/waha?token=wrong", json=msg).status_code == 403
    assert client.post("/webhook/waha?token=s3cret", json=msg).status_code == 200


# ---------------- Google Places source ----------------
def _place(n):
    return {"displayName": {"text": f"Biz {n}"}, "internationalPhoneNumber": f"+92 300 000{n:04d}",
            "websiteUri": f"https://biz{n}.pk", "formattedAddress": "Main St", "rating": 4.5, "types": ["dentist"]}


def test_google_mapping_and_pagination(monkeypatch):
    from app import finder
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "k")
    calls = []

    class R:
        status_code = 200
        def __init__(self, d): self.d = d
        def json(self): return self.d

    def fake_post(url, json, headers, timeout):
        calls.append(dict(json))
        assert headers["X-Goog-Api-Key"] == "k" and "places.websiteUri" in headers["X-Goog-FieldMask"]
        if "pageToken" not in json:
            return R({"places": [_place(i) for i in range(20)], "nextPageToken": "t"})
        return R({"places": [_place(i) for i in range(20, 25)]})

    monkeypatch.setattr(finder.httpx, "post", fake_post)
    leads = finder.find_google("dentist", "Lahore")
    assert len(leads) == 25 and len(calls) == 2 and calls[1]["pageToken"] == "t"
    assert leads[0]["source"] == "google_places" and leads[0]["website"] == "https://biz0.pk"
    assert leads[0]["phone"].startswith("+92") and "4.5" in leads[0]["notes"]


def test_google_errors_and_disabled(monkeypatch):
    from app import finder
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "")
    with pytest.raises(finder.PlacesError):
        finder.find_google("dentist", "Lahore")
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "secret-key")

    class R:
        status_code = 429
        def json(self): return {}

    monkeypatch.setattr(finder.httpx, "post", lambda *a, **k: R())
    with pytest.raises(finder.PlacesError, match="quota") as e:
        finder.find_google("dentist", "Lahore")
    assert "secret-key" not in str(e.value)


def test_google_option_in_ui(client, monkeypatch):
    assert 'value="google"' not in client.get("/").text
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "k")
    assert 'value="google"' in client.get("/").text
