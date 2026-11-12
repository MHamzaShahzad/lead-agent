"""Optional email channel: SMTP sending and an IMAP reply poller. Disabled until SMTP_HOST/SMTP_FROM are set.
Sending is done by sendqueue.deliver (same hours, gaps and approval as WhatsApp); this module only talks SMTP/IMAP.
Mail is never deleted: replies we handled are only flagged as read (\\Seen)."""
import email
import email.policy
import email.utils
import imaplib
import re
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, make_msgid, parseaddr

from . import config, db

UNSUB_LINE = "If you'd rather not hear from us, just reply \"unsubscribe\" and I won't email you again."
_AUTO = re.compile(r"mailer-daemon|postmaster|no-?reply|do-not-reply", re.I)
_QUOTE_START = re.compile(r"^(on .{5,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+|_{5,})\s*$", re.I)


def enabled():
    return bool(config.SMTP_HOST and from_address())


def imap_enabled():
    return bool(enabled() and config.IMAP_HOST and (config.IMAP_USER or config.SMTP_USER))


def from_address():
    return parseaddr(config.SMTP_FROM or config.SMTP_USER)[1] if "@" in (config.SMTP_FROM or config.SMTP_USER) else ""


def valid_address(a):
    return bool(re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", (a or "").strip()))


# ---------------- sending ----------------
def build(to, subject, body, in_reply_to=None, references=None):
    m = EmailMessage()
    sender = config.SMTP_FROM if "@" in config.SMTP_FROM else formataddr((config.BRAND_NAME, from_address()))
    m["From"], m["To"], m["Subject"] = sender, to, " ".join(subject.split())
    m["Date"] = email.utils.formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=from_address().split("@")[-1])
    m["List-Unsubscribe"] = f"<mailto:{from_address()}?subject=unsubscribe>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = " ".join(x for x in (references or "", in_reply_to) if x).strip()
    m.set_content(body)
    return m


def _smtp():
    if config.SMTP_TLS == "ssl":
        s = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
    else:
        s = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        if config.SMTP_TLS == "starttls":
            s.starttls()
    if config.SMTP_USER:
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
    return s


def send(to, subject, body, in_reply_to=None, references=None):
    """Send one plain-text email. Returns its Message-ID. Raises on any SMTP problem."""
    if not enabled():
        raise RuntimeError("Email is not set up (SMTP_HOST and SMTP_FROM).")
    if not valid_address(to):
        raise RuntimeError("The lead has no valid email address.")
    m = build(to, subject, body, in_reply_to, references)
    s = _smtp()
    try:
        s.send_message(m)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return m["Message-ID"]


def check_smtp():
    """Read-only test: connect and log in, send nothing. Returns (ok, text)."""
    if not enabled():
        return False, "Enter the SMTP host and From address first."
    try:
        s = _smtp()
        s.noop()
        s.quit()
        return True, "SMTP login works."
    except Exception as e:
        return False, f"SMTP failed: {str(e)[:150]}"


# ---------------- receiving ----------------
def _imap():
    c = imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT)
    c.login(config.IMAP_USER or config.SMTP_USER, config.IMAP_PASSWORD or config.SMTP_PASSWORD)
    return c


def check_imap():
    if not imap_enabled():
        return False, "Enter the IMAP host first."
    try:
        c = _imap()
        c.logout()
        return True, "IMAP login works."
    except Exception as e:
        return False, f"IMAP failed: {str(e)[:150]}"


def clean_reply(text):
    """The new part of a reply: drop quoted lines and everything after 'On ... wrote:'."""
    out = []
    for line in text.replace("\r", "").split("\n"):
        if _QUOTE_START.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _body(msg):
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return part.get_content()
    part = msg.get_body(preferencelist=("html",))
    return re.sub(r"<[^>]+>", " ", part.get_content()) if part is not None else ""


def parse_incoming(raw):
    """-> dict(from, msgid, refs, subject, text, auto) from raw RFC822 bytes."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    refs = re.findall(r"<[^>]+>", f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}")
    auto = (str(msg.get("Auto-Submitted", "no")).lower() != "no" or str(msg.get("Precedence", "")).lower() in ("bulk", "junk", "auto_reply")
            or bool(_AUTO.search(sender)))
    return {"from": sender, "msgid": str(msg.get("Message-ID", "")).strip(), "refs": refs,
            "subject": " ".join(str(msg.get("Subject", "")).split()), "text": clean_reply(_body(msg)), "auto": auto}


def poll(handler):
    """Fetch new mail since the last poll, match it to leads, call handler(lead, text, subject, msgid) for each reply.
    Returns the number handled. Matched mail is flagged \\Seen; nothing is ever deleted. First run only marks the
    current end of the mailbox, so old mail is not imported."""
    c = _imap()
    handled = 0
    try:
        typ, _ = c.select("INBOX")
        if typ != "OK":
            raise RuntimeError("cannot open INBOX")
        validity = (c.response("UIDVALIDITY")[1] or [b""])[0]
        validity = (validity or b"").decode() if isinstance(validity, bytes) else str(validity)
        last = db.kv_get("imap_last_uid")
        if last is None or db.kv_get("imap_validity") != validity:
            _, data = c.uid("search", None, "ALL")
            uids = data[0].split() if data and data[0] else []
            db.kv_set("imap_last_uid", int(uids[-1]) if uids else 0)
            db.kv_set("imap_validity", validity)
            return 0
        _, data = c.uid("search", None, f"UID {int(last) + 1}:*")
        uids = sorted({int(u) for u in (data[0].split() if data and data[0] else []) if int(u) > int(last)})[:100]
        for uid in uids:
            typ, parts = c.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            db.kv_set("imap_last_uid", uid)
            if not raw:
                continue
            m = parse_incoming(raw)
            if m["auto"] or db.has_msgid(m["msgid"]):
                continue
            lead = db.lead_by_msgid(m["refs"]) or db.lead_by_email(m["from"])
            if not lead:
                continue
            text = m["text"]
            if re.search(r"unsubscribe", m["subject"], re.I) and len(text) < 200 and not re.search(r"unsubscribe", text, re.I):
                text = "unsubscribe"
            if not text:
                continue
            handler(lead, text, m["subject"], m["msgid"])
            c.uid("store", str(uid), "+FLAGS", "(\\Seen)")
            handled += 1
    finally:
        try:
            c.logout()
        except Exception:
            pass
    return handled


def run_poll(handler):
    """poll() plus status bookkeeping for the UI card. Never raises."""
    try:
        n = poll(handler)
        db.kv_set("email_poll_error", "")
        db.kv_set("email_poll_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        return n
    except Exception as e:
        db.kv_set("email_poll_error", str(e)[:200])
        return 0


def status():
    return {"smtp": enabled(), "imap": imap_enabled(), "from": from_address(), "sent_today": db.sent_today(channel="email"),
            "limit": config.EMAIL_DAILY_LIMIT, "poll_at": db.kv_get("email_poll_at"), "poll_error": db.kv_get("email_poll_error") or ""}
