"""Demo mode: try Lead Agent with sample data and no real WhatsApp, email or CRM.

    DEMO_MODE=1 ./run.sh        or        ./run.sh --demo        or        python -m app.demo

Uses its own database (demo.db, never leads.db). WhatsApp and email sends are recorded here instead of
leaving the machine, and "Simulate reply" feeds a pretend incoming message through the normal reply pipeline.
If Ollama is not running, the app's canned fallback drafts are used.
"""
import os

if __name__ == "__main__":
    os.environ["DEMO_MODE"] = "1"          # must be set before config is imported

import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from html import escape

from . import ai, config, db

SENT = []                                  # every "sent" message: {"channel", "to", "body", ...}
_LOCK = threading.Lock()

PRESET_REPLIES = {
    "interested": "That sounds interesting, can you tell me more?",
    "asking_price": "How much would this cost for a small shop like ours?",
    "not_now": "Thanks, but not right now. Maybe in a few months.",
    "not_interested": "No thanks, we are not interested.",
    "stop": "STOP",
}


def _record(channel, to, body, **extra):
    with _LOCK:
        SENT.append({"channel": channel, "to": to, "body": body, "at": datetime.now(timezone.utc).isoformat(), **extra})
        del SENT[:-500]


def fake_whatsapp(phone, text):
    _record("whatsapp", phone, text)
    return {"id": "demo-" + uuid.uuid4().hex[:8], "demo": True}


def fake_email(msg):
    _record("email", msg["To"], msg.get_content() if hasattr(msg, "get_content") else str(msg), subject=msg["Subject"])
    return msg["Message-ID"]


def _lead_channel(lead):
    last = next((m for m in reversed(db.messages(lead_id=lead["id"])) if m["status"] in ("sent", "received")), None)
    if last:
        return last.get("channel") or "whatsapp"
    return db.pick_channel(lead, "reply") or "whatsapp"


def simulate_reply(lead_id, text=None):
    """Pretend the lead wrote back. Goes through the real inbound pipeline (STOP, AI tag, reply draft, follow-up cancel)."""
    from . import main                     # late import: main imports this module
    lead = db.get_lead(lead_id)
    if not lead:
        return None
    text = (text or "").strip() or PRESET_REPLIES["interested"]
    text = PRESET_REPLIES.get(text, text)
    ch = _lead_channel(lead)
    if ch == "email" and lead.get("email"):
        main.handle_inbound(None, text, lead=lead, channel="email",
                            subject="Re: " + (_last_subject(lead_id) or "Hello"), msgid=f"<demo-{uuid.uuid4().hex}@demo.invalid>")
    else:
        main.handle_inbound(lead["phone"], text, lead=lead, channel="whatsapp")
    return text


def _last_subject(lead_id):
    return next((m["subject"] for m in reversed(db.messages(lead_id=lead_id)) if m.get("subject")), None)


# ---------------- sample data ----------------
def _ts(**ago):
    return (datetime.now(timezone.utc) - timedelta(**ago)).strftime("%Y-%m-%d %H:%M:%S")


def _out(lid, body, kind="outreach", status="sent", ch="whatsapp", subject=None, **ago):
    mid = db.add_message(lid, "out", body, kind, status, ch, subject)
    if status == "sent":
        db.update_message(mid, sent_at=_ts(**ago))
    return mid


def _in(lid, body, tag, ch="whatsapp", subject=None, **ago):
    mid = db.add_message(lid, "in", body, "inbound", "received", ch, subject)
    db.update_message(mid, sent_at=_ts(**ago), tag=tag, next_step=ai.REPLY_TAGS[tag][1])
    return mid


def _lead(name, niche, city, phone, email, website, score, why, campaign, status="new", notes=None):
    lid, _ = db.add_lead({"name": name, "niche": niche, "city": city, "phone": phone, "email": email,
                          "website": website, "campaign": campaign, "source": "osm", "notes": notes})
    db.update_lead(lid, score=score, score_reason=why, status=status)
    return lid


def seed():
    """Fill an empty demo database with ~15 sample leads across every stage. Returns the number of leads added."""
    if db.list_leads():
        return 0
    D, C = "Leeds dentists & clinics", "Manchester cafés"
    pitch = "We build modern websites and WhatsApp automation for small local businesses."

    # --- new, with drafts waiting for approval
    a = _lead("Brightsmile Dental", "dentist", "Leeds", "447700900101", "hello@brightsmile.example.com", "brightsmile.example.com", 82,
              "No online booking and the site is not mobile friendly: a good fit for a booking page and WhatsApp reminders.", D)
    _out(a, "Hi Brightsmile team! I noticed patients can't book online. We build simple booking pages with WhatsApp reminders for dental practices. Open to a quick chat this week?", status="draft")
    b = _lead("Northern Roots Café", "cafe", "Manchester", "447700900102", None, None, 74,
              "No website at all, only a social page. They would benefit from a menu site with WhatsApp ordering.", C)
    _out(b, "Hi Northern Roots! Love your coffee photos. We help cafés take WhatsApp orders and get a simple website live fast. Worth a 10-minute chat?", status="draft")
    c = _lead("Kinetic Gym", "gym", "Leeds", None, "team@kineticgym.example.com", "kineticgym.example.com", 68,
              "Email only. Class timetable is a PDF: a live timetable with sign-ups would help.", D)
    _out(c, "Hi Kinetic team,\n\nYour class timetable is a PDF, so members can't book a spot. We build live timetables with sign-ups for gyms. Would you be open to a quick chat?",
         ch="email", subject="Quick idea for Kinetic Gym", status="draft")
    _lead("The Copper Kettle Tearoom", "cafe", "Manchester", "447700900103", None, "copperkettle.example.com", 55,
          "Has a basic site. Possible need for online table booking.", C)

    # --- contacted: sent messages, follow-ups planned (one due now)
    d = _lead("Harbour View Bakery", "bakery", "Manchester", "447700900104", None, None, 71, "No website; strong local following.", C, "contacted")
    _out(d, "Hi Harbour View! We build simple websites and WhatsApp ordering for bakeries. Open to a quick chat?", hours=40)
    db.schedule_followups(d, config.FOLLOWUP_DAYS)
    e = _lead("Oakwood Physio", "physiotherapy", "Leeds", "447700900105", None, "oakwoodphysio.example.com", 77, "Outdated site, no online booking.", D, "contacted")
    _out(e, "Hi Oakwood team, we help physio clinics with online booking and WhatsApp reminders. Would a quick chat be useful?", days=5)
    db.schedule_followups(e, config.FOLLOWUP_DAYS)
    with db.conn() as cn:
        cn.execute("UPDATE followups SET due_at=? WHERE lead_id=? AND step=1", (_ts(hours=2), e))
    f = _lead("Spark Electricians", "electrician", "Leeds", None, "info@sparkelec.example.com", "sparkelec.example.com", 63, "Email only; site has no quote form.", D, "contacted")
    _out(f, "Hi Spark team,\n\nYour site has no quote form, so jobs go through phone calls only. We build quote request forms that land in WhatsApp. Open to a quick chat?",
         ch="email", subject="A quote form for Spark Electricians", days=2)
    db.schedule_followups(f, config.FOLLOWUP_DAYS)
    g = _lead("Summit Yoga Studio", "yoga studio", "Manchester", "447700900106", "hello@summityoga.example.com", "summityoga.example.com", 66, "Class bookings by DM; a booking page would help.", C, "contacted")
    _out(g, "Hi Summit Yoga! We build booking pages for studios with WhatsApp reminders. Fancy a quick chat?", days=3)
    db.schedule_followups(g, config.FOLLOWUP_DAYS)

    # --- replied: AI-tagged replies with a reply draft waiting
    h = _lead("Greenleaf Landscaping", "landscaping", "Leeds", "447700900107", None, None, 69, "No website; relies on word of mouth.", D, "replied")
    _out(h, "Hi Greenleaf! We build simple websites with a photo gallery and WhatsApp enquiries for landscapers. Open to a chat?", days=2)
    _in(h, "That sounds interesting, can you tell me more?", "interested", hours=20)
    _out(h, "Happy to! We would build a one-page site with your best project photos and a WhatsApp button for quotes. Could we do a quick call this week?", "reply", status="draft")
    i = _lead("Pixel & Print", "print shop", "Manchester", "447700900108", None, "pixelprint.example.com", 72, "Has a site but no online ordering for print jobs.", C, "replied")
    _out(i, "Hi Pixel & Print! We help print shops take orders and proofs over WhatsApp. Worth a quick chat?", days=3)
    _in(i, "How much would this cost for a small shop like ours?", "asking_price", hours=5)
    _out(i, "Good question! Exact details depend on what you need, so the best next step is a quick call where we can share a clear figure. Does tomorrow work?", "reply", status="draft")
    j = _lead("Urban Barber Co", "barber", "Leeds", "447700900109", None, None, 60, "Walk-in only; online booking would help.", D, "replied")
    _out(j, "Hi Urban Barber! We build simple online booking for barbers, with WhatsApp reminders. Interested?", days=4)
    _in(j, "Not right now, maybe in a couple of months.", "not_now", days=1)
    k = _lead("Little Stars Nursery", "nursery", "Manchester", None, "office@littlestars.example.com", "littlestars.example.com", 64, "Enquiry form is broken; parents ask by email.", C, "replied")
    _out(k, "Hi Little Stars team,\n\nYour enquiry form didn't work when we tried it. We build reliable enquiry forms that notify you instantly. Open to a quick chat?",
         ch="email", subject="Enquiry form for Little Stars", days=2)
    _in(k, "Thanks for flagging that. Yes, please send some details.", "interested", ch="email", subject="Re: Enquiry form for Little Stars", hours=8)
    _out(k, "Thanks for getting back to me! I'll keep it short: we can fix and replace the enquiry form quickly. Could we book a 15-minute call to go through it?",
         "reply", status="draft", ch="email", subject="Re: Enquiry form for Little Stars")

    # --- closed outcomes
    l = _lead("Metro Vet Clinic", "veterinary", "Leeds", "447700900110", None, "metrovet.example.com", 80, "Strong fit for appointment reminders.", D, "won")
    _out(l, "Hi Metro Vet! We help clinics cut no-shows with WhatsApp reminders. Open to a chat?", days=12)
    _in(l, "Yes please, let's talk. Can you call on Thursday?", "interested", days=11)
    _out(l, "Perfect, Thursday it is. Talk then!", "reply", days=11)
    m = _lead("Bellaluna Salon", "hair salon", "Manchester", "447700900111", None, None, 58, "Social-only presence.", C, "lost")
    _out(m, "Hi Bellaluna! We build booking pages for salons. Interested in a quick chat?", days=8)
    _in(m, "No thanks, we are not interested.", "not_interested", days=7)
    n = _lead("Quickfix Plumbers", "plumber", "Leeds", "447700900112", None, None, 50, "Asked us to stop.", D, "opted_out")
    _out(n, "Hi Quickfix! We build websites for tradespeople. Open to a quick chat?", days=6)
    _in(n, "STOP", "not_interested", days=6)
    # reminder and a note, so those parts of the UI have content
    db.add_note(h, "Owner prefers calls after 5pm.", (datetime.now().date() + timedelta(days=1)).isoformat())
    return len(db.list_leads())


def banner_html():
    return ('<div class="demobar" role="status"><b>Demo mode</b> Sample data only. Nothing is sent to WhatsApp or email; '
            'sends are recorded, and replies are simulated. <span class="sub">Real data is never touched.</span></div>')


def simulate_html(lead, nxt=""):
    opts = "".join(f'<option value="{k}">{escape(v)}</option>' for k, v in PRESET_REPLIES.items())
    return (f'<form class="demosim" method="post" action="/demo/simulate-reply"><input type="hidden" name="lead_id" value="{lead["id"]}">{nxt}'
            f'<label class="lbl">Demo: pretend {escape(lead["name"])} wrote back</label>'
            f'<select name="preset" aria-label="Reply to simulate">{opts}</select>'
            '<input name="text" maxlength="500" placeholder="…or type your own reply" aria-label="Custom reply">'
            '<button class="secondary">Simulate reply</button></form>')


def main():
    import uvicorn
    print(f"Demo mode: sample data in {config.DB_PATH} (nothing is sent anywhere)")
    uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
