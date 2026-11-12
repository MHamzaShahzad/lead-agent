"""Lead Gen Agent: web dashboard + WhatsApp webhook.  Run:  uvicorn app.main:app --port 8000"""
import asyncio
import csv
import hmac
import io
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from urllib.parse import quote

from . import ai, api, auth, config, db, finder, notify, ui, webhooks, whatsapp

STOP_WORDS = re.compile(r"^\s*(stop|unsubscribe|band karo|mat bhejo|no thanks)\s*[.!]*\s*$", re.I)


async def _followup_loop():
    while True:
        await asyncio.to_thread(run_followups)
        try:
            await asyncio.to_thread(db.backup_if_due)
        except Exception:
            pass                                    # a failed backup must never stop the loop
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(app):
    db.init()
    task = asyncio.create_task(_followup_loop())
    yield
    task.cancel()


app = FastAPI(title="Lead Gen Agent", lifespan=lifespan)
app.include_router(api.router)
FLASH = {"msg": ""}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/v1/"):         # the REST API has its own Bearer-key auth (app/api.py)
        return await call_next(request)
    if path in ("/docs", "/openapi.json", "/redoc") and api.key_ok(api.bearer(request)):
        return await call_next(request)     # API docs: dashboard login OR the API key
    if (config.DASHBOARD_PASSWORD and path not in ("/login", "/webhook/waha")
            and not auth.valid(request.cookies.get(auth.COOKIE))):
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login?next=" + quote(nxt), status_code=303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/"):
    return ui.login_page("", next)


@app.post("/login")
def login(password: str = Form(""), next: str = Form("/")):
    if not config.DASHBOARD_PASSWORD or not auth.check_password(password):
        time.sleep(1)                                   # slow down guessing
        return HTMLResponse(ui.login_page("Wrong password.", next), status_code=401)
    r = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)
    r.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.DAYS * 86400, httponly=True, samesite="lax")
    return r


@app.get("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(auth.COOKIE)
    return r


def back(msg="", to="/"):
    FLASH["msg"] = msg
    if not to.startswith("/") or to.startswith("//"):
        to = "/"
    return RedirectResponse(to, status_code=303)


# ---------------- agent actions ----------------
def score_and_draft(lead_id):
    lead = db.get_lead(lead_id)
    score, reason = ai.score_lead(lead)
    db.update_lead(lead_id, score=score, score_reason=reason)
    if lead.get("phone") and not db.messages(lead_id=lead_id):
        db.add_message(lead_id, "out", ai.draft_outreach(lead), "outreach", "draft")


api.score_hook = score_and_draft


def import_leads(rows, tasks):
    new = dup = 0
    for row in rows:
        lead_id, is_new = db.add_lead(row)
        if is_new:
            new += 1
            tasks.add_task(score_and_draft, lead_id)
        else:
            dup += 1
    return f"Imported {new} new leads, {dup} duplicates merged. AI is scoring and drafting in the background."


INBOUND_LOCK = threading.Lock()   # one sender at a time: no duplicate leads for a new number
DRAFT_LOCK = threading.Lock()     # one reply draft at a time, so bursts of messages give ONE draft


def handle_inbound(phone, text):
    with INBOUND_LOCK:
        lead = db.lead_by_phone(phone)
        if not lead:
            info = ai.extract_inbound(text)
            lead_id, _ = db.add_lead({**info, "phone": phone, "source": "whatsapp",
                                      "name": info.get("name") or f"WhatsApp {phone}",
                                      "notes": info.get("intent")})
            lead = db.get_lead(lead_id)
            score, reason = ai.score_lead({**lead, "notes": f"They messaged us first: {text}"})
            db.update_lead(lead_id, score=score, score_reason=reason)
        mid = db.add_message(lead["id"], "in", text, "inbound", "received")
        db.cancel_followups(lead["id"])
        if STOP_WORDS.match(text):
            db.update_lead(lead["id"], status="opted_out")
    notify.reply_alert(lead["name"])
    stop = STOP_WORDS.match(text)
    tag, step = (("not_interested", ai.REPLY_TAGS["not_interested"][1]) if stop else ai.classify_reply(text))
    db.update_message(mid, tag=tag, next_step=step)
    webhooks.emit("reply.received", {"lead": db.get_lead(lead["id"]),
                                     "message": {"id": mid, "body": text, "tag": tag, "next_step": step}})
    if stop:
        return
    with DRAFT_LOCK:
        lead = db.get_lead(lead["id"])
        msgs = db.messages(lead_id=lead["id"])
        if lead["status"] == "opted_out" or any(m["direction"] == "in" and m["id"] > mid for m in msgs):
            return                         # a newer message arrived: its own task writes the one reply
        if lead["status"] in ("new", "contacted"):
            db.update_lead(lead["id"], status="replied")
        history = [m for m in reversed(msgs) if m["status"] in ("sent", "received")]
        # one waiting reply per lead: older unsent reply/follow-up drafts are out of date now
        for old in msgs:
            if old["status"] == "draft" and old["kind"] in ("reply", "followup"):
                db.update_message(old["id"], status="rejected", error="superseded by a newer message from the lead")
        db.add_message(lead["id"], "out", ai.draft_reply(lead, history), "reply", "draft")


def run_followups():
    made = 0
    for f in db.due_followups():
        lead = db.get_lead(f["lead_id"])
        db.add_message(lead["id"], "out", ai.draft_followup(lead, f["step"]), "followup", "draft")
        db.set_followup(f["id"], "drafted")
        made += 1
    return made


def _with_optout(lead, m, body):
    """Add the opt-out line to the very first message we send a lead (never to replies or follow-ups)."""
    line = config.OPTOUT_LINE.strip()
    if (config.OPTOUT_ENABLED and line and m["kind"] == "outreach" and line.lower() not in body.lower()
            and not any(x["direction"] == "out" and x["status"] == "sent" for x in db.messages(lead_id=lead["id"]))):
        return f"{body}\n\n{line}"
    return body


def send_message(mid, body):
    m = db.get_message(mid)
    lead = db.get_lead(m["lead_id"])
    body = re.sub(r"^\[fallback\]\s*", "", body.strip())
    if not body:
        raise whatsapp.SendBlocked("Message is empty.")
    whatsapp.check_limits(lead)                  # refuse first, so a blocked draft stays a draft
    body = _with_optout(lead, m, body)
    db.update_message(mid, body=body, status="approved")
    try:
        whatsapp.send_text(lead["phone"], body)
    except Exception as e:
        db.update_message(mid, status="failed", error=str(e)[:300])
        raise whatsapp.SendBlocked(str(e)) from e
    db.update_message(mid, status="sent", sent_at=db.now(), error=None)
    webhooks.emit("message.sent", {"lead": db.get_lead(lead["id"]),
                                   "message": {"id": mid, "body": body, "kind": m["kind"]}})
    if lead["status"] == "new":
        db.update_lead(lead["id"], status="contacted")
    if m["kind"] in ("outreach", "reply"):     # nudge later if they go quiet (any reply cancels it)
        db.schedule_followups(lead["id"], config.FOLLOWUP_DAYS)


# ---------------- routes ----------------
@app.post("/webhook/waha")
async def waha_webhook(request: Request, tasks: BackgroundTasks, token: str = ""):
    if config.WEBHOOK_TOKEN and not hmac.compare_digest(token.encode(), config.WEBHOOK_TOKEN.encode()):
        return Response(status_code=403)
    parsed = whatsapp.parse_webhook(await request.json())
    if parsed:
        tasks.add_task(handle_inbound, *parsed)
    return {"ok": True}


@app.post("/leads")
def add_lead(tasks: BackgroundTasks, name: str = Form(""), website: str = Form(""), niche: str = Form(""),
             phone: str = Form(""), email: str = Form(""), city: str = Form("")):
    return back(import_leads([dict(name=name, website=website, niche=niche, phone=phone,
                                   email=email, city=city, source="manual")], tasks))


@app.post("/import")
async def import_csv(tasks: BackgroundTasks, file: UploadFile):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    return back(import_leads(finder.parse_csv(text), tasks))


@app.post("/find")
def find(tasks: BackgroundTasks, niche: str = Form(...), city: str = Form(...)):
    try:
        rows = finder.find_osm(niche, city)
    except Exception as e:
        return back(f"Search failed: {e}")
    return back(f"Found {len(rows)} on OpenStreetMap. " + import_leads(rows, tasks))


@app.post("/leads/{lead_id}/rescore")
def rescore(lead_id: int, tasks: BackgroundTasks, next: str = Form("/")):
    tasks.add_task(score_and_draft, lead_id)
    return back("Re-scoring in background. Refresh in a few seconds.", next)


@app.post("/leads/{lead_id}/draft")
def new_draft(lead_id: int, next: str = Form("/")):
    lead = db.get_lead(lead_id)
    if not lead or lead["status"] == "opted_out":
        return back("Not created: this lead asked us to stop.", next)
    if _has_replied(lead_id):                    # they wrote to us: write a reply, not a cold first message
        if any(m["status"] == "draft" for m in db.messages(lead_id=lead_id)):
            return back("A draft is already waiting below. Edit it and send.", next)
        hist = [m for m in db.conversation(lead_id) if m["status"] in ("sent", "received")]
        db.add_message(lead_id, "out", ai.draft_reply(lead, hist), "reply", "draft")
        return back("Reply draft written. Read it below before sending.", next)
    db.add_message(lead_id, "out", ai.draft_outreach(db.get_lead(lead_id)), "outreach", "draft")
    return back("New draft created. Review it in Approvals.", next)


@app.post("/leads/{lead_id}/status")
def set_status(lead_id: int, status: str = Form(...), next: str = Form("/")):
    db.update_lead(lead_id, status=status)
    if status in ("won", "lost", "opted_out"):
        db.cancel_followups(lead_id)
    return back(f"Lead marked {status}.", next)


@app.post("/messages/{mid}/send")
def approve_send(mid: int, body: str = Form(...), next: str = Form("/")):
    try:
        send_message(mid, body)
    except whatsapp.SendBlocked as e:
        return back(f"Not sent: {e}", next)
    return back("Sent on WhatsApp.", next)


def _redraft(m, lead):
    if m["kind"] == "reply":
        hist = [h for h in db.conversation(lead["id"]) if h["status"] in ("sent", "received") and h["id"] < m["id"]]
        return ai.draft_reply(lead, hist)
    if m["kind"] == "followup":
        n = sum(1 for h in db.conversation(lead["id"]) if h["kind"] == "followup" and h["id"] <= m["id"])
        return ai.draft_followup(lead, max(1, n))
    return ai.draft_outreach(lead)


@app.post("/messages/{mid}/regenerate")
def regenerate(mid: int):
    m = db.get_message(mid)
    if not m or m["status"] != "draft":
        return back("That draft is no longer waiting.")
    db.update_message(mid, body=_redraft(m, db.get_lead(m["lead_id"])))
    return back("New version written. Read it before sending.")


@app.post("/messages/{mid}/rewrite")
def rewrite_draft(mid: int, mode: str = Form(...), body: str = Form("")):
    m = db.get_message(mid)
    if not m or m["status"] != "draft" or mode not in ai.REWRITE_MODES:
        return back("That draft is no longer waiting.")
    try:
        new = ai.rewrite(body.strip() or m["body"], mode)
    except ai.OllamaDown:
        return back("The AI is offline, so it can't rewrite right now.")
    db.update_message(mid, body=new)
    return back("Rewritten. Read it before sending.")


BULK_LOCK = threading.Lock()


def bulk_send(ids):
    """Send approved drafts one by one, spaced out. Skips drafts with warnings; stops at the daily limit."""
    if not BULK_LOCK.acquire(blocking=False):
        return
    try:
        for mid in ids:
            m = db.get_message(mid)
            if not m or m["status"] != "draft" or ui.draft_warning(m["body"]):
                continue
            for _ in range(3):
                try:
                    send_message(mid, m["body"])
                    break
                except whatsapp.SendBlocked as e:
                    txt = str(e)
                    if txt.startswith(("Daily limit", "Outside sending hours")):
                        return
                    wait = re.match(r"Slow down: wait (\d+)s", txt)
                    if not wait:
                        break                       # failed or opted out: it stays in Approvals
                    time.sleep(int(wait.group(1)) + 1)
    finally:
        BULK_LOCK.release()


@app.post("/messages/bulk-send")
def bulk_send_route(tasks: BackgroundTasks, ids: list[int] = Form([])):
    if not ids:
        return back("Tick at least one draft first.")
    if BULK_LOCK.locked():
        return back("Not started: a bulk send is already running. Wait for it to finish.")
    problem = whatsapp.hours_problem()
    if problem:
        return back("Not started: " + problem)
    left = max(0, config.DAILY_SEND_LIMIT - db.sent_today())
    tasks.add_task(bulk_send, sorted(ids))
    return back(f"Sending up to {min(len(ids), left)} messages about {config.MIN_SECONDS_BETWEEN_SENDS}s apart "
                f"(daily limit leaves room for {left}). Drafts with warnings are skipped. Refresh to see progress.")


@app.post("/messages/{mid}/reject")
def reject(mid: int, next: str = Form("/")):
    db.update_message(mid, status="rejected")
    return back("Draft rejected.", next)


@app.post("/whatsapp/relink")
def relink(phone: str = Form(...)):
    try:
        code = whatsapp.request_pairing_code(phone)
    except whatsapp.SendBlocked as e:
        return back(f"Re-link failed: {e}")
    return back(f"Pairing code: {code} — WhatsApp → Linked devices → Link a device → "
                "'Link with phone number instead' → enter it within 1 minute.")


@app.post("/settings")
async def save_settings(request: Request, my_business: str = Form(""), ollama_model: str = Form(""),
                        daily_limit: str = Form(""), min_seconds: str = Form(""), followup_days: str = Form(""),
                        optout_on: str = Form(""), optout_line: str = Form(""),
                        brand_name: str = Form(None), brand_tagline: str = Form(None)):
    form = await request.form()
    try:
        biz = " ".join(my_business.split())
        model = ollama_model.strip()
        limit, gap = int(daily_limit), int(min_seconds)
        days = [int(x) for x in followup_days.replace(" ", "").split(",") if x]
    except ValueError:
        return back("Not saved: limits and follow-up days must be whole numbers (days like 3,7).")
    if not biz or len(biz) > 1500:
        return back("Not saved: describe your business in 1 to 1500 characters.")
    if not model or " " in model:
        return back("Not saved: enter the Ollama model name, e.g. llama3.2:3b.")
    if not 1 <= limit <= 500 or not 0 <= gap <= 3600:
        return back("Not saved: daily limit must be 1-500 and the gap 0-3600 seconds.")
    if len(days) > 6 or any(not 1 <= d <= 90 for d in days) or days != sorted(days):
        return back("Not saved: follow-up days must be up to 6 increasing numbers between 1 and 90, like 3,7.")
    line = " ".join(optout_line.split())
    windows = []
    for i, day in enumerate(whatsapp.DAYS):
        if not form.get(f"day{i}_on"):
            windows.append(None)
            continue
        a, b = str(form.get(f"day{i}_start", "")), str(form.get(f"day{i}_end", ""))
        if not whatsapp.valid_window(a, b):
            return back(f"Not saved: {day} needs a start time earlier than its end time, like 09:00 and 20:00.")
        windows.append((a, b))
    if not any(windows):
        return back("Not saved: turn on at least one sending day.")
    if len(line) > 120 or (optout_on and not line):
        return back("Not saved: the opt-out line must be 1 to 120 characters (or untick it).")
    brand = {}
    if brand_name is not None:
        brand["BRAND_NAME"] = " ".join(brand_name.split())[:40] or "Lead Agent"
    if brand_tagline is not None:
        brand["BRAND_TAGLINE"] = " ".join(brand_tagline.split())[:60]
    config.save_settings({**brand, "SEND_SCHEDULE": whatsapp.format_schedule(windows), "OPTOUT_ENABLED": "1" if optout_on else "0",
                          "OPTOUT_LINE": line or "Reply STOP to opt out", "MY_BUSINESS": biz, "OLLAMA_MODEL": model, "DAILY_SEND_LIMIT": limit,
                          "MIN_SECONDS_BETWEEN_SENDS": gap, "FOLLOWUP_DAYS": ",".join(map(str, days))})
    return back("Settings saved. They apply right away.")


@app.post("/followups/run")
def followups_now():
    return back(f"{run_followups()} follow-up drafts created.")


def _has_replied(lead_id):
    return any(m["direction"] == "in" for m in db.messages(lead_id=lead_id))


def bulk_draft(ids):
    for i in ids:
        lead = db.get_lead(i)
        if (lead and not _has_replied(i) and lead.get("phone") and lead["status"] != "opted_out"
                and not any(m["status"] == "draft" for m in db.messages(lead_id=i))):
            db.add_message(i, "out", ai.draft_outreach(lead), "outreach", "draft")


@app.post("/leads/bulk")
def bulk(tasks: BackgroundTasks, ids: list[int] = Form([]), action: str = Form(...), status: str = Form("")):
    if not ids:
        return back("Tick at least one lead first.")
    if action == "draft":
        tasks.add_task(bulk_draft, ids)
        return back(f"AI is writing messages for {len(ids)} leads. They will appear in Approvals.")
    if action == "stage" and status in ui.STATUS_LABEL:
        for i in ids:
            db.update_lead(i, status=status)
            if status in ("won", "lost", "opted_out"):
                db.cancel_followups(i)
        return back(f"{len(ids)} leads marked {ui.STATUS_LABEL[status]}.")
    if action == "delete":
        for i in ids:
            db.delete_lead(i)
        return back(f"Deleted {len(ids)} leads.")
    return back("Unknown action.")


CSV_COLS = ("name", "phone", "website", "niche", "city", "email", "campaign", "status", "score", "score_reason", "notes", "created_at")


@app.get("/leads.csv")
def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLS)
    for l in db.list_leads():
        # leading = + - @ would run as a formula in Excel; prefix with an apostrophe
        w.writerow([("'" + str(v)) if str(v)[:1] in ("=", "+", "-", "@") else v
                    for v in (("" if l[c] is None else l[c]) for c in CSV_COLS)])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="leads.csv"'})


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(lead_id: int, came_from: str = Query("", alias="from")):
    lead = db.get_lead(lead_id)
    if not lead:
        return RedirectResponse("/", status_code=303)
    msg, FLASH["msg"] = FLASH["msg"], ""
    return ui.lead_page(lead, msg=msg, convo=db.conversation(lead_id), followups=db.lead_followups(lead_id),
                        notes=db.lead_notes(lead_id), reminders=db.lead_reminders(lead_id),
                        came_from=came_from)


@app.post("/leads/{lead_id}")
def edit_lead(lead_id: int, name: str = Form(""), phone: str = Form(""), website: str = Form(""),
              niche: str = Form(""), city: str = Form(""), notes: str = Form(""),
              email: str = Form(None), campaign: str = Form(None)):
    if not db.get_lead(lead_id):
        return back("Lead not found.")
    extra = {k: v for k, v in (("email", email), ("campaign", campaign)) if v is not None}
    db.edit_lead(lead_id, name=name, phone=phone, website=website, niche=niche, city=city, notes=notes, **extra)
    return back("Saved.", f"/leads/{lead_id}")


@app.post("/leads/{lead_id}/find-contact")
def find_contact(lead_id: int):
    lead = db.get_lead(lead_id)
    to = f"/leads/{lead_id}"
    if not lead:
        return back("Lead not found.")
    if not (lead["website"] or "").strip():
        return back("Add the website first, then try again.", to)
    email, phone = finder.find_contacts_on_website(lead["website"])
    filled = db.fill_empty_contacts(lead_id, email, phone)
    if filled:
        return back("Found and saved: " + " and ".join(filled) + ". Existing details were not changed.", to)
    if email or phone:
        return back("Found details on the website, but this lead already has an email and phone, so nothing was changed.", to)
    return back("Could not find an email or phone on that website.", to)


@app.post("/leads/{lead_id}/notes")
def add_note(lead_id: int, body: str = Form(""), remind_on: str = Form("")):
    to = f"/leads/{lead_id}"
    body = body.strip()
    if not db.get_lead(lead_id):
        return back("Lead not found.")
    if not body:
        return back("Write something in the note first.", to)
    if remind_on:
        try:
            datetime.strptime(remind_on, "%Y-%m-%d")
        except ValueError:
            return back("That reminder date is not valid.", to)
    db.add_note(lead_id, body[:2000], remind_on or None)
    return back("Note saved." + (f" Reminder set for {remind_on}." if remind_on else ""), to)


@app.post("/reminders/{rid}/done")
def reminder_done(rid: int, next: str = Form("/")):
    db.finish_reminder(rid)
    return back("Reminder done.", next)


@app.post("/leads/{lead_id}/cancel-followups")
def cancel_fu(lead_id: int):
    db.cancel_followups(lead_id)
    return back("Planned follow-ups cancelled.", f"/leads/{lead_id}")


@app.post("/leads/{lead_id}/delete")
def delete_lead(lead_id: int):
    lead = db.get_lead(lead_id)
    db.delete_lead(lead_id)
    return back(f"Deleted {lead['name']}." if lead else "Lead not found.")


@app.get("/api/leads")
def api_leads():
    return db.list_leads()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    msg, FLASH["msg"] = FLASH["msg"], ""
    wa_status, wa_number = whatsapp.session_info()
    drafts = db.messages(status="draft") + db.messages(status="failed")
    drafts.sort(key=lambda d: d["id"], reverse=True)
    for d in drafts:
        d["context"] = db.last_context(d["lead_id"], d["id"]) if d["kind"] != "outreach" else []
        lead = db.get_lead(d["lead_id"]) or {}
        d["score"] = lead.get("score")
        d["lead_status"] = lead.get("status")
        last_in = next((m for m in db.messages(lead_id=d["lead_id"]) if m["direction"] == "in"), None)
        d["their_msg"], d["their_at"] = (last_in["body"], last_in["sent_at"]) if last_in else (None, None)
        d["their_tag"], d["their_step"] = (last_in["tag"], last_in["next_step"]) if last_in else (None, None)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    leads = db.list_leads()
    working = sum(1 for l in leads if l["score"] is None and l["created_at"] >= cutoff)
    return ui.page(results=db.results(), working=working, replies_week=db.replies_this_week(), msg=msg, ai_status=ai.status(), wa_status=wa_status, wa_number=wa_number, last_backup=db.last_backup(), campaigns=db.campaign_results(), reminders=db.due_reminders(),
                   sent_today=db.sent_today(), leads=leads, drafts=drafts,
                   inbox=db.replies_by_lead()[:30], followups=db.list_followups())
