"""Sending queue. Approving a draft only queues it; one background worker sends queued messages one at a time,
inside the sending hours, under the daily limit, with a random gap. All state is in SQLite, so it survives restarts."""
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, db, emailer, webhooks, whatsapp

LOCK = threading.Lock()          # one tick at a time


def clock():
    return datetime.now(timezone.utc)


def _offset(now):
    """Local time minus UTC, in whole minutes (derived from the two clocks so both can be mocked)."""
    d = whatsapp._local_now() - now.replace(tzinfo=None)
    return timedelta(minutes=round(d.total_seconds() / 60))


def to_local(t, now=None):
    now = now or clock()
    return t.replace(tzinfo=None) + _offset(now)


def channel_of(m):
    return m.get("channel") or "whatsapp"


def limit_for(channel):
    return config.EMAIL_DAILY_LIMIT if channel == "email" else config.DAILY_SEND_LIMIT


def _gap_key(channel):
    return "gap_next_email" if channel == "email" else "gap_next"


def pick_gap():
    lo = config.SEND_GAP_MIN
    return random.randint(lo, max(lo, config.SEND_GAP_MAX))


def paused():
    return db.kv_get("queue_paused") == "1"


def set_paused(flag):
    db.kv_set("queue_paused", "1" if flag else "0")


def _fmt(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if s else None


def estimate(n, now=None, channel="whatsapp"):
    """UTC times when the next n queued messages of one channel should go out (hours, daily limit and average gap considered)."""
    now = now or clock()
    off = _offset(now)
    gap = timedelta(seconds=(config.SEND_GAP_MIN + config.SEND_GAP_MAX) / 2)
    lim = limit_for(channel)
    t, last = now, db.last_sent_at(channel)
    if last:
        t = max(t, last + timedelta(seconds=int(db.kv_get(_gap_key(channel), 0) or 0)))
    day, count = t.strftime("%Y-%m-%d"), db.sent_today(t, channel)
    out = []
    for _ in range(n):
        for _ in range(20):
            if t.strftime("%Y-%m-%d") != day:
                day, count = t.strftime("%Y-%m-%d"), db.sent_today(t, channel)
            if count >= lim:
                t = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
                continue
            loc = t.replace(tzinfo=None) + off
            if whatsapp.hours_problem(loc):
                nxt = whatsapp.next_open_dt(loc)
                if not nxt:
                    break
                t = (nxt - off).replace(tzinfo=timezone.utc)
                continue
            break
        out.append(t)
        t += gap
        count += 1
    return out


def reschedule(now=None):
    q = db.queued()
    for ch in ("whatsapp", "email"):
        mine = [m for m in q if channel_of(m) == ch]
        for m, t in zip(mine, estimate(len(mine), now, ch)):
            db.update_message(m["id"], scheduled_at=_fmt(t))


def enqueue(mid, body, subject=None):
    """Put a draft (or failed message) in the queue. Raises SendBlocked when it can't be queued."""
    m = db.get_message(mid)
    if not m or m["status"] not in ("draft", "failed"):
        raise whatsapp.SendBlocked("That message is no longer waiting.")
    lead = db.get_lead(m["lead_id"])
    body = re.sub(r"^\[fallback\]\s*", "", body.strip())
    if not body:
        raise whatsapp.SendBlocked("Message is empty.")
    if lead["status"] == "opted_out":
        raise whatsapp.SendBlocked("Lead opted out (said STOP).")
    if channel_of(m) == "email":
        subject = " ".join((subject if subject is not None else m.get("subject") or "").split())
        if not emailer.enabled():
            raise whatsapp.SendBlocked("Email is not set up. Add the SMTP settings in Setup.")
        if not emailer.valid_address(lead.get("email")):
            raise whatsapp.SendBlocked("This lead has no valid email address.")
        if not subject:
            raise whatsapp.SendBlocked("Add a subject line.")
        db.update_message(mid, subject=subject)
    db.update_message(mid, body=body, status="queued", error=None, scheduled_at=_fmt(clock()))
    reschedule()
    return parse(db.get_message(mid)["scheduled_at"])


def cancel(mid):
    m = db.get_message(mid)
    if not m or m["status"] != "queued":
        return False
    db.update_message(mid, status="draft", scheduled_at=None)
    reschedule()
    return True


def _with_optout(lead, m, body):
    """Add the opt-out line to the very first message we send a lead (never to replies or follow-ups)."""
    if channel_of(m) == "email":          # always on first emails (unsubscribe is a legal must in many countries)
        first = not any(x["direction"] == "out" and x["status"] == "sent" and channel_of(x) == "email" for x in db.messages(lead_id=lead["id"]))
        return f"{body}\n\n{emailer.UNSUB_LINE}" if m["kind"] == "outreach" and first and emailer.UNSUB_LINE not in body else body
    line = config.OPTOUT_LINE.strip()
    if (config.OPTOUT_ENABLED and line and m["kind"] == "outreach" and line.lower() not in body.lower()
            and not any(x["direction"] == "out" and x["status"] == "sent" for x in db.messages(lead_id=lead["id"]))):
        return f"{body}\n\n{line}"
    return body


def _skip_reason(m, lead):
    if not lead or lead["status"] == "opted_out":
        return "Skipped: lead opted out (said STOP)."
    if any(x["direction"] == "in" and x["id"] > m["id"] for x in db.messages(lead_id=lead["id"])):
        return "Skipped: the lead replied while this was queued."
    return None


def _thread_refs(lead, m):
    """In-Reply-To / References for an email that continues a conversation (latest message with an id before this one)."""
    prev = [x for x in reversed(db.messages(lead_id=lead["id"])) if x.get("msgid") and x["id"] < m["id"]]
    return (prev[-1]["msgid"], " ".join(x["msgid"] for x in prev[-5:])) if prev else (None, None)


def deliver(m, lead):
    mid = m["id"]
    ch = channel_of(m)
    body = _with_optout(lead, m, m["body"])
    db.update_message(mid, body=body, status="sending")
    try:
        if ch == "email":
            irt, refs = _thread_refs(lead, m)
            db.update_message(mid, msgid=emailer.send(lead["email"], m["subject"] or "Hello", body, irt, refs))
        else:
            if config.SEND_TYPING:
                whatsapp.typing(lead["phone"], True)
                time.sleep(min(5.0, 1.0 + len(body) / 60))
            whatsapp.send_text(lead["phone"], body)
    except Exception as e:
        db.update_message(mid, status="failed", error=str(e)[:300], scheduled_at=None)
        return False
    db.update_message(mid, status="sent", sent_at=_fmt(clock()), error=None, scheduled_at=None)
    webhooks.emit("message.sent", {"lead": db.get_lead(lead["id"]),
                                   "message": {"id": mid, "body": body, "kind": m["kind"], "channel": ch,
                                               **({"subject": m["subject"]} if ch == "email" else {})}})
    if lead["status"] == "new":
        db.update_lead(lead["id"], status="contacted")
    if m["kind"] in ("outreach", "reply"):
        db.schedule_followups(lead["id"], config.FOLLOWUP_DAYS)
    return True


def tick(now=None):
    """Do at most one send. Returns 'sent', 'failed', 'idle', 'paused' or 'waiting'."""
    with LOCK:
        if paused():
            return "paused"
        now = now or clock()
        m = lead = None
        waiting = False
        for cand in db.queued():
            cl = db.get_lead(cand["lead_id"])
            why = _skip_reason(cand, cl)
            if why:
                db.update_message(cand["id"], status="cancelled", error=why, scheduled_at=None)
                continue
            ch = channel_of(cand)
            last = db.last_sent_at(ch)
            if (whatsapp.hours_problem(whatsapp._local_now()) or db.sent_today(now, ch) >= limit_for(ch)
                    or (ch == "email" and not emailer.enabled())):
                waiting = True
                continue
            if last and (now - last).total_seconds() < int(db.kv_get(_gap_key(ch), 0) or 0):
                waiting = True
                continue
            m, lead = cand, cl          # a blocked channel never holds up the other one
            break
        if not m:
            if waiting:
                reschedule(now)
            return "waiting" if waiting else "idle"
        ok = deliver(m, lead)
        if ok:
            db.kv_set(_gap_key(channel_of(m)), pick_gap())
        reschedule(now)
        return "sent" if ok else "failed"


def drain(limit=1000):
    """Run ticks until nothing more can be sent right now (used by tests)."""
    n = 0
    while n < limit and tick() in ("sent", "failed"):
        n += 1
    return n


def recover():
    """After a crash mid-send we cannot know if it went out: never resend blindly."""
    for m in db.messages(status="sending"):
        db.update_message(m["id"], status="failed",
                          error="Interrupted while sending. Check the chat in WhatsApp before trying again.", scheduled_at=None)
