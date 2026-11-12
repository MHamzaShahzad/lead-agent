"""Email channel: SMTP and IMAP are mocked (smtplib/imaplib); nothing leaves the machine."""
import imaplib
import smtplib
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app import config, db, emailer, sendqueue, whatsapp


class FakeSMTP:
    sent, logins = [], []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def starttls(self):
        pass

    def login(self, u, p):
        FakeSMTP.logins.append((u, p))

    def send_message(self, m):
        FakeSMTP.sent.append(m)

    def noop(self):
        pass

    def quit(self):
        pass


class FakeIMAP:
    """One mailbox: list of (uid, raw bytes). Records \\Seen flags and records that nothing is deleted."""
    box, seen, calls = [], [], []

    def __init__(self, host, port=993):
        pass

    def login(self, u, p):
        pass

    def select(self, name):
        return "OK", [b"1"]

    def response(self, name):
        return name, [b"7"]

    def uid(self, cmd, *a):
        FakeIMAP.calls.append((cmd, a))
        if cmd == "search":
            crit = a[1]
            lo = 1 if crit == "ALL" else int(crit.split()[1].split(":")[0])
            return "OK", [b" ".join(str(u).encode() for u, _ in FakeIMAP.box if u >= lo)]
        if cmd == "fetch":
            raw = dict(FakeIMAP.box)[int(a[0])]
            return "OK", [(b"1 (UID 1 BODY[] {1}", raw), b")"]
        if cmd == "store":
            FakeIMAP.seen.append(int(a[0]))
        return "OK", [b""]

    def logout(self):
        pass


def raw_mail(frm, subject, body, msgid, in_reply_to=None, **hdr):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, "me@mine.test", subject, msgid
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    for k, v in hdr.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m.as_bytes()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    for k, v in dict(MIN_SECONDS_BETWEEN_SENDS=0, SEND_GAP_MIN=0, SEND_GAP_MAX=0, SEND_TYPING=False, QUEUE_WORKER=False,
                     BACKUP_DIR=str(tmp_path / "b"), SEND_START="00:00", SEND_END="23:59", SEND_SCHEDULE="", NTFY_TOPIC="",
                     DASHBOARD_PASSWORD="", WEBHOOK_TOKEN="", CRM_WEBHOOK_URL="", SMTP_HOST="smtp.test", SMTP_PORT=587, SMTP_USER="me@mine.test",
                     SMTP_PASSWORD="pw", SMTP_FROM="Me <me@mine.test>", SMTP_TLS="starttls", IMAP_HOST="imap.test", IMAP_USER="", IMAP_PASSWORD="",
                     EMAIL_DAILY_LIMIT=30).items():
        monkeypatch.setattr(config, k, v)
    FakeSMTP.sent, FakeSMTP.logins = [], []
    FakeIMAP.box, FakeIMAP.seen, FakeIMAP.calls = [], [], []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    wa = []
    monkeypatch.setattr(whatsapp, "send_text", lambda phone, text: wa.append(text))
    monkeypatch.setattr(whatsapp, "status", lambda: "WORKING")
    monkeypatch.setattr(whatsapp, "session_info", lambda: ("WORKING", "971500000000"))
    from app.main import app
    with TestClient(app) as c:
        c.wa = wa
        yield c


def email_lead(client, name="Mail Co", email="boss@mailco.test", phone=""):
    client.post("/leads", data={"name": name, "email": email, "phone": phone})
    lead = [l for l in db.list_leads() if l["name"] == name][0]
    return lead


def first_draft(lead_id):
    return [m for m in db.messages(lead_id=lead_id) if m["status"] == "draft"][0]


def test_email_lead_gets_email_draft_with_subject(client):
    lead = email_lead(client)
    client.post(f"/leads/{lead['id']}/draft")
    d = first_draft(lead["id"])
    assert d["channel"] == "email" and d["subject"] and d["body"]
    both = email_lead(client, "Both Co", "a@both.test", "0300 1112233")
    client.post(f"/leads/{both['id']}/draft", data={})
    assert first_draft(both["id"])["channel"] == "whatsapp"          # phone present: WhatsApp by default


def test_send_via_queue_with_unsubscribe(client):
    lead = email_lead(client)
    client.post(f"/leads/{lead['id']}/draft")
    d = first_draft(lead["id"])
    client.post(f"/messages/{d['id']}/send", data={"body": "Hello there", "subject": "  Quick   idea "})
    assert db.get_message(d["id"])["status"] == "queued"
    assert sendqueue.drain() == 1
    m = FakeSMTP.sent[0]
    assert m["To"] == "boss@mailco.test" and m["Subject"] == "Quick idea"
    assert "mailto:me@mine.test" in m["List-Unsubscribe"] and "unsubscribe" in m["List-Unsubscribe"]
    assert "unsubscribe" in m.get_content().lower() and m.get_content().startswith("Hello there")
    assert FakeSMTP.logins == [("me@mine.test", "pw")]
    row = db.get_message(d["id"])
    assert row["status"] == "sent" and row["msgid"] == m["Message-ID"] and client.wa == []
    assert db.get_lead(lead["id"])["status"] == "contacted"
    # follow-up uses email, is threaded and carries no second unsubscribe line
    db.add_message(lead["id"], "out", "Just checking in", "followup", "draft", "email", "Re: Quick idea")
    fu = first_draft(lead["id"])
    sendqueue.enqueue(fu["id"], "Just checking in")
    sendqueue.drain()
    f = FakeSMTP.sent[1]
    assert f["In-Reply-To"] == m["Message-ID"] and "unsubscribe" not in f.get_content().lower()


def test_email_daily_limit_is_separate_from_whatsapp(client, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_DAILY_LIMIT", 1)
    monkeypatch.setattr(config, "DAILY_SEND_LIMIT", 1)
    ids = []
    for n in range(2):
        lead = email_lead(client, f"E{n}", f"e{n}@x.test")
        mid = db.add_message(lead["id"], "out", "hi", "outreach", "draft", "email", "S")
        sendqueue.enqueue(mid, "hi")
        ids.append(mid)
    wl = db.get_lead(db.add_lead({"name": "WA Co", "phone": "+923001234567"})[0])
    wm = db.add_message(wl["id"], "out", "hi wa", "outreach", "draft")
    sendqueue.enqueue(wm, "hi wa")
    sendqueue.drain()
    assert len(FakeSMTP.sent) == 1 and len(client.wa) == 1                # each channel hit its own limit of 1
    assert db.get_message(ids[1])["status"] == "queued"
    assert sendqueue.tick() == "waiting"


def test_enqueue_blocked_without_email_or_setup(client, monkeypatch):
    lead = email_lead(client, "NoMail", "")
    mid = db.add_message(lead["id"], "out", "hi", "outreach", "draft", "email", "S")
    with pytest.raises(whatsapp.SendBlocked):
        sendqueue.enqueue(mid, "hi")
    lead2 = email_lead(client, "HasMail", "x@y.test")
    mid2 = db.add_message(lead2["id"], "out", "hi", "outreach", "draft", "email", "")
    with pytest.raises(whatsapp.SendBlocked, match="subject"):
        sendqueue.enqueue(mid2, "hi")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    with pytest.raises(whatsapp.SendBlocked, match="not set up"):
        sendqueue.enqueue(mid2, "hi", "S")


def test_disabled_when_unset(client, monkeypatch):
    for k in ("SMTP_HOST", "IMAP_HOST"):
        monkeypatch.setattr(config, k, "")
    assert not emailer.enabled() and not emailer.imap_enabled()
    lead = email_lead(client)
    r = client.post(f"/leads/{lead['id']}/draft")
    assert db.messages(lead_id=lead["id"]) == []                          # email-only lead, email off: no draft
    page = client.get("/").text
    assert "Email channel" in page and "Off." in page
    assert FakeSMTP.sent == [] and FakeIMAP.calls == []


def _sent_email(client, lead):
    mid = db.add_message(lead["id"], "out", "Hello", "outreach", "draft", "email", "Quick idea")
    sendqueue.enqueue(mid, "Hello")
    sendqueue.drain()
    return db.get_message(mid)


def test_imap_reply_matching_tag_followup_cancel_and_seen(client):
    lead = email_lead(client)
    sent = _sent_email(client, lead)
    assert db.kv_get("imap_last_uid") is None
    FakeIMAP.box = [(1, raw_mail("old@x.test", "old", "ignore me", "<old@x>"))]
    assert emailer.run_poll(lambda *a: None) == 0                         # first poll only records the mailbox end
    assert db.kv_get("imap_last_uid") == "1"
    assert db.lead_followups(lead["id"])                                  # a follow-up is planned until they reply
    FakeIMAP.box += [
        (2, raw_mail("Boss <boss@mailco.test>", "Re: Quick idea",
                     "Sounds interesting, what does it cost?\n\nOn Thu, Me wrote:\n> Hello", "<r1@mailco>", sent["msgid"])),
        (3, raw_mail("stranger@else.test", "Hi", "Buy our stuff", "<s@else>")),
        (4, raw_mail("boss@mailco.test", "Out of office", "I am away", "<ooo@mailco>", Auto_Submitted="auto-replied")),
    ]
    from app import main
    assert emailer.run_poll(main.handle_email_reply) == 1
    assert FakeIMAP.seen == [2] and not any(c[0] == "store" and "Deleted" in str(c) for c in FakeIMAP.calls)
    msgs = db.messages(lead_id=lead["id"])
    reply = [m for m in msgs if m["direction"] == "in"][0]
    assert reply["channel"] == "email" and reply["body"] == "Sounds interesting, what does it cost?" and reply["subject"] == "Re: Quick idea"
    assert reply["tag"] and reply["msgid"] == "<r1@mailco>"
    assert db.get_lead(lead["id"])["status"] == "replied" and db.lead_followups(lead["id"]) == []
    draft = [m for m in msgs if m["status"] == "draft"][0]               # AI reply draft goes back by email, threaded
    assert draft["kind"] == "reply" and draft["channel"] == "email" and draft["subject"] == "Re: Quick idea"
    assert emailer.run_poll(main.handle_email_reply) == 0                 # nothing processed twice
    assert db.kv_get("email_poll_error") == ""


def test_imap_matches_by_in_reply_to_from_other_address_and_unsubscribe(client):
    lead = email_lead(client)
    sent = _sent_email(client, lead)
    db.kv_set("imap_last_uid", 0)
    db.kv_set("imap_validity", "7")
    FakeIMAP.box = [(1, raw_mail("assistant@mailco.test", "Re: Quick idea", "Please remove me from your list", "<u@x>", sent["msgid"]))]
    from app import main
    assert emailer.run_poll(main.handle_email_reply) == 1
    assert db.get_lead(lead["id"])["status"] == "opted_out"
    assert [m for m in db.messages(lead_id=lead["id"]) if m["status"] == "draft" and m["kind"] == "reply"] == []
    mid = db.add_message(lead["id"], "out", "again", "outreach", "draft", "email", "S")
    with pytest.raises(whatsapp.SendBlocked):
        sendqueue.enqueue(mid, "again")


def test_poll_error_is_reported_not_raised(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", boom)
    assert emailer.run_poll(lambda *a: None) == 0
    assert "connection refused" in db.kv_get("email_poll_error")
    assert "Last inbox check failed" in client.get("/").text


def test_switch_channel_and_lead_channel_and_ui(client):
    lead = email_lead(client, "Both", "b@both.test", "0300 5556677")
    client.post(f"/leads/{lead['id']}/draft")
    d = first_draft(lead["id"])
    assert d["channel"] == "whatsapp"
    client.post(f"/messages/{d['id']}/channel", data={"channel": "email"})
    d = db.get_message(d["id"])
    assert d["channel"] == "email" and d["subject"]
    page = client.get(f"/leads/{lead['id']}").text
    assert "Email" in page and 'name="subject"' in page and "Send as email" in page
    assert 'class="tag chan' in client.get("/").text
    client.post(f"/leads/{lead['id']}/channel", data={"channel": "email"})
    assert db.get_lead(lead["id"])["channel"] == "email"
    assert db.pick_channel(db.get_lead(lead["id"])) == "email"


def test_old_database_gets_channel_columns(tmp_path, monkeypatch):
    import sqlite3
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript(db.SCHEMA + "INSERT INTO leads (name) VALUES ('x'); INSERT INTO messages (lead_id, direction, body, kind, status) VALUES (1,'out','hi','outreach','sent');")
    c.commit()
    c.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init()
    db.init()
    assert db.messages()[0]["channel"] == "whatsapp"


def test_setup_saves_email_settings_masks_password_and_test_sends_nothing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "supersecretpw99")
    page = client.get("/setup").text
    assert "supersecretpw99" not in page
    form = dict(my_business="We sell widgets", brand_name="Acme", provider="waha", send_start="09:00", send_end="18:00", daily_limit="10",
                gap_min="1", gap_max="2", smtp_host="smtp.new.test", smtp_port="465", smtp_tls="ssl", smtp_user="u@new.test",
                smtp_from="U <u@new.test>", imap_host="imap.new.test", email_limit="12", smtp_password="")
    r = client.post("/setup", data=form, follow_redirects=False)
    assert r.status_code == 303 and config.SMTP_HOST == "smtp.new.test" and config.SMTP_PORT == 465 and config.EMAIL_DAILY_LIMIT == 12
    assert config.SMTP_PASSWORD == "supersecretpw99"                      # blank keeps the saved secret
    r = client.post("/setup/test-email", data={"smtp_host": "smtp.test", "smtp_from": "me@mine.test", "smtp_user": "me@mine.test"})
    assert r.json()["ok"] and FakeSMTP.sent == [] and config.SMTP_HOST == "smtp.new.test"   # config restored after the test
    bad = client.post("/setup", data={**form, "smtp_from": "not-an-address"})
    assert "From address" in bad.text
