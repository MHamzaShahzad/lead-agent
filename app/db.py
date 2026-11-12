"""SQLite lead database with automatic duplicate detection."""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import config, webhooks

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    website TEXT, niche TEXT, phone TEXT, email TEXT, city TEXT, notes TEXT,
    source TEXT DEFAULT 'manual',
    score INTEGER, score_reason TEXT,
    status TEXT DEFAULT 'new',        -- new, contacted, replied, won, lost, opted_out
    phone_key TEXT, domain_key TEXT, name_key TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_phone ON leads(phone_key);
CREATE INDEX IF NOT EXISTS ix_domain ON leads(domain_key);
CREATE INDEX IF NOT EXISTS ix_name ON leads(name_key);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    direction TEXT NOT NULL,          -- in / out
    body TEXT NOT NULL,
    kind TEXT DEFAULT 'outreach',     -- outreach, reply, followup, inbound
    status TEXT DEFAULT 'draft',      -- draft, queued, sending, sent, rejected, cancelled, failed, received
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS lead_notes (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    body TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    due_date TEXT NOT NULL,           -- YYYY-MM-DD, your local date
    note TEXT,
    done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    step INTEGER NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending'     -- pending, drafted, cancelled
);
"""


def backup_now():
    """Copy the database into BACKUP_DIR with SQLite's backup API, keep only the newest BACKUPS_KEEP. Returns the path."""
    import os
    from pathlib import Path
    d = Path(config.BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"leads-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(d.glob("leads-*.db"))[:-config.BACKUPS_KEEP]:
        old.unlink()
    return str(dest)


def last_backup():
    """Local datetime of the newest backup file, or None."""
    from pathlib import Path
    files = sorted(Path(config.BACKUP_DIR).glob("leads-*.db"))
    return datetime.fromtimestamp(files[-1].stat().st_mtime) if files else None


def backup_if_due():
    """At most one backup per 24 hours. Returns the new path or None."""
    if config.DEMO_MODE:
        return None
    last = last_backup()
    if last and (datetime.now() - last) < timedelta(hours=24):
        return None
    return backup_now()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        c.execute("CREATE TABLE IF NOT EXISTS phone_alias (key TEXT PRIMARY KEY, lead_id INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
        _add_columns(c, "messages", {"tag": "TEXT", "next_step": "TEXT", "scheduled_at": "TEXT",
                                      "channel": "TEXT DEFAULT 'whatsapp'", "subject": "TEXT", "msgid": "TEXT"})
        _add_columns(c, "leads", {"campaign": "TEXT", "external_id": "TEXT", "updated_at": "TEXT", "channel": "TEXT"})
        c.execute("CREATE INDEX IF NOT EXISTS ix_external ON leads(external_id)")
        c.execute("UPDATE leads SET updated_at=COALESCE(updated_at, created_at) WHERE updated_at IS NULL")


def _add_columns(c, table, cols):
    """Add columns to an existing table only when missing (safe to run on every start)."""
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, typ in cols.items():
        if name not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


# ---------- duplicate keys ----------
def phone_key(phone):
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    return digits[-10:] if len(digits) >= 7 else None   # last 10 digits ignore country-code style


def domain_key(website):
    if not website:
        return None
    w = website if "://" in website else "http://" + website
    host = (urlparse(w).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def name_key(name, city=""):
    n = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for junk in ("pvtltd", "ltd", "llc", "inc", "co"):
        if n.endswith(junk) and len(n) > len(junk) + 3:
            n = n[: -len(junk)]
    return f"{n}|{re.sub(r'[^a-z]', '', (city or '').lower())}" if n else None


def find_duplicate(c, lead):
    for col, key in (("external_id", lead.get("external_id")),
                     ("phone_key", phone_key(lead.get("phone"))),
                     ("domain_key", domain_key(lead.get("website"))),
                     ("name_key", name_key(lead.get("name"), lead.get("city")))):
        if key:
            row = c.execute(f"SELECT id FROM leads WHERE {col}=?", (key,)).fetchone()
            if row:
                return row["id"]
    return None


FIELDS = ("name", "website", "niche", "phone", "email", "city", "notes", "source", "external_id", "campaign")


def add_lead(lead):
    """Insert a lead. Returns (id, is_new). Duplicates fill in missing fields instead."""
    lead_id, is_new, changed = _add_lead(lead)
    if is_new or changed:
        webhooks.emit("lead.created" if is_new else "lead.updated", get_lead(lead_id))
    return lead_id, is_new


def _add_lead(lead):
    lead = {k: (str(lead.get(k) or "").strip() or None) for k in FIELDS}
    if not lead["name"]:
        lead["name"] = lead["website"] or lead["phone"] or "Unknown"
    with conn() as c:
        dup = find_duplicate(c, lead)
        if dup:
            existing = dict(c.execute("SELECT * FROM leads WHERE id=?", (dup,)).fetchone())
            fill = {k: v for k, v in lead.items() if v and not existing.get(k)}
            if fill:
                c.execute(f"UPDATE leads SET {', '.join(k + '=?' for k in fill)}, updated_at=? WHERE id=?",
                          (*fill.values(), now(), dup))
                _refresh_keys(c, dup)
            return dup, False, bool(fill)
        cur = c.execute(
            f"INSERT INTO leads ({', '.join(FIELDS)}, phone_key, domain_key, name_key, updated_at) "
            f"VALUES ({', '.join('?' * (len(FIELDS) + 4))})",
            (*[lead[k] for k in FIELDS], phone_key(lead["phone"]),
             domain_key(lead["website"]), name_key(lead["name"], lead["city"]), now()))
        return cur.lastrowid, True, True


def _refresh_keys(c, lead_id):
    r = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    c.execute("UPDATE leads SET phone_key=?, domain_key=?, name_key=? WHERE id=?",
              (phone_key(r["phone"]), domain_key(r["website"]), name_key(r["name"], r["city"]), lead_id))


def get_lead(lead_id):
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(r) if r else None


def lead_by_phone(phone):
    k = phone_key(phone)
    if not k:
        return None
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE phone_key=?", (k,)).fetchone()
        if not r:   # same person arriving under a second WhatsApp id
            r = c.execute("SELECT l.* FROM leads l JOIN phone_alias a ON a.lead_id=l.id WHERE a.key=?", (k,)).fetchone()
        return dict(r) if r else None


def update_lead(lead_id, **fields):
    old = get_lead(lead_id) if webhooks.enabled() else None
    with conn() as c:
        c.execute(f"UPDATE leads SET {', '.join(k + '=?' for k in fields)}, updated_at=? WHERE id=?",
                  (*fields.values(), now(), lead_id))
    if old is not None:
        _emit_changes(old, get_lead(lead_id))


def _emit_changes(old, new):
    if not old or not new:
        return
    if old["status"] != new["status"]:
        webhooks.emit("lead.stage_changed", {**new, "previous_status": old["status"]})
        if new["status"] == "opted_out":
            webhooks.emit("lead.opted_out", new)
    if any(old.get(k) != new.get(k) for k in new if k not in ("status", "updated_at")):
        webhooks.emit("lead.updated", new)


def list_leads():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM leads ORDER BY COALESCE(score,-1) DESC, id DESC")]


def add_message(lead_id, direction, body, kind, status, channel="whatsapp", subject=None, msgid=None):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO messages (lead_id, direction, body, kind, status, sent_at, channel, subject, msgid) VALUES (?,?,?,?,?,?,?,?,?)",
            (lead_id, direction, body, kind, status, now() if status == "received" else None, channel, subject, msgid))
        c.execute("UPDATE leads SET updated_at=? WHERE id=?", (now(), lead_id))
        return cur.lastrowid


def lead_by_email(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE lower(trim(email))=? ORDER BY id LIMIT 1", (email,)).fetchone()
        return dict(r) if r else None


def lead_by_msgid(msgids):
    """The lead an email Message-ID (one of ours, from In-Reply-To/References) belongs to."""
    ids = [i for i in msgids if i]
    if not ids:
        return None
    with conn() as c:
        r = c.execute(f"SELECT lead_id FROM messages WHERE msgid IN ({','.join('?' * len(ids))}) LIMIT 1", ids).fetchone()
    return get_lead(r["lead_id"]) if r else None


def has_msgid(msgid):
    with conn() as c:
        return bool(msgid) and c.execute("SELECT 1 FROM messages WHERE msgid=?", (msgid,)).fetchone() is not None


def channel_usable(lead, channel):
    from . import emailer
    if channel == "email":
        return emailer.enabled() and emailer.valid_address(lead.get("email"))
    return bool(lead.get("phone"))


def pick_channel(lead, kind="outreach"):
    """Channel for a new message to this lead, or None when neither works. Replies answer on the channel the lead wrote on;
    otherwise the lead's explicit choice, then the channel of our last message to them, then WhatsApp if there is a phone, else email."""
    msgs = messages(lead_id=lead["id"])                       # newest first
    pref = None
    if kind == "reply":
        pref = next((m.get("channel") for m in msgs if m["direction"] == "in"), None)
    pref = pref or lead.get("channel") or next((m.get("channel") for m in msgs if m["direction"] == "out" and m["status"] == "sent"), None)
    order = [pref] if pref in ("whatsapp", "email") else []
    order += [c for c in ("whatsapp", "email") if c not in order]
    return next((c for c in order if channel_usable(lead, c)), None)


def get_message(mid):
    with conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None


def update_message(mid, **fields):
    with conn() as c:
        c.execute(f"UPDATE messages SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                  (*fields.values(), mid))


def messages(status=None, lead_id=None):
    q, args = "SELECT m.*, l.name AS lead_name, l.phone, l.email FROM messages m JOIN leads l ON l.id=m.lead_id WHERE 1=1", []
    if status:
        q += " AND m.status=?"; args.append(status)
    if lead_id:
        q += " AND m.lead_id=?"; args.append(lead_id)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY m.id DESC", args)]


def sent_today(now=None, channel="whatsapp"):
    """Messages sent on the UTC day of `now` (default: today) over one channel (each channel has its own daily limit)."""
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM messages WHERE status='sent' AND date(sent_at)=? AND COALESCE(channel,'whatsapp')=?",
                         (day, channel)).fetchone()[0]


def kv_get(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def kv_set(key, value):
    with conn() as c:
        c.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


def queued():
    """Queued messages, oldest first (the order they are sent in)."""
    return sorted(messages(status="queued"), key=lambda m: m["id"])


def last_sent_at(channel="whatsapp"):
    with conn() as c:
        r = c.execute("SELECT MAX(sent_at) FROM messages WHERE status='sent' AND COALESCE(channel,'whatsapp')=?", (channel,)).fetchone()[0]
        return datetime.strptime(r, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if r else None


def schedule_followups(lead_id, days):
    base = datetime.now(timezone.utc)
    with conn() as c:
        if c.execute("SELECT 1 FROM followups WHERE lead_id=? AND status='pending'", (lead_id,)).fetchone():
            return
        for step, d in enumerate(days, 1):
            c.execute("INSERT INTO followups (lead_id, step, due_at) VALUES (?,?,?)",
                      (lead_id, step, (base + timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")))


def cancel_followups(lead_id):
    with conn() as c:
        c.execute("UPDATE followups SET status='cancelled' WHERE lead_id=? AND status='pending'", (lead_id,))


def due_followups():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT f.* FROM followups f JOIN leads l ON l.id=f.lead_id "
            "WHERE f.status='pending' AND f.due_at <= ? AND l.status IN ('contacted', 'replied') "
            "AND f.step = (SELECT MIN(step) FROM followups WHERE lead_id=f.lead_id AND status='pending')",
            (now(),))]


def list_followups():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT f.*, l.name AS lead_name FROM followups f JOIN leads l ON l.id=f.lead_id "
            "WHERE f.status='pending' ORDER BY f.due_at")]


def set_followup(fid, status):
    with conn() as c:
        c.execute("UPDATE followups SET status=? WHERE id=?", (status, fid))


# ---------- lead detail helpers ----------
EDITABLE = ("name", "phone", "website", "niche", "city", "notes", "email", "campaign", "external_id")


def edit_lead(lead_id, **fields):
    """Save edited contact fields (blank -> NULL, name kept if blank) and refresh duplicate keys."""
    vals = {k: (str(fields.get(k) or "").strip() or None) for k in EDITABLE if k in fields}
    if not vals.get("name", "x"):
        vals.pop("name")
    if not vals:
        return
    old = get_lead(lead_id) if webhooks.enabled() else None
    with conn() as c:
        c.execute(f"UPDATE leads SET {', '.join(k + '=?' for k in vals)}, updated_at=? WHERE id=?",
                  (*vals.values(), now(), lead_id))
        _refresh_keys(c, lead_id)
    if old is not None:
        _emit_changes(old, get_lead(lead_id))


def fill_empty_contacts(lead_id, email=None, phone=None):
    """Fill email/phone only when the lead has none. Returns the list of field names that were filled."""
    lead = get_lead(lead_id)
    fill = {}
    if email and not (lead["email"] or "").strip():
        fill["email"] = email
    if phone and not (lead["phone"] or "").strip():
        fill["phone"] = phone
    if fill:
        with conn() as c:
            c.execute(f"UPDATE leads SET {', '.join(k + '=?' for k in fill)}, updated_at=? WHERE id=?",
                      (*fill.values(), now(), lead_id))
            _refresh_keys(c, lead_id)
        webhooks.emit("lead.updated", get_lead(lead_id))
    return list(fill)


def add_note(lead_id, body, remind_on=None):
    """Save a dated note; with remind_on (YYYY-MM-DD) also create a reminder carrying the same text."""
    with conn() as c:
        c.execute("INSERT INTO lead_notes (lead_id, body) VALUES (?,?)", (lead_id, body))
        if remind_on:
            c.execute("INSERT INTO reminders (lead_id, due_date, note) VALUES (?,?,?)", (lead_id, remind_on, body))


def lead_notes(lead_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lead_notes WHERE lead_id=? ORDER BY id DESC", (lead_id,))]


def lead_reminders(lead_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM reminders WHERE lead_id=? AND done=0 ORDER BY due_date, id", (lead_id,))]


def due_reminders(today=None):
    """Open reminders due today or earlier (local date), with lead names."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT r.*, l.name AS lead_name FROM reminders r JOIN leads l ON l.id=r.lead_id "
            "WHERE r.done=0 AND r.due_date<=? ORDER BY r.due_date, r.id", (today,))]


def finish_reminder(rid):
    with conn() as c:
        c.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))


def campaign_results():
    """Per campaign: leads, leads messaged, leads that wrote back, reply rate (%)."""
    with conn() as c:
        rows = c.execute(
            "SELECT l.campaign AS name, COUNT(*) AS leads, "
            "SUM(EXISTS(SELECT 1 FROM messages m WHERE m.lead_id=l.id AND m.direction='out' AND m.status='sent')) AS messaged, "
            "SUM(EXISTS(SELECT 1 FROM messages m WHERE m.lead_id=l.id AND m.direction='out' AND m.status='sent') "
            "AND EXISTS(SELECT 1 FROM messages m WHERE m.lead_id=l.id AND m.status='received')) AS replied "
            "FROM leads l WHERE COALESCE(l.campaign,'')<>'' GROUP BY l.campaign ORDER BY leads DESC").fetchall()
    return [{**dict(r), "rate": round(100 * r["replied"] / r["messaged"]) if r["messaged"] else None} for r in rows]


def delete_lead(lead_id):
    with conn() as c:
        c.execute("DELETE FROM messages WHERE lead_id=?", (lead_id,))
        c.execute("DELETE FROM followups WHERE lead_id=?", (lead_id,))
        c.execute("DELETE FROM lead_notes WHERE lead_id=?", (lead_id,))
        c.execute("DELETE FROM reminders WHERE lead_id=?", (lead_id,))
        c.execute("DELETE FROM leads WHERE id=?", (lead_id,))


def conversation(lead_id):
    """All messages for a lead, oldest first."""
    return list(reversed(messages(lead_id=lead_id)))


def lead_followups(lead_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM followups WHERE lead_id=? AND status='pending' ORDER BY due_at", (lead_id,))]


def replies_by_lead():
    """One row per lead that wrote to us: latest inbound message + how many they sent."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT m.*, l.name AS lead_name, l.phone, l.email, "
            "(SELECT COUNT(*) FROM messages WHERE lead_id=m.lead_id AND status='received') AS n "
            "FROM messages m JOIN leads l ON l.id=m.lead_id "
            "WHERE m.status='received' AND m.id=(SELECT MAX(id) FROM messages "
            "WHERE lead_id=m.lead_id AND status='received') ORDER BY m.id DESC")]


def last_context(lead_id, before_id, n=3):
    """Last n real conversation messages (sent or received) before a draft, oldest first."""
    with conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE lead_id=? AND id<? AND status IN ('sent','received') "
                         "ORDER BY id DESC LIMIT ?", (lead_id, before_id, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def replies_this_week():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM messages WHERE status='received' "
                         "AND created_at >= datetime('now','-7 days')").fetchone()[0]


def results():
    """Reply rate = leads that wrote back / leads we have messaged."""
    with conn() as c:
        messaged = c.execute("SELECT COUNT(DISTINCT lead_id) FROM messages WHERE direction='out' AND status='sent'").fetchone()[0]
        replied = c.execute("SELECT COUNT(DISTINCT lead_id) FROM messages WHERE direction='out' AND status='sent' "
                            "AND lead_id IN (SELECT lead_id FROM messages WHERE status='received')").fetchone()[0]
        won = c.execute("SELECT COUNT(*) FROM leads WHERE status='won'").fetchone()[0]
        week = c.execute("SELECT COUNT(*) FROM messages WHERE status='sent' AND sent_at >= datetime('now','-7 days')").fetchone()[0]
    return {"messaged": messaged, "replied": replied, "won": won, "sent_week": week,
            "rate": round(100 * replied / messaged) if messaged else None}
